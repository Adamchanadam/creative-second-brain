"""Offline regression for page-scoped incomplete infographic recovery."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime/workspace").is_dir():
    ROOT = ROOT / "runtime/workspace"

import sys
sys.path.insert(0, str(ROOT))
from tools import compiler_worker_v2 as worker
from tools.infographic_prompt_contract import (
    INFOGRAPHIC_STANDALONE_PROMPT_MARKER,
    INFOGRAPHIC_VISIBLE_TEXT_MARKER,
    footer_format_equivalent,
    image_semantic_readback_errors,
    quality_only_reverse_review_failure_errors,
)


class PageScopedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR"))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        self.package.mkdir()
        canonical = "完整且固定的來源正文。"
        (self.package / "canonical_source.md").write_text(canonical, encoding="utf-8")
        self.source_identity = "user_provided_text:" + worker.sha256_text(canonical)
        self.pages = []
        ledger_pages = {}
        self.footer = "來源:Example Source　資訊圖:2026-10-12 (Creative Brain)"
        for number in (1, 2, 3):
            page_id = f"IG-{number:03d}"
            prompt = (
                f"{INFOGRAPHIC_STANDALONE_PROMPT_MARKER}\n"
                f"{INFOGRAPHIC_VISIBLE_TEXT_MARKER}\n"
                f"- Page {number}\n- Required step {number}\n- {number}/3\n- {self.footer}\n\n"
                "以上是圖面可見文字白名單"
            )
            prompt_path = self.package / "prompts" / f"ig-{number}.txt"
            prompt_path.parent.mkdir(exist_ok=True)
            prompt_path.write_text(prompt + "\n", encoding="utf-8")
            asset = self.package / "assets" / f"ig-{number}.png"
            asset.parent.mkdir(exist_ok=True)
            asset.write_bytes(b"\x89PNG\r\n\x1a\nfixture-" + bytes([number]))
            prompt_hash = worker.sha256_text(prompt)
            png_hash = worker.sha256_bytes(asset.read_bytes())
            page = {
                "page_id": page_id,
                "title": f"Page {number}",
                "source_anchors": [f"anchor {number}"],
                "locked_prompt_path": prompt_path.relative_to(self.package).as_posix(),
                "locked_prompt_sha256": prompt_hash,
                "asset_path": asset.relative_to(self.package).as_posix(),
                "compiled_plan_schema_version": "fixture-plan-v1",
                "compiled_plan_sha256": f"plan-{number}",
                "prompt_contract_version": "fixture-prompt-v1",
                "status": "receipt_verified" if number != 2 else "awaiting_imagegen",
            }
            self.pages.append(page)
            attempt = {
                "attempt_id": f"ATT-{number}-1",
                "attempt_number": 1,
                "attempt_kind": "initial_generation",
                "action_identity": f"action-{number}-1",
                "status": "accepted" if number != 2 else "blocking_failure",
                "failure_code": "" if number != 2 else "wrong_or_missing_locked_text",
                "png_sha256": png_hash,
            }
            attempts = [attempt]
            if number == 2:
                attempts.append({
                    "attempt_id": "ATT-2-2", "attempt_number": 2,
                    "attempt_kind": "blocking_repair", "action_identity": "action-2-2",
                    "status": "blocking_failure", "failure_code": "wrong_or_missing_locked_text",
                    "png_sha256": png_hash,
                })
            ledger_pages[page_id] = {
                "page_id": page_id, "locked_prompt_sha256": prompt_hash,
                "asset_path": asset.relative_to(self.package).as_posix(), "attempts": attempts,
            }
        self.kp_path = self.package / "knowledge_package.json"
        self.kp = {
            "job_id": "CW2-RECOVERY", "source": {
                "source_id": "KS-VS-RECOVERY", "source_input_kind": "user_provided_text",
                "source_identity": self.source_identity,
                "canonical_body_sha256": worker.sha256_text(canonical),
            }, "semantic_plan_version": "fixture-v1", "coverage_units": [], "infographics": self.pages,
        }
        worker.write_json(self.kp_path, self.kp)
        worker.write_json(worker.image_attempt_ledger_path(self.package), {
            "schema_version": worker.V2_IMAGEGEN_ATTEMPT_LEDGER_VERSION,
            "source_identity": self.source_identity,
            "generation_contract": worker.INFOGRAPHIC_GENERATION_CONTRACT,
            "pages": ledger_pages,
        })
        job = {
            "schema_version": worker.JOB_SCHEMA_VERSION, "job_id": "CW2-RECOVERY",
            "source_id": "KS-VS-RECOVERY", "source_input_kind": "user_provided_text",
            "source_identity": self.source_identity, "source_profile": "article", "status": "running",
            "first_accepted_at": "2026-10-12T00:00:00Z", "updated_at": "2026-10-12T00:00:00Z",
            "package_dir": self.package.relative_to(self.root).as_posix(),
        }
        scope = worker.ingest_plan_scope(job, self.kp)
        job["execution_contract"] = {
            "schema_version": worker.EXECUTION_CONTRACT_VERSION,
            "mode": "content_fast_path",
            **worker.execution_policy("article", 3),
            "scope": scope, "scope_sha256": worker.canonical_payload_sha256(scope),
            "first_accepted_at": job["first_accepted_at"], "plan_locked_at": job["first_accepted_at"],
        }
        worker.write_v2_job(self.root, job)

    def plan(self):
        scope = {
            "canonical_source_sha256": self.kp["source"]["canonical_body_sha256"],
            "page_ids": [page["page_id"] for page in self.pages], "page_count": 3,
            "shared_titles": [page["title"] for page in self.pages],
            "shared_footers": [self.footer, self.footer, self.footer],
        }
        return {
            "schema_version": worker.INFOGRAPHIC_REVISION_PLAN_VERSION,
            "revision_id": "recovery-r1", "source_id": "KS-VS-RECOVERY",
            "source_identity": self.source_identity,
            "recovery": {"failed_page_id": "IG-002", "reason": "exhausted_image_text_contract",
                         "locked_scope": scope},
        }

    def invoke(self, plan):
        path = self.root / "recovery-plan.json"
        worker.write_json(path, plan)
        return worker.command_prepare_infographic_revision(SimpleNamespace(
            root=self.root, kp_file=self.kp_path, plan_file=path,
            revision_id=None, recover_incomplete=True,
        ))

    def test_only_failed_page_reopens_and_siblings_stay_byte_stable(self):
        before_kp = worker.read_json(self.kp_path)
        before_ledger = worker.read_json(worker.image_attempt_ledger_path(self.package))
        before_job = worker.read_json(worker.job_file(self.root, "CW2-RECOVERY"))
        sibling_bytes = {
            number: ((self.package / "assets" / f"ig-{number}.png").read_bytes(),
                     (self.package / "prompts" / f"ig-{number}.txt").read_bytes())
            for number in (1, 3)
        }
        self.assertEqual(self.invoke(self.plan()), 0)
        after_kp = worker.read_json(self.kp_path)
        after_ledger = worker.read_json(worker.image_attempt_ledger_path(self.package))
        after_job = worker.read_json(worker.job_file(self.root, "CW2-RECOVERY"))
        for index in (0, 2):
            self.assertEqual(after_kp["infographics"][index], before_kp["infographics"][index])
        self.assertEqual(after_ledger["pages"]["IG-001"], before_ledger["pages"]["IG-001"])
        self.assertEqual(after_ledger["pages"]["IG-003"], before_ledger["pages"]["IG-003"])
        self.assertEqual(after_job["job_id"], before_job["job_id"])
        self.assertEqual(after_job["first_accepted_at"], before_job["first_accepted_at"])
        self.assertEqual(after_kp["infographics"][1]["status"], "planned")
        self.assertEqual(after_kp["infographics"][1]["source_anchors"], ["anchor 2"])
        for number, (png, prompt) in sibling_bytes.items():
            self.assertEqual((self.package / "assets" / f"ig-{number}.png").read_bytes(), png)
            self.assertEqual((self.package / "prompts" / f"ig-{number}.txt").read_bytes(), prompt)

    def test_scope_drift_is_rejected_without_writing(self):
        plan = self.plan()
        plan["recovery"]["locked_scope"]["page_ids"] = ["IG-001", "IG-003", "IG-002"]
        before_kp = self.kp_path.read_bytes()
        before_ledger = worker.image_attempt_ledger_path(self.package).read_bytes()
        before_job = worker.job_file(self.root, "CW2-RECOVERY").read_bytes()
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "page count or order differs"):
            self.invoke(plan)
        self.assertEqual(self.kp_path.read_bytes(), before_kp)
        self.assertEqual(worker.image_attempt_ledger_path(self.package).read_bytes(), before_ledger)
        self.assertEqual(worker.job_file(self.root, "CW2-RECOVERY").read_bytes(), before_job)

    def test_source_anchor_replacement_is_rejected_without_writing(self):
        plan = self.plan()
        plan["recovery"]["source_anchors"] = ["not permitted"]
        before_kp = self.kp_path.read_bytes()
        before_ledger = worker.image_attempt_ledger_path(self.package).read_bytes()
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "cannot change locked source_anchors"):
            self.invoke(plan)
        self.assertEqual(self.kp_path.read_bytes(), before_kp)
        self.assertEqual(worker.image_attempt_ledger_path(self.package).read_bytes(), before_ledger)

    def test_shared_source_metadata_drift_is_rejected_without_writing(self):
        drifted = worker.read_json(self.kp_path)
        drifted["source"]["source_label"] = "Drifted source label"
        worker.write_json(self.kp_path, drifted)
        before_kp = self.kp_path.read_bytes()
        before_ledger = worker.image_attempt_ledger_path(self.package).read_bytes()
        before_job = worker.job_file(self.root, "CW2-RECOVERY").read_bytes()
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "shared source metadata differs"):
            self.invoke(self.plan())
        self.assertEqual(self.kp_path.read_bytes(), before_kp)
        self.assertEqual(worker.image_attempt_ledger_path(self.package).read_bytes(), before_ledger)
        self.assertEqual(worker.job_file(self.root, "CW2-RECOVERY").read_bytes(), before_job)

    def test_legacy_scope_without_semantic_binding_is_rejected_without_writing(self):
        job = worker.read_json(worker.job_file(self.root, "CW2-RECOVERY"))
        job["execution_contract"]["scope"].pop("semantic_content_sha256")
        job["execution_contract"]["scope_sha256"] = worker.canonical_payload_sha256(job["execution_contract"]["scope"])
        worker.write_v2_job(self.root, job)
        drifted = worker.read_json(self.kp_path)
        drifted["source"]["source_label"] = "Legacy unbound drift"
        worker.write_json(self.kp_path, drifted)
        before_kp = self.kp_path.read_bytes()
        before_ledger = worker.image_attempt_ledger_path(self.package).read_bytes()
        before_job = worker.job_file(self.root, "CW2-RECOVERY").read_bytes()
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "lacks semantic source metadata binding"):
            self.invoke(self.plan())
        self.assertEqual(self.kp_path.read_bytes(), before_kp)
        self.assertEqual(worker.image_attempt_ledger_path(self.package).read_bytes(), before_ledger)
        self.assertEqual(worker.job_file(self.root, "CW2-RECOVERY").read_bytes(), before_job)

    def test_compile_keeps_siblings_and_uses_original_page_index(self):
        self.assertEqual(self.invoke(self.plan()), 0)
        before_kp = worker.read_json(self.kp_path)
        before_ledger = worker.read_json(worker.image_attempt_ledger_path(self.package))
        sibling_bytes = {
            number: ((self.package / "assets" / f"ig-{number}.png").read_bytes(),
                     (self.package / "prompts" / f"ig-{number}.txt").read_bytes())
            for number in (1, 3)
        }
        recovered_prompt = (
            f"{INFOGRAPHIC_STANDALONE_PROMPT_MARKER}\n{INFOGRAPHIC_VISIBLE_TEXT_MARKER}\n"
            f"- Page 2\n- Required step 2\n- 2/3\n- {self.footer}\n\n以上是圖面可見文字白名單"
        )
        compiled = {
            "schema_version": "fixture-plan-v1", "prompt_contract_version": "fixture-prompt-v1",
            "compiled_plan_sha256": "plan-2", "prompt": recovered_prompt,
            "asset_path": "assets/ig-2.png",
        }
        with patch.object(worker, "infographic_plan_preflight_issues", return_value=[]), \
             patch.object(worker, "validate_compiled_infographic_page", return_value=[]), \
             patch.object(worker, "validate_primary_work_metadata", return_value=[]), \
             patch.object(worker, "validate_coverage_units", return_value=[]), \
             patch.object(worker, "validate_coverage_audit", return_value=[]), \
             patch.object(worker, "validate_content_map", return_value=[]), \
             patch.object(worker, "validate_multi_page_orientation", return_value=[]), \
             patch.object(worker, "validate_reader_metadata", return_value=[]), \
             patch.object(worker, "validate_visual_structure", return_value=[]), \
             patch.object(worker, "compiled_infographic_page", return_value=compiled):
            self.assertEqual(worker.command_compile_infographics(SimpleNamespace(
                root=self.root, kp_file=self.kp_path, plan_repair_file=None,
            )), 0)
        after_kp = worker.read_json(self.kp_path)
        after_ledger = worker.read_json(worker.image_attempt_ledger_path(self.package))
        for index in (0, 2):
            self.assertEqual(after_kp["infographics"][index], before_kp["infographics"][index])
        self.assertEqual(after_ledger["pages"]["IG-001"], before_ledger["pages"]["IG-001"])
        self.assertEqual(after_ledger["pages"]["IG-003"], before_ledger["pages"]["IG-003"])
        self.assertEqual(after_kp["infographics"][1]["locked_prompt_path"], "prompts/ig-2.txt")
        self.assertEqual(after_kp["infographics"][1]["asset_path"], "assets/ig-2.png")
        self.assertIn("2/3", (self.package / "prompts" / "ig-2.txt").read_text(encoding="utf-8"))
        for number, (png, prompt) in sibling_bytes.items():
            self.assertEqual((self.package / "assets" / f"ig-{number}.png").read_bytes(), png)
            self.assertEqual((self.package / "prompts" / f"ig-{number}.txt").read_bytes(), prompt)

    def test_footer_provenance_is_mandatory_but_presentation_is_advisory(self):
        prompt = (
            f"{INFOGRAPHIC_VISIBLE_TEXT_MARKER}\n- Title\n- 可合法壓縮的正文描述\n"
            f"- 1/1\n- {self.footer}\n\n以上是圖面可見文字白名單"
        )
        png_hash = "a" * 64
        readback = {
            "inspection_method": "independent_image_readback", "inspector_identity": "offline-reviewer",
            "inspected_png_sha256": png_hash, "inspected_prompt_sha256": worker.sha256_text(prompt),
            "visible_text_readback": ["Title", "壓縮正文", "1/1", "來源：Example Source\n資訊圖：2026-10-12"],
            "unexpected_visible_text": [], "internal_process_text_detected": False,
            "semantic_alignment": {"status": "passed", "checked_prompt_visible_text_count": 4, "semantic_errors": []},
        }
        self.assertEqual(image_semantic_readback_errors(readback, png_sha256=png_hash, prompt=prompt), [])
        self.assertTrue(footer_format_equivalent(
            "來源:研究文章　資訊圖:2026-10-13 (創造型 2nd Brain)",
            "來源:研究\n文章　資訊圖:2026-10-13",
        ))
        self.assertTrue(footer_format_equivalent(
            "來源:研究文章　資訊圖:2026-10-13 (創造型 2nd Brain)",
            "來源:研究文章　資訊圖:2026-10-13 創造型 2nd Brain",
        ))
        self.assertFalse(footer_format_equivalent(
            "來源:研究文章　資訊圖:2026-10-13 (創造型 2nd Brain)",
            "來源:錯誤文章　資訊圖:2026-10-13",
        ))
        self.assertFalse(footer_format_equivalent(
            "來源:研究文章　資訊圖:2026-10-13 (創造型 2nd Brain)",
            "來源:研究文章　資訊圖:2026-10-14",
        ))
        historic_footer_brand_only = copy.deepcopy(readback)
        historic_footer_brand_only.update({
            "formal_ingestion_allowed": False,
            "reader_review": "Historic review rejected only its fixed brand presentation.",
            "review_axes": {
                "source_fidelity": "passed",
                "visible_text_line_review": "passed",
                "footer_signature": "failed",
            },
        })
        self.assertEqual(
            quality_only_reverse_review_failure_errors(
                historic_footer_brand_only, png_sha256=png_hash, prompt=prompt,
            ),
            [],
        )
        wrong_source = copy.deepcopy(readback)
        wrong_source["visible_text_readback"][-1] = "來源:Wrong Author　資訊圖:2026-10-12 (Creative Brain)"
        self.assertTrue(image_semantic_readback_errors(wrong_source, png_sha256=png_hash, prompt=prompt))
        missing_date = copy.deepcopy(readback)
        missing_date["visible_text_readback"][-1] = "來源:Example Source"
        self.assertTrue(image_semantic_readback_errors(missing_date, png_sha256=png_hash, prompt=prompt))

    def test_presentation_punctuation_and_negative_wording_stay_semantic_but_numbers_and_math_do_not(self):
        png_hash = "b" * 64

        def readback_for(prompt, visible):
            return {
                "inspection_method": "independent_image_readback", "inspector_identity": "offline-reviewer",
                "inspected_png_sha256": png_hash, "inspected_prompt_sha256": worker.sha256_text(prompt),
                "visible_text_readback": visible, "unexpected_visible_text": [], "internal_process_text_detected": False,
                "semantic_alignment": {"status": "passed", "checked_prompt_visible_text_count": len(visible), "semantic_errors": []},
            }

        def prompt_for(body):
            return (
                f"{INFOGRAPHIC_VISIBLE_TEXT_MARKER}\n- Title\n- {body}\n- 1/1\n"
                f"- {self.footer}\n\n以上是圖面可見文字白名單"
            )

        punctuation_prompt = prompt_for("步驟 3：核對來源。")
        punctuation_readback = readback_for(
            punctuation_prompt, ["Title", "步驟 3:核對來源。", "1/1", self.footer]
        )
        self.assertEqual(image_semantic_readback_errors(punctuation_readback, png_sha256=png_hash, prompt=punctuation_prompt), [])
        negative_prompt = prompt_for("不得刪除來源證據")
        negative_readback = readback_for(
            negative_prompt, ["Title", "不可以刪除來源證據", "1/1", self.footer]
        )
        self.assertEqual(image_semantic_readback_errors(negative_readback, png_sha256=png_hash, prompt=negative_prompt), [])
        for expected, observed in (
            ("比例 10%", "比例 11%"),
            ("上限 ≤ 3", "上限 < 3"),
        ):
            locked_prompt = prompt_for(expected)
            failed = readback_for(locked_prompt, ["Title", observed, "1/1", self.footer])
            self.assertTrue(image_semantic_readback_errors(failed, png_sha256=png_hash, prompt=locked_prompt), (expected, observed))


if __name__ == "__main__":
    unittest.main()
