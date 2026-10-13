"""Rebuild a read-only, portable knowledge nebula from the configured vault.

No network, vault mutation, inference of relations, or dependency on a demo.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from urllib.parse import quote

try:
    from .obsidian_idea_retrieval import read_markdown, NOTE_LINK_RE
    from .workspace_paths import vault_path
    from .knowledge_nebula_layout import arrange
except ImportError:
    from obsidian_idea_retrieval import read_markdown, NOTE_LINK_RE
    from workspace_paths import vault_path
    from knowledge_nebula_layout import arrange

ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'knowledge-nebula'
MARKER = '<!-- knowledge-nebula-generated-v1 -->'
PALETTE = [[.36,.78,1],[.57,.50,1],[.89,.46,.83],[.53,.81,.93],[.68,.58,1],[.40,.70,1],[.77,.60,.91]]
SECTIONS = {'連到 Hub','形成取捨','品味線索','可應用於','核心概念','支持','延伸','相關概念','相關連結'}
CHECKED_DIRS = set()

def plain(path):
    for p in (path, *path.parents):
        if p in CHECKED_DIRS: continue
        try: st = p.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&0x400:
            raise ValueError('Link/junction paths are not supported')
        if stat.S_ISDIR(st.st_mode): CHECKED_DIRS.add(p)
    return path

def inside(root, relative):
    raw = str(relative).replace('\\','/')
    if not raw or raw.startswith('/') or ':' in raw or '..' in Path(raw).parts:
        raise ValueError('Expected a relative path within the selected workspace/vault')
    p = plain(root / raw)
    if not p.is_relative_to(root): raise ValueError('Path escapes selected root')
    return p

def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def stable(s): return int(hashlib.sha256(s.encode()).hexdigest()[:12],16)
def scalar(v): return v if isinstance(v,str) else ''
def values(v): return v if isinstance(v,list) else []

def preview_files(vault, fields):
    """Use only canonical declared media; never crawl or regenerate images."""
    case = fields.get('asset_mode') == 'case_evidence'
    candidates = values(fields.get('evidence_screenshots' if case else 'infographics'))
    label = '案例截圖' if case else '資訊圖'
    if not candidates:
        candidates = values(fields.get('evidence_screenshots'))
        label = '案例截圖'
    images = []
    for rel in candidates:
        if not isinstance(rel, str): continue
        p = inside(vault, rel)
        if p.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'} or not p.is_file(): continue
        images.append((p, label))
    return images


def preview_images(vault, fields, output_dir):
    return [dict(src=quote(os.path.relpath(p, output_dir).replace('\\', '/'), safe='/.-_'), kind=label)
            for p, label in preview_files(vault, fields)]

def collect(root, output_dir):
    CHECKED_DIRS.clear()
    root = plain(Path(root).absolute()).resolve()
    cfg = root/'brain.json'
    config = json.loads(cfg.read_text(encoding='utf-8-sig')) if cfg.exists() else {}
    vault = vault_path(root, config.get('vault',os.environ.get('CREATIVE_SECOND_BRAIN_VAULT','knowledge-base-vault')))
    plain(vault)
    if not vault.is_dir(): raise ValueError('Configured vault does not exist')
    sources_file = inside(vault,'_registry/sources.jsonl')
    if not sources_file.is_file(): raise ValueError('Source registry is missing; refusing to replace a valid graph')
    warnings=[]; provenance={}; nodes=[]; edges=[]
    def track(p):
        plain(p); provenance[p.relative_to(root).as_posix()] = digest(p)
    track(sources_file)
    if cfg.exists(): track(cfg)
    rows={}
    for line in sources_file.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip(): continue
        r=json.loads(line); sid=r.get('source_id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]+',sid): raise ValueError('Invalid source identity')
        if sid in rows: raise ValueError('Duplicate source identity: '+sid)
        rows[sid]=r
    for sid,r in sorted(rows.items()):
        if r.get('status') in {'deleted','retired','archived'}: continue
        p=inside(vault,r.get('source_note_path',''))
        if not p.is_file(): warnings.append({'id':sid,'reason':'missing canonical note'}); continue
        track(p); f,body=read_markdown(p)
        for media, _ in preview_files(vault, f): track(media)
        outcome=inside(vault,'outcome-pages/'+sid+'_outcome.html')
        href=''
        if outcome.is_file():
            track(outcome); href=quote(os.path.relpath(outcome,output_dir).replace('\\','/'),safe='/.-_')
        else: warnings.append({'id':sid,'reason':'missing outcome page; node retained without broken link'})
        title=scalar(f.get('title')) or sid
        nodes.append(dict(id=sid,title=title,label=title if len(title)<=23 else title[:22]+'…',kind='source',copy=scalar(f.get('summary')),date=scalar(f.get('capture_date')),searchText=scalar(r.get('source_url'))+' '+scalar(f.get('source_url')),href=href,sourcePath=p.relative_to(vault).as_posix(),color=PALETTE[stable(sid)%7],images=preview_images(vault,f,output_dir)))
    source_ids={n['id'] for n in nodes}; concepts=[]; title_map={}
    folder=inside(vault,'concepts')
    for p in sorted(folder.rglob('*.md')) if folder.exists() else []:
        plain(p)
        if not p.is_relative_to(vault): raise ValueError('Concept escapes vault')
        track(p); f,body=read_markdown(p)
        if f.get('concept_status')!='graph_node': continue
        rel=p.relative_to(vault).as_posix(); nid='idea-'+hashlib.sha256(rel.encode()).hexdigest()[:16]
        title=scalar(f.get('title')) or scalar(f.get('concept')) or p.stem
        sentence=re.search(r'## (?:一句話|定義來源)\s+([^\n]+)',body)
        n=dict(id=nid,title=title,label=title if len(title)<=23 else title[:22]+'…',kind='idea',ideaType=scalar(f.get('idea_type')),copy=sentence.group(1).removeprefix('- ') if sentence else scalar(f.get('graph_role')),question=scalar(f.get('graph_question')),sourcePath=rel,color=PALETTE[stable(nid)%7])
        nodes.append(n); concepts.append((n,f,body))
        for alias in {title,p.stem,rel[:-3]}: title_map.setdefault(alias,[]).append(nid)
    # Registry-managed relations supersede old generated concept links. Reading
    # them here also makes refresh-button/watcher updates safe before a graph
    # note rebuild; handwritten semantic bridges keep their existing meaning.
    registry = inside(vault, '_registry/relations.jsonl')
    relation_rows = []
    if registry.is_file():
        track(registry)
        relation_rows = [json.loads(line) for line in registry.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    kp_concepts = {}
    owned_paths = {n['sourcePath']:n['id'] for n,f,_ in concepts if f.get('graph_scope') == 'concept_links_only'}
    if relation_rows:
        try:
            from .obsidian_vault_adapter import concept_note_path
        except ImportError:
            from obsidian_vault_adapter import concept_note_path
        kp_registry = inside(vault, '_registry/knowledge_points.jsonl')
        if not kp_registry.is_file(): raise ValueError('Relations require the knowledge-point identity registry')
        track(kp_registry)
        for line in kp_registry.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip(): continue
            row = json.loads(line)
            kp_note = inside(vault,row['kp_note_path'])
            if not kp_note.is_file(): continue
            track(kp_note); fields,_ = read_markdown(kp_note)
            if fields.get('kp_uid') != row['kp_uid']: raise ValueError('Knowledge-point identity mismatch')
            point = str(fields.get('knowledge_point') or fields.get('definition') or kp_note.stem)
            concept = concept_note_path(vault,point).relative_to(vault).as_posix()
            if concept in owned_paths: kp_concepts[row['kp_uid']] = owned_paths[concept]
    managed_pairs, active_pairs = set(), set()
    active_rows = []
    for row in relation_rows:
        a,b = kp_concepts.get(row.get('source_kp_uid')),kp_concepts.get(row.get('target_kp_uid'))
        if not a or not b or a == b: continue
        pair = frozenset((a,b)); managed_pairs.add(pair)
        if row.get('status','linked') == 'linked':
            active_pairs.add(pair); active_rows.append((a,b,row))
    def link(a,b,kind,path,quote_text):
        if a==b:return
        pair = frozenset((a,b))
        if path in owned_paths and kind == '相關概念' and pair in managed_pairs and pair not in active_pairs:return
        edges.append(dict(a=a,b=b,type=kind,evidence='筆記明列此關係；未推論相似度或因果。',path=path,quote=quote_text))
    for n,f,body in concepts:
        for record in values(f.get('source_records')):
            # Longest exact identity prefix, rather than guessing source numbers.
            candidate=str(record)
            while candidate and candidate not in source_ids:
                candidate=candidate.rsplit('_',1)[0] if '_' in candidate else ''
            if candidate: link(candidate,n['id'],'查證來源',n['sourcePath'],'source_records: '+str(record))
        section=''; fenced=False
        for line in body.splitlines():
            if line.lstrip().startswith(('```','~~~')):fenced=not fenced;continue
            if fenced:continue
            if line.startswith('## '):section=line[3:].strip()
            if section not in SECTIONS:continue
            for target in NOTE_LINK_RE.findall(line):
                ids=title_map.get(target.removesuffix('.md'),[])
                if len(ids)==1:link(n['id'],ids[0],section,n['sourcePath'],line.strip())
        for line in values(f.get('related_concepts')):
            for target in NOTE_LINK_RE.findall(line):
                ids=title_map.get(target.removesuffix('.md'),[])
                if len(ids)==1:link(n['id'],ids[0],'相關概念',n['sourcePath'],'related_concepts: '+line)
    represented = {frozenset((e['a'],e['b'])) for e in edges}
    for a,b,row in active_rows:
        if frozenset((a,b)) not in represented:
            link(a,b,'相關概念','_registry/relations.jsonl',str(row.get('relation',''))+' '+str(row.get('evidence','')))
    edges=list({(e['a'],e['b'],e['type'],e['path'],e['quote']):e for e in edges}.values())
    degree={n['id']:0 for n in nodes}
    for e in edges:degree[e['a']]+=1;degree[e['b']]+=1
    layout = arrange(nodes, edges)
    group_colors = {g['id']: PALETTE[i % len(PALETTE)] for i, g in enumerate(layout['groups'])}
    for n in nodes:
        n['degree']=degree[n['id']]
        if n['group'] != 'unlinked':
            n['color']=group_colors[n['group']]
    nodes.sort(key=lambda n:(n.get('ideaType')!='hub',n['kind']!='idea',-n['degree'],n['id']))
    # Detect concurrent note edits before producing a replacement.
    for rel,sha in provenance.items():
        if digest(inside(root,rel))!=sha: raise ValueError('Vault changed during read; rerun after editing finishes')
    fingerprint=hashlib.sha256(json.dumps(provenance,sort_keys=True).encode()).hexdigest()
    return dict(schema='knowledge_nebula_v1',nodes=nodes,edges=edges,layout=layout,sourceCount=len(source_ids),ideaCount=len(concepts),snapshot=fingerprint,provenance=provenance,warnings=warnings,displayName=config.get('display_name','創造型第二大腦'),catalogHref=quote(os.path.relpath(vault/'outcome-pages/knowledge.html',output_dir).replace('\\','/'),safe='/.-_'),limits=dict(scene=96,list=40,relations=30),relationshipMeaning='依明列連線形成主題群；距離是關係佈局，不是語義相似度分數。')

def refresh_installed(vault, output_dir):
    """Update an already installed nebula after the normal outcome transaction.

    The nearest workspace marker must name this exact vault. Never install a
    graph in an unrelated fixture, export folder or neighbouring workspace.
    Errors propagate to the caller; the previous atomic projection survives.
    """
    vault = plain(Path(vault).absolute()).resolve()
    target = vault/'outcome-pages/nebula.html'
    if Path(output_dir).resolve() != target.parent or not target.is_file(): return None
    if MARKER not in target.read_text(encoding='utf-8')[:200]: return None
    for root in vault.parents:
        marker = plain(root/'brain.json')
        if marker.is_file():
            config = json.loads(marker.read_text(encoding='utf-8-sig'))
            if vault_path(root, config.get('vault', 'knowledge-base-vault')) != vault:
                raise ValueError('Workspace marker does not own the selected vault')
            return build(root)
    return None


def input_stamp(root):
    """Cheap local change detection; canonical content is validated by build()."""
    root = plain(Path(root).absolute()).resolve()
    marker = plain(root/'brain.json')
    config = json.loads(marker.read_text(encoding='utf-8-sig')) if marker.exists() else {}
    vault = vault_path(root, config.get('vault', 'knowledge-base-vault'))
    registry = inside(vault, '_registry/sources.jsonl')
    paths = {marker, registry, inside(vault, '_registry/relations.jsonl'), inside(vault, '_registry/knowledge_points.jsonl'), ASSETS/'template.html', ASSETS/'shaders.js',
             Path(__file__).with_name('reader_visual.css'), Path(__file__).with_name('reader_passage.js')}
    for line in registry.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip(): continue
        row = json.loads(line)
        note = inside(vault, row['source_note_path'])
        paths.add(note)
        if note.is_file():
            fields, _ = read_markdown(note)
            paths.update(media for media, _ in preview_files(vault, fields))
        sid = row['source_id']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', sid): raise ValueError('Invalid source identity')
        paths.add(inside(vault, f'outcome-pages/{sid}_outcome.html'))
    folder = inside(vault, 'concepts')
    if folder.exists(): paths.update(folder.rglob('*.md'))
    kp_folder = inside(vault, 'knowledge-points/by-source')
    if kp_folder.exists(): paths.update(kp_folder.glob('*/*.md'))
    stamps = []
    for path in sorted(paths):
        plain(path)
        try:
            info = path.stat(); stamps.append((str(path), info.st_mtime_ns, info.st_size))
        except FileNotFoundError: stamps.append((str(path), None, None))
    return tuple(stamps)


def build(root, output=None):
    CHECKED_DIRS.clear()
    root=plain(Path(root).absolute()).resolve()
    config=json.loads((root/'brain.json').read_text(encoding='utf-8-sig')) if (root/'brain.json').exists() else {}
    default=str(Path(config.get('vault','knowledge-base-vault'))/'outcome-pages/nebula.html')
    target=inside(root,output or default)
    if target.suffix!='.html': raise ValueError('Output must be an HTML file')
    if target.exists() and MARKER not in target.read_text(encoding='utf-8')[:200]:
        raise ValueError('Refusing to overwrite a file not owned by this generator')
    started=time.perf_counter(); data=collect(root,target.parent)
    template=(ASSETS/'template.html').read_text(encoding='utf-8')
    shaders=(ASSETS/'shaders.js').read_text(encoding='utf-8')
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    fallback=''.join('<li><a href="'+html.escape(n['href'],quote=True)+'">'+html.escape(n['title'])+'</a></li>' for n in data['nodes'] if n.get('href'))
    page=MARKER+'\n'+template.replace('/*__DATA__*/','const DATA='+encoded+';').replace('/*__SHADERS__*/',shaders).replace('<!--FALLBACK-->',fallback)
    try:
        from .reader_shell import apply_visual
    except ImportError:
        from reader_shell import apply_visual
    page=apply_visual(page, 'nebula')
    changed=not target.exists() or target.read_text(encoding='utf-8')!=page
    if changed:
        target.parent.mkdir(parents=True,exist_ok=True)
        temp=target.with_name(target.name+'.tmp-'+str(os.getpid()))
        with temp.open('x',encoding='utf-8',newline='\n') as f:f.write(page)
        os.replace(temp,target)
    return dict(status='updated' if changed else 'unchanged',output=target.relative_to(root).as_posix(),sources=data['sourceCount'],concepts=data['ideaCount'],edges=len(data['edges']),snapshot=data['snapshot'],warnings=data['warnings'],bytes=len(page.encode()),seconds=round(time.perf_counter()-started,3),sha256=digest(target))

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path.cwd());p.add_argument('--output',help='Workspace-relative generated HTML')
    p.add_argument('--watch',action='store_true',help='Foreground local refresh; Ctrl+C stops. No server or scheduler.')
    p.add_argument('--interval',type=float,default=5);a=p.parse_args()
    if a.interval<1:p.error('interval must be at least 1 second')
    try:
        while True:
            try: print(json.dumps(build(a.root,a.output),ensure_ascii=False),flush=True)
            except (ValueError,OSError,KeyError,TypeError) as e:
                print(json.dumps({'status':'blocked','reason':str(e)},ensure_ascii=False),flush=True)
                if not a.watch:return 2
            if not a.watch:return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:return 0

if __name__=='__main__':raise SystemExit(main())
