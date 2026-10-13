"""Isolated contract counterexamples; no live provider or production Vault writes."""
from __future__ import annotations
import copy
import argparse
import json
import sys
import uuid
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image

SOURCE_ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime-root", type=Path, default=SOURCE_ROOT / "runtime" / "workspace")
parser.add_argument("--work-root", type=Path, required=True)
options, unittest_args = parser.parse_known_args()
ROOT = options.runtime_root.resolve()
CACHE = options.work_root.resolve()
CACHE.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(ROOT))
from tools import ingest_rail as rail, compiler_worker_v2 as worker
from tools.ingest_plan_contract import VERSION, build_kp, input_fingerprint, plan_schema, schema_errors
from tools.obsidian_vault_adapter import full_body_packet_issues, rewrite_infographic_embeds, write_plan, AdapterStop, source_note_without_canonical_section
from tools.case_evidence_contract import sha256_file
from tools.infographic_prompt_contract import reader_page_sha256
from tools import obsidian_vault_adapter as adapter

BODY = "固定流程可保留完整原文與來源證據，並減少重複操作及核對所需時間。"


def semantic_plan(package):
    plan = {
        "schema_version": VERSION, "input_fingerprint": input_fingerprint(package),
        "source_metadata": {"source_label": "使用者提供文字", "source_type": "使用者提供文字", "primary_work_status": "none"},
        "analysis": {"summary": ["本文說明固定流程如何保存全文、保留來源證據及減少重複核對。"], "source_claims": [{"id": "C-1", "claim": BODY, "evidence_anchor": BODY, "confidence": "高"}]},
        "coverage_units": [{"id": "U-1", "text": BODY, "source_evidence": BODY, "unit_types": ["structure_unit"], "dispositions": ["source_claim", "knowledge_point", "infographic"], "infographic_text": [BODY]}],
        "knowledge": [{"kp_id": "KP-VS-0001-001", "title": "完整原文保留", "claim": BODY, "layer": "原文主張", "confidence": "高", "evidence_anchor": BODY}],
        "relations": [],
        "application": {"knowledge_use": "事實真源", "adoption_status": "收藏", "adoption_reason": "保存這項方法供日後參考。", "personalization_links": [], "context_anchors": [], "compounding_insight_status": "needs_context", "compounding_insight_types": [], "compounding_next_action": "有實際案例時再比較適用性。", "taste_network_status": "否", "taste_link_status": "not_applicable", "taste_reason": "來源没有作品品味判斷。"},
        "infographics": [{"page_id": "IG-001", "page_focus": BODY, "page_focus_evidence": BODY, "layout_archetype": "重點總覽", "content_use": "事實真源", "graph_type": "原文重點卡", "visual_structure": {"kind": "fact_cards", "items": [{"heading_text": BODY, "body_text": BODY}]}, "coverage_ids": ["U-1"], "reader_question": "固定流程有甚麼用途？", "reader_takeaway": BODY, "cross_page_handoff": "本頁已完整呈現這项方法。"}],
    }
    plan["application"]["compounding_judgments"] = {kind: {"decision": "需要更多脈絡", "reason": "尚未有足夠既有筆記作比較。"} for kind in ("補底", "加深", "連接", "修正", "行動")}
    bundle = worker.read_json(package / "input_bundle.json")
    page = {**plan["infographics"][0], "title": bundle["title"], "visible_text": [BODY]}
    plan["infographics"][0]["reader_review"] = {
        "page_sha256": reader_page_sha256(page), "method": "isolated_page_readback",
        "reviewer_id": "OFFLINE-TEST-ONLY", "verdict": "passed",
        "answer": "Synthetic contract fixture only, not live reader evidence.",
        "relationship_explanation": "Synthetic contract relationship only.",
        "unexplained_terms": [], "missing_context": [], "misleading_relations": [],
    }
    return plan


