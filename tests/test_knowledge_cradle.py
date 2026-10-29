"""星之搖籃 background jobs: real HTTP against a temporary nebula root, with the deterministic fake runner (never the real claude)."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'runtime' / 'workspace').is_dir():
    ROOT = ROOT / 'runtime' / 'workspace'
sys.path.insert(0, str(ROOT / 'tools'))
import knowledge_cradle as cradle
import knowledge_nebula as nebula
import knowledge_nebula_server as server_module
import outcome_page_generator as generator
import thinking_archive as archive
from knowledge_nebula_server import NebulaServer

FAKE = Path(__file__).resolve().parent / 'cradle_fake_claude.py'
FAKE_CODEX = Path(__file__).resolve().parent / 'cradle_fake_codex.py'
import cradle_fixture as fixture_module
TOOLS = ['Edit', 'Glob', 'Grep', 'Read', 'Write']
def generation_after(steps=1):
    prefix, current = server_module.SERVICE_GENERATION.rsplit('-v', 1)
    return prefix + '-v' + str(int(current) + steps)


V7 = generation_after()


class CradleCase(unittest.TestCase):
    VAULT = 'vault'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cradle-')
        self.trace_temp = tempfile.TemporaryDirectory(prefix='cradle-trace-')
        self.root, self.trace = Path(self.temp.name), Path(self.trace_temp.name)
        packet, _, _ = fixture_module.fixture(self.root, self.VAULT)
        self.vault = Path(packet['vault'])
        (self.root / 'brain.json').write_text(json.dumps({'vault': self.VAULT}), encoding='utf-8')
        (self.vault / '_registry').mkdir(exist_ok=True)
        rows = [{'source_id': f'SRC-{n}', 'source_note_path': f'sources/SRC-{n}.md', 'status': 'linked'} for n in (1, 2)]
        (self.vault / '_registry/sources.jsonl').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
        nebula.build(self.root)
        self.notes = ['sources/SRC-1.md', 'sources/SRC-2.md']
        self.env = patch.dict(os.environ, {'CRADLE_FAKE_TRACE': str(self.trace), 'CRADLE_FAKE_SCENARIO': 'success',
                                          'CRADLE_FAKE_CODEX': 'success', 'CRADLE_FAKE_AUTH': 'claude.ai'})
        self.env.start()
        self.servers = []
        self.cleanup_processes = []
        self.settings()
        self.server = self.start()

    def tearDown(self):
        for server, thread, forever in reversed(self.servers):
            if forever:
                server.shutdown()
            server.server_close()
            thread.join(30)
        for process in self.cleanup_processes:
            if process.poll() is None:
                process.kill()
            process.wait()
        self.reap_leftovers()
        self.env.stop()
        self.trace_temp.cleanup()
        self.temp.cleanup()

    # ---------------------------------------------------------------- helpers
    def start(self, idle=None):
        server = NebulaServer(self.root, 0, idle) if idle else NebulaServer(self.root)
        thread = threading.Thread(target=server.serve_until_idle if idle else server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread, not idle))
        return server

    def settings(self, **changes):
        value = {'schema': 'knowledge_cradle_settings_v1', 'runner': 'claude', 'claude_command': [sys.executable, '-B', str(FAKE)],
                 'codex_command': [sys.executable, '-B', str(FAKE_CODEX)],
                 'timeouts': {'write': 40, 'other': 40}, 'max_review_rounds': 3, 'max_mechanical_fixes': 2}
        value.update(changes)
        folder = self.root / '.reader-runtime' / 'cradle'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'settings.json').write_text(json.dumps(value), encoding='utf-8')

    def scenario(self, name, **extra):
        os.environ['CRADLE_FAKE_SCENARIO'] = name
        for key, value in extra.items():
            os.environ[key] = str(value)

    def auth(self, server=None):
        server = server or self.server
        return {'Origin': server.origin, 'X-Nebula-Token': server.token}

    def api(self, method, path, body=None, headers='auth', raw=None, server=None):
        server = server or self.server
        sent = self.auth(server) if headers == 'auth' else dict(headers or {})
        data = raw
        if isinstance(body, bytes):
            data = body
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode('utf-8')
            sent.setdefault('Content-Type', 'application/json')
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
        try:
            connection.request(method, path, data, sent)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def post_job(self, kind='think', notes=None, question='自動灌溉之後，巡園要重新想甚麼？', server=None, **more):
        body = {'kind': kind, 'notes': notes or self.notes, 'question': question, **more}
        status, data = self.api('POST', '/api/cradle/jobs', body, server=server)
        self.assertEqual(status, 202, data)
        return data['job_id']

    def job(self, job_id, server=None):
        status, data = self.api('GET', f'/api/cradle/jobs/{job_id}', server=server)
        self.assertEqual(status, 200, data)
        return data

    def wait(self, job_id, *statuses, timeout=90, server=None):
        deadline = time.time() + timeout
        while True:
            job = self.job(job_id, server)
            if job['status'] in statuses:
                return job
            if time.time() > deadline:
                self.fail(f'job stayed {job["status"]} (step {job["step"]}): {job["error"]}')
            time.sleep(0.1)

    def traces(self):
        path = self.trace / 'trace.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []

    def codex_traces(self):
        path = self.trace / 'codex-trace.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []

    def wait_trace(self, predicate, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = [t for t in self.traces() if predicate(t)]
            if found:
                return found
            time.sleep(0.1)
        self.fail('the fake runner never reached the expected point')

    def hang_pids(self):
        pids = []
        for line in self.traces() + self.codex_traces():
            pids += [line['pid']] + ([line['grandchild']] if 'grandchild' in line else [])
        return pids

    def assert_gone(self, pids):
        deadline = time.time() + 5
        while any(archive._pid_alive(pid) for pid in pids) and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual([pid for pid in pids if archive._pid_alive(pid)], [])

    def reap_leftovers(self):
        """Whatever the fake runner left running (only pids it traced itself) is ended before the trace folder goes, even if a test failed."""
        for pid in self.hang_pids():
            if archive._pid_alive(pid) and (cradle.process_image_name(pid) or '').startswith('python'):
                cradle.end_tree_by_pid(pid)
                if archive._pid_alive(pid) and os.name == 'nt':                             # taskkill can be refused: end only this traced pid directly
                    import ctypes
                    kernel = ctypes.WinDLL('kernel32')
                    kernel.OpenProcess.restype = ctypes.c_void_p
                    kernel.TerminateProcess.argtypes, kernel.CloseHandle.argtypes = [ctypes.c_void_p, ctypes.c_uint], [ctypes.c_void_p]
                    handle = kernel.OpenProcess(0x0001, False, pid)                         # PROCESS_TERMINATE
                    if handle:
                        kernel.TerminateProcess(handle, 1)
                        kernel.CloseHandle(handle)

    def folder(self, job_id):
        return self.root / '.reader-runtime' / 'cradle' / 'jobs' / job_id

    def log(self, job_id):
        return [json.loads(line) for line in (self.folder(job_id) / 'log.jsonl').read_text(encoding='utf-8').splitlines()]

    def records(self):
        return sorted(p.name for p in (self.vault / 'thinking').glob('ASK-*.json')) if (self.vault / 'thinking').is_dir() else []

    def nebula_nodes(self):
        text = (self.vault / 'outcome-pages/nebula.html').read_text(encoding='utf-8')
        return json.loads(text.split('const DATA=', 1)[1].split(';\n', 1)[0])['nodes']


class RequestTests(CradleCase):
    def test_requests_are_validated_and_authenticated(self):
        good = {'kind': 'think', 'notes': self.notes, 'question': '問題'}
        plain = {'Content-Type': 'application/json'}
        self.assertEqual(self.api('POST', '/api/cradle/jobs', good, headers=plain)[0], 403)            # no token
        self.assertEqual(self.api('POST', '/api/cradle/jobs', good, headers={**self.auth(), 'Origin': 'http://evil.example'})[0], 403)
        self.assertEqual(self.api('POST', '/api/cradle/jobs', good, headers={'X-Nebula-Token': self.server.token})[0], 403)  # a POST needs the Origin
        self.assertEqual(self.api('POST', '/api/cradle/jobs', good, headers={**self.auth(), 'X-Nebula-Token': 'wrong'})[0], 403)
        for path in ('/api/cradle/jobs', '/api/cradle/jobs/CJ-20260101-0001', '/api/cradle/jobs/CJ-20260101-0001/draft'):
            self.assertEqual(self.api('GET', path, headers={})[0], 403)
            self.assertEqual(self.api('GET', path, headers={'Origin': 'http://evil.example', 'X-Nebula-Token': self.server.token})[0], 403)
        bad = [
            {**good, 'notes': ['/etc/x.md']}, {**good, 'notes': ['C:/x.md']}, {**good, 'notes': ['../x.md']}, {**good, 'notes': ['sources/../x.md']},
            {**good, 'notes': ['sources\\SRC-1.md']}, {**good, 'notes': ['sources/SRC-1.txt']}, {**good, 'notes': ['sources/missing.md']},
            {**good, 'notes': ['sources/SRC-1.md', 'sources/SRC-1.md']}, {**good, 'notes': [f'sources/n{n}.md' for n in range(7)]},
            {**good, 'notes': []}, {**good, 'notes': 'sources/SRC-1.md'}, {**good, 'notes': [5]},
            {**good, 'question': '字' * 501}, {**good, 'question': ' \r\n '}, {**good, 'question': 5}, {k: v for k, v in good.items() if k != 'question'},
            {**good, 'extra': 1}, {**good, 'kind': 'bad'}, {**good, 'kind': []}, {**good, 'kind': None}, {**good, 'way': 'think'},
            {'kind': 'questions', 'notes': self.notes}, {'kind': 'questions', 'notes': self.notes, 'way': 'bad'},
            {**good, 'basket': ['x'] * 7}, {**good, 'basket': ['x' * 161]}, {**good, 'basket': 'x'}, {**good, 'encounter': 'x'},
            {**good, 'encounter': {'text': '字' * 1000}}, {**good, 'from_questions': 'CJ-20260101-0001'},
            {'kind': 'questions', 'way': 'think', 'notes': self.notes, 'from_questions': 'CJ-20260101-0001'}, [], 'text',
        ]
        for body in bad:
            with self.subTest(body=str(body)[:80]):
                status, data = self.api('POST', '/api/cradle/jobs', body)
                self.assertEqual((status, data), (400, {'error': '孕育要求格式不正確，沒有開始孕育。'}))
        for raw, headers in ((b'', {'Content-Type': 'application/json'}), (b'{not json', {'Content-Type': 'application/json'}),
                             (json.dumps(good).encode(), {'Content-Type': 'text/plain'}), (b'{"x":"' + b'a' * 17000 + b'"}', {'Content-Type': 'application/json'}),
                             ('\udcff'.encode('utf-8', 'surrogatepass'), {'Content-Type': 'application/json'})):
            status, _ = self.api('POST', '/api/cradle/jobs', raw=raw, headers={**self.auth(), **headers})
            self.assertEqual(status, 400)
        self.assertEqual(self.api('GET', '/api/cradle/jobs/CJ-20260101-0001')[1], {'error': '沒有此孕育工作。'})
        self.assertEqual(self.api('GET', '/api/cradle/jobs/nope')[0], 404)
        self.assertEqual(self.api('POST', '/api/cradle/jobs/CJ-20260101-0001/retry', b'')[0], 404)
        self.assertEqual(self.api('GET', '/api/cradle/jobs')[1], {'jobs': [], 'active': 0, 'awaiting_pick': 0,
                                                              'runner': {'name': 'claude', 'label': 'Claude Code', 'ready': None}})
        self.assertFalse((self.root / '.reader-runtime' / 'cradle' / 'jobs').exists())  # nothing was accepted, so no job folder was created

    def test_constructing_a_server_creates_no_runtime_folder(self):
        import shutil
        shutil.rmtree(self.root / '.reader-runtime')
        fresh = NebulaServer(self.root)
        fresh.server_close()
        self.assertFalse((self.root / '.reader-runtime').exists())

    def test_runtime_folder_links_are_refused(self):
        outside = Path(self.temp.name + '-outside')
        outside.mkdir()
        self.addCleanup(lambda: outside.exists() and outside.rmdir())
        link = self.root / '.reader-runtime'
        if link.exists():
            for child in link.rglob('*'):
                pass
            import shutil
            shutil.rmtree(link)
        made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True) if os.name == 'nt' else None
        if made is None or made.returncode != 0:
            try:
                os.symlink(outside, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('this account cannot create a link or junction')
        status, _ = self.api('POST', '/api/cradle/jobs', {'kind': 'think', 'notes': self.notes, 'question': '問題'})
        self.assertEqual(status, 500)
        self.assertEqual(list(outside.iterdir()), [])
        os.rmdir(link) if os.name == 'nt' else link.unlink()


class PipelineTests(CradleCase):
    def test_think_job_ends_as_a_new_star_with_a_clean_trail(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'sk-test-not-real'}):
            self.assertIn('ANTHROPIC_API_KEY', os.environ)
            status, made = self.api('POST', '/api/cradle/jobs', {'kind': 'think', 'notes': self.notes, 'question': '自動灌溉之後\n巡園要重新想甚麼？',
                                                                 'basket': ['n1'], 'encounter': {'tier': 1}})
            self.assertEqual((status, made['position']), (202, 0))
            self.assertRegex(made['job_id'], r'CJ-\d{8}-0001')
            job = self.wait(made['job_id'], 'done')
        job_id = made['job_id']
        self.assertEqual((job['status'], job['step'], job['eta_seconds'], job['error'], job['runner']), ('done', None, None, None, 'claude'))
        self.assertEqual((job['question'], job['basket'], job['notes'], job['round']), ('自動灌溉之後 巡園要重新想甚麼？', ['n1'], self.notes, 1))
        self.assertNotIn('way', job)
        key = job['result']['star_id']
        self.assertEqual(job['result'], {'record': f'thinking/{key}.md', 'star_id': key, 'href': f'thinking/{key}.html', 'saved': True, 'in_nebula': True})
        self.assertEqual(self.records(), [key + '.json'])
        star = [n for n in self.nebula_nodes() if n['kind'] == 'thought']
        self.assertEqual([(n['id'], n['href']) for n in star], [(key, f'thinking/{key}.html')])
        self.assertTrue((self.vault / 'outcome-pages' / 'thinking' / f'{key}.html').is_file())
        self.assertEqual(self.server.applied_reader_input, server_module.reader_input_stamp(self.root))   # the watcher will not rebuild the same input
        # the AI saw no key, only the restricted flags, and every run was checked
        calls = self.traces()
        self.assertEqual([c['step'] for c in calls], ['write', 'review'])
        self.assertTrue(all(c['api_key'] is False for c in calls))
        for flag in ('--restricted', '--strict-mcp-config', '--no-session-persistence'):
            self.assertIn(flag, calls[0]['argv'])
        self.assertEqual(calls[0]['argv'][calls[0]['argv'].index('--tools') + 1], 'Read,Write,Edit,Glob,Grep')
        log = self.log(job_id)
        self.assertEqual([(line['run'], line['step']) for line in log], [('01-write', 'write'), ('02-review', 'review')])
        for line in log:
            self.assertEqual((line['tools'], line['apiKeySource'], line['mcp_servers'], line['exit'], line['agent_receipt'], line['error_code']),
                             (TOOLS, 'none', [], 0, 'completed', None))
            self.assertEqual((line['cost_usd'], line['num_turns'], line['denials'], line['rate_limit_status']), (0.0123, 3, 0, 'allowed'))
            self.assertEqual(Path(line['argv0']), Path(sys.executable))
        # only the files listed for each step reached the AI
        runs = self.folder(job_id) / 'runs'
        self.assertEqual(sorted(p.name for p in (runs / '01-write').iterdir()),
                         ['answer.json', 'packet.json', 'receipt.json', 'spec.md', 'task.md', 'tension_check.py', 'thought_analysis.py'])
        self.assertEqual(sorted(p.name for p in (runs / '02-review').iterdir()),
                         ['answer.json', 'packet.json', 'receipt.json', 'review.json', 'spec.md', 'target.txt', 'task.md', 'thought_analysis.py'])
        self.assertTrue((runs / '01-write.events.jsonl').is_file() and (runs / '01-write.stderr.txt').is_file())
        self.assertEqual(cradle.WRITE_TASK, (runs / '01-write' / 'task.md').read_text(encoding='utf-8'))
        # the page token is never written anywhere
        token = self.server.token.encode()
        for path in (self.root / '.reader-runtime' / 'cradle').rglob('*'):
            if path.is_file():
                self.assertNotIn(token, path.read_bytes(), path.name)
        receipt = json.loads((self.folder(job_id) / 'receipt.json').read_text(encoding='utf-8'))
        self.assertEqual((receipt['job_id'], receipt['kind'], receipt['status'], receipt['runner'], receipt['error']), (job_id, 'think', 'done', 'claude', None))
        self.assertEqual([(s['step'], s['run'], s['exit'], s['agent_receipt']) for s in receipt['steps']], [('write', '01-write', 0, 'completed'), ('review', '02-review', 0, 'completed')])
        self.assertEqual((receipt['review'], receipt['result']), ({'rounds': 1, 'verdicts': [['accept']]}, job['result']))
        self.assertIsInstance(receipt['minutes'], float)
        self.assertIsNone(json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))['pid'])
        # the list, the draft and the summary view
        status, listing = self.api('GET', '/api/cradle/jobs')
        self.assertEqual((status, listing['active'], listing['awaiting_pick'], [j['job_id'] for j in listing['jobs']]), (200, 0, 0, [job_id]))
        self.assertTrue(all(k not in listing['jobs'][0] for k in ('notes', 'basket', 'questions')))
        status, draft = self.api('GET', f'/api/cradle/jobs/{job_id}/draft')
        self.assertEqual(status, 200)
        self.assertIn('自動灌溉之後 巡園要重新想甚麼？', draft['markdown'])
        self.assertEqual([(r['id'], r['verdict']) for r in draft['review']], [('I1', 'accept')])
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 409)
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/cancel', b'')[0], 409)

    def test_two_jobs_wait_their_turn(self):
        self.scenario('slow', CRADLE_FAKE_DELAY=1)
        first = self.post_job()
        status, second = self.api('POST', '/api/cradle/jobs', {'kind': 'gap', 'notes': self.notes, 'question': '第二條'})
        self.assertEqual((status, second['position']), (202, 1))
        self.wait_trace(lambda t: True)
        self.assertEqual(self.job(second['job_id'])['status'], 'queued')
        self.assertIsNone(self.job(second['job_id'])['started_at'])
        a, b = self.wait(first, 'done'), self.wait(second['job_id'], 'done')
        self.assertLessEqual(a['ended_at'], b['started_at'])
        self.assertEqual([c['step'] for c in self.traces()], ['write', 'review'] * 2)
        self.assertEqual(len(self.records()), 2)

    def test_queue_is_capped_and_estimates_follow_the_pipeline(self):
        self.scenario('hang')
        ids = [self.post_job(question=f'問題{n}') for n in range(10)]
        self.wait_trace(lambda t: 'grandchild' in t)
        status, refused = self.api('POST', '/api/cradle/jobs', {'kind': 'think', 'notes': self.notes, 'question': '第十一'})
        self.assertEqual((status, refused), (409, {'error': '排隊的孕育太多，請等現有的完成。'}))
        waiting = self.job(ids[1])
        self.assertEqual((waiting['status'], waiting['eta_seconds'], waiting['step']), ('queued', 5 + 240 + 5 + 150 + 30, None))
        running = self.job(ids[0])
        self.assertEqual((running['status'], running['step']), ('running', 'write'))
        self.assertGreaterEqual(running['eta_seconds'], 5 + 5 + 150 + 30 + 30)
        status, listing = self.api('GET', '/api/cradle/jobs')
        self.assertEqual((listing['active'], len(listing['jobs']), listing['jobs'][0]['job_id']), (10, 10, ids[-1]))
        self.assertEqual(self.server.cradle.busy(), 10)

    def test_a_retried_older_job_is_counted_behind_the_newer_ones_in_the_real_queue_order(self):
        self.scenario('hang')
        first, second, third = self.post_job(question='甲'), self.post_job(question='乙'), self.post_job(question='丙')
        self.wait_trace(lambda t: 'grandchild' in t)
        self.assertEqual([self.job(j)['position'] for j in (first, second, third)], [None, 1, 2])
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{first}/cancel', b'')[0], 200)
        self.wait(second, 'running')
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{first}/retry', b'')[0], 200)   # the oldest id goes to the back of the queue
        expected = {first: 2, second: None, third: 1}                                       # B is running, C is next, A is last (A's id is older than C's)
        self.assertEqual({j: self.job(j)['position'] for j in expected}, expected)
        listing = self.api('GET', '/api/cradle/jobs')[1]['jobs']
        self.assertEqual({j['job_id']: j['position'] for j in listing}, expected)
        done = self.server.cradle.status(first)
        self.assertEqual(done['position'], 2)
        for job_id in (first, second, third):
            self.api('POST', f'/api/cradle/jobs/{job_id}/cancel', b'')
        self.assertEqual([self.job(j)['position'] for j in expected], [None, None, None])    # an ended job is nowhere in the queue

    def test_review_round_two_and_previous_review(self):
        self.scenario('revise-then-accept')
        job_id = self.post_job()
        job = self.wait(job_id, 'done')
        self.assertEqual(job['round'], 2)
        self.assertEqual([c['step'] for c in self.traces()], ['write', 'review', 'revise', 'review'])
        runs = self.folder(job_id) / 'runs'
        self.assertNotIn('previous_review.json', {p.name for p in (runs / '02-review').iterdir()})
        self.assertEqual({p.name for p in (runs / '03-revise').iterdir()} - {'receipt.json', 'answer.json'},
                         {'packet.json', 'spec.md', 'thought_analysis.py', 'tension_check.py', 'task.md', 'review.json'})
        self.assertIn('previous_review.json', {p.name for p in (runs / '04-review').iterdir()})
        self.assertIn('另有 previous_review.json', (runs / '04-review' / 'task.md').read_text(encoding='utf-8'))
        receipt = json.loads((self.folder(job_id) / 'receipt.json').read_text(encoding='utf-8'))
        self.assertEqual(receipt['review'], {'rounds': 2, 'verdicts': [['revise'], ['accept']]})
        record = json.loads((self.vault / 'thinking' / self.records()[0]).read_text(encoding='utf-8'))
        self.assertIn('已按覆核意見修訂', record['answer']['analysis']['thesis'])

    def test_never_accepted_answer_stops_after_three_rounds_and_can_start_again(self):
        self.scenario('never-accept')
        job_id = self.post_job()
        job = self.wait(job_id, 'failed')
        self.assertEqual([c['step'] for c in self.traces()], ['write', 'review', 'revise', 'review', 'revise', 'review'])
        error = job['error']
        self.assertEqual((error['code'], error['retryable'], error['actions']), ('review_not_converged', True, ['retry', 'edit_question', 'draft']))
        self.assertEqual(error['message'], '覆核者仍認為有推論站不住：推論跳過了替代條件，要補寫。')
        self.assertEqual((job['result'], self.records()), (None, []))
        self.assertEqual(self.api('GET', f'/api/cradle/jobs/{job_id}/draft')[0], 200)      # the unaccepted draft stays readable
        self.scenario('success')
        status, again = self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')
        self.assertEqual((status, again['status'], again['attempt']), (200, 'queued', 2))
        done = self.wait(job_id, 'done')
        self.assertEqual((done['attempt'], len(self.records())), (2, 1))
        self.assertTrue((self.folder(job_id) / 'runs' / '07-write').is_dir())            # run numbers keep counting; earlier runs are kept
        self.assertEqual(self.log(job_id)[6]['attempt'], 2)

    def test_bad_quotes_are_fixed_or_the_job_stops(self):
        self.scenario('bad-quote-then-fixed')
        job_id = self.post_job()
        self.wait(job_id, 'done')
        self.assertEqual([c['step'] for c in self.traces()], ['write', 'fix', 'review'])
        fix = self.folder(job_id) / 'runs' / '02-fix'
        self.assertEqual(sorted(p.name for p in fix.iterdir()), ['answer.json', 'mechanical_errors.txt', 'packet.json', 'receipt.json', 'spec.md', 'task.md', 'tension_check.py', 'thought_analysis.py'])
        self.assertIn('不存在於任何證據的引文', (fix / 'mechanical_errors.txt').read_text(encoding='utf-8'))
        self.assertIn('不存在於任何證據的引文', (self.folder(job_id) / 'runs' / '01-write' / 'answer.json').read_text(encoding='utf-8'))
        self.assertNotIn('不存在於任何證據的引文', (fix / 'answer.json').read_text(encoding='utf-8'))
        self.scenario('unfixable-quote')
        second = self.post_job()
        job = self.wait(second, 'failed')
        self.assertEqual((job['error']['code'], job['error']['retryable'], job['error']['actions']), ('mechanical_unfixed', True, ['retry']))
        self.assertEqual(job['error']['message'], '答案的引文與原文不符，修正兩次仍未通過。')
        self.assertEqual([c['step'] for c in self.traces()][3:], ['write', 'fix', 'fix'])
        self.assertEqual(len(self.records()), 1)

    def test_review_format_problem_gets_one_more_review_in_the_same_round(self):
        self.scenario('review-bad-once')
        job_id = self.post_job()
        job = self.wait(job_id, 'done')
        self.assertEqual((job['round'], [c['step'] for c in self.traces()]), (1, ['write', 'review', 'review']))
        self.scenario('review-bad-format')
        second = self.post_job()
        failed = self.wait(second, 'failed')
        self.assertEqual((failed['error']['code'], failed['error']['message'], failed['error']['retryable']), ('review_invalid', '覆核結果格式不正確，未能完成覆核。', True))

    def test_archive_stopped_after_the_save_resumes_with_one_record(self):
        with patch.object(generator, 'refresh_outcome_index', side_effect=OSError('interrupted')):
            job_id = self.post_job()
            job = self.wait(job_id, 'failed')
        self.assertEqual((job['error']['code'], job['error']['retryable'], job['error']['actions']), ('archive_failed', True, ['retry']))
        self.assertTrue(job['error']['message'].startswith('答案已通過覆核，但存檔失敗：') and job['error']['message'].endswith('。草稿已保留。'))
        self.assertEqual((len(self.records()), job['result']), (1, None))                 # saved, but the readers do not exist yet
        calls_before = len(self.traces())
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 200)
        done = self.wait(job_id, 'done')
        self.assertEqual((done['result']['in_nebula'], len(self.records()), len(self.traces())), (True, 1, calls_before))   # archive only: no new AI run
        self.assertEqual(len(list((self.vault / 'thinking').glob('ASK-*.md'))), 1)

    def test_nebula_that_cannot_show_the_star_is_refreshed_by_retry(self):
        with patch.object(server_module.NebulaServer, 'refresh_projections', side_effect=OSError('locked')), \
                patch('thinking_archive.refresh_nebula', return_value={'status': 'refresh_failed', 'error': 'OSError: locked'}):
            job_id = self.post_job()
            job = self.wait(job_id, 'failed')
            self.assertEqual((job['error']['code'], job['error']['actions'], job['error']['message']),
                             ('nebula_not_updated', ['refresh'], '思考紀錄已存好，但星雲暫時未能顯示新星：OSError: locked。'))
            self.assertTrue(job['result']['saved'] and not job['result']['in_nebula'])
            self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')
            again = self.wait(job_id, 'failed')                                            # refresh fails again with the new reason
            self.assertEqual(again['error']['message'], '思考紀錄已存好，但星雲暫時未能顯示新星：OSError: locked。')
        self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')
        done = self.wait(job_id, 'done')
        self.assertEqual((done['result']['in_nebula'], len(self.records())), (True, 1))
        self.assertEqual(len(self.traces()), 2)

    def test_refresh_job_and_archive_wait_for_each_other(self):
        entered, release = threading.Event(), threading.Event()
        real = archive.archive_answer

        def held(*args, **kwargs):
            entered.set()
            release.wait(20)
            return real(*args, **kwargs)
        refresh_id = 'a' * 32
        outcome = {}
        with patch.object(archive, 'archive_answer', side_effect=held):
            job_id = self.post_job()
            self.assertTrue(entered.wait(60))
            self.assertTrue(self.server.build_lock.locked())

            def refresh():
                outcome['status'] = self.api('POST', '/api/refresh-jobs', {'job_id': refresh_id}, headers={'X-Nebula-Token': self.server.token, 'Origin': self.server.origin})

            thread = threading.Thread(target=refresh)
            thread.start()
            time.sleep(1)
            self.assertTrue(thread.is_alive())                                             # the refresh job is waiting for the archive's admission
            self.assertEqual(self.api('GET', f'/api/refresh-jobs/{refresh_id}')[1]['status'], 'pending')
            release.set()
            thread.join(60)
        done = self.wait(job_id, 'done')
        self.assertEqual((outcome['status'][0], outcome['status'][1]['status']), (200, 'ready'))
        self.assertTrue(done['result']['in_nebula'])
        self.assertFalse(self.server.build_lock.locked())


    def test_job_waiting_for_archive_admission_can_still_be_cancelled(self):
        self.assertTrue(self.server.begin_write())                                            # someone else holds the write lock
        try:
            job_id = self.post_job()
            deadline = time.time() + 60
            while time.time() < deadline and self.job(job_id)['step'] != 'archive':
                time.sleep(0.1)
            self.assertEqual(self.job(job_id)['step'], 'archive')
            status, cancelled = self.api('POST', f'/api/cradle/jobs/{job_id}/cancel', b'')
            self.assertEqual((status, cancelled['status'], cancelled['error']), (200, 'cancelled', None))
        finally:
            self.server.end_write()
        time.sleep(1.5)                                                                       # the worker must neither save nor overwrite the cancellation
        self.assertEqual((self.job(job_id)['status'], self.records(), self.server.build_lock.locked()), ('cancelled', [], False))
        self.assertEqual(json.loads((self.folder(job_id) / 'receipt.json').read_text(encoding='utf-8'))['status'], 'cancelled')
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 200)   # and a cancelled job can start again
        self.assertEqual(self.wait(job_id, 'done')['result']['saved'], True)
        self.assertEqual(len(self.records()), 1)

    def test_job_that_is_saving_cannot_be_cancelled(self):
        entered, release = threading.Event(), threading.Event()
        real = archive.archive_answer

        def held(*args, **kwargs):
            entered.set()
            release.wait(20)
            return real(*args, **kwargs)
        with patch.object(archive, 'archive_answer', side_effect=held):
            job_id = self.post_job()
            self.assertTrue(entered.wait(60))
            self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/cancel', b''), (409, {'error': '正在存成新星，請稍候，完成後才能停止。'}))
            release.set()
        done = self.wait(job_id, 'done')
        self.assertEqual((done['result']['saved'], len(self.records())), (True, 1))


class SubfolderVaultPipelineTests(CradleCase):
    VAULT = 'notes/vault'

    def test_think_job_ends_as_a_new_star_with_a_clean_trail(self):
        PipelineTests.test_think_job_ends_as_a_new_star_with_a_clean_trail(self)
        records = list((self.root / self.VAULT / 'thinking').glob('ASK-*.json'))
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].is_relative_to(self.root / self.VAULT))
        cradle_root = self.root / '.reader-runtime' / 'cradle'
        self.assertTrue(cradle_root.is_dir())
        self.assertFalse((self.root / self.VAULT / '.reader-runtime').exists())
        key = records[0].stem
        nebula_page = self.root / self.VAULT / 'outcome-pages' / 'nebula.html'
        self.assertTrue(nebula_page.is_file())
        self.assertIn(key, nebula_page.read_text(encoding='utf-8'))


class FailureTests(CradleCase):
    def test_runner_failures_are_named_in_plain_words(self):
        cases = [
            ('not-logged-in', 'not_logged_in', 'Claude 未登入，請先打開 Claude 登入一次。', True, ['retry']),
            ('usage-limit', 'usage_limit', '已到用量上限，約 3 小時後可再試。', True, ['retry_later']),
            ('usage-limit-no-reset', 'usage_limit', '已到用量上限，請稍後再試。', True, ['retry_later']),
            ('unsafe-tools', 'unsafe_runner', '背景 AI 的安全設定與預期不符，已停止，沒有使用它的結果。', False, []),
            ('unsafe-key', 'unsafe_runner', '背景 AI 的安全設定與預期不符，已停止，沒有使用它的結果。', False, []),
            ('no-init', 'unsafe_runner', '背景 AI 的安全設定與預期不符，已停止，沒有使用它的結果。', False, []),
            ('receipt-failed', 'agent_failed', 'AI 回報未能完成：合成失敗原因', True, ['retry', 'edit_question']),
            ('no-receipt', 'agent_failed', 'AI 回報未能完成：沒有收據', True, ['retry', 'edit_question']),
        ]
        retry_me = None
        for name, code, message, retryable, actions in cases:
            with self.subTest(scenario=name):
                self.scenario(name)
                job_id = self.post_job()
                job = self.wait(job_id, 'failed')
                self.assertEqual((job['error']['code'], job['error']['message'], job['error']['retryable'], job['error']['actions']), (code, message, retryable, actions))
                self.assertEqual((job['result'], self.records(), self.log(job_id)[0]['error_code']), (None, [], code))
                if code == 'unsafe_runner':          # its files were never used, and retry is refused
                    self.assertNotIn('answer_path', job)
                    self.assertEqual(self.api('GET', f'/api/cradle/jobs/{job_id}/draft')[0], 404)
                    self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 409)
                if name == 'not-logged-in':
                    retry_me = job_id
        packet = (self.folder(retry_me) / 'packet.json').read_bytes()
        self.scenario('success')
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{retry_me}/retry', b'')[0], 200)
        done = self.wait(retry_me, 'done')
        self.assertEqual((done['attempt'], (self.folder(retry_me) / 'packet.json').read_bytes()), (1, packet))   # same step, same fixed packet

    def test_missing_runner_program(self):
        self.settings(claude_command=[str(self.root / 'no-such-claude.exe')])
        job = self.wait(self.post_job(), 'failed')
        self.assertEqual((job['error']['code'], job['error']['message'], job['error']['actions'], job['error']['retryable']),
                         ('tool_missing', '這部電腦找不到 Claude Code。', ['copy'], True))

    def test_timeout_ends_the_whole_process_tree(self):
        self.scenario('hang')
        self.settings(claude_command=[sys.executable, '-B', str(FAKE)], timeouts={'write': 2, 'other': 2})
        job_id = self.post_job()
        job = self.wait(job_id, 'failed')
        self.assertEqual((job['error']['code'], job['error']['message'], job['error']['retryable'], job['error']['actions']),
                         ('timeout', '寫答案超過 1 分鐘仍未完成，已停止。', True, ['retry']))
        pids = self.hang_pids()
        self.assertEqual(len(pids), 2)                                                      # the AI itself and its grandchild
        self.assert_gone(pids)
        self.assertTrue(self.log(job_id)[0]['timed_out'])
        self.assertIsNone(json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))['pid'])

    def test_cancel_kills_the_running_tree_and_stays_cancelled(self):
        self.scenario('hang')
        first, second = self.post_job(), self.post_job(question='第二')
        self.wait_trace(lambda t: 'grandchild' in t)
        running = json.loads((self.folder(first) / 'job.json').read_text(encoding='utf-8'))
        self.assertIsInstance(running['pid'], int)                                          # the child's pid is recorded while it runs
        status, queued = self.api('POST', f'/api/cradle/jobs/{second}/cancel', b'')
        self.assertEqual((status, queued['status']), (200, 'cancelled'))
        self.assertEqual(len(self.traces()), 1)                                              # the queued job never started an AI
        status, cancelled = self.api('POST', f'/api/cradle/jobs/{first}/cancel', b'')
        self.assertEqual((status, cancelled['status'], cancelled['error']), (200, 'cancelled', None))
        pids = self.hang_pids()
        self.assert_gone(pids)
        time.sleep(1.5)                                                                     # the worker must not overwrite the cancellation
        for job_id in (first, second):
            self.assertEqual(self.job(job_id)['status'], 'cancelled')
            self.assertEqual(json.loads((self.folder(job_id) / 'receipt.json').read_text(encoding='utf-8'))['status'], 'cancelled')
        self.assertEqual(json.loads((self.folder(first) / 'job.json').read_text(encoding='utf-8'))['pid'], None)
        self.assertEqual(self.records(), [])
        self.scenario('success')
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{first}/retry', b'')[0], 200)   # a cancelled job can be retried
        self.assertEqual(self.wait(first, 'done')['status'], 'done')

    @unittest.skipUnless(os.name == 'nt', 'Job Objects are a Windows mechanism')
    def test_ai_without_a_job_object_is_ended_and_the_run_fails_closed(self):
        self.scenario('orphan', CRADLE_FAKE_SPAWN_DELAY=2)                                 # the AI would start its helper 2 s after launch
        started = []
        original = subprocess.Popen

        class Recording(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                started.append(self)
        with patch.object(cradle.JobObject, 'attach', return_value=None), patch.object(cradle.subprocess, 'Popen', Recording):
            job_id = self.post_job()
            job = self.wait(job_id, 'failed', 'done')
        self.assertEqual((job['status'], job['error']['code'], job['error']['message'], job['error']['retryable'], job['error']['actions']),
                         ('failed', 'unsafe_runner', '背景 AI 的安全設定與預期不符，已停止，沒有使用它的結果。', False, []))
        ai = [p for p in started if str(FAKE) in map(str, p.args)]
        self.assertEqual(len(ai), 1)
        self.assertIsNotNone(ai[0].poll())                                                  # the AI itself was ended, at once
        time.sleep(3)                                                                       # long enough for it to have run on: it did not
        self.assertEqual(self.traces(), [])
        self.assert_gone(self.hang_pids())
        self.assertEqual((self.log(job_id)[0]['error_code'], self.records(), len(self.log(job_id))), ('unsafe_runner', [], 1))
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 409)

    def _contained_failure(self, **patches):
        """A spawn-first run whose containment is sabotaged (taskkill refused as well): checks the run failed closed and nothing ever ran."""
        self.scenario('spawn-first')
        started, original = [], subprocess.Popen

        class Recording(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                started.append(self)
        with ExitStack() as stack:
            stack.enter_context(patch.object(cradle, 'end_tree_by_pid', return_value=False))   # "ERROR: Access denied"
            stack.enter_context(patch.object(cradle.subprocess, 'Popen', Recording))
            for name, value in patches.items():
                stack.enter_context(patch.object(cradle.JobObject if name == 'attach' else cradle, name, value, create=True))
            job_id = self.post_job()
            job = self.wait(job_id, 'failed', 'done', timeout=120)
        ai = [p for p in started if str(FAKE) in map(str, p.args)]
        self.assertEqual(len(ai), 1)
        self.assertIsNotNone(ai[0].poll())                                                  # the suspended AI was ended and its exit confirmed
        self.assertEqual((job['status'], job['error']['code'], job['error']['retryable']), ('failed', 'unsafe_runner', False))
        time.sleep(2)                                                                       # long enough for a leaked process to have started something
        self.assertEqual(self.traces(), [])                                                 # the fake runner never executed its first line ...
        self.assertEqual(self.hang_pids(), [])                                              # ... so neither it nor any grandchild ever existed
        self.assertEqual((self.log(job_id)[0]['error_code'], self.records(), len(self.log(job_id))), ('unsafe_runner', [], 1))
        self.assertIsNone(json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))['pid'])

    @unittest.skipUnless(os.name == 'nt', 'Job Objects are a Windows mechanism')
    def test_refused_job_object_assignment_never_lets_the_ai_run_even_when_taskkill_is_refused(self):
        self._contained_failure(attach=lambda handle: None)

    @unittest.skipUnless(os.name == 'nt', 'Job Objects are a Windows mechanism')
    def test_failed_resume_after_containment_ends_the_suspended_ai_before_it_runs(self):
        self._contained_failure(resume_process=lambda handle: False)

    @unittest.skipUnless(os.name == 'nt', 'Job Objects are a Windows mechanism')
    def test_a_descendant_started_as_the_very_first_action_is_gone_after_the_step(self):
        self.scenario('spawn-first')
        job_id = self.post_job()
        job = self.wait(job_id, 'done')
        self.assertEqual(job['error'], None)
        grandchildren = [t['grandchild'] for t in self.traces() if 'grandchild' in t]
        self.assertEqual((len(grandchildren), len([t for t in self.traces() if t.get('event') == 'start'])), (2, 2))   # write and review
        self.assert_gone(grandchildren)

    @unittest.skipUnless(os.name == 'nt', 'Job Objects are a Windows mechanism')
    def test_a_run_that_ends_normally_leaves_no_descendant(self):
        self.scenario('orphan')
        job_id = self.post_job()
        job = self.wait(job_id, 'done')
        self.assertEqual(job['error'], None)
        grandchildren = [t['grandchild'] for t in self.traces() if 'grandchild' in t]
        self.assertEqual(len(grandchildren), 2)                                              # one per run (write, review)
        self.assert_gone(grandchildren)

    def test_unsafe_run_output_is_never_read_or_logged(self):
        real, reads = cradle.read_json, []

        def traced(path):
            reads.append(Path(path).name)
            return real(path)
        for name in ('unsafe-tools', 'unsafe-key', 'no-init'):
            with self.subTest(scenario=name):
                reads.clear()
                self.scenario(name)
                with patch.object(cradle, 'read_json', traced):
                    job_id = self.post_job()
                    job = self.wait(job_id, 'failed')
                self.assertEqual(job['error']['code'], 'unsafe_runner')
                self.assertEqual([r for r in reads if r in {'receipt.json', 'answer.json', 'review.json', 'questions.json'}], [])
                line = self.log(job_id)[0]
                self.assertEqual((line['agent_receipt'], line['note'], line['error_code']), (None, None, 'unsafe_runner'))
                self.assertTrue((self.folder(job_id) / 'runs' / '01-write' / 'receipt.json').is_file())   # the AI did write it; the server just never opened it

    def _review_replaced(self, bad_reads, malformed):
        real, seen = cradle.read_json, []

        def replaced(path):
            value = real(path)
            if Path(path).name == 'review.json' and len(seen) < bad_reads and isinstance(value, dict):
                seen.append(1)
                return {**value, 'items': malformed}
            return value
        return patch.object(cradle, 'read_json', replaced)

    def test_malformed_review_items_are_rerun_once_then_review_invalid(self):
        for malformed in (1, 'text', {'id': 'I1', 'verdict': 'accept'}, [1], [{'id': 'I1', 'verdict': 'accept'}, 'x']):
            with self.subTest(items=str(malformed)):
                before = len(self.traces())
                with self._review_replaced(2, malformed):
                    job = self.wait(self.post_job(question=f'覆核壞格式 {malformed!s}'), 'failed')
                self.assertEqual((job['error']['code'], job['error']['message'], job['error']['retryable']),
                                 ('review_invalid', '覆核結果格式不正確，未能完成覆核。', True))
                self.assertEqual([t['step'] for t in self.traces()][before:], ['write', 'review', 'review'])
        with self._review_replaced(1, 1):                                                  # a single bad review is simply asked again
            done = self.wait(self.post_job(question='只壞一次'), 'done')
        self.assertEqual((done['round'], len(self.records())), (1, 1))
        self.assertEqual([t['step'] for t in self.traces()][-3:], ['write', 'review', 'review'])

    def test_malformed_ai_files_never_crash_the_checks(self):
        packet = {'session_id': 'ASK-x', 'evidence': [{'id': 'L1', 'text': '原文'}, {'id': 'L2', 'text': '另一份'}]}
        angles = [a for a, _ in cradle.WAYS['think']]
        base = {'session_id': 'ASK-x', 'surprising': 1, 'questions': [{'angle': a, 'question': f'問{n}', 'why': '因', 'notes': ['L1']} for n, a in enumerate(angles)]}
        odd = [None, 1, 2.5, True, 'x', [], {}, [1], [[1]], [{}], {'a': 1}]
        for key in ('angle', 'question', 'why', 'notes'):
            for value in odd:
                broken = json.loads(json.dumps(base))
                broken['questions'][1][key] = value
                self.assertIsInstance(cradle.check_questions(packet, 'think', broken), list, (key, value))
        for value in odd:
            for broken in ({**base, 'questions': value}, {**base, 'surprising': value}, {**base, 'session_id': value}, value):
                self.assertIsInstance(cradle.check_questions(packet, 'think', broken), list)
            self.assertIsInstance(cradle.mechanical(packet, {'session_id': 'ASK-x', 'analysis': value, 'tension_responses': value})[0], list)
        self.scenario('questions-bad')                                                       # and the whole pipeline ends in the plain-language failure
        failed = self.wait(self.post_job('questions', question='', way='think'), 'failed')
        self.assertEqual(failed['error']['code'], 'questions_invalid')

    def test_settings_gate_every_job(self):
        path = self.root / '.reader-runtime' / 'cradle' / 'settings.json'
        with patch.object(self.server.cradle, '_ensure_worker'):
            job_id = self.post_job()
            self.server.cradle.jobs[job_id]['runner'] = 'retired'
        self.server.cradle._ensure_worker()
        job = self.wait(job_id, 'failed')
        self.assertEqual((job['error']['code'], job['error']['message'], job['error']['retryable']),
                         ('runner_not_ready', '還未設定可用的背景 AI，請到 AI 設置揀一個；也可改用「複製指令」。', False))
        self.assertEqual((job['runner'], self.traces(), job['error']['actions']), ('retired', [], ['copy']))
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job["job_id"]}/retry', b'')[0], 409)
        path.write_bytes(b'{ not json')
        job_id = self.post_job(question='設定壞了')
        job = self.wait(job_id, 'failed')
        self.assertEqual((job['error']['code'], job['error']['message'], job['error']['retryable']),
                         ('settings_invalid', '搖籃設定檔格式不正確，沒有開始孕育。', True))
        self.assertEqual(path.read_bytes(), b'{ not json')                                   # never overwritten
        for broken in ({'timeouts': {'write': 0, 'other': 5}}, {'timeouts': {'write': '9', 'other': 5}}, {'max_review_rounds': 0},
                       {'max_mechanical_fixes': -1}, {'claude_command': []}, {'claude_command': [1]}, {'schema': 'x'}, {'runner': 5}):
            self.settings(**broken)
            with self.subTest(broken=broken):
                with self.assertRaises(cradle.JobFailure) as caught:
                    self.server.cradle._settings()
                self.assertEqual(caught.exception.error['code'], 'settings_invalid')
        self.scenario('not-logged-in')
        self.settings()
        self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')
        self.assertEqual(self.wait(job_id, 'failed')['error']['code'], 'not_logged_in')     # the settings are fine now: it reached the runner

    def test_missing_answer_specification(self):
        broken = self.root / 'spec-doc.md'
        broken.write_text('# 沒有那兩節\n', encoding='utf-8')
        with patch.object(cradle, 'SPEC_DOC', broken):
            job = self.wait(self.post_job(), 'failed')
        self.assertEqual((job['error']['code'], job['error']['message'], job['error']['retryable']), ('spec_missing', '找不到答案規格，未能開始孕育。', True))
        self.assertEqual(self.traces(), [])

    def test_blocked_material_is_not_retryable(self):
        with patch.object(cradle.ask, 'prepare', return_value={'status': 'needs_review', 'deferred_required_paths': ['x']}):
            blocked = self.wait(self.post_job(question='放不下'), 'failed')
        self.assertEqual((blocked['error']['code'], blocked['error']['retryable'], blocked['error']['actions']), ('prepare_blocked', False, ['edit_question']))
        self.assertEqual(blocked['error']['message'], '這幾份筆記未能完整放入材料包，未能開始孕育。請減少星的數目或改揀其他星。')
        self.assertFalse((self.folder(blocked['job_id']) / 'packet.json').exists())
        with patch.object(cradle.ask, 'prepare', side_effect=ValueError('boom')):
            broken = self.wait(self.post_job(question='準備失敗'), 'failed')
        self.assertEqual((broken['error']['code'], broken['error']['retryable'], broken['error']['detail']), ('prepare_blocked', False, 'ValueError: boom'))
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{broken["job_id"]}/retry', b'')[0], 409)
        self.assertEqual(self.traces(), [])


class LifecycleTests(CradleCase):
    def test_handoff_stops_the_ai_and_a_new_server_finishes_the_job(self):
        self.scenario('hang')
        job_id = self.post_job()
        self.wait_trace(lambda t: 'grandchild' in t)
        status, answer = self.api('POST', '/api/handoff', {'generation': V7})
        self.assertEqual((status, answer['status']), (200, 'retiring'))
        self.assert_gone(self.hang_pids())
        saved = json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual((saved['status'], saved['next'], saved['interrupted'], saved['pid']), ('queued', 'write', True, None))
        status, refused = self.api('POST', '/api/cradle/jobs', {'kind': 'think', 'notes': self.notes, 'question': '更新中'})
        self.assertEqual((status, refused), (409, {'error': '星雲服務正在更新，請稍後再試。'}))
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 409)
        self.assertEqual(self.records(), [])
        first_server, thread, _ = self.servers.pop()
        first_server.shutdown()
        first_server.server_close()
        thread.join(30)
        self.scenario('success')
        second = self.start()
        done = self.wait(job_id, 'done', server=second)
        self.assertEqual((done['error'], len(self.records())), (None, 1))
        self.assertEqual(done['result']['saved'], True)

    def test_restart_with_a_running_job_resumes_it_and_ends_only_a_matching_stale_child(self):
        self.scenario('hang')
        job_id = self.post_job()
        self.wait_trace(lambda t: 'grandchild' in t)
        first_server, thread, _ = self.servers.pop()
        first_server.shutdown()
        first_server.server_close()                                                            # ends the AI and requeues the job
        thread.join(30)
        path = self.folder(job_id) / 'job.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(state['status'], 'queued')
        # simulate a crash: the file still says "running", with the pid of a leftover child that is the runner program ...
        stale = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        bystander = subprocess.Popen(['ping', '-n', '120', '127.0.0.1'], stdout=subprocess.DEVNULL) if os.name == 'nt' else subprocess.Popen(['sleep', '120'])
        self.cleanup_processes += [stale, bystander]
        state.update(status='running', pid=stale.pid, pid_created=cradle.process_created(stale.pid), step='write')
        path.write_text(json.dumps(state), encoding='utf-8')
        self.scenario('success')
        second = self.start()
        stale.wait(20)                                                                         # ... which the new server ended
        self.assertIsNotNone(stale.poll())
        done = self.wait(job_id, 'done', server=second)
        self.assertEqual((done['status'], len(self.records())), ('done', 1))
        # a recorded pid that now belongs to another program (same pid number, other creation time) is left alone
        second_job = self.post_job(question='第二條', server=second)
        self.wait(second_job, 'done', server=second)
        other = self.folder(second_job) / 'job.json'
        state = json.loads(other.read_text(encoding='utf-8'))
        state.update(status='running', pid=bystander.pid, pid_created=cradle.process_created(bystander.pid) + 1, ended_at=None, next='archive')
        self.assertIsNone(bystander.poll())
        thread = [s for s in self.servers if s[0] is second][0]
        second.shutdown(); second.server_close(); thread[1].join(30)
        self.servers.remove(thread)
        other.write_text(json.dumps(state), encoding='utf-8')
        third = self.start()
        self.assertEqual(self.wait(second_job, 'done', server=third)['status'], 'done')
        self.assertIsNone(bystander.poll())
        self.assertEqual(len(self.records()), 2)

    def test_a_stale_child_that_cannot_be_ended_is_never_run_alongside_a_new_run(self):
        self.scenario('not-logged-in')
        job_id = self.post_job()
        self.wait(job_id, 'failed')
        first_server, thread, _ = self.servers.pop()
        first_server.shutdown()
        first_server.server_close()
        thread.join(30)
        stale = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.cleanup_processes.append(stale)
        path = self.folder(job_id) / 'job.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state.update(status='running', pid=stale.pid, pid_created=cradle.process_created(stale.pid), step='write', next='write', error=None, ended_at=None)
        path.write_text(json.dumps(state), encoding='utf-8')
        self.scenario('success')
        asked = []

        def refuses(pid):                                                                     # taskkill answering "Access denied"
            asked.append(pid)
            return False
        expected = {'code': 'stale_process', 'message': '上次的背景 AI 仍未結束，未能安全接續。請稍後再試。', 'retryable': True, 'actions': ['retry']}
        with patch.object(cradle, 'end_tree_by_pid', refuses), patch.object(cradle, 'STALE_WAIT_SECONDS', 0.4, create=True):
            second = self.start()
            job = self.job(job_id, server=second)
            self.assertEqual(({k: job['error'][k] for k in expected}, job['status']), (expected, 'failed'))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['pid'], stale.pid)    # still recorded
            self.assertTrue(asked and set(asked) == {stale.pid})
            for _ in range(2):                                                                # a retry repeats the check and starts nothing
                self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)
                again = self.wait(job_id, 'failed', server=second)
                self.assertEqual(again['error']['code'], 'stale_process')
                self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['pid'], stale.pid)
            self.assertIsNone(stale.poll())
            self.assertEqual([t['step'] for t in self.traces()], ['write'])                  # only the very first (logged-out) run ever started
            self.assertEqual(self.records(), [])
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)   # now taskkill works: it ends the old child, then runs
        done = self.wait(job_id, 'done', server=second)
        stale.wait(20)
        self.assertEqual((done['error'], len(self.records()), json.loads(path.read_text(encoding='utf-8'))['pid']), (None, 1, None))

    def test_cancel_while_waiting_for_an_old_child_keeps_its_pid_until_it_is_gone(self):
        self.scenario('not-logged-in')
        job_id = self.post_job()
        self.wait(job_id, 'failed')
        first_server, thread, _ = self.servers.pop()
        first_server.shutdown()
        first_server.server_close()
        thread.join(30)
        stale = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.cleanup_processes.append(stale)
        path = self.folder(job_id) / 'job.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state.update(status='running', pid=stale.pid, pid_created=cradle.process_created(stale.pid), step='write', next='write', error=None, ended_at=None)
        path.write_text(json.dumps(state), encoding='utf-8')
        self.scenario('success')
        recorded = lambda: json.loads(path.read_text(encoding='utf-8'))['pid']
        with patch.object(cradle, 'end_tree_by_pid', return_value=False), patch.object(cradle, 'STALE_WAIT_SECONDS', 0.2):
            second = self.start()
            self.assertEqual(self.job(job_id, server=second)['error']['code'], 'stale_process')
        entered, release = threading.Event(), threading.Event()

        def refuses_slowly(pid):                                                            # the retry sits inside the old-child check
            entered.set()
            self.assertTrue(release.wait(10))
            return False
        with patch.object(cradle, 'end_tree_by_pid', refuses_slowly), patch.object(cradle, 'STALE_WAIT_SECONDS', 0.2):
            self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)
            self.assertTrue(entered.wait(10))
            status, cancelled = self.api('POST', f'/api/cradle/jobs/{job_id}/cancel', b'', server=second)
            self.assertEqual((status, cancelled['status']), (200, 'cancelled'))
            self.assertEqual(recorded(), stale.pid)                                        # the old AI is alive: its pid must stay recorded
            release.set()
            time.sleep(1.0)                                                                 # the cancelled check unwinds
            self.assertEqual((self.job(job_id, server=second)['status'], recorded()), ('cancelled', stale.pid))
            for _ in range(2):                                                              # every later retry repeats the check and starts nothing
                self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)
                again = self.wait(job_id, 'failed', server=second)
                self.assertEqual(again['error']['code'], 'stale_process')
                self.assertEqual(recorded(), stale.pid)
                self.assertIsNone(stale.poll())
            self.assertEqual(([t['step'] for t in self.traces()], self.records()), (['write'], []))
        stale.kill()
        stale.wait(20)
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)
        done = self.wait(job_id, 'done', server=second)
        self.assertEqual((done['error'], len(self.records()), recorded()), (None, 1, None))

    def test_a_job_keeps_the_pid_of_a_live_runner_when_it_ends(self):
        job_id = self.post_job()
        self.wait(job_id, 'done')
        live = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.cleanup_processes.append(live)
        engine = self.server.cradle
        job = engine.jobs[job_id]
        for status in ('cancelled', 'failed'):
            with engine.lock:
                job['pid'] = live.pid
                engine._end(job, status)
            self.assertEqual(json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))['pid'], live.pid)
        live.kill()
        live.wait(20)
        with engine.lock:
            engine._end(job, 'cancelled')
        self.assertIsNone(json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))['pid'])

    # ---- one identity rule for the recorded background AI: pid + creation time ("gone" / "same" / "unknown")
    def old_child_job(self, created='right'):
        """A job whose file says it is running a step with the pid of a live old child; the first server is stopped.

        ``created``: 'right' = the child's real creation time, 'wrong' = another creation time (the pid was reused), None = legacy file."""
        self.scenario('not-logged-in')
        job_id = self.post_job()
        self.wait(job_id, 'failed')
        first_server, thread, _ = self.servers.pop()
        first_server.shutdown()
        first_server.server_close()
        thread.join(30)
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.cleanup_processes.append(child)
        path = self.folder(job_id) / 'job.json'
        state = json.loads(path.read_text(encoding='utf-8'))
        state.update(status='running', pid=child.pid, step='write', next='write', error=None, ended_at=None)
        state.pop('pid_created', None)
        if created is not None:
            real = cradle.process_created(child.pid)
            state['pid_created'] = real if created == 'right' else real + 1
        path.write_text(json.dumps(state), encoding='utf-8')
        self.scenario('success')
        return job_id, path, child

    def assert_blocked_by(self, job_id, path, child, second):
        """The old AI lives and cannot be shown to be gone: pid kept, nothing started, every retry says stale_process."""
        recorded = lambda: json.loads(path.read_text(encoding='utf-8'))['pid']
        job = self.job(job_id, server=second)
        self.assertEqual((job['status'], job['error']['code'], recorded()), ('failed', 'stale_process', child.pid))
        for _ in range(2):
            self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)
            again = self.wait(job_id, 'failed', server=second)
            self.assertEqual((again['error']['code'], recorded()), ('stale_process', child.pid))
        self.assertIsNone(child.poll())
        self.assertEqual(([t['step'] for t in self.traces()], self.records()), (['write'], []))   # only the very first (logged-out) run ever started

    def assert_released(self, job_id, path, child, second):
        child.kill()
        child.wait(20)
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'', server=second)[0], 200)
        done = self.wait(job_id, 'done', server=second)
        self.assertEqual((done['error'], len(self.records()), json.loads(path.read_text(encoding='utf-8'))['pid']), (None, 1, None))

    def test_unreadable_settings_never_make_a_live_old_ai_forgotten(self):
        job_id, path, child = self.old_child_job('right')
        settings = self.root / '.reader-runtime' / 'cradle' / 'settings.json'
        good = settings.read_text(encoding='utf-8')
        settings.write_text('{not json', encoding='utf-8')
        with patch.object(cradle, 'end_tree_by_pid', return_value=False), patch.object(cradle, 'STALE_WAIT_SECONDS', 0.3):
            second = self.start()
            self.assert_blocked_by(job_id, path, child, second)
        settings.write_text(good, encoding='utf-8')
        self.assert_released(job_id, path, child, second)

    def test_unreadable_process_identity_never_makes_a_live_old_ai_forgotten(self):
        job_id, path, child = self.old_child_job('right')
        asked = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(cradle, 'end_tree_by_pid', lambda pid: asked.append(pid) or False))
            stack.enter_context(patch.object(cradle, 'process_created', side_effect=OSError('unreadable')))
            stack.enter_context(patch.object(cradle, 'creation_time_of_handle', side_effect=OSError('unreadable')))
            stack.enter_context(patch.object(cradle, 'process_image_name', return_value=None))
            second = self.start()
            self.assert_blocked_by(job_id, path, child, second)
        self.assertEqual(asked, [])                                                          # "unknown" is never ended
        self.assert_released(job_id, path, child, second)

    def test_a_legacy_job_file_without_a_creation_time_never_ends_a_live_process(self):
        job_id, path, child = self.old_child_job(None)
        asked = []
        with patch.object(cradle, 'end_tree_by_pid', lambda pid: asked.append(pid) or True):
            second = self.start()
            self.assert_blocked_by(job_id, path, child, second)
        self.assertEqual(asked, [])
        self.assert_released(job_id, path, child, second)

    def handle_world(self, events, created=101, exited=False, waits=True, read=None, opens=77):
        """The Windows handle calls made visible: every call is logged, and ``end`` says whether the verified handle was held at that moment."""
        held = []

        def opened(pid):
            events.append('open')
            if opens:
                held.append(1)
            return opens

        def alive(handle, seconds):
            events.append('wait' if seconds else 'alive?')
            return exited if not seconds else waits

        def reads(handle):
            events.append('read')
            if read:
                raise read
            return created

        def end(pid):
            events.append('end-held' if held else 'end-NOT-held')
            return True

        def closed(handle):
            events.append('close')
            held.clear()
        stack = ExitStack()
        for name, fake in (('open_process_for_end', opened), ('wait_process_exit', alive), ('creation_time_of_handle', reads),
                           ('end_tree_by_pid', end), ('close_process_handle', closed)):
            stack.enter_context(patch.object(cradle, name, fake))
        return stack

    @unittest.skipUnless(os.name == 'nt', 'the handle check is a Windows mechanism')
    def test_the_recorded_process_is_ended_only_while_the_handle_that_verified_it_is_held(self):
        pid, recorded = 123456, 101
        no_pid_lookup = patch.object(cradle, 'process_identity', side_effect=AssertionError('the pid must not be looked at again by number'))
        events = []
        with self.handle_world(events), no_pid_lookup:                                      # the recorded process: ended through the held handle
            self.assertTrue(cradle.end_recorded_tree(pid, recorded, 1))
        self.assertEqual(events, ['open', 'alive?', 'read', 'end-held', 'wait', 'close'])
        events = []
        with self.handle_world(events, created=202), no_pid_lookup:                         # 202: the pid now belongs to another program
            self.assertTrue(cradle.end_recorded_tree(pid, recorded, 1))
        self.assertEqual(events, ['open', 'alive?', 'read', 'close'])
        events = []
        with self.handle_world(events, exited=True), no_pid_lookup:                         # already exited (a handle elsewhere keeps the pid openable)
            self.assertTrue(cradle.end_recorded_tree(pid, None, 1))
        self.assertEqual(events, ['open', 'alive?', 'close'])
        for label, kwargs, expected in (('unreadable', {'read': OSError('unreadable')}, ['open', 'alive?', 'read', 'close']),
                                        ('not exited in time', {'waits': False}, ['open', 'alive?', 'read', 'end-held', 'wait', 'close'])):
            events = []
            with self.handle_world(events, **kwargs):                                       # never an end command without a verified identity; no exit, no "gone"
                self.assertFalse(cradle.end_recorded_tree(pid, recorded, 0.1), label)
            self.assertEqual(events, expected, label)
        events = []
        with self.handle_world(events):                                                     # a legacy job file has no creation time: unknown, never ended
            self.assertFalse(cradle.end_recorded_tree(pid, None, 0.1))
        self.assertEqual(events, ['open', 'alive?', 'close'])
        for answer, expect in (('unknown', False), ('gone', True), ('same', False)):        # the handle cannot be opened: no end command, whatever the identity says
            events = []
            with self.handle_world(events, opens=None), patch.object(cradle, 'process_identity', return_value=answer):
                self.assertEqual(cradle.end_recorded_tree(pid, recorded, 0.1), expect)
            self.assertEqual(events, ['open'])
        events = []
        with self.handle_world(events):                                                     # the restart / retry path uses the same check
            self.assertTrue(object.__new__(cradle.Cradle)._reap_stale({'pid': pid, 'pid_created': recorded}))
        self.assertEqual(events, ['open', 'alive?', 'read', 'end-held', 'wait', 'close'])

    @unittest.skipUnless(os.name == 'nt', 'the handle check is a Windows mechanism')
    def test_a_real_process_is_ended_through_the_held_handle_and_a_reused_pid_is_not(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
        self.cleanup_processes.append(child)
        created = cradle.process_created(child.pid)
        asked = []
        with patch.object(cradle, 'end_tree_by_pid', lambda p: asked.append(p) or True):
            self.assertTrue(cradle.end_recorded_tree(child.pid, created + 1, 1))             # the same pid but another creation time
            self.assertEqual((asked, child.poll()), ([], None))
        self.assertTrue(cradle.end_recorded_tree(child.pid, created, 20))                    # the real taskkill, then the exit is awaited on the handle
        self.assertIsNotNone(child.poll())
        self.assertTrue(cradle.end_recorded_tree(child.pid, None, 1))                        # exited, though this test still holds a handle: gone, nothing asked again

    def test_a_reused_pid_is_forgotten_and_the_innocent_process_is_never_ended(self):
        asked = []
        job_id, path, innocent = self.old_child_job('wrong')                                 # restart path (_scan)
        with patch.object(cradle, 'end_tree_by_pid', lambda pid: asked.append(pid) or True):
            second = self.start()
            done = self.wait(job_id, 'done', server=second)
            self.assertEqual((done['error'], len(self.records()), json.loads(path.read_text(encoding='utf-8'))['pid']), (None, 1, None))
            self.assertIsNone(innocent.poll())
            # retry path (_process): the pid of a failed, retryable job is now somebody else's program
            third_job = self.post_job(question='第二條', server=second)
            self.wait(third_job, 'done', server=second)
            engine, other = second.cradle, self.folder(third_job) / 'job.json'
            with engine.lock:
                job = engine.jobs[third_job]
                job.update(status='failed', error=cradle.stale_failure().error, pid=innocent.pid, pid_created=cradle.process_created(innocent.pid) + 1)
                engine._save_locked(job)
            self.assertEqual(self.api('POST', f'/api/cradle/jobs/{third_job}/retry', b'', server=second)[0], 200)
            self.assertEqual(self.wait(third_job, 'done', server=second)['error'], None)
            self.assertEqual(json.loads(other.read_text(encoding='utf-8'))['pid'], None)
        self.assertEqual((asked, innocent.poll()), ([], None))                              # not asked to end it; still running
        innocent.kill()
        innocent.wait(20)

    @unittest.skipUnless(os.name == 'nt', 'Job Objects are a Windows mechanism')
    def test_a_suspended_ai_whose_exit_is_not_confirmed_stays_tracked(self):
        self.scenario('spawn-first')
        started, original = [], subprocess.Popen

        class Unkillable(original):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                started.append(self)

            allow = False

            def kill(self):                                                                  # termination "fails" until the test allows it
                if self.allow:
                    super().kill()
        asked = []
        refuse = lambda pid: asked.append(pid) or False
        try:
            self._suspended_ai_stays_tracked(started, asked, refuse, Unkillable)
        finally:                                                                             # the suspended processes are ours: end them whatever happened
            for process in started:
                process.allow = True
                if process.poll() is None:
                    process.kill()
                process.wait(20)

    def _suspended_ai_stays_tracked(self, started, asked, refuse, Unkillable):
        with ExitStack() as stack:
            stack.enter_context(patch.object(cradle, 'end_tree_by_pid', refuse))
            stack.enter_context(patch.object(cradle.subprocess, 'Popen', Unkillable))
            stack.enter_context(patch.object(cradle.JobObject, 'attach', lambda handle: None))
            stack.enter_context(patch.object(cradle, 'SUSPENDED_WAITS', (0.3, 0.3), create=True))
            stack.enter_context(patch.object(cradle, 'STALE_WAIT_SECONDS', 0.3))
            job_id = self.post_job()
            job = self.wait(job_id, 'failed', 'done', timeout=120)
            ai = [p for p in started if str(FAKE) in map(str, p.args)]
            self.assertEqual(len(ai), 1)
            self.assertEqual((job['error']['code'], job['error']['retryable']), ('unsafe_runner', False))
            self.assertIsNone(ai[0].poll())                                                  # suspended, never ended
            path = self.folder(job_id) / 'job.json'
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual((saved['pid'], saved['pid_created']), (ai[0].pid, cradle.process_created(ai[0].pid)))
            self.assertIsInstance(saved['pid_created'], int)
            self.assertEqual(self.traces(), [])                                              # it never ran a line
            for _ in range(2):                                                               # every restart checks it again: alive, so kept
                first_server, thread, _ = self.servers.pop()
                first_server.shutdown()
                first_server.server_close()
                thread.join(30)
                second = self.start()
                self.assertEqual(self.job(job_id, server=second)['status'], 'failed')
                self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['pid'], ai[0].pid)
            self.assertTrue(asked and set(asked) == {ai[0].pid})                             # "same": it was asked to end, and refused
            self.assertIsNone(ai[0].poll())
            ai[0].allow = True
            ai[0].kill()                                                                     # the test ends the process it started
            ai[0].wait(20)
            first_server, thread, _ = self.servers.pop()
            first_server.shutdown()
            first_server.server_close()
            thread.join(30)
            self.start()
        self.assertIsNone(json.loads(path.read_text(encoding='utf-8'))['pid'])              # gone: forgotten
        self.assertEqual(self.hang_pids(), [])

    def test_idle_server_waits_for_a_job_then_closes(self):
        self.scenario('slow', CRADLE_FAKE_DELAY=2)
        server = self.start(idle=1)
        job_id = self.post_job(server=server)
        thread = self.servers[-1][1]
        time.sleep(3.5)                                                                       # longer than the idle limit, job still working
        self.assertTrue(thread.is_alive())
        self.assertIn(self.job(job_id, server=server)['status'], {'queued', 'running'})
        deadline = time.time() + 60
        while thread.is_alive() and time.time() < deadline:
            time.sleep(0.2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.records()), 1)
        saved = json.loads((self.folder(job_id) / 'job.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['status'], 'done')


class QuestionTests(CradleCase):
    def test_questions_then_pick_then_job(self):
        made = self.post_job('questions', question='', way='think')
        job = self.wait(made, 'done')
        self.assertEqual((job['way'], job['surprising'], job['picked'], job['dismissed'], job['step'], job['error']), ('think', 2, None, False, None, None))
        self.assertEqual([q['angle'] for q in job['questions']], [a for a, _ in cradle.WAYS['think']])
        self.assertEqual(self.api('GET', f'/api/cradle/jobs/{made}/draft')[0], 404)
        run = self.folder(made) / 'runs' / '01-questions'
        self.assertEqual(sorted(p.name for p in run.iterdir()), ['packet.json', 'questions.json', 'receipt.json', 'task.md', 'ways.json'])
        ways = json.loads((run / 'ways.json').read_text(encoding='utf-8'))
        self.assertEqual(ways['angles'][0], {'angle': '新應用', 'prompt': '這些筆記的做法，用在哪一個場景會長出甚麼新做法？'})
        self.assertEqual(json.loads((self.folder(made) / 'packet.json').read_text(encoding='utf-8'))['question'], '為以上筆記擬題')
        self.assertIn('每條問題可以針對其中一份或幾份筆記。', (run / 'task.md').read_text(encoding='utf-8'))
        self.assertEqual(self.api('GET', '/api/cradle/jobs')[1]['awaiting_pick'], 1)
        self.assertEqual(self.records(), [])                                                   # a questions job saves nothing
        chosen = job['questions'][1]['question']
        # every wrong way to pick is refused and starts nothing
        before = len(self.api('GET', '/api/cradle/jobs')[1]['jobs'])
        wrong = [dict(kind='think', question=chosen, from_questions='CJ-20200101-0001'),
                 dict(kind='think', question='不在五條之內', from_questions=made),
                 dict(kind='gap', question=chosen, from_questions=made),
                 dict(kind='think', question=chosen, from_questions=made, notes=['sources/SRC-1.md']),
                 dict(kind='think', question=chosen, from_questions=5)]
        for body in wrong:
            with self.subTest(body=str(body)[:60]):
                body.setdefault('notes', self.notes)
                self.assertEqual(self.api('POST', '/api/cradle/jobs', body)[0], 400)
        self.assertEqual(len(self.api('GET', '/api/cradle/jobs')[1]['jobs']), before)
        # a questions job that is not finished cannot be picked from
        self.scenario('slow', CRADLE_FAKE_DELAY=1)
        unfinished = self.post_job('questions', question='方向', way='think')
        self.assertEqual(self.api('POST', '/api/cradle/jobs', dict(kind='think', notes=self.notes, question=chosen, from_questions=unfinished))[0], 400)
        direction = self.wait(unfinished, 'done')
        self.assertIn('direction.txt', {p.name for p in (self.folder(unfinished) / 'runs' / '01-questions').iterdir()})
        self.assertIn('另有 direction.txt', (self.folder(unfinished) / 'runs' / '01-questions' / 'task.md').read_text(encoding='utf-8'))
        # dismissed ones cannot be picked
        status, dismissed = self.api('POST', f'/api/cradle/jobs/{unfinished}/cancel', b'')
        self.assertEqual((status, dismissed['dismissed'], dismissed['status']), (200, True, 'done'))
        self.assertEqual(self.api('POST', '/api/cradle/jobs', dict(kind='think', notes=self.notes, question=direction['questions'][0]['question'], from_questions=unfinished))[0], 400)
        self.assertEqual(self.api('GET', '/api/cradle/jobs')[1]['awaiting_pick'], 1)
        # the real pick
        self.scenario('success')
        picked = self.post_job('think', question=chosen, from_questions=made)
        self.assertEqual(self.job(made)['picked'], picked)
        self.assertEqual(self.api('GET', '/api/cradle/jobs')[1]['awaiting_pick'], 0)
        self.assertEqual(self.api('POST', '/api/cradle/jobs', dict(kind='think', notes=self.notes, question=chosen, from_questions=made))[0], 400)   # already picked
        self.assertEqual(self.wait(picked, 'done')['question'], chosen)
        self.assertEqual(len(self.records()), 1)

    def test_malformed_questions_are_fixed_once_or_the_job_fails(self):
        self.scenario('questions-bad-then-fixed')
        good = self.post_job('questions', question='', way='gap')
        job = self.wait(good, 'done')
        self.assertEqual([c['step'] for c in self.traces()], ['questions', 'qfix'])
        self.assertEqual([q['angle'] for q in job['questions']], [a for a, _ in cradle.WAYS['gap']])
        self.assertIn('format_errors.txt', {p.name for p in (self.folder(good) / 'runs' / '02-qfix').iterdir()})
        self.assertIn('questions 要正好有 5 項', (self.folder(good) / 'format_errors.txt').read_text(encoding='utf-8'))
        self.assertIn('五條問題都只針對筆記之間的「關係」', (self.folder(good) / 'runs' / '01-questions' / 'task.md').read_text(encoding='utf-8'))
        self.scenario('questions-bad')
        bad = self.post_job('questions', question='', way='think')
        failed = self.wait(bad, 'failed')
        self.assertEqual((failed['error']['code'], failed['error']['message'], failed['error']['retryable']),
                         ('questions_invalid', 'AI 擬的問題格式不正確，未能列出問題。', True))
        self.assertEqual([c['step'] for c in self.traces()][2:], ['questions', 'qfix', 'qfix'])
        self.scenario('success')
        self.api('POST', f'/api/cradle/jobs/{bad}/retry', b'')
        again = self.wait(bad, 'done')
        self.assertEqual((again['attempt'], len(again['questions'])), (2, 5))

    def test_question_format_rules(self):
        packet = {'session_id': 'ASK-x', 'evidence': [{'id': 'L1'}, {'id': 'L2'}]}
        angles = [a for a, _ in cradle.WAYS['think']]
        good = {'session_id': 'ASK-x', 'surprising': 4, 'questions': [
            {'angle': a, 'question': f'問題{n}', 'why': '原因', 'notes': ['L1', 'L2']} for n, a in enumerate(angles)]}
        self.assertEqual(cradle.check_questions(packet, 'think', good), [])
        def changed(**fields):
            value = json.loads(json.dumps(good))
            for path, replacement in fields.items():
                target = value
                *head, last = path.split('.')
                for part in head:
                    target = target[int(part)] if part.isdigit() else target[part]
                target[int(last) if last.isdigit() else last] = replacement
            return value
        for broken in (changed(session_id='other'), changed(surprising=5), changed(surprising=True), changed(**{'questions.0.angle': '別的'}),
                       changed(**{'questions.1.question': 'a\nb'}), changed(**{'questions.1.question': '長' * 121}), changed(**{'questions.1.question': ''}),
                       changed(**{'questions.1.question': '問題0'}), changed(**{'questions.2.why': ''}), changed(**{'questions.2.why': '長' * 151}),
                       changed(**{'questions.3.notes': []}), changed(**{'questions.3.notes': ['L3']}), changed(**{'questions.3.notes': ['L1', 'L1']}),
                       changed(questions=good['questions'][:4]), [], None):
            self.assertTrue(cradle.check_questions(packet, 'think', broken), broken)


class RunnerSetupTests(CradleCase):
    def runners(self, refresh=True):
        status, body = self.api('GET', '/api/cradle/runners' + ('?refresh=1' if refresh else ''))
        self.assertEqual(status, 200, body)
        self.assertEqual(set(body), {'runner', 'checked_at', 'runners'})
        self.assertIsInstance(body['checked_at'], str)
        for row in body['runners']:
            self.assertEqual(set(row), {'name', 'label', 'installed', 'version', 'login', 'locked', 'ready', 'reason'})
        return {row['name']: row for row in body['runners']}

    def test_fresh_settings_approval_returns_every_registry_runner(self):
        self.settings(runner=None)
        self.assertEqual(self.server.cradle._runner_snapshot()['runners'], [])
        status, body = self.api('POST', '/api/cradle/settings', {'runner': 'codex'})
        self.assertEqual((status, body['runner']), (200, 'codex'), body)
        self.assertEqual([row['name'] for row in body['runners']], list(cradle.RUNNERS))
        self.assertEqual(self.api('GET', '/api/cradle/runners'), (200, body))

    def test_close_ends_detection_version_tree_and_starts_no_more_calls(self):
        store = self.server.cradle
        previous = store._runner_snapshot()
        with patch.dict(os.environ, {'CRADLE_FAKE_CODEX': 'version-hang'}):
            self.assertEqual(self.api('GET', '/api/cradle/jobs')[0], 200)
            deadline = time.monotonic() + 15
            while not any('grandchild' in row for row in self.codex_traces()) and time.monotonic() < deadline:
                time.sleep(.1)
            traces = self.codex_traces()
            hung = next((row for row in traces if 'grandchild' in row), None)
            self.assertIsNotNone(hung, traces)
            self.assertIsNotNone(store.probe_proc)
            before = (self.traces(), traces)
            started = time.monotonic()
            store.close()
            self.assertLess(time.monotonic() - started, 30)
        self.assertFalse(store.detection_thread.is_alive())
        self.assert_gone([hung['pid'], hung['grandchild']])
        self.assertEqual(store._runner_snapshot(), previous)
        self.assertEqual(self.api('GET', '/api/cradle/runners?refresh=1')[1], {'runner': 'claude', **previous})
        self.assertEqual((self.traces(), self.codex_traces()), before)

    def test_detection_after_close_preserves_cached_result(self):
        self.runners()
        previous = self.server.cradle._runner_snapshot()
        before = (self.traces(), self.codex_traces())
        self.server.cradle.close()
        self.assertEqual(self.api('GET', '/api/cradle/runners?refresh=1')[1], {'runner': 'claude', **previous})
        self.assertEqual((self.traces(), self.codex_traces()), before)

    def test_runner_detection_login_lock_and_missing_shapes(self):
        cases = [('claude.ai', 'account'), ('api_key', 'api_key'), ('api_key_helper', 'api_key'),
                 ('oauth_token', 'other'), ('third_party', 'other'), ('none', 'none')]
        for method, login in cases:
            with self.subTest(claude=method), patch.dict(os.environ, {'CRADLE_FAKE_AUTH': method}):
                row = self.runners()['claude']
                self.assertEqual((row['installed'], row['version'], row['login'], row['locked'], row['ready']),
                                 (True, 'Claude Code 2.0-fake', login, True, login == 'account'))
                expected = (None if login == 'account' else 'Claude Code 未登入，請先打開 Claude Code 登入一次。' if login == 'none'
                            else 'Claude Code 只用 API key 登入，背景孕育只接受帳戶登入。' if login == 'api_key'
                            else 'Claude Code 的登入方式不是個人帳戶，背景孕育暫不接受。')
                self.assertEqual(row['reason'], expected)
        for scenario, login, locked in [('success', 'account', True), ('login-none', 'none', True),
                                        ('login-apikey', 'api_key', True), ('leaky', 'account', False), ('no-backend', 'account', False)]:
            with self.subTest(codex=scenario), patch.dict(os.environ, {'CRADLE_FAKE_CODEX': scenario}):
                row = self.runners()['codex']
                self.assertEqual((row['version'], row['login'], row['locked'], row['ready']),
                                 ('codex-cli 0.163.0-fake', login, locked, locked and login == 'account'))
                self.assertEqual(row['reason'], None if scenario == 'success' else
                                 'Codex 未登入，請先打開 Codex 登入一次。' if login == 'none' else
                                 'Codex 只用 API key 登入，背景孕育只接受帳戶登入。' if login == 'api_key' else
                                 'Codex 的鎖緊測試未通過（讀得到資料夾外的檔案或連得上網絡），暫不可用。')
        self.settings(claude_command=[str(self.root / 'missing-claude.exe')], codex_command=[str(self.root / 'missing-codex.exe')])
        missing = self.runners()
        for name, label in [('claude', 'Claude Code'), ('codex', 'Codex')]:
            self.assertEqual(missing[name], {'name': name, 'label': label, 'installed': False, 'version': None,
                                            'login': 'none', 'locked': False, 'ready': False, 'reason': f'這部電腦找不到 {label}。'})

    def test_settings_approve_refuse_disable_and_preserve_invalid_file(self):
        path = self.root / '.reader-runtime/cradle/settings.json'
        self.settings(runner=None, extra_setting={'preserve': True})
        original = json.loads(path.read_text(encoding='utf-8'))
        for name in ('claude', 'codex'):
            status, body = self.api('POST', '/api/cradle/settings', {'runner': name})
            self.assertEqual((status, body['runner']), (200, name), body)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {**original, 'runner': name})
        before = path.read_bytes()
        with patch.dict(os.environ, {'CRADLE_FAKE_CODEX': 'login-apikey'}):
            status, body = self.api('POST', '/api/cradle/settings', {'runner': 'codex'})
        self.assertEqual((status, body), (409, {'error': 'Codex 只用 API key 登入，背景孕育只接受帳戶登入。'}))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.api('POST', '/api/cradle/settings', {'runner': None})[0], 200)
        self.assertEqual(json.loads(path.read_text(encoding='utf-8')), original)
        self.assertEqual(self.api('GET', '/api/cradle/jobs')[1]['runner'], {'name': None, 'label': None, 'ready': False})
        for value in ({}, {'runner': 'bad'}, {'runner': []}, {'runner': True}, {'runner': 'claude', 'extra': 1}, [], None):
            self.assertEqual(self.api('POST', '/api/cradle/settings', value), (400, {'error': 'AI 設置的要求格式不正確。'}))
        self.assertEqual(self.api('POST', '/api/cradle/settings', raw=b'{bad', headers={**self.auth(), 'Content-Type': 'application/json'}),
                         (400, {'error': 'AI 設置的要求格式不正確。'}))
        for raw in (b'{bad', b'[]', json.dumps({**original, 'runner': 'retired'}).encode()):
            path.write_bytes(raw)
            for name in ('claude', None):
                self.assertEqual(self.api('POST', '/api/cradle/settings', {'runner': name}),
                                 (409, {'error': '搖籃設定檔格式不正確，沒有更改。'}))
                self.assertEqual(path.read_bytes(), raw)

    def test_unapproved_jobs_refused_legacy_settings_and_codex_command_validation(self):
        self.settings(runner=None)
        body = {'kind': 'think', 'notes': self.notes, 'question': '問題'}
        self.assertEqual(self.api('POST', '/api/cradle/jobs', body),
                         (409, {'error': '還未設定背景 AI，請先在 AI 設置揀一個；也可以改用「複製指令」。'}))
        self.assertFalse((self.root / '.reader-runtime/cradle/jobs').exists())
        path = self.root / '.reader-runtime/cradle/settings.json'
        path.unlink()  # a fresh install also starts unapproved
        self.assertEqual(self.api('POST', '/api/cradle/jobs', body)[0], 409)
        self.assertIsNone(json.loads(path.read_text(encoding='utf-8'))['runner'])
        self.settings()
        value = json.loads(path.read_text(encoding='utf-8'))
        value.pop('codex_command')
        path.write_text(json.dumps(value), encoding='utf-8')
        self.assertEqual(self.wait(self.post_job(), 'done')['runner'], 'claude')
        for command in ([], [''], [1], 'codex', False):
            self.settings(codex_command=command)
            job = self.wait(self.post_job(), 'failed')
            self.assertEqual(job['error']['code'], 'settings_invalid')
        self.settings(codex_command=None)
        self.assertIsNone(self.server.cradle._settings()['codex_command'])

    def test_setup_routes_authentication_module_absence_and_email_never_leaks(self):
        for method, path, body in [('GET', '/api/cradle/runners', None), ('POST', '/api/cradle/settings', {'runner': None})]:
            for headers in ({}, {**self.auth(), 'Origin': 'http://evil.example'}, {**self.auth(), 'X-Nebula-Token': 'wrong'}):
                self.assertEqual(self.api(method, path, body, headers=headers)[0], 403)
            with patch.object(server_module, 'cradle', None):
                self.assertEqual(self.api(method, path, body)[0], 404)
        self.assertEqual(self.api('POST', '/api/cradle/settings', {'runner': None}, headers={'X-Nebula-Token': self.server.token})[0], 403)
        self.runners()
        self.assertEqual(self.api('POST', '/api/cradle/settings', {'runner': 'claude'})[0], 200)
        job = self.wait(self.post_job(), 'done')
        responses = [self.api('GET', path)[1] for path in ('/api/cradle/runners', '/api/cradle/jobs', '/api/cradle/jobs/' + job['job_id'])]
        self.assertNotIn('cradle-dummy@example.invalid', json.dumps(responses))
        for path in (self.root / '.reader-runtime/cradle').rglob('*'):
            if path.is_file():
                self.assertNotIn(b'cradle-dummy@example.invalid', path.read_bytes(), path.name)

    def test_jobs_list_detects_once_in_background_and_polls_use_cache(self):
        with patch.dict(os.environ, {'CRADLE_FAKE_CODEX': 'version-slow', 'CRADLE_FAKE_CODEX_DELAY': '1'}):
            started = time.monotonic()
            status, body = self.api('GET', '/api/cradle/jobs')
            self.assertEqual((status, body['runner']), (200, {'name': 'claude', 'label': 'Claude Code', 'ready': None}))
            self.assertLess(time.monotonic() - started, 1)
            for _ in range(5):
                self.assertEqual(self.api('GET', '/api/cradle/jobs')[0], 200)
            self.server.cradle.detection_thread.join(15)
        before = self.codex_traces()
        self.assertEqual([t['step'] for t in before], ['--version', 'login', 'sandbox'])
        for _ in range(5):
            self.assertEqual(self.api('GET', '/api/cradle/jobs')[1]['runner'], {'name': 'claude', 'label': 'Claude Code', 'ready': True})
        self.runners(refresh=False)
        self.assertEqual(self.codex_traces(), before)
        self.runners()
        self.assertEqual(len(self.codex_traces()), len(before) + 3)

    def test_concurrent_detection_is_single_flight_and_timeout_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        real_call = self.server.cradle._call
        def slow(prefix, args, deadline):
            if args == ['--version'] and str(FAKE) in prefix:
                entered.set()
                release.wait(5)
            return real_call(prefix, args, deadline)
        results = []
        with patch.object(self.server.cradle, '_call', slow):
            first = threading.Thread(target=lambda: results.append(self.api('GET', '/api/cradle/runners?refresh=1')))
            first.start()
            self.assertTrue(entered.wait(5))
            second = threading.Thread(target=lambda: results.append(self.api('GET', '/api/cradle/runners?refresh=1')))
            second.start()
            time.sleep(.2)
            release.set()
            first.join(15)
            second.join(15)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual([t['step'] for t in self.codex_traces()], ['--version', 'login', 'sandbox'])
        before = len(self.codex_traces())
        with patch.dict(os.environ, {'CRADLE_FAKE_CODEX': 'version-hang'}):
            start = time.monotonic()
            row = self.runners()['codex']
        self.assertFalse(row['ready'])
        self.assertEqual(row['reason'], 'Codex 沒有及時回應，請稍後再按「偵測」。')
        self.assertLess(time.monotonic() - start, 12)
        self.assert_gone([row[key] for row in self.codex_traces()[before:] for key in ('pid', 'grandchild') if key in row])

    def test_codex_program_resolution_uses_native_binary_and_non_windows_is_unlocked(self):
        prefix = self.root / 'npm'
        native = prefix / 'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        native.parent.mkdir(parents=True)
        native.write_bytes(b'fixture, never launched')
        with patch.object(cradle.shutil, 'which', side_effect=lambda name: str(prefix / 'codex.cmd') if name == 'codex.cmd' else None):
            self.assertEqual(cradle.codex_command({}), [str(native)])
        with patch.object(cradle.shutil, 'which', side_effect=lambda name: 'native-fallback.exe' if name == 'codex.exe' else None):
            self.assertEqual(cradle.codex_command({}), ['native-fallback.exe'])
        fake_os = type('NonWindows', (), {'name': 'posix', 'environ': os.environ})
        real_call = self.server.cradle._call
        def host_call(prefix, args, deadline):
            with patch.object(cradle, 'os', os):  # process containment must use the actual host, not the platform-policy double
                return real_call(prefix, args, deadline)
        with patch.object(cradle, 'os', fake_os), patch.object(self.server.cradle, '_call', host_call):
            row = self.runners()['codex']
        self.assertEqual((row['installed'], row['login'], row['locked'], row['ready'], row['reason']),
                         (True, 'account', False, False, '未在這個系統核實 Codex 的鎖緊，暫不可用。'))

    def test_probe_link_is_refused_without_touching_its_target(self):
        folder = self.root / '.reader-runtime/cradle/probe'
        folder.mkdir()
        outside = self.root / 'outside-probe'
        outside.mkdir()
        link = folder / 'work'
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(outside)], capture_output=True)
        if done.returncode != 0:
            self.skipTest('cannot create junction')
        try:
            self.settings(runner='codex')
            job = self.wait(self.post_job(), 'failed')
            self.assertEqual(job['error']['code'], 'unsafe_runner')
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual([row['step'] for row in self.codex_traces()], ['login'])
        finally:
            os.rmdir(link)


