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
from tools.ingest_plan_contract import COMPACT_VERSION, CURRENT_SOURCE_CATALOG_VERSION, VERSION, build_kp, compact_plan_schema, input_fingerprint, plan_schema, schema_errors
from tools.obsidian_vault_adapter import full_body_packet_issues, rewrite_infographic_embeds, write_plan, AdapterStop, source_note_without_canonical_section
from tools.case_evidence_contract import sha256_file
from tools.infographic_prompt_contract import (
    infographic_spec_read_receipt,
    load_infographic_reverse_review_contract,
    reader_page_sha256,
    visible_text_from_prompt,
)
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


def compact_semantic_plan(package, block_ids=None, *, current=False):
    if block_ids is None:
        block_ids = ["SRC-001"]
    elif isinstance(block_ids, str):
        block_ids = [block_ids]
    plan = {
        "schema_version": COMPACT_VERSION,
        "input_fingerprint": input_fingerprint(package),
        "summary": ["本文說明固定流程如何保存完整原文、來源證據及必要知識分析。"],
        "application": {"knowledge_use": "學習材料"},
        "knowledge": [{
            "title": "完整原文保留",
            "claim": BODY,
            "layer": "原文主張",
            "confidence": "高",
            "evidence_block_ids": block_ids,
        }],
    }
    if current:
        # The current work-order contract: versioned catalog binding plus a same-round concept decision.
        plan["source_catalog_version"] = CURRENT_SOURCE_CATALOG_VERSION
        plan["input_fingerprint"] = input_fingerprint(package, CURRENT_SOURCE_CATALOG_VERSION)
        plan["knowledge"][0]["reusable_concept"] = {"status": "not_reusable", "reason": "固定流程說明只屬單一情境，沒有可重用的抽象概念。"}
    return plan


