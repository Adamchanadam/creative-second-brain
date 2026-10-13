"""Local-first evidence packets and reviewed answers for the active AI runtime.

No model/API calls, automatic web queries or formal vault writes are performed.
The operating contract lives in the semantic-index specification.
"""
from __future__ import annotations
import os

import argparse
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

try:
    from .knowledge_material_selector import select_materials
    from .obsidian_idea_retrieval import retrieve_question, load_concept_notes
    from .source_identity import normalize_url
    from . import thought_analysis as thinking
except ImportError:
    from knowledge_material_selector import select_materials
    from obsidian_idea_retrieval import retrieve_question, load_concept_notes
    from source_identity import normalize_url
    import thought_analysis as thinking

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ask_second_brain_v1"
MAX_EVIDENCE_NOTES = 10
MAX_SINGLE_SOURCE_BUNDLE_NOTES = 16


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("path must remain inside its declared root")
    return resolved


def prepare(
    question: str,
    vault: Path,
    limit: int = 4,
    notes: list[str] | None = None,
    selected_only: bool = False,
    topic_ids: list[str] | None = None,
) -> dict:
    if not question.strip() or not 1 <= limit <= 8:
        raise ValueError("provide a question and a limit between 1 and 8")
    vault = vault.resolve()
    if selected_only and not notes:
        raise ValueError("selected-only requires explicit evidence notes")
    materials = {"candidates": [], "blocked_reasons": []} if selected_only else select_materials(
        query=question, vault=vault, limit=limit, topic_ids=topic_ids,
    )
    graph = {} if selected_only else retrieve_question(query=question, vault=vault)
    graph_paths = list(graph.get("must_read_notes", []))
    required_paths = list(notes or [])
    for candidate in materials["candidates"]:
        required_paths.extend(candidate["must_read"])
    required_paths = list(dict.fromkeys(required_paths))
    paths = [*required_paths, *graph_paths[:1]]
    paths.extend(item["path"] for item in graph.get("matched_idea_notes", []) if item.get("type") == "tension")
    paths.extend(graph_paths)
    evidence, gaps = [], list(graph.get("missing_or_weak_links", []))
    gaps.extend(materials.get("blocked_reasons", []))
    unique_paths = list(dict.fromkeys(paths))
    bundle_limit = MAX_EVIDENCE_NOTES
    if len(materials["candidates"]) == 1 and len(required_paths) <= MAX_SINGLE_SOURCE_BUNDLE_NOTES:
        bundle_limit = max(bundle_limit, len(required_paths))
    deferred = []
    for relative in unique_paths:
        if len(evidence) >= bundle_limit:
            deferred.append(relative)
            continue
        try:
            path = inside(vault, Path(relative))
            if path.suffix != ".md" or path.relative_to(vault).parts[0] not in {"sources", "applications", "knowledge-points", "concepts", "source-archives"}:
                raise ValueError("not a knowledge evidence note")
            body = path.read_text(encoding="utf-8-sig")
        except (ValueError, OSError) as exc:
            gaps.append({"path": relative, "reason": str(exc)})
            continue
        evidence.append({"id": f"L{len(evidence)+1}", "path": path.relative_to(vault).as_posix(), "sha256": digest(body), "text": body})
    deferred_required = [path for path in deferred if path in set(required_paths)]
    if deferred_required:
        gaps.append({"reason": "required_source_bundle_incomplete", "message": "必要來源包超出可讀上限；不可用部分材料作答。請收窄問題或明示選定來源後重新 prepare。", "paths": deferred_required})
    # Preserve typed edges rather than inventing topical recommendations.
    concepts = {} if selected_only else load_concept_notes(vault)
    seed = concepts.get(graph.get("seed_note", {}).get("title", ""))
    adjacent = []
    if seed:
        for relation in ("反例", "形成取捨", "延伸", "可應用於", "品味線索", "需要查證"):
            for title in seed.links_by_section.get(relation, []):
                if title in concepts:
                    adjacent.append({"from": seed.title, "relation": relation, "title": title, "path": concepts[title].rel_path, "status": "candidate_only"})
    status = "insufficient_evidence" if not evidence else "needs_review" if deferred_required else "context_ready"
    return {"schema_version": SCHEMA, "session_id": f"ASK-{uuid4().hex}", "status": status, "question": question.strip(), "vault": str(vault), "evidence": evidence, "deferred_paths": deferred, "required_paths": required_paths, "deferred_required_paths": deferred_required, "local_explorations": adjacent, "gaps": gaps, "source_candidates": materials["candidates"], "research_topic_ids": list(topic_ids or []), "graph_path": graph.get("path_text", ""), "web_status": "not_searched", "answer_status": "not_written", "reasoning_contract": "thinking_v2", "reading_tasks": ["Read evidence bodies and distinguish original records from their derivatives", "Find a tension or difference in mechanism, not only topical similarity", "Search for the strongest alternative; selected notes do not exhaust the vault", "Separate source statements, inference steps and discriminating observations"] + (["Required source bundle is incomplete: do not answer from this packet; narrow the question or explicitly select one source and prepare again."] if deferred_required else []), "requested_notes": list(notes or [])}


