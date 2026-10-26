"""Source attachments use a completed synthetic PUBLIC source only."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
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
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import ingest_plan_contract as contract  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402
from tools import outcome_page_generator  # noqa: E402
from tools import workspace_paths  # noqa: E402


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@contextmanager
def source_generator_root(root: Path):
    """Use the fixture's installed-workspace root for direct in-process calls."""
    defaults = outcome_page_generator.completed_v2_package_for_standard_outcome.__kwdefaults__
    assert defaults is not None
    previous = defaults["root"]
    defaults["root"] = root
    try:
        yield
    finally:
        defaults["root"] = previous


@contextmanager
def fixture_environment(item: SimpleNamespace):
    """Match the configured nested-vault setting used by the public rail."""
    previous = os.environ.get("CREATIVE_SECOND_BRAIN_VAULT")
    if item.vault_setting:
        os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = item.vault_setting
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("CREATIVE_SECOND_BRAIN_VAULT", None)
        else:
            os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = previous


class AttachSourceImageTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        self.base = base / ("attach-source-" + uuid.uuid4().hex[:12])
        self.base.mkdir()
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.item = self.completed_fixture("standard")

    def completed_fixture(self, name: str, *, nested_vault: bool = False) -> SimpleNamespace:
        root = self.base / name
        root.mkdir()
        (root / "tools").mkdir()
        shutil.copy2(ROOT / "tools" / "outcome_page_generator.py", root / "tools" / "outcome_page_generator.py")
        if nested_vault:
            (root / "brain.json").write_text(json.dumps({"vault": "notes/vault"}), encoding="utf-8")
        source_id = "KS-VS-8011" if not nested_vault else "KS-VS-8012"
        job_id = "ATTACH" if not nested_vault else "NESTED"
        source = root / "input" / "source.md"
        source.parent.mkdir(parents=True)
        source_text = "來源附圖只可支持同一已完成來源的可追溯知識點。"
        source.write_text(source_text, encoding="utf-8")
        rail.start(SimpleNamespace(
            root=root, url=None, image_file=None, text_file=source,
            source_id=source_id, job_id=job_id, title="合成來源附圖驗收",
            slug=job_id.lower(), profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
        ))
        _, package, kp_path = rail.paths(root, job_id)
        version = contract.CURRENT_SOURCE_CATALOG_VERSION
        plan = {
            "schema_version": contract.COMPACT_VERSION,
            "input_fingerprint": contract.input_fingerprint(package, version),
            "source_catalog_version": version,
            "summary": ["此合成來源只供驗證來源附圖的正式交易。"],
            "knowledge": [{
                "title": "來源附圖必須支持同一來源知識點",
                "claim": source_text,
                "layer": "原文主張",
                "confidence": "高",
                "evidence_block_ids": ["SRC-001"],
                "reusable_concept": {"status": "not_reusable", "reason": "合成 fixture 只驗證來源附圖。"},
            }],
            "application": {"knowledge_use": "學習材料"},
        }
        response = root / "input" / "semantic-plan.json"
        worker.write_json(response, plan)
        rail.submit_plan(root, job_id, response)
        vault = workspace_paths.managed_vault_path(root) if nested_vault else workspace_paths.vault_path(root)
        completed = rail.advance(root, job_id, vault)
        self.assertEqual(completed["status"], "completed", completed)
        return SimpleNamespace(
            root=root,
            vault=vault,
            source_id=source_id,
            job_id=job_id,
            package=package,
            kp_path=kp_path,
            completion=worker.read_json(package / "completion_receipt.json"),
            vault_setting="notes/vault" if nested_vault else None,
        )

    @staticmethod
    def image(item: SimpleNamespace, name: str, fmt: str = "PNG") -> Path:
        path = item.root / "input" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 24), (32, 96, 160)).save(path, fmt)
        return path

    @staticmethod
    def attachment_response(
        item: SimpleNamespace,
        image: Path,
        *,
        foreign_kp: bool = False,
        include_mapping: bool = True,
    ) -> Path:
        with fixture_environment(item):
            context = rail.source_attachment_context(item.root, item.source_id)
        _, inspection = rail.source_attachment_image(item.root, image)
        response = rail.attachment_response_template(item.source_id, inspection, context["knowledge_points"])
        response["image_analysis"].update({
            "analysis_status": "complete",
            "checker_status": "passed",
            "checker_evidence": "合成測試已核對完整圖片和可見標示。",
            "visual_type": "diagram",
            "title": "來源附圖",
            "summary": "這張附圖支持同一來源的一條既有知識點。",
            "anchors": [{"id": "IMG-001", "kind": "text", "evidence": "同一來源", "region": {"x": 0, "y": 0, "width": 1, "height": 1}}],
            "transcription": [{"anchor_id": "IMG-001", "text": "同一來源", "confidence": "high"}],
            "source_claims": [{"claim": "圖片可見同一來源標示。", "anchor_ids": ["IMG-001"], "verification_status": "image_visible"}],
            "coverage": [{"anchor_id": "IMG-001", "disposition": "knowledge_point", "reason": "對應已完成知識點。"}],
        })
        if include_mapping:
            target = "OTHER:KP-999" if foreign_kp else context["knowledge_points"][0]["kp_id"]
            response["kp_mapping"] = [{
                "anchor_id": "IMG-001", "kp_ids": [target], "disposition": "supports", "reason": "圖片直接支持該知識點。",
            }]
        else:
            response["kp_mapping"] = []
        path = item.root / "input" / (image.stem + "-attachment-response.json")
        worker.write_json(path, response)
        return path

    @staticmethod
    def attach(item: SimpleNamespace, image: Path, response: Path | None = None) -> dict[str, object]:
        with fixture_environment(item):
            return rail.attach_source_image(item.root, item.source_id, image, response)

    def test_work_order_preserves_vault_and_completed_order_routes_same_work_images(self) -> None:
        image = self.image(self.item, "attachment.png")
        vault_before = tree_hashes(self.item.vault)
        result = self.attach(self.item, image)
        self.assertEqual((result["status"], result["write_performed"]), ("needs_input", False))
        self.assertEqual(tree_hashes(self.item.vault), vault_before)
        order = rail.work_order(self.item.root, self.item.job_id)
        command = order["same_work_images"]["command"]
        self.assertIn("attach-source-image", command)
        self.assertEqual(command[command.index("--source-id") + 1], self.item.source_id)

    def test_valid_png_attachment_completes_with_readback_and_quality(self) -> None:
        image = self.image(self.item, "attachment.png")
        result = self.attach(self.item, image, self.attachment_response(self.item, image))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual((result["readback"], result["reader_quality"]), ("completed", "ready"))
        asset = self.item.root / result["asset_path"]
        self.assertTrue(asset.is_file())
        self.assertTrue(asset.name.startswith(result["image_sha256"][:12]))
        readback = worker.completed_receipt_readback(
            self.item.root, self.item.package, self.item.kp_path, worker.read_json(self.item.kp_path),
        )
        self.assertEqual(readback["source_id"], self.item.source_id)

    def test_duplicate_hash_is_already_attached_without_writing(self) -> None:
        image = self.image(self.item, "attachment.png")
        first = self.attach(self.item, image, self.attachment_response(self.item, image))
        self.assertEqual(first["status"], "completed", first)
        before = tree_hashes(self.item.root)
        repeated = self.attach(self.item, image, self.attachment_response(self.item, image))
        self.assertEqual((repeated["status"], repeated["write_performed"]), ("already_attached", False))
        self.assertEqual(tree_hashes(self.item.root), before)

    def test_foreign_kp_and_missing_anchor_mapping_are_zero_write_failures(self) -> None:
        image = self.image(self.item, "attachment.png")
        # Each response is written and submitted before the next one replaces the shared file name.
        for options, code in (({"foreign_kp": True}, "kp_mapping_foreign_kp"),
                              ({"include_mapping": False}, "kp_mapping_anchor_count")):
            with self.subTest(code=code):
                response = self.attachment_response(self.item, image, **options)
                before = tree_hashes(self.item.root)
                result = self.attach(self.item, image, response)
                self.assertEqual((result["status"], result["write_performed"]), ("failed", False))
                self.assertEqual([error["code"] for error in result["errors"]], [code])
                self.assertEqual(tree_hashes(self.item.root), before)

    def test_webp_attachment_completes(self) -> None:
        image = self.image(self.item, "attachment.webp", "WEBP")
        result = self.attach(self.item, image, self.attachment_response(self.item, image))
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["asset_path"].endswith(".webp"))
        self.assertEqual((result["readback"], result["reader_quality"]), ("completed", "ready"))

    def test_incomplete_image_is_rejected_before_any_write(self) -> None:
        image = self.item.root / "input" / "incomplete.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\ntruncated")
        before = tree_hashes(self.item.root)
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "image is unsupported|image .*incomplete"):
            self.attach(self.item, image)
        self.assertEqual(tree_hashes(self.item.root), before)

    def test_same_work_check_lists_recent_source_but_never_itself(self) -> None:
        offered = rail.same_work_check(self.item.root, "KS-VS-8999", "input/other.png")
        self.assertEqual([row["source_id"] for row in offered["recent_sources"]], [self.item.source_id])
        self.assertIn("--image-file input/other.png", offered["if_same_work"])
        self.assertEqual(rail.same_work_check(self.item.root, self.item.source_id, "input/other.png")["recent_sources"], [])

    def redirected_image_job(self) -> tuple[Path, Path, dict[str, object]]:
        image = self.image(self.item, "same-work.png")
        order = rail.start(SimpleNamespace(
            root=self.item.root, url=None, image_file=image, text_file=None,
            source_id="KS-VS-8999", job_id="REDIRECT", title="轉回來源附圖",
            slug="redirect", profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
        ))
        job_path = worker.job_file(self.item.root, order["job_id"])
        return image, job_path, worker.read_json(job_path)

    def test_attaching_instead_removes_matching_unfinished_image_job(self) -> None:
        image, job_path, job = self.redirected_image_job()
        package = self.item.root / job["package_dir"]
        accepted = self.item.root / job["accepted_input_path"]
        result = self.attach(self.item, image, self.attachment_response(self.item, image))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual([row["status"] for row in result["redirected_image_jobs"]], ["removed"])
        self.assertFalse(job_path.exists() or package.exists() or accepted.exists())

    def test_committing_matching_image_job_is_preserved(self) -> None:
        image, job_path, job = self.redirected_image_job()
        job["status"] = "committing"
        worker.write_json(job_path, job)
        result = self.attach(self.item, image, self.attachment_response(self.item, image))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["redirected_image_jobs"][0]["status"], "kept")
        self.assertIn("status committing", result["redirected_image_jobs"][0]["reasons"])
        self.assertTrue(job_path.exists())

    def test_nested_vault_attachment_renders_a_correct_reader_path(self) -> None:
        nested = self.completed_fixture("nested", nested_vault=True)
        image = self.image(nested, "nested.png")
        result = self.attach(nested, image, self.attachment_response(nested, image))
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual((result["readback"], result["reader_quality"]), ("completed", "ready"))
        page = nested.vault / "outcome-pages" / f"{nested.source_id}_outcome.html"
        expected_src = os.path.relpath(nested.root / result["asset_path"], page.parent).replace("\\", "/")
        html = page.read_text(encoding="utf-8")
        self.assertIn("<h2>附圖</h2>", html)
        self.assertIn(f'src="{expected_src}"', html)
        with fixture_environment(nested):
            readback = worker.completed_receipt_readback(
                nested.root, nested.package, nested.kp_path, worker.read_json(nested.kp_path),
            )
        self.assertEqual(readback["source_id"], nested.source_id)


if __name__ == "__main__":
    unittest.main()
