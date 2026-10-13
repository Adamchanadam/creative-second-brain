"""Local, bounded reader with automatic projection refresh. No note writes."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import unquote, urlsplit, quote
from urllib.request import build_opener, ProxyHandler
import webbrowser

import knowledge_nebula as nebula
from reader_port_policy import candidate_ports, find_reader_state
from reader_service_lease import ReaderLease

SERVICE_GENERATION = "knowledge-nebula-server-v6"
PREFERENCE_SCHEMA = "knowledge_nebula_preferences_v1"
DEFAULT_PREFERENCES = {"schema": PREFERENCE_SCHEMA, "palette": "violet", "reduced_motion": False, "entrance_enabled": True}
PALETTE_IDS = {"violet", "aurora", "rose", "amber"}
MAX_REFRESH_RECEIPTS = 32
_GENERATION = re.compile(r"knowledge-nebula-server-v([1-9]\d*)\Z")
_REFRESH_JOB_ID = re.compile(r"[a-f0-9]{32}\Z")


def is_newer_generation(candidate: str, current: str = SERVICE_GENERATION) -> bool:
    """Only a numerically newer compatible reader may request retirement."""
    requested = _GENERATION.fullmatch(candidate)
    active = _GENERATION.fullmatch(current)
    return bool(requested and active and int(requested.group(1)) > int(active.group(1)))


def preference_path(vault: Path) -> Path:
    """Keep reader-only preferences beside, but outside, user notes."""
    return nebula.inside(vault, ".reader/nebula-preferences.json")


def load_preferences(vault: Path) -> dict:
    path = preference_path(vault)
    if not path.exists():
        return dict(DEFAULT_PREFERENCES)
    nebula.plain(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Saved reader preferences are invalid") from error
    if not isinstance(value, dict) or set(value) != set(DEFAULT_PREFERENCES) or value.get("schema") != PREFERENCE_SCHEMA:
        raise ValueError("Saved reader preferences are invalid")
    if not isinstance(value.get("palette"), str) or value.get("palette") not in PALETTE_IDS or not isinstance(value.get("reduced_motion"), bool) or not isinstance(value.get("entrance_enabled"), bool):
        raise ValueError("Saved reader preferences are invalid")
    return dict(value)


def save_preferences(vault: Path, value: object) -> dict:
    """Atomically replace a validated reader-only preference record."""
    if not isinstance(value, dict) or set(value) != {"palette", "reduced_motion", "entrance_enabled"}:
        raise ValueError("Invalid reader preferences")
    if not isinstance(value["palette"], str) or value["palette"] not in PALETTE_IDS or not isinstance(value["reduced_motion"], bool) or not isinstance(value["entrance_enabled"], bool):
        raise ValueError("Invalid reader preferences")
    # Refuse to overwrite a malformed record; this preserves recoverable user state.
    load_preferences(vault)
    path = preference_path(vault)
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    nebula.plain(folder)
    nebula.plain(path)
    saved = {"schema": PREFERENCE_SCHEMA, **value}
    temp = folder / (".nebula-preferences-" + secrets.token_hex(8) + ".tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(saved, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        nebula.plain(temp)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return saved


def refresh_reader_projections(root: Path) -> dict:
    """Rebuild only reader projections from already-saved local truth.

    This deliberately avoids source ingestion, outcome-page regeneration and
    image work.  Existing completed outcome pages remain historical inputs;
    the catalogue, portal and nebula are the replaceable reader layer.
    """
    config = json.loads((root / "brain.json").read_text(encoding="utf-8-sig"))
    vault = nebula.vault_path(root, config.get("vault", "knowledge-base-vault"))
    try:
        from outcome_page_generator import refresh_outcome_index
    except ImportError:
        from tools.outcome_page_generator import refresh_outcome_index
    readers = refresh_outcome_index(vault=vault, output_dir=vault / "outcome-pages", portal=vault / "成果頁總覽.md",
                                    defer_nebula=True)
    try:
        from reader_shell import refresh_current_reader_visuals
    except ImportError:
        from tools.reader_shell import refresh_current_reader_visuals
    readers.update(refresh_current_reader_visuals(vault))
    graph = nebula.build(root)
    return {"readers": readers, "nebula": graph}


def reader_input_stamp(root: Path) -> tuple:
    """Track saved source truth, but never generated reader output (no loops)."""
    config = json.loads((root / "brain.json").read_text(encoding="utf-8-sig"))
    vault = nebula.vault_path(root, config.get("vault", "knowledge-base-vault"))
    paths = [root / "brain.json", vault / "_registry" / "sources.jsonl", vault / "_registry" / "relations.jsonl",
             vault / "_registry" / "knowledge_points.jsonl"]
    for folder, pattern in (("sources", "*.md"), ("applications", "*.md"), ("knowledge-points", "*.md"),
                            ("concepts", "*.md"), ("thinking", "*.json")):
        directory = vault / folder
        if directory.is_dir():
            paths.extend(directory.rglob(pattern))
    stamps = []
    for path in sorted(set(paths)):
        nebula.plain(path)
        try:
            metadata = path.stat()
            stamps.append((str(path), metadata.st_mtime_ns, metadata.st_size))
        except FileNotFoundError:
            stamps.append((str(path), None, None))
    return tuple(stamps)


class NebulaServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root, port=0, idle_seconds=180):
        self.root = nebula.plain(Path(root).absolute()).resolve()
        self.identity = hashlib.sha256(str(self.root).casefold().encode()).hexdigest()[:20]
        config = json.loads((self.root/'brain.json').read_text(encoding='utf-8-sig')) if (self.root/'brain.json').exists() else {}
        self.vault = nebula.vault_path(self.root, config.get('vault', 'knowledge-base-vault'))
        nebula.plain(self.vault)
        self.page = self.vault/'outcome-pages/nebula.html'
        self.token = secrets.token_urlsafe(32)
        self.build_lock = threading.Lock()
        self.lifecycle_lock = threading.Lock()
        self.retiring = False
        self.retire_requested = threading.Event()
        self.last_active = time.monotonic()
        self.idle_seconds = idle_seconds
        self.update_error = False
        self.applied_reader_input: tuple | None = None
        self.refresh_jobs: dict[str, dict] = {}
        self.refresh_jobs_lock = threading.Lock()
        self.watch_stop = threading.Event()
        self.watch_thread = None
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'
        self.page_url = self.origin+'/'+quote(self.page.relative_to(self.root).as_posix(), safe='/')
        self.home_url = self.origin + '/index.html#prompts'

    def begin_write(self) -> bool:
        """Atomically reject new projection or preference writes after handoff."""
        if not self.lifecycle_lock.acquire(blocking=False):
            return False
        try:
            if self.retiring:
                return False
            return self.build_lock.acquire(blocking=False)
        finally:
            self.lifecycle_lock.release()

    def end_write(self) -> None:
        self.build_lock.release()

    def close_write_admission(self) -> bool:
        """Close admission, then drain the single previously admitted writer."""
        with self.lifecycle_lock:
            if self.retiring:
                return False
            self.retiring = True
            self.build_lock.acquire()
            self.build_lock.release()
            self.retire_requested.set()
            return True

    def begin_handoff(self) -> bool:
        return self.close_write_admission()

    def _job_snapshot(self, job: dict) -> dict:
        return {key: value for key, value in job.items() if key in {"job_id", "status", "result", "error"}}

    def refresh_job(self, job_id: str) -> dict | None:
        with self.refresh_jobs_lock:
            job = self.refresh_jobs.get(job_id)
            return self._job_snapshot(job) if job is not None else None

    def _update_refresh_job(self, job_id: str, **changes: object) -> None:
        with self.refresh_jobs_lock:
            job = self.refresh_jobs.get(job_id)
            if job is not None:
                job.update(changes)

    def _trim_refresh_jobs(self) -> bool:
        for job_id, job in list(self.refresh_jobs.items()):
            if len(self.refresh_jobs) < MAX_REFRESH_RECEIPTS:
                break
            if job.get("status") in {"ready", "failed"}:
                del self.refresh_jobs[job_id]
        return len(self.refresh_jobs) < MAX_REFRESH_RECEIPTS

    def submit_refresh_job(self, job_id: str) -> tuple[dict, bool]:
        """Reserve one idempotent reader refresh; a later poll owns its outcome."""
        with self.refresh_jobs_lock:
            existing = self.refresh_jobs.get(job_id)
            if existing is not None:
                return self._job_snapshot(existing), False
            for existing in self.refresh_jobs.values():
                if existing.get("status") in {"pending", "running"}:
                    return self._job_snapshot(existing), False
            if self.retiring:
                raise ValueError("reader service is retiring")
            if not self._trim_refresh_jobs():
                raise ValueError("reader refresh receipt capacity is busy")
            self.refresh_jobs[job_id] = {"job_id": job_id, "status": "pending"}
        return self.refresh_job(job_id) or {"job_id": job_id, "status": "pending"}, True

    def _publish_refresh(self, refreshed: dict) -> dict:
        result = refreshed["nebula"]
        self.page = nebula.inside(self.root, result["output"])
        self.vault = self.page.parent.parent
        self.page_url = self.origin+'/'+quote(result["output"], safe='/')
        public_result = {key: result[key] for key in ["status", "sources", "concepts", "edges", "snapshot", "warnings"]}
        public_result["url"] = self.page_url
        public_result["reader_sync"] = {"status": refreshed["readers"]["status"],
                                        "source_count": refreshed["readers"]["source_count"],
                                        "historical_pages_rewritten": refreshed["readers"]["historical_pages_rewritten"]}
        return public_result

    def refresh_projections(self) -> dict:
        """Refresh once and only share the watcher stamp if the input stayed stable."""
        before = reader_input_stamp(self.root)
        refreshed = refresh_reader_projections(self.root)
        result = self._publish_refresh(refreshed)
        after = reader_input_stamp(self.root)
        self.applied_reader_input = after if before == after else None
        self.update_error = False
        return result

    def _run_refresh_job(self, job_id: str) -> None:
        while not self.begin_write():
            if self.retiring or self.watch_stop.is_set():
                self._update_refresh_job(job_id, status="failed", error="reader service is retiring")
                return
            time.sleep(.1)
        self._update_refresh_job(job_id, status="running")
        try:
            self._update_refresh_job(job_id, status="ready", result=self.refresh_projections())
        except (ValueError, OSError, KeyError, TypeError):
            self.update_error = True
            self._update_refresh_job(job_id, status="failed", error="reader synchronization failed")
        finally:
            self.last_active = time.monotonic()
            self.end_write()

    def start_watching(self, interval=2):
        if self.watch_thread: return
        self.watch_thread = threading.Thread(target=self.watch, args=(interval,), daemon=True)
        self.watch_thread.start()

    def watch(self, interval):
        pending = None
        while not self.watch_stop.wait(interval):
            try:
                current = reader_input_stamp(self.root)
                # Two identical observations let multi-file saves settle.
                if current != pending:
                    pending = current
                elif current != self.applied_reader_input:
                    if not self.begin_write(): continue
                    try:
                        self.refresh_projections()
                    finally:
                        self.end_write()
                else:
                    self.update_error = False
            except (ValueError, OSError, KeyError, TypeError):
                self.update_error = True

    def server_close(self):
        # A handler can have accepted a request before the idle loop exits.
        # Close its later write admission before releasing this service's lease.
        self.close_write_admission()
        self.watch_stop.set()
        if self.watch_thread: self.watch_thread.join()
        super().server_close()

    def serve_until_idle(self):
        self.timeout = 1
        self.start_watching()
        try:
            while (not self.retire_requested.is_set()
                   and (time.monotonic()-self.last_active < self.idle_seconds or self.build_lock.locked())):
                self.handle_request()
        finally:
            self.server_close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log local media names or capability tokens.

    def reply(self, status, content, kind='application/json; charset=utf-8'):
        data = json.dumps(content, ensure_ascii=False).encode() if isinstance(content, dict) else content
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        if getattr(self, 'reader_revision', None): self.send_header('X-Reader-Revision', self.reader_revision)
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        self.end_headers()
        if self.command != 'HEAD':
            try: self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError): pass

    def allowed_request(self):
        return (self.headers.get('Host') == urlsplit(self.server.origin).netloc
                and self.headers.get('Origin', self.server.origin) == self.server.origin
                and self.headers.get('Sec-Fetch-Site') not in {'cross-site'})

    def allowed_preference_request(self):
        return (self.allowed_request()
                and secrets.compare_digest(self.headers.get('X-Nebula-Token', ''), self.server.token))

    def allowed_lifecycle_request(self):
        """Handoff is never an unauthenticated local stop operation."""
        return (self.allowed_request()
                and self.headers.get('Origin') == self.server.origin
                and secrets.compare_digest(self.headers.get('X-Nebula-Token', ''), self.server.token))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if not self.allowed_request(): return self.reply(403, {'error':'此連線只供本機星雲使用。'})
        self.server.last_active = time.monotonic()
        path = urlsplit(self.path).path
        if path == '/api/status':
            return self.reply(200, {'app':'knowledge-nebula', 'identity':self.server.identity,
                                    'generation':SERVICE_GENERATION, 'retiring':self.server.retiring})
        if path == '/api/ping':
            ready = self.server.page.is_file() and not self.server.build_lock.locked() and not self.server.update_error and not self.server.retiring
            return self.reply(200, {'ready':ready, 'revision':nebula.digest(self.server.page),
                                     'updating':self.server.build_lock.locked(), 'error':self.server.update_error})
        if path.startswith('/api/refresh-jobs/'):
            if not self.allowed_preference_request():
                return self.reply(403, {'error':'此同步收據只供目前本機閱讀服務使用。'})
            job_id = path.removeprefix('/api/refresh-jobs/')
            if not _REFRESH_JOB_ID.fullmatch(job_id):
                return self.reply(404, {'status':'unknown'})
            receipt = self.server.refresh_job(job_id)
            return self.reply(200, receipt) if receipt is not None else self.reply(404, {'job_id':job_id, 'status':'unknown'})
        if path == '/api/preferences':
            if not self.allowed_preference_request():
                return self.reply(403, {'error':'此設定只供目前本機星雲使用。'})
            try:
                return self.reply(200, {'preferences': load_preferences(self.server.vault)})
            except ValueError:
                return self.reply(422, {'error':'已保存的星雲偏好無法讀取，沒有覆寫原有設定。'})
        if path == '/':
            self.send_response(302); self.send_header('Location', self.server.page_url); self.end_headers(); return
        try:
            target = nebula.inside(self.server.root, unquote(path.lstrip('/')))
            if target.resolve() != target or not target.resolve().is_relative_to(self.server.root):
                return self.reply(404, {'error':'沒有此閱讀資源。'})
            suffix = target.suffix.lower()
            readable = (target == self.server.root/'index.html'
                        or target.is_relative_to(self.server.vault/'outcome-pages')
                        or target.is_relative_to(self.server.vault/'assets')
                        or target.is_relative_to(self.server.root/'assets'))
            if not readable or suffix not in {'.html','.css','.js','.png','.jpg','.jpeg','.webp','.gif','.svg','.woff','.woff2'}:
                return self.reply(404, {'error':'沒有此閱讀資源。'})
            if not target.is_file(): return self.reply(404, {'error':'檔案不存在。'})
            body = target.read_bytes()
            self.reader_revision = hashlib.sha256(body).hexdigest()
            if target == self.server.page:
                injection = 'const LOCAL_SYNC='+json.dumps({'token':self.server.token,
                            'revision':hashlib.sha256(body).hexdigest()})+';'
                body = body.replace(b'const LOCAL_SYNC=null;', injection.encode(), 1)
            elif target == self.server.root/'index.html' or (target.parent == self.server.page.parent and target.name in {'index.html','knowledge.html','thinking.html'}):
                # Catalogue filters already live in the URL. Preserve scroll on
                # automatic reload; never rewrite or inject the files on disk.
                script = """<script>(function(){const revision=REVISION,key='reader-auto:'+location.pathname;let busy=0;for(const e of ['keydown','input','pointerdown','wheel'])document.addEventListener(e,()=>busy=Date.now(),{passive:true});try{const saved=JSON.parse(sessionStorage.getItem(key)||'null');if(saved){sessionStorage.removeItem(key);requestAnimationFrame(()=>{scrollTo(0,saved.y);if(saved.focus)document.getElementById(saved.focus)?.focus({preventScroll:true});});}}catch{}setInterval(async()=>{try{const r=await fetch(location.href,{method:'HEAD',cache:'no-store'});if(!r.ok||document.hidden||Date.now()-busy<2500)return;if(r.headers.get('X-Reader-Revision')!==revision){try{sessionStorage.setItem(key,JSON.stringify({y:scrollY,focus:document.activeElement?.id}));}catch{}location.reload();}}catch{}},5000);})();</script>""".replace('REVISION', json.dumps(self.reader_revision))
                body += script.encode()
            kind = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            if suffix in {'.html','.js','.css'}: kind += '; charset=utf-8'
            return self.reply(200, body, kind)
        except (ValueError, OSError): return self.reply(404, {'error':'沒有此閱讀資源。'})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == '/api/refresh-jobs':
            if not self.allowed_preference_request():
                return self.reply(403, {'error':'此同步收據只供目前本機閱讀服務使用。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > 128:
                    raise ValueError('Invalid reader refresh receipt')
                value = json.loads(self.rfile.read(length).decode('utf-8'))
                job_id = value.get('job_id') if isinstance(value, dict) else None
                if not isinstance(job_id, str) or not _REFRESH_JOB_ID.fullmatch(job_id):
                    raise ValueError('Invalid reader refresh receipt')
                receipt, created = self.server.submit_refresh_job(job_id)
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                return self.reply(400, {'error':'同步收據無效，沒有開始新的同步。'})
            self.server.last_active = time.monotonic()
            if not created:
                return self.reply(202, receipt)
            # This handler owns the accepted job.  ThreadingHTTPServer can
            # still answer receipt polls after a deliberately short client
            # request times out, without another worker or duplicate build.
            self.server._run_refresh_job(job_id)
            return self.reply(200, self.server.refresh_job(job_id) or {'job_id':job_id, 'status':'unknown'})
        if path == '/api/preferences':
            if not self.allowed_preference_request():
                return self.reply(403, {'error':'此設定只供目前本機星雲使用。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > 1024: raise ValueError("Invalid reader preferences")
                value = json.loads(self.rfile.read(length).decode('utf-8'))
                if not self.server.begin_write():
                    return self.reply(409, {'error':'閱讀服務正在安全接替，沒有寫入偏好設定。'})
                try:
                    saved = save_preferences(self.server.vault, value)
                finally:
                    self.server.end_write()
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, OSError):
                return self.reply(400, {'error':'星雲偏好格式不正確，原有設定沒有改動。'})
            self.server.last_active = time.monotonic()
            return self.reply(200, {'preferences': saved})
        if path == '/api/handoff':
            # Keep the auth test adjacent to this destructive lifecycle action:
            # same origin and a token injected only into the current page.
            if not self.allowed_lifecycle_request():
                return self.reply(403, {'error':'閱讀服務接替只供目前本機星雲頁面使用。'})
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > 256: raise ValueError("Invalid reader handoff")
                value = json.loads(self.rfile.read(length).decode('utf-8'))
                target_generation = value.get('generation') if isinstance(value, dict) else None
                if not isinstance(target_generation, str) or not is_newer_generation(target_generation):
                    raise ValueError("Invalid reader handoff")
            except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                return self.reply(400, {'error':'閱讀服務接替要求無效，原有服務仍在運作。'})
            if not self.server.begin_handoff():
                return self.reply(409, {'error':'閱讀服務已在接替中，沒有新增寫入。'})
            return self.reply(200, {'status':'retiring', 'generation':SERVICE_GENERATION})
        if (not self.allowed_request() or self.headers.get('Origin') != self.server.origin
                or not secrets.compare_digest(self.headers.get('X-Nebula-Token', ''), self.server.token)):
            return self.reply(403, {'error':'請由知識星雲頁面使用同步按鈕。'})
        if path != '/api/refresh': return self.reply(404, {'error':'沒有此操作。'})
        if not self.server.begin_write(): return self.reply(409, {'error':'另一個同步正在進行或服務正在安全接替，請稍後再試。'})
        self.server.last_active = time.monotonic()
        try:
            return self.reply(200, self.server.refresh_projections())
        except (ValueError, OSError, KeyError, TypeError):
            self.server.update_error = True
            return self.reply(422, {'error':'同步未完成：資料正在修改或格式有問題。原有星雲已保留，請稍後再試。'})
        finally:
            self.server.last_active = time.monotonic()
            self.server.end_write()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--idle-seconds', type=int, default=180)
    parser.add_argument('--page', choices=('nebula', 'home'), default='nebula')
    parser.add_argument('--require-generation', default='')
    args = parser.parse_args()
    root = nebula.plain(args.root.absolute()).resolve()
    identity = hashlib.sha256(str(root).casefold().encode()).hexdigest()[:20]
    config = json.loads((root / 'brain.json').read_text(encoding='utf-8-sig'))
    vault = nebula.vault_path(root, config.get('vault', 'knowledge-base-vault'))
    nebula_page = vault / 'outcome-pages' / 'nebula.html'
    local_open = build_opener(ProxyHandler({})).open
    def existing_reader():
        def probe(origin, timeout):
            try:
                with local_open(origin+'/api/status', timeout=timeout) as response:
                    return json.load(response)
            except (OSError, ValueError, json.JSONDecodeError):
                return None
        return find_reader_state(identity, probe, timeout=.3)

    origin, current = existing_reader()
    if current is not None:
        if current.get('generation') != SERVICE_GENERATION:
            raise SystemExit('現有閱讀服務屬較舊版本，不能安全自動接替；請先由已授權的工作區助手完成遷移。')
        if current.get('retiring'):
            raise SystemExit('同一工作區的閱讀服務正在安全接替；請稍候並重用已驗證的本機網址。')
        url = (origin + '/index.html#prompts' if args.page == 'home'
               else origin + '/' + quote(nebula_page.relative_to(root).as_posix(), safe='/'))
        if not args.no_browser: webbrowser.open(url)
        print(json.dumps({'status':'already-running','url':url}), flush=True)
        return 0

    # This lease is held by the server, never by the spawning opener.  A
    # concurrent direct launch therefore cannot bind a second fallback port.
    lease = ReaderLease(root, 'service')
    if not lease.acquire():
        raise SystemExit('同一工作區的閱讀服務正在啟動；請稍候並重用已驗證的本機網址。')
    try:
        origin, current = existing_reader()
        if current is not None:
            if current.get('generation') != SERVICE_GENERATION:
                raise SystemExit('現有閱讀服務屬較舊版本，不能安全自動接替；請先由已授權的工作區助手完成遷移。')
            if current.get('retiring'):
                raise SystemExit('同一工作區的閱讀服務正在安全接替；請稍候並重用已驗證的本機網址。')
            url = (origin + '/index.html#prompts' if args.page == 'home'
                   else origin + '/' + quote(nebula_page.relative_to(root).as_posix(), safe='/'))
            print(json.dumps({'status':'already-running','url':url}), flush=True)
            return 0
        for port in candidate_ports(identity):
            try: server = NebulaServer(root, port, args.idle_seconds)
            except OSError: continue
            try:
                if not server.begin_write():
                    raise ValueError('reader service could not begin initial projection refresh')
                try:
                    server.refresh_projections()
                finally:
                    server.end_write()
                # Initial projection work can take longer than a deliberately
                # short test idle period.  Start the inactivity clock only once
                # the loopback endpoint can actually accept its first request.
                server.last_active = time.monotonic()
                url = server.home_url if args.page == 'home' else server.page_url
                print(json.dumps({'status':'ready','url':url}), flush=True)
                if not args.no_browser: webbrowser.open(url)
                server.serve_until_idle()
            finally: server.server_close()
            return 0
    finally:
        lease.release()
    raise SystemExit('本機連線無法啟動，請稍後重試。')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '知識星雲未能開啟。原有筆記沒有改動，請交由 工作區助手檢查本機資料與連線。', '知識星雲', 16)
        raise

