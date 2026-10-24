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
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import ingest_plan_contract as contract  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402


class KnowledgeUseRequiredTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
        self.root = base / ("knowledge-use-" + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        source = self.root / "source.md"
        source.write_text("A complete fixture statement supports this knowledge point.", encoding="utf-8")
        rail.start(SimpleNamespace(root=self.root, url=None, image_file=None, text_file=source, source_id="KS-VS-USE", job_id="USE-TEST", title="Use fixture", slug="use", profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE))
        _, self.package, _ = rail.paths(self.root, "USE-TEST")

    def plan(self) -> dict:
        block = contract.source_block_catalog(self.package, version=contract.SOURCE_CATALOG_VERSION_V4)["blocks"][0]
        return {"schema_version": contract.COMPACT_VERSION, "source_catalog_version": contract.SOURCE_CATALOG_VERSION_V4, "input_fingerprint": contract.input_fingerprint(self.package, contract.SOURCE_CATALOG_VERSION_V4), "summary": ["Fixture."], "knowledge": [{"title": "Fixture", "claim": block["text"], "layer": "原文主張", "confidence": "高", "evidence_block_ids": [block["id"]], "reusable_concept": {"status": "not_reusable", "reason": "Fixture."}}]}

    def test_v4_requires_and_accepts_an_explicit_decision(self) -> None:
        with self.assertRaisesRegex(ValueError, "knowledge_use_decision_missing.*choose one of"):
            contract.materialize_compact_plan(self.plan(), self.package)
        invalid = self.plan(); invalid["application"] = {"knowledge_use": "invalid"}
        with self.assertRaisesRegex(ValueError, "expected one of"):
            contract.materialize_compact_plan(invalid, self.package)
        valid = self.plan(); valid["application"] = {"knowledge_use": "學習材料"}
        self.assertEqual(contract.materialize_compact_plan(valid, self.package)["application"]["knowledge_use"], "學習材料")


if __name__ == "__main__":
    unittest.main()
