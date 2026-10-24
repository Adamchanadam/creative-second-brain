"""Knowledge-direction signals: where evidence builds up, where sources disagree, what grew lately.

A pure read of the registry and the concept notes: no model call, no write, and the same vault and the
same ``today`` give the same bytes.  The signal definitions and every threshold are specified once in
the semantic-index specification (section 2); the named constants below are the only copy in code.
Home page, ``$brain-think`` and ``research-program next`` consume this module and define nothing of
their own.  The concept scope is the idea retrieval's one kp_uid-to-concept-page map
(``concept_notes_with_kp_uids``): every active concept page that records a linked knowledge point.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

try:
    from .obsidian_idea_retrieval import concept_name_of, concept_notes_with_kp_uids
    from .obsidian_vault_adapter import (
        AdapterStop, CROSS_SOURCE_SECTIONS, concept_projection_facts, read_knowledge_point_registry,
        read_source_registry_rows,
    )
    from .workspace_paths import WorkspacePathError, vault_path
except ImportError:
    from obsidian_idea_retrieval import concept_name_of, concept_notes_with_kp_uids  # type: ignore
    from obsidian_vault_adapter import (  # type: ignore
        AdapterStop, CROSS_SOURCE_SECTIONS, concept_projection_facts, read_knowledge_point_registry,
        read_source_registry_rows,
    )
    from workspace_paths import WorkspacePathError, vault_path  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "knowledge_direction_v1"

# Signal thresholds: the sole definition (specification: semantic-index section 2).
EVIDENCE_MIN_SOURCES = 3        # evidence_building: at least this many evidence sources (evidence_source_count) ...
EVIDENCE_MIN_SUPPORTS = 1       # ... and at least this many supports relations
TENSION_MIN_AGAINST = 1         # tension_hotspot: contradicts + limits at least this many ...
TENSION_MIN_FOR = 1             # ... and supports + extends at least this many
RECENT_DAYS = 30                # recent_growth: a cross-source link updated within this many days
NEW_CONCEPT_DAYS = 30           # isolated_new: created within this many days
DEFAULT_TOP_N = 5
AGAINST_RELATIONS = ("contradicts", "limits")
FOR_RELATIONS = ("supports", "extends")
# $brain-think: relation types that make an established tension, and how many one packet carries.
THINK_TENSION_RELATIONS = ("contradicts", "limits", "tradeoff")
MAX_TENSIONS_IN_PACKET = 6

RELATION_ORDER = tuple(relation for relation, *_ in CROSS_SOURCE_SECTIONS)
RELATION_LABELS = {relation: heading for relation, heading, *_ in CROSS_SOURCE_SECTIONS}
SIGNALS = ("evidence_building", "tension_hotspot", "recent_growth", "isolated_new")
SOURCE_ID = re.compile(r"KS-[A-Z]+-\d+")


class DirectionError(ValueError):
    """The registry could not be read as one consistent whole."""


def thresholds() -> dict[str, object]:
    return {
        "evidence_building": {"min_sources": EVIDENCE_MIN_SOURCES, "min_supports": EVIDENCE_MIN_SUPPORTS},
        "tension_hotspot": {"against": list(AGAINST_RELATIONS), "min_against": TENSION_MIN_AGAINST,
                            "for": list(FOR_RELATIONS), "min_for": TENSION_MIN_FOR},
        "recent_growth": {"days": RECENT_DAYS},
        "isolated_new": {"sources": 1, "days": NEW_CONCEPT_DAYS},
        "think_tension_relations": list(THINK_TENSION_RELATIONS),
        "max_tensions_in_packet": MAX_TENSIONS_IN_PACKET,
    }


def parse_day(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def load_facts(vault: Path) -> dict[str, object]:
    """Valid concepts, their linked knowledge points and the linked cross-source relations, from one read."""
    try:
        return _read_facts(vault)
    except AdapterStop as exc:
        raise DirectionError(exc.message) from exc
    except (OSError, UnicodeError) as exc:
        raise DirectionError(f"vault unreadable: {exc}") from exc


def _read_facts(vault: Path) -> dict[str, object]:
    kp_rows = read_knowledge_point_registry(vault)
    facts = concept_projection_facts(vault)
    links = list((facts or {}).get("links", []))
    concepts: list[dict[str, object]] = []
    uid_concepts: dict[str, list[int]] = {}
    # Every active concept page (new format, generated legacy, handwritten) with its linked kp_uids: the sole map.
    pages = sorted(((concept_name_of(note), note, found) for note, found in concept_notes_with_kp_uids(vault)),
                   key=lambda item: (item[0], str(item[1].path)))
    for name, note, found in pages:
        uids = sorted({uid for uid in found if uid in kp_rows and kp_rows[uid].get("status", "linked") == "linked"})
        if not uids:
            continue
        sources = {str(kp_rows[uid].get("source_id") or uid.split(":", 1)[0]) for uid in uids}
        days = [day for day in (parse_day(kp_rows[uid].get("updated")) for uid in uids) if day]
        created = min(days) if days else parse_day(note.fields.get("updated"))
        index = len(concepts)
        concepts.append({"name": name, "path": f"concepts/{note.path.name}", "kp_uids": uids,
                         "sources": sources, "created": created})
        for uid in uids:
            uid_concepts.setdefault(uid, []).append(index)
    return {"kp": kp_rows, "links": links, "concepts": concepts, "uid_concepts": uid_concepts}


def _phrase(relations: dict[str, int], types: tuple[str, ...]) -> str:
    return "、".join(f"{RELATION_LABELS[kind]} {relations[kind]} 條" for kind in types if relations[kind])


def _explain(signal: str, row: dict[str, object]) -> str:
    relations = row["relations"]
    if signal == "evidence_building":
        return f"{row['evidence_source_count']} 個來源支持或延伸這個概念，其中 {relations['supports']} 條跨來源關係互相支持"
    if signal == "tension_hotspot":
        return (f"同時有{_phrase(relations, FOR_RELATIONS)}與{_phrase(relations, AGAINST_RELATIONS)}，"
                "值得研究各自在甚麼條件下成立")
    if signal == "recent_growth":
        return f"最近 {RECENT_DAYS} 天新增 {row['recent_link_count']} 條跨來源連結，最近一條在 {row['last_link_at']}"
    return f"{row['created_at']} 入庫，目前只有 1 個來源，還沒有和其他來源連結"


def compute_direction(vault: Path, *, top_n: int = DEFAULT_TOP_N, today: date | None = None) -> dict[str, object]:
    """The per-concept table and the top ``top_n`` concepts of each signal."""
    if not isinstance(top_n, int) or isinstance(top_n, bool) or top_n < 1:
        raise ValueError("top_n must be a positive integer")
    today = today or date.today()
    facts = load_facts(vault)
    concepts = facts["concepts"]
    rows = [{"concept_name": item["name"], "concept_path": item["path"], "source_count": len(item["sources"]),
             "evidence_source_count": len(item["sources"]), "relations": {kind: 0 for kind in RELATION_ORDER}, "last_link_at": "",
             "created_at": item["created"].isoformat() if item["created"] else "", "recent_link_count": 0}
            for item in concepts]
    evidence_sources = [set(item["sources"]) for item in concepts]
    kp_rows = facts["kp"]
    for link in facts["links"]:
        ends = (str(link["source_kp_uid"]), str(link["target_kp_uid"]))
        touched = set(facts["uid_concepts"].get(ends[0], [])) | set(facts["uid_concepts"].get(ends[1], []))
        day = parse_day(link.get("updated"))
        for index in touched:
            row = rows[index]
            row["relations"][str(link["relation"])] += 1
            if str(link["relation"]) in FOR_RELATIONS:
                # the other side's source is evidence too: supports/extends only, never contradicts, limits, tradeoff, applies_to
                evidence_sources[index].update(str(kp_rows[uid].get("source_id") or uid.split(":", 1)[0])
                                               for uid in ends if uid not in concepts[index]["kp_uids"])
            if day:
                row["last_link_at"] = max(str(row["last_link_at"]), day.isoformat())
                if 0 <= (today - day).days <= RECENT_DAYS:
                    row["recent_link_count"] += 1
    for row, found in zip(rows, evidence_sources):
        row["evidence_source_count"] = len(found)
    scored: dict[str, list[tuple[int, dict[str, object]]]] = {signal: [] for signal in SIGNALS}
    for row in rows:
        relations = row["relations"]
        against = sum(relations[kind] for kind in AGAINST_RELATIONS)
        supporting = sum(relations[kind] for kind in FOR_RELATIONS)
        if row["evidence_source_count"] >= EVIDENCE_MIN_SOURCES and relations["supports"] >= EVIDENCE_MIN_SUPPORTS:
            scored["evidence_building"].append((row["evidence_source_count"] + relations["supports"], row))
        if against >= TENSION_MIN_AGAINST and supporting >= TENSION_MIN_FOR:
            scored["tension_hotspot"].append((against + supporting, row))
        if row["recent_link_count"] >= 1:
            scored["recent_growth"].append((row["recent_link_count"], row))
        created = parse_day(row["created_at"])
        if (row["source_count"] == 1 and not any(relations.values()) and created
                and 0 <= (today - created).days <= NEW_CONCEPT_DAYS):
            scored["isolated_new"].append((NEW_CONCEPT_DAYS - (today - created).days, row))
    signals = {}
    for signal in SIGNALS:
        ranked = sorted(scored[signal], key=lambda item: (-item[0], str(item[1]["concept_name"])))[:top_n]
        signals[signal] = [{"concept_name": row["concept_name"], "concept_path": row["concept_path"], "score": score,
                            "source_count": row["source_count"], "evidence_source_count": row["evidence_source_count"],
                            "relations": dict(row["relations"]),
                            "last_link_at": row["last_link_at"], "created_at": row["created_at"],
                            "text": _explain(signal, row)} for score, row in ranked]
    return {
        "schema_version": SCHEMA, "today": today.isoformat(), "top_n": top_n, "thresholds": thresholds(),
        "concept_count": len(rows), "cross_source_relation_count": len(facts["links"]),
        "signals": signals,
        "concepts": [{key: row[key] for key in ("concept_name", "concept_path", "source_count", "evidence_source_count", "relations",
                                                    "last_link_at", "created_at")}
                     for row in rows],
    }


def source_note_paths(vault: Path) -> dict[str, str]:
    try:
        rows = read_source_registry_rows(vault)
    except (AdapterStop, OSError) as exc:
        raise DirectionError(str(getattr(exc, "message", exc))) from exc
    return {str(item.get("source_id")): str(item.get("source_note_path") or "")
            for _, item in rows if item.get("source_id") and item.get("status", "linked") == "linked"}


def source_ids_in_paths(paths: list[str]) -> set[str]:
    return {found for path in paths for found in SOURCE_ID.findall(str(path))}


def established_tensions(vault: Path, source_ids: set[str], *, limit: int = MAX_TENSIONS_IN_PACKET) -> dict[str, object]:
    """Linked cross-source contradicts/limits/tradeoff relations around the concepts of the selected sources.

    Related knowledge points are those of the selected sources plus every knowledge point of the concepts they
    belong to.  Relations touching a selected source come first; the rest follow by relation_id.
    """
    selected = {str(item) for item in source_ids if str(item).strip()}
    if not selected:
        return {"tensions": [], "omitted": 0}
    facts = load_facts(vault)
    kp_rows, concepts = facts["kp"], facts["concepts"]
    direct = {uid for uid, row in kp_rows.items() if row.get("status", "linked") == "linked"
              and str(row.get("source_id") or uid.split(":", 1)[0]) in selected}
    related = set(direct)
    for index in {index for uid in direct for index in facts["uid_concepts"].get(uid, [])}:
        related.update(concepts[index]["kp_uids"])
    notes = source_note_paths(vault)
    found = []
    for link in facts["links"]:
        if link["relation"] not in THINK_TENSION_RELATIONS:
            continue
        source_uid, target_uid = str(link["source_kp_uid"]), str(link["target_kp_uid"])
        if source_uid not in related and target_uid not in related:
            continue
        newer = str(kp_rows[source_uid].get("source_id") or link.get("source_id") or source_uid.split(":", 1)[0])
        older = str(link.get("target_source_id") or kp_rows[target_uid].get("source_id") or target_uid.split(":", 1)[0])
        names = sorted({concepts[index]["name"] for uid in (source_uid, target_uid) for index in facts["uid_concepts"].get(uid, [])})
        found.append({
            "relation_id": str(link["relation_id"]), "relation_type": str(link["relation"]),
            "insight": str(link.get("insight") or ""),
            "source_kp_uid": source_uid, "target_kp_uid": target_uid,
            "source_id": newer, "target_source_id": older,
            "source_note_path": notes.get(newer, ""), "target_source_note_path": notes.get(older, ""),
            "concept_names": names,
        })
    found.sort(key=lambda item: (not ({item["source_id"], item["target_source_id"]} & selected), item["relation_id"]))
    return {"tensions": found[:limit], "omitted": max(0, len(found) - limit)}


def resolve_vault(root: Path, vault: Path | None) -> Path:
    try:
        target = vault.resolve() if vault else vault_path(root, os.environ.get("CREATIVE_SECOND_BRAIN_VAULT"))
    except WorkspacePathError as exc:
        raise DirectionError(str(exc)) from exc
    if not target.is_dir():
        raise DirectionError("vault folder not found")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only knowledge-direction signals from the registry.")
    parser.add_argument("--root", type=Path, default=ROOT, help="workspace root; the vault is its configured vault folder")
    parser.add_argument("--vault", type=Path, help="vault folder, when it is not the workspace's configured vault")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="concepts listed per signal")
    parser.add_argument("--today", help="YYYY-MM-DD; default is today's date")
    parser.add_argument("--concepts", action="store_true", help="also print the per-concept table")
    parser.add_argument("--json", action="store_true", help="print JSON (the default is a short reading)")
    args = parser.parse_args(argv)
    try:
        today = date.fromisoformat(args.today) if args.today else None
        result = compute_direction(resolve_vault(args.root, args.vault), top_n=args.top, today=today)
    except ValueError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2
    if not args.concepts:
        result = {key: value for key, value in result.items() if key != "concepts"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    for signal, items in result["signals"].items():
        print(f"{signal}: {len(items)}")
        for item in items:
            print(f"  {item['concept_name']}：{item['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
