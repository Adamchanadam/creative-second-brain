"""Focused reader UX checks for the optional infographic action."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "workspace" / "tools"))

import outcome_page_generator as generator


def render_fixture(*, asset_mode: str, asset_srcs: list[str], source_original_image_src: str = "") -> str:
    return generator.render_html(
        source_id="KS-VS-0057",
        source_fields={
            "title": "提取與交錯練習",
            "reader_title": "提取與交錯練習",
            "summary": "完整來源已保存，可正常閱讀與搜尋。",
            "source_url": "https://example.test/ies",
            "asset_mode": asset_mode,
        },
        app_fields={},
        knowledge_points=[{"knowledge_point": "先提取再核對", "definition": "低壓回想可用來練習提取。"}],
        asset_srcs=asset_srcs,
        supplementary_asset_srcs=[],
        source_original_image_src=source_original_image_src,
        case_evidence_reproduction_url="",
        url_overview="來源內容可供閱讀與核對。",
        relationship="視覺整理是可選的後續工作。",
        insight="完整文字不依賴資訊圖才可閱讀。",
        scenes=[],
        category_judgments={},
        vault_note_sections=[],
        source_overview_heading="這份來源講甚麼",
        validated_source_input_kind="user_provided_image" if source_original_image_src else "user_provided_text",
        validated_source_identity="user_provided_image:fixture" if source_original_image_src else "user_provided_text:fixture",
        research_reuse=[],
        method_verification={},
    )


def main() -> int:
    action = generator.optional_infographic_action("KS-VS-0057", "knowledge_only")
    assert "$brain-infographic KS-VS-0057" in action
    assert "為這頁製作資訊圖" in action
    assert "data-infographic-command" in action
    assert generator.optional_infographic_action("KS-VS-0057", "formal_infographic") == ""

    text_page = render_fixture(asset_mode="knowledge_only", asset_srcs=[])
    assert text_page.count("optional-infographic-action") >= 1
    assert "top-no-media" in text_page
    assert "navigator.clipboard.writeText" in text_page
    for forbidden in ("文字優先", "尚未製作資訊圖", "待補圖", "閱讀狀態"):
        assert forbidden not in text_page

    original_image_page = render_fixture(
        asset_mode="knowledge_only",
        asset_srcs=[],
        source_original_image_src="assets/original-source.png",
    )
    assert 'src="assets/original-source.png"' in original_image_page
    assert "原始圖片證據" in original_image_page
    assert "$brain-infographic KS-VS-0057" in original_image_page

    imaged_page = render_fixture(asset_mode="formal_infographic", asset_srcs=["assets/formal.png"])
    assert '<img src="assets/formal.png"' in imaged_page
    assert "$brain-infographic KS-VS-0057" not in imaged_page
    assert "為這頁製作資訊圖" not in imaged_page
    print("optional infographic reader action: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