class RailTests(unittest.TestCase):
    def setUp(self):
        cache = CACHE
        # Keep diagnostic fixtures; normal inherited ACLs work in Windows
        # sandboxes where tempfile's private-directory ACL cannot be reopened.
        self.root = cache / ("rail-" + uuid.uuid4().hex[:12])
        self.root.mkdir()
        source = self.root / "source.md"
        source.write_text(BODY + "\n文字優先保存完整來源。", encoding="utf-8")
        self.order = rail.start(SimpleNamespace(root=self.root, url=None, image_file=None, text_file=source, source_id="KS-VS-0001", job_id="RAIL-TEST", title="固定流程", slug="fixture", profile="article", asset_mode=worker.ASSET_MODE_FORMAL))
        _, self.package, self.kp_path = rail.paths(self.root, "RAIL-TEST")

    def _envelope(self, order):
        """The rail hands out a short pointer; the full image action lives in its envelope file."""
        return worker.read_json(self.root / order["action_envelope"])

    def _work_order_file(self, order):
        """The rail hands out a short pointer; the full work order lives in its file."""
        return worker.read_json(self.root / order["work_order_file"])

    def _reading_blocks(self, order):
        """(block id, text) rows of the reading files named by a work-order pointer."""
        return [tuple(line.split("\t", 1)) for name in order["reading_files"]
                for line in (self.root / name).read_text(encoding="utf-8").splitlines()]

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

    def test_new_start_defaults_to_text_first(self):
        source = self.root / "text-first.md"
        source.write_text(BODY, encoding="utf-8")
        order = rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-TEXT-FIRST", job_id="RAIL-TEXT-FIRST",
            title="文字優先知識保存", slug="text-first", profile="article",
        ))
        bundle = worker.read_json(rail.paths(self.root, "RAIL-TEXT-FIRST")[1] / "input_bundle.json")
        self.assertEqual(bundle["asset_mode"], worker.ASSET_MODE_KNOWLEDGE)
        self.assertNotIn("docs/資訊圖生成規格.md", self._work_order_file(order)["required_reads"])
        plan = semantic_plan(rail.paths(self.root, "RAIL-TEXT-FIRST")[1])
        plan["asset_mode"] = worker.ASSET_MODE_KNOWLEDGE
        plan["infographics"] = []
        audit = worker.build_coverage_audit(
            build_kp(plan, rail.paths(self.root, "RAIL-TEXT-FIRST")[1], root=self.root)
        )
        self.assertEqual(audit["status"], "complete")
        self.assertEqual(audit["multi_volume_status"], "not_requested")
        self.assertEqual(audit["pending_infographic_mappings"], [])

    def test_normal_text_first_uses_compact_block_bound_plan_without_images(self):
        source = self.root / "compact.md"
        last_paragraph = "最後一段證據明確要求在保存全文後，仍保留可追溯的來源定位。"
        source.write_text(
            BODY + "\n\n中間段落只用來證明不相鄰引用不可被合成一段連續原文。\n\n" + last_paragraph,
            encoding="utf-8",
        )
        order = rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-COMPACT", job_id="RAIL-COMPACT",
            title="快速文字保存", slug="compact", profile="article",
        ))
        _, package, _ = rail.paths(self.root, "RAIL-COMPACT")
        work = self._work_order_file(order)
        self.assertEqual(work["response_schema"]["properties"]["schema_version"]["enum"], [COMPACT_VERSION])
        self.assertEqual(work["required_reads"], [])
        self.assertEqual(work["source_catalog_version"], CURRENT_SOURCE_CATALOG_VERSION)
        self.assertNotIn("source_blocks", work)
        reading = self._reading_blocks(order)
        self.assertEqual(len(reading), 3)
        self.assertTrue(all(block_id.startswith("SPAN-") for block_id, _ in reading))
        self.assertIn(BODY, reading[0][1])
        # Older SRC-* citations still resolve for a plan that does not carry a catalog version.
        plan = compact_semantic_plan(package, ["SRC-001", "SRC-003"])
        plan["source_metadata"] = {
            "source_label": "快速文字保存",
            "source_type": "使用者提供文字",
            "author": "測試作者",
            "primary_work_status": "none",
        }
        plan["application"] = {
            "knowledge_use": "工作流程",
            "adoption_status": "試行",
            "adoption_reason": "來源本身描述保存及來源定位流程。",
            "personalization_links": [],
            "context_anchors": [],
            "compounding_insight_status": "needs_context",
            "compounding_insight_types": [],
            "compounding_next_action": "待有另一份可比較的來源再判斷是否形成跨來源洞見。",
            "compounding_judgments": {
                kind: {"decision": "需要更多脈絡", "reason": "此 fixture 沒有第二來源可比對。"}
                for kind in ("補底", "加深", "連接", "修正", "行動")
            },
            "taste_network_status": "否",
            "taste_link_status": "not_applicable",
            "taste_reason": "來源是工作流程材料，沒有作品品味判斷。",
        }
        self.assertEqual(schema_errors(plan, compact_plan_schema()), [])
        kp = build_kp(plan, package, root=self.root)
        self.assertEqual(kp["semantic_plan_version"], COMPACT_VERSION)
        self.assertEqual(kp["source"]["asset_mode"], worker.ASSET_MODE_KNOWLEDGE)
        self.assertEqual(kp["infographics"], [])
        self.assertEqual(kp["coverage_units"][0]["source_block_ids"], ["SRC-001", "SRC-003"])
        self.assertIn("【來源片段】", kp["knowledge"][0]["evidence_anchor"])
        self.assertEqual(worker.validate_kp(kp, package), [])
        self.assertEqual(kp["source"]["source_type"], "使用者提供文字")
        self.assertEqual(kp["application"]["knowledge_use"], "工作流程")
        self.assertEqual(kp["application"]["adoption_status"], "試行")
        self.assertEqual(kp["application"]["compounding_insight_status"], "needs_context")
        self.assertEqual(kp["application"]["taste_link_status"], "not_applicable")
        self.assertEqual(worker.build_coverage_audit(kp)["multi_volume_status"], "not_requested")
        bad = compact_semantic_plan(package, ["SRC-999"])
        with self.assertRaisesRegex(ValueError, "unknown accepted source block"):
            build_kp(bad, package, root=self.root)

    def test_compact_plan_requires_only_structured_core_source_blocks(self):
        source = self.root / "compact-core.md"
        source.write_text(
            "本文保留完整來源，但不把所有保存段落冒充為已分析。\n\n"
            "第一步：先備份資料並確認備份可恢復。\n\n"
            "第二步：完成備份驗證才可修改正式資料。",
            encoding="utf-8",
        )
        rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-COMPACT-CORE", job_id="RAIL-COMPACT-CORE",
            title="核心步驟", slug="compact-core", profile="article",
        ))
        _, package, _ = rail.paths(self.root, "RAIL-COMPACT-CORE")
        incomplete = build_kp(
            compact_semantic_plan(package, ["SRC-001"]), package, root=self.root,
        )
        errors = worker.validate_kp(incomplete, package)
        self.assertIn(
            "compact semantic plan omits required core source block(s): SRC-002",
            errors,
        )
        self.assertIn(
            "compact semantic plan omits required core source block(s): SRC-003",
            errors,
        )
        complete = build_kp(
            compact_semantic_plan(package, ["SRC-001", "SRC-002", "SRC-003"]),
            package,
            root=self.root,
        )
        self.assertEqual(worker.validate_kp(complete, package), [])

    def test_compact_submit_keeps_single_writer_and_records_normal_timing(self):
        source = self.root / "compact-submit.md"
        source.write_text(BODY + "\n\n文字優先工作單不要求製作圖片。", encoding="utf-8")
        rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-COMPACT-SUBMIT", job_id="RAIL-COMPACT-SUBMIT",
            title="快速文字提交", slug="compact-submit", profile="article",
        ))
        _, package, _ = rail.paths(self.root, "RAIL-COMPACT-SUBMIT")
        response = self.root / "compact-submit-response.json"
        worker.write_json(response, compact_semantic_plan(package, current=True))
        rail.measured(
            self.root, "RAIL-COMPACT-SUBMIT", "submit_plan", rail.submit_plan,
            root=self.root, job_id="RAIL-COMPACT-SUBMIT", response_path=response,
        )
        job = worker.read_json(worker.job_file(self.root, "RAIL-COMPACT-SUBMIT"))
        contract = job["execution_contract"]
        self.assertEqual(contract["execution_profile"], "normal_knowledge_only_v1")
        self.assertEqual(contract["target_seconds"], 180)
        self.assertEqual(contract["imagegen"], "prohibited")
        self.assertEqual(job["rail_events"][-1]["stage"], "submit_plan")
        self.assertEqual(job["rail_events"][-1]["status"], "completed")
        preview = worker.ingest_execution_summary(job, completed_at=worker.iso_now())
        self.assertEqual(preview["stage_wall_seconds"]["image_generation_and_readback"], "not_applicable")
        self.assertIn(preview["target_status"], {"within_target", "over_target"})

        result = rail.advance(self.root, "RAIL-COMPACT-SUBMIT", self.root / "vault")
        self.assertEqual(result["action"], "stop")
        completed = worker.read_json(worker.job_file(self.root, "RAIL-COMPACT-SUBMIT"))
        adapter_record = completed["commit_adapter"]
        self.assertTrue((self.root / adapter_record["source_note"]).is_file())
        self.assertTrue((self.root / adapter_record["application_note"]).is_file())
        self.assertTrue(all((self.root / path).is_file() for path in adapter_record["knowledge_points"]))
        application = (self.root / adapter_record["application_note"]).read_text(encoding="utf-8")
        self.assertIn('png_entry: ""', application)
        self.assertIn('next_action: "再讀原文或知識點"', application)
        self.assertIn('compounding_insight_status: "needs_context"', application)
        self.assertIn('taste_link_status: "not_applicable"', application)
        receipt = worker.read_json(package / "completion_receipt.json")
        self.assertEqual(receipt["execution"]["target_seconds"], 180)
        self.assertEqual(receipt["execution"]["stage_wall_seconds"]["image_generation_and_readback"], "not_applicable")

    def test_image_default_preserves_original_without_requesting_infographic(self):
        image = self.root / "original-default.png"
        Image.new("RGB", (32, 48), "white").save(image)
        original = image.read_bytes()
        order = rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=image, text_file=None,
            source_id=None, job_id="RAIL-IMAGE-DEFAULT", title="原圖標題",
            slug="image-default", profile="article",
        ))
        _, package, _ = rail.paths(self.root, "RAIL-IMAGE-DEFAULT")
        self.assertEqual(worker.read_json(package / "input_bundle.json")["asset_mode"], worker.ASSET_MODE_KNOWLEDGE)
        self.assertEqual((self.root / order["original_image"]).read_bytes(), original)

    def test_normal_image_combines_analysis_then_repairs_only_rejected_semantics(self):
        image = self.root / "combined-image.png"
        Image.new("RGB", (32, 48), "black").save(image)
        order = rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=image, text_file=None,
            source_id="KS-VS-IMAGE-COMPACT", job_id="RAIL-IMAGE-COMPACT",
            title="原圖快速保存", slug="image-compact", profile="article",
        ))
        self.assertIn("image_analysis", order["response_template"])
        self.assertEqual(set(order["response_template"]), {"image_analysis"})
        analysis = order["response_template"]["image_analysis"]
        analysis.update(analysis_status="complete", checker_status="passed", checker_evidence="Fixture image reviewed.",
                        visual_type="document_scan", title="原圖快速保存", summary=BODY)
        analysis["anchors"][0].update(kind="text", evidence=BODY)
        analysis["transcription"][0].update(text=BODY, confidence="high")
        analysis["source_claims"][0].update(claim=BODY, verification_status="image_visible")
        analysis["coverage"][0].update(disposition="knowledge_point", reason="保留原圖及可讀文字。")
        response = self.root / "combined-image-response.json"
        worker.write_json(response, {"image_analysis": analysis})
        repair_order = rail.submit_image_analysis(self.root, "RAIL-IMAGE-COMPACT", response)
        _, package, kp_path = rail.paths(self.root, "RAIL-IMAGE-COMPACT")
        self.assertTrue((package / "image_analysis.json").is_file())
        self.assertEqual(repair_order["action"], "prepare_semantic_plan")
        self.assertEqual([block_id for block_id, _ in self._reading_blocks(repair_order)], ["IMG-001"])
        self.assertFalse(kp_path.exists())
        draft = worker.read_json(self.root / repair_order["draft_file"])
        self.assertEqual(draft["schema_version"], COMPACT_VERSION)
        self.assertIn("input_fingerprint", draft)
        rejected = copy.deepcopy(draft)
        rejected["summary"] = ["原圖中的文字說明保存原圖和可追溯來源的流程。"]
        rejected["application"] = {"knowledge_use": "學習材料"}
        rejected["knowledge"] = [{
            "title": "原圖來源證據", "claim": BODY, "layer": "原文主張",
            "confidence": "高", "evidence_block_ids": ["IMG-404"],
            "reusable_concept": {"status": "not_reusable", "reason": "單張原圖的文字沒有可重用的抽象概念。"},
        }]
        rejected_path = self.root / "image-semantic-rejected.json"
        worker.write_json(rejected_path, rejected)
        with self.assertRaisesRegex(ValueError, "unknown accepted source block"):
            rail.submit_plan(self.root, "RAIL-IMAGE-COMPACT", rejected_path)
        repaired = self.root / "image-semantic-repair.json"
        rejected["knowledge"][0]["evidence_block_ids"] = ["IMG-001"]
        worker.write_json(repaired, rejected)
        rail.measured(
            self.root, "RAIL-IMAGE-COMPACT", "submit_plan", rail.submit_plan,
            root=self.root, job_id="RAIL-IMAGE-COMPACT", response_path=repaired,
        )
        self.assertTrue(kp_path.is_file())
        self.assertEqual(worker.read_json(kp_path)["infographics"], [])

    def test_legacy_combined_image_submission_remains_accepted(self):
        image = self.root / "legacy-combined-image.png"
        Image.new("RGB", (32, 48), "black").save(image)
        order = rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=image, text_file=None,
            source_id="KS-VS-IMAGE-LEGACY", job_id="RAIL-IMAGE-LEGACY",
            title="原圖舊式合併提交", slug="image-legacy", profile="article",
        ))
        analysis = order["response_template"]["image_analysis"]
        analysis.update(analysis_status="complete", checker_status="passed", checker_evidence="Fixture image reviewed.",
                        visual_type="document_scan", title="原圖舊式合併提交", summary=BODY)
        analysis["anchors"][0].update(kind="text", evidence=BODY)
        analysis["transcription"][0].update(text=BODY, confidence="high")
        analysis["source_claims"][0].update(claim=BODY, verification_status="image_visible")
        analysis["coverage"][0].update(disposition="knowledge_point", reason="保留原圖及可讀文字。")
        response = self.root / "legacy-combined-image-response.json"
        worker.write_json(response, {
            "image_analysis": analysis,
            "semantic_plan": {
                "schema_version": COMPACT_VERSION,
                "source_catalog_version": CURRENT_SOURCE_CATALOG_VERSION,
                "summary": ["原圖中的文字說明保存原圖和可追溯來源的流程。"],
                "application": {"knowledge_use": "學習材料"},
                "knowledge": [{
                    "title": "原圖來源證據", "claim": BODY, "layer": "原文主張",
                    "confidence": "高", "evidence_block_ids": ["IMG-001"],
                    "reusable_concept": {"status": "not_reusable", "reason": "單張原圖的文字沒有可重用的抽象概念。"},
                }],
            },
        })
        result = rail.submit_image_analysis(self.root, "RAIL-IMAGE-LEGACY", response)
        _, _, kp_path = rail.paths(self.root, "RAIL-IMAGE-LEGACY")
        self.assertTrue(kp_path.is_file())
        self.assertNotEqual(result["action"], "review_original_image")

    def test_case_evidence_without_mode_keeps_formal_route(self):
        manifest = self.root / "case-evidence.json"
        manifest.write_text("{}", encoding="utf-8")
        args = SimpleNamespace(
            root=self.root, url="https://example.com/case", image_file=None, text_file=None,
            source_id=None, job_id=None, title=None, slug="case", profile="article",
            case_evidence_file=manifest, asset_mode=None,
        )
        with patch.object(rail, "capture", side_effect=worker.CompilerWorkerV2Error("offline fixture")):
            with self.assertRaises(worker.CompilerWorkerV2Error):
                rail.start(args)
        identity = "url:" + worker.sha256_text(worker.normalize_url(args.url))
        owner = worker.latest_v2_job_for_source(self.root, identity)
        self.assertEqual(owner["rail_capture"]["asset_mode"], worker.ASSET_MODE_FORMAL)

    def test_add_infographic_creates_a_formal_child_without_mutating_text_first_parent(self):
        source = self.root / "text-first-source.md"
        source.write_text(BODY + "\n先完成文字，再按需要補圖。", encoding="utf-8")
        rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-0010", job_id="TEXT-FIRST", title="固定流程補圖",
            slug="text-first", profile="article", asset_mode=None,
        ))
        _, parent_package, parent_kp_path = rail.paths(self.root, "TEXT-FIRST")
        text_plan = semantic_plan(parent_package)
        text_plan["asset_mode"] = worker.ASSET_MODE_KNOWLEDGE
        text_plan["infographics"] = []
        worker.write_json(parent_kp_path, build_kp(text_plan, parent_package, root=self.root))
        completed = rail.advance(self.root, "TEXT-FIRST", Path("knowledge-base-vault"))
        self.assertEqual(completed["status"], "completed")
        parent_bytes = parent_kp_path.read_bytes()
        parent_receipt = (parent_package / "completion_receipt.json").read_bytes()
        listed = rail.add_infographic(self.root, None, None, None)
        self.assertEqual(listed["action"], "select_infographic_source")
        self.assertEqual([item["source_id"] for item in listed["candidates"]], ["KS-VS-0010"])
        request = rail.add_infographic(self.root, "KS-VS-0010", None, None)
        self.assertEqual(request["action"], "prepare_infographic_revision")
        self.assertEqual(request["continuation"]["directive"], "continue_same_job")
        parent_kp = worker.read_json(parent_kp_path)
        formal_plan = semantic_plan(parent_package)
        units = {unit["id"]: unit for unit in formal_plan["coverage_units"]}
        for page in formal_plan["infographics"]:
            page["status"] = "planned"
            page["content_map"] = [
                {
                    "coverage_id": coverage_id,
                    "source_evidence": units[coverage_id]["source_evidence"],
                    "visible_text": units[coverage_id]["infographic_text"],
                }
                for coverage_id in page["coverage_ids"]
            ]
            page["visible_text"] = list(dict.fromkeys(
                line for item in page["content_map"] for line in item["visible_text"]
            ))
            page["source_anchors"] = list(dict.fromkeys(
                item["source_evidence"] for item in page["content_map"]
            ))
            page["title"] = parent_kp["source"]["title"]
            page["title_source"] = parent_kp["source"]["title_source"]
            page["reader_review"]["page_sha256"] = reader_page_sha256(page)
        revision_plan = {
            "schema_version": worker.INFOGRAPHIC_REVISION_PLAN_VERSION,
            "mode": "knowledge_only_to_infographic",
            "revision_id": "add-image-r1",
            "job_id": "TEXT-FIRST-IMAGE-R1",
            "source_id": parent_kp["source"]["source_id"],
            "source_identity": parent_kp["source"]["source_identity"],
            "coverage_units": parent_kp["coverage_units"],
            "infographics": formal_plan["infographics"],
        }
        plan_path = self.root / "add-image-r1.json"
        worker.write_json(plan_path, revision_plan)
        order = rail.add_infographic(self.root, "KS-VS-0010", plan_path, "add-image-r1")
        self.assertEqual(order["job_id"], "TEXT-FIRST-IMAGE-R1")
        self.assertEqual(order["action"], "compile_plan")
        child_kp_path = self.root / order["package_dir"] / "knowledge_package.json"
        child_kp = worker.read_json(child_kp_path)
        self.assertEqual(child_kp["source"]["asset_mode"], worker.ASSET_MODE_FORMAL)
        self.assertEqual(child_kp["projection"]["asset_mode"], worker.ASSET_MODE_FORMAL)
        self.assertEqual(worker.read_json(child_kp_path.parent / "input_bundle.json")["asset_mode"], worker.ASSET_MODE_FORMAL)
        self.assertEqual(
            worker.revision_semantic_content_sha256(child_kp),
            worker.revision_semantic_content_sha256(parent_kp),
        )
        self.assertEqual(parent_kp_path.read_bytes(), parent_bytes)
        self.assertEqual((parent_package / "completion_receipt.json").read_bytes(), parent_receipt)
        resumed = rail.add_infographic(self.root, "KS-VS-0010", None, None)
        self.assertEqual(resumed["job_id"], "TEXT-FIRST-IMAGE-R1")
        child_kp["knowledge"][0]["claim"] = "changed after text-first acceptance"
        worker.write_json(child_kp_path, child_kp)
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "changed accepted semantic content"):
            worker.validate_job_package_binding(self.root, child_kp_path, child_kp)

    def test_compact_parent_adds_only_infographic_overlay(self):
        source = self.root / "compact-overlay-source.md"
        source.write_text(
            BODY + "\n\n中間段落只保存，不預設等於已分析。\n\n"
            "最後段落保留作圖面閱讀的來源定位。",
            encoding="utf-8",
        )
        rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-COVER-01", job_id="COMPACT-OVERLAY",
            title="文字優先補圖", slug="compact-overlay", profile="article",
        ))
        _, parent_package, parent_kp_path = rail.paths(self.root, "COMPACT-OVERLAY")
        worker.write_json(
            parent_kp_path,
            build_kp(
                compact_semantic_plan(parent_package, ["SRC-001", "SRC-003"]),
                parent_package,
                root=self.root,
            ),
        )
        self.assertEqual(
            rail.advance(self.root, "COMPACT-OVERLAY", Path("knowledge-base-vault"))["status"],
            "completed",
        )
        parent_kp = worker.read_json(parent_kp_path)
        revision_units = copy.deepcopy(parent_kp["coverage_units"])
        unit = revision_units[0]
        unit["dispositions"].append("infographic")
        unit["infographic_text"] = [BODY]
        page = copy.deepcopy(semantic_plan(parent_package)["infographics"][0])
        page.update({
            "status": "planned",
            "coverage_ids": [unit["id"]],
            "content_map": [{
                "coverage_id": unit["id"],
                "source_evidence": unit["source_evidence"],
                "visible_text": unit["infographic_text"],
            }],
            "visible_text": list(unit["infographic_text"]),
            "source_anchors": [unit["source_evidence"]],
            "title": parent_kp["source"]["title"],
            "title_source": parent_kp["source"]["title_source"],
        })
        page["reader_review"]["page_sha256"] = reader_page_sha256(page)
        revision_plan = {
            "schema_version": worker.INFOGRAPHIC_REVISION_PLAN_VERSION,
            "mode": "knowledge_only_to_infographic",
            "revision_id": "compact-overlay-r1",
            "job_id": "COMPACT-OVERLAY-IMAGE-R1",
            "source_id": parent_kp["source"]["source_id"],
            "source_identity": parent_kp["source"]["source_identity"],
            "coverage_units": revision_units,
            "infographics": [page],
        }
        plan_path = self.root / "compact-overlay-r1.json"
        worker.write_json(plan_path, revision_plan)
        order = rail.add_infographic(
            self.root, "KS-VS-COVER-01", plan_path, "compact-overlay-r1",
        )
        child_kp = worker.read_json(self.root / order["package_dir"] / "knowledge_package.json")
        self.assertEqual(child_kp["semantic_plan_version"], COMPACT_VERSION)
        child_package = self.root / order["package_dir"]
        self.assertEqual(
            worker.validate_coverage_units(
                child_kp,
                (child_package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n"),
                source_blocks=worker.compact_coverage_source_blocks(child_kp, child_package),
            ),
            [],
        )
        self.assertEqual(
            worker.infographic_plan_preflight_issues(child_kp, child_package, phase="plan"),
            [],
        )
        self.assertEqual(
            worker.revision_semantic_content_sha256(child_kp),
            worker.revision_semantic_content_sha256(parent_kp),
        )
        tampered = copy.deepcopy(revision_units)
        tampered[0]["source_evidence"] = "不是已接受的原文證據"
        self.assertTrue(
            worker.knowledge_only_infographic_overlay_errors(
                parent_kp["coverage_units"], tampered,
            )
        )

    def test_legacy_revision_hash_keeps_precompact_coverage_ledger(self):
        kp = build_kp(semantic_plan(self.package), self.package, root=self.root)
        source = copy.deepcopy(kp["source"])
        source.pop("asset_mode", None)
        expected = worker.canonical_payload_sha256({
            "source": source,
            "analysis": kp["analysis"],
            "coverage_units": kp["coverage_units"],
            "knowledge": kp["knowledge"],
            "relations": kp["relations"],
            "application": kp["application"],
        })
        self.assertNotEqual(kp["semantic_plan_version"], COMPACT_VERSION)
        self.assertEqual(worker.revision_semantic_content_sha256(kp), expected)

    def test_table_parser_keeps_escaped_divider_text_and_writer_rejects_missing_kp(self):
        text = """## 來源稽核

| 欄位 | 值 |
| --- | --- |
| URL | https://example.test/a---b |

### 知識點草案

| ID | 知識點 | 層級 | 可信度 | 來源證據 |
| --- | --- | --- | --- | --- |
| KP-VS-0001-001 | 比較 | 原文主張 | 高 | 條件 A \\| --- \\| 條件 B |
"""
        rows = adapter.parse_kp_rows(text)
        self.assertEqual([row.kp_id for row in rows], ["KP-VS-0001-001"])
        self.assertEqual(rows[0].evidence, "條件 A | --- | 條件 B")
        self.assertEqual(
            adapter.parse_table_after_heading(text, "## 來源稽核")["URL"],
            "https://example.test/a---b",
        )
        with patch.object(worker, "run_json_subprocess", return_value={
            "status": "ready",
            "verification": {"passed": True},
            "knowledge_points": ["knowledge-base-vault/knowledge-points/by-source/KS-VS-0001/KP-VS-0001-001.md"],
        }):
            with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "did not retain every Knowledge Package point"):
                worker.write_vault_and_readback(
                    root=self.root,
                    vault=self.root / "knowledge-base-vault",
                    source_slice=self.root / "source.md",
                    knowledge_point_ids=["KP-VS-0001-001", "KP-VS-0001-002"],
                    write=False,
                )

    def _prepared_text_first_infographic_child(self, source_id="KS-VS-0011", *, slug="text-first"):
        source = self.root / f"{source_id}-source.md"
        source.write_text(BODY + "\n先完成文字，再按需要補圖。", encoding="utf-8")
        parent_job_id = f"{source_id}-TEXT"
        rail.start(SimpleNamespace(
            root=self.root, url=None, image_file=None, text_file=source,
            source_id=source_id, job_id=parent_job_id, title="固定流程補圖",
            slug=slug, profile="article", asset_mode=None,
        ))
        _, parent_package, parent_kp_path = rail.paths(self.root, parent_job_id)
        text_plan = semantic_plan(parent_package)
        text_plan["asset_mode"] = worker.ASSET_MODE_KNOWLEDGE
        text_plan["infographics"] = []
        worker.write_json(parent_kp_path, build_kp(text_plan, parent_package, root=self.root))
        self.assertEqual(
            rail.advance(self.root, parent_job_id, Path("knowledge-base-vault"))["status"],
            "completed",
        )
        parent_kp = worker.read_json(parent_kp_path)
        formal_plan = semantic_plan(parent_package)
        units = {unit["id"]: unit for unit in formal_plan["coverage_units"]}
        for page in formal_plan["infographics"]:
            page["status"] = "planned"
            page["content_map"] = [
                {
                    "coverage_id": coverage_id,
                    "source_evidence": units[coverage_id]["source_evidence"],
                    "visible_text": units[coverage_id]["infographic_text"],
                }
                for coverage_id in page["coverage_ids"]
            ]
            page["visible_text"] = list(dict.fromkeys(
                line for item in page["content_map"] for line in item["visible_text"]
            ))
            page["source_anchors"] = list(dict.fromkeys(
                item["source_evidence"] for item in page["content_map"]
            ))
            page["title"] = parent_kp["source"]["title"]
            page["title_source"] = parent_kp["source"]["title_source"]
            page["reader_review"]["page_sha256"] = reader_page_sha256(page)
        revision_id = f"{source_id}-image-r1"
        child_job_id = f"{source_id}-IMAGE-R1"
        revision_plan = {
            "schema_version": worker.INFOGRAPHIC_REVISION_PLAN_VERSION,
            "mode": "knowledge_only_to_infographic",
            "revision_id": revision_id,
            "job_id": child_job_id,
            "source_id": source_id,
            "source_identity": parent_kp["source"]["source_identity"],
            "coverage_units": parent_kp["coverage_units"],
            "infographics": formal_plan["infographics"],
        }
        plan_path = self.root / f"{revision_id}.json"
        worker.write_json(plan_path, revision_plan)
        order = rail.add_infographic(self.root, source_id, plan_path, revision_id)
        child_kp_path = self.root / order["package_dir"] / "knowledge_package.json"
        return {
            "source_id": source_id,
            "parent_package": parent_package,
            "parent_kp_path": parent_kp_path,
            "child_kp_path": child_kp_path,
            "revision_id": revision_id,
            "child_job_id": child_job_id,
            "plan_path": plan_path,
        }

    def test_prepare_infographic_revision_recovers_missing_same_child_job(self):
        fixture = self._prepared_text_first_infographic_child("KS-VS-0011")
        child_job_path = worker.job_file(self.root, fixture["child_job_id"])
        child_job_path.unlink()
        resumed = rail.add_infographic(self.root, fixture["source_id"], None, None)
        child = worker.read_json(child_job_path)
        self.assertEqual(child["job_id"], fixture["child_job_id"])
        self.assertEqual(child["package_dir"], fixture["child_kp_path"].parent.relative_to(self.root).as_posix())
        self.assertEqual(resumed["job_id"], fixture["child_job_id"])

    def test_add_infographic_pointer_returns_verified_completion(self):
        fixture = self._prepared_text_first_infographic_child("KS-VS-0012")
        child_kp = worker.read_json(fixture["child_kp_path"])
        asset = self.root / "knowledge-base-vault" / "assets" / "pointer-fixture.png"
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b"synthetic-pointer-asset")
        completion_path = fixture["child_kp_path"].parent / "completion_receipt.json"
        completion = {
            "schema_version": worker.COMPLETION_SCHEMA_VERSION,
            "status": "completed",
            "job_id": fixture["child_job_id"],
            "source_id": fixture["source_id"],
            "source_identity": child_kp["source"]["source_identity"],
            "adapter": {"assets": [asset.relative_to(self.root).as_posix()]},
        }
        completion["payload_sha256"] = worker.canonical_payload_sha256(completion)
        worker.write_json(completion_path, completion)
        worker.write_current_infographic_pointer(
            self.root, fixture["child_kp_path"].parent, child_kp, completion_path, completion,
        )
        result = rail.add_infographic(self.root, fixture["source_id"], None, None)
        self.assertEqual(result["continuation"]["directive"], "deliver_verified_result")
        self.assertEqual(result["completion"]["completion_receipt"], completion_path.relative_to(self.root).as_posix())
        # A real completion also closes the child job; only an unfinished child counts as the active addition.
        worker.complete_v2_job(self.root, fixture["child_job_id"], completion_path)
        # The synthetic receipt is not a full writer receipt; readback is covered elsewhere.
        # The 1.9.18 parent-receipt check reads the parent package too, so the parent keeps its real receipt.
        parent_kp = worker.read_json(fixture["parent_kp_path"])
        parent_completion = worker.read_json(
            worker.bound_completion_path(self.root, fixture["parent_package"], fixture["parent_kp_path"], parent_kp)
        )

        def readback(_root, package, _kp_path, _kp, **_kwargs):
            if Path(package).resolve() == Path(fixture["parent_package"]).resolve():
                return parent_completion
            return completion

        with patch.object(worker, "completed_receipt_readback", side_effect=readback):
            rebuild = rail.add_infographic(self.root, fixture["source_id"], None, None, rebuild=True)
        self.assertEqual(rebuild["action"], "prepare_infographic_revision")
        self.assertTrue(rebuild["rebuild"])
        self.assertEqual(self._work_order_file(rebuild)["revision_plan_contract"]["mode"], "knowledge_only_to_infographic")

    def test_completed_formal_source_returns_existing_image_unless_rebuilt(self):
        legacy_kp = build_kp(semantic_plan(self.package), self.package, root=self.root)
        del legacy_kp["source"]["asset_mode"]
        worker.write_json(self.kp_path, legacy_kp)
        job = worker.read_json(worker.job_file(self.root, "RAIL-TEST"))
        job["status"] = "completed"
        worker.write_v2_job(self.root, job)
        completion = {
            "schema_version": worker.COMPLETION_SCHEMA_VERSION,
            "status": "completed", "payload_sha256": "fixture-receipt",
        }
        with patch.object(worker, "completed_receipt_readback", return_value=completion):
            result = rail.add_infographic(self.root, "KS-VS-0001", None, None)
            self.assertTrue(result["already_exists"])
            self.assertEqual(result["completion"]["readback"], "verified")
            rebuild = rail.add_infographic(self.root, "KS-VS-0001", None, None, rebuild=True)
        self.assertEqual(rebuild["action"], "prepare_infographic_revision")
        self.assertEqual(self._work_order_file(rebuild)["revision_plan_contract"]["mode"], "formal_infographic_revision")
        self.assertIn("--rebuild", rebuild["submit"])

    def test_rebuild_prepares_a_new_child_after_completed_text_first_child(self):
        fixture = self._prepared_text_first_infographic_child("KS-VS-0012B")
        child_job_path = worker.job_file(self.root, fixture["child_job_id"])
        child_job = worker.read_json(child_job_path)
        child_job["status"] = "completed"
        worker.write_v2_job(self.root, child_job)
        plan = worker.read_json(fixture["plan_path"])
        plan.update({
            "revision_id": "KS-VS-0012B-image-r2",
            "job_id": "KS-VS-0012B-IMAGE-R2",
        })
        rebuild_plan = self.root / "KS-VS-0012B-image-r2.json"
        worker.write_json(rebuild_plan, plan)
        rebuilt = rail.add_infographic(
            self.root, fixture["source_id"], rebuild_plan,
            "KS-VS-0012B-image-r2", rebuild=True,
        )
        self.assertEqual(rebuilt["job_id"], "KS-VS-0012B-IMAGE-R2")
        self.assertNotEqual(rebuilt["job_id"], fixture["child_job_id"])
        self.assertTrue((self.root / rebuilt["package_dir"] / "knowledge_package.json").is_file())

    def test_interrupted_child_writer_allows_only_its_verified_parent_note_and_outcome_drift(self):
        source_id = "KS-VS-0013-LONG-ABCD"
        fixture = self._prepared_text_first_infographic_child(source_id, slug="long-overlay-path-budget-fixt")
        child_kp = worker.read_json(fixture["child_kp_path"])
        child_package = fixture["child_kp_path"].parent
        action = self._envelope(rail.advance(self.root, fixture["child_job_id"], Path("knowledge-base-vault")))
        asset = self.root / action["expected_asset"]
        asset.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1024, 1536), "#f7f2e8").save(asset, "PNG")
        visible = visible_text_from_prompt(action["prompt"])
        accepted_review = {
            "inspection_method": "independent_image_readback",
            "inspector_identity": "OFFLINE-TEST-ONLY",
            "formal_ingestion_allowed": True,
            "spec_read_receipt": infographic_spec_read_receipt(),
            "review_axes": {
                axis.strip(): "passed"
                for axis in load_infographic_reverse_review_contract()["required_review_axes"].split(",")
            },
            "reader_review": "Synthetic image accepted only for a crash-window contract test.",
            "reader_comprehension": {
                "answer": "The source-backed flow is comprehensible.",
                "relationship_explanation": "The reader can trace the stated relation.",
                "unexplained_terms": [], "missing_context": [], "misleading_relations": [],
            },
            "inspected_png_sha256": worker.sha256_bytes(asset.read_bytes()),
            "inspected_prompt_sha256": worker.sha256_text(action["prompt"]),
            "visible_text_readback": visible,
            "unexpected_visible_text": [], "internal_process_text_detected": False,
        }
        response = rail.image_response_template(self.root, action)
        response.update({
            "runtime_tool": "Codex imagegen",
            "imagegen_dispatch_evidence": "OFFLINE-TEST-ONLY-DISPATCH",
            "outcome": "accepted", "failure_code": "",
            "semantic_evidence": "Synthetic fixture for an interrupted child writer test.",
            "semantic_readback": accepted_review,
            "page_number_readback": visible[-2], "footer_text_readback": visible[-1],
        })
        response_path = child_package / "accepted-child-image.json"
        worker.write_json(response_path, response)
        with patch.object(worker, "run_infographic_visual_qc", return_value={"status": "ready", "checks": []}):
            self.assertEqual(worker.command_imagegen_complete(SimpleNamespace(
                root=self.root, kp_file=fixture["child_kp_path"], response_file=response_path,
            )), 0)
        with patch.object(worker, "audit_outcome_reader_quality", side_effect=worker.CompilerWorkerV2Error("injected reader interruption")):
            with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "injected reader interruption"):
                rail.advance(self.root, fixture["child_job_id"], Path("knowledge-base-vault"))
        child_kp = worker.read_json(fixture["child_kp_path"])
        attempts = worker.read_json(worker.image_attempt_ledger_path(child_package))["pages"]["IG-001"]["attempts"]
        journal_path = next((child_package / worker.INFOGRAPHIC_OVERLAY_EVIDENCE_DIR).glob("*/overlay.json"))
        journal = worker.read_json(journal_path)
        self.assertEqual(len(child_package.parent.parent.name), 50)
        self.assertEqual(journal_path.parent.name, journal["overlay_id"][:worker.INFOGRAPHIC_OVERLAY_FOLDER_ID_CHARS])
        self.assertRegex(journal["overlay_id"], r"^[0-9a-f]{64}$")
        self.assertLessEqual(
            len(str(journal_path.with_name("." + "f" * 32 + ".tmp").resolve())),
            256,
        )
        resumed = rail.add_infographic(self.root, fixture["source_id"], None, None)
        self.assertEqual(resumed["job_id"], fixture["child_job_id"])
        completed = rail.advance(self.root, fixture["child_job_id"], Path("knowledge-base-vault"))
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(
            len(worker.read_json(worker.image_attempt_ledger_path(child_package))["pages"]["IG-001"]["attempts"]),
            len(attempts),
        )
        pointer = worker.read_current_infographic_pointer(
            self.root,
            fixture["parent_package"],
            worker.read_json(fixture["parent_kp_path"]),
        )
        self.assertIsNotNone(pointer)
        self.assertEqual(pointer["current_revision_id"], fixture["revision_id"])

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
                               source_id=None, job_id=None, title=None, slug="url", profile="article", asset_mode=worker.ASSET_MODE_FORMAL)
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
        draft = worker.read_json(self.root / again["response_draft_file"])
        self.assertEqual(draft["runtime_tool"], "")
        self.assertEqual(draft["page_number_readback"], "")
        self.assertEqual(draft["footer_text_readback"], "")
        self.assertIsNone(draft["semantic_readback"]["internal_process_text_detected"])
        kp = worker.read_json(self.kp_path)
        kp["knowledge"][0]["claim"] = "changed after lock"
        worker.write_json(self.kp_path, kp)
        with self.assertRaises(worker.CompilerWorkerV2Error):
            rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault"))

    def _authorized_failure_action(self):
        worker.write_json(self.kp_path, build_kp(semantic_plan(self.package), self.package, root=self.root))
        action = self._envelope(rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault")))
        asset = self.root / action["expected_asset"]
        asset.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1024, 1536), "#f7f2e8")
        image.save(asset, "PNG")
        return action, asset

    def _repairable_failure_response(self, action, *, include_evidence=True):
        response = rail.image_response_template(self.root, action)
        expected = visible_text_from_prompt(action["prompt"])[0]
        review = response["semantic_readback"]
        review.update({
            "inspector_identity": "OFFLINE-TEST-ONLY",
            "failure_code": "wrong_or_missing_locked_text",
            "content_failure_evidence": {
                "failure_code": "wrong_or_missing_locked_text",
                "expected_locked_text": expected,
                "observed_problem": "The PNG omits a locked sentence needed for accurate reading.",
                "reader_impact": "Readers lose the required explanation.",
            },
        })
        if not include_evidence:
            review["failure_code"] = ""
            review["content_failure_evidence"] = {
                "failure_code": "", "expected_locked_text": "",
                "observed_problem": "", "reader_impact": "",
            }
        response.update({
            "runtime_tool": "Codex imagegen",
            "imagegen_dispatch_evidence": "OFFLINE-TEST-ONLY-DISPATCH",
            "outcome": "blocking_failure",
            "failure_code": "wrong_or_missing_locked_text",
            "semantic_evidence": "Offline fixture documents one locked-text omission.",
            "page_number_readback": "",
            "footer_text_readback": "",
        })
        return response

    def _complete_image_response(self, response):
        response_path = self.package / "same-png-failure.json"
        worker.write_json(response_path, response)
        return worker.command_imagegen_complete(SimpleNamespace(
            root=self.root, kp_file=self.kp_path, response_file=response_path,
        ))

    def test_missing_first_failure_evidence_does_not_persist_and_same_png_legacy_supplement_unlocks_one_repair(self):
        action, _ = self._authorized_failure_action()
        missing = self._repairable_failure_response(action, include_evidence=False)
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "lacks required failure evidence"):
            self._complete_image_response(missing)
        ledger_path = worker.image_attempt_ledger_path(self.package)
        after_rejected = worker.read_json(ledger_path)
        self.assertEqual(after_rejected["pages"]["IG-001"]["attempts"][-1]["status"], "authorized")
        self.assertEqual(worker.read_json(worker.image_action_envelope_path(self.package))["state"], "awaiting_imagegen")

        complete = self._repairable_failure_response(action)
        self.assertEqual(self._complete_image_response(complete), 0)
        ledger = worker.read_json(ledger_path)
        record = ledger["pages"]["IG-001"]
        legacy = record["attempts"][-1]
        legacy["semantic_readback"].pop("failure_code")
        legacy["semantic_readback"].pop("content_failure_evidence")
        worker.write_json(ledger_path, ledger)
        kp = worker.read_json(self.kp_path)
        worker.apply_attempt_result_to_kp(kp["infographics"][0], record, legacy)
        worker.write_json(self.kp_path, kp)

        supplement_order = rail.work_order(self.root, "RAIL-TEST")
        self.assertEqual(supplement_order["action"], "supplement_failure_evidence")
        self.assertEqual(supplement_order["continuation"]["directive"], "continue_same_job")
        self.assertIn("Do not call image generation", supplement_order["instructions"])

        wrong_hash = copy.deepcopy(complete)
        wrong_hash["semantic_readback"]["inspected_png_sha256"] = "f" * 64
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "failure evidence supplement"):
            self._complete_image_response(wrong_hash)

        self.assertEqual(self._complete_image_response(complete), 0)
        supplemented_bytes = ledger_path.read_bytes()
        self.assertEqual(self._complete_image_response(complete), 0)
        self.assertEqual(ledger_path.read_bytes(), supplemented_bytes)
        altered = copy.deepcopy(complete)
        altered["semantic_readback"]["content_failure_evidence"]["observed_problem"] = "A different claim about the same PNG is not allowed."
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "may not alter existing evidence"):
            self._complete_image_response(altered)

        repair_order = rail.work_order(self.root, "RAIL-TEST")
        self.assertEqual(repair_order["action"], "authorize_blocking_repair")
        repair = rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault"))
        self.assertEqual(repair["action"], "generate_image_once")
        self.assertEqual(self._envelope(repair)["attempt_number"], 2)
        self.assertEqual(rail.advance(self.root, "RAIL-TEST", Path("knowledge-base-vault"))["action"], "reconcile_image_result")
        self.assertEqual(len(worker.read_json(ledger_path)["pages"]["IG-001"]["attempts"]), 2)

    def test_completed_text_failure_with_complete_evidence_keeps_existing_same_png_accepted_rereview(self):
        action, _ = self._authorized_failure_action()
        self.assertEqual(self._complete_image_response(self._repairable_failure_response(action)), 0)
        visible = visible_text_from_prompt(action["prompt"])
        accepted_review = {
            "inspection_method": "independent_image_readback",
            "inspector_identity": "OFFLINE-TEST-ONLY",
            "formal_ingestion_allowed": True,
            "spec_read_receipt": infographic_spec_read_receipt(),
            "review_axes": {
                axis.strip(): "passed"
                for axis in load_infographic_reverse_review_contract()["required_review_axes"].split(",")
            },
            "reader_review": "The same PNG now has a complete independent reader review.",
            "reader_comprehension": {
                "answer": "The source-backed flow is comprehensible.",
                "relationship_explanation": "The reader can trace the stated relation.",
                "unexplained_terms": [], "missing_context": [], "misleading_relations": [],
            },
            "inspected_png_sha256": worker.sha256_bytes((self.root / action["expected_asset"]).read_bytes()),
            "inspected_prompt_sha256": worker.sha256_text(action["prompt"]),
            "visible_text_readback": visible,
            "unexpected_visible_text": [], "internal_process_text_detected": False,
        }
        accepted = rail.image_response_template(self.root, action)
        accepted.update({
            "runtime_tool": "Codex imagegen",
            "imagegen_dispatch_evidence": "OFFLINE-TEST-ONLY-DISPATCH",
            "outcome": "accepted", "failure_code": "",
            "semantic_evidence": "The existing PNG received a complete independent re-review.",
            "semantic_readback": accepted_review,
            "page_number_readback": visible[-2], "footer_text_readback": visible[-1],
        })
        with patch.object(worker, "run_infographic_visual_qc", return_value={"status": "ready", "checks": []}):
            self.assertEqual(self._complete_image_response(accepted), 0)
        latest = worker.read_json(worker.image_attempt_ledger_path(self.package))["pages"]["IG-001"]["attempts"][-1]
        self.assertEqual(latest["status"], "accepted")
        self.assertIn("prior_rejected_result", latest)

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
        order = rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id=None, job_id="RAIL-IMAGE", title="原圖標題", slug="image", profile="article", asset_mode=worker.ASSET_MODE_FORMAL))
        self.assertEqual(order["action"], "review_original_image")
        self.assertEqual((self.root / order["original_image"]).read_bytes(), original)
        self.assertEqual(order["response_template"]["checker_status"], "pending")
        _, package, kp = rail.paths(self.root, "RAIL-IMAGE")
        self.assertFalse(kp.exists())
        self.assertEqual(worker.read_json(package / "input_bundle.json")["requested_title"], "原圖標題")
        again = rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id=None, job_id=None, title=None, slug=None, profile="article", asset_mode=worker.ASSET_MODE_FORMAL))
        self.assertEqual(again["job_id"], "RAIL-IMAGE")
        response = order["response_template"]
        response_path = self.root / "image-analysis.json"
        worker.write_json(response_path, response)
        with self.assertRaisesRegex(worker.CompilerWorkerV2Error, "image analysis contract failed"):
            rail.invoke(worker.command_accept_image, root=self.root, source_id=order["source_id"], job_id="RAIL-IMAGE", image_file=image, image_analysis_file=response_path, title=None, slug="image", profile="article", asset_mode=worker.ASSET_MODE_FORMAL)
        response.update(analysis_status="complete", checker_status="passed", checker_evidence="Fixture only: complete region reviewed.",
                        visual_type="document_scan", title="原圖標題", summary=BODY)
        response["anchors"][0].update(kind="text", evidence=BODY)
        response["transcription"][0].update(text=BODY, confidence="high")
        response["source_claims"][0].update(claim=BODY, verification_status="image_visible")
        response["coverage"][0].update(disposition="knowledge_point", reason="完整保留原圖及可辨文字。")
        worker.write_json(response_path, response)
        rail.invoke(worker.command_accept_image, root=self.root, source_id=order["source_id"], job_id="RAIL-IMAGE", image_file=image, image_analysis_file=response_path, title="原圖標題", slug="image", profile="article", asset_mode=worker.ASSET_MODE_FORMAL)
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
