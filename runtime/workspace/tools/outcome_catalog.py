"""Self-contained, paginated reader catalog for knowledge outcomes and thinking records."""

from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


CATALOG_PAGES = ("index.html", "knowledge.html", "thinking.html")
READER_LABELS = {"thinking": "完整論證"}

# Card layout contract: a preview image is an optional extra, never the skeleton.
# Every card owns a fixed type column on the left; the thumbnail column exists only
# for a card that has a preview, so a card without one keeps no empty slot.
TYPE_RAIL_WIDTH_PX = 44
PREVIEW_WIDTH_PX = 168
CARD_MOBILE_MAX_WIDTH_PX = 600

THINKING_TYPE = "thinking"
GENERIC_TYPE = "generic"
# Existing data field -> type column icon.  ``source_input_kind`` is the only
# type signal the catalogue row already carries; anything else is generic.
SOURCE_TYPE_BY_INPUT_KIND = {"url": "web", "user_provided_text": "text", "user_provided_image": "image"}
# A saved URL on one of these hosts is shown as video (the row's own source URL is the only evidence).
VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "bilibili.com")
_ICON_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
# Inline, dependency-free icons: 24 px grid, 1.6 px round strokes, drawn like the nebula icon set.
SOURCE_TYPES = {
    "web": {"label": "網頁", "svg": _ICON_OPEN + '<circle cx="12" cy="12" r="8.5"/><path d="M3.5 12h17M12 3.5c2.6 2.4 3.8 5.3 3.8 8.5s-1.2 6.1-3.8 8.5c-2.6-2.4-3.8-5.3-3.8-8.5S9.4 5.9 12 3.5z"/></svg>'},
    "video": {"label": "影片", "svg": _ICON_OPEN + '<rect x="3" y="5.5" width="18" height="13" rx="3.2"/><path d="M10.2 9.4l4.6 2.6-4.6 2.6z"/></svg>'},
    "text": {"label": "文字", "svg": _ICON_OPEN + '<path d="M7 3.5h6.5L18.5 8.5V19a1.5 1.5 0 0 1-1.5 1.5H7A1.5 1.5 0 0 1 5.5 19V5A1.5 1.5 0 0 1 7 3.5zM13.5 3.5v5h5M8.5 12.5h7M8.5 16h5"/></svg>'},
    "image": {"label": "圖片", "svg": _ICON_OPEN + '<rect x="3.5" y="4.5" width="17" height="15" rx="2.6"/><circle cx="9" cy="10" r="1.6"/><path d="M4 17l5-4.4 3.4 3 3-2.6 4.6 4"/></svg>'},
    THINKING_TYPE: {"label": "思考", "svg": _ICON_OPEN + '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg>'},
    GENERIC_TYPE: {"label": "來源", "svg": _ICON_OPEN + '<path d="M7 4.5h10a1 1 0 0 1 1 1V20l-6-4-6 4V5.5a1 1 0 0 1 1-1z"/></svg>'},
}


def write_catalog_pages(
    entries: list[dict], output_dir: Path, page_size: int, thinking: list[dict] | None = None, *, vault: Path | None = None,
    research_topics: list[dict] | None = None, research_materials: list[dict] | None = None,
    research_snapshot: str | None = None,
) -> list[Path]:
    """Write the overview and two catalogue surfaces.

    Knowledge rows accept the schema emitted by
    ``outcome_page_generator.outcome_index_entry``. Nothing is fetched at reader time.
    """
    if page_size < 1:
        raise ValueError("page_size must be greater than 0")
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    knowledge = [_knowledge_row(entry, destination) for entry in entries]
    answers = [dict(row, url=_local_url(row['url'], destination), preview_url='', type_kind=THINKING_TYPE, needs_review=_explicit_review(row['status']) or bool(row.get('warnings'))) for row in thinking or []]
    research = _research_overview(research_topics or [], research_materials or [], destination, research_snapshot)
    payloads = (
        _overview_html(knowledge, page_size, answers, research),
        _catalog_html("知識成果", "knowledge.html", "knowledge", knowledge, page_size, research),
        _catalog_html("思考紀錄", "thinking.html", "thinking", answers, page_size),
    )
    paths = [destination / name for name in CATALOG_PAGES]
    try:
        from .reader_shell import apply_theme, atomic_reader_write, catalogue_projection_lock
    except ImportError:
        from reader_shell import apply_theme, atomic_reader_write, catalogue_projection_lock
    with catalogue_projection_lock(destination):
        for path, content in zip(paths, payloads):
            content = apply_theme(content, path, vault or destination.parent, {'index':'overview'}.get(path.stem,path.stem))
            atomic_reader_write(path, content)
    return paths