@unittest.skipUnless(os.name == 'nt', 'Codex lock-down is verified on Windows only')
class CodexPipelineTests(CradleCase):
    def setUp(self):
        super().setUp()
        self.settings(runner='codex')

    def test_codex_think_and_questions_exact_argv_preflights_tokens_and_task_text(self):
        with patch.dict(os.environ, {key: 'fake-secret' for key in cradle.SECRET_ENV}):
            status, _ = self.api('GET', '/api/cradle/runners')
            self.assertEqual(status, 200)
            think = self.wait(self.post_job(), 'done')
            questions = self.wait(self.post_job(kind='questions', way='gap', question=''), 'done')
        calls = self.codex_traces()
        self.assertTrue(all(not call['secret_env'] for call in calls))
        execs = [call for call in calls if call['step'] == 'exec']
        self.assertEqual(len(execs), 3)
        # One detection probe, followed by one fresh login/probe for every AI step.
        self.assertEqual([call['step'] for call in calls], ['--version', 'login', 'sandbox'] + ['login', 'sandbox', 'exec'] * 3)
        expected_config = ['windows.sandbox="mxc"', 'default_permissions="cradle"',
                           'permissions.cradle.filesystem={":minimal"="read", ":workspace_roots"={"."="write"}}',
                           'permissions.cradle.network.enabled=false', 'approval_policy="never"', 'web_search="disabled"',
                           'project_doc_max_bytes=0', 'history.persistence="none"', 'forced_login_method="chatgpt"']
        disabled = ['apps', 'plugins', 'hooks', 'memories', 'multi_agent', 'browser_use', 'computer_use', 'image_generation', 'view_image']
        for call in execs:
            run = Path(call['cwd'])
            self.assertEqual(call['argv'], ['exec', '-C', str(run), '--skip-git-repo-check', '--ephemeral', '--ignore-user-config',
                                            '--ignore-rules', '--json'] + [s for v in expected_config for s in ('-c', v)]
                             + [s for v in disabled for s in ('--disable', v)] + ['讀 task.md 並完整照做。'])
        for job in (think, questions):
            receipt = cradle.read_json(self.folder(job['job_id']) / 'receipt.json')
            self.assertEqual((receipt['runner'], receipt['runner_version']), ('codex', 'codex-cli 0.163.0-fake'))
            for entry, step in zip(self.log(job['job_id']), receipt['steps']):
                self.assertEqual(entry['tokens'], {'input_tokens': 100, 'cached_input_tokens': 40, 'cache_write_input_tokens': 0,
                                                  'output_tokens': 25, 'reasoning_output_tokens': 5})
                self.assertEqual(step['tokens'], entry['tokens'])
                self.assertEqual(entry['tools'], ['command_execution', 'agent_message'])
                self.assertTrue(all(entry[key] is None for key in ('mcp_servers', 'apiKeySource', 'model', 'cost_usd')))
        task = (self.folder(think['job_id']) / 'runs/01-write/task.md').read_text(encoding='utf-8')
        replacement = '你只可以用指令讀寫這個資料夾內的檔案，不要執行 python 或其他程式'
        self.assertEqual(task, cradle.WRITE_TASK.replace('你不能執行程式', replacement))
        qtask = (self.folder(questions['job_id']) / 'runs/01-questions/task.md').read_text(encoding='utf-8')
        self.assertIn(replacement, qtask)
        self.assertNotIn('你不能執行程式', qtask)
        self.settings()
        claude = self.wait(self.post_job(), 'done')
        self.assertEqual((self.folder(claude['job_id']) / 'runs/01-write/task.md').read_text(encoding='utf-8'), cradle.WRITE_TASK)
        self.assertTrue(all(step['tokens'] is None for step in cradle.read_json(self.folder(claude['job_id']) / 'receipt.json')['steps']))
        self.assertFalse((self.root / '.reader-runtime/cradle/probe/work/ok.txt').exists())

    def test_codex_preflight_leak_and_missing_backend_never_start_exec(self):
        for scenario in ('leaky', 'no-backend'):
            with self.subTest(scenario=scenario), patch.dict(os.environ, {'CRADLE_FAKE_CODEX': scenario}):
                job = self.wait(self.post_job(), 'failed')
                self.assertEqual(job['error']['code'], 'unsafe_runner')
        calls = self.codex_traces()
        self.assertEqual([call['step'] for call in calls], ['login', 'sandbox'] * 2)
        for call in calls:
            if call['step'] == 'sandbox':
                self.assertEqual(Path(call['cwd']), self.root / '.reader-runtime/cradle/probe/work')
                self.assertIn('-NonInteractive', call['argv'])
                self.assertIn(str(self.server.server_port), call['argv'][-1])

    def test_codex_login_none_or_api_key_never_start_exec(self):
        for scenario, code in [('login-none', 'not_logged_in'), ('login-apikey', 'unsafe_runner')]:
            with self.subTest(scenario=scenario), patch.dict(os.environ, {'CRADLE_FAKE_CODEX': scenario}):
                job = self.wait(self.post_job(), 'failed')
                self.assertEqual(job['error']['code'], code)
                if code == 'not_logged_in':
                    self.assertEqual(job['error']['message'], 'Codex 未登入，請先打開 Codex 登入一次。')
        self.assertEqual([call['step'] for call in self.codex_traces()], ['login', 'login'])

    def test_codex_events_fail_closed_before_any_output_read_and_classify_errors(self):
        real_read = cradle.read_json
        for scenario, code in [('exec-mcp', 'unsafe_runner'), ('exec-no-thread', 'unsafe_runner'),
                               ('exec-401', 'not_logged_in'), ('exec-limit', 'usage_limit')]:
            reads = []
            def read(path):
                reads.append(Path(path).name)
                return real_read(path)
            with self.subTest(scenario=scenario), patch.dict(os.environ, {'CRADLE_FAKE_CODEX': scenario}), patch.object(cradle, 'read_json', read):
                job = self.wait(self.post_job(), 'failed')
                self.assertEqual(job['error']['code'], code)
                if code == 'unsafe_runner':
                    self.assertEqual([r for r in reads if r in {'receipt.json', 'answer.json', 'review.json', 'questions.json'}], [])
                    self.assertIsNone(self.log(job['job_id'])[0]['agent_receipt'])

    def test_codex_cancel_and_timeout_end_the_tree_including_grandchild(self):
        for scenario, action in [('exec-hang', 'cancel'), ('exec-hang', 'timeout'), ('probe-hang', 'cancel')]:
            with self.subTest(action=action, scenario=scenario), patch.dict(os.environ, {'CRADLE_FAKE_CODEX': scenario}):
                self.settings(runner='codex', timeouts={'write': 2 if action == 'timeout' else 40, 'other': 40})
                before = len(self.codex_traces())
                job_id = self.post_job()
                deadline = time.monotonic() + 15
                while not any('grandchild' in c for c in self.codex_traces()[before:]) and time.monotonic() < deadline:
                    time.sleep(.1)
                pids = [c[k] for c in self.codex_traces()[before:] for k in ('pid', 'grandchild') if k in c]
                self.assertTrue(any('grandchild' in c for c in self.codex_traces()[before:]))
                if action == 'cancel':
                    self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/cancel', b'')[1]['status'], 'cancelled')
                else:
                    self.assertEqual(self.wait(job_id, 'failed')['error']['code'], 'timeout')
                self.assert_gone(pids)

    def test_codex_retry_keeps_its_runner_after_settings_change(self):
        with patch.dict(os.environ, {'CRADLE_FAKE_CODEX': 'exec-401'}):
            job_id = self.post_job()
            self.assertEqual(self.wait(job_id, 'failed')['runner'], 'codex')
        self.settings(runner='claude')
        self.assertEqual(self.api('POST', f'/api/cradle/jobs/{job_id}/retry', b'')[0], 200)
        self.assertEqual(self.wait(job_id, 'done')['runner'], 'codex')
        self.assertEqual(self.traces(), [])


