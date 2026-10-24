"""The one portable renderer for a configured workspace's daily home."""
from __future__ import annotations

import html
import importlib.util
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

try:
    from .knowledge_direction import compute_direction
except ImportError:
    from knowledge_direction import compute_direction  # type: ignore


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
    if not isinstance(value, list) or len(value) != 15:
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
    if seen != {"think", "url", "text", "image", "infographic", "grow", "create", "research", "tidy", "guide", "nebula", "check", "resume", "fix", "update"}:
        raise ValueError("workspace entry contract is invalid")
    return entries


DIRECTION_EMPTY_LINE = "累積更多入庫後，這裏會顯示知識方向"
DIRECTION_UNAVAILABLE_LINE = "知識方向暫時讀不到，請讓 AI 檢查知識庫的登記資料"
DIRECTION_BLOCKS = (
    ("evidence_building", "證據累積", "多個來源談到、而且互相支持的概念"),
    ("tension_hotspot", "分歧值得研究", "同時有支持與反例或限制的概念"),
    ("recent_growth", "近期增長", "最近有新跨來源連結的概念"),
    ("isolated_new", "剛入庫的概念", "目前只有一個來源、還沒有連結的新概念"),
)
DIRECTION_STYLE = (
    "<style>#direction .direction-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}"
    "#direction .direction-grid article{padding:14px;border:1px solid var(--line);border-radius:14px;background:rgba(255,255,255,.04)}"
    "#direction h3{margin:0 0 4px;font-size:1rem}#direction .direction-grid p,#direction li{color:var(--muted);font-size:.86rem;line-height:1.6}"
    "#direction ul{margin:8px 0 0;padding-left:1.2em}#direction li a{color:var(--gold)}"
    "@media (max-width:760px){#direction .direction-grid{grid-template-columns:1fr}}</style>"
)


def direction_section(vault: Path, vault_rel: str) -> str:
    """The home's knowledge-direction block: the signals computed by knowledge_direction, nothing decided here."""
    head = '<section id="direction" class="help-panel" aria-labelledby="direction-heading"><h2 id="direction-heading">知識方向</h2>'
    try:
        direction = compute_direction(vault)
    except ValueError:
        return head + "<p>" + html.escape(DIRECTION_UNAVAILABLE_LINE) + "</p></section>"
    if not direction["cross_source_relation_count"]:
        return head + "<p>" + html.escape(DIRECTION_EMPTY_LINE) + "</p></section>"
    base = quote(vault_rel.strip("/"), safe="/")
    blocks = []
    for key, title, note in DIRECTION_BLOCKS:
        items = direction["signals"][key]
        if not items:
            continue
        rows = "".join(
            '<li><a href="' + html.escape(base + "/" + quote(item["concept_path"], safe="/"), quote=True) + '">'
            + html.escape(item["concept_name"]) + "</a>：" + html.escape(item["text"]) + "</li>" for item in items)
        blocks.append("<article><h3>" + html.escape(title) + "</h3><p>" + html.escape(note) + "</p><ul>" + rows + "</ul></article>")
    body = '<div class="direction-grid">' + "".join(blocks) + "</div>" if blocks else "<p>" + html.escape(DIRECTION_EMPTY_LINE) + "</p>"
    return DIRECTION_STYLE + head + "<p>由已核對的跨來源關係推算，不是 AI 臨時判斷。</p>" + body + "</section>"


def document(*, runtime: Path, workspace: Path, vault_rel: str, display_name: str,
             package_version: str, entries: object) -> str:
    """Build the same daily entry page for setup, managed adoption, and guide."""
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
    marker = '<section id="prompts"'
    if page.count(marker) != 1:
        raise ValueError("daily-home renderer shape changed")
    page = page.replace(marker, direction_section(workspace / vault_rel, vault_rel) + marker, 1)
    return page


def render(*, runtime: Path, workspace: Path, vault_rel: str, display_name: str,
           package_version: str, entries: object) -> Path:
    """Render the same daily entry page for setup, managed adoption, and guide."""
    page = document(
        runtime=runtime,
        workspace=workspace,
        vault_rel=vault_rel,
        display_name=display_name,
        package_version=package_version,
        entries=entries,
    )
    destination = workspace / "index.html"
    destination.write_text(page, encoding="utf-8", newline="\n")
    return destination