def _knowledge_row(entry: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    explicit_status = _text(entry.get("status"))
    status = explicit_status or _text(entry.get("adoption"))
    warnings = entry.get("warnings") if isinstance(entry.get("warnings"), list) else []
    url = _local_url(entry.get("page_path"), output_dir)
    try:
        application_count = max(0, int(entry.get("research_application_count") or 0))
    except (TypeError, ValueError):
        application_count = 0
    return {
        "source_id": _text(entry.get("source_id")),
        "title": _text(entry.get("title")) or "未命名成果",
        "summary": _text(entry.get("summary")),
        "topic": _text(entry.get("source_input_kind")) or "未分類",
        "type_kind": _source_type(entry.get("source_input_kind"), entry.get("source_url")),
        "status": status or "未標示",
        "updated_at": _text(entry.get("sort_date")),
        "url": url,
        "preview_url": _local_url(entry.get("asset_path"), output_dir),
        "source": _text(entry.get("source_identity") or entry.get("source_url")),
        "source_url": _safe_external_url(entry.get("source_url")),
        "source_url_label": _text(entry.get("source_url_label")) or "打開原 URL",
        "warnings": [_text(warning) for warning in warnings if _text(warning)],
        "needs_review": _explicit_review(explicit_status) or bool(warnings),
        "research_topic_ids": _string_list(entry.get("research_topic_ids")),
        "research_facets": _research_facets(entry.get("research_facets")),
        "research_application_count": application_count,
        "application_preview_url": f"{url}#research-applications" if url and application_count else "",
    }




def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _source_type(input_kind: Any, source_url: Any = "") -> str:
    """Type column key for a knowledge row; an unknown input kind is always ``generic``."""
    kind = SOURCE_TYPE_BY_INPUT_KIND.get(_text(input_kind), GENERIC_TYPE)
    if kind == "web" and _is_video_url(source_url):
        return "video"
    return kind


def _is_video_url(value: Any) -> bool:
    try:
        host = (urlparse(_text(value)).hostname or "").lower()
    except ValueError:
        return False
    return any(host == known or host.endswith("." + known) for known in VIDEO_HOSTS)




def _explicit_review(status: str) -> bool:
    return status.casefold() in {"error", "needs_review", "blocked"}


def _string_list(value: Any) -> list[str]:
    values = value if isinstance(value, list) else []
    return list(dict.fromkeys(_text(item) for item in values if _text(item)))


def _research_facets(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    return {key: _string_list(items) for key, items in value.items() if _text(key) and _string_list(items)}


_RESEARCH_FIELD_LABELS = {
    "daily_case_cap": "每日上限",
    "total_case_cap": "總案例上限",
    "case_cap": "案例上限",
    "search_cap": "搜尋上限",
    "searches": "每次搜尋上限",
    "candidate_summaries_target": "候選摘要上限",
    "deep_reads": "深讀上限",
    "deep_read_cap": "深讀上限",
    "cadence": "節奏",
    "cost": "成本",
    "domain": "領域",
    "collects": "收集重點",
    "excludes": "不包括",
    "source_input_kinds": "來源形式",
    "source_profiles": "來源類別",
    "media_boundary": "媒體邊界",
    "asset_mode": "證據形式",
    "frequency": "頻率",
    "time": "時間",
    "timezone": "時區",
    "in_scope": "包括",
    "out_of_scope": "不包括",
    "included": "包括",
    "excluded": "不包括",
    "primary": "主要",
    "secondary": "次要",
    "notes": "備註",
}


def _research_text(value: Any) -> str:
    """Render API structures as reader text, never their Python repr."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "、".join(part for part in (_research_text(item) for item in value) if part)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _research_text(item)
            if text:
                parts.append(f"{_RESEARCH_FIELD_LABELS.get(str(key), str(key))}：{text}")
        return "；".join(parts)
    return _text(value)


def _research_state(value: Any) -> str:
    return {
        "active": "已啟用",
        "paused": "已暫停",
        "missing": "未設定",
        "not_configured": "未設定",
        "pending": "待處理",
        "none": "暫無待處理",
        "registered": "已登記",
        "unknown": "未知",
    }.get(_text(value).casefold(), _research_text(value) or "未知")


def _research_mode(value: Any) -> str:
    return {
        "scheduled_research": "持續研究",
        "continuous": "持續研究",
        "one_off": "一次性研究",
        "manual": "按要求執行",
    }.get(_text(value).casefold(), _research_text(value) or "未知")


def _research_reason(value: Any) -> str:
    text = _research_text(value)
    known = {
        "topic mode is manual_research": "主題未設為持續研究",
        "no reserved or committing work": "沒有預留或寫入中的工作",
        "reserved or committing work exists": "有預留或寫入中的工作",
        "no configured automation.toml was found": "找不到已設定的本機排程紀錄",
        "scheduler identity is absent or ambiguous": "排程身分缺失或不明確",
        "matching automation status is PAUSED": "相符排程目前已暫停",
        "matching automation status is ACTIVE": "相符排程目前已啟用",
        "runner readiness only": "僅表示執行準備，未表示排程已啟用",
    }
    return known.get(text, text if re.search(r"[\u4e00-\u9fff]", text) else "詳細原因未能讀回")


def _research_plan(schedule: Any, daily_target: Any, hard_cap: Any) -> str:
    contract = schedule if isinstance(schedule, dict) else {}
    time = _text(contract.get("time"))
    timezone = _text(contract.get("timezone"))
    frequency = _text(contract.get("frequency") or contract.get("cadence"))
    frequency_label = {"daily": "每日", "weekly": "每週", "monthly": "每月"}.get(frequency.casefold(), frequency or "未知")
    timing = "未知" if not time else time + (f"（{timezone}）" if timezone else "")
    parts = [f"原訂時間：{timing}", f"頻率：{frequency_label}"]
    if daily_target is not None:
        parts.append(f"每日目標：{_research_text(daily_target) or '未知'} 個案例")
    else:
        parts.append("每日目標：未知")
    if hard_cap is not None:
        parts.append(f"總上限：{_research_text(hard_cap) or '未知'} 個案例")
    else:
        parts.append("總上限：未知")
    return "；".join(parts)


def _local_url(value: Any, output_dir: Path) -> str:
    """A local relative path only; catalog pages never receive remote/script URLs."""
    text = _text(value).replace("\\", "/")
    if not text or text.startswith(("/", "//")):
        return ""
    try:
        path = Path(text)
        if not path.is_absolute() and re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):
            return ""
        target = path.resolve() if path.is_absolute() else (output_dir / path).resolve()
        root = output_dir.parent.resolve()
        target.relative_to(root)
        return os.path.relpath(target, output_dir).replace("\\", "/")
    except (OSError, ValueError):
        return ""


def _safe_external_url(value: Any) -> str:
    text = _text(value)
    return text if text.startswith(("https://", "http://")) else ""


def _research_overview(
    topics: list[dict[str, Any]], materials: list[dict[str, Any]], output_dir: Path, snapshot: str | None,
) -> dict[str, Any]:
    material_rows = [row for row in materials if isinstance(row, dict)]
    prepared: list[dict[str, Any]] = []
    active = attention = 0
    for raw in topics:
        if not isinstance(raw, dict):
            continue
        topic_id = _text(raw.get("topic_id"))
        if not topic_id:
            continue
        topic_materials = [
            row for row in material_rows
            if topic_id in _string_list(row.get("topic_ids"))
        ]
        source_ids = { _text(row.get("source_id")) for row in topic_materials if row.get("material_kind") == "source" and _text(row.get("source_id")) }
        applications = [row for row in topic_materials if row.get("material_kind") == "application"]
        profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else {}
        scheduler = raw.get("scheduler") if isinstance(raw.get("scheduler"), dict) else {}
        runner = raw.get("runner") if isinstance(raw.get("runner"), dict) else {}
        work = raw.get("work") if isinstance(raw.get("work"), dict) else {}
        status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
        scheduler_state = _text(scheduler.get("state")) or "unknown"
        work_state = _text(work.get("state")) or "unknown"
        overall = _text(status.get("overall")) or "unknown"
        schedule_contract = raw.get("schedule_contract") if isinstance(raw.get("schedule_contract"), dict) else {}
        daily_target = raw.get("daily_target", raw.get("daily_case_cap"))
        hard_cap = raw.get("hard_cap", raw.get("total_case_cap"))
        scheduler_active = scheduler_state == "active" and status.get("scheduler_active") is True
        if scheduler_active:
            active += 1
        if scheduler_state in {"paused", "missing", "not_configured", "unknown"} or work_state in {"pending", "unknown"}:
            attention += 1
        latest = raw.get("latest_receipt") if isinstance(raw.get("latest_receipt"), dict) else {}
        links: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        for material in topic_materials:
            url = _research_local_url(material.get("outcome_page_path"), output_dir)
            source_id = _text(material.get("source_id"))
            if url and url not in seen_urls:
                links.append({"label": f"開啟 {source_id or '成果'}", "url": url})
                seen_urls.add(url)
        budget = raw.get("budget") if isinstance(raw.get("budget"), dict) else profile.get("budget") if isinstance(profile.get("budget"), dict) else {}
        budget_text = _research_text(budget)
        limits = "；".join(
            value for value in (
                _research_reason(scheduler.get("reason")), _research_reason(work.get("reason")), budget_text,
            ) if value
        ) or "未知"
        execution = "；".join(
            value for value in (
                f"模式：{_research_mode(raw.get('mode') or profile.get('mode'))}",
                f"執行準備：{'可執行' if runner.get('ready') is True else '未就緒／未知'}",
                f"排程：{_research_state(scheduler_state)}",
                f"工作：{_research_state(work_state)}",
                f"整體狀態：{_research_state(overall)}",
                _research_reason(runner.get("reason")),
            ) if value
        )
        latest_result = _text(latest.get("run_id")) or _text(latest.get("path"))
        if not latest_result:
            for material in topic_materials:
                receipt = material.get("latest_receipt") if isinstance(material.get("latest_receipt"), dict) else {}
                receipt_path = _text(receipt.get("path"))
                if receipt_path:
                    latest_result = f"{_text(material.get('source_id')) or '成果'}：正式憑證 {receipt_path}"
                    break
        latest_result = latest_result or "未有可讀回結果"
        prepared.append({
            "topic_id": topic_id,
            "display_name": _text(raw.get("display_name")) or topic_id,
            "purpose": _research_text(raw.get("purpose") if raw.get("purpose") is not None else profile.get("purpose")) or "未知",
            "scope": _research_text(raw.get("scope") if raw.get("scope") is not None else profile.get("scope")) or "未知",
            "focus": _research_text(raw.get("focus") if raw.get("focus") is not None else profile.get("focus")) or "未知",
            "execution": execution,
            "plan": _research_plan(schedule_contract, daily_target, hard_cap),
            "last_checked": _research_text(raw.get("checked_at")) or _research_text(snapshot) or "未知",
            "counts": {"sources": len(source_ids), "applications": len(applications)},
            "latest_result": latest_result,
            "limits": limits,
            "links": links,
            "scheduler_state": scheduler_state,
            "work_state": work_state,
            "overall": overall,
        })
    return {
        "snapshot_at": _text(snapshot),
        "counts": {"total": len(prepared), "active": active, "attention": attention},
        "topic_options": [{"id": row["topic_id"], "label": row["display_name"]} for row in prepared],
        "topics": prepared,
    }


def _research_local_url(value: Any, output_dir: Path) -> str:
    text = _text(value).replace("\\", "/")
    if not text or text.startswith(("/", "//")) or ".." in text.split("/") or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text):
        return ""
    try:
        target = (output_dir.parent / text).resolve()
        target.relative_to(output_dir.parent.resolve())
        return os.path.relpath(target, output_dir).replace("\\", "/")
    except (OSError, ValueError):
        return ""




def _overview_html(knowledge: list[dict[str, Any]], page_size: int, answers: list[dict], research: dict[str, Any]) -> str:
    payload = {
        "kind": "overview",
        "items": [dict(row, catalog_kind="knowledge") for row in knowledge]
        + [dict(row, catalog_kind="thinking") for row in answers],
        "thinking": _newest(answers)[:4],
        "review_thinking": [row for row in answers if row['needs_review']][:4],
        "knowledge": _newest(knowledge)[:4],
        "review_knowledge": [row for row in knowledge if row["needs_review"]][:4],
        "counts": {"knowledge": len(knowledge), "thinking": len(answers)},
        "research": research,
        "page_size": page_size,
        "page_count": max(1, (len(knowledge) + page_size - 1) // page_size),
    }
    body = """
<header class="archive-head"><p class="eyebrow">閱讀檔案</p><h1>成果總覽</h1><p>從最近更新的知識成果與思考紀錄，回到真正需要的那一頁。</p></header>
<section class="counts" aria-label="成果數量"><a href="knowledge.html"><strong data-count="knowledge">0</strong><span>知識成果</span></a><a href="thinking.html"><strong data-count="thinking">0</strong><span>思考紀錄</span></a></section>
<section id="research-topics" class="overview-section" aria-label="研究主題"><div class="section-heading"><h2>研究主題</h2><span id="research-snapshot" class="metadata"></span></div><div class="research-counts" aria-label="研究主題數量"></div><div id="research-topic-list"></div></section>
<div id="overview" aria-live="polite"></div><noscript><p class="notice">請用上方導航開啟知識成果或思考紀錄的靜態清單。</p></noscript>
"""
    script = r'''
const CATALOG_DATA=JSON.parse(document.getElementById('catalog-data').textContent),root=document.getElementById('overview');
document.querySelector('[data-count="knowledge"]').textContent=CATALOG_DATA.counts.knowledge;document.querySelector('[data-count="thinking"]').textContent=CATALOG_DATA.counts.thinking;
const esc=value=>{const el=document.createElement('span');el.textContent=value||'';return el.innerHTML};const attr=value=>String(value||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;');const localLink=url=>url?url+(url.includes('?')?'&':'?')+'return='+encodeURIComponent('index.html'+location.search):'';
const list=(rows,kind)=>rows.length?'<ol class="compact-list">'+rows.map(row=>`<li>${row.url?`<a href="${attr(localLink(row.url))}">${esc(row.title)}</a>`:esc(row.title)}${row.summary?`<small>${esc(row.summary)}</small>`:''}</li>`).join('')+'</ol>':'<p class="empty">目前沒有可列出的項目。</p>';
const section=(title,href,rows,kind)=>`<section class="overview-section"><div class="section-heading"><h2>${title}</h2><a href="${href}">查看全部</a></div>${list(rows,kind)}</section>`;
root.innerHTML=section('最近的思考紀錄','thinking.html?sort=updated',CATALOG_DATA.thinking,'thinking')+section('待核對的思考紀錄','thinking.html?status=needs_review',CATALOG_DATA.review_thinking,'thinking')+section('最近更新的知識成果','knowledge.html?sort=updated',CATALOG_DATA.knowledge,'knowledge')+section('待檢查','knowledge.html?status=needs_review',CATALOG_DATA.review_knowledge,'knowledge');
const research=CATALOG_DATA.research||{counts:{total:0,active:0,attention:0},topics:[]},snapshot=document.getElementById('research-snapshot'),researchCounts=document.querySelector('.research-counts'),researchList=document.getElementById('research-topic-list');snapshot.textContent=research.snapshot_at?`狀態快照：${research.snapshot_at}`:'狀態快照：未知';for(const [value,label] of [[research.counts.total,'總主題'],[research.counts.active,'排程已啟用'],[research.counts.attention,'暫停／待處理']]){const item=document.createElement('p');item.className='research-count';item.textContent=`${label}：${value}`;researchCounts.append(item)}if(!research.topics.length){researchList.textContent='目前沒有可讀回的研究主題。'}for(const row of research.topics){const detail=document.createElement('details'),summary=document.createElement('summary');summary.textContent=row.display_name||row.topic_id;detail.append(summary);const fields=[['目的',row.purpose],['範圍',row.scope],['焦點',row.focus],['原訂節奏與上限',row.plan],['執行方式',row.execution],['上次核對',row.last_checked],['成果',`正式來源 ${row.counts.sources}；衍生作品／試驗 ${row.counts.applications}`],['最近結果',row.latest_result],['限制',row.limits]];for(const [label,value] of fields){if(!value)continue;const line=document.createElement('p'),strong=document.createElement('strong');strong.textContent=label+'：';line.append(strong,document.createTextNode(value));detail.append(line)}if(row.links.length){const links=document.createElement('p');links.append(document.createTextNode('連結：'));for(const link of row.links){const anchor=document.createElement('a');anchor.href=localLink(link.url);anchor.textContent=link.label;links.append(anchor,document.createTextNode(' '))}detail.append(links)}researchList.append(detail)}
'''
    return _page_shell("成果總覽", "index.html", body, payload, script)


def _catalog_html(title: str, active: str, kind: str, rows: list[dict[str, Any]], page_size: int, research: dict[str, Any] | None = None) -> str:
    topic_label = "輸入類型" if kind == "knowledge" else "類型"
    payload = {"kind": kind, "reader_label": READER_LABELS.get(kind, ""), "source_types": SOURCE_TYPES, "items": rows, "page_size": page_size, "page_count": max(1, (len(rows) + page_size - 1) // page_size), "research_topics": (research or {}).get("topic_options", []) if kind == "knowledge" else []}
    body = f"""
<header class="archive-head"><p class="eyebrow">閱讀檔案</p><h1>{html.escape(title)}</h1><p>{'找回問過的問題、完整推理與來源；這些是推理成果，不等同原始來源。' if kind == 'thinking' else '按標題、摘要或類型尋找已入庫的知識成果。'}</p></header>
<section class="controls" aria-label="{html.escape(title)}篩選"><label>搜尋<input id="query" type="search" autocomplete="off" placeholder="輸入關鍵字"></label><label>{topic_label}<select id="topic"><option value="">全部{topic_label}</option></select></label><label id="research-topic-control" hidden>研究主題<select id="research-topic"><option value="">全部研究主題</option></select></label><label id="research-facet-control" hidden>研究分類<select id="research-facet"><option value="">全部研究分類</option></select></label><label>狀態<select id="status"><option value="">全部狀態</option><option value="needs_review">待檢查</option></select></label><label>排序<select id="sort"><option value="source">來源順序</option><option value="updated">最近更新</option><option value="title">標題</option></select></label><label>每頁<select id="size"><option value="20">20 項</option><option value="50">50 項</option></select></label><button id="clear" type="button">清除篩選</button></section>
<p id="result-status" class="result-status" aria-live="polite"></p><section id="results" class="result-list" aria-label="結果"></section><nav id="pagination" class="pagination" aria-label="目錄頁碼"></nav><noscript>{_basic_list(rows, kind)}</noscript>
"""
    return _page_shell(title, active, body, payload, _CATALOG_SCRIPT)


def _basic_list(rows: list[dict[str, Any]], kind: str) -> str:
    if not rows:
        return '<p class="notice">目前沒有可列出的項目。</p>'
    lines = []
    for row in _newest(rows):
        title = html.escape((READER_LABELS[kind] + "｜" if kind in READER_LABELS else "") + row["title"])
        url = html.escape(row["url"], quote=True)
        text = html.escape(row["summary"])
        lines.append(f'<li>{f"<a href=\"{url}\">{title}</a>" if url else title}{(" — " + text) if text else ""}</li>')
    return '<section class="no-script-list"><h2>項目清單</h2><ol>' + "".join(lines) + "</ol></section>"


def _newest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: _date_key(row.get("updated_at")), reverse=True)


def _date_key(value: Any) -> tuple[int, str]:
    text = _text(value)
    try:
        return 1, datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return 0, text


def _page_shell(title: str, active: str, body: str, payload: dict[str, Any], script: str) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}｜成果檔案</title><style>{_CSS}</style></head><body><main><nav class="tabs" aria-label="成果檔案導航">{_nav(active)}</nav>{body}</main><script type="application/json" id="catalog-data">{data}</script><script>{_logic_script()}</script><script>{script}</script></body></html>'''


def _nav(active: str) -> str:
    tabs = (("index.html", "總覽"), ("knowledge.html", "知識成果"), ("thinking.html", "思考紀錄"))
    return "".join(f'<a href="{href}"' + (' aria-current="page"' if href == active else "") + f">{label}</a>" for href, label in tabs)


def _logic_script() -> str:
    return Path(__file__).with_name("outcome_catalog_assets.js").read_text(encoding="utf-8")


_CSS = ':root{--paper:#f5f0e7;--ink:#162022;--muted:#637073;--line:#cfc5b5;--accent:#35666b;--panel:#fffdf8;--focus:#c05732}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Serif TC","Microsoft JhengHei",serif;line-height:1.6}main{width:min(1180px,calc(100% - 48px));margin:auto;padding:28px 0 68px}.tabs{display:flex;border-bottom:1px solid var(--line);margin-bottom:42px;overflow:auto}.tabs a{color:var(--muted);font-family:"Microsoft JhengHei",sans-serif;font-weight:700;padding:11px 18px;text-decoration:none;white-space:nowrap}.tabs a[aria-current=page]{border-bottom:3px solid var(--accent);color:var(--ink)}a{color:var(--accent);text-underline-offset:3px}.archive-head{max-width:760px;margin:0 0 30px}.eyebrow{color:var(--accent);font-family:Arial,sans-serif;font-size:.8rem;font-weight:800;letter-spacing:.13em;margin:0}h1{font-size:clamp(2rem,4.6vw,4rem);letter-spacing:-.04em;line-height:1.08;margin:4px 0 12px}h2{line-height:1.25}.archive-head p:last-child,.metadata,.result-status{color:var(--muted)}.counts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line);border:1px solid var(--line);margin:0 0 42px}.counts a{background:var(--panel);color:var(--ink);padding:22px 26px;text-decoration:none}.counts strong{display:block;font:2rem Georgia,serif}.overview-section{border-top:1px solid var(--line);padding:24px 0}.section-heading{display:flex;justify-content:space-between;gap:16px}.compact-list{list-style:none;margin:12px 0 0;padding:0}.compact-list li{border-top:1px solid var(--line);padding:12px 0}.compact-list a{color:var(--ink);font-weight:700}.compact-list small{color:var(--muted);display:block}.research-counts{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.research-count{border:1px solid var(--line);margin:0;padding:5px 9px}.research-counts+div details{background:var(--panel);border:1px solid var(--line);margin-top:9px;padding:10px 13px}.research-counts+div summary{cursor:pointer;font-weight:700}.controls{align-items:end;border-block:1px solid var(--line);display:grid;gap:12px;grid-template-columns:minmax(160px,2fr) repeat(6,minmax(110px,1fr)) auto;padding:14px 0}.controls label{color:var(--muted);font:700 .78rem Arial,sans-serif}.controls input,.controls select,.controls button{background:var(--panel);border:1px solid var(--line);color:var(--ink);display:block;font:inherit;margin-top:4px;min-height:39px;padding:7px 9px;width:100%}.controls button{cursor:pointer;width:auto}.result-list{border-top:1px solid var(--line)}.result{--type-gold:#8a6420;--type-silver:#aeb4bd;display:grid;column-gap:18px;row-gap:12px;grid-template-columns:@RAIL@px minmax(0,1fr);border-bottom:1px solid var(--line);padding:18px 0;overflow-wrap:anywhere}html[data-brain-visual]:not([data-brain-visual=nebula]) .result{--type-gold:var(--accent);--type-silver:var(--muted)}.result.has-preview{grid-template-columns:@RAIL@px minmax(0,1fr) @PREVIEW@px}.type-rail{align-items:center;align-self:start;border:1px solid var(--type-silver);border-radius:12px;color:var(--type-gold);display:flex;grid-column:1;grid-row:1;height:@RAIL@px;justify-content:center;width:@RAIL@px}.type-rail svg{display:block;height:26px;width:26px}.result-body{grid-column:2;grid-row:1;min-width:0}.preview{align-self:start;background:#e9e0d2;display:block;grid-column:3;grid-row:1;min-height:82px}.preview img{display:block;height:auto;max-height:300px;object-fit:contain;width:100%}.result h2{font-size:1.2rem;margin:0}.result p{margin:5px 0}.metadata{overflow-wrap:anywhere}.tags{display:flex;flex-wrap:wrap;gap:6px;margin:9px 0}.tag{border:1px solid var(--line);font: .77rem Arial,sans-serif;padding:2px 7px}.warning{color:#8a411d}.pagination{display:flex;flex-wrap:wrap;gap:7px;margin-top:22px}.pagination button{background:var(--panel);border:1px solid var(--line);cursor:pointer;min-height:35px;padding:5px 10px}.pagination button[aria-current=page]{background:var(--ink);color:var(--panel)}.empty,.notice{border-left:3px solid var(--accent);color:var(--muted);padding:8px 12px}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid var(--focus);outline-offset:3px}@media(max-width:768px){main{width:min(100% - 28px,1180px);padding-top:18px}.tabs{margin-bottom:28px}.controls{grid-template-columns:repeat(2,minmax(0,1fr))}.controls label:first-child{grid-column:1/-1}.controls button{width:100%}}@media(max-width:390px){main{width:calc(100% - 20px)}.controls{grid-template-columns:1fr}.controls label:first-child{grid-column:auto}.counts{grid-template-columns:1fr}.tabs a{padding:10px 11px}}@media(max-width:@MOBILE@px){.result.has-preview{grid-template-columns:@RAIL@px minmax(0,1fr)}.result.has-preview .type-rail{grid-row:1/span 2}.result.has-preview .preview{grid-column:2;grid-row:2;width:min(100%,@PREVIEW@px)}}'.replace("@RAIL@", str(TYPE_RAIL_WIDTH_PX)).replace("@PREVIEW@", str(PREVIEW_WIDTH_PX)).replace("@MOBILE@", str(CARD_MOBILE_MAX_WIDTH_PX))


_CATALOG_SCRIPT = r'''
const CATALOG_DATA=JSON.parse(document.getElementById('catalog-data').textContent),query=document.getElementById('query'),topic=document.getElementById('topic'),researchTopic=document.getElementById('research-topic'),researchFacet=document.getElementById('research-facet'),researchTopicControl=document.getElementById('research-topic-control'),researchFacetControl=document.getElementById('research-facet-control'),status=document.getElementById('status'),sort=document.getElementById('sort'),size=document.getElementById('size'),clear=document.getElementById('clear'),results=document.getElementById('results'),pagination=document.getElementById('pagination'),resultStatus=document.getElementById('result-status');let state=OutcomeCatalogState.parseState(location.search,CATALOG_DATA.page_size);
const typeLabel=value=>({url:'網頁',user_provided_text:'提供文字',user_provided_image:'提供圖片'})[value]||value;const statusLabel=value=>({ready:'可閱讀',needs_review:'待檢查',error:'建立失敗',blocked:'受阻'})[value]||value;const displayDate=value=>String(value||'').match(/^\d{4}-\d{2}-\d{2}/)?.[0]||String(value||'');
const researchTopics=Array.isArray(CATALOG_DATA.research_topics)?CATALOG_DATA.research_topics:[],topicLabels=new Map(researchTopics.map(row=>[row.id,row.label||row.id])),facetLabels={design_families:'設計家族',design_family:'設計家族',coverage_dimensions:'覆蓋範圍',application_scenes:'應用場景',classification:'研究分類',tags:'標籤'};
query.value=state.q;topic.value=state.topic;status.value=state.status;sort.value=state.sort;if(!['20','50'].includes(String(state.size))){const option=document.createElement('option');option.value=String(state.size);option.textContent=`${state.size} 項（預設）`;size.prepend(option)}size.value=String(state.size);
for(const value of [...new Set(CATALOG_DATA.items.flatMap(row=>[row.topic]).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-Hant'))){const option=document.createElement('option');option.value=value;option.textContent=CATALOG_DATA.kind==='knowledge'?typeLabel(value):value;topic.append(option)}topic.value=state.topic;for(const value of [...new Set(CATALOG_DATA.items.map(row=>row.status).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-Hant'))){const option=document.createElement('option');option.value=value;option.textContent=statusLabel(value);status.append(option)}status.value=state.status;
function updateResearchFacets(){researchFacet.replaceChildren(new Option('全部研究分類',''));const selected=researchTopic.value;if(!selected){researchFacetControl.hidden=true;state.research_facet='';return}const values=[];for(const row of CATALOG_DATA.items){if(!(row.research_topic_ids||[]).includes(selected))continue;for(const [kind,items] of Object.entries(row.research_facets||{})){for(const value of items||[])values.push([`${kind}:${value}`,`${facetLabels[kind]||kind}：${value}`])}}for(const [value,label] of values.sort((a,b)=>a[1].localeCompare(b[1],'zh-Hant')).filter((item,index,all)=>index===0||item[0]!==all[index-1][0]))researchFacet.add(new Option(label,value));researchFacet.value=state.research_facet;if(researchFacet.value!==state.research_facet)state.research_facet='';researchFacetControl.hidden=researchFacet.options.length===1}
if(CATALOG_DATA.kind==='knowledge'&&researchTopics.length){researchTopicControl.hidden=false;for(const item of researchTopics)researchTopic.add(new Option(item.label||item.id,item.id));researchTopic.value=state.research_topic;if(researchTopic.value!==state.research_topic)state.research_topic='';updateResearchFacets()}else{state.research_topic='';state.research_facet=''}
function sync(reset){state={q:query.value.trim(),topic:topic.value,research_topic:researchTopic.value,research_facet:researchFacet.value,status:status.value,sort:sort.value,page:reset?1:state.page,size:Number(size.value)};const search=OutcomeCatalogState.stateToSearch(state);history.replaceState(null,'',location.pathname+(search?'?'+search:'')+location.hash)}function returnLink(url){if(!url||/^[a-z][a-z0-9+.-]*:/i.test(url)||url.startsWith('//'))return '#';const [path,anchor='']=url.split('#',2),page=location.pathname.split('/').pop(),returnTo=encodeURIComponent(['index.html','knowledge.html','thinking.html'].includes(page)?page+location.search:'knowledge.html');return path+(path.includes('?')?'&':'?')+'return='+returnTo+(anchor?'#'+anchor:'')}
function render(){const selected=OutcomeCatalogState.selectItems(CATALOG_DATA.items,CATALOG_DATA.kind,state),slice=OutcomeCatalogState.paginate(selected,state.page,state.size);state.page=slice.currentPage;sync(false);resultStatus.textContent=slice.total?`找到 ${slice.total} 項結果，顯示第 ${slice.currentPage} 頁。`:'沒有符合條件的結果。';results.replaceChildren(...slice.items.map(card));pagination.replaceChildren(...buttons(slice.pageCount,slice.currentPage))}
const SOURCE_TYPES=CATALOG_DATA.source_types||{};function typeRail(row){const known=Object.prototype.hasOwnProperty.call(SOURCE_TYPES,row.type_kind),key=known?row.type_kind:'generic',type=SOURCE_TYPES[key]||{label:'來源',svg:''},rail=document.createElement('div');rail.className='type-rail';rail.dataset.type=key;rail.setAttribute('role','img');rail.setAttribute('aria-label','類型：'+type.label);rail.title=type.label;rail.innerHTML=type.svg;return rail}
function previewBox(row,article){const preview=document.createElement(row.url?'a':'div');preview.className='preview';if(row.url)preview.href=returnLink(row.url);const image=document.createElement('img');image.src=row.preview_url;image.alt=`${row.title} 預覽`;image.loading='lazy';image.addEventListener('error',()=>{preview.remove();article.classList.remove('has-preview')});preview.append(image);return preview}
function card(row){const article=document.createElement('article'),hasPreview=Boolean(row.preview_url);article.className='result'+(CATALOG_DATA.kind==='thinking'?' thinking-result':'')+(hasPreview?' has-preview':'');article.append(typeRail(row));const content=document.createElement('div'),heading=document.createElement('h2');content.className='result-body';if(row.url){const link=document.createElement('a');link.href=returnLink(row.url);link.textContent=(CATALOG_DATA.reader_label?CATALOG_DATA.reader_label+'｜':'')+row.title;heading.append(link)}else heading.textContent=row.title;content.append(heading);const description=document.createElement('p');description.textContent=row.summary;content.append(description);const meta=document.createElement('p');meta.className='metadata';meta.textContent=[displayDate(row.updated_at),CATALOG_DATA.kind==='knowledge'?row.source_id:row.source].filter(Boolean).join(' · ');content.append(meta);if(CATALOG_DATA.kind==='knowledge'&&row.source_url){const source=document.createElement('a');source.className='metadata';source.href=row.source_url;source.target='_blank';source.rel='noreferrer';source.textContent=row.source_url_label;content.append(source)}if(CATALOG_DATA.kind==='knowledge'&&row.application_preview_url){const application=document.createElement('a');application.className='metadata';application.href=returnLink(row.application_preview_url);application.textContent='預覽應用／試驗'+(row.research_application_count>1?'（'+row.research_application_count+' 項）':'');content.append(application)}const tags=document.createElement('div');tags.className='tags';for(const value of [row.topic,row.status].filter(Boolean)){const tag=document.createElement('span');tag.className='tag';tag.textContent=CATALOG_DATA.kind==='knowledge'?typeLabel(value)||statusLabel(value):statusLabel(value);if(CATALOG_DATA.kind==='knowledge'&&value===row.status)tag.textContent=statusLabel(value);tags.append(tag)}if(CATALOG_DATA.kind==='knowledge')for(const id of row.research_topic_ids||[]){const tag=document.createElement('span');tag.className='tag';tag.textContent='研究：'+(topicLabels.get(id)||id);tags.append(tag)}content.append(tags);if(row.warnings&&row.warnings.length){const warning=document.createElement('p');warning.className='metadata warning';warning.textContent=`待核對：${row.warnings.join('；')}`;content.append(warning)}article.append(content);if(hasPreview)article.append(previewBox(row,article));return article}
function buttons(count,current){if(count===1)return [];return Array.from({length:count},(_,index)=>{const page=index+1,button=document.createElement('button');button.type='button';button.textContent=String(page);button.setAttribute('aria-label',`第 ${page} 頁`);if(page===current)button.setAttribute('aria-current','page');button.addEventListener('click',()=>{state.page=page;render();window.scrollTo({top:0,behavior:'smooth'})});return button})}for(const control of [query,topic,status,sort,size,researchFacet])control.addEventListener(control===query?'input':'change',()=>{sync(true);render()});researchTopic.addEventListener('change',()=>{updateResearchFacets();sync(true);render()});clear.addEventListener('click',()=>{query.value='';topic.value='';researchTopic.value='';state=OutcomeCatalogState.parseState('',20);updateResearchFacets();status.value='';sort.value='source';size.value='20';render();query.focus()});render();
'''
