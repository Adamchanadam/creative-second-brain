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
import unicodedata
from pathlib import Path
from uuid import uuid4

try:
    from .obsidian_vault_adapter import retained_note_body_offsets
    from .knowledge_direction import established_tensions, source_ids_in_paths
    from .knowledge_material_selector import select_materials
    from .obsidian_idea_retrieval import retrieve_question, load_concept_notes
    from .source_identity import normalize_url
    from . import thought_analysis as thinking
except ImportError:
    from obsidian_vault_adapter import retained_note_body_offsets
    from knowledge_direction import established_tensions, source_ids_in_paths
    from knowledge_material_selector import select_materials
    from obsidian_idea_retrieval import retrieve_question, load_concept_notes
    from source_identity import normalize_url
    import thought_analysis as thinking

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "ask_second_brain_v1"
MAX_EVIDENCE_NOTES = 10
MAX_SINGLE_SOURCE_BUNDLE_NOTES = 16
PACKET_BODY_MAX_CHARS = 20000
CANONICAL_BODY_HEADING = "### Canonical article body"
CANONICAL_BODY_END_HEADING = "## 知識用途分流"
PACKET_PROJECTION = "source_sections_omitted"
RETAINED_BODY_LABEL = "正文保存區"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def project_packet_note(text: str) -> tuple[str, str | None]:
    """Return the bounded Ask-packet copy, or a delimiter gap for a malformed source section."""
    try:
        bounds = retained_note_body_offsets(text)
    except ValueError:
        return text, "canonical_body_not_delimited"
    if bounds is None:
        projected = text
    else:
        start, end = bounds
        body = text[start:end]
        if len(body) <= PACKET_BODY_MAX_CHARS:
            projected = text
        else:
            notice = f"（{RETAINED_BODY_LABEL}共 {len(body)} 字元，未載入此問答材料；需要逐字核對時讀知識點筆記的證據或原檔。）\n"
            projected = text[:start] + notice + text[end:]

    infographics = list(re.finditer(r"(?m)^## 資訊圖[ \t]*(?=\n|\Z)", projected))
    if not infographics:
        return projected, None
    if len(infographics) != 1:
        return projected, "infographic_section_not_delimited"
    start = infographics[0].end()
    next_section = re.search(r"(?m)^## ", projected[start:])
    end = start + next_section.start() if next_section else len(projected)
    infographic = projected[start:end]
    if len(infographic) <= PACKET_BODY_MAX_CHARS:
        return projected, None
    notice = f"\n（資訊圖製作紀錄共 {len(infographic)} 字元，未載入此問答材料；需要時讀原檔。）\n\n"
    return projected[:start] + notice + projected[end:], None


def inside(root: Path, path: Path) -> Path:
    root = root.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError("path must remain inside its declared root")
    return resolved


EXPANSION_DECISION_GUIDANCE = '請加 --expand "同一問題的另一說法"（可重複，建議包含另一語言或同義詞）或 --no-expand "理由"。'


def comparable_question_text(value: str) -> str:
    """Normalise only the comparison boundary used by the prepare CLI."""
    value = unicodedata.normalize("NFKC", value).casefold().strip()
    while value and unicodedata.category(value[0]).startswith("P"):
        value = value[1:].lstrip()
    while value and unicodedata.category(value[-1]).startswith("P"):
        value = value[:-1].rstrip()
    return value


def cli_expansion_decision(
    question: str,
    expansions: list[str],
    no_expand: str | None,
    *,
    selected_only: bool,
) -> tuple[list[str], dict[str, object]]:
    """Validate the explicit CLI-only expansion decision before any output exists."""
    question_key = comparable_question_text(question)
    clean_expansions: list[str] = []
    seen: set[str] = set()
    for raw in expansions:
        item = raw.strip()
        if not item:
            raise ValueError(EXPANSION_DECISION_GUIDANCE)
        item_key = comparable_question_text(item)
        if item_key == question_key:
            raise ValueError(EXPANSION_DECISION_GUIDANCE)
        if item_key not in seen:
            seen.add(item_key)
            clean_expansions.append(item)
    if len(clean_expansions) > 5:
        raise ValueError("--expand 最多可有 5 個另一說法。")
    reason = no_expand.strip() if no_expand is not None else ""
    if no_expand is not None and not reason:
        raise ValueError(EXPANSION_DECISION_GUIDANCE)
    if clean_expansions and no_expand is not None:
        raise ValueError(EXPANSION_DECISION_GUIDANCE)
    if selected_only:
        return clean_expansions, {"mode": "not_applicable_selected_only"}
    if clean_expansions:
        return clean_expansions, {"mode": "expanded", "count": len(clean_expansions)}
    if no_expand is not None:
        return clean_expansions, {"mode": "declined", "reason": reason}
    raise ValueError(EXPANSION_DECISION_GUIDANCE)