def check_evidence(packet: dict) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA:
        raise ValueError("unsupported question packet")
    if not re.fullmatch(r"ASK-[0-9a-f]{32}", packet.get("session_id", "")):
        raise ValueError("invalid session id")
    lookup = {}
    for item in packet["evidence"]:
        path = inside(Path(packet["vault"]), Path(item["path"]))
        if path.suffix != ".md" or not re.fullmatch(r"L[1-9][0-9]*", item["id"]):
            raise ValueError("invalid local evidence")
        text = path.read_text(encoding="utf-8-sig")
        if digest(text) != item["sha256"] or digest(item["text"]) != item["sha256"]:
            raise ValueError("local evidence changed; prepare and reread before answering")
        if item["id"] in lookup:
            raise ValueError("duplicate evidence id")
        lookup[item["id"]] = item
    return lookup


def review_answer(packet: dict, answer: dict, review: dict | None = None) -> dict:
    if packet.get("status") == "needs_review" or packet.get("deferred_required_paths"):
        raise ValueError("question packet has an incomplete required source bundle; prepare a complete packet before answering")
    lookup = check_evidence(packet)
    if not isinstance(answer, dict):
        raise ValueError("answer must be an object")
    if answer.get("session_id") != packet["session_id"]:
        raise ValueError("answer belongs to another question")
    web = answer.get("web_sources", [])
    for item in web:
        if not item.get("readback") or not item.get("title") or not item.get("retrieved_at"):
            raise ValueError("web sources require actual readback, title and retrieval time")
        url = normalize_url(item["url"])
        if not isinstance(item.get("id"), str) or not re.fullmatch(r"W[1-9][0-9]*", item["id"]) or item["id"] in lookup:
            raise ValueError("duplicate or invalid web evidence id")
        lookup[item["id"]] = {**item, "url": url, "text": item["readback"]}
    if "analysis" in answer:
        if web and not answer.get("web_reason"):
            raise ValueError("explain why web search followed local retrieval")
        thinking.validate_analysis(answer["analysis"], lookup)
        if review is not None:
            thinking.validate_review(packet, answer, review)
        return {**answer, "schema_version": SCHEMA, "status": "complete" if review else "review_required",
                "semantic_acceptance": "reviewed_reasoning_not_proven_truth" if review else "requires_runtime_review",
                "review_target_sha256": thinking.review_target(packet, answer), "review": review,
                "question": packet["question"], "evidence": list(lookup.values()), "formal_ingest_complete": False}
    claims = answer.get("claims", [])
    if not claims:
        raise ValueError("answer needs explicit claims, not a retrieval summary")
    for claim in claims:
        if claim.get("kind") not in {"supported", "inference", "gap"} or not claim.get("text", "").strip():
            raise ValueError("claims require text and supported/inference/gap classification")
        citations = claim.get("citations", [])
        if claim["kind"] != "gap" and not citations:
            raise ValueError("supported claims and inferences require evidence")
        for citation in citations:
            source = lookup.get(citation.get("id"))
            if not source or not citation.get("quote") or citation["quote"] not in source["text"]:
                raise ValueError("citation must match actual evidence text")
    explorations = answer.get("explorations", [])
    if len(explorations) > 3:
        raise ValueError("use at most three meaningful explorations")
    for item in explorations:
        if item.get("origin") not in {"local", "web"} or not all(item.get(key) for key in ("question", "why_adjacent", "new_value", "evidence_ids")):
            raise ValueError("exploration needs an evidenced bridge and new value")
        if any(key not in lookup for key in item["evidence_ids"]):
            raise ValueError("unknown exploration evidence")
        if item["origin"] == "local" and any(not key.startswith("L") for key in item["evidence_ids"]):
            raise ValueError("local exploration cannot be labelled from web evidence")
        if item["origin"] == "web" and not any(key.startswith("W") for key in item["evidence_ids"]):
            raise ValueError("web exploration needs read-back web evidence")
    if web and not answer.get("web_reason"):
        raise ValueError("explain why web search followed local retrieval")
    return {**answer, "schema_version": SCHEMA, "status": "citation_checked", "semantic_acceptance": "requires_runtime_review", "question": packet["question"], "formal_ingest_complete": False}


