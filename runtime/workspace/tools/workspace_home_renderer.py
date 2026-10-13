"""The one portable renderer for a configured workspace's daily home."""
from __future__ import annotations

import html
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any


def _daily_home(runtime: Path):
    source = runtime / "tools" / "daily_home.py"
    source_parent = str(source.parent)
    if source_parent not in sys.path:
        sys.path.insert(0, source_parent)
    spec = importlib.util.spec_from_file_location("creative_second_brain_daily_home", source)
    if not spec or not spec.loader:
        raise ValueError("daily home renderer is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_entries(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) != 14:
        raise ValueError("workspace entry contract is invalid")
    required = {"key", "skill", "home_title", "purpose", "host_prompt"}
    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in value:
        if not isinstance(row, dict) or set(row) != required:
            raise ValueError("workspace entry contract is invalid")
        entry = {key: str(row[key]) for key in required}
        if not entry["key"] or entry["key"] in seen or not entry["skill"].startswith("brain-"):
            raise ValueError("workspace entry contract is invalid")
        seen.add(entry["key"])
        entries.append(entry)
    if seen != {"think", "url", "text", "image", "grow", "create", "research", "tidy", "guide", "nebula", "check", "resume", "fix", "update"}:
        raise ValueError("workspace entry contract is invalid")
    return entries


def render(*, runtime: Path, workspace: Path, vault_rel: str, display_name: str,
           package_version: str, entries: object) -> Path:
    """Render the same daily entry page for setup, managed adoption, and guide."""
    daily_home = _daily_home(runtime)
    page = daily_home.render(
        assets=runtime / "assets" / "guide",
        vault_rel=vault_rel,
        profile="portable",
        package_version=package_version,
    )
    title = html.escape(display_name.strip()) + " 的第二大腦"
    if not re.search(r"<h1>.*?</h1>", page, re.S):
        raise ValueError("daily-home renderer shape changed")
    page = re.sub(r"<title>.*?</title>", "<title>" + title + "</title>", page, count=1, flags=re.S)
    rows = []
    entry_rows = validate_entries(entries)
    think_alias = daily_home.think_alias_markup(runtime / "assets" / "guide")
    for definition in entry_rows:
        key = definition["key"]
        heading = html.escape(definition["home_title"])
        purpose = html.escape(definition["purpose"])
        prompt = html.escape(definition["host_prompt"])
        alias = think_alias if key == 'think' else ''
        rows.append(
            '<details class="prompt" id="p-' + key + '" data-skill="' + html.escape(definition["skill"], quote=True) + '"><summary>' + heading + '</summary>'
            '<div class="prompt-body"><p>' + purpose + '</p>' + alias + '<p>在已開啟這個工作區的 Agent 對話，複製以下指令，再補上方括號內的材料或問題：</p>'
            '<textarea id="public-command-' + key + '" name="public-command-' + key + '" readonly aria-label="' + heading + '指令">' + prompt + '</textarea>'
            '<button class="copy" type="button">複製指令</button><span class="status-line" role="status" aria-live="polite"></span>'
            '</div></details>'
        )
    page = re.sub(r'<div id="commands">.*?</div><p id="empty"', '<div id="commands">' + "".join(rows) + '</div><p id="empty"', page, count=1, flags=re.S)
    page = re.sub(
        r'(<span id="count"(?:\s+[^>]*)?>)\d+ 項(</span>)',
        lambda match: match.group(1) + str(len(entry_rows)) + " 項" + match.group(2),
        page,
        count=1,
    )
    page = re.sub(
        r'<section id="help"[^>]*>.*?</section>',
        '<section id="help" class="help-panel"><h2>設定與說明</h2><p>這是你的完整第二大腦入口。選擇相應功能後，AI 會按完成條件讀回成果。</p>'
        '<p>要改設定、備份、搬移或接續時，直接告訴 AI 你想做的事；它會先檢查目前工作區，再說明真正需要你確認的動作。</p>'
        '<p><a href="#prompts">開啟全部操作</a> · <a href="#main">回到工作首頁 ↑</a></p></section>',
        page,
        count=1,
        flags=re.S,
    )
    destination = workspace / "index.html"
    destination.write_text(page, encoding="utf-8", newline="\n")
    return destination