def fit_tensions(tensions: list[dict], required_paths: list[str], room: int) -> tuple[list[dict], list[dict]]:
    """Admit established tensions in their given order, each only as a pair that fits the room left in the read limit.

    A tension needs the source notes of both sides that are not yet required (empty paths do not count).  It joins when
    all of them fit; one with no new note always joins.  One that does not fit adds no path and is reported instead.
    """
    seen, room = set(required_paths), max(0, room)
    included, over_capacity = [], []
    for tension in tensions:
        new = list(dict.fromkeys(path for path in (tension["source_note_path"], tension["target_source_note_path"]) if path and path not in seen))
        if len(new) > room:
            over_capacity.append({key: tension[key] for key in ("relation_id", "relation_type", "insight", "concept_names", "source_id", "target_source_id")})
            continue
        included.append(tension)
        seen.update(new)
        room -= len(new)
    return included, over_capacity


def prepare(
    question: str,
    vault: Path,
    limit: int = 4,
    notes: list[str] | None = None,
    selected_only: bool = False,
    topic_ids: list[str] | None = None,
    expansions: list[str] | None = None,
    expansion_decision: dict[str, object] | None = None,
) -> dict:
    if not question.strip() or not 1 <= limit <= 8:
        raise ValueError("provide a question and a limit between 1 and 8")
    vault = vault.resolve()
    if selected_only and not notes:
        raise ValueError("selected-only requires explicit evidence notes")
    received_expansions = [item for item in (expansions or []) if isinstance(item, str)]
    clean_expansions = [item.strip() for item in received_expansions if item.strip()]
    materials = {"candidates": [], "blocked_reasons": []} if selected_only else select_materials(
        query=question, vault=vault, limit=limit, topic_ids=topic_ids, expansions=clean_expansions,
    )
    graph = {} if selected_only else retrieve_question(query=question, vault=vault)
    graph_paths = list(graph.get("must_read_notes", []))
    required_paths = list(notes or [])
    for candidate in materials["candidates"]:
        required_paths.extend(candidate["must_read"])
    required_paths = list(dict.fromkeys(required_paths))
    single_source = len(materials["candidates"]) == 1 and len(required_paths) <= MAX_SINGLE_SOURCE_BUNDLE_NOTES
    # Established cross-source tensions around the selected sources: read both sides before answering.
    # A tension joins only as a pair, within the read limit left after the user's notes and the material candidates.
    selected_sources = {str(candidate.get("source_id") or "") for candidate in materials["candidates"]} | source_ids_in_paths(notes or [])
    tension_set = established_tensions(vault, selected_sources - {""})
    room = (MAX_SINGLE_SOURCE_BUNDLE_NOTES if single_source else MAX_EVIDENCE_NOTES) - len(required_paths)
    tensions, over_capacity = fit_tensions(tension_set["tensions"], required_paths, room)
    for tension in tensions:
        required_paths.extend(path for path in (tension["source_note_path"], tension["target_source_note_path"]) if path)
    required_paths = list(dict.fromkeys(required_paths))

    optional_knowledge_point_paths = []
    candidate_knowledge_point_paths = [candidate.get("knowledge_point_paths", []) for candidate in materials["candidates"]]
    for index in range(max((len(paths) for paths in candidate_knowledge_point_paths), default=0)):
        for paths_for_candidate in candidate_knowledge_point_paths:
            if index < len(paths_for_candidate):
                path = paths_for_candidate[index]
                if path and path not in required_paths:
                    optional_knowledge_point_paths.append(path)

    paths = [*required_paths, *graph_paths[:1]]
    paths.extend(item["path"] for item in graph.get("matched_idea_notes", []) if item.get("type") == "tension")
    paths.extend(optional_knowledge_point_paths)
    paths.extend(graph_paths[1:])
    evidence, gaps = [], list(graph.get("missing_or_weak_links", []))
    gaps.extend(materials.get("blocked_reasons", []))
    if over_capacity:
        gaps.append({"reason": "established_tensions_over_capacity", "message": f"{len(over_capacity)} 條已成立張力因可讀上限未納入；不可聲稱已回應這些張力。", "relation_ids": [item["relation_id"] for item in over_capacity]})
    unique_paths = list(dict.fromkeys(paths))
    bundle_limit = MAX_EVIDENCE_NOTES
    if single_source:
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
        relative_path = path.relative_to(vault).as_posix()
        projected = body
        delimiter_gap = None
        if path.relative_to(vault).parts[0] == "sources":
            projected, delimiter_gap = project_packet_note(body)
        if delimiter_gap:
            gaps.append({"path": relative_path, "reason": delimiter_gap})
        item = {"id": f"L{len(evidence)+1}", "path": relative_path, "sha256": digest(projected), "text": projected}
        if projected != body:
            item.update({"source_sha256": digest(body), "projection": PACKET_PROJECTION})
        evidence.append(item)
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
    return {"schema_version": SCHEMA, "session_id": f"ASK-{uuid4().hex}", "status": status, "question": question.strip(), "query_expansions": received_expansions, "expansion_decision": expansion_decision or {"mode": "not_recorded"}, "vault": str(vault), "evidence": evidence, "deferred_paths": deferred, "required_paths": required_paths, "deferred_required_paths": deferred_required, "local_explorations": adjacent, "established_tensions": tensions, "established_tensions_omitted": tension_set["omitted"], "established_tensions_over_capacity": over_capacity, "gaps": gaps, "source_candidates": materials["candidates"], "research_topic_ids": list(topic_ids or []), "graph_path": graph.get("path_text", ""), "web_status": "not_searched", "answer_status": "not_written", "reasoning_contract": "thinking_v2", "reading_tasks": ["Read evidence bodies and distinguish original records from their derivatives", "Find a tension or difference in mechanism, not only topical similarity", "Search for the strongest alternative; selected notes do not exhaust the vault", "Separate source statements, inference steps and discriminating observations"] + (["Required source bundle is incomplete: do not answer from this packet; narrow the question or explicitly select one source and prepare again."] if deferred_required else []) + (["Established cross-source tensions: read both sides, then answer each in tension_responses (one entry per relation_id)."] if tensions else []) + ([f"{len(over_capacity)} established tension(s) did not fit the read limit (see established_tensions_over_capacity): this packet does not answer them, so do not claim a response; narrow the selected notes and prepare again to cover them."] if over_capacity else []), "requested_notes": list(notes or [])}


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
        if item.get("projection") == PACKET_PROJECTION:
            projected, delimiter_gap = project_packet_note(text)
            if (delimiter_gap == "canonical_body_not_delimited" or projected == text or digest(text) != item.get("source_sha256")
                    or projected != item["text"] or digest(item["text"]) != item["sha256"]):
                raise ValueError("local evidence changed; prepare and reread before answering")
        elif "projection" in item or "source_sha256" in item:
            raise ValueError("invalid local evidence projection")
        elif digest(text) != item["sha256"] or digest(item["text"]) != item["sha256"]:
            raise ValueError("local evidence changed; prepare and reread before answering")
        if item["id"] in lookup:
            raise ValueError("duplicate evidence id")
        lookup[item["id"]] = item
    return lookup


