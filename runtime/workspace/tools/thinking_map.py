"""Evidence-preserving visual projection of thinking_v2; no inference or network.

All node bodies are taken from the reviewed answer. Short map labels are visibly
marked excerpts; the inspector and linear reading retain the complete wording.
"""
from __future__ import annotations

import html
import json
import re
from pathlib import Path

VERSION = "thinking-map/3"
KINDS = {"hypothesis": "待驗假說", "deduction": "有條件推導", "design": "設計提案"}
# Box width 256 plus a 70-unit corridor, so a four-character edge label never touches either box.
COLUMN_STEP = 326
MAP_WIDTH = 960
NO_LINE_START = set("，。、；：！？）」』】》〉,.;:!?)]}…")
NO_LINE_END = set("（「『【《〈([{")
LENSES = [
    ("shift", "01", "問題怎樣改變", "沿著原先的問法，找到材料帶出的新問題。"),
    ("collision", "02", "材料在哪裏拉扯", "先看每份材料的作用，再比較新判斷與另一個解釋。"),
    ("mechanism", "03", "背後怎樣發生", "逐步檢視推導，連同它依賴的假設一起看。"),
    ("adjacent", "04", "走到另一個觀點", "跨到另一個思考框架，再帶一個有用的問題回來。"),
]


def esc(value):
    return html.escape(str(value), quote=True)


def model(item: dict, evidence: list[dict]) -> dict:
    """Projection only: typed edges describe reasoning, never proven causality."""
    nodes = {}

    def add(key, label, body, role="inference", detail=None, refs=None):
        nodes[key] = {"id": key, "label": label, "body": body, "role": role,
                      "short": item.get("map_labels", {}).get(key, ""),
                      "detail": detail or [], "refs": refs or []}

    add("before", "原先的問法", item["implication"]["before"], "frame", [item["baseline"]])
    add("delta", "新暴露的問題", item["delta"], detail=[item["claim"]])
    add("after", "現在值得追問", item["implication"]["after"], "possibility")
    add("claim", KINDS[item["kind"]], item["claim"], detail=[item["delta"]])
    add("mechanism", "可能的作用機制", item["mechanism"], detail=["適用界線：" + item["boundary"]])
    add("assumptions", "成立需要甚麼", item["assumptions"], "condition")
    add("alternative", "另一個解釋", item["alternative"], "challenge", ["適用界線：" + item["boundary"]])
    add("observe", "用甚麼觀察分辨", item["discriminator"]["observe"], "condition",
        ["若支持：" + item["discriminator"]["supports"], "若削弱：" + item["discriminator"]["undermines"]])
    adj = item["adjacent"]
    add("from", "原來看的範圍", adj["from_frame"], "frame")
    add("bridge", "為何值得跨過去", adj["bridge"])
    add("to", "換一個角度", adj["to_frame"], "possibility")
    add("question", "往外多問一步", adj["question"], "possibility")
    add("return", "帶回原題的價值", adj["return_value"], "possibility")
    lookup = {e["id"]: e for e in evidence}
    premises = []
    for index, p in enumerate(item["premises"]):
        key = "p" + str(index + 1)
        ev = lookup[p["id"]]
        add(key, "材料 " + p["id"] + " · " + str(index+1), p["contribution"], "source",
            ["原文短引：「" + p["quote"] + "」", "出處：" + (ev.get("title") or ev.get("path") or ev.get("url") or p["id"])], [p["id"]])
        premises.append(key)
    steps = []
    for index, step in enumerate(item["derivation"]):
        key = "step" + str(index + 1)
        add(key, "推導 " + str(index + 1), step)
        steps.append(key)

    def edge(a, b, label, kind="inference"):
        return {"from": a, "to": b, "label": label, "kind": kind}

    # Three columns with reserved edge corridors. Narrow views use an HTML route.
    views = {}
    views["shift"] = {"rows": [["before", "delta", "after"]], "edges": [edge("before", "delta", "材料帶出"), edge("delta", "after", "改變問法")], "focus": "delta"}
    rows = [[p, "delta" if index == 0 else None, "claim" if index == 0 else "alternative" if index == 1 else None] for index, p in enumerate(premises)]
    edges = [edge(p, "delta", "提供依據", "evidence") for p in premises]
    edges += [edge("delta", "claim", "提出判斷"), edge("alternative", "claim", "競爭解釋", "challenge")]
    views["collision"] = {"rows": rows, "edges": edges, "focus": "delta"}
    rows = [["assumptions", steps[0], "alternative"]]
    rows += [[None, key, None] for key in steps[1:]]
    rows += [["mechanism", "claim", "observe"]]
    edges = [edge("assumptions", steps[0], "依賴前提", "condition")]
    edges += [edge(a, b, "繼續推導") for a, b in zip(steps, steps[1:])]
    edges += [edge(steps[-1], "claim", "條件成立時"), edge("mechanism", "claim", "可能機制"), edge("claim", "observe", "需要分辨", "condition"), edge("alternative", "observe", "也要檢查", "challenge")]
    views["mechanism"] = {"rows": rows, "edges": edges, "focus": "assumptions"}
    views["adjacent"] = {"rows": [["from", "bridge", "to"], ["return", None, "question"]], "edges": [edge("from", "bridge", "跨越理由"), edge("bridge", "to", "換個框架"), edge("to", "question", "延伸一問", "adjacent"), edge("question", "return", "帶回原題", "adjacent")], "focus": "to"}
    return {"id": item["id"], "nodes": nodes, "views": views}


