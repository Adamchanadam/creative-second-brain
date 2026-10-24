from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools import ask_second_brain as ask  # noqa: E402


class QueryExpansionDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / ("query-expansion-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.vault = self.root / "knowledge-base-vault"
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_cli(self, *args: str) -> tuple[int, dict]:
        output = io.StringIO()
        with patch.object(ask, "ROOT", self.root), patch.object(sys, "argv", ["ask_second_brain.py", *args]), contextlib.redirect_stdout(output):
            return ask.main(), json.loads(output.getvalue())

    def test_cli_requires_one_expansion_decision_without_writing(self) -> None:
        for arguments in ((), ("--expand", "Other wording", "--no-expand", "not needed"), ("--no-expand", " ")):
            with self.subTest(arguments=arguments):
                code, result = self.run_cli("prepare", "--question", "Question", *arguments)
                self.assertEqual((code, result["status"]), (1, "blocked"))
                self.assertFalse((self.root / "outputs").exists())

    def test_selected_only_records_not_applicable_and_deduplicates(self) -> None:
        note = self.vault / "sources" / "evidence.md"
        note.parent.mkdir(parents=True)
        note.write_text("# evidence\n", encoding="utf-8")
        code, result = self.run_cli("prepare", "--question", "Question", "--selected-only", "--note", "sources/evidence.md", "--expand", " Alternate ", "--expand", "alternate")
        payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(payload["query_expansions"], ["Alternate"])
        self.assertEqual(payload["expansion_decision"], {"mode": "not_applicable_selected_only"})


if __name__ == "__main__":
    unittest.main()
