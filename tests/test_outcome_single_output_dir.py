from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools import outcome_page_generator as generator  # noqa: E402


class SingleSourceOutputDirTests(unittest.TestCase):
    def run_main(self, *argv: str) -> list[Path | None]:
        outputs: list[Path | None] = []
        def fake_generate(*, source_id, vault, output=None, root=None):
            outputs.append(output)
            return {"status": "ready", "output": str(output or generator.DEFAULT_OUTPUT_DIR / f"{source_id}_outcome.html")}
        with patch.object(generator, "generate_outcome_page", fake_generate), patch("tools.knowledge_nebula.refresh_installed", return_value={"status": "ready"}), patch.object(sys, "argv", ["outcome_page_generator.py", *argv]), patch("builtins.print"):
            self.assertEqual(generator.main(), 0)
        return outputs

    def test_one_source_honours_output_dir_and_output_wins(self) -> None:
        target = ROOT / "outputs" / "_tmp_single_output_dir"
        self.assertEqual(self.run_main("--source-id", "KS-VS-0001", "--output-dir", str(target)), [target / "KS-VS-0001_outcome.html"])
        page = target / "named.html"
        self.assertEqual(self.run_main("--source-id", "KS-VS-0001", "--output", str(page), "--output-dir", str(target / "other")), [page])


if __name__ == "__main__":
    unittest.main()
