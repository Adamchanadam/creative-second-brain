from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import ingest_plan_contract as contract  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402


class ImageTwoStepSubmitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()) / ("image-two-step-" + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True); self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def start(self, job_id: str) -> dict:
        image = self.root / f"{job_id}.png"; Image.new("RGB", (32, 32), "white").save(image)
        return rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id="KS-VS-" + job_id, job_id=job_id, title="Image fixture", slug=job_id.lower(), profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE))

    @staticmethod
    def analysis(pointer: dict) -> dict:
        result = copy.deepcopy(pointer["response_template"]["image_analysis"])
        result.update({"analysis_status": "complete", "checker_status": "passed", "checker_evidence": "Fixture inspection.", "visual_type": "infographic", "title": "Image fixture", "summary": "Fixture.", "anchors": [{"id": "IMG-001", "kind": "text", "evidence": "Fixture fact.", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}], "transcription": [{"anchor_id": "IMG-001", "text": "Fixture fact.", "confidence": "high"}], "source_claims": [{"claim": "Fixture fact.", "anchor_ids": ["IMG-001"], "verification_status": "image_visible"}], "coverage": [{"anchor_id": "IMG-001", "disposition": "knowledge_point", "reason": "Fixture."}]})
        return result

    @staticmethod
    def semantic(version: str) -> dict:
        return {"schema_version": contract.COMPACT_VERSION, "source_catalog_version": version, "summary": ["Fixture."], "knowledge": [{"title": "Fixture fact", "claim": "Fixture fact.", "layer": "原文主張", "confidence": "高", "evidence_block_ids": ["IMG-001"], "reusable_concept": {"status": "not_reusable", "reason": "Fixture."}}], "application": {"knowledge_use": "學習材料"}}

    def test_analysis_creates_a_separate_semantic_work_order(self) -> None:
        initial = self.start("IMG-TWO")
        response = self.root / "analysis.json"; worker.write_json(response, {"image_analysis": self.analysis(initial)})
        with patch.object(rail, "work_order_prior_candidates", return_value=[]):
            next_order = rail.submit_image_analysis(self.root, "IMG-TWO", response)
        self.assertEqual((next_order["status"], next_order["action"]), ("needs_input", "prepare_semantic_plan"))
        draft = worker.read_json(self.root / next_order["draft_file"]); draft.update(self.semantic(contract.SOURCE_CATALOG_VERSION_V4))
        draft["input_fingerprint"] = contract.input_fingerprint(rail.paths(self.root, "IMG-TWO")[1], contract.SOURCE_CATALOG_VERSION_V4)
        semantic = self.root / "semantic.json"; worker.write_json(semantic, draft)
        self.assertEqual(rail.check_plan(self.root, "IMG-TWO", semantic)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
