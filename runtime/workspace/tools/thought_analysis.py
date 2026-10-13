"""Shared reasoning contract and offline readers for Ask and Canvas.

Mechanical checks establish traceability and review binding, never semantic truth.
The normative reasoning requirements live in the semantic-index specification.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

SCHEMA = "thinking_v2"
VERSION = "thinking-reader/2"


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def review_target(packet: dict, answer: dict) -> str:
    return fingerprint({"question": packet["question"], "session_id": packet["session_id"],
                        "evidence": packet["evidence"], "answer": answer})


def text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must contain a substantive explanation")
    return value.strip()


def normalized(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).casefold()


def source_family(evidence: dict) -> str:
    # Source, application and KP copies of one record are not independent inputs.
    found = re.search(r"KS-[A-Z]+-\d+", evidence.get("path", ""))
    if found:
        return found.group()
    body = evidence.get("text", "")
    origins = sorted(set(re.findall(r"KS-[A-Z]+-\d+", body)))
    if origins:
        return "|".join(origins)
    declared = re.search(r'^source_id:\s*["\']?([^\s"\']+)', body, re.MULTILINE)
    if declared:
        return declared.group(1)
    return evidence.get("url") or ("body:" + fingerprint(body))


def validate_analysis(analysis: dict, lookup: dict) -> dict:
    if not isinstance(analysis, dict) or analysis.get("schema") != SCHEMA:
        raise ValueError("deep thinking requires analysis.schema=thinking_v2")
    text(analysis.get("thesis"), "thesis")
    insights = analysis.get("insights")
    if not isinstance(insights, list) or len(insights) > 3:
        raise ValueError("use at most three developed insights")
    if not insights:
        text(analysis.get("no_increment_reason"), "no_increment_reason")
    ids = set()
    for item in insights:
        key = item.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"I[1-9][0-9]*", key) or key in ids:
            raise ValueError("insight ids must be unique I identifiers")
        ids.add(key)
        if item.get("kind") not in {"hypothesis", "deduction", "design"}:
            raise ValueError("insight kind must distinguish hypothesis, deduction or design")
        for field in ("title", "claim", "baseline", "delta", "mechanism", "assumptions", "alternative", "boundary"):
            text(item.get(field), key + "." + field)
        premises = item.get("premises", [])
        if not isinstance(premises, list) or len(premises) < 2:
            raise ValueError("a collision needs at least two evidenced premises")
        families = set()
        for premise in premises:
            ev = lookup.get(premise.get("id"))
            quote = text(premise.get("quote"), "premise.quote")
            text(premise.get("contribution"), "premise.contribution")
            if not ev or quote not in ev["text"]:
                raise ValueError("premise quote must match the current evidence")
            families.add(source_family(ev))
        # An aggregate concept that cites the same records cannot masquerade as a new source.
        origins = [set(f.split("|")) for f in families]
        if len(families) < 2 or not any(a.isdisjoint(b) for a in origins for b in origins):
            raise ValueError("collision requires distinct source families, not copies of one record")
        steps = item.get("derivation")
        if not isinstance(steps, list) or len(steps) < 2 or any(not isinstance(s, str) or not s.strip() for s in steps):
            raise ValueError("show the inference steps between premises and new claim")
        for group, fields in {"discriminator": ("observe", "supports", "undermines"),
                              "implication": ("before", "after"),
                              "adjacent": ("question", "from_frame", "to_frame", "bridge", "return_value")}.items():
            part = item.get(group)
            if not isinstance(part, dict):
                raise ValueError("missing " + group)
            for field in fields:
                text(part.get(field), group + "." + field)
        pairs = [(item["baseline"], item["claim"]), (item["baseline"], item["delta"]),
                 (item["implication"]["before"], item["implication"]["after"]),
                 (item["discriminator"]["supports"], item["discriminator"]["undermines"]),
                 (item["adjacent"]["from_frame"], item["adjacent"]["to_frame"])]
        if any(normalized(a) == normalized(b) for a, b in pairs):
            raise ValueError("restatement or circular contrast is not reasoning increment")
        claim = normalized(item["claim"])
        if any(claim in normalized(p["quote"]) for p in premises):
            raise ValueError("a quotation cannot be promoted to a new insight")
        if "map_labels" in item:
            labels = item["map_labels"]
            allowed = {"before", "after", "delta", "claim", "mechanism", "assumptions", "alternative", "observe", "from", "to", "bridge", "question", "return"}
            allowed.update("p" + str(n+1) for n in range(len(premises)))
            allowed.update("step" + str(n+1) for n in range(len(steps)))
            if not isinstance(labels, dict) or not labels or set(labels) - allowed:
                raise ValueError("map_labels must address existing reasoning nodes")
            for label in labels.values():
                if len(text(label, "map label")) > 40 or "\n" in label:
                    raise ValueError("map labels must be single-line phrases of at most 40 characters")
    return analysis


def validate_review(packet: dict, answer: dict, review: dict) -> dict:
    if not isinstance(review, dict) or review.get("target_sha256") != review_target(packet, answer):
        raise ValueError("review is missing or bound to a different answer/evidence version")
    if review.get("method") not in {"independent", "author_second_pass"}:
        raise ValueError("declare the actual semantic review method")
    text(review.get("reviewer"), "reviewer")
    expected = {i["id"] for i in answer["analysis"]["insights"]}
    rows = review.get("items", [])
    if not isinstance(rows, list) or len(rows) != len(expected) or {r.get("id") for r in rows} != expected:
        raise ValueError("review must address every insight exactly once")
    for row in rows:
        for field in ("increment_check", "grounding_check", "strongest_objection", "adjudication"):
            text(row.get(field), "review." + field)
        if row.get("verdict") != "accept":
            raise ValueError("unresolved semantic objection; revise before delivery")
        item = next(i for i in answer["analysis"]["insights"] if i["id"] == row["id"])
        if item.get("map_labels"):
            text(row.get("visual_check"), "review.visual_check")
    if not expected:
        text(review.get("no_increment_check"), "no_increment_check")
    return review


LABELS = {"hypothesis": "待驗假說", "deduction": "有條件推導", "design": "設計提案"}


def markdown(question: str, analysis: dict, evidence: list[dict], review: dict | None) -> str:
    lines = ["# " + question, "", analysis["thesis"], "", "以下是可反駁的推理成果；來源支持不代表因果已被證實。", ""]
    if not analysis["insights"]:
        lines += ["## 尚未形成新洞見", "", analysis["no_increment_reason"], ""]
    for item in analysis["insights"]:
        lines += ["## " + item["title"], "", LABELS[item["kind"]] + "：" + item["claim"], "",
                  "### 碰出了甚麼問題", "", "材料原已說明：" + item["baseline"], "", "新增判斷：" + item["delta"], "",
                  "### 推理依據", ""]
        for p in item["premises"]:
            lines += [f"- [{p['id']}]「{p['quote']}」— {p['contribution']}"]
        lines += [""] + [f"{n}. {s}" for n, s in enumerate(item["derivation"], 1)]
        lines += ["", "### 底層機制與假設", "", item["mechanism"], "", "依賴假設：" + item["assumptions"], "",
                  "### 另一個解釋", "", item["alternative"], "", "適用界線：" + item["boundary"], "",
                  "### 怎樣分辨", "", item["discriminator"]["observe"], "",
                  "支持這個判斷：" + item["discriminator"]["supports"], "", "削弱或推翻：" + item["discriminator"]["undermines"], "",
                  "### 判斷會怎樣改變", "", "原先：" + item["implication"]["before"], "", "現在：" + item["implication"]["after"], "",
                  "### 換一層看", "", item["adjacent"]["question"], "", item["adjacent"]["from_frame"] + " → " + item["adjacent"]["to_frame"], "",
                  item["adjacent"]["bridge"], "", item["adjacent"]["return_value"], ""]
    lines += ["## 證據位置", ""]
    lines += [f"- {e['id']}：{e.get('path') or e.get('url')}" for e in evidence]
    lines += ["", "## 覆核範圍", "", "已記錄逐項語義覆核；不代表普遍真理、全球原創性或正式入庫。" if review else "尚待語義覆核。", ""]
    return "\n".join(lines)


def graph_model(analysis: dict) -> tuple[list[dict], list[dict]]:
    nodes, edges = [], []
    for index, item in enumerate(analysis["insights"]):
        y = index * 580
        prefix = item["id"]
        parts = [("premises", "材料碰撞", "\n\n".join(p["contribution"] for p in item["premises"]), 0, 0),
                 ("mechanism", "底層機制", item["mechanism"], 400, 0),
                 ("claim", item["title"], LABELS[item["kind"]] + "\n" + item["claim"], 800, 0),
                 ("alternative", "另一個解釋", item["alternative"], 0, 275),
                 ("discriminator", "分辨的觀察", item["discriminator"]["observe"], 400, 275),
                 ("action", "換一層看", item["adjacent"]["question"], 800, 275)]
        for role, label, body, x, dy in parts:
            nodes.append({"id": prefix + "-" + role, "type": "text", "x": x, "y": y + dy, "width": 350, "height": 230,
                          "text": label + "\n\n" + body, "color": "4" if role == "claim" else "2" if role == "alternative" else "6"})
        for a, b, label in [("premises", "mechanism", "推理橋樑"), ("mechanism", "claim", "條件成立時"),
                            ("alternative", "discriminator", "與主張競爭"), ("claim", "discriminator", "接受檢驗"),
                            ("discriminator", "action", "改變下一問")]:
            edges.append({"id": prefix + "-" + a + "-" + b, "fromNode": prefix + "-" + a, "toNode": prefix + "-" + b, "toEnd": "arrow", "label": label})
    return nodes, edges




def render_svg(item: dict) -> str:
    esc = html.escape
    contents = [("premises", "材料碰撞", "；".join(p["id"] + "：「" + p["quote"] + "」" for p in item["premises"])),
                ("mechanism", "底層機制", item["mechanism"]),
                ("claim", LABELS[item["kind"]], item["claim"]),
                ("alternative", "另一個解釋", item["alternative"]),
                ("discriminator", "分辨的觀察", item["discriminator"]["observe"]),
                ("action", "下一層問題", item["adjacent"]["question"])]
    # Size each row from its real argument, including long inputs. No placeholder
    # captions, ellipses or clipped claims masquerading as a reasoning diagram.
    wrapped = [[line[n:n+17] for line in body.splitlines() for n in range(0,len(line),17)] for _,_,body in contents]
    heights = [max(170, 100 + 24 * max(len(lines) for lines in wrapped[start:start+3])) for start in (0,3)]
    second_y = heights[0] + 70
    middle1, middle2 = 20 + heights[0] // 2, second_y + heights[1] // 2
    blocks = [(role,title,wrapped[i],20+(i%3)*340,20 if i<3 else second_y,heights[i//3]) for i,(role,title,_) in enumerate(contents)]
    markup = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1020 {second_y+heights[1]+20}" role="img" aria-label="證據、機制、假說、反方與下一問的關係圖"><defs><marker id="arrow-{esc(item["id"])}" markerWidth="9" markerHeight="9" refX="8" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="none" stroke="#47685f"/></marker></defs>']
    for path, label, x, y in [(f"M320 {middle1} H358", "推導", 340, middle1-13), (f"M660 {middle1} H698", "條件", 680, middle1-13),
                             (f"M320 {middle2} H358", "競爭", 340, middle2-13), (f"M850 {20+heights[0]} V{second_y-25} H510 V{second_y-2}", "可反駁", 697, second_y-31),
                             (f"M660 {middle2} H698", "推進", 680, middle2-13)]:
        markup.append(f'<path d="{path}" fill="none" stroke="#47685f" stroke-width="2" marker-end="url(#arrow-{esc(item["id"])})"/><text x="{x}" y="{y}" text-anchor="middle" font-size="11" fill="#47685f">{label}</text>')
    for role, title, lines, x, y, height in blocks:
        markup.append(f'<a href="#{esc(item["id"])}-{role}" data-focus="{role}"><rect x="{x}" y="{y}" width="300" height="{height}" rx="14" fill="{ "#dbeade" if role == "claim" else "#fff9ef"}" stroke="#a8b8ad"/><text x="{x+20}" y="{y+40}" font-size="20" fill="#203f35">{esc(title)}</text>')
        for line_index, line in enumerate(lines):
            markup.append(f'<text x="{x+20}" y="{y+76+line_index*24}" font-size="15" fill="#41564d">{esc(line)}</text>')
        markup.append('</a>')
    markup.append('</svg>')
    return "".join(markup)



def render_html(question: str, analysis: dict, evidence: list[dict], review: dict, *, surface: str = "answer", return_url: str = "", navigation: dict | None = None) -> str:
    try:
        from . import thinking_map
    except ImportError:
        import thinking_map
    return thinking_map.render(question, analysis, evidence, review, surface=surface, return_url=return_url, navigation=navigation)