def excerpt_lines(value: str, width=19, count=4):
    """Use explicit ellipsis, never pretend an excerpt is a semantic summary.

    Line breaks avoid a closing mark opening a line or an opening mark closing one; the characters themselves never change.
    """
    chars, lines, length = "", [], 0.0
    for char in re.sub(r"\s+", " ", value):
        size = 1  # Conservative across CJK and wide Latin glyphs; never shrink labels.
        if length + size > width:
            carry = ""
            if len(chars) > 1 and (char in NO_LINE_START or chars[-1] in NO_LINE_END):
                carry, chars = chars[-1], chars[:-1]
            lines.append(chars)
            chars, length = carry, float(len(carry))
        chars += char
        length += size
    if chars:
        lines.append(chars)
    if len(lines) > count:
        lines = lines[:count]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def scene_svg(data, lens):
    view, key = data["views"][lens], data["id"]
    positions = {node: (26 + col * COLUMN_STEP, 36 + row * 240) for row, cells in enumerate(view["rows"]) for col, node in enumerate(cells) if node}
    height = len(view["rows"]) * 240 + 22
    parts = [f'<svg class="concept-map" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MAP_WIDTH} {height}" role="group" aria-label="{esc(dict((x[0], x[2]) for x in LENSES)[lens])}">', '<defs>']
    for role, color in [("evidence", "#48706c"), ("inference", "#344c98"), ("challenge", "#a54e3d"), ("condition", "#8b6a27"), ("adjacent", "#695699")]:
        parts.append(f'<marker id="{key}-{lens}-{role}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 8 5 L 0 9" fill="none" stroke="{color}" stroke-width="1.5"/></marker>')
    parts.append('</defs>')
    labelled = set()
    for e in view["edges"]:
        ax, ay = positions[e["from"]]; bx, by = positions[e["to"]]
        # Edges that converge on one box with the same meaning share a single label; the others stay readable and the
        # complete list of connections remains under the map and in the accessible name.
        label_text = '' if (e["to"], e["label"]) in labelled else esc(e["label"])
        labelled.add((e["to"], e["label"]))
        if ax == bx:
            down = by > ay
            x1, y1, x2, y2 = ax + 128, ay + (168 if down else 0), bx + 128, by + (0 if down else 168)
            d = f'M{x1} {y1} L{x2} {y2}'
            tx, ty = x1 + 9, (y1+y2)/2 + 6
            anchor = 'start'
        else:
            right = bx > ax
            x1, y1, x2, y2 = ax + (256 if right else 0), ay + 84, bx + (0 if right else 256), by + 84
            mid = (x1 + x2) / 2
            d = f'M{x1} {y1} C{mid} {y1} {mid} {y2} {x2} {y2}'
            tx, ty, anchor = mid, (y1 + y2)/2 - 12, 'middle'
        text_element = ('<text x="' + str(tx) + '" y="' + str(ty) + '" text-anchor="' + anchor + '">' + label_text + '</text>') if label_text else ''
        parts.append(f'<g class="connection {e["kind"]}" data-from="{e["from"]}" data-to="{e["to"]}"><path d="{d}" marker-end="url(#{key}-{lens}-{e["kind"]})"/>{text_element}</g>')
    for node, (x, y) in positions.items():
        n = data["nodes"][node]
        focus = 'mechanism' if node == 'mechanism' else node
        parts.append(f'<a href="#{key}-read-{node}" class="map-node {n["role"]}" data-node="{node}" data-focus="{focus}" role="button" aria-controls="{key}-inspector" aria-pressed="false" aria-label="{esc(n["label"])}：{esc(n["body"])}"><rect x="{x}" y="{y}" width="256" height="168" rx="12"/><text class="node-label" x="{x+18}" y="{y+29}">{esc(n["label"])}</text>')
        for index, line in enumerate(excerpt_lines(n["short"] or n["body"], 10.6, 4)):
            parts.append(f'<text class="node-excerpt" x="{x+18}" y="{y+57+index*23}">{esc(line)}</text>')
        hint = '點選展開依據與條件 ↗' if n['short'] else '節錄 · 點選讀完整內容 ↗'
        parts.append(f'<text class="node-hint" x="{x+18}" y="{y+154}">{hint}</text></a>')
    parts.append('</svg>')
    return ''.join(parts)


