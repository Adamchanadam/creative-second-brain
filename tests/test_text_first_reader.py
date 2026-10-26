"""Focused text-first reader and entry-contract regression checks."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "runtime" / "workspace" / "tools"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT / "runtime" / "workspace"))
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(SCRIPTS))

import outcome_page_generator as generator
import outcome_page_quality_audit as audit
import setup_workspace


SOURCE_ID = "KS-VS-TEXT-FIRST"
IDENTITY = "user_provided_text:" + "a" * 64


def main() -> int:
    text_first_media = generator.outcome_media_block([], [], "完整文字來源", "knowledge_only")
    assert text_first_media == ""
    assert "<img" not in text_first_media
    assert "資訊圖 1" not in text_first_media
    assert '<img src="assets/formal.png"' in generator.outcome_media_block(
        ["assets/formal.png"], [], "正式來源", "formal_infographic"
    )

    source_fields = {
        "title": "完整文字來源",
        "reader_title": "完整文字來源",
        "summary": "這是可先閱讀的完整來源摘要。",
        "source_url": "https://example.test/text-first",
        "asset_mode": "knowledge_only",
        "infographic_status": "pending",
        "coverage_unit_total": 3,
        "coverage_unit_infographic_mapped": 0,
        "infographic_page_count": 0,
        "infographic_multi_volume_status": "not_generated",
        "coverage_audit_status": "text_only_pending_infographic",
    }
    page_html = generator.render_html(
        source_id=SOURCE_ID,
        source_fields=source_fields,
        app_fields={},
        knowledge_points=[{"knowledge_point": "完整文字知識點", "definition": "已保存的來源可供日後搜尋與重讀。"}],
        asset_srcs=[],
        supplementary_asset_srcs=[],
        source_original_image_src="",
        source_attachment_entries=[],
        case_evidence_reproduction_url="",
        url_overview="完整來源已保存，現在可先閱讀與核對。",
        relationship="它讓閱讀與補圖成為先後分開的工作。",
        insight="完整來源及知識分析可先形成可追溯的閱讀成果。",
        scenes=["需要視覺整理時才明示補做資訊圖。"],
        category_judgments={},
        vault_note_sections=[],
        source_overview_heading="這份文字講甚麼",
        validated_source_input_kind="user_provided_text",
        validated_source_identity=IDENTITY,
        research_reuse=[],
        method_verification={},
    )
    assert "https://example.test/text-first" in page_html
    assert "🧠 知識點" in page_html
    assert "知識點與資訊圖重建" not in page_html
    assert "文字優先" not in page_html
    assert "尚未製作資訊圖" not in page_html
    assert "待補圖" not in page_html
    assert "$brain-infographic KS-VS-TEXT-FIRST" in page_html
    assert "為這頁製作資訊圖" in page_html
    assert "data-infographic-command" in page_html
    assert "navigator.clipboard.writeText" in page_html
    coverage = generator.coverage_audit_block(source_fields, 0, 1)
    assert coverage == ""

    with tempfile.TemporaryDirectory(dir=os.environ.get("CREATIVE_SECOND_BRAIN_TEST_TMP") or None) as temporary:
        vault = Path(temporary) / "vault"
        source_note = vault / "sources" / "2026" / "10" / f"{SOURCE_ID}_fixture.md"
        page = vault / "outcome-pages" / f"{SOURCE_ID}_outcome.html"
        source_note.parent.mkdir(parents=True)
        page.parent.mkdir(parents=True)
        source_note.write_text(
            "---\nasset_mode: knowledge_only\npng_semantic_validation_status: skipped\n"
            "png_reverse_review_status: skipped\n---\n\n# 完整文字來源\n",
            encoding="utf-8",
        )
        page.write_text(page_html, encoding="utf-8")
        report = audit.audit_page(page, vault)
        assert report["asset_issues"] == []
        assert audit.infographic_source_status_issues(page, vault) == []
        assert audit.missing_assets("", page, vault, asset_mode="knowledge_only") == []
        assert audit.missing_assets("", page, vault) == ["missing infographic image"]
        actual_missing = audit.missing_assets('<img src="assets/missing.png">', page, vault, asset_mode="knowledge_only")
        assert actual_missing == ["missing image file: assets/missing.png"]

    registry = json.loads((ROOT / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    assert registry["fix"]["entry"].endswith("rail inspect --job-id <JOB_ID>")
    assert "add-infographic" not in registry["fix"]["entry"]
    assert "正式入庫後" in registry["text"]["purpose"]
    assert "原圖" in registry["image"]["purpose"] and "畫圖" in registry["image"]["purpose"]
    assert registry["infographic"]["entry"].endswith("rail add-infographic --source-id <知識頁ID>")
    assert registry["infographic"]["host_prompt"] == "$brain-infographic <知識頁ID>"
    assert "可選資訊圖" in registry["infographic"]["purpose"]
    payloads = setup_workspace.skill_entry_payloads("public-portable")
    assert "資訊圖不阻礙文字成果" in payloads[".agents/skills/brain-text/SKILL.md"].decode("utf-8")
    fix_skill = payloads[".agents/skills/brain-fix/SKILL.md"].decode("utf-8")
    assert "rail inspect --job-id <JOB_ID>" in fix_skill
    assert "$brain-fix INGEST-123 的閱讀頁打不開，請修復" in fix_skill
    assert "不得轉為補圖" in fix_skill
    assert "brain-infographic" in fix_skill
    infographic_skill = payloads[".agents/skills/brain-infographic/SKILL.md"].decode("utf-8")
    assert "rail add-infographic --source-id <知識頁ID>" in infographic_skill
    assert "文字內容已完成，不會因未製圖而受阻" in infographic_skill
    assert "已有合格圖只交付現有成果，不自動畫" in infographic_skill
    assert "不是畫圖或補圖" in payloads[".agents/skills/brain-image/SKILL.md"].decode("utf-8")
    print(json.dumps({"status": "passed", "checks": ["reader", "audit", "entries"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
