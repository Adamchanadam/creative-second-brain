"""Retrieve idea-layer paths from the Obsidian 2nd Brain vault.

This tool does not answer from model memory. It reads only vault notes and
registries, then emits a receipt that shows how a question travels through the
idea graph back to record IDs.
"""

from __future__ import annotations
import os

import argparse
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .obsidian_vault_adapter import concept_is_active, frontmatter_fields_from_text
except ImportError:
    from obsidian_vault_adapter import concept_is_active, frontmatter_fields_from_text


SCHEMA_VERSION = "obsidian_idea_retrieval_receipt_v1"
ACCEPTANCE_SCHEMA_VERSION = "obsidian_idea_retrieval_acceptance_v1"
DEFAULT_VAULT = Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault"))
DEFAULT_OUTPUT_DIR = Path("outputs/obsidian_idea_retrieval")
TOOL_PATH = "tools/obsidian_idea_retrieval.py"

NOTE_LINK_RE = re.compile(r"(?<!!)\[\[([^\]#|]+)(?:[#|][^\]]*)?\]\]")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
RECORD_ID_RE = re.compile(r"\b(?:KS|KP|APP|SRC|REL)-[A-Z0-9][A-Za-z0-9_-]*\b")


@dataclass(frozen=True)
class StandardQuestion:
    question_id: str
    question: str
    seed_title: str
    purpose: str


@dataclass
class ConceptNote:
    title: str
    path: Path
    rel_path: str
    note_type: str
    idea_type: str
    graph_role: str
    graph_question: str
    one_liner: str
    body: str
    fields: dict[str, Any]
    links_by_section: dict[str, list[str]]
    source_records: list[str]
    application_records: list[str]
    knowledge_point_records: list[str]


STANDARD_QUESTIONS: tuple[StandardQuestion, ...] = (
    StandardQuestion(
        "Q01_ai_tool_selection",
        "我應如何選 AI 工具？",
        "如何選擇 AI 工具",
        "驗證工具選擇問題能同時回到 hub、tension、taste signal、concept 和 record。",
    ),
    StandardQuestion(
        "Q02_content_intake_value",
        "如何判斷一篇內容值得入庫？",
        "如何判斷一篇內容值得入庫",
        "驗證入庫判斷不只看摘要，而能回到 information gain、行動用途和品味線索。",
    ),
    StandardQuestion(
        "Q03_taste_preference_signal",
        "甚麼內容會反映我的品味偏好？",
        "品味與生活策展",
        "驗證品味問題能找回 taste signal、生活場景、拒絕邊界與來源證據。",
    ),
    StandardQuestion(
        "Q04_taste_candidate_pool",
        "如何建立個人品味候選池？",
        "如何建立個人品味候選池",
        "驗證候選池問題能由 application scene 連回品味 hub 與採納證據。",
    ),
    StandardQuestion(
        "Q05_learning_creation_traps",
        "我現在學習／創作應避開甚麼陷阱？",
        "學習方法與記憶",
        "驗證學習與創作問題能回到 tension、學習 concept 和可提取行動。",
    ),
)