def check_tension_responses(packet: dict, answer: dict) -> None:
    """Every established tension in the packet needs one substantive response; the rule is in the semantic-index specification."""
    expected = [item["relation_id"] for item in packet.get("established_tensions") or []]
    if not expected:
        return
    responses = answer.get("tension_responses")
    if not isinstance(responses, list) or not responses:
        raise ValueError("tension_responses_missing: the packet carries established tensions; answer each one in tension_responses as {relation_id, response}")
    answered = set()
    for item in responses:
        response = item.get("response") if isinstance(item, dict) else None
        if not isinstance(item, dict) or item.get("relation_id") not in expected or not isinstance(response, str) or not response.strip():
            raise ValueError("tension_response_invalid: each entry needs a relation_id from the packet and a non-empty response")
        answered.add(item["relation_id"])
    if len(answered) != len(responses):
        raise ValueError("tension_response_invalid: one response per relation_id")
    missing = [relation_id for relation_id in expected if relation_id not in answered]
    if missing:
        raise ValueError("tension_response_uncovered: no response for " + ", ".join(missing))


def review_answer(packet: dict, answer: dict, review: dict | None = None) -> dict:
    if packet.get("status") == "needs_review" or packet.get("deferred_required_paths"):
        raise ValueError("question packet has an incomplete required source bundle; prepare a complete packet before answering")
    lookup = check_evidence(packet)
    if not isinstance(answer, dict):
        raise ValueError("answer must be an object")
    if answer.get("session_id") != packet["session_id"]:
        raise ValueError("answer belongs to another question")
    check_tension_responses(packet, answer)
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
    parser.add_argument("--expand", action="append", default=[], help="Alternate wording of the same question; repeatable and recorded in the packet.")
    parser.add_argument("--no-expand", help="One-sentence reason no alternate wording is needed for this question.")
    parser.add_argument("--selected-only", action="store_true", help="Use only explicitly selected notes after runtime material selection; not whole-vault coverage")
    parser.add_argument("--web-id")
    parser.add_argument("--suitability", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/ask_second_brain")
    args = parser.parse_args()
    try:
        out = inside(ROOT, args.output_dir)
        if args.action == "prepare":
            clean_expansions, decision = cli_expansion_decision(
                args.question or "", args.expand, args.no_expand, selected_only=args.selected_only,
            )
            result = prepare(args.question or "", inside(ROOT, args.vault), notes=args.note, selected_only=args.selected_only, topic_ids=args.topic_id, expansions=clean_expansions, expansion_decision=decision)
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