def paras(value):
    return ''.join('<p>' + esc(p) + '</p>' for p in value.split('\n') if p)


def nav_link(link):
    href = link.get('href', '')
    if not href or href.startswith('//') or (re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', href) and not href.startswith(('http://', 'https://'))):
        return ''
    result = '<a href="' + esc(href) + '">' + esc(link['label']) + ' ↗</a>'
    if link.get('note'):
        result += '<a hidden data-obsidian-path="' + esc(link.get('note_href', href)) + '">在 Obsidian 開啟 ↗</a>'
    return result


def render(question, analysis, evidence, review, *, surface="answer", return_url="", navigation=None):
    if surface != "answer":
        raise ValueError("Only the answer reader is supported")
    navigation = navigation or {}
    tabs, articles, models = [], [], []
    for index, item in enumerate(analysis["insights"]):
        key = item["id"]
        data = model(item, evidence)
        models.append(data)
        tabs.append(f'<button class="insight-tab" data-insight="{key}" aria-pressed="{str(index==0).lower()}"><span>{index+1:02d}</span>{esc(item["title"])}</button>')
        lenses = ''.join(f'<button data-lens="{mode}" aria-pressed="{str(mode=="shift").lower()}"><span>{num}</span><b>{label}</b></button>' for mode, num, label, _ in LENSES)
        scenes = []
        for mode, num, label, description in LENSES:
            v = data["views"][mode]
            route = []
            route_order = {
                "shift": ["before", "delta", "after"],
                "collision": [n for n in data["nodes"] if re.fullmatch(r'p\d+', n)] + ["delta", "claim", "alternative"],
                "mechanism": ["assumptions"] + [n for n in data["nodes"] if re.fullmatch(r'step\d+', n)] + ["mechanism", "claim", "alternative", "observe"],
                "adjacent": ["from", "bridge", "to", "question", "return"],
            }[mode]
            for node in route_order:
                n = data["nodes"][node]
                incoming = [data["nodes"][e["from"]]["label"] + " · " + e["label"] for e in v["edges"] if e["to"] == node]
                relation = '<span class="route-connection">' + esc('；'.join(incoming)) + '</span>' if incoming else ''
                route.append(f'<div class="route-stop">{relation}<button class="route-node {n["role"]}" data-node="{node}" aria-controls="{key}-inspector" aria-pressed="false"><span>{esc(n["label"])}</span><b>{esc(n["short"] or n["body"])}</b><small>點選查看依據與完整說明 ↗</small></button></div>')
            relations = ''.join(f'<li>{esc(data["nodes"][e["from"]]["label"])} <span>{esc(e["label"])}</span> {esc(data["nodes"][e["to"]]["label"])}</li>' for e in v["edges"])
            scenes.append(f'<section class="scene" data-scene="{mode}" {"hidden" if mode!="shift" else ""}><div class="scene-heading"><span class="scene-index">{num}</span><div><h3>{label}</h3><p>{description}</p></div></div>{scene_svg(data,mode)}<div class="mobile-route">{"".join(route)}</div><details class="relationship-list"><summary>讀取這幅圖的連線意思</summary><ul>{relations}</ul></details></section>')
        default = data["nodes"]["delta"]
        full_nodes = []
        for n in data["nodes"].values():
            refs = ''.join(f'<a class="evidence-link" href="#evidence-{esc(r)}">閱讀 {esc(r)} 原始材料 ↗</a>' for r in n["refs"])
            full_nodes.append(f'<section id="{key}-read-{n["id"]}"><h4>{esc(n["label"])}</h4>{paras(n["body"])}{"".join(paras(s) for s in n["detail"])}{refs}</section>')
        adj = item["adjacent"]
        prompt = f'沿著「{question}」繼續追問：{adj["question"]}\n原先框架：{adj["from_frame"]}\n相鄰框架：{adj["to_frame"]}\n跨越理由：{adj["bridge"]}\n帶回原題的價值：{adj["return_value"]}\n已有推理：{item["claim"]}\n先檢查這個替代解釋：{item["alternative"]}\n請重新讀相關來源，指出新增判斷與可推翻條件；不可把本頁假說當作已證實事實。'
        heading = f'''
<div class="insight-heading"><div><span class="eyebrow">思考線索 {index+1:02d} / {len(analysis['insights']):02d} · {esc(KINDS[item['kind']])}</span><h2>{esc(item['title'])}</h2></div><a href="#{key}-full" class="text-link">閱讀完整論證 ↓</a></div>
<p class="claim" id="{key}-claim">{esc(item['claim'])}</p>'''
        workspace = f'''
<div class="visual-workspace"><nav class="lens-nav" aria-label="切換觀察角度">{lenses}</nav>
<div class="workspace-body"><div class="scene-stack">{''.join(scenes)}<div class="map-legend"><span class="source">材料線索</span><span class="inference">推理／假說</span><span class="challenge">競爭解釋</span><span class="possibility">另一個觀點</span><span class="condition">成立條件</span></div><p class="map-note">箭嘴表示推理關係。虛線表示待檢驗條件或競爭解釋；並非已證實因果。</p></div>
<aside class="inspector" id="{key}-inspector" tabindex="-1" aria-label="選中節點的完整內容"><span class="eyebrow inspector-role">推理／假說</span><h3 class="inspector-title">{esc(default['label'])}</h3><div class="inspector-body">{paras(default['body'])}{''.join(paras(s) for s in default['detail'])}</div><div class="inspector-links"></div><a class="inspector-deep" href="#{key}-read-delta">在完整論證中閱讀 ↓</a><p class="inspector-note">點選圖中任何一站，完整內容會在這裏展開。</p><button class="back-to-map">返回關係圖 ↑</button></aside></div>
<div class="tour-bar"><span class="tour-state" role="status">01 / 04 · 問題怎樣改變</span><div><button class="tour-prev" disabled aria-label="上一個角度">←</button><button class="tour-next">下一個角度 →</button></div></div></div>'''
        actions = f'''
<section class="experiment" id="{key}-discriminator"><div><span class="eyebrow">讓想法接受挑戰</span><h3>甚麼結果，會令我改變看法？</h3>{paras(item['discriminator']['observe'])}</div><div class="experiment-outcome"><div class="choices" role="group" aria-label="比較假設觀察結果"><button data-result="unknown" aria-pressed="true">還未觀察</button><button data-result="supports" aria-pressed="false">假如支持</button><button data-result="undermines" aria-pressed="false">假如削弱</button></div><p class="scenario-label">目前：還未作觀察</p><p class="result" aria-live="polite" data-unknown="仍是待驗判斷。先作上述觀察，再决定採用程度。" data-supports="{esc(item['discriminator']['supports'])}" data-undermines="{esc(item['discriminator']['undermines'])}">仍是待驗判斷。先作上述觀察，再决定採用程度。</p><p class="muted">這是已寫好的條件比較，並未取得新證據。</p></div></section>
<section class="next-question" id="{key}-action"><span class="eyebrow">把這一步帶往下一輪</span><h3>{esc(adj['question'])}</h3><p>{esc(adj['return_value'])}</p><details><summary>展開可直接使用的追問</summary><p>複製交給第二大腦，讓下一輪重新閱讀材料與反例。</p><textarea readonly aria-label="追問文字">{esc(prompt)}</textarea><button class="copy">複製追問</button><span class="copy-status" role="status"></span></details></section>'''
        full = f'<details class="full-argument" id="{key}-full" open><summary>閱讀完整論證與全部依據</summary><div class="linear-reading">{"".join(full_nodes)}</div></details>'
        articles.append(f'<article class="insight" id="{key}">{heading}{workspace}{actions}{full}</article>')
    sources = []
    for e in evidence:
        title = e.get("title") or e.get("path") or e.get("url") or e["id"]
        source_nav = nav_link(navigation.get('sources', {}).get(e['id'], {}))
        sources.append(f'<details id="evidence-{esc(e["id"])}"><summary><span>{esc(e["id"])}</span> {esc(title)}</summary><p class="muted">來源快照 · {esc(e.get("path") or e.get("url") or "")}</p><nav class="record-nav">{source_nav}</nav><pre>{esc(e["text"])}</pre></details>')
    reviews = ''.join('<details><summary>' + esc(r['id']) + ' 的覆核理由</summary>' + ''.join('<p><b>' + label + '：</b>' + esc(r[field]) + '</p>' for field,label in [('increment_check','新增價值'),('grounding_check','證據邊界'),('strongest_objection','最強質疑'),('adjudication','裁決')]) + '</details>' for r in review.get('items', []))
    payload = json.dumps(models, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    assets = Path(__file__).parent
    css = (assets / 'thinking_map.css').read_text(encoding='utf-8')
    js = (assets / 'thinking_map.js').read_text(encoding='utf-8')
    back = '<a id="return-link" href="' + esc(return_url) + '">返回' + '思考紀錄' + ' ↗</a>' if return_url else ''
    record_nav = '<nav class="record-nav" aria-label="知識庫導航">' + ''.join(nav_link(x) for x in navigation.get('links', [])) + '</nav>' if navigation.get('links') else ''
    notices = ''.join('<p class="record-warning" role="status">' + esc(x) + '</p>' for x in navigation.get('warnings', []))
    empty = '<section class="empty"><h2>這次還未形成新洞見</h2>' + paras(analysis.get('no_increment_reason', '')) + '</section>'
    controls = '<div class="view-controls" role="group" aria-label="選擇閱讀方式"><button data-view="text" aria-pressed="true">完整論證</button><button data-view="map" aria-pressed="false">互動思考地圖</button><a href="#evidence">查原始材料 ↗</a></div>'
    overview = '<details class="thesis" open><summary>先讀整體判斷</summary>' + paras(analysis['thesis']) + '</details>'
    evidence_title = '原始材料與覆核'
    evidence_note = '原文、推論與待驗假說分開保留。圖中距離、大小和顏色不表示可信度。'
    review_section = '<details><summary>本次覆核的範圍</summary><p>' + ('獨立覆核' if review.get('method') == 'independent' else '作者另輪覆核') + '。可追溯的推理仍可能被新證據修正，不代表已證實因果、全球原創性或正式入庫。</p>' + reviews + '</details>'
    reader_title = '思考紀錄'
    return f'''<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(question)} · {reader_title}</title><link rel="icon" href="data:,"><style>{css}</style></head>
<body data-view="text" data-surface="{surface}" data-reader="{VERSION}"><a class="skip-link" href="#reading">跳到完整閱讀</a><header><div class="masthead"><a class="brand" href="#top"><span>創造型第二大腦<small>THINKING ATLAS</small></span></a><span class="surface-label">問我的第二大腦</span>{back}</div>
{record_nav}{notices}<div class="intro" id="top"><div><span class="eyebrow">{reader_title}</span><h1>{esc(question)}</h1></div><div class="intro-note"><p>看見材料之間的關係，<br>找到值得多問一步的地方。</p><a href="#reading">開始探索 <span>↘</span></a></div></div>
{overview}{controls}</header>
<main id="reading"><nav class="insight-tabs" aria-label="切換思考線索">{''.join(tabs)}</nav>{''.join(articles) if articles else empty}
<section class="evidence" id="evidence"><span class="eyebrow">回到出處</span><h2>{evidence_title}</h2><p class="muted">{evidence_note}</p>{''.join(sources)}{review_section}</section></main>
<footer><span>創造型第二大腦 · 思考地圖</span><p>本頁可離線操作。切換只改變閱讀角度與條件比較；繼續追問須交給 AI 重新取證。本頁不傳送資料、不回寫筆記。</p></footer><div class="sr-only" id="map-status" aria-live="polite"></div><script type="application/json" id="map-data">{payload}</script><script>{js}</script></body></html>'''