SECTION_PRIORITY = (
    "連到 Hub",
    "形成取捨",
    "品味線索",
    "可應用於",
    "核心概念",
    "支持",
    "延伸",
    "相關概念",
)
IDEA_TYPES = {"hub", "tension", "taste_signal", "application_scene"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Retrieve idea-layer paths from the Obsidian vault.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--query", help="Natural-language question to route through the idea graph.")
    group.add_argument("--five-question-acceptance", action="store_true", help="Run the five standard retrieval checks.")
    parser.add_argument("--vault", type=Path, default=DEFAULT_VAULT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write-receipts", action="store_true", help="Write JSON receipts under the output directory.")
    parser.add_argument("--check", action="store_true", help="Return non-zero unless the selected check is ready.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path.cwd().resolve()
    vault = resolve_inside(root, args.vault)
    output_dir = resolve_inside(root, args.output_dir)

    if args.five_question_acceptance:
        result = run_five_question_acceptance(vault=vault, output_dir=output_dir, write_receipts=args.write_receipts)
        print_result(result, args.json)
        return 1 if args.check and result["status"] != "ready" else 0

    receipt = retrieve_question(query=str(args.query), vault=vault)
    if args.write_receipts:
        receipt_path = write_receipt(output_dir, receipt_filename(receipt), receipt)
        receipt["receipt_path"] = receipt_path
    print_result(receipt, args.json)
    return 1 if args.check and receipt["status"] != "ready" else 0


def retrieve_question(*, query: str, vault: Path, question_id: str = "", seed_title: str = "") -> dict[str, Any]:
    notes = load_concept_notes(vault)
    source_registry, kp_registry = load_record_registries(vault)
    selected_seed_title = seed_title or seed_for_query(query)
    seed = notes.get(selected_seed_title) if selected_seed_title else best_note_for_query(notes, query)
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    if not seed:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "insufficient_evidence",
            "question_id": question_id,
            "question": query,
            "generated_at": generated_at,
            "vault": str(vault),
            "retrieval_path": [],
            "path_text": "",
            "must_read_notes": [],
            "record_ids": [],
            "record_paths": [],
            "answer_summary": "vault 內找不到足夠接近的 idea note；不可把 AI 記憶當成本地知識庫提取結果。",
            "missing_or_weak_links": [{"reason": "missing_seed_idea", "seed_title": selected_seed_title or ""}],
            "bridge_backlog": ["為這條問題建立或連接一個 application_scene / hub，再補 tension、taste signal、concept 和 record。"],
            "truth_boundary": truth_boundary(),
        }

    selected_notes, missing_links = select_route(seed, notes)
    grouped = group_notes_by_type(selected_notes)
    record_ids = collect_record_ids(selected_notes)
    record_paths = resolve_record_paths(record_ids, vault, source_registry, kp_registry)
    status, weak_links = classify_receipt(seed, grouped, record_ids, record_paths, missing_links)
    bridge_backlog = build_bridge_backlog(seed, grouped, record_ids, record_paths, missing_links)
    retrieval_path = build_retrieval_path(query, seed, grouped, record_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "question_id": question_id,
        "question": query,
        "generated_at": generated_at,
        "vault": str(vault),
        "seed_note": note_summary(seed),
        "matched_idea_notes": [note_summary(note) for note in selected_notes if note.idea_type in IDEA_TYPES],
        "matched_concept_notes": [note_summary(note) for note in selected_notes if note.idea_type not in IDEA_TYPES],
        "retrieval_path": retrieval_path,
        "path_text": render_path_text(retrieval_path),
        "must_read_notes": must_read_notes(selected_notes, record_paths),
        "record_ids": record_ids,
        "record_paths": record_paths,
        "answer_summary": build_answer_summary(seed, grouped, record_ids),
        "missing_or_weak_links": weak_links,
        "bridge_backlog": bridge_backlog,
        "truth_boundary": truth_boundary(),
    }


def run_five_question_acceptance(*, vault: Path, output_dir: Path, write_receipts: bool) -> dict[str, Any]:
    receipts: list[dict[str, Any]] = []
    receipt_paths: list[str] = []
    for item in STANDARD_QUESTIONS:
        receipt = retrieve_question(query=item.question, vault=vault, question_id=item.question_id, seed_title=item.seed_title)
        receipt["acceptance_purpose"] = item.purpose
        if write_receipts:
            receipt_paths.append(write_receipt(output_dir / "receipts", f"{item.question_id}.json", receipt))
        receipts.append(receipt)

    ready_count = sum(1 for receipt in receipts if receipt["status"] == "ready")
    needs_bridge_count = sum(1 for receipt in receipts if receipt["status"] == "needs_bridge")
    insufficient_count = sum(1 for receipt in receipts if receipt["status"] == "insufficient_evidence")
    status = "ready" if ready_count >= 3 and insufficient_count == 0 else "needs_review"
    result = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "status": status,
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "vault": str(vault),
        "acceptance_rule": "五條問題全部有 receipt；至少三條 ready，其餘最多 needs_bridge；任何 insufficient_evidence 都不可升級。",
        "ready_count": ready_count,
        "needs_bridge_count": needs_bridge_count,
        "insufficient_evidence_count": insufficient_count,
        "receipt_paths": receipt_paths,
        "receipts": receipts,
    }
    if write_receipts:
        result["summary_path"] = write_receipt(output_dir, "five_question_acceptance.json", result)
    return result


def load_concept_notes(vault: Path) -> dict[str, ConceptNote]:
    concept_dir = vault / "concepts"
    notes: dict[str, ConceptNote] = {}
    if not concept_dir.exists():
        return notes
    for path in sorted(concept_dir.glob("*.md")):
        fields, body = read_markdown(path)
        if not concept_is_active(fields):
            continue
        title = str(fields.get("title") or fields.get("concept") or path.stem).strip()
        if not title:
            continue
        links_by_section = parse_links_by_section(body)
        source_records = list_values(fields.get("source_records"))
        application_records = list_values(fields.get("application_records"))
        knowledge_point_records = list_values(fields.get("knowledge_point_records"))
        note = ConceptNote(
            title=title,
            path=path,
            rel_path=vault_relative(path, vault),
            note_type=str(fields.get("type") or "").strip(),
            idea_type=str(fields.get("idea_type") or "").strip() or "concept",
            graph_role=str(fields.get("graph_role") or "").strip(),
            graph_question=str(fields.get("graph_question") or "").strip(),
            one_liner=section_first_sentence(body, "一句話"),
            body=body,
            fields=fields,
            links_by_section=links_by_section,
            source_records=source_records,
            application_records=application_records,
            knowledge_point_records=knowledge_point_records,
        )
        notes[title] = note
        notes.setdefault(path.stem, note)
    return notes


def load_record_registries(vault: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    sources: dict[str, str] = {}
    for row in read_jsonl(vault / "_registry" / "sources.jsonl"):
        source_id = str(row.get("source_id") or "").strip()
        source_path = str(row.get("source_note_path") or "").strip()
        if source_id and source_path:
            sources[source_id] = source_path

    kp_paths: dict[str, list[str]] = {}
    for row in read_jsonl(vault / "_registry" / "knowledge_points.jsonl"):
        kp_id = str(row.get("kp_id") or "").strip()
        kp_path = str(row.get("kp_note_path") or "").strip()
        if kp_id and kp_path:
            kp_paths.setdefault(kp_id, []).append(kp_path)
    return sources, kp_paths


def select_route(seed: ConceptNote, notes: dict[str, ConceptNote]) -> tuple[list[ConceptNote], list[dict[str, str]]]:
    selected: list[ConceptNote] = []
    missing_links: list[dict[str, str]] = []
    add_note(selected, seed)

    for heading in SECTION_PRIORITY:
        for title in seed.links_by_section.get(heading, []):
            target = notes.get(title)
            if target:
                add_note(selected, target)
            else:
                missing_links.append({"from": seed.title, "section": heading, "target": title})

    add_backlinks_if_needed(seed, selected, notes)
    return selected, missing_links


def add_backlinks_if_needed(seed: ConceptNote, selected: list[ConceptNote], notes: dict[str, ConceptNote]) -> None:
    grouped = group_notes_by_type(selected)
    missing_types = {"hub", "tension", "taste_signal", "application_scene"} - set(grouped)
    if not missing_types:
        return
    for note in unique_notes(notes):
        if note.title == seed.title or note.idea_type not in missing_types:
            continue
        if seed.title in all_links(note):
            add_note(selected, note)
            grouped = group_notes_by_type(selected)
            missing_types = {"hub", "tension", "taste_signal", "application_scene"} - set(grouped)
            if not missing_types:
                return


def group_notes_by_type(notes: list[ConceptNote]) -> dict[str, list[ConceptNote]]:
    grouped: dict[str, list[ConceptNote]] = {}
    for note in notes:
        grouped.setdefault(note.idea_type if note.idea_type in IDEA_TYPES else "concept", []).append(note)
    return grouped


def classify_receipt(
    seed: ConceptNote,
    grouped: dict[str, list[ConceptNote]],
    record_ids: list[str],
    record_paths: list[dict[str, str]],
    missing_links: list[dict[str, str]],
) -> tuple[str, list[dict[str, Any]]]:
    weak_links: list[dict[str, Any]] = []
    if not record_ids:
        return "insufficient_evidence", [{"reason": "missing_record_ids", "note": seed.title}]

    required = {"hub", "tension", "taste_signal", "application_scene", "concept"}
    covered = {key for key, values in grouped.items() if key in required and values}
    missing_types = sorted(required - covered)
    if missing_types:
        weak_links.append({"reason": "missing_idea_layer_type", "types": missing_types})

    unresolved_records = [row for row in record_paths if row.get("status") != "found"]
    if unresolved_records:
        weak_links.append({"reason": "unresolved_record_paths", "records": unresolved_records[:8]})

    if missing_links:
        weak_links.append({"reason": "missing_wiki_targets", "links": missing_links[:8]})

    if len(covered) >= 4 and not missing_links and len(record_ids) >= 1:
        return "ready", weak_links
    return "needs_bridge", weak_links


def build_bridge_backlog(
    seed: ConceptNote,
    grouped: dict[str, list[ConceptNote]],
    record_ids: list[str],
    record_paths: list[dict[str, str]],
    missing_links: list[dict[str, str]],
) -> list[str]:
    backlog: list[str] = []
    for idea_type, label in (
        ("hub", "補 hub，讓問題有主題入口"),
        ("tension", "補 tension，讓答案保留取捨"),
        ("taste_signal", "補 taste signal，讓品味偏好可被重用"),
        ("application_scene", "補 application_scene，讓知識可轉成行動"),
        ("concept", "補 concept，讓答案有可重用想法單位"),
    ):
        if not grouped.get(idea_type):
            backlog.append(f"{seed.title}: {label}")
    if not record_ids:
        backlog.append(f"{seed.title}: 補 record ID，否則不可查證。")
    unresolved = [row["record_id"] for row in record_paths if row.get("status") != "found"]
    if unresolved:
        backlog.append("補 record path 對應：" + "、".join(unresolved[:8]))
    if missing_links:
        backlog.append("補缺失 wiki target：" + "、".join(sorted({row["target"] for row in missing_links})[:8]))
    if not backlog:
        backlog.append("本題提取路徑足夠；下一輪整理時可按新來源補更精準 bridge，而不是為加線硬連。")
    return backlog


def build_retrieval_path(
    query: str,
    seed: ConceptNote,
    grouped: dict[str, list[ConceptNote]],
    record_ids: list[str],
) -> list[dict[str, Any]]:
    path: list[dict[str, Any]] = [{"step": "question", "value": query}]
    seed_type = seed.idea_type if seed.idea_type in IDEA_TYPES else "concept"
    path.append({"step": seed_type, "notes": [seed.title]})
    for idea_type in ("hub", "tension", "taste_signal", "application_scene", "concept"):
        notes = [note.title for note in grouped.get(idea_type, []) if note.title != seed.title]
        if notes:
            path.append({"step": idea_type, "notes": notes[:6]})
    if record_ids:
        path.append({"step": "record", "ids": record_ids[:12]})
    return path


def render_path_text(path: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in path:
        if item["step"] == "question":
            parts.append("問題")
        elif item["step"] == "record":
            parts.append(" / ".join(str(value) for value in item.get("ids", [])[:4]))
        else:
            parts.append(" / ".join(f"[[{note}]]" for note in item.get("notes", [])[:4]))
    return " -> ".join(part for part in parts if part)


def build_answer_summary(seed: ConceptNote, grouped: dict[str, list[ConceptNote]], record_ids: list[str]) -> str:
    parts: list[str] = []
    if seed.one_liner:
        parts.append(seed.one_liner)
    elif seed.graph_role:
        parts.append(seed.graph_role)
    hubs = note_titles(grouped.get("hub", []), exclude=seed.title)
    tensions = note_titles(grouped.get("tension", []), exclude=seed.title)
    taste_signals = note_titles(grouped.get("taste_signal", []), exclude=seed.title)
    applications = note_titles(grouped.get("application_scene", []), exclude=seed.title)
    concepts = note_titles(grouped.get("concept", []), exclude=seed.title)
    if hubs:
        parts.append("先從主題入口看：" + "、".join(hubs[:3]) + "。")
    if tensions:
        parts.append("判斷時保留取捨：" + "、".join(tensions[:3]) + "。")
    if taste_signals:
        parts.append("再用個人品味線索校準：" + "、".join(taste_signals[:3]) + "。")
    if applications:
        parts.append("可轉成行動場景：" + "、".join(applications[:3]) + "。")
    if concepts:
        parts.append("查證前先讀概念：" + "、".join(concepts[:4]) + "。")
    if record_ids:
        parts.append("最後回到 record ID 查證：" + "、".join(record_ids[:5]) + "。")
    return "".join(parts)


def collect_record_ids(notes: list[ConceptNote]) -> list[str]:
    ids: list[str] = []
    for note in notes:
        for value in (*note.source_records, *note.application_records, *note.knowledge_point_records):
            ids.extend(RECORD_ID_RE.findall(value))
        ids.extend(RECORD_ID_RE.findall(note.body))
    return unique_preserve(ids)


def resolve_record_paths(
    record_ids: list[str],
    vault: Path,
    source_registry: dict[str, str],
    kp_registry: dict[str, list[str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record_id in record_ids:
        kind = record_kind(record_id)
        paths: list[str] = []
        if kind == "source":
            source_key = source_base_id(record_id)
            if source_key in source_registry:
                paths.append(source_registry[source_key])
        elif kind == "application":
            source_key = source_base_id(record_id.replace("APP-", "", 1))
            paths.extend(vault_relative(path, vault) for path in sorted((vault / "applications").rglob(f"APP-{source_key}_*.md")))
        elif kind == "knowledge_point":
            paths.extend(kp_registry.get(record_id, []))
        rows.append(
            {
                "record_id": record_id,
                "kind": kind,
                "status": "found" if paths else "unresolved",
                "paths": ";".join(unique_preserve(paths)),
            }
        )
    return rows


def must_read_notes(selected_notes: list[ConceptNote], record_paths: list[dict[str, str]]) -> list[str]:
    paths = [note.rel_path for note in selected_notes]
    for row in record_paths:
        raw_paths = row.get("paths") or ""
        if not raw_paths:
            continue
        paths.extend(path for path in raw_paths.split(";") if path)
    return unique_preserve(paths)


def prepare_note_for_score(note: ConceptNote) -> tuple[str, str, bool]:
    note_text = " ".join([note.title, note.one_liner, note.graph_role, note.graph_question, note.body])
    return normalize_text(note.title), normalize_text(note_text), note.idea_type in IDEA_TYPES


def prepare_query_for_score(query: str) -> tuple[str, list[str]]:
    return normalize_text(query), [normalize_text(term) for term in query_terms(query)]


def score_prepared(note: tuple[str, str, bool], query: tuple[str, list[str]]) -> int:
    normalized_title, normalized_note_text, is_idea = note
    normalized_query, terms = query
    score = 0
    if normalized_title and (normalized_title in normalized_query or normalized_query in normalized_title):
        score += 100
    score += 8 * sum(1 for term in terms if term in normalized_title)
    score += 2 * sum(1 for term in terms if term in normalized_note_text)
    if is_idea:
        score += 5
    return score


def score_concept_note(note: ConceptNote, query: str) -> int:
    """Sole lexical scoring rule for a concept note against one query."""
    return score_prepared(prepare_note_for_score(note), prepare_query_for_score(query))


def best_note_for_query(notes: dict[str, ConceptNote], query: str) -> ConceptNote | None:
    scored: list[tuple[int, str, ConceptNote]] = []
    for note in unique_notes(notes):
        score = score_concept_note(note, query)
        if score > 0:
            scored.append((score, note.title, note))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored[0][2]


# ---------------------------------------------------------------------------
# Older-knowledge candidates (concept compounding).  `related_prior_candidates` scores with its own
# rule below; `best_note_for_query` / `score_concept_note` above stay untouched for think and the
# nebula.  Meaning and calibration basis: docs/Obsidian語義索引與關聯圖落地方案.md, section 2
# "舊知識候選的評分"; candidate rules: Compiler spec section 7.2.
# ---------------------------------------------------------------------------
PRIOR_STOPWORDS = frozenset({
    "研究", "支持", "可能", "部分", "方法", "原文", "可以", "需要", "透過", "以及",
    "the", "and", "of", "to", "a", "in", "is", "for",
})
PRIOR_BM25_K1 = 1.2  # term-frequency saturation of the BM25-lite score
PRIOR_BM25_B = 0.75  # length normalisation of the BM25-lite score
PRIOR_NAME_WEIGHT = 2  # a concept-name token counts this many times in the concept's text
# A query's score is divided by its own idf mass, but clamped to [FLOOR, CAP] "rare tokens" (the idf of
# a token found in one concept).  The floor stops a one-word heading from scoring high alone; the cap stops
# a long claim from being diluted by the many ordinary words it contains.
PRIOR_QUERY_MASS_FLOOR = 3.0
PRIOR_QUERY_MASS_CAP = 8.0
PRIOR_SCORE_DIGITS = 3
# Normalised score (0-1) that marks a candidate ABOVE THRESHOLD.  The threshold is a strength label only: every
# listed candidate must be answered (ingest_rail owns that rule), because the lexical score cannot reliably rank the
# truly related candidates first (three bench rounds: the link count rose and fell with the number of candidates
# that had to be answered).  The threshold never decides whether a candidate is listed or answered.  Basis: the
# offline replay on the four bench-cmp-20261015 seed vaults, table in the semantic-index spec section 2 "舊知識候選的評分".
PRIOR_LINK_MIN_SCORE = 0.25
PRIOR_KPS_PER_CANDIDATE = 2
# Loose knowledge points (kind kp) get their own slots, so they add to the concept list rather than
# displacing concepts a source actually links to (Round 2b calibration).
PRIOR_KP_PER_QUERY = 2
PRIOR_KP_CANDIDATE_TOTAL = 4
PRIOR_REGISTRY_FILES = ("knowledge_points.jsonl", "sources.jsonl")  # the registry files the candidate search reads
_PRIOR_LINK_TARGET_RE = re.compile(r"\]\(\s*https?://[^)\s]*\)")
_PRIOR_URL_RE = re.compile(r"https?://\S+")
_PRIOR_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]+")


def prior_tokens(text: str) -> list[str]:
    """Chinese character bigrams and lower-case English words (two or more characters), without stop words or URLs."""
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = _PRIOR_URL_RE.sub(" ", _PRIOR_LINK_TARGET_RE.sub("]", text))
    tokens: list[str] = []
    for run in _PRIOR_TOKEN_RE.findall(text):
        if run[0].isascii():
            if len(run) >= 2:
                tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return [token for token in tokens if token not in PRIOR_STOPWORDS]


class PriorIndex:
    """BM25-lite statistics over the text of the eligible concepts (name, one-liner, linked prior knowledge points)."""

    def __init__(self, documents: list[tuple[str, list[str]]]):
        self.size = len(documents)
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.lengths: list[int] = []
        for index, (name, texts) in enumerate(documents):
            counts: dict[str, int] = {}
            for token in prior_tokens(name):
                counts[token] = counts.get(token, 0) + PRIOR_NAME_WEIGHT
            for text in texts:
                for token in prior_tokens(text):
                    counts[token] = counts.get(token, 0) + 1
            self.lengths.append(sum(counts.values()))
            for token, count in counts.items():
                self.postings.setdefault(token, []).append((index, count))
        self.average_length = (sum(self.lengths) / self.size) if self.size else 0.0

    def idf(self, token: str) -> float:
        frequency = max(len(self.postings.get(token, ())), 1)  # an unseen token weighs as much as the rarest seen one
        return math.log(1 + (self.size - frequency + 0.5) / (frequency + 0.5))

    def scores(self, query: str) -> list[float]:
        """Normalised 0-1 relevance of every concept to one query (see PRIOR_QUERY_MASS_FLOOR/CAP)."""
        tokens = set(prior_tokens(query))
        result = [0.0] * self.size
        if not tokens or not self.size:
            return result
        scale = PRIOR_BM25_K1 + 1
        rare = math.log(1 + (self.size - 0.5) / 1.5) * scale  # the weight of one token found in a single concept
        mass = sum(self.idf(token) for token in tokens) * scale
        denominator = min(max(mass, PRIOR_QUERY_MASS_FLOOR * rare), PRIOR_QUERY_MASS_CAP * rare)
        for token in tokens:
            weight = self.idf(token) * scale
            for index, count in self.postings.get(token, ()):
                length_part = 1 - PRIOR_BM25_B + PRIOR_BM25_B * self.lengths[index] / self.average_length
                result[index] += weight * count / (count + PRIOR_BM25_K1 * length_part)
        return [min(1.0, value / denominator) for value in result]


def prior_registry_fingerprint(vault: Path) -> str:
    """sha256 over the registry files the candidate search reads; stored with a candidate list as audit evidence."""
    digest = hashlib.sha256()
    for name in PRIOR_REGISTRY_FILES:
        try:
            data = (vault / "_registry" / name).read_bytes()
        except OSError:
            data = b""
        digest.update(name.encode("utf-8") + b"\0" + hashlib.sha256(data).hexdigest().encode("ascii") + b"\n")
    return digest.hexdigest()


def prior_source_titles(vault: Path, source_ids: set[str]) -> dict[str, str]:
    """Title of each of these sources, read from its source note (path in sources.jsonl); an unreadable one is left out."""
    base = vault.resolve()
    titles: dict[str, str] = {}
    for row in read_jsonl(vault / "_registry" / "sources.jsonl"):
        source_id, rel_path = str(row.get("source_id") or "").strip(), str(row.get("source_note_path") or "").strip()
        if source_id not in source_ids or source_id in titles or not rel_path:
            continue
        path = (vault / rel_path).resolve()
        if not path.is_relative_to(base):
            continue
        try:
            fields, _ = read_markdown(path)
        except (OSError, UnicodeDecodeError):
            continue
        title = str(fields.get("title") or "").strip()
        if title:
            titles[source_id] = title
    return titles


def related_prior_candidates(
    vault: Path,
    queries: list[str],
    *,
    exclude_source_id: str,
    per_query: int = 3,
    total: int = 8,
    kp_per_query: int = PRIOR_KP_PER_QUERY,
    kp_total: int = PRIOR_KP_CANDIDATE_TOTAL,
    min_score: float = PRIOR_LINK_MIN_SCORE,
    id_prefix: str = "RC",
    exclude_paths: frozenset[str] | set[str] = frozenset(),
    exclude_kp_uids: frozenset[str] | set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Top-K older candidates for these queries: ``kind`` concept (with up to two linked knowledge points) or kp.

    Read-only and deterministic for one vault state.  A ``concept`` candidate is an active concept that still
    holds at least one linked knowledge point from a source other than ``exclude_source_id``.  A ``kp``
    candidate is one linked knowledge point of another source that no active concept note records (empty
    ``concept_name`` and ``concept_path``; ``prior_kps`` is that point alone), so it is never offered twice.
    The score is the normalised BM25-lite relevance of one query to the document: the concept's name, one-liner
    and linked prior claims, or the point's claim and its source title; a candidate keeps its best query score.
    ``exclude_paths`` (concept paths) and ``exclude_kp_uids`` leave candidates out of the pool, and ``id_prefix``
    names the candidate ids, so a second list can extend a first without renumbering it.
    """
    notes = unique_notes(load_concept_notes(vault))
    linked_by_kp_id = linked_kps_by_kp_id(vault)
    pool: list[tuple[str, str, str, list[dict[str, str]]]] = []  # (kind, name, key, prior_kps); key = concept path or kp_uid
    documents: list[tuple[str, list[str]]] = []
    absorbed: set[str] = set()  # every linked kp_uid some active concept note records, whatever its source
    for note in sorted(notes, key=lambda item: item.rel_path):
        recorded = prior_kps_for_note(note, linked_by_kp_id, None, limit=None)
        absorbed.update(row["kp_uid"] for row in recorded)
        all_prior = [row for row in recorded if row["source_id"] != exclude_source_id]
        if all_prior:
            pool.append(("concept", concept_name_of(note), note.rel_path, all_prior[:PRIOR_KPS_PER_CANDIDATE]))
            documents.append((concept_name_of(note), [note.one_liner, *[row["claim"] for row in all_prior]]))
    loose: dict[str, dict[str, str]] = {}
    for rows in linked_by_kp_id.values():
        for row in rows:
            kp_uid, source_id, claim = str(row["kp_uid"]), str(row.get("source_id") or ""), str(row.get("knowledge_point") or "")
            if kp_uid not in absorbed and source_id != exclude_source_id and claim.strip() and kp_uid not in loose:
                loose[kp_uid] = {"kp_uid": kp_uid, "claim": claim, "source_id": source_id}
    titles = prior_source_titles(vault, {row["source_id"] for row in loose.values()})
    for kp_uid in sorted(loose):
        pool.append(("kp", "", kp_uid, [loose[kp_uid]]))
        documents.append(("", [loose[kp_uid]["claim"], titles.get(loose[kp_uid]["source_id"], "")]))
    if not pool:
        return []
    index = PriorIndex(documents)
    best: dict[tuple[str, str], tuple[float, str, list[dict[str, str]]]] = {}
    seen_queries: set[str] = set()
    for query in queries:
        query = str(query or "").strip()
        if not normalize_text(query) or query in seen_queries:
            continue
        seen_queries.add(query)
        scored = []
        for (kind, name, key, prior_kps), score in zip(pool, index.scores(query)):
            score = round(score, PRIOR_SCORE_DIGITS)
            if score > 0 and key not in (exclude_paths if kind == "concept" else exclude_kp_uids):
                scored.append((-score, kind != "concept", name, key, kind, prior_kps))
        scored.sort(key=lambda item: item[:4])
        # Concepts and loose points never compete for the same per-query slots.
        picked = [item for item in scored if item[4] == "concept"][:per_query] + [item for item in scored if item[4] == "kp"][:kp_per_query]
        for negative, _, name, key, kind, prior_kps in picked:
            if (kind, key) not in best or -negative > best[(kind, key)][0]:
                best[(kind, key)] = (-negative, name, prior_kps)
    rank = lambda item: (-item[1][0], item[0][0] != "concept", item[1][1], item[0][1])
    ordered = sorted(
        sorted((item for item in best.items() if item[0][0] == "concept"), key=rank)[:total]
        + sorted((item for item in best.items() if item[0][0] == "kp"), key=rank)[:kp_total],
        key=rank,
    )
    return [
        {
            "candidate_id": f"{id_prefix}-{position:02d}",
            "kind": kind,
            "concept_name": name,
            "concept_path": key if kind == "concept" else "",
            "score": score,
            "above_threshold": score >= min_score,
            "prior_kps": prior_kps,
        }
        for position, ((kind, key), (score, name, prior_kps)) in enumerate(ordered, start=1)
    ]


def concept_name_of(note: ConceptNote) -> str:
    return str(note.fields.get("concept") or note.title).strip()


def linked_kps_by_kp_id(vault: Path) -> dict[str, list[dict[str, Any]]]:
    """The registry's linked knowledge-point rows that carry a kp_uid, grouped by kp_id."""
    linked_by_kp_id: dict[str, list[dict[str, Any]]] = {}
    for row in read_jsonl(vault / "_registry" / "knowledge_points.jsonl"):
        kp_id = str(row.get("kp_id") or "").strip()
        if kp_id and str(row.get("status") or "").strip() == "linked" and str(row.get("kp_uid") or "").strip():
            linked_by_kp_id.setdefault(kp_id, []).append(row)
    return linked_by_kp_id


def concept_notes_with_kp_uids(vault: Path) -> list[tuple[ConceptNote, list[str]]]:
    """Every active concept note (by path) with the linked kp_uids it records: the one kp_uid-to-concept-page map.

    A note records the knowledge points named by its frontmatter ``knowledge_point_records`` (``prior_kps_for_note``),
    generated and handwritten pages alike; the concept-page projection of the vault adapter reads this same map."""
    linked_by_kp_id = linked_kps_by_kp_id(vault)
    return [(note, [row["kp_uid"] for row in prior_kps_for_note(note, linked_by_kp_id, None, limit=None)])
            for note in sorted(unique_notes(load_concept_notes(vault)), key=lambda item: item.rel_path)]


def existing_concept_names(vault: Path) -> set[str]:
    """Names of every active concept note, as written in the note's `concept` field."""
    return {concept_name_of(note) for note in unique_notes(load_concept_notes(vault))}


def prior_kps_for_note(
    note: ConceptNote,
    linked_by_kp_id: dict[str, list[dict[str, Any]]],
    exclude_source_id: str | None,
    *,
    limit: int | None = PRIOR_KPS_PER_CANDIDATE,
) -> list[dict[str, str]]:
    """The linked, other-source knowledge points a concept note records (first ``limit``; all when None).

    ``exclude_source_id`` None lists every source."""
    source_bases = {source_base_id(record) for record in note.source_records}
    picked: list[dict[str, str]] = []
    for kp_id in note.knowledge_point_records:
        rows = linked_by_kp_id.get(kp_id, [])
        matching = [row for row in rows if str(row.get("source_id") or "") in source_bases]
        for row in matching or rows:
            source_id = str(row.get("source_id") or "")
            kp_uid = str(row.get("kp_uid") or "")
            if (exclude_source_id is not None and source_id == exclude_source_id) or any(item["kp_uid"] == kp_uid for item in picked):
                continue
            picked.append({"kp_uid": kp_uid, "claim": str(row.get("knowledge_point") or ""), "source_id": source_id})
        if limit is not None and len(picked) >= limit:
            break
    return picked if limit is None else picked[:limit]


def seed_for_query(query: str) -> str:
    normalized = normalize_text(query)
    for item in STANDARD_QUESTIONS:
        if normalize_text(item.question) == normalized:
            return item.seed_title
    return ""


def read_markdown(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8-sig")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    fields = parse_frontmatter(match.group(1))
    return fields, text[match.end() :]


def parse_frontmatter(text: str) -> dict[str, Any]:
    """A frontmatter block (without its --- fences), read by the one shared vault reader."""
    return frontmatter_fields_from_text("---\n" + text + "\n---\n")


def parse_links_by_section(body: str) -> dict[str, list[str]]:
    sections = section_texts(body)
    return {heading: unique_preserve(NOTE_LINK_RE.findall(text)) for heading, text in sections.items()}


def section_texts(body: str) -> dict[str, str]:
    matches = list(HEADING_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[match.group(1).strip()] = body[start:end].strip()
    return sections


def section_first_sentence(body: str, heading: str) -> str:
    text = section_texts(body).get(heading, "")
    for line in text.splitlines():
        stripped = line.strip(" -\t")
        if stripped:
            return stripped
    return ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def note_summary(note: ConceptNote) -> dict[str, str]:
    return {
        "title": note.title,
        "path": note.rel_path,
        "type": note.idea_type if note.idea_type in IDEA_TYPES else "concept",
        "one_liner": note.one_liner,
        "graph_role": note.graph_role,
    }


def note_titles(notes: list[ConceptNote], *, exclude: str = "") -> list[str]:
    return [note.title for note in notes if note.title != exclude]


def all_links(note: ConceptNote) -> list[str]:
    links: list[str] = []
    for section_links in note.links_by_section.values():
        links.extend(section_links)
    return unique_preserve(links)


def unique_notes(notes: dict[str, ConceptNote]) -> list[ConceptNote]:
    seen: set[Path] = set()
    unique: list[ConceptNote] = []
    for note in notes.values():
        if note.path in seen:
            continue
        seen.add(note.path)
        unique.append(note)
    return unique


def add_note(notes: list[ConceptNote], note: ConceptNote) -> None:
    if all(existing.path != note.path for existing in notes):
        notes.append(note)


def list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def query_terms(query: str) -> list[str]:
    chunks = re.findall(r"[A-Za-z0-9_+-]+|[\u4e00-\u9fff]+", query.lower())
    terms: set[str] = set()
    for chunk in chunks:
        if len(chunk) <= 1:
            continue
        terms.add(chunk)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            for size in (2, 3, 4):
                if len(chunk) > size:
                    for index in range(0, len(chunk) - size + 1):
                        terms.add(chunk[index : index + size])
    return sorted(terms, key=lambda item: (-len(item), item))


def normalize_text(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text.lower(), flags=re.UNICODE)


def record_kind(record_id: str) -> str:
    if record_id.startswith("KS-") or record_id.startswith("SRC-"):
        return "source"
    if record_id.startswith("APP-"):
        return "application"
    if record_id.startswith("KP-"):
        return "knowledge_point"
    return "record"


def source_base_id(record_id: str) -> str:
    match = re.match(r"(KS-[A-Z]+-\d+)", record_id)
    if match:
        return match.group(1)
    match = re.match(r"(KS-\d+)", record_id)
    return match.group(1) if match else record_id.split("_", 1)[0]


def unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def receipt_filename(receipt: dict[str, Any]) -> str:
    question_id = str(receipt.get("question_id") or "").strip()
    if question_id:
        return f"{question_id}.json"
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", normalize_text(str(receipt.get("question") or "")))[:48].strip("_")
    return f"receipt_{stem or 'question'}.json"


def write_receipt(output_dir: Path, filename: str, payload: dict[str, Any]) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path.as_posix()


def resolve_inside(root: Path, path: Path) -> Path:
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    if resolved != root and root not in resolved.parents:
        raise SystemExit(f"path outside workspace: {path}")
    return resolved


def vault_relative(path: Path | str, vault: Path) -> str:
    value = Path(path)
    if not value.is_absolute():
        return value.as_posix()
    try:
        return value.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        return value.as_posix()


def truth_boundary() -> str:
    return (
        "This receipt is derived from Obsidian vault notes and registries only. "
        "Record IDs remain plain text for traceability and are not graph wikilinks."
    )


def print_result(result: dict[str, Any], json_mode: bool) -> None:
    if json_mode:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(f"status: {result['status']}")
    if result.get("schema_version") == ACCEPTANCE_SCHEMA_VERSION:
        print(f"ready: {result['ready_count']} needs_bridge: {result['needs_bridge_count']} insufficient: {result['insufficient_evidence_count']}")
        for receipt in result["receipts"]:
            print(f"- {receipt['question_id']} {receipt['status']}: {receipt['path_text']}")
        return
    print(f"question: {result.get('question', '')}")
    print(f"path: {result.get('path_text', '')}")
    for path in result.get("must_read_notes", []):
        print(f"read: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
