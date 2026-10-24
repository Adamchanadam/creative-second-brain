from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir(): ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import ingest_plan_contract as contract  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402


class TextNamedStructureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()) / ("text-structure-" + uuid.uuid4().hex[:12]); self.root.mkdir(parents=True); self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_v4_preserves_blocks_and_adds_heading_metadata(self) -> None:
        source = self.root / "article.md"; source.write_text("# Article\n\nIntro.\n\n## Five Rules\n\nOne.\n\nTwo.\n\nThree.\n", encoding="utf-8")
        rail.start(SimpleNamespace(root=self.root, url=None, image_file=None, text_file=source, source_id="KS-VS-TEXT", job_id="TEXT", title="Article", slug="text", profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE))
        _, package, _ = rail.paths(self.root, "TEXT")
        v3 = contract.source_block_catalog(package, version=contract.SOURCE_CATALOG_VERSION_V3)["blocks"]
        v4 = contract.source_block_catalog(package, version=contract.SOURCE_CATALOG_VERSION_V4)["blocks"]
        self.assertEqual([row["id"] for row in v4], [row["id"] for row in v3])
        self.assertEqual([row["text"] for row in v4], [row["text"] for row in v3])
        self.assertEqual(next(row["heading_level"] for row in v4 if row["text"] == "## Five Rules"), 2)


if __name__ == "__main__": unittest.main()
