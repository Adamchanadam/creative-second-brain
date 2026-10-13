"""Read-only regression checks for the first-install instructions in r7."""
from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    args = parser.parse_args()
    delivery = args.delivery.absolute()
    guide = text(delivery / "creative-second-brain" / "index.html")
    prompt = text(ROOT / "install-prompt.txt")
    first_use = text(ROOT / "references" / "first-use.md")
    requirements = [line.strip() for line in text(ROOT / "runtime" / "workspace" / "requirements.txt").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    assert len(requirements) == 4
    assert {value.split("==", 1)[0] for value in requirements} == {"Pillow", "beautifulsoup4", "yt-dlp[default]", "tzdata"}

    registry = json.loads(text(ROOT / "spec" / "command-registry.json"))["commands"]
    canonical = [name for name, entry in sorted(registry.items(), key=lambda item: item[1].get("menu_order", 999))
                 if "menu_order" in entry and not entry.get("alias_for")]
    assert canonical == ["guide", "url", "text", "image", "think", "create", "grow", "research", "resume", "check", "fix", "tidy", "nebula", "update"]
    rendered = re.findall(r'<textarea id="daily-([^\"]+)"[^>]*>(.*?)</textarea>', guide, flags=re.DOTALL)
    assert [name for name, _ in rendered] == canonical
    assert [html.unescape(value).strip() for _, value in rendered] == [registry[name]["host_prompt"] for name in canonical]
    for name in canonical:
        assert f"$brain-{name}" in prompt
    assert "教學頁列出的 `$brain-*` 入口" in first_use
    assert registry["ask"].get("alias_for") == "think"
    update_tool = delivery / "creative-second-brain" / "runtime" / "workspace" / "tools" / "brain_update.py"
    assert update_tool.is_file()
    for document in (prompt, first_use):
        assert "<package>/runtime/workspace/tools/brain_update.py" in document
        assert "v1.9.15" in document

    for document in (prompt, first_use, guide):
        assert "requirements.txt 唯一列出的 Pillow、beautifulsoup4、`yt-dlp[default]` 與 tzdata" in document
        for value in requirements:
            assert value not in document
        assert "先唯讀比對" in document
        assert "在已確認的更新範圍內才更新專用 venv" in document
        assert "只有超出原同意範圍才詢問" in document
    for document in (prompt, first_use, guide):
        assert "--skip-download" in document
        assert "不下載影片" in document
        assert "不把描述／摘要當字幕" in document
        assert "正式資訊圖生成未就緒" in document
    for phrase in (
        "若未附位置或不能存取，先集中問我必要的套件位置",
        "不可先寫 workspace",
        "只有 ZIP 時可核對內部完整性",
    ):
        assert phrase in prompt and phrase in guide
    assert "node node_modules/playwright/cli.js install chromium" in prompt
    assert "node node_modules/playwright/cli.js install chromium" in first_use
    print(json.dumps({"status": "passed", "coverage": ["four locked Python requirements including timezone data", "VTT-only policy", "package-location fail-closed prompt", "ImageGen missing exact label", "registry and generated 14-command parity"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
