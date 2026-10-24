"""Exercise bounded completion evidence names at the Windows path limit."""
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


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import completion_revisions as revisions
from tools import compiler_worker_v2 as worker
from tools import ingest_plan_contract as contract
from tools import ingest_rail as rail
from tools import workspace_paths


SOURCE_ID = "KS-VS-20261023-ABCDE"
PACKAGE_SLUG = "p" * 29
PACKAGE_FOLDER_LENGTH = 50
REVISION_ID = "REV-0055-CITATION-20260926"
BODY = "此合成來源只驗證 Windows 路徑預算下的完成來源修訂交易。"


class EvidencePathBudgetTests(unittest.TestCase):
    """Use formal product entries; only fixtures and legacy reshaping are local."""

    def root_at(self, length: int, label: str) -> Path:
        base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()).resolve()
        base.mkdir(parents=True, exist_ok=True)
        stem = f"terra-p1-{label}-{uuid.uuid4().hex[:8]}-"
        padding = length - len(str(base / stem))
        self.assertGreater(padding, 0, "test temporary root is already too long")
        root = base / (stem + "x" * padding)
        self.assertEqual(len(str(root.resolve())), length)
        root.mkdir()
        return root

    def assert_tree_within_budget(self, root: Path, label: str, *, ignore: tuple[Path, ...] = ()) -> int:
        paths = [
            path for path in [root, *root.rglob("*")]
            if not any(path == excluded or excluded in path.parents for excluded in ignore)
        ]
        longest = max(paths, key=lambda path: len(str(path.resolve())))
        longest_length = len(str(longest.resolve()))
        self.assertLessEqual(longest_length, 256, f"{label}: {longest}")
        print(
            f"PATH_BUDGET {label} longest={longest_length} "
            f"path={longest.relative_to(root).as_posix() if longest != root else '.'}"
        )
        return longest_length

    @staticmethod
    def compact_plan(package: Path) -> dict:
        version = contract.CURRENT_SOURCE_CATALOG_VERSION
        return {
            "schema_version": contract.COMPACT_VERSION,
            "input_fingerprint": contract.input_fingerprint(package, version),
            "source_catalog_version": version,
            "summary": ["此合成來源只驗證完成來源修訂的路徑預算。"],
            "knowledge": [{
                "title": "路徑預算修訂",
                "claim": BODY,
                "layer": "原文主張",
                "confidence": "高",
                "evidence_block_ids": ["SRC-001"],
                "reusable_concept": {"status": "not_reusable", "reason": "只用於離線路徑預算測試。"},
            }],
            "application": {"knowledge_use": "學習材料"},
        }

    def complete_source(self, root: Path) -> tuple[Path, Path]:
        (root / "tools").mkdir(exist_ok=True)
        shutil.copy2(ROOT / "tools" / "outcome_page_generator.py", root / "tools" / "outcome_page_generator.py")
        source = root / "source.md"
        source.write_text(BODY, encoding="utf-8")
        job_id = "PATH-BUDGET"
        rail.start(SimpleNamespace(
            root=root, url=None, image_file=None, text_file=source,
            source_id=SOURCE_ID, job_id=job_id, title="路徑預算來源",
            slug=PACKAGE_SLUG, profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
        ))
        _, package, kp_path = rail.paths(root, job_id)
        self.assertEqual(package.relative_to(root).parent.as_posix(), "outputs/compiler_worker_v2")
        self.assertEqual(len(package.name), PACKAGE_FOLDER_LENGTH)
        response_path = root / "content-plan.json"
        worker.write_json(response_path, self.compact_plan(package))
        rail.measured(root, job_id, "submit_plan", rail.submit_plan,
                      root=root, job_id=job_id, response_path=response_path)
        completed = rail.advance(root, job_id, workspace_paths.vault_path(root))
        self.assertEqual(completed["status"], "completed", completed)
        return package, kp_path

    def prepare_content(self, root: Path) -> tuple[Path, Path, Path, dict]:
        package, kp_path = self.complete_source(root)
        plan = self.compact_plan(package)
        plan["application"] = {
            "knowledge_use": "學習材料",
            "adoption_reason": "修訂已保存的應用判斷，但不改變來源身分。",
        }
        plan_path = root / "content-revision-plan.json"
        worker.write_json(plan_path, plan)
        prepared = revisions.prepare_content_revision(
            root, kp_path, plan_path,
            authorization="Explicit synthetic path-budget content revision authorization.",
            reason="Exercise real bounded evidence names for a completed source revision.",
        )
        return package, kp_path, plan_path, prepared

    @staticmethod
    def accepted_review(prepared: dict, path: Path) -> dict:
        review = copy.deepcopy(prepared["review_template"])
        review.update(status="accepted", reviewer="SYNTHETIC-PATH-BUDGET-TEST")
        review["payload_sha256"] = revisions.digest(review)
        worker.write_json(path, review)
        return review

    def assert_transaction_temp_budget(self, journal_path: Path) -> None:
        temporary = journal_path.with_name("." + journal_path.name + ".tmp")
        self.assertEqual(len(str(temporary.resolve())), len(str(journal_path.resolve())) + 5)
        self.assertLessEqual(len(str(temporary.resolve())), 256)

    def legacy_content_transaction(self, root: Path, journal_path: Path) -> Path:
        """Convert a real prepared transaction to the retained 1.9.19/1.9.20 spelling."""

        journal = worker.read_json(journal_path)
        current = journal_path.parent
        legacy = current.with_name(f"content-{journal['request_sha256']}")
        current.rename(legacy)
        journal["request_path"] = (legacy / "request.json").relative_to(root).as_posix()
        for item in journal["items"]:
            staged = legacy / Path(item["candidate_path"]).name
            snapshot = legacy / Path(item["before_path"]).name
            index = staged.name.split("-", 1)[0]
            legacy_staged = legacy / f"{index}-{Path(item['path']).name}"
            legacy_snapshot = legacy / f"{index}-before-{Path(item['path']).name}"
            staged.rename(legacy_staged)
            snapshot.rename(legacy_snapshot)
            item["candidate_path"] = legacy_staged.relative_to(root).as_posix()
            item["before_path"] = legacy_snapshot.relative_to(root).as_posix()
        journal["payload_sha256"] = revisions.digest(journal)
        legacy_path = legacy / "transaction.json"
        legacy_path.write_bytes(revisions._json_bytes(journal))
        return legacy_path

    def test_budgeted_content_and_child_archives(self) -> None:
        root = self.root_at(60, "content")
        self.assertEqual(len(str(root.resolve())), 60)
        package, _, _, prepared = self.prepare_content(root)
        journal_path = root / prepared["revision_file"]
        self.assertEqual(journal_path.parent.name[:8], "content-")
        self.assertEqual(len(journal_path.parent.name), len("content-") + 12)
        self.assert_transaction_temp_budget(journal_path)
        self.assert_tree_within_budget(root, "content-prepared")

        review_path = root / "content-review.json"
        self.accepted_review(prepared, review_path)
        dry_run = revisions.apply_content_revision(root, journal_path, review_path, dry_run=True)
        self.assertEqual(dry_run["status"], "reviewed_ready")
        self.assert_transaction_temp_budget(journal_path)
        self.assert_tree_within_budget(root, "content-dry-run")
        self.assertEqual(revisions.apply_content_revision(root, journal_path, review_path)["status"], "completed")
        self.assert_transaction_temp_budget(journal_path)
        self.assert_tree_within_budget(root, "content-applied")

        child = package / "revisions" / REVISION_ID
        self.assertEqual(len(child.name), 26)
        asset = child / "assets" / f"{SOURCE_ID}_{PACKAGE_SLUG}_source_01.png"
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"path-budget contract-defect source")
        image_sha = worker.sha256_bytes(asset.read_bytes())
        archived = worker.archive_contract_defect_assets(child, {
            "pages": {"IG-001": {"asset_path": asset.relative_to(child).as_posix(), "attempts": [{"png_sha256": image_sha}]}},
        }, 1)
        self.assertEqual(archived[0]["archived_asset_path"], f"assets/reopen-01/01-{image_sha[:12]}.png")
        self.assert_tree_within_budget(root, "contract-defect-archive")

        reference = child / "assets" / "rejected.png"
        reference.write_bytes(b"path-budget rejected visual")
        first = {"attempt_number": 1, "png_sha256": worker.sha256_bytes(reference.read_bytes())}
        preserved, archive = worker.preserve_visual_repair_reference(child, reference, first, {})
        self.assertEqual(archive.name, f"ref-1-{first['png_sha256'][:12]}.png")
        self.assertEqual(preserved["preserved_reference_asset"], archive.relative_to(child).as_posix())
        self.assert_tree_within_budget(root, "visual-repair-archive")

    def test_legacy_content_transaction_reuses_full_digest_folder_and_feeds_recovery(self) -> None:
        root = self.root_at(60, "legacy")
        package, kp_path, plan_path, prepared = self.prepare_content(root)
        legacy_journal = self.legacy_content_transaction(root, root / prepared["revision_file"])
        self.assertEqual(len(legacy_journal.parent.name), len("content-") + 64)
        reused = revisions.prepare_content_revision(
            root, kp_path, plan_path,
            authorization="Explicit synthetic path-budget content revision authorization.",
            reason="Exercise real bounded evidence names for a completed source revision.",
        )
        self.assertEqual(root / reused["revision_file"], legacy_journal)
        review_path = root / "legacy-content-review.json"
        review = self.accepted_review(reused, review_path)
        self.assertEqual(
            revisions.apply_content_revision(root, legacy_journal, review_path, dry_run=True)["status"],
            "reviewed_ready",
        )
        self.assertEqual(revisions.apply_content_revision(root, legacy_journal, review_path)["status"], "completed")
        self.assertTrue((legacy_journal.parent / f"review-{revisions.digest(review)}.json").is_file())

        journal = worker.read_json(legacy_journal)
        completion_path = root / journal["completion_path"]
        evidence = revisions._recovery_evidence(root, {
            "package": package,
            "completion": worker.read_json(completion_path),
            "kp_path": root / journal["kp_path"],
            "completion_path": completion_path,
        }, [legacy_journal], legacy_journal.parent)
        self.assertTrue(evidence["files"])
        self.assert_tree_within_budget(
            root, "legacy-content-completed", ignore=(legacy_journal.parent,),
        )

    def test_budget_refusal_precedes_evidence_directory_creation(self) -> None:
        source_root = self.root_at(96, "refusal-source")
        source_package, source_kp_path = self.complete_source(source_root)
        root = self.root_at(105, "refusal-target")
        shutil.copytree(source_root, root, dirs_exist_ok=True)
        package = root / source_package.relative_to(source_root)
        kp_path = root / source_kp_path.relative_to(source_root)
        plan = self.compact_plan(package)
        plan["application"] = {
            "knowledge_use": "學習材料",
            "adoption_reason": "修訂應用判斷，但不改變來源身分。",
        }
        plan_path = root / "overbudget-content-plan.json"
        worker.write_json(plan_path, plan)
        evidence_root = package / "completion_revision_evidence"
        self.assertFalse(evidence_root.exists())
        before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        }
        with self.assertRaises(ValueError):
            revisions.prepare_content_revision(
                root, kp_path, plan_path,
                authorization="Explicit synthetic path-budget content revision authorization.",
                reason="Refuse the transaction before creating any over-budget evidence folder.",
            )
        after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse(evidence_root.exists())


if __name__ == "__main__":
    unittest.main()
