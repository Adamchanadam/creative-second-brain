"""Lock Windows child-process flags so reader helpers never create a visible console.

``DETACHED_PROCESS`` gives a venv Python redirector no console; its console-Python child can then make
Windows open a visible terminal.  The reader launch and ``taskkill`` helper must instead use
``CREATE_NO_WINDOW``.  These checks only inspect code or record mocked subprocess calls: no vault,
workspace, reader, or process tree is started.
"""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "runtime" / "workspace" if (ROOT / "runtime" / "workspace" / "tools").is_dir() else ROOT
TOOLS = CODE / "tools"


def _is_creationflags_key(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "creationflags"


def _integer_uses_detached_bit(node: ast.AST) -> list[int]:
    return [part.lineno for part in ast.walk(node)
            if isinstance(part, ast.Constant) and isinstance(part.value, int) and not isinstance(part.value, bool)
            and part.value & 0x8]


def detached_process_violations(source: str, name: str = "<source>") -> list[str]:
    """Return source locations using DETACHED_PROCESS or its 0x8 flag inside creationflags."""
    tree = ast.parse(source, name)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "DETACHED_PROCESS":
            violations.append(f"{name}:{node.lineno}: DETACHED_PROCESS name")
        elif isinstance(node, ast.Attribute) and node.attr == "DETACHED_PROCESS":
            violations.append(f"{name}:{node.lineno}: DETACHED_PROCESS attribute")
        elif isinstance(node, ast.alias) and node.name == "DETACHED_PROCESS":
            violations.append(f"{name}:{node.lineno}: DETACHED_PROCESS import")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value == "DETACHED_PROCESS":
            violations.append(f"{name}:{node.lineno}: DETACHED_PROCESS string")

        flag_values: list[ast.AST] = []
        if isinstance(node, ast.Call):
            flag_values.extend(keyword.value for keyword in node.keywords if keyword.arg == "creationflags")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Subscript) and _is_creationflags_key(target.slice) for target in targets):
                flag_values.append(node.value)
        for value in flag_values:
            for lineno in _integer_uses_detached_bit(value):
                violations.append(f"{name}:{lineno}: DETACHED_PROCESS bit 0x8 in creationflags")
    return violations


def python_sources() -> list[Path]:
    paths: list[Path] = []
    for base in (ROOT / "scripts", CODE / "tools"):
        if base.is_dir():
            paths.extend(path for path in base.rglob("*.py") if "tests" not in path.parts)
    return sorted(paths)


def load(name: str, path: Path):
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class WindowsNoConsoleWindowTests(unittest.TestCase):
    def test_static_guard_rejects_detached_process_and_its_bit(self):
        for path in python_sources():
            with self.subTest(path=path):
                self.assertEqual(detached_process_violations(path.read_text(encoding="utf-8"), str(path)), [])

    def test_static_guard_self_check(self):
        counterexamples = (
            "subprocess.Popen(a, creationflags=subprocess.DETACHED_PROCESS)",
            'options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0)',
            "from subprocess import DETACHED_PROCESS",
            "subprocess.Popen(a, creationflags=0x00000008 | 0x200)",
        )
        for source in counterexamples:
            with self.subTest(source=source):
                self.assertTrue(detached_process_violations(source), source)
        self.assertEqual(detached_process_violations(
            "subprocess.Popen(a, creationflags=subprocess.CREATE_NO_WINDOW | 0x4)"), [])

    @unittest.skipUnless(os.name == "nt", "Windows creation flags are unavailable")
    def test_reader_launch_uses_no_window_not_detached_process(self):
        if str(TOOLS) not in sys.path:
            sys.path.insert(0, str(TOOLS))
        brain = load("windows_no_console_brain", ROOT / "scripts" / "brain.py")
        cfg = {"vault": "notes", "entry_contract": []}
        recorded: list[dict] = []
        clock = {"now": 0.0}

        class ExitedProcess:
            def poll(self):
                return 1

        def record_popen(*args, **kwargs):
            recorded.append(kwargs)
            return ExitedProcess()

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(brain, "require_ready", return_value=cfg), \
             patch.object(brain, "ROOT", Path(directory)), \
             patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
             patch.object(brain, "_reader_ports", return_value=(54321,)), \
             patch.object(brain, "_find_reader_state", return_value=(None, None)), \
             patch.object(brain, "_refresh_home_projection", return_value={"status": "home_refreshed"}), \
             patch.object(brain.subprocess, "Popen", side_effect=record_popen), \
             patch.object(brain.time, "monotonic", side_effect=lambda: clock["now"]), \
             patch.object(brain.time, "sleep", side_effect=lambda seconds: clock.__setitem__("now", clock["now"] + seconds)):
            with self.assertRaisesRegex(ValueError, "exited before readiness"):
                brain.open_reader("home")

        self.assertEqual(len(recorded), 1)
        flags = recorded[0]["creationflags"]
        self.assertTrue(flags & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(flags & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(flags & 0x8)  # DETACHED_PROCESS: CREATE_NO_WINDOW is ignored beside it
        self.assertFalse(flags & subprocess.CREATE_NEW_CONSOLE)  # a new console is a visible window

    @unittest.skipUnless(os.name == "nt", "Windows creation flags are unavailable")
    def test_taskkill_uses_no_window(self):
        if str(TOOLS) not in sys.path:
            sys.path.insert(0, str(TOOLS))
        cradle = load("windows_no_console_cradle", TOOLS / "knowledge_cradle.py")
        recorded: list[dict] = []

        def record_run(*args, **kwargs):
            recorded.append(kwargs)
            return subprocess.CompletedProcess(args=[], returncode=0)

        with patch.object(cradle.subprocess, "run", side_effect=record_run):
            self.assertTrue(cradle.end_tree_by_pid(12345))
        self.assertEqual(len(recorded), 1)
        self.assertIn("creationflags", recorded[0])
        self.assertTrue(recorded[0]["creationflags"] & subprocess.CREATE_NO_WINDOW)
        self.assertFalse(recorded[0]["creationflags"] & (0x8 | subprocess.CREATE_NEW_CONSOLE))


if __name__ == "__main__":
    unittest.main()
