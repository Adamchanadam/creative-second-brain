"""Cold-start server behavior over a synthetic portable workspace."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT / "tools"))

import knowledge_nebula as nebula
import knowledge_nebula_server as server_module
from knowledge_nebula_server import NebulaServer


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="nebula-service-")
        self.root = Path(self.temp.name)
        self.vault = self.root / "vault"
        for folder in ("_registry", "sources", "concepts", "outcome-pages", "assets"):
            (self.vault / folder).mkdir(parents=True, exist_ok=True)
        (self.root / "brain.json").write_text(json.dumps({"vault": "vault"}), encoding="utf-8")
        (self.vault / "_registry" / "sources.jsonl").write_text("", encoding="utf-8")
        nebula.build(self.root)
        self.server = NebulaServer(self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(30)
        self.temp.cleanup()

    def request(self, path, method="GET", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def test_a_port_that_cannot_be_bound_stays_an_os_error_so_the_next_port_is_tried(self):
        with patch.object(socketserver.TCPServer, "server_bind", side_effect=PermissionError(10013, "socket access denied")):
            with self.assertRaises(OSError):
                NebulaServer(self.root)

    def test_the_cold_start_rebuild_runs_beside_requests_so_an_opener_finds_the_service_at_once(self):
        entered, release, announced = threading.Event(), threading.Event(), threading.Event()

        def slow_refresh():
            entered.set()
            release.wait(10)

        with patch.object(self.server, "refresh_projections", side_effect=slow_refresh):
            failure = self.server.start_initial_refresh(announced.set)
            self.assertTrue(entered.wait(5))
            try:
                self.assertEqual(self.request("/api/status")[0], 200)
                ping = json.loads(self.request("/api/ping")[1])
                self.assertTrue(ping["updating"])
                self.assertFalse(ping["ready"])
                self.assertFalse(announced.is_set())
            finally:
                release.set()
            self.assertTrue(announced.wait(5))
        self.assertEqual(failure, [])
        self.assertTrue(json.loads(self.request("/api/ping")[1])["ready"])

    def test_a_direct_launch_opens_the_browser_at_once_and_says_ready_after_the_rebuild(self):
        root, vault = self.root / "direct", self.root / "direct" / "vault"
        for folder in ("_registry", "sources", "concepts", "outcome-pages", "assets"):
            (vault / folder).mkdir(parents=True, exist_ok=True)
        (root / "brain.json").write_text(json.dumps({"vault": "vault"}), encoding="utf-8")
        (vault / "_registry" / "sources.jsonl").write_text("", encoding="utf-8")
        nebula.build(root)
        out, release, seen = io.StringIO(), threading.Event(), []

        def slow_refresh(server):
            release.wait(10)

        def browse(url):
            seen.append(out.getvalue())
            release.set()

        def serve(server):
            deadline = time.monotonic() + 10
            while '"ready"' not in out.getvalue() and time.monotonic() < deadline:
                time.sleep(0.02)

        with patch.object(NebulaServer, "refresh_projections", autospec=True, side_effect=slow_refresh), \
             patch.object(NebulaServer, "serve_until_idle", autospec=True, side_effect=serve), \
             patch.object(server_module.webbrowser, "open", side_effect=browse), \
             patch.object(sys, "argv", ["knowledge_nebula_server.py", "--root", str(root)]), \
             contextlib.redirect_stdout(out):
            self.assertEqual(server_module.main(), 0)
        self.assertEqual(seen, [""])
        self.assertEqual(json.loads(out.getvalue())["status"], "ready")

    def test_a_failed_cold_start_rebuild_still_ends_the_service(self):
        with patch.object(self.server, "refresh_projections", side_effect=ValueError("unreadable registry")):
            failure = self.server.start_initial_refresh(lambda: failure.append("announced"))
            self.assertTrue(self.server.retire_requested.wait(5))
        self.assertEqual(len(failure), 1)
        self.assertIsInstance(failure[0], ValueError)
        self.assertFalse(self.server.build_lock.locked())

    def test_a_stop_during_the_cold_start_waits_for_the_ready_line(self):
        release, stopped, lines = threading.Event(), threading.Event(), []

        def announce():
            release.wait(10)
            lines.append("ready")

        with patch.object(self.server, "refresh_projections"):
            failure = self.server.start_initial_refresh(announce)
            threading.Thread(target=lambda: (self.server.close_write_admission(), stopped.set()), daemon=True).start()
            try:
                self.assertFalse(stopped.wait(0.5))
            finally:
                release.set()
            self.assertTrue(stopped.wait(5))
        self.assertEqual((lines, failure), (["ready"], []))

    def test_a_ready_line_that_cannot_be_written_ends_the_service(self):
        def announce():
            raise BrokenPipeError(32, "Broken pipe")

        with patch.object(self.server, "refresh_projections"):
            failure = self.server.start_initial_refresh(announce)
            self.assertTrue(self.server.retire_requested.wait(5))
        self.assertEqual(len(failure), 1)
        self.assertIsInstance(failure[0], BrokenPipeError)
        self.assertFalse(self.server.build_lock.locked())


if __name__ == "__main__":
    unittest.main()

