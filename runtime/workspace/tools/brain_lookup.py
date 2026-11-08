"""Read-only lookup of the knowledge base for global entries (brain-ask).

Writes nothing: no vault, outputs, receipts or cache files. Material selection is
delegated to knowledge_material_selector.select_materials; this tool only adds
absolute paths, bounded excerpts, claim attribution sentences and outcome-page
file:// paths so an AI working outside the workspace can answer and cite.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True  # zero-write: never create __pycache__ in the workspace

TOOLS_DIR = Path(__file__).resolve().parent
CODE_ROOT = TOOLS_DIR.parent
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from tools.knowledge_material_selector import (  # noqa: E402
    completed_package_for_matching,
    path_is_inside_vault,
    select_materials,
)
from tools.obsidian_vault_adapter import (  # noqa: E402
    attribution_reader_line,
    extract_canonical_body_markdown,
    retained_original_location,
)
from tools.outcome_page_generator import read_markdown_record  # noqa: E402
from tools.workspace_paths import WorkspacePathError, bind_process_vault  # noqa: E402

SCHEMA = "creative_second_brain_brain_lookup_v1"
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
READ_FAILURE_REASONS = {"missing_registry", "invalid_jsonl"}  # selector reasons meaning the registry could not be read
EXCERPT_LIMIT = 600
MAX_KNOWLEDGE_POINTS = 5
MAX_EVIDENCE = 4

ANSWER_RULES = [
    "只用本結果內的內容，以及所列筆記的內容作答；不憑模型記憶補充庫內沒有的說法。",
    "引用原文時必須逐字照錄摘錄內容，不得改寫後當作原文。",
    "候選若帶有「主張歸屬」句，須照寫：那不是來源作者自己的主張時，不得說成來源作者的主張。",
    "答案末尾列出「引用：來源編號 · 筆記路徑 · 成果頁」，每個用到的來源一行；路徑用本結果給的絕對路徑，沒有成果頁便省略該項。",
    "摘錄只是片段；需要更多內容時，只可唯讀開啟本結果所列的筆記路徑。",
    "材料不足、status 是 no_material、或候選與問題不相干時，如實說明庫內沒有足夠材料，不要用猜測補上。",
    "這是唯讀快答：不得寫入、修改或整理知識庫，也不得保存思考紀錄或入庫。",
    "用戶要求存入、入庫、修訂或保存思考時，不執行任何寫入命令，只回覆 `$brain-think <問題>`（Claude Code 為 `/brain-think`）等命令，並請用戶回到知識庫工作區執行。",
]


def workspace_root_from_file() -> Path:
    return CODE_ROOT


def load_vault(root: Path) -> Path:
    config = root / "brain.json"
    if not config.is_file():
        raise FileNotFoundError("找不到 brain.json：" + str(root))
    try:
        data = json.loads(config.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"brain.json 不是有效的 JSON：{config}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError("brain.json 的內容不是物件，無法讀取 vault 設定：" + str(config))
    vault = str(data.get("vault") or "").strip()
    if not vault:
        raise ValueError("brain.json 沒有 vault 設定")
    return (root / vault).resolve()


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def focused_excerpt(text: str, terms: list[str], limit: int = EXCERPT_LIMIT) -> str:
    """Bounded excerpt starting near the first matched term (or the head of the text)."""
    flat = compact(text)
    if len(flat) <= limit:
        return flat
    lowered = flat.lower()
    positions = [lowered.find(term.lower()) for term in terms if term and lowered.find(term.lower()) >= 0]
    start = max(0, (min(positions) if positions else 0) - limit // 6)
    piece = flat[start:start + limit].strip()
    return ("…" if start > 0 else "") + piece + ("…" if start + limit < len(flat) else "")


def inside(path: Path, vault: Path) -> bool:
    try:
        return path_is_inside_vault(path, vault)
    except OSError:
        return False


def vault_path(vault: Path, relative: str) -> Path | None:
    if not relative:
        return None
    path = (vault / relative)
    return path if inside(path, vault) else None


def read_record(path: Path | None) -> tuple[dict[str, object], str]:
    if not path or not path.is_file():
        return {}, ""
    try:
        return read_markdown_record(path)
    except (OSError, UnicodeError):
        return {}, ""


def original_text(vault: Path, source_id: str, source_path: Path | None, fields: dict[str, object]) -> str:
    """The retained original, from the one location its retention mode owns."""
    mode = str(fields.get("source_retention_mode") or "").strip()
    try:
        location = retained_original_location(mode or None)
    except ValueError:
        return ""
    try:
        if location == "source_note_body":
            if not source_path:
                return ""
            return extract_canonical_body_markdown(source_path.read_text(encoding="utf-8-sig"))
        package = completed_package_for_matching(vault, source_id)
        if not package:
            return ""
        canonical = (package / "canonical_source.md").resolve()
        if not canonical.is_file() or not canonical.is_relative_to(vault.parent.resolve()):
            return ""
        return canonical.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError):
        return ""


def knowledge_point_entry(vault: Path, relative: str, terms: list[str], transcript_archive: bool) -> dict[str, Any] | None:
    path = vault_path(vault, relative)
    fields, body = read_record(path)
    if not path or not fields:
        return None
    text = str(fields.get("knowledge_point") or fields.get("definition") or "").strip()
    if not text:
        text = compact(body)
    entry: dict[str, Any] = {
        "kp_id": str(fields.get("kp_id") or path.stem),
        "path": str(path),
        "text": focused_excerpt(text, terms),
    }
    anchor = str(fields.get("source_anchor") or "").strip()
    if anchor:
        entry["evidence"] = focused_excerpt(anchor, terms)
    attribution = attribution_reader_line(
        fields.get("attribution_origin"),
        fields.get("attribution_label"),
        fields.get("attribution_relay"),
        transcript_archive=transcript_archive,
        verified_name=fields.get("attribution_verified_name"),
    )
    if attribution:
        entry["attribution"] = attribution
    return entry


def build_candidate(vault: Path, raw: dict[str, Any], terms: list[str]) -> dict[str, Any]:
    source_id = str(raw.get("source_id") or "")
    source_path = vault_path(vault, str(raw.get("source_note") or ""))
    app_path = vault_path(vault, str(raw.get("application_note") or ""))
    fields, _ = read_record(source_path)
    transcript_archive = str(fields.get("source_retention_mode") or "").strip() == "transcript_archive_required"
    matched = [str(item) for item in raw.get("matched_terms", [])] or terms

    candidate: dict[str, Any] = {
        "source_id": source_id,
        "title": str(raw.get("title") or ""),
        "score": raw.get("score"),
        "answer_material_status": raw.get("answer_material_status"),
        "summary": focused_excerpt(str(raw.get("summary") or ""), matched),
        "source_url": str(raw.get("source_url") or ""),
        "source_note_path": str(source_path) if source_path and source_path.is_file() else "",
        "application_note_path": str(app_path) if app_path and app_path.is_file() else "",
    }
    if raw.get("missing_materials"):
        candidate["missing_materials"] = raw["missing_materials"]

    original = original_text(vault, source_id, source_path, fields)
    candidate["original_excerpt"] = focused_excerpt(original, matched) if original else ""

    points = []
    for relative in list(raw.get("knowledge_point_paths") or [])[:MAX_KNOWLEDGE_POINTS]:
        entry = knowledge_point_entry(vault, str(relative), matched, transcript_archive)
        if entry:
            points.append(entry)
    candidate["knowledge_points"] = points

    evidence = []
    for item in list(raw.get("evidence_snippets") or [])[:MAX_EVIDENCE]:
        row = {"kind": item.get("kind", ""), "snippet": str(item.get("snippet") or "")}
        located = vault_path(vault, str(item.get("path") or ""))
        if located:
            row["path"] = str(located)
        evidence.append(row)
    candidate["evidence_snippets"] = evidence

    page = vault / "outcome-pages" / f"{source_id}_outcome.html"
    if source_id and page.is_file() and inside(page, vault):
        candidate["outcome_page_path"] = str(page.resolve())
        candidate["outcome_page_url"] = page.resolve().as_uri()
    return candidate


def lookup(root: Path, question: str, limit: int = DEFAULT_LIMIT, expansions: list[str] | None = None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": SCHEMA,
        "read_only": True,
        "workspace_root": str(root),
        "question": question,
        "answer_rules": list(ANSWER_RULES),
    }
    if limit > MAX_LIMIT:
        base["limit_applied"] = MAX_LIMIT
        base["limit_note"] = f"--limit {limit} 超過上限，已改以 {MAX_LIMIT} 計。"
        limit = MAX_LIMIT
    try:
        vault = load_vault(root)
        if not vault.is_dir():
            raise FileNotFoundError("找不到知識庫資料夾：" + str(vault))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {**base, "status": "error", "message": str(exc), "candidate_count": 0, "candidates": []}
    base["vault"] = str(vault)

    try:
        result = select_materials(query=question, vault=vault, limit=limit, expansions=expansions or [])
    except (Exception, SystemExit) as exc:  # never a traceback: the caller still needs the answer rules
        return {**base, "status": "error", "message": f"取材失敗：{exc}", "candidate_count": 0, "candidates": []}
    unreadable = [item for item in result.get("blocked_reasons") or [] if item.get("reason") in READ_FAILURE_REASONS]
    if unreadable and not result.get("candidates"):
        details = "；".join(f"{item['reason']}（{item.get('path', '')}{' 第 ' + item['line'] + ' 行' if item.get('line') else ''}）" for item in unreadable)
        return {**base, "status": "error", "message": "知識庫登記檔讀取失敗，不能當作庫內沒有材料：" + details,
                "blocked_reasons": result["blocked_reasons"], "candidate_count": 0, "candidates": []}

    terms = [str(item) for item in result.get("query_terms", [])]
    candidates = [build_candidate(vault, raw, terms) for raw in result.get("candidates", [])]
    out = {**base, "query_terms": terms, "candidate_count": len(candidates), "candidates": candidates}
    if not candidates:
        out["status"] = "no_material"
        out["message"] = "知識庫沒有選出與這個問題相關的材料。"
    else:
        out["status"] = "ready"
    if result.get("blocked_reasons"):
        out["blocked_reasons"] = result["blocked_reasons"]
        if unreadable:
            out["message"] = "部分登記檔讀取異常，下列結果可能不完整，作答時須如實說明。"
    return out


def render_text(result: dict[str, Any]) -> str:
    lines = [f"status: {result['status']}", f"question: {result['question']}", f"candidates: {result['candidate_count']}"]
    if result.get("message"):
        lines.append(f"message: {result['message']}")
    if result.get("limit_note"):
        lines.append(f"limit: {result['limit_note']}")
    for candidate in result["candidates"]:
        lines.append(f"- {candidate['source_id']} {candidate['title']}")
        for key in ("source_note_path", "application_note_path", "outcome_page_url"):
            if candidate.get(key):
                lines.append(f"  {key}: {candidate[key]}")
        for point in candidate["knowledge_points"]:
            lines.append(f"  {point['kp_id']}: {point['text']}")
            if point.get("attribution"):
                lines.append(f"    {point['attribution']}")
    lines.append("answer_rules:")
    lines.extend(f"  {index}. {rule}" for index, rule in enumerate(result["answer_rules"], 1))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only knowledge base lookup (writes nothing).")
    parser.add_argument("--question", required=True, help="Question to look up in the knowledge base.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--expand", action="append", default=[], help="Alternate wording of the question; repeatable.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--workspace", type=Path, default=None, help="Override the workspace root (default: the root this file belongs to).")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be greater than 0")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    root = (args.workspace or workspace_root_from_file()).resolve()
    try:
        bind_process_vault(root)
    except WorkspacePathError as exc:
        result = {"schema": SCHEMA, "read_only": True, "workspace_root": str(root), "question": args.question,
                  "answer_rules": list(ANSWER_RULES), "status": "error", "message": str(exc), "candidate_count": 0, "candidates": []}
    else:
        result = lookup(root, args.question, args.limit, args.expand)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render_text(result))
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
