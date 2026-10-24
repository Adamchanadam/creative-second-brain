"""Durable derived answers and rebuildable readers; never source ingestion.

Storage and completion contract: semantic-index specification, formal ask entry.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

try:
    from . import thought_analysis as thinking
    from .reader_shell import apply_theme, atomic_reader_write, catalogue_projection_lock, guide_for
except ImportError:
    import thought_analysis as thinking
    from reader_shell import apply_theme, atomic_reader_write, catalogue_projection_lock, guide_for

SCHEMA = 'thinking_record_v1'
ID = re.compile(r'ASK-[0-9a-f]{32}')
LOCK_WAIT_SECONDS = 60
LOCK_POLL_SECONDS = 0.5


def safe(vault: Path, path: Path) -> Path:
    vault, path = Path(vault).absolute(), Path(path).absolute()
    if not path.is_relative_to(vault) or path == vault:
        raise ValueError('archive path escapes vault')
    for node in (path, *path.parents):
        if node.exists() or node.is_symlink():
            mode = node.lstat()
            if stat.S_ISLNK(mode.st_mode) or getattr(mode, 'st_file_attributes', 0) & 0x400:
                raise ValueError('archive path contains a link or junction')
    if not path.resolve().is_relative_to(vault.resolve()):
        raise ValueError('archive path resolves outside vault')
    return path


def encoded_relative(target: Path, base: Path) -> str:
    return quote(os.path.relpath(target, base).replace('\\', '/'), safe='/')


def checked_note(vault: Path, relative: str) -> Path:
    relative = relative_note(relative)
    path = safe(vault, vault / relative)
    if not path.is_file():
        raise ValueError('source note is missing')
    return path


def relative_note(relative: str) -> str:
    if not isinstance(relative, str) or not relative or ':' in relative or '\\' in relative or Path(relative).is_absolute() or '..' in Path(relative).parts or Path(relative).suffix != '.md':
        raise ValueError('note must be vault-relative')
    return relative


@contextmanager
def lock(vault: Path, path: Path):
    path = safe(vault, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            handle = path.open('x', encoding='utf-8')
            break
        except FileExistsError:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('another write is active or interrupted; inspect the named lock before resuming: ' + str(path))
            time.sleep(min(LOCK_POLL_SECONDS, remaining))
    try:
        with handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        path.unlink()  # Only the lock acquired by this invocation.


def atomic_text(vault: Path, path: Path, content: str):
    path = safe(vault, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return
    # Reader HTML is shared with the visual refresh.  Its whole output tree
    # uses the same OS lock, so a late shell refresh cannot restore an older
    # body over a newly generated thinking/note reader.
    reader_root = safe(vault, vault / 'outcome-pages')
    if path.suffix.lower() == '.html' and path.is_relative_to(reader_root):
        with catalogue_projection_lock(reader_root):
            if path.exists() and path.read_text(encoding='utf-8') == content:
                return
            atomic_reader_write(path, content)
        return
    temp = safe(vault, path.with_name(path.name + '.' + uuid4().hex + '.tmp'))
    try:
        with temp.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def validate_record(record: dict, key: str):
    if not ID.fullmatch(key) or record.get('schema') != SCHEMA or record.get('id') != key:
        raise ValueError('invalid thinking record identity')
    packet, answer, review = record['packet'], record['answer'], record['review']
    if packet['session_id'] != key or answer['session_id'] != key:
        raise ValueError('thinking record question identity mismatch')
    if record.get('record_sha256') != thinking.fingerprint({k: v for k, v in record.items() if k != 'record_sha256'}):
        raise ValueError('thinking record was edited; preserve it for review')
    lookup = {e['id']: e for e in packet['evidence']}
    if len(lookup) != len(packet['evidence']):
        raise ValueError('duplicate evidence')
    for e in packet['evidence']:
        relative_note(e['path'])
        if hashlib.sha256(e['text'].encode('utf-8')).hexdigest() != e['sha256']:
            raise ValueError('source snapshot checksum mismatch')
    for e in answer.get('web_sources', []):
        if e['id'] in lookup or not e.get('readback'):
            raise ValueError('invalid external snapshot')
        lookup[e['id']] = {**e, 'text': e['readback']}
    thinking.validate_analysis(answer['analysis'], lookup)
    thinking.validate_review(packet, answer, review)
    return list(lookup.values())


def note_text(record: dict) -> str:
    key, packet = record['id'], record['packet']
    evidence = validate_record(record, key)
    source_links = []
    for e in packet['evidence']:
        # Markdown links work even for filenames that cannot be wikilinks.
        source_links.append('- [' + e['id'] + '](' + quote('../' + e['path'], safe='/') + ')')
    return '\n'.join([
        '---', 'type: thinking_record', 'epistemic_status: derived_reasoning',
        'answer_id: ' + key, 'title: ' + json.dumps(packet['question'], ensure_ascii=False),
        'created: ' + record['created_at'], '---', '',
        '> 思考紀錄：推理成果與待驗判斷，不是新增的原始來源。', '',
        '[返回成果總覽](../成果頁總覽.md) · [思考紀錄目錄](../outcome-pages/thinking.html) · '
        '[互動思考地圖](../outcome-pages/thinking/' + key + '.html)', '',
        '## 原始筆記', '', *source_links, '',
        thinking.markdown(packet['question'], record['answer']['analysis'], evidence, record['review']),
    ])


def save_record(vault: Path, packet: dict, answer: dict, review: dict) -> Path:
    """Caller validates live evidence. Same identity resumes; differing work never overwrites."""
    key = packet['session_id']
    if not ID.fullmatch(key):
        raise ValueError('invalid answer identity')
    target = safe(vault, vault / 'thinking' / (key + '.json'))
    companion = safe(vault, target.with_suffix('.md'))
    frozen_packet = {k: packet[k] for k in ('schema_version', 'session_id', 'question', 'evidence')}
    frozen_packet['vault'] = '.'
    for e in packet['evidence']:
        checked_note(vault, e['path'])
    with lock(vault, target.with_suffix('.lock')):
        if target.exists():
            record = json.loads(target.read_text(encoding='utf-8'))
            validate_record(record, key)
            if record['packet'] != frozen_packet or record['answer'] != answer or record['review'] != review:
                raise ValueError('same answer identity has different content; existing record preserved')
        else:
            record = {'schema': SCHEMA, 'id': key, 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                      'packet': frozen_packet, 'answer': answer, 'review': review}
            record['record_sha256'] = thinking.fingerprint(record)
        note = note_text(record)
        if companion.exists() and companion.read_text(encoding='utf-8') != note:
            raise ValueError('thinking note was edited; existing note preserved')
        atomic_text(vault, target, json.dumps(record, ensure_ascii=False, indent=2) + '\n')
        atomic_text(vault, companion, note)
        if json.loads(target.read_text(encoding='utf-8')) != record or companion.read_text(encoding='utf-8') != note:
            raise ValueError('thinking record readback failed')
    return target




def note_reader(vault: Path, note: Path) -> Path:
    """UTF-8 current-note projection; raw Markdown may be misdecoded by HTTP/file viewers."""
    note = safe(vault, note)
    relative = note.relative_to(vault).as_posix()
    page = safe(vault, vault/'outcome-pages/notes'/('N-'+hashlib.sha256(relative.encode()).hexdigest()[:24]+'.html'))
    body = note.read_text(encoding='utf-8-sig')
    source_url = encoded_relative(note, page.parent)
    title = note.stem
    heading = re.search(r'^# (.+)$', body, re.M)
    if heading: title = heading.group(1)
    content = '''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="vault-note" content="'''+html.escape(relative,quote=True)+'''"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+html.escape(title)+''' · 筆記</title>
<style>*{box-sizing:border-box}body{margin:0;background:#f7f6f0;color:#252d35;font:17px/1.85 system-ui,sans-serif}main{max-width:960px;padding:36px 24px;margin:auto}nav{display:flex;gap:12px 24px;flex-wrap:wrap;border-bottom:1px solid #ddd;padding-bottom:20px}a{color:#344c85}h1{font-size:clamp(24px,4vw,40px);line-height:1.4}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}small{color:#657078}[hidden]{display:none}</style>
<main><nav><a id="back" href="../thinking.html">返回思考紀錄</a><a href="../index.html">成果總覽</a><a download href="'''+html.escape(source_url,quote=True)+'''">下載原筆記</a><a hidden id="obsidian">在 Obsidian 開啟</a></nav><h1>'''+html.escape(title)+'''</h1><small>目前筆記的閱讀快照 · '''+html.escape(relative)+''' · 重新整理目錄後更新</small><pre>'''+html.escape(body)+'''</pre></main>
<script>const ret=new URLSearchParams(location.search).get('return');if(ret&&/^(thinking\\/ASK-[0-9a-f]{32}|thinking|index)\\.html$/.test(ret)){document.getElementById('back').href='../'+ret;document.getElementById('back').textContent='返回剛才的閱讀頁';}
const original='''+json.dumps(source_url)+''';if(location.protocol==='file:'){const u=new URL(original,location.href);let p=decodeURIComponent(u.pathname);if(/^\\/[A-Za-z]:\\//.test(p))p=p.slice(1);if(u.hostname)p='//'+u.hostname+p;const a=document.getElementById('obsidian');a.href='obsidian://open?path='+encodeURIComponent(p);a.hidden=false;}</script></html>'''
    atomic_text(vault, page, apply_theme(content, page, vault, 'thinking'))
    return page


def missing_reader(vault: Path, page: Path, heading: str) -> str:
    """Keep retired reader URLs readable through the same branded shell."""
    content = ('<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
               '<meta name="viewport" content="width=device-width,initial-scale=1">'
               '<title>' + html.escape(heading) + '</title><main><h1>'
               + html.escape(heading) + '</h1><a href="../thinking.html">返回思考紀錄</a></main></html>')
    return apply_theme(content, page, vault, 'thinking')


def navigation(vault: Path, base: Path, note: Path, evidence: list[dict], *, record_id: str = '', reader: Path | None = None) -> dict:
    def readable(p, label):
        page = note_reader(vault, p)
        back = reader.relative_to(vault/'outcome-pages').as_posix() if reader else 'thinking.html'
        return {'label': label, 'href': encoded_relative(page, base)+'?return='+quote(back, safe=''), 'note': True, 'note_href': encoded_relative(p, base)}
    links = [{'label': '成果總覽', 'href': encoded_relative(vault/'outcome-pages/index.html', base)},
             {'label': '思考紀錄', 'href': encoded_relative(vault/'outcome-pages/thinking.html', base)}]
    argument = {}
    if note.is_file():
        argument = readable(note, '閱讀筆記')
        links.append(argument)
    if record_id and ID.fullmatch(record_id) and safe(vault, vault/'thinking'/f'{record_id}.json').is_file():
        argument = {'label': '同題問答', 'href': encoded_relative(vault/'outcome-pages/thinking'/f'{record_id}.html', base)}
        links.append(argument)
    source_links = {}
    for e in evidence:
        if 'path' in e:
            try:
                p = checked_note(vault, e['path'])
                source_links[e['id']] = readable(p, '打開目前來源筆記')
            except (ValueError, OSError):
                pass
        elif e.get('url', '').startswith(('https://', 'http://')):
            source_links[e['id']] = {'href': e['url'], 'label': '打開原始網頁'}
    return {'links': links, 'sources': source_links, 'argument': argument}


def export_records(vault: Path, output_dir: Path) -> list[dict]:
    folder = safe(vault, vault/'thinking')
    safe(vault, output_dir/'thinking.html')
    rows, expected = [], set()
    for path in sorted(folder.glob('ASK-*.json')):
        key = path.stem
        if not ID.fullmatch(key):
            continue
        page = safe(vault, output_dir/'thinking'/f'{key}.html')
        expected.add(page.name)
        warnings = []
        row = {'answer_id': key, 'title': key, 'summary': '待檢查的思考紀錄', 'topic': '推理成果',
               'source': f'thinking/{key}.md', 'updated_at': '', 'url': f'thinking/{key}.html', 'warnings': warnings}
        try:
            record = json.loads(safe(vault, path).read_text(encoding='utf-8'))
            evidence = validate_record(record, key)
            note = safe(vault, path.with_suffix('.md'))
            if not note.is_file() or note.read_text(encoding='utf-8') != note_text(record):
                warnings.append('思考筆記缺失或已改動，須核對；下方仍為已保存版本。')
            for e in record['packet']['evidence']:
                try:
                    current = checked_note(vault, e['path']).read_text(encoding='utf-8-sig')
                    saved_hash = e['source_sha256'] if 'source_sha256' in e else e['sha256']
                    if hashlib.sha256(current.encode('utf-8')).hexdigest() != saved_hash:
                        warnings.append(e['id'] + ' 來源已更新；這份答案保留當時的來源快照。')
                except (ValueError, OSError):
                    warnings.append(e['id'] + ' 目前來源不可用；這份答案保留當時的來源快照。')
            nav = navigation(vault, page.parent, note, evidence, reader=page)
            if not note.is_file():
                nav['links'] = [x for x in nav['links'] if not x.get('note')]
            nav['warnings'] = warnings
            content = thinking.render_html(record['packet']['question'], record['answer']['analysis'], evidence,
                                           record['review'], return_url='../thinking.html', navigation=nav)
            row.update(title=record['packet']['question'], summary=record['answer']['analysis']['thesis'], updated_at=record['created_at'])
        except (ValueError, KeyError, TypeError, AttributeError, OSError) as error:
            warnings.append('保存內容未通過完整性核對：' + str(error))
            content = '<!doctype html><meta charset="utf-8"><h1>此思考紀錄需要核對</h1><p>' + html.escape(warnings[-1]) + '</p><a href="../thinking.html">返回思考紀錄</a>'
        row['status'] = 'needs_review' if warnings else 'ready'
        atomic_text(vault, page, apply_theme(content, page, vault, 'thinking'))
        rows.append(row)
    reader_dir = safe(vault, output_dir/'thinking')
    if reader_dir.exists():
        for old in reader_dir.glob('ASK-*.html'):
            if ID.fullmatch(old.stem) and old.name not in expected:
                atomic_text(vault, old, missing_reader(vault, old, '原思考紀錄已不在目前集合'))
    for old in (output_dir/'notes').glob('N-*.html'):
        safe(vault, old)
        meta = re.search(r'<meta name="vault-note" content="([^"]+)">', old.read_text(encoding='utf-8'))
        if not meta:
            continue
        try:
            original = checked_note(vault, html.unescape(meta.group(1)))
        except (ValueError, OSError):
            atomic_text(vault, old, missing_reader(vault, old, '原筆記已不在目前集合'))
        else:
            note_reader(vault, original)
    return rows


def portal_links(vault: Path, portal: Path) -> str:
    folder = safe(vault, vault/'thinking')
    lines = ['\n## 思考紀錄\n', '推理成果與待驗判斷；不等同原始來源。\n',
             '[打開思考紀錄目錄](' + encoded_relative(vault/'outcome-pages/thinking.html', portal.parent) + ')\n']
    if folder.exists():
        for path in sorted(folder.glob('ASK-*.md')):
            if ID.fullmatch(path.stem):
                safe(vault, path)
                try:
                    title = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['packet']['question']
                    title = re.sub(r'[\[\]|\r\n]', ' ', title)
                except (ValueError, OSError, KeyError, TypeError):
                    title = path.stem
                # Stable wikilinks give Obsidian an actual inbound link.
                lines.append('- [[thinking/' + path.stem + '|' + title + ']]')
    return '\n'.join(lines) + '\n'


def refresh_nebula(vault: Path) -> dict:
    """Bring an already installed nebula up to date, so the new record appears in it as a new star.

    The record is already saved and exported when this runs, so no outcome of it can undo the record: a nebula that is not
    installed, that did not update, or that the release gate has kept from rebuilding is only reported (receipt field ``nebula``:
    updated / not_installed / paused / refresh_failed)."""
    try:
        try:
            from .knowledge_nebula import refresh_installed
        except ImportError:
            from knowledge_nebula import refresh_installed
        result = refresh_installed(vault, vault/'outcome-pages')
        if result is None:
            return {'status': 'not_installed'}
        if result.get('status') == 'paused':  # unregistered reader code: nothing was rebuilt, so this is not an update
            return {'status': 'paused', 'reason': result['reason']}
        return {'status': 'updated', 'changed': result['status'] == 'updated'}
    except Exception as error:  # the record and its readers stay as saved
        return {'status': 'refresh_failed', 'error': type(error).__name__ + ': ' + str(error)}


def archive_answer(vault: Path, packet: dict, answer: dict, review: dict) -> dict:
    """No complete result until the record and its navigable catalogue exist."""
    try:
        from .outcome_page_generator import refresh_outcome_index
        from .ask_second_brain import review_answer
    except ImportError:
        from outcome_page_generator import refresh_outcome_index
        from ask_second_brain import review_answer
    review_answer(packet, answer, review)  # Validate current sources before any durable write.
    record = save_record(vault, packet, answer, review)
    output, portal = vault/'outcome-pages', vault/'成果頁總覽.md'
    # Validate every projection destination before the legacy shared refresh writes.
    for name in ('index.html', 'knowledge.html', 'thinking.html'):
        safe(vault, output/name)
    safe(vault, portal)
    # The installed nebula is refreshed once, below, after the readers exist (so the new star can link to its reader) and with
    # its outcome kept out of the record's fate; the catalogue refresh itself leaves it alone here.
    refresh_outcome_index(vault=vault, output_dir=output, portal=portal, defer_nebula=True)
    key = packet['session_id']
    catalogue = (output/'thinking.html').read_text(encoding='utf-8')
    match = re.search(r'<script type="application/json" id="catalog-data">(.*?)</script>', catalogue, re.S)
    rows = json.loads(match.group(1))['items'] if match else []
    found = [x for x in rows if x.get('answer_id') == key]
    if len(found) != 1 or found[0]['status'] != 'ready':
        raise ValueError('answer catalogue readback failed; record preserved, retry same inputs')
    reader = safe(vault, output/'thinking'/f'{key}.html')
    content = reader.read_text(encoding='utf-8')
    if key not in portal.read_text(encoding='utf-8') or 'thinking.html' not in (output/'index.html').read_text(encoding='utf-8'):
        raise ValueError('answer has no incoming portal/catalogue route')
    # Read all relative navigation targets, not only the primary output file.
    guide = guide_for(vault)
    for href in re.findall(r'href="([^"]+)"', content):
        href = html.unescape(href)
        if href.startswith('#') or urlsplit(href).scheme:
            continue
        candidate = reader.parent / unquote(urlsplit(href).path)
        # The shared guide is outside the vault but inside this workspace.
        # Only that exact known guide may use the wider read-only boundary.
        boundary = guide.parent if guide and candidate.resolve() == guide.resolve() else vault
        target = safe(boundary, candidate)
        if not target.is_file():
            raise ValueError('answer navigation target missing: ' + href)
    return {'status': 'complete', 'answer_id': key, 'record': str(record), 'note': str(record.with_suffix('.md')),
            'reader': str(reader), 'catalogue': str(output/'thinking.html'), 'portal': str(portal),
            'nebula': refresh_nebula(vault),
            'source_ingest_complete': False, 'epistemic_status': 'derived_reasoning'}