def intake_handoff(packet: dict, answer: dict, web_id: str, suitability: dict) -> dict:
    reviewed = review_answer(packet, answer)
    source = next((s for s in reviewed.get("web_sources", []) if s["id"] == web_id), None)
    if source is None:
        raise ValueError("select a read-back web source")
    for dimension in ("taste", "level", "claims", "novelty"):
        value = suitability.get(dimension, {})
        if value.get("decision") != "suitable" or not str(value.get("reason", "")).strip():
            raise ValueError(f"candidate needs a reasoned suitability decision: {dimension}")
    return {"schema_version": "second_brain_growth_handoff_v1", "status": "candidate_ready_for_ingest", "session_id": packet["session_id"], "question": packet["question"], "url": normalize_url(source["url"]), "suitability": suitability, "formal_ingest_complete": False, "entry": "tools/compiler_worker_v2.py accept-job", "source_id_allocation": "use current registry; do not reuse an existing source id", "continue_with": "docs/Compiler Worker與Knowledge Package設計及驗收規格.md", "post_ingest": "tools/obsidian_semantic_maintenance.py --new-content-candidates --write-receipts --check --json"}


def render(packet: dict, answer: dict) -> str:
    if "analysis" in answer:
        return thinking.markdown(packet["question"], answer["analysis"], answer.get("evidence", packet["evidence"]), answer.get("review"))
    evidence = {e["id"]: str(inside(Path(packet["vault"]), Path(e["path"]))) for e in packet["evidence"]}
    evidence.update({s["id"]: normalize_url(s["url"]) for s in answer.get("web_sources", [])})
    lines = [f"# 問我的第二大腦：{packet['question']}", "", "## 回答", ""]
    labels = {"supported": "有據判斷", "inference": "延伸推論", "gap": "資料缺口"}
    for claim in answer["claims"]:
        refs = " ".join(f"[{c['id']}](<{evidence[c['id']]}>)" for c in claim.get("citations", []))
        lines.extend([f"{labels[claim['kind']]}：{claim['text']} {refs}", ""])
    lines.extend(["## 相鄰探索", ""])
    for item in answer.get("explorations", []):
        refs = " ".join(f"[{key}](<{evidence[key]}>)" for key in item["evidence_ids"])
        lines.extend([f"### {item['question']}", "", f"{'庫內' if item['origin']=='local' else '網上延伸'}：{item['why_adjacent']} {refs}", "", f"新角度：{item['new_value']}", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="問我的第二大腦：供當前 AI 執行的本地優先問答流程")
    parser.add_argument("action", choices=("prepare", "answer", "intake-handoff"))
    parser.add_argument("--question")
    parser.add_argument("--vault", type=Path, default=ROOT / os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault"))
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--answer", type=Path)
    parser.add_argument("--review", type=Path, help="Substantive semantic review bound to the answer/evidence hash")
    parser.add_argument("--note", action="append", default=[], help="Explicit vault-relative evidence note; repeat to prioritize contrasting sources")
    parser.add_argument("--topic-id", action="append", default=[], help="Optional bounded research topic filter; it selects evidence only and never creates work.")
    parser.add_argument("--selected-only", action="store_true", help="Use only explicitly selected notes after runtime material selection; not whole-vault coverage")
    parser.add_argument("--web-id")
    parser.add_argument("--suitability", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/ask_second_brain")
    args = parser.parse_args()
    try:
        out = inside(ROOT, args.output_dir)
        if args.action == "prepare":
            result = prepare(args.question or "", inside(ROOT, args.vault), notes=args.note, selected_only=args.selected_only, topic_ids=args.topic_id)
        else:
            packet = json.loads(inside(ROOT, args.packet).read_text(encoding="utf-8"))
            inside(ROOT, Path(packet["vault"]))
            answer = json.loads(inside(ROOT, args.answer).read_text(encoding="utf-8"))
            review = json.loads(inside(ROOT, args.review).read_text(encoding="utf-8")) if args.review else None
            result = review_answer(packet, answer, review) if args.action == "answer" else intake_handoff(packet, answer, args.web_id, json.loads(inside(ROOT, args.suitability).read_text(encoding="utf-8")))
        if result["status"] == "review_required":
            print(json.dumps({"status": "review_required", "review_target_sha256": result["review_target_sha256"], "message": "Read the actual sources and candidate; supply a version-bound semantic review before delivery."}, ensure_ascii=False))
            return 1
        if args.action == "answer" and "analysis" in result:
            try:
                from .thinking_archive import archive_answer
            except ImportError:
                from thinking_archive import archive_answer
            receipt = archive_answer(Path(packet["vault"]), packet, answer, review)
            print(json.dumps(receipt, ensure_ascii=False))
            return 0
        out.mkdir(parents=True, exist_ok=True)
        target = inside(ROOT, out / f"{result['session_id']}-{args.action}.json")
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if target.exists() and target.read_text(encoding="utf-8") != serialized:
            raise ValueError("existing result differs; preserve it and use another output directory")
        markdown = render(packet, result) if args.action == "answer" else None
        md_target = inside(ROOT, target.with_suffix(".md"))
        if markdown is not None and md_target.exists() and md_target.read_text(encoding="utf-8") != markdown:
            raise ValueError("existing reader answer differs; preserve it and use another output directory")
        html_target = target.with_suffix(".html")
        webpage = thinking.render_html(packet["question"], result["analysis"], result["evidence"], result["review"]) if args.action == "answer" and "analysis" in result else None
        if webpage is not None and html_target.exists() and html_target.read_text(encoding="utf-8") != webpage:
            raise ValueError("existing interactive answer differs; use another output directory")
        target.write_text(serialized, encoding="utf-8")
        if args.action == "answer":
            md_target.write_text(markdown, encoding="utf-8")
        if webpage is not None:
            html_target.write_text(webpage, encoding="utf-8")
        if target.read_text(encoding="utf-8") != serialized or (webpage is not None and html_target.read_text(encoding="utf-8") != webpage):
            raise ValueError("output readback failed")
        print(json.dumps({"status": result["status"], "path": str(target), "session_id": result["session_id"], "reader": str(html_target) if webpage else str(md_target) if markdown else None}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps({"status": "blocked", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
