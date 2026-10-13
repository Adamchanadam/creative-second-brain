"""Case evidence keeps public v1 readback and strict v2 admission separate."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path[:0] = [str(ROOT), str(ROOT.parent.parent / "tests")]

from test_research_integrity import make_case_evidence, write_json
from tools.case_evidence_contract import CaseEvidenceError, validate_manifest


class CaseEvidenceVersionBoundary(unittest.TestCase):
    def test_v1_fixture_is_accepted_but_v2_requires_new_observation(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR")) as tmp:
            workspace = Path(tmp)
            manifest_path, contract, manifest, source_url = make_case_evidence(workspace)
            accepted, _ = validate_manifest(
                workspace, manifest_path, primary_url=source_url, selection_contract=contract,
            )
            self.assertEqual(accepted["schema_version"], "case_evidence_manifest_v1")

            manifest["schema_version"] = "case_evidence_manifest_v2"
            v2_path = manifest_path.with_name("incomplete-v2.json")
            write_json(v2_path, manifest)
            with self.assertRaisesRegex(CaseEvidenceError, "source_observation"):
                validate_manifest(workspace, v2_path, primary_url=source_url, selection_contract=contract)


if __name__ == "__main__":
    unittest.main()
