"""Manual case revalidation accepts only a bound immutable receipt chain."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
from tools.research_program import ProgramError, validate_revalidation_lineage


def digest(value: dict) -> str:
    unsigned = {key: item for key, item in value.items() if key != "payload_sha256"}
    return hashlib.sha256(json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RevalidationLineage(unittest.TestCase):
    case_id = "CASE-LINEAGE"
    topic_id = "portable-topic"
    source_id = "SOURCE-LINEAGE"
    source_identity = "url:fixture-identity"
    selection = {"version": "fixture-v1", "criteria": ["source_interaction"]}

    def test_cli_exposes_manual_revalidation_only(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / "research_program.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("complete-case-revalidation", result.stdout)
        self.assertNotIn("scheduled", result.stdout.lower())

    def test_completion_command_returns_verified_then_replay(self) -> None:
        """The stateful completion command passes its full state to summary."""
        from tools import research_program as program

        with tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            directory = root / "program"
            directory.mkdir()
            original_path = root / "original-receipt.json"
            original_path.write_text('{"historic":"receipt"}\n', encoding="utf-8")
            package = root / "revision"
            package.mkdir()
            revision_receipt = package / "completion-receipt.json"
            revision_receipt.write_text('{"revision":"receipt"}\n', encoding="utf-8")
            contract = {
                "schema_version": "research_topic_selection_contract_v1",
                "version": "fixture-v1",
                "criteria": [{"id": "method", "label": "Method", "evidence_roles": ["interaction"]}],
                "source_interaction_required": True,
                "reproduction_required": True,
                "learning_alignment_required": True,
                "reproduction_criterion": "method",
            }
            source_id, source_identity = "SOURCE-COMPLETE", "url:complete-fixture"
            original_hash = sha(original_path)
            state = {"accepted_cases": [{
                "case_id": "CASE-COMPLETE", "status": "qualified", "source_id": source_id,
                "source_identity": source_identity, "primary_url": "https://example.test/complete",
                "completion_receipt": original_path.relative_to(root).as_posix(),
                "completion_receipt_sha256": original_hash,
                "method_verification": {"state": "needs_revalidation"},
            }]}
            kp_path = package / "knowledge_package.json"
            kp_path.write_text(json.dumps({
                "source": {"source_id": source_id, "source_identity": source_identity,
                           "case_evidence_manifest_sha256": "manifest-hash"},
                "case_evidence": {"manifest_sha256": "manifest-hash"},
                "revision": {"kind": "case_revalidation", "revision_id": "R-COMPLETE",
                             "selection_contract_sha256": program.canonical_json_sha256(contract)},
            }), encoding="utf-8")
            receipt = {
                "knowledge_package_path": kp_path.relative_to(root).as_posix(),
                "source_identity": source_identity, "case_evidence": {"case_id": "CASE-COMPLETE", "topic_id": "fixture-topic"},
            }
            config = {"timezone": "Europe/London", "selection_contract": contract, "evidence_contract": {}}
            args = SimpleNamespace(root=root, topic_id="fixture-topic", case_id="CASE-COMPLETE",
                                   completion_receipt=revision_receipt.relative_to(root).as_posix(), at=None,
                                   isolated_test_clock=True)
            original_write_json = program.write_json
            with patch.object(program, "load_topic", return_value=(directory, config, state)), \
                 patch.object(program, "program_lock", return_value=__import__("contextlib").nullcontext()), \
                 patch.object(program, "event_times", return_value=("2026-10-07", "2026-10-07T12:00:00+00:00", False)), \
                 patch.object(program, "immutable_case_completion", return_value=receipt), \
                 patch.object(program, "validate_revalidation_lineage", return_value=["R-COMPLETE"]), \
                 patch.object(program, "capacity", return_value={"total_accepted": 1}), \
                 patch.object(program, "write_json", wraps=original_write_json):
                with patch("sys.stdout", new_callable=__import__("io").StringIO) as first_stdout:
                    self.assertEqual(program.command_complete_case_revalidation(args), 0)
                first = json.loads(first_stdout.getvalue())
                self.assertEqual(first["status"], "verified")
                self.assertEqual(first["method_verification"], {"verified": 1, "not_assessed": 0, "needs_revalidation": 0, "legacy_unreviewed": 0})
                saved = (directory / "state.json").read_bytes()
                self.assertEqual(json.loads(saved.decode("utf-8"))["accepted_cases"][0]["method_verification"]["state"], "verified")
                with patch("sys.stdout", new_callable=__import__("io").StringIO) as replay_stdout:
                    self.assertEqual(program.command_complete_case_revalidation(args), 0)
                replay = json.loads(replay_stdout.getvalue())
                self.assertEqual(replay["status"], "replayed")
                self.assertEqual(replay["method_verification"]["verified"], 1)
                self.assertEqual((directory / "state.json").read_bytes(), saved)

    def write_receipt(self, package: Path, job_id: str, *, artifacts: list[dict] | None = None) -> Path:
        kp_path = package / "knowledge_package.json"
        receipt = {
            "schema_version": "compiler_worker_v2_completion_receipt_v1",
            "status": "completed",
            "job_id": job_id,
            "source_id": self.source_id,
            "source_identity": self.source_identity,
            "knowledge_package_path": kp_path.relative_to(self.root).as_posix(),
            "knowledge_package_sha256": sha(kp_path),
            "case_evidence": {"case_id": self.case_id, "topic_id": self.topic_id, "case_qualified": True},
            "artifact_manifest": artifacts or [],
        }
        receipt["payload_sha256"] = digest(receipt)
        path = package / "completion_receipt.json"
        path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        return path

    def revision(self, parent_receipt: Path, revision_id: str) -> Path:
        parent = json.loads(parent_receipt.read_text(encoding="utf-8"))
        parent_kp_path = self.root / parent["knowledge_package_path"]
        parent_package = parent_kp_path.parent
        target = parent_package / "revisions" / revision_id
        target.mkdir(parents=True)
        job_id = "JOB-" + revision_id
        selection_hash = digest(self.selection)
        plan = {
            "schema_version": "case_revalidation_plan_v1", "revision_id": revision_id, "job_id": job_id,
            "case_id": self.case_id, "topic_id": self.topic_id, "source_id": self.source_id,
            "source_identity": self.source_identity, "original_completion_receipt_sha256": sha(parent_receipt),
            "selection_contract": self.selection, "selection_contract_sha256": selection_hash,
            "canonical_source_sha256": "fixture-canonical-source",
        }
        plan_path = target / "revalidation_plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        origin = {
            "schema_version": "case_revalidation_origin_v1", "revision_id": revision_id, "job_id": job_id,
            "case_id": self.case_id, "topic_id": self.topic_id, "source_id": self.source_id,
            "source_identity": self.source_identity,
            "original_package": parent_package.relative_to(self.root).as_posix(),
            "original_knowledge_package_sha256": sha(parent_kp_path),
            "original_completion_path": parent_receipt.relative_to(self.root).as_posix(),
            "original_completion_receipt_sha256": sha(parent_receipt),
            "canonical_source_sha256": "fixture-canonical-source",
            "selection_contract": self.selection, "selection_contract_sha256": selection_hash,
            "revision_plan_sha256": sha(plan_path),
        }
        origin["payload_sha256"] = digest(origin)
        origin_path = target / "revision_origin.json"
        origin_path.write_text(json.dumps(origin, ensure_ascii=False), encoding="utf-8")
        source = json.loads(parent_kp_path.read_text(encoding="utf-8"))["source"]
        kp = {
            "job_id": job_id, "source": source,
            "revision": {
                "kind": "case_revalidation", "revision_id": revision_id,
                "origin_package": origin["original_package"],
                "original_completion_path": origin["original_completion_path"],
                "original_completion_receipt_sha256": origin["original_completion_receipt_sha256"],
                "selection_contract": self.selection, "selection_contract_sha256": selection_hash,
                "revision_plan_sha256": origin["revision_plan_sha256"],
            },
        }
        (target / "knowledge_package.json").write_text(json.dumps(kp, ensure_ascii=False), encoding="utf-8")
        return self.write_receipt(target, job_id, artifacts=[
            {"path": origin_path.relative_to(self.root).as_posix(), "sha256": sha(origin_path)},
            {"path": plan_path.relative_to(self.root).as_posix(), "sha256": sha(plan_path)},
        ])

    def test_chain_and_tampered_plan(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR")) as tmp:
            self.root = Path(tmp)
            base = self.root / "package"
            base.mkdir()
            (base / "knowledge_package.json").write_text(json.dumps({"job_id": "JOB-BASE", "source": {
                "source_id": self.source_id, "source_identity": self.source_identity,
            }}), encoding="utf-8")
            accepted = self.write_receipt(base, "JOB-BASE")
            r1 = self.revision(accepted, "R1")
            r2 = self.revision(r1, "R2")
            self.assertEqual(
                validate_revalidation_lineage(
                    self.root, r2, accepted_receipt_path=accepted, accepted_receipt_sha256=sha(accepted),
                    case_id=self.case_id, topic_id=self.topic_id, source_id=self.source_id,
                    source_identity=self.source_identity, selection_contract=self.selection,
                ),
                ["R2", "R1"],
            )
            plan = r1.parent / "revalidation_plan.json"
            changed = json.loads(plan.read_text(encoding="utf-8"))
            changed["tampered"] = True
            plan.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ProgramError, "origin evidence"):
                validate_revalidation_lineage(
                    self.root, r2, accepted_receipt_path=accepted, accepted_receipt_sha256=sha(accepted),
                    case_id=self.case_id, topic_id=self.topic_id, source_id=self.source_id,
                    source_identity=self.source_identity, selection_contract=self.selection,
                )


if __name__ == "__main__":
    unittest.main()
