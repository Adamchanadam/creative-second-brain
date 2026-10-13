"""Completion revisions preserve immutable inputs and reject later drift."""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT / "tools"))
import completion_revisions as revisions


class CompletionRevisions(unittest.TestCase):
    def test_accept_protection_and_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            kp = package / "knowledge_package.json"
            kp.write_text(json.dumps({"source": {"retention_mode": "summarized_allowed"}}), encoding="utf-8")
            source_slice = package / "slice.md"
            source_slice.write_text("old slice", encoding="utf-8")
            original = package / "canonical_source.md"
            original.write_bytes(b"Immutable original\nSecond line\n")
            source = root / "source.md"
            source.write_text("Original source summary", encoding="utf-8")
            note = root / "application.md"
            note.write_text("old interpretation", encoding="utf-8")
            receipt = {
                "source_id": "TEST",
                "source_identity": "identity",
                "knowledge_package_path": "package/knowledge_package.json",
                "knowledge_package_sha256": revisions.sha(kp.read_bytes()),
                "source_slice_path": "package/slice.md",
                "source_slice_sha256": revisions.sha(source_slice.read_bytes()),
                "adapter": {"source_note": "source.md", "application_note": "application.md", "knowledge_points": []},
                "artifact_manifest": [
                    {"path": "source.md", "sha256": revisions.sha(source.read_bytes())},
                    {"path": "application.md", "sha256": revisions.sha(note.read_bytes())},
                    {"path": "package/canonical_source.md", "sha256": revisions.sha(original.read_bytes())},
                ],
            }
            receipt["payload_sha256"] = revisions.digest(receipt)
            receipt_path = package / "completion_receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            original_receipt = receipt_path.read_bytes()
            proof = root / "review.txt"
            proof.write_text("Reviewed interpretation against the immutable original.", encoding="utf-8")
            note.write_text("New interpretation\nWith explicit scope.\n", encoding="utf-8")
            record = {
                "schema_version": revisions.SCHEMA,
                "completion_payload_sha256": receipt["payload_sha256"],
                "source_id": "TEST",
                "source_identity": "identity",
                "authorization": "user explicitly authorized this revision",
                "review_summary": "source-grounded scope correction",
                "previous_pointer_sha256": "",
                "protected_inputs": [{"path": "package/canonical_source.md", "sha256": revisions.sha(original.read_bytes())}],
                "changes": [{
                    "original_path": "application.md",
                    "original_sha256": receipt["artifact_manifest"][1]["sha256"],
                    "current_path": "application.md",
                    "sha256": revisions.sha(note.read_bytes()),
                    "reason": "correct interpretation",
                    "evidence": [{"path": "review.txt", "sha256": revisions.sha(proof.read_bytes()), "supports": "source comparison"}],
                }],
            }

            def signed(value: dict) -> dict:
                value["payload_sha256"] = revisions.digest(value)
                return value

            for forbidden in ("package/canonical_source.md", "package/knowledge_package.json", "package/slice.md"):
                bad = copy.deepcopy(record)
                bad["changes"][0].update(
                    original_path=forbidden,
                    original_sha256=revisions.original_files(receipt)[forbidden],
                    current_path=forbidden,
                    sha256=revisions.sha((root / forbidden).read_bytes()),
                )
                with self.assertRaises(ValueError):
                    revisions.validate(root, signed(bad), receipt)
            original_lf = original.read_bytes()
            original.write_bytes(original_lf.replace(b"\n", b"\r\n"))
            revisions.validate(root, signed(record), receipt)
            original.write_bytes(original_lf + b"changed")
            with self.assertRaises(ValueError):
                revisions.validate(root, signed(record), receipt)
            original.write_bytes(original_lf.replace(b"\n", b"\r\n"))
            revisions.accept(root, package, receipt, record)
            self.assertEqual(receipt_path.read_bytes(), original_receipt)
            self.assertTrue(revisions.read_current(root, package, receipt))
            proof.write_text("changed evidence", encoding="utf-8")
            with self.assertRaises(ValueError):
                revisions.read_current(root, package, receipt)
            self.assertEqual(receipt_path.read_bytes(), original_receipt)


if __name__ == "__main__":
    unittest.main()