class RailTests(unittest.TestCase):
    def setUp(self):
        cache = CACHE
        # Keep diagnostic fixtures; normal inherited ACLs work in Windows
        # sandboxes where tempfile's private-directory ACL cannot be reopened.
        self.root = cache / ("rail-" + uuid.uuid4().hex[:12])
        self.root.mkdir()
        source = self.root / "source.md"
        source.write_text(BODY, encoding="utf-8")
        self.order = rail.start(SimpleNamespace(root=self.root, url=None, image_file=None, text_file=source, source_id="KS-VS-0001", job_id="RAIL-TEST", title="固定流程", slug="fixture", profile="article"))
        _, self.package, self.kp_path = rail.paths(self.root, "RAIL-TEST")

    def test_schema_and_materialization(self):
        plan = semantic_plan(self.package)
        self.assertEqual(schema_errors(plan, plan_schema()), [])
        kp = build_kp(plan, self.package, root=self.root)
        self.assertEqual(kp["source"]["retention_mode"], "full_body_required")
        self.assertEqual(kp["infographics"][0]["visible_text"], [BODY])
        self.assertEqual(worker.infographic_plan_preflight_issues(kp, self.package, phase="plan"), [])

    def test_semantic_input_is_agent_continuation_not_human_wait(self):
        self.assertEqual(self.order["action"], "prepare_semantic_plan")
        self.assertEqual(self.order["continuation"]["owner"], "agent")
        self.assertEqual(self.order["continuation"]["directive"], "continue_same_job")
        self.assertFalse(self.order["continuation"]["task_complete"])

    def test_continuation_does_not_upgrade_blocked_unknown_or_unverified_completion(self):
        for order in ({"status": "blocked", "action": "inspect_failed_attempt"},
                      {"status": "needs_input", "action": "unknown"},
                      {"status": "completed", "action": "stop"}):
            result = rail.continuation_order(order)["continuation"]
            self.assertEqual(result["directive"], "report_blocker")
            self.assertFalse(result["task_complete"])
        result = rail.continuation_order({"status": "completed", "action": "stop", "completion": {"readback": "verified"}})
        self.assertTrue(result["continuation"]["task_complete"])

    def test_compiler_to_writer_preserves_schema_defined_coverage_ids(self):
        kp = build_kp(semantic_plan(self.package), self.package, root=self.root)
        ids = ["U-01", "內容|甲、乙", "custom-coverage"]
        kp["infographics"][0]["coverage_ids"] = ids
        rendered = worker.render_source_slice(kp, self.package)
        block = adapter.extract_infographic_blocks(rendered)[0]
        self.assertEqual(adapter.coverage_ids_from_profile(adapter.infographic_profile(block)), set(ids))

    def test_coverage_json_is_strict_and_legacy_notes_remain_supported(self):
        for bad in ('', 'invalid', '{}', '[]', '[1]', '["U-01", "U-01"]', '[""]'):
            self.assertEqual(adapter.coverage_ids_from_profile({"coverage_ids_json": bad, "coverage": "K01"}), set())
            block = f'- coverage_ids_json:{bad}\n- 重要性覆蓋:K01\n'
            self.assertEqual(adapter.coverage_ids_from_profile(adapter.infographic_profile(block)), set())
        self.assertEqual(adapter.coverage_ids_from_profile({"coverage": "K01、CU-EX-02"}), {"K01", "CU-EX-02"})

    def test_distinct_coverage_does_not_excuse_duplicate_visible_text(self):
        blocks = [f'- 標題:主題{i}\n- 本圖分工:第{i}頁有不同的具體閱讀目的與對象。\n- coverage_ids_json:["U-0{i}"]\n- 重要性覆蓋:第{i}頁的獨立內容\n- 圖內文字:\n  1. 這是跨頁完全重複而不應保留的正文句子。\n' for i in (1, 2)]
        issues = adapter.infographic_duplicate_issues_from_blocks(blocks)
        self.assertIn("duplicate_visible_text", {issue["issue"] for issue in issues})

    def test_unknown_state_fields_fail(self):
        plan = semantic_plan(self.package)
        plan["source_metadata"]["source_identity"] = "invented"
        plan["infographics"][0]["status"] = "receipt_verified"
        self.assertEqual(len(schema_errors(plan, plan_schema())), 2)

    def test_path_traversal_rejected_at_contract_and_writer(self):
        plan = semantic_plan(self.package)
        plan["knowledge"][0]["kp_id"] = "KP-x/../../../../../../escaped"
        with self.assertRaisesRegex(ValueError, "safe KP- identifier"):
            build_kp(plan, self.package, root=self.root)
        with self.assertRaisesRegex(AdapterStop, "escapes"):
            write_plan({"registry_issue": None, "root": self.root, "vault": self.root / "vault",
                        "source_note": self.root / "escaped.md"})
        self.assertFalse((self.root / "escaped.md").exists())

    def test_low_level_preflight_cannot_bypass_grounding(self):
        kp = build_kp(semantic_plan(self.package), self.package, root=self.root)
        kp["knowledge"][0]["evidence_anchor"] = "invented quote"
        kp["analysis"]["source_claims"][0]["evidence_anchor"] = "also invented"
        issues = worker.infographic_plan_preflight_issues(kp, self.package, phase="plan")
        self.assertGreaterEqual(sum("quote not in complete source" in i["message"] for i in issues), 2)

    def test_failed_capture_reuses_original_job(self):
        args = SimpleNamespace(root=self.root, url="https://example.com/article", image_file=None, text_file=None,
                               source_id=None, job_id=None, title=None, slug="url", profile="article")
        with patch.object(rail, "capture", side_effect=worker.CompilerWorkerV2Error("offline fixture")):
            with self.assertRaises(worker.CompilerWorkerV2Error):
                rail.start(args)
        identity = "url:" + worker.sha256_text(worker.normalize_url(args.url))
        owner = worker.latest_v2_job_for_source(self.root, identity)
        self.assertEqual(rail.work_order(self.root, owner["job_id"])["action"], "retry_capture")
        with patch.object(rail, "capture") as capture:
            again = rail.start(args)
        self.assertEqual(again["job_id"], owner["job_id"])
        self.assertEqual(capture.call_args.kwargs["job"]["source_id"], owner["source_id"])

    def test_stale_source_and_invented_evidence_fail(self):
        plan = semantic_plan(self.package)
        plan["knowledge"][0]["evidence_anchor"] = "原文沒有這一句"
        with self.assertRaisesRegex(ValueError, "quote not in complete source"):
            build_kp(plan, self.package, root=self.root)
        (self.package / "canonical_source.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "accepted source changed"):
            build_kp(plan, self.package, root=self.root)

    def test_compile_authorize_and_resume_no_duplicate(self):
        kp = build_kp(semantic_plan(self.package), self.package, root=self.root)
        worker.write_json(self.kp_path, kp)
        first = rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault"))
        self.assertEqual(first["action"], "generate_image_once")
        self.assertEqual(first["continuation"]["directive"], "continue_same_job")
        ledger = worker.image_attempt_ledger_path(self.package).read_bytes()
        again = rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault"))
        self.assertEqual(again["action"], "reconcile_image_result")
        self.assertEqual(again["continuation"]["directive"], "continue_same_job")
        self.assertFalse(again["continuation"]["task_complete"])
        self.assertEqual(ledger, worker.image_attempt_ledger_path(self.package).read_bytes())
        self.assertEqual(again["response_template"]["runtime_tool"], "")
        self.assertEqual(again["response_template"]["page_number_readback"], "")
        self.assertEqual(again["response_template"]["footer_text_readback"], "")
        self.assertIsNone(again["response_template"]["semantic_readback"]["internal_process_text_detected"])
        kp = worker.read_json(self.kp_path)
        kp["knowledge"][0]["claim"] = "changed after lock"
        worker.write_json(self.kp_path, kp)
        with self.assertRaises(worker.CompilerWorkerV2Error):
            rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault"))

    def test_full_body_not_self_declared_hash(self):
        data = SimpleNamespace(source_input_kind="user_provided_text", canonical_body_sha256="fake", canonical_body_markdown="摘要")
        packet = {"source_retention_mode": "full_body_required", "canonical_body_required": True, "canonical_body_sha256": "fake", "canonical_body_representative_anchors": ["摘要"]}
        worker.write_json(self.package / "source_capture_metadata.json", packet)
        worker.write_json(self.package / "review_packet.json", packet)
        issues = full_body_packet_issues(self.package / "source_capture_metadata.json", self.package / "review_packet.json", data)
        self.assertIn("full_body_capture_text_mismatch", [i["code"] for i in issues])

    def test_fenced_original_images_unchanged(self):
        data = SimpleNamespace(source_id="KS-VS-0001", asset_paths=[Path("old.png")])
        original = "`````markdown\n![original](old.png)\n```example\n![again](old.png)\n`````\n"
        with patch("tools.obsidian_vault_adapter.vault_relative", return_value="assets/new.png"):
            result = rewrite_infographic_embeds(original + "![projection](old.png)\n", data, [Path("new.png")])
        self.assertTrue(result.startswith(original))
        self.assertTrue(result.endswith("![[assets/new.png]]\n"))
        note = "## 正文保存區\n\n### Canonical article body\n\n`````markdown\n![[assets/example.png]]\n`````\n\n## 資訊圖\n![[assets/new.png]]\n"
        self.assertEqual(source_note_without_canonical_section(note).count("![[assets/"), 1)

    def test_rebind_cannot_manufacture_completion(self):
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "retired"):
            worker.command_rebind_existing_completion(SimpleNamespace())

    def test_rebind_recovery_archives_only_frozen_partial_artifacts(self):
        vault = self.root / "knowledge-base-vault"
        old_id, new_id = "KS-VS-0001", "KS-VS-0002"
        moved = [
            (f"sources/2026/10/{old_id}_partial.md", f"sources/2026/10/{new_id}_partial.md", "source"),
            (f"applications/2026/10/APP-{old_id}_partial.md", f"applications/2026/10/APP-{new_id}_partial.md", "application"),
            (f"knowledge-points/by-source/{old_id}/KP-VS-0001-001.md", f"knowledge-points/by-source/{new_id}/KP-VS-0002-001.md", "knowledge"),
            (f"assets/{old_id.lower()}/case-evidence", f"assets/{new_id.lower()}/case-evidence", "asset"),
        ]
        artifact_hashes = []
        for old_relative, new_relative, kind in moved:
            active = vault / new_relative
            if kind == "asset":
                active.mkdir(parents=True)
                active = active / "manifest.json"
                old_relative = old_relative + "/manifest.json"
            else:
                active.parent.mkdir(parents=True, exist_ok=True)
            active.write_text(f"frozen partial {kind}", encoding="utf-8")
            artifact_hashes.append({
                "path": (vault / old_relative).relative_to(self.root).as_posix(),
                "sha256": worker.sha256_bytes(active.read_bytes()),
            })
        job = worker.read_json(worker.job_file(self.root, "RAIL-TEST"))
        bundle = worker.read_json(self.package / "input_bundle.json")
        job["source_id"] = new_id
        bundle["source_id"] = new_id
        worker.write_json(self.package / "input_bundle.json", bundle)
        job["source_id_rebind_checkpoint"] = {
            "schema_version": "source_id_rebind_checkpoint_v1",
            "state": "vault_rebound",
            "old_source_id": old_id,
            "new_source_id": new_id,
            "source_identity": job["source_identity"],
            "artifact_hashes": artifact_hashes,
            "writer_rollback": {
                "moved": [
                    {"from": old_relative, "to": new_relative}
                    for old_relative, new_relative, _ in moved
                ]
            },
        }
        worker.write_v2_job(self.root, job)

        preview = rail.archive_rebound_partial_artifacts(self.root, "RAIL-TEST", vault, apply=False)
        self.assertEqual(preview["status"], "would_archive")
        self.assertEqual(len(preview["artifacts"]), 4)
        self.assertTrue((vault / moved[0][1]).exists())

        archived = rail.archive_rebound_partial_artifacts(self.root, "RAIL-TEST", vault, apply=True)
        self.assertEqual(archived["status"], "archived")
        self.assertTrue((self.package / "rebind-recovery" / "ks-vs-0001-to-ks-vs-0002" / "manifest.json").is_file())
        self.assertFalse((vault / moved[0][1]).exists())
        self.assertTrue((self.package / "rebind-recovery" / "ks-vs-0001-to-ks-vs-0002" / "vault" / moved[0][1]).is_file())
        self.assertEqual(
            rail.archive_rebound_partial_artifacts(self.root, "RAIL-TEST", vault, apply=False)["status"],
            "already_archived",
        )

    def test_image_branch_keeps_original_and_requires_review(self):
        image = self.root / "original.png"
        Image.new("RGB", (32, 48), "white").save(image)
        original = image.read_bytes()
        order = rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id=None, job_id="RAIL-IMAGE", title="原圖標題", slug="image", profile="article"))
        self.assertEqual(order["action"], "review_original_image")
        self.assertEqual((self.root / order["original_image"]).read_bytes(), original)
        self.assertEqual(order["response_template"]["checker_status"], "pending")
        _, package, kp = rail.paths(self.root, "RAIL-IMAGE")
        self.assertFalse(kp.exists())
        self.assertEqual(worker.read_json(package / "input_bundle.json")["requested_title"], "原圖標題")
        again = rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id=None, job_id=None, title=None, slug=None, profile="article"))
        self.assertEqual(again["job_id"], "RAIL-IMAGE")
        response = order["response_template"]
        response_path = self.root / "image-analysis.json"
        worker.write_json(response_path, response)
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "image analysis contract failed"):
            rail.invoke(worker.command_accept_image, root=self.root, source_id=order["source_id"], job_id="RAIL-IMAGE", image_file=image, image_analysis_file=response_path, title=None, slug="image", profile="article")
        response.update(analysis_status="complete", checker_status="passed", checker_evidence="Fixture only: complete region reviewed.",
                        visual_type="document_scan", title="原圖標題", summary=BODY)
        response["anchors"][0].update(kind="text", evidence=BODY)
        response["transcription"][0].update(text=BODY, confidence="high")
        response["source_claims"][0].update(claim=BODY, verification_status="image_visible")
        response["coverage"][0].update(disposition="knowledge_point", reason="完整保留原圖及可辨文字。")
        worker.write_json(response_path, response)
        rail.invoke(worker.command_accept_image, root=self.root, source_id=order["source_id"], job_id="RAIL-IMAGE", image_file=image, image_analysis_file=response_path, title="原圖標題", slug="image", profile="article")
        plan = semantic_plan(package)
        plan["source_metadata"]["source_type"] = "使用者提供圖片"
        plan["knowledge"][0]["evidence_anchor"] = "IMG-001"
        image_kp = build_kp(plan, package, root=self.root)
        image_kp["knowledge"][0]["evidence_anchor"] = "IMG-999"
        issues = worker.infographic_plan_preflight_issues(image_kp, package, phase="plan")
        self.assertTrue(any("existing IMG-*" in item["message"] for item in issues))


    def test_distinct_pages_with_shared_boilerplate_are_not_false_duplicates(self):
        common = "本頁資料皆由相同來源保存，以明確證據說明方法和限制，不將作者主張當成普遍成立的事實。" * 4
        blocks = [
            f'- 標題:主題{i}\n- 本圖分工:第{i}頁有不同的具體閱讀目的與對象。\n'
            f'- coverage_ids_json:["U-0{i}"]\n- 重要性覆蓋:本頁涵蓋U-0{i}\n'
            f'- 承接邏輯:{common}\n- 圖內文字:\n  1. {text}\n'
            for i, text in enumerate(("實驗比較行為是否相同，但不證明程式碼相同。", "創作需要考慮讀者感受，工具成功不等於作品動人。"), 1)
        ]
        self.assertEqual(adapter.infographic_duplicate_issues_from_blocks(blocks), [])
        legacy = [block.replace(f'- coverage_ids_json:["U-0{i}"]\n', "") for i, block in enumerate(blocks, 1)]
        self.assertIn("semantic_profile_overlap", {item["issue"] for item in adapter.infographic_duplicate_issues_from_blocks(legacy)})
        overlap = [blocks[0], blocks[1].replace('["U-02"]', '["U-01"]')]
        self.assertIn("semantic_profile_overlap", {item["issue"] for item in adapter.infographic_duplicate_issues_from_blocks(overlap)})

    def test_completed_order_requires_live_readback_not_receipt_presence(self):
        kp = build_kp(semantic_plan(self.package), self.package, root=self.root)
        worker.write_json(self.kp_path, kp)
        worker.write_json(self.package / "completion_receipt.json", {"fixture_only": True})
        with patch.object(worker, "command_project", side_effect=worker.CompilerWorkerV2Error("readback failed")) as readback:
            with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "readback failed"):
                rail.work_order(self.root, "RAIL-TEST")
            self.assertFalse(readback.call_args.args[0].write_vault)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *unittest_args])
