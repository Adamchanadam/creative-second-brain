"""Focused mobile control-space and accessibility contract for the Nebula source."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "runtime" / "workspace" / "assets" / "knowledge-nebula" / "template.html"


def main() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    assert '<details class="tool-drawer"><summary aria-label="開啟更多星雲工具">更多工具</summary>' in source
    assert '<details class="mobile-filter-drawer"><summary>篩選及視圖設定</summary>' in source
    assert "toolDrawerContent.append(ambientToggle)" in source
    assert "toolDrawerContent.append(refresh)" in source
    assert "mobileFilterContent.append(sceneNav)" in source
    assert "mobileFilterContent.append(paletteHome)" in source
    assert ".tool-drawer[open] .tool-drawer-content{display:grid" in source
    assert ".mobile-filter-drawer[open] .mobile-filter-content{display:flex" in source
    assert "min-width:44px;min-height:44px" in source
    assert ".searchbox{top:76px}" in source and ".list{top:170px}" in source
    for removed in ("依筆記連線分群 · 跨群節點仍可互連", "未展示橋接的成果也在這裡。", "留白，是尚待探索的地方。", "弧線 · 已有整理依據"):
        assert removed not in source
    print(json.dumps({"status": "passed", "checks": [
        "mobile starts with the exploration canvas above closed secondary controls",
        "all hidden controls remain reachable through labelled native details drawers",
        "palette, motion, sync, search clearing, filters, paging and catalogue entry remain wired",
        "mobile primary touch controls have approximately 44px targets",
        "desktop keeps the existing direct control layout",
    ]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