class SmallPieceTests(unittest.TestCase):
    def test_codex_event_reader_ignores_garbage_keeps_last_usage_and_caps_failure(self):
        with tempfile.TemporaryDirectory(prefix='cradle-events-') as folder:
            path = Path(folder) / 'events.jsonl'
            rows = [{'type': 'thread.started'}, {'type': 'turn.completed', 'usage': {'input_tokens': 1}},
                    {'type': 'item.started', 'item': {'type': 'reasoning'}},
                    {'type': 'item.completed', 'item': {'type': 'reasoning'}},
                    {'type': 'turn.completed', 'usage': {'input_tokens': 2}}, {'type': 'error', 'message': 'x' * 500}]
            path.write_text('warning\n{bad\n[]\n' + '\n'.join(json.dumps(row) for row in rows), encoding='utf-8')
            self.assertEqual(cradle.read_codex_events(path), {'thread_started': True, 'tools': ['reasoning'],
                                                            'tokens': {'input_tokens': 2}, 'failure': 'x' * 300})

    def test_answer_specification_is_the_two_sections(self):
        text = cradle.spec_text()
        self.assertTrue(text.startswith('#### 取證、推理與交付'))
        self.assertEqual(re.findall(r'(?m)^#{1,6} .*', text), ['#### 取證、推理與交付', '#### `thinking_v2` 資料契約'])
        self.assertTrue(text.endswith('\n'))

    def test_failure_classes(self):
        events = {'init': {'tools': TOOLS, 'mcp_servers': [], 'apiKeySource': 'none'}, 'rate': {'status': 'rejected', 'resetsAt': time.time() + 2.2 * 3600},
                  'result': {'is_error': True, 'result': 'x'}}
        failure = cradle.classify(1, False, events, '', None, [], Path('.'), 10, 'write')
        self.assertEqual((failure.error['code'], failure.error['message']), ('usage_limit', '已到用量上限，約 3 小時後可再試。'))
        events['rate']['resetsAt'] = time.time() + 10
        self.assertEqual(cradle.classify(1, False, events, '', None, [], Path('.'), 10, 'write').error['message'], '已到用量上限，約 1 小時後可再試。')
        events['rate'] = {}
        self.assertEqual(cradle.classify(1, False, {**events, 'result': {'api_error_status': 403}}, '', None, [], Path('.'), 10, 'write').error['code'], 'not_logged_in')
        self.assertEqual(cradle.classify(7, False, {**events, 'result': {}}, '', {'note': '原因'}, [], Path('.'), 10, 'write').error['message'], 'AI 回報未能完成：原因')
        self.assertEqual(cradle.classify(7, False, {**events, 'result': {}}, '', None, [], Path('.'), 10, 'write').error['message'], '背景 AI 未能完成這一步（結束碼 7）。')
        self.assertEqual(cradle.classify(0, True, events, '', None, [], Path('.'), 600, 'review').error['message'], '覆核超過 10 分鐘仍未完成，已停止。')


if __name__ == '__main__':
    unittest.main()

