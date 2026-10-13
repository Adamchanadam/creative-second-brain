"""Prove every public entry follows the current command contract."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
ENTRIES = (
    ("guide", "brain-guide", 1, "入口", "首頁／版本"),
    ("url", "brain-url", 2, "收集", "文章／論文／影片字幕"),
    ("text", "brain-text", 3, "收集", "完整文字"),
    ("image", "brain-image", 4, "收集", "原圖"),
    ("think", "brain-think", 5, "使用", "思考並保存"),
    ("create", "brain-create", 6, "使用", "用知識創作"),
    ("grow", "brain-grow", 7, "探索", "評估來源"),
    ("research", "brain-research", 8, "探索", "研究主題"),
    ("resume", "brain-resume", 9, "維護", "接續工作"),
    ("check", "brain-check", 10, "維護", "核對成果"),
    ("fix", "brain-fix", 11, "維護", "修復問題"),
    ("tidy", "brain-tidy", 12, "維護", "整理目錄"),
    ("nebula", "brain-nebula", 13, "維護", "知識星雲"),
    ("update", "brain-update", 14, "維護", "安全更新"),
)
CONTRACT_PROMPTS = {
    "think": "$brain-think [問題]", "url": "$brain-url [網址]", "text": "$brain-text\n[貼上原文]",
    "image": "$brain-image [附上一張圖片]", "grow": "$brain-grow [來源]", "create": "$brain-create [想做的成品]",
    "research": "$brain-research 查看目前研究主題", "tidy": "$brain-tidy", "guide": "$brain-guide", "nebula": "$brain-nebula",
    "check": "$brain-check [來源或工作編號]", "resume": "$brain-resume [來源或工作編號]",
    "fix": "$brain-fix [哪裏有甚麼問題]", "update": "$brain-update",
}


def run(*items: str, cwd: Path) -> str:
    result = subprocess.run([PYTHON, "-B", *items], cwd=cwd, text=True,
                            encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode:
        raise AssertionError("command failed: " + " ".join(items) + "\n" + result.stdout + result.stderr)
    return result.stdout


def assert_page_prompts(page: Path, expected: dict[str, str]) -> None:
    soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
    fields = soup.select('textarea[id^="daily-"]')
    assert len(fields) == len(ENTRIES)
    assert [field["id"].removeprefix("daily-") for field in fields] == [key for key, *_ in ENTRIES]
    for key, *_ in ENTRIES:
        field = soup.select_one("#daily-" + key)
        assert field is not None and field.get_text() == expected[key], key


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    work = args.work_root.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    work.mkdir(parents=True, exist_ok=True)

    if args.package:
        package = args.package.resolve()
    else:
        build = work / "build"
        run(str(ROOT / "scripts" / "build_profiles.py"), "--output", str(build), "--profile", "public-portable", cwd=ROOT)
        package = build / "public-portable"
    registry_document = json.loads((package / "spec" / "command-registry.json").read_text(encoding="utf-8"))
    version = json.loads((package / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    assert isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert "version" not in registry_document
    registry = registry_document["commands"]
    for key, skill, order, group, label in ENTRIES:
        definition = registry[key]
        assert definition["host_prompt"] == CONTRACT_PROMPTS[key], skill
        assert (definition.get("menu_order"), definition.get("menu_group"), definition.get("menu_label")) == (order, group, label), skill

    assert_page_prompts(package / "index.html", CONTRACT_PROMPTS)
    assert (package / "assets" / "setup" / "obsidian-cli-1.13.7-zh-tw.png").is_file()
    package_guide = BeautifulSoup((package / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert "設定 → 關於 → 進階" in package_guide.get_text(" ", strip=True)
    assert package_guide.select_one('#obsidian-cli-example[src="assets/setup/obsidian-cli-1.13.7-zh-tw.png"]')

    workspace = work / "my-brain"
    run(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable",
        "--workspace", str(workspace), "--vault", "My Notes", "--display-name", "Parity User", cwd=work)
    home_soup = BeautifulSoup((workspace / "index.html").read_text(encoding="utf-8"), "html.parser")
    fields = home_soup.select('textarea[id^="public-command-"]')
    assert len(fields) == len(ENTRIES)
    assert [field["id"].removeprefix("public-command-") for field in fields] == [key for key, *_ in ENTRIES]
    assert [item.get_text(strip=True) for item in home_soup.select(".command-card strong")] == [
        "$brain-guide", "$brain-think", "$brain-nebula",
    ]
    for key, skill, order, group, label in ENTRIES:
        field = home_soup.select_one("#public-command-" + key)
        assert field is not None and field.get_text() == CONTRACT_PROMPTS[key], skill
        metadata = workspace / ".agents" / "skills" / skill / "agents" / "openai.yaml"
        content = metadata.read_text(encoding="utf-8")
        expected_title = f"{order:02d} {group}｜/{skill}｜{label}"
        assert metadata.is_file() and json.dumps(CONTRACT_PROMPTS[key], ensure_ascii=False) in content, skill
        assert "display_name: " + json.dumps(expected_title, ensure_ascii=False) in content, skill
        assert "display_name: " + json.dumps(registry[key]["home_title"], ensure_ascii=False) not in content, skill
    alias = (workspace / ".agents" / "skills" / "brain-ask" / "SKILL.md").read_text(encoding="utf-8")
    assert "舊名相容" in alias and "brain-think" in alias

    print(json.dumps({"status": "passed", "coverage": [
        "14 fixed public entry prompts", "package guide fields", "installed workspace fields",
        "installed skill metadata", "portable Obsidian setup image",
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
