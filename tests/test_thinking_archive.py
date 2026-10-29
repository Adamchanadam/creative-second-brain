"""Archive-lock behavior over the portable synthetic fixture."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
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

import cradle_fixture
import thinking_archive as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="archive-")
        self.root = Path(self.temp.name)
        self.packet, self.answer, self.review = cradle_fixture.fixture(self.root, "vault")
        self.vault = Path(self.packet["vault"])

    def tearDown(self):
        self.temp.cleanup()

    def save(self):
        return archive.archive_answer(self.vault, self.packet, self.answer, self.review)

    def lock(self):
        path = self.vault / "thinking" / f'{self.packet["session_id"]}.lock'
        path.parent.mkdir(exist_ok=True)
        return path

    def test_dead_pid_lock_is_removed_before_archive(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        path = self.lock()
        path.write_text(str(child.pid), encoding="utf-8")
        self.assertEqual(self.save()["status"], "complete")
        self.assertFalse(path.exists())

    def test_clearing_a_stale_lock_never_lets_two_writers_in(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        key, path = self.packet["session_id"], self.lock()
        path.write_text(str(child.pid), encoding="utf-8")
        real_unlink, real_atomic = Path.unlink, archive.atomic_text
        paused, go, b_inside, release_b = (threading.Event() for _ in range(4))
        guard = threading.Lock()
        holders, overlap, errors = [], [], []

        def slow_unlink(target, *args, **kwargs):
            if Path(target).name == path.name and threading.current_thread().name == "writer-A" and not paused.is_set():
                paused.set()
                if not go.wait(10):
                    raise RuntimeError("test coordination timeout")
            return real_unlink(target, *args, **kwargs)

        def traced(vault, target, content):
            name = threading.current_thread().name
            with guard:
                holders.append(name)
                if len(holders) > 1:
                    overlap.append(list(holders))
            try:
                if name == "writer-B" and Path(target).suffix == ".json":
                    b_inside.set()
                    release_b.wait(5)
                return real_atomic(vault, target, content)
            finally:
                with guard:
                    holders.remove(name)

        def writer():
            try:
                archive.save_record(self.vault, self.packet, self.answer, self.review)
            except Exception as error:
                errors.append(threading.current_thread().name + ": " + type(error).__name__ + ": " + str(error))

        with patch.object(Path, "unlink", slow_unlink), \
             patch.object(archive, "atomic_text", traced), \
             patch.object(archive, "LOCK_WAIT_SECONDS", 20), \
             patch.object(archive, "LOCK_POLL_SECONDS", 0.01):
            a = threading.Thread(target=writer, name="writer-A")
            a.start()
            self.assertTrue(paused.wait(10))
            b = threading.Thread(target=writer, name="writer-B")
            b.start()
            b_inside.wait(1.5)
            go.set()
            time.sleep(0.5)
            release_b.set()
            a.join(20)
            b.join(20)
        self.assertFalse(a.is_alive() or b.is_alive())
        self.assertEqual((overlap, errors), ([], []))
        self.assertFalse(path.exists())
        self.assertFalse(path.with_name(path.name + ".break").exists())
        records = sorted((self.vault / "thinking").glob("ASK-*.json"))
        self.assertEqual(len(records), 1)
        archive.validate_record(json.loads(records[0].read_text(encoding="utf-8")), key)

    def test_stale_lock_breaker_left_by_a_crash_blocks_like_a_stale_lock(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        path = self.lock()
        path.write_text(str(child.pid), encoding="utf-8")
        breaker = path.with_name(path.name + ".break")
        breaker.write_text("", encoding="utf-8")
        with patch.object(archive, "LOCK_WAIT_SECONDS", 0.05), patch.object(archive, "LOCK_POLL_SECONDS", 0.005):
            with self.assertRaisesRegex(ValueError, "write is active"):
                self.save()
        self.assertEqual((path.read_text(encoding="utf-8"), breaker.exists()), (str(child.pid), True))

    def test_lock_that_changed_since_it_was_read_is_never_removed(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        path, swapped = self.lock(), []
        path.write_text(str(child.pid), encoding="utf-8")
        real = archive._pid_alive

        def alive(pid):
            if pid == child.pid and not swapped:
                swapped.append(1)
                path.write_text(str(os.getpid()), encoding="utf-8")
                return False
            return real(pid)

        with patch.object(archive, "_pid_alive", alive), \
             patch.object(archive, "LOCK_WAIT_SECONDS", 0.05), \
             patch.object(archive, "LOCK_POLL_SECONDS", 0.005):
            with self.assertRaisesRegex(ValueError, "write is active"):
                self.save()
        self.assertEqual(path.read_text(encoding="utf-8"), str(os.getpid()))
        self.assertFalse(path.with_name(path.name + ".break").exists())

    def test_live_pid_lock_still_times_out(self):
        path = self.lock()
        path.write_text(str(os.getpid()), encoding="utf-8")
        with patch.object(archive, "LOCK_WAIT_SECONDS", 0.01), patch.object(archive, "LOCK_POLL_SECONDS", 0.001):
            with self.assertRaisesRegex(ValueError, "write is active"):
                self.save()

    def test_windows_refusals_while_a_lock_is_read_or_deleted_are_waited_out(self):
        real_open, real_unlink, refused = Path.open, Path.unlink, {"open": 0, "unlink": 0}

        def open_once(path, *args, **kwargs):
            if path.suffix == ".lock" and args and args[0] == "x" and not refused["open"]:
                refused["open"] += 1
                raise PermissionError("delete pending")
            return real_open(path, *args, **kwargs)

        def unlink_twice(path, *args, **kwargs):
            if path.suffix == ".lock" and refused["unlink"] < 2:
                refused["unlink"] += 1
                raise PermissionError("in use")
            return real_unlink(path, *args, **kwargs)

        with patch.object(Path, "open", open_once), \
             patch.object(Path, "unlink", unlink_twice), \
             patch.object(archive, "LOCK_POLL_SECONDS", 0.001), \
             patch.object(archive, "LOCK_UNLINK_PAUSE_SECONDS", 0.001):
            self.assertEqual(self.save()["status"], "complete")
        self.assertEqual(refused, {"open": 1, "unlink": 2})
        self.assertEqual(list((self.vault / "thinking").glob("*.lock")), [])

    def test_a_folder_that_cannot_be_written_is_still_an_error(self):
        real_open = Path.open

        def refuse(path, *args, **kwargs):
            if path.suffix == ".lock" and args and args[0] == "x":
                raise PermissionError("read-only folder")
            return real_open(path, *args, **kwargs)

        with patch.object(Path, "open", refuse), \
             patch.object(archive, "LOCK_WAIT_SECONDS", 0.02), \
             patch.object(archive, "LOCK_POLL_SECONDS", 0.001):
            with self.assertRaises(PermissionError):
                self.save()

    def test_empty_or_unreadable_lock_is_never_cleared(self):
        path = self.lock()
        for content in (b"", bytes([255, 254, 0]), b"99999999999999999999", b"0"):
            path.write_bytes(content)
            with patch.object(archive, "LOCK_WAIT_SECONDS", 0.01), patch.object(archive, "LOCK_POLL_SECONDS", 0.001):
                with self.assertRaisesRegex(ValueError, "write is active"):
                    self.save()
            self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()

