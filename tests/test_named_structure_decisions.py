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
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir(): ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402


class NamedStructureDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()) / ("named-structure-" + uuid.uuid4().hex[:12])
        self.root.mkdir(parents=True); self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def plan(self) -> tuple[str, dict]:
        image = self.root / "source.png"; Image.new("RGB", (48, 48), "white").save(image)
        job_id = "STRUCT-" + uuid.uuid4().hex[:8]
        order = rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id=None, job_id=job_id, title="Fixture", slug=job_id.lower(), profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE))
        analysis = copy.deepcopy(order["response_template"]["image_analysis"])
        anchors = [{"id": "IMG-010", "kind": "title", "evidence": "Six Rules", "region": {"x": 0, "y": 0, "width": 1, "height": .2}}, *[{"id": f"IMG-01{i}", "kind": "text", "evidence": f"Rule {i}", "region": {"x": 0, "y": i*.2, "width": 1, "height": .2}} for i in range(1, 4)]]
        analysis.update({"analysis_status": "complete", "checker_status": "passed", "checker_evidence": "Fixture.", "visual_type": "infographic", "title": "Fixture", "summary": "Fixture.", "anchors": anchors, "transcription": [{"anchor_id": row["id"], "text": row["evidence"], "confidence": "high"} for row in anchors], "source_claims": [{"claim": "Fixture", "anchor_ids": [row["id"] for row in anchors], "verification_status": "image_visible"}], "coverage": [{"anchor_id": row["id"], "disposition": "source_evidence" if row["id"] == "IMG-010" else "knowledge_point", "reason": "Fixture."} for row in anchors]})
        path = self.root / "analysis.json"; worker.write_json(path, analysis)
        rail.submit_image_analysis(self.root, job_id, path)
        pointer = rail.work_order(self.root, job_id); draft = worker.read_json(self.root / pointer["draft_file"])
        draft.update(summary=["Fixture."], application={"knowledge_use": "學習材料"}, knowledge=[{"title": f"Rule {i}", "claim": f"Rule {i}", "layer": "原文主張", "confidence": "高", "evidence_block_ids": [f"IMG-01{i}"], "reusable_concept": {"status": "not_reusable", "reason": "Fixture."}} for i in range(1, 4)])
        return job_id, draft

    def test_named_candidate_requires_parent_or_reasoned_decision(self) -> None:
        job, plan = self.plan(); path = self.root / "missing.json"; worker.write_json(path, plan)
        failed = rail.check_plan(self.root, job, path)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len([x for x in failed["errors"] if x.startswith("named_structure_decision_missing:")]), 1)
        plan["structure_decisions"] = [{"block_id": "IMG-010", "decision": "not_a_named_structure", "reason": "The title is only fixture context and not a declared framework."}]
        worker.write_json(path, plan); self.assertEqual(rail.check_plan(self.root, job, path)["status"], "passed")


if __name__ == "__main__": unittest.main()
