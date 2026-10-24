"""Typed semantic input for the compiler; identities and workflow state are not AI fields.

Normative owner: docs/Compiler Worker與Knowledge Package設計及驗收規格.md §7.2.
The exported JSON Schema and its validator share the same definition. This module
does not call a model, generate images, or write the Vault.
"""
from __future__ import annotations
import os

import copy
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

try:
    from tools.case_evidence_contract import ASSET_MODE_CASE, ASSET_MODE_FORMAL, ASSET_MODE_KNOWLEDGE, package_manifest
    from tools.infographic_compiler import (
        INFOGRAPHIC_GRAPH_TYPE_CONTRACTS,
        materialize_infographic_page_bindings,
        reader_infographic_title,
        validate_primary_work_metadata,
    )
    from tools.obsidian_vault_adapter import (
        AdapterStop,
        KnowledgePoint,
        KnowledgeRelation,
        VALID_SOURCE_TYPES,
        VALID_KNOWLEDGE_USE,
        VALID_COMPOUNDING_STATUSES,
        VALID_COMPOUNDING_TYPES,
        SOURCE_EVIDENCE_FRAGMENT_SEPARATOR,
        canonical_body_contains_anchor,
        validate_source_anchored_knowledge,
    )
    from tools.image_source_contract import canonical_analysis_sha256, validate_visual_analysis
    from tools.youtube_transcript_capture import (
        TRANSCRIPT_ARCHIVE_MODE,
        caption_reading_chunks,
        is_youtube_caption_transcript,
        youtube_time_link,
    )
except ModuleNotFoundError:
    from case_evidence_contract import ASSET_MODE_CASE, ASSET_MODE_FORMAL, ASSET_MODE_KNOWLEDGE, package_manifest
    from infographic_compiler import (  # type: ignore
        INFOGRAPHIC_GRAPH_TYPE_CONTRACTS,
        materialize_infographic_page_bindings,
        reader_infographic_title,
        validate_primary_work_metadata,
    )
    from obsidian_vault_adapter import (  # type: ignore
        AdapterStop,
        KnowledgePoint,
        KnowledgeRelation,
        VALID_SOURCE_TYPES,
        VALID_KNOWLEDGE_USE,
        VALID_COMPOUNDING_STATUSES,
        VALID_COMPOUNDING_TYPES,
        SOURCE_EVIDENCE_FRAGMENT_SEPARATOR,
        canonical_body_contains_anchor,
        validate_source_anchored_knowledge,
    )
    from image_source_contract import canonical_analysis_sha256, validate_visual_analysis
    from youtube_transcript_capture import (  # type: ignore
        TRANSCRIPT_ARCHIVE_MODE,
        caption_reading_chunks,
        is_youtube_caption_transcript,
        youtube_time_link,
    )

VERSION = "ingest_semantic_plan_v1"
COMPACT_VERSION = "ingest_semantic_plan_compact_v1"
SOURCE_CATALOG_VERSION = "source_block_catalog_v2"
# v3 differs from v2 only for a YouTube caption archive: its blocks are the
# de-duplicated reading chunks (caption_reading_chunks) and its evidence is a
# short program-built quotation with a time link.  Everything else is v2.
SOURCE_CATALOG_VERSION_V3 = "source_block_catalog_v3"
# v4 keeps every v3 text/URL block byte-for-byte and adds deterministic
# Markdown-heading metadata to ordinary canonical-source spans.
SOURCE_CATALOG_VERSION_V4 = "source_block_catalog_v4"
SOURCE_CATALOG_VERSIONS = (SOURCE_CATALOG_VERSION, SOURCE_CATALOG_VERSION_V3, SOURCE_CATALOG_VERSION_V4)
CURRENT_SOURCE_CATALOG_VERSION = SOURCE_CATALOG_VERSION_V4
CAPTION_EVIDENCE_MAX_BLOCKS = 4
CAPTION_EVIDENCE_MAX_CHARS = 700
# The only relation vocabulary: same-source relations and cross-source links share it.
# Meaning and limits: docs/Obsidian語義索引與關聯圖落地方案.md section 2.
RELATION_TYPES = ("supports", "contradicts", "limits", "extends", "tradeoff", "applies_to")
PRIOR_DECISIONS = ("linked", "no_meaningful_link")
# A supports or extends link also says whether the new knowledge point belongs to the older concept (same_concept)
# or stays a separate one (distinct).  Rules and error codes: Compiler spec section 7.2.
CONCEPT_RELATIONS = ("same_concept", "distinct")
CONCEPT_RELATION_LINK_TYPES = ("supports", "extends")
# A source title becomes a named-structure candidate only when this many
# distinct submitted knowledge points cite its following anchors.
STRUCTURE_CANDIDATE_MIN_MEMBERS = 3
STRUCTURE_DECISION_MIN_REASON_CHARS = 3
STRUCTURE_DECISIONS = ("not_a_named_structure",)


def text_schema(*, values=None, empty=False):
    result = {"type": "string", "minLength": 0 if empty else 1}
    if values is not None:
        result["enum"] = sorted(values)
    return result


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


TEXT = text_schema()
TEXTS = array(TEXT)
CONFIDENCE = text_schema(values=("高", "中", "低"))
LAYERS = ("原文主張", "原文方法", "原文數據", "原文限制", "原文治理", "AI 轉化")
VALID_ADOPTION_STATUSES = {"收藏", "再訪", "試行", "深讀", "暫存", "拒絕"}
RETRIEVAL_ALIAS_MAX_ITEMS = 8
RETRIEVAL_ALIAS_MAX_CHARS = 80


def visual_schemas():
    """Project graph shapes from their existing executable owner, never copy them."""
    variants = []
    for contract in INFOGRAPHIC_GRAPH_TYPE_CONTRACTS.values():
        if contract.get("formal_generation_allowed") is False:
            continue
        fields = {"kind": text_schema(values=[contract["kind"]])}
        fields.update({key: TEXT for key in contract.get("required_text_fields", ())})
        for name, collection in contract.get("collections", {}).items():
            fields[name] = array(obj({key: TEXT for key in collection["required_fields"]}), collection["min_items"])
        variants.append(obj(fields))
    return {"oneOf": variants}


def plan_schema():
    citation = obj({"title": TEXT, "authors": TEXT, "publication": TEXT,
                    "original_date": TEXT, "original_source_url": TEXT,
                    "original_source_url_status": text_schema(values=["verified"])},
                   ["title", "authors", "publication", "original_source_url", "original_source_url_status"])
    metadata = obj({"source_label": TEXT, "source_type": text_schema(values=VALID_SOURCE_TYPES),
                    "author": TEXT, "original_date": TEXT, "content_owner": TEXT,
                    "primary_work_status": text_schema(values=["primary", "none"]),
                    "primary_work_title": TEXT, "primary_work_evidence": TEXT,
                    "citation_evidence": citation, "retrieval_aliases": array(text_schema(empty=True))}, ["source_label", "source_type", "primary_work_status"])
    claim = obj({"id": TEXT, "claim": TEXT, "evidence_anchor": TEXT, "confidence": CONFIDENCE})
    unit = obj({"id": TEXT, "text": TEXT, "source_evidence": TEXT,
                "source_block_ids": TEXTS,
                "unit_types": array(TEXT, 1), "value_axes": TEXTS,
                "dispositions": array(text_schema(values=["source_claim", "knowledge_point", "summary", "infographic", "application", "relation", "omitted"]), 1),
                "infographic_text": TEXTS, "omission_reason": TEXT},
               ["id", "text", "source_evidence", "unit_types", "dispositions"])
    knowledge = obj({"kp_id": TEXT, "title": TEXT, "claim": TEXT,
                     "layer": text_schema(values=LAYERS), "confidence": CONFIDENCE, "evidence_anchor": TEXT})
    knowledge["properties"]["reusable_concept"] = reusable_concept_schema()
    relation = obj({"source_kp": TEXT, "target_kp": TEXT, "relation": TEXT, "evidence_anchor": TEXT, "confidence": CONFIDENCE})
    application = obj({"knowledge_use": text_schema(values=VALID_KNOWLEDGE_USE),
        "adoption_status": text_schema(values=VALID_ADOPTION_STATUSES),
        "adoption_reason": TEXT, "personalization_links": TEXTS,
        "context_anchors": array(obj({"path": TEXT, "quote": TEXT})),
        "compounding_insight_status": text_schema(values=VALID_COMPOUNDING_STATUSES),
        "compounding_insight_types": array(text_schema(values=VALID_COMPOUNDING_TYPES)),
        "compounding_next_action": TEXT, "taste_network_status": text_schema(values=["是", "否"]),
        "compounding_judgments": obj({kind: obj({"decision": text_schema(values=["成立", "不成立", "需要更多脈絡"]), "reason": TEXT}) for kind in VALID_COMPOUNDING_TYPES}),
        "taste_link_status": text_schema(values=["linked", "taste_needs_context", "exploration_needs_context", "not_applicable"]),
        "taste_reason": TEXT})
    application["properties"].update({"analysis_markdown": TEXT, "supporting_sources": array(obj({"title": TEXT, "url": TEXT, "source_identity": TEXT, "supporting_sections": TEXT, "capture_path": TEXT, "capture_sha256": TEXT})), "related_sources": array(obj({"source_id": TEXT, "note": TEXT}))})
    application["properties"].update({
        "personal_relevance": text_schema(values=["高", "中", "低", "未知"]),
        "personal_relevance_reason": TEXT,
        "idea_candidate_status": text_schema(values=["has_candidates", "no_reusable_idea", "needs_review"]),
    })
    page = obj({"page_id": TEXT, "title": TEXT, "title_evidence": TEXT,
        "page_focus": TEXT, "page_focus_evidence": TEXT, "layout_archetype": TEXT,
        "content_use": text_schema(values=VALID_KNOWLEDGE_USE),
        "graph_type": text_schema(values=[g for g, c in INFOGRAPHIC_GRAPH_TYPE_CONTRACTS.items() if c.get("formal_generation_allowed") is not False]),
        "visual_structure": visual_schemas(), "coverage_ids": array(TEXT, 1),
        "reader_question": TEXT, "reader_takeaway": TEXT, "cross_page_handoff": TEXT,
        "collection_thesis": TEXT, "collection_importance": TEXT, "collection_roadmap_summary": TEXT,
        "number_labels": TEXTS, "number_evidence": TEXT},
        ["page_id", "page_focus", "page_focus_evidence", "layout_archetype", "content_use", "graph_type", "visual_structure", "coverage_ids", "reader_question", "reader_takeaway", "cross_page_handoff"])
    page["properties"]["reader_review"] = obj({
        "page_sha256": TEXT, "method": text_schema(values=["isolated_page_readback"]),
        "reviewer_id": TEXT, "verdict": text_schema(values=["passed", "needs_revision"]),
        "answer": TEXT, "relationship_explanation": TEXT,
        "unexplained_terms": TEXTS, "missing_context": TEXTS, "misleading_relations": TEXTS})
    method_card = obj({
        "title": TEXT,
        "purpose": TEXT,
        "observed_design": TEXT,
        "reproduction_method": TEXT,
        "boundary": TEXT,
    })
    case_evidence = obj({"method_card": method_card})
    properties = {
        "schema_version": text_schema(values=[VERSION]), "input_fingerprint": TEXT,
        "source_catalog_version": text_schema(values=SOURCE_CATALOG_VERSIONS),
        "source_metadata": metadata,
        "analysis": obj({"summary": array(TEXT, 1), "source_claims": array(claim, 1)}),
        "coverage_units": array(unit, 1), "knowledge": array(knowledge, 1),
        "relations": array(relation), "application": application,
        "prior_knowledge": compiled_prior_knowledge_schema(),
        "structure_decisions": structure_decisions_schema(),
        "asset_mode": text_schema(values=[ASSET_MODE_FORMAL, ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE]),
        "case_evidence": case_evidence,
        "infographics": array(page),
    }
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **obj(
        properties,
        ["schema_version", "input_fingerprint", "source_metadata", "analysis", "coverage_units", "knowledge", "relations", "application", "infographics"],
    )}


def compact_plan_schema():
    """The bounded semantic input used by new text-first jobs.

    Source identity, full-body retention, source quotations, KP IDs, coverage
    IDs and conservative no-context application values are compiler facts.  An
    agent supplies only the semantic claims it actually analysed, bound to the
    source-block IDs emitted in its work order.  The legacy v1 schema remains
    available for formal infographic, case and compatibility workflows.
    """
    full = plan_schema()["properties"]
    metadata = copy.deepcopy(full["source_metadata"])
    metadata["required"] = []
    application = copy.deepcopy(full["application"])
    application["required"] = []
    compact_knowledge = obj({
        "title": TEXT,
        "claim": TEXT,
        "layer": text_schema(values=LAYERS),
        "confidence": CONFIDENCE,
        "evidence_block_ids": array(TEXT, 1),
    })
    compact_knowledge["properties"]["reusable_concept"] = reusable_concept_schema()
    compact_relation = obj({
        "source_index": {
            **TEXT,
            "description": "1-based position of the source item in the submitted knowledge array, as a string.",
            "examples": ["1"],
        },
        "target_index": {
            **TEXT,
            "description": "1-based position of the target item in the submitted knowledge array, as a string.",
            "examples": ["2"],
        },
        "relation": TEXT,
        "confidence": CONFIDENCE,
        "evidence_block_ids": array(TEXT, 1),
    })
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **obj({
            "schema_version": text_schema(values=[COMPACT_VERSION]),
            "input_fingerprint": TEXT,
            "source_catalog_version": text_schema(values=SOURCE_CATALOG_VERSIONS),
            "summary": array(TEXT, 1),
            "knowledge": array(compact_knowledge, 1),
            "source_metadata": metadata,
            "relations": array(compact_relation),
            "application": application,
            "prior_knowledge": prior_knowledge_schema(),
            "structure_decisions": structure_decisions_schema(),
        }, ["schema_version", "input_fingerprint", "summary", "knowledge"]),
    }


def schema_errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Validate the small, explicit JSON Schema vocabulary emitted above.

    This is intentionally not a general JSON Schema implementation. No coercion,
    default injection or silently ignored semantic input is permitted.
    """
    if "oneOf" in schema:
        results = [schema_errors(value, option, path) for option in schema["oneOf"]]
        if sum(not errors for errors in results) == 1:
            return []
        return [f"{path}: does not match exactly one permitted graph structure"]
    expected = schema.get("type")
    types = {"object": dict, "array": list, "string": str}
    if expected in types and not isinstance(value, types[expected]):
        return [f"{path}: expected {expected}"]
    errors = []
    if expected == "object":
        properties = schema["properties"]
        errors += [f"{path}.{key}: unknown field" for key in value if key not in properties]
        errors += [f"{path}.{key}: required" for key in schema.get("required", []) if key not in value]
        for key in value.keys() & properties.keys():
            errors.extend(schema_errors(value[key], properties[key], f"{path}.{key}"))
    elif expected == "array":
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], f"{path}[{index}]"))
    elif expected == "string":
        if len(value.strip()) < schema.get("minLength", 0):
            errors.append(f"{path}: nonempty text required")
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: expected one of {schema['enum']}")
    return errors


def reusable_concept_schema():
    return obj({"status": text_schema(values=["reusable", "not_reusable", "needs_review"]),
                "name": text_schema(empty=True), "reason": TEXT,
                "understanding_delta": text_schema(empty=True)}, ["status", "reason"])


def prior_knowledge_schema():
    """What the agent supplies about older knowledge.  Rules and error codes: Compiler spec section 7.2."""
    link = obj({
        "source_index": {**TEXT, "description": "1-based position of the new knowledge item in this plan, as a string.", "examples": ["1"]},
        "target_kp_uid": {**TEXT, "description": "kp_uid of an older knowledge point listed in related-knowledge.md or the check output."},
        "relation_type": {**TEXT, "description": "One of: " + ", ".join(RELATION_TYPES) + "."},
        "insight": {**TEXT, "description": "What putting the two points together shows; not a rewording of either claim."},
        "evidence_block_ids": {**array(TEXT), "description": "At least one source block id of this source that supports the new side."},
        "concept_relation": {**text_schema(values=CONCEPT_RELATIONS), "description": "Required when relation_type is supports or extends: same_concept = the new item's reusable_concept.name is exactly the target concept's name; distinct = it stays a separate concept. A target listed as 未歸入概念的知識點 has no concept yet, so only distinct fits."},
        "concept_relation_reason": {**text_schema(empty=True), "description": "Required when concept_relation is distinct: one sentence on why the two concepts stay apart."},
    }, ["source_index", "target_kp_uid", "relation_type", "insight"])
    return obj({
        "decision": text_schema(values=PRIOR_DECISIONS),
        "reason": text_schema(empty=True),
        "cross_links": array(link),
        "not_linked": array(obj({"target_kp_uid": TEXT, "reason": TEXT})),
    }, ["decision"])


def compiled_prior_knowledge_schema():
    link = obj({"source_kp": TEXT, "target_kp_uid": TEXT, "relation_type": TEXT, "insight": TEXT,
                "evidence_block_ids": TEXTS, "evidence_anchor": text_schema(empty=True),
                "concept_relation": text_schema(values=CONCEPT_RELATIONS), "concept_relation_reason": text_schema(empty=True)},
               ["source_kp", "target_kp_uid", "relation_type", "insight", "evidence_anchor"])
    return obj({
        "decision": text_schema(empty=True),
        "reason": text_schema(empty=True),
        "cross_links": array(link),
        "not_linked": array(obj({"target_kp_uid": TEXT, "reason": TEXT})),
    }, ["decision", "reason", "cross_links", "not_linked"])


def structure_decisions_schema():
    """Optional dismissals for source-native named structures in a new plan."""
    return array(obj({
        "block_id": TEXT,
        "decision": text_schema(values=STRUCTURE_DECISIONS),
        "reason": TEXT,
    }, ["block_id", "decision", "reason"]))


def blank_prior_knowledge():
    """The unfilled template the work order may carry; equal to leaving the section out."""
    return {"decision": "", "reason": "", "cross_links": [], "not_linked": []}


def without_blank_prior_knowledge(plan: dict) -> dict:
    value = plan.get("prior_knowledge") if isinstance(plan, dict) else None
    if isinstance(value, dict) and set(value) <= set(blank_prior_knowledge()) and not any(
            value.get(key) for key in blank_prior_knowledge()):
        return {key: item for key, item in plan.items() if key != "prior_knowledge"}
    return plan


def input_fingerprint(package: Path, catalog_version: str | None = None) -> str:
    names = (
        "input_bundle.json",
        "canonical_source.md",
        "source_capture_metadata.json",
        "image_analysis.json",
        "case_evidence/manifest.json",
    )
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
               for name in names if (package / name).is_file()}
    if catalog_version is not None:
        catalog = source_block_catalog(package, version=catalog_version)
        payload["source_catalog"] = hashlib.sha256(json.dumps(catalog, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def source_block_catalog(package: Path, *, version: str | None = None) -> dict:
    """Expose stable source-local evidence IDs without promoting them to coverage.

    The canonical body remains the complete retained source.  Text blocks only
    make exact locations addressable for an agent's selected semantic claims;
    they do not say every paragraph has been analysed or covered.  Image jobs
    use already accepted IMG anchors, so a later semantic correction never
    asks the agent to re-read the original image.
    """
    if version not in {None, *SOURCE_CATALOG_VERSIONS}:
        raise ValueError("unsupported source catalog strategy")
    bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8"))
    kind = bundle.get("source_input_kind")
    if kind == "user_provided_image":
        analysis_path = package / "image_analysis.json"
        if not analysis_path.is_file():
            raise ValueError("source blocks require accepted original-image analysis")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        transcriptions: dict[str, list[str]] = {}
        for row in analysis.get("transcription", []):
            if isinstance(row, dict) and isinstance(row.get("anchor_id"), str) and isinstance(row.get("text"), str):
                transcriptions.setdefault(row["anchor_id"], []).append(row["text"])
        blocks = []
        for anchor in analysis.get("anchors", []):
            if not isinstance(anchor, dict):
                continue
            block_id = str(anchor.get("id") or "").strip()
            if not block_id:
                continue
            evidence = str(anchor.get("evidence") or "").strip()
            text = "\n".join(part for part in [evidence, *transcriptions.get(block_id, [])] if part).strip()
            blocks.append({"id": block_id, "text": text})
    else:
        canonical = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
        blocks = [
            {"id": f"SRC-{index:03d}", "text": paragraph.strip()}
            for index, paragraph in enumerate(re.split(r"\n\s*\n", canonical), start=1)
            if paragraph.strip()
        ]
    if not blocks or any(not block["text"] for block in blocks):
        raise ValueError("source blocks are incomplete; reread the accepted source")
    result = {
        "canonical_body_sha256": bundle.get("canonical_body_sha256", ""),
        "blocks": blocks,
    }
    if version:
        result["strategy"] = version
        if kind != "user_provided_image":
            if (
                version in {SOURCE_CATALOG_VERSION_V3, SOURCE_CATALOG_VERSION_V4}
                and bundle.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE
                and is_youtube_caption_transcript(canonical)
            ):
                # A caption archive is read through its de-duplicated chunks.
                # The whole transcript is one SRC paragraph, so it is never exposed.
                result["blocks"] = caption_reading_chunks(canonical)
            else:
                result["legacy_blocks"] = blocks  # Never reinterpret an existing SRC ID.
                result["blocks"] = precise_source_spans(canonical)
                if version == SOURCE_CATALOG_VERSION_V4:
                    result["blocks"] = _with_heading_metadata(result["blocks"], canonical)
    return result


def _with_heading_metadata(blocks: list[dict], canonical: str) -> list[dict]:
    """Add v4-only ATX heading facts without changing v3 blocks or their IDs."""
    metadata = _markdown_heading_lines(canonical)
    result = []
    for block in blocks:
        row = dict(block)
        start = int(row.get("start", -1))
        level = next((candidate_level for line_start, line_end, candidate_level, _ in metadata
                      if line_start <= start < line_end), None)
        row["kind"] = "heading" if level is not None else "text"
        row["heading_level"] = level
        result.append(row)
    return result


def _markdown_heading_lines(canonical: str) -> list[tuple[int, int, int, str]]:
    """Return original ATX heading lines, respecting their exact fence delimiters."""
    metadata: list[tuple[int, int, int, str]] = []
    fence: tuple[str, int] | None = None
    offset = 0
    for line in canonical.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        marker = re.match(r"^(?P<marker>`{3,}|~{3,})", raw)
        if marker:
            token = marker.group("marker")
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1]:
                fence = None
        elif fence is None:
            match = re.match(r"^(#{1,6})[ \t]+", raw)
            if match:
                metadata.append((offset, offset + len(raw), len(match.group(1)),
                                 re.sub(r"^#{1,6}[ \t]+", "", raw).strip()))
        offset += len(line)
    return metadata


def _point_references(plan: dict, package: Path | None = None) -> list[set[str]]:
    text_blocks: list[dict] = []
    if (
        package is not None
        and plan.get("source_catalog_version") == SOURCE_CATALOG_VERSION_V4
    ):
        bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8"))
        if bundle.get("source_input_kind") != "user_provided_image":
            text_blocks = source_block_catalog(package, version=SOURCE_CATALOG_VERSION_V4)["blocks"]
    references = []
    for point in plan.get("knowledge") or []:
        if not isinstance(point, dict):
            references.append(set())
            continue
        block_ids = point.get("evidence_block_ids")
        if isinstance(block_ids, list):
            references.append({str(block_id) for block_id in block_ids})
        elif text_blocks:
            anchor = str(point.get("evidence_anchor") or "")
            references.append({str(block["id"]) for block in text_blocks
                               if anchor and anchor in str(block.get("text") or "")})
        else:
            references.append(set(re.findall(r"\bIMG-[A-Za-z0-9_-]+\b", str(point.get("evidence_anchor") or ""))))
    return references


def _normalised_structure_title(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    while text and (text[0].isspace() or unicodedata.category(text[0]).startswith("P")):
        text = text[1:]
    while text and (text[-1].isspace() or unicodedata.category(text[-1]).startswith("P")):
        text = text[:-1]
    return text.strip()


def _image_named_structure_groups(plan: dict, package: Path) -> list[dict]:
    """Return image candidates through the shared source-named structure rule."""
    return _named_structure_groups(plan, package, image_only=True)


def _named_structure_groups(plan: dict, package: Path, *, image_only: bool = False) -> list[dict]:
    """Return source-native title/heading groups that have enough cited members."""
    bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8"))
    references = _point_references(plan, package)
    headings: list[tuple[str, str, int | None, int, list[str]]] = []
    is_v4_text = False
    if bundle.get("source_input_kind") == "user_provided_image":
        analysis_path = package / "image_analysis.json"
        if not analysis_path.is_file():
            return []
        anchors = [anchor for anchor in json.loads(analysis_path.read_text(encoding="utf-8")).get("anchors", [])
                   if isinstance(anchor, dict) and str(anchor.get("id") or "").strip()]
        for index, anchor in enumerate(anchors):
            if anchor.get("kind") != "title":
                continue
            following = []
            for child in anchors[index + 1:]:
                if child.get("kind") == "title":
                    break
                following.append(str(child["id"]))
            headings.append((str(anchor["id"]), str(anchor.get("evidence") or "").strip(), None, index, following))
    elif not image_only and plan.get("source_catalog_version") == SOURCE_CATALOG_VERSION_V4:
        catalog = source_block_catalog(package, version=SOURCE_CATALOG_VERSION_V4)
        blocks = catalog["blocks"]
        canonical = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
        lines = _markdown_heading_lines(canonical)
        is_v4_text = True
        for index, (line_start, line_end, level, title) in enumerate(lines):
            heading_blocks = [block for block in blocks
                              if line_start <= int(block.get("start", -1)) < line_end]
            if not heading_blocks:
                continue
            next_boundary = next((next_start for next_start, _, next_level, _ in lines[index + 1:]
                                  if next_level <= level), len(canonical))
            following = [str(block["id"]) for block in blocks
                         if line_end <= int(block.get("start", -1)) < next_boundary]
            headings.append((str(heading_blocks[0]["id"]), title, level, index, following))
    else:
        return []

    groups = []
    source_title = _normalised_structure_title(bundle.get("title")) if is_v4_text else ""
    h1_count = sum(1 for _, _, level, _, _ in headings if level == 1) if is_v4_text else 0
    for block_id, title, level, index, following in headings:
        if source_title and _normalised_structure_title(title) == source_title:
            continue
        if level == 1 and h1_count == 1 and index == 0:
            continue
        members = [point_index for point_index, point_refs in enumerate(references) if point_refs.intersection(following)]
        if len(members) >= STRUCTURE_CANDIDATE_MIN_MEMBERS:
            groups.append({"block_id": block_id, "title": title, "members": members,
                           "parents": [point_index for point_index, point_refs in enumerate(references) if block_id in point_refs]})
    return groups


def _plan_relation_pairs(plan: dict) -> set[tuple[int, int]]:
    """Normalise the two plan relation shapes to zero-based point pairs."""
    pairs = set()
    knowledge = plan.get("knowledge") or []
    if plan.get("schema_version") == COMPACT_VERSION:
        for relation in plan.get("relations") or []:
            if not isinstance(relation, dict):
                continue
            try:
                source, target = int(relation.get("source_index")), int(relation.get("target_index"))
            except (TypeError, ValueError):
                continue
            if 1 <= source <= len(knowledge) and 1 <= target <= len(knowledge):
                pairs.add((source - 1, target - 1))
        return pairs
    positions = {str(item.get("kp_id") or ""): index for index, item in enumerate(knowledge) if isinstance(item, dict)}
    for relation in plan.get("relations") or []:
        if not isinstance(relation, dict):
            continue
        source = positions.get(str(relation.get("source_kp") or ""))
        target = positions.get(str(relation.get("target_kp") or ""))
        if source is not None and target is not None:
            pairs.add((source, target))
    return pairs


def _meaningful_structure_reason(value: object) -> bool:
    return sum(character.isalnum() for character in str(value or "")) >= STRUCTURE_DECISION_MIN_REASON_CHARS


def named_structure_issues(plan: dict, package: Path) -> list[str]:
    """Return all deterministic named-structure gaps for a new eligible plan.

    A current image title is a candidate when at least three knowledge points
    cite following anchors.  A title reference is ordinary context unless its
    point also keeps the visible source name and links every member; that bundle
    is the affirmative framework-parent declaration.
    """
    groups = _named_structure_groups(plan, package)
    if not groups:
        return []
    relations = _plan_relation_pairs(plan)
    decisions = plan.get("structure_decisions") or []
    decision_by_block = {}
    issues = []
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            continue
        block_id = str(decision.get("block_id") or "")
        if block_id in decision_by_block:
            issues.append(f"named_structure_decision_duplicate: structure_decisions[{index}].block_id {block_id!r} repeats an earlier decision.")
        decision_by_block[block_id] = decision

    unresolved = []
    candidate_ids = {group["block_id"] for group in groups}
    for group in groups:
        block_id, parents = group["block_id"], group["parents"]
        decision = decision_by_block.get(block_id)
        if decision is not None:
            if not _meaningful_structure_reason(decision.get("reason")):
                issues.append(f"named_structure_decision_reason_thin: structure_decisions for {block_id} needs a meaningful reason for not_a_named_structure.")
            continue
        valid_parent = False
        for parent in parents:
            point = (plan.get("knowledge") or [])[parent]
            parent_text = "\n".join(str(point.get(key) or "") for key in ("title", "claim"))
            members = [member for member in group["members"] if member != parent]
            linked = all((parent, member) in relations or (member, parent) in relations for member in members)
            if group["title"] and group["title"] not in parent_text:
                continue
            if linked:
                valid_parent = True
                break
        if not valid_parent:
            unresolved.append(group)

    for block_id in decision_by_block:
        if block_id not in candidate_ids:
            issues.append(f"named_structure_decision_not_candidate: structure_decisions names {block_id!r}, which is not an undecided named-structure candidate.")
    if unresolved:
        labels = ", ".join(f"{group['block_id']} ({group['title'] or 'untitled title anchor'})" for group in unresolved)
        issues.insert(0,
            "named_structure_decision_missing: undecided named-structure candidate(s): " + labels
            + ". Add a parent knowledge point citing the title and relations to its members, or one structure_decisions not_a_named_structure entry with a meaningful reason for each listed block.")
    return issues


def precise_source_spans(canonical: str) -> list[dict]:
    """Exact, immutable offsets with neighbouring context, not rewritten captions.

    Sentence ends select small spans for ordinary prose. Caption lines are kept
    intact, grouped through sentence ends, with neighbouring cues for context.
    Unpunctuated text uses bounded line groups; an agent can cite adjacent spans
    together when the complete claim needs more context.
    """
    if not re.search(r"^\[\d{2}:\d{2}:\d{2}.*? --> ", canonical, re.M):
        spans = []
        for paragraph in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", canonical):
            begin = paragraph.start()
            boundaries = [paragraph.start()+m.end() for m in re.finditer(
                r"(?:[。！？]+[\"'”’）)]*|[.!?]+[\"'”’）)]*(?=\s|$))", paragraph.group())]
            for end in [*boundaries, paragraph.end()]:
                while begin < end and canonical[begin].isspace(): begin += 1
                while end > begin and canonical[end-1].isspace(): end -= 1
                if end > begin:
                    spans.append({"id": f"SPAN-{begin:07d}-{end:07d}", "text": canonical[begin:end], "start": begin, "end": end})
                begin = end
        return spans
    lines = list(re.finditer(r"[^\n]+(?:\n|$)", canonical))
    groups = []
    start = 0
    for index, line in enumerate(lines):
        text = line.group().rstrip()
        paragraph_end = line.end() < len(canonical) and canonical[line.end():].startswith("\n")
        sentence_end = bool(re.search(r"[。！？.!?][\"'”’）)]*$", text))
        if sentence_end or paragraph_end or index - start >= 11 or index == len(lines) - 1:
            # Caption context includes two adjacent complete cues. Offsets refer
            # to the retained canonical body, so a displayed ID cannot drift.
            caption = bool(re.match(r"\[\d{2}:\d{2}:\d{2}", lines[start].group()))
            first = max(0, start - 2) if caption else start
            last = min(len(lines) - 1, index + 2) if caption else index
            begin, end = lines[first].start(), lines[last].end()
            while end > begin and canonical[end - 1].isspace(): end -= 1
            groups.append({"id": f"SPAN-{begin:07d}-{end:07d}", "text": canonical[begin:end],
                           "start": begin, "end": end})
            start = index + 1
    return list({row["id"]: row for row in groups}.values())


def semantic_response_schema(asset_mode: str, *, current: bool = True) -> dict:
    """Return the narrow normal-route contract without changing explicit modes."""
    result = compact_plan_schema() if asset_mode == ASSET_MODE_KNOWLEDGE else plan_schema()
    if current:
        result["required"] = list(result["required"]) + ["source_catalog_version"]
        item = result["properties"]["knowledge"]["items"]
        item["required"] = list(item["required"]) + ["reusable_concept"]
        if asset_mode == ASSET_MODE_KNOWLEDGE:
            result["properties"]["relations"]["items"]["properties"]["relation"] = text_schema(values=RELATION_TYPES)
    return result


def _default_source_metadata(bundle: dict) -> dict:
    kind = str(bundle.get("source_input_kind") or "")
    source_type = {
        "user_provided_text": "使用者提供文字",
        "user_provided_image": "使用者提供圖片",
        "url": "其他",
    }.get(kind, "其他")
    return {
        "source_label": str(bundle.get("title") or bundle.get("source_id") or "來源"),
        "source_type": source_type,
        "primary_work_status": "none",
    }


def retrieval_alias_errors(
    metadata: object,
    source_title: str,
    *,
    path: str,
) -> list[str]:
    """Return every retrieval-alias contract breach with one stable error code."""
    if not isinstance(metadata, dict) or "retrieval_aliases" not in metadata:
        return []
    aliases = metadata.get("retrieval_aliases")
    if not isinstance(aliases, list):
        return [f"retrieval_alias_invalid: {path}.retrieval_aliases 必須是字串陣列"]
    errors: list[str] = []
    seen: set[str] = set()
    title_key = unicodedata.normalize("NFKC", source_title).casefold().strip()
    for index, value in enumerate(aliases, start=1):
        prefix = f"retrieval_alias_invalid: {path}.retrieval_aliases 第 {index} 項"
        if index > RETRIEVAL_ALIAS_MAX_ITEMS:
            errors.append(f"{prefix} 超出最多 {RETRIEVAL_ALIAS_MAX_ITEMS} 項。")
        if not isinstance(value, str):
            errors.append(f"{prefix} 必須是字串。")
            continue
        alias = value.strip()
        if not alias:
            errors.append(f"{prefix} 去空白後不可為空。")
        if len(alias) > RETRIEVAL_ALIAS_MAX_CHARS:
            errors.append(f"{prefix} 去空白後不可超過 {RETRIEVAL_ALIAS_MAX_CHARS} 字元。")
        if "\n" in alias or "\r" in alias:
            errors.append(f"{prefix} 不可含換行。")
        if "[[" in alias or "]]" in alias or "|" in alias:
            errors.append(f"{prefix} 不可含 [[、]] 或 |。")
        alias_key = unicodedata.normalize("NFKC", alias).casefold()
        if alias_key and alias_key == title_key:
            errors.append(f"{prefix} 不可與來源標題完全相同。")
        if alias_key in seen:
            errors.append(f"{prefix} 與較早別名重複（忽略大小寫）。")
        elif alias_key:
            seen.add(alias_key)
    return errors


def normalised_retrieval_aliases(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep the accepted source metadata shape while storing aliases without edge whitespace."""
    result = copy.deepcopy(metadata)
    if "retrieval_aliases" in result:
        result["retrieval_aliases"] = [item.strip() for item in result["retrieval_aliases"]]
    return result


def plan_errors_with_retrieval_aliases(plan: object, schema: dict, source_title: str) -> list[str]:
    """Let the existing schema gate own every field except the alias-specific diagnostics."""
    errors = schema_errors(plan, schema)
    errors = [
        error for error in errors
        if not error.startswith("$.source_metadata.retrieval_aliases")
    ]
    metadata = plan.get("source_metadata") if isinstance(plan, dict) else None
    return errors + retrieval_alias_errors(metadata, source_title, path="source_metadata")


def _default_application() -> dict:
    return {
        "adoption_status": "收藏",
        "adoption_reason": "先保存已核對的來源內容，未加入未有證據的個人判斷。",
        "personalization_links": [],
        "context_anchors": [],
        "compounding_insight_status": "needs_context",
        "compounding_insight_types": [],
        "compounding_next_action": "有具體既有筆記證據時才補充個人脈絡判斷。",
        "taste_network_status": "否",
        "compounding_judgments": {
            kind: {"decision": "需要更多脈絡", "reason": "未提供可核對的既有筆記證據。"}
            for kind in VALID_COMPOUNDING_TYPES
        },
        "taste_link_status": "not_applicable",
        "taste_reason": "本次未建立品味扣連，保留作知識與工作流程材料。",
    }


def knowledge_use_issues(plan: dict) -> list[str]:
    """Require an explicit use decision for plans issued under the v4 contract."""
    if plan.get("source_catalog_version") != SOURCE_CATALOG_VERSION_V4:
        return []
    application = plan.get("application")
    if not isinstance(application, dict) or "knowledge_use" not in application:
        return ["knowledge_use_decision_missing: application.knowledge_use is required for a new plan; choose one of " + repr(sorted(VALID_KNOWLEDGE_USE))]
    return []


def _with_legacy_knowledge_use_default(plan: dict) -> dict:
    """Keep the historic default only for v2/v3 plans already issued under it."""
    if (
        isinstance(plan, dict)
        and plan.get("source_catalog_version") in {None, "", SOURCE_CATALOG_VERSION, SOURCE_CATALOG_VERSION_V3}
        and isinstance(plan.get("application"), dict)
        and "knowledge_use" not in plan["application"]
    ):
        plan = copy.deepcopy(plan)
        plan["application"] = {**plan["application"], "knowledge_use": "事實真源"}
    return plan


def materialize_compact_plan(plan: dict, package: Path) -> dict:
    """Compile compact, source-block-bound analysis into the existing v1 KP plan."""
    plan = without_blank_prior_knowledge(plan)
    plan = _with_legacy_knowledge_use_default(plan)
    bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8"))
    errors = plan_errors_with_retrieval_aliases(plan, compact_plan_schema(), str(bundle.get("title") or ""))
    errors += knowledge_use_issues(plan)
    if errors:
        raise ValueError("\n".join(errors))
    if plan["input_fingerprint"] != input_fingerprint(package, plan.get("source_catalog_version")):
        raise ValueError("input_fingerprint: accepted source changed; reread the current work order")
    if plan.get("source_catalog_version") in SOURCE_CATALOG_VERSIONS:
        issues = named_structure_issues(plan, package)
        if issues:
            raise ValueError("\n".join(issues))
    if plan.get("source_catalog_version") in SOURCE_CATALOG_VERSIONS:
        for index, point in enumerate(plan["knowledge"]):
            if "reusable_concept" not in point:
                raise ValueError(f"knowledge[{index}].reusable_concept: same-round decision required by the current analysis contract")
        for index, relation in enumerate(plan.get("relations") or []):
            if relation["relation"] not in RELATION_TYPES:
                raise ValueError(f"relations[{index}].relation: expected one of {list(RELATION_TYPES)}")
    if bundle.get("asset_mode") != ASSET_MODE_KNOWLEDGE:
        raise ValueError("compact semantic plan is only valid for an accepted knowledge_only job")
    catalog = source_block_catalog(package, version=plan.get("source_catalog_version"))
    expected_hash = str(bundle.get("canonical_body_sha256") or "")
    if catalog["canonical_body_sha256"] != expected_hash:
        raise ValueError("source block catalog canonical hash mismatch")
    rows = {row["id"]: row for row in [*catalog.get("legacy_blocks", []), *catalog["blocks"]]}
    blocks = {block_id: row["text"] for block_id, row in rows.items()}
    # A v3 YouTube caption archive quotes de-duplicated chunks with one time link.
    caption_archive = (
        plan.get("source_catalog_version") in {SOURCE_CATALOG_VERSION_V3, SOURCE_CATALOG_VERSION_V4}
        and bundle.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE
        and bundle.get("source_input_kind") == "url"
        and bool(bundle.get("youtube_video_id"))
        and is_youtube_caption_transcript((package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n"))
    )
    quote_errors: list[str] = []

    def evidence(block_ids: list[str], where: str = "") -> str:
        unknown = [block_id for block_id in block_ids if block_id not in blocks]
        if unknown:
            raise ValueError("evidence_block_ids: unknown accepted source block(s): " + ", ".join(unknown))
        if bundle.get("source_input_kind") == "user_provided_image":
            return ", ".join(block_ids)
        if caption_archive:
            quote = SOURCE_EVIDENCE_FRAGMENT_SEPARATOR.join(blocks[block_id] for block_id in block_ids)
            quote += " — " + youtube_time_link(str(bundle["youtube_video_id"]), rows[block_ids[0]]["start_seconds"])
            if len(block_ids) > CAPTION_EVIDENCE_MAX_BLOCKS or len(quote) > CAPTION_EVIDENCE_MAX_CHARS:
                quote_errors.append(
                    f"{where}.evidence_block_ids: evidence_quote_too_long: {len(block_ids)} block(s) render {len(quote)} characters; "
                    f"cite at most {CAPTION_EVIDENCE_MAX_BLOCKS} blocks and {CAPTION_EVIDENCE_MAX_CHARS} characters, choosing the chunks that best support the claim")
            return quote
        return SOURCE_EVIDENCE_FRAGMENT_SEPARATOR.join(blocks[block_id] for block_id in block_ids)

    evidence_locations = [
        (f"knowledge[{index}]", item["evidence_block_ids"])
        for index, item in enumerate(plan["knowledge"])
    ]
    evidence_locations.extend(
        (f"relations[{index}]", relation["evidence_block_ids"])
        for index, relation in enumerate(plan.get("relations", []))
    )
    given_prior_knowledge = plan.get("prior_knowledge") or {}
    evidence_locations.extend(
        (f"prior_knowledge.cross_links[{index}]", link.get("evidence_block_ids") or [])
        for index, link in enumerate(given_prior_knowledge.get("cross_links") or [])
    )
    unknown_evidence_errors = []
    for where, block_ids in evidence_locations:
        unknown = list(dict.fromkeys(block_id for block_id in block_ids if block_id not in blocks))
        if unknown:
            message = f"{where}.evidence_block_ids: unknown accepted source block(s): {', '.join(unknown)}"
            if where.startswith("prior_knowledge.cross_links["):
                message = f"cross_link_evidence_invalid: {message}"
            unknown_evidence_errors.append(message)
    if unknown_evidence_errors:
        raise ValueError("\n".join(unknown_evidence_errors))

    knowledge = []
    coverage_units = []
    claims = []
    for index, item in enumerate(plan["knowledge"], start=1):
        block_ids = list(item["evidence_block_ids"])
        anchor = evidence(block_ids, f"knowledge[{index - 1}]")
        kp_id = f"KP-{bundle['source_id']}-{index:03d}"
        knowledge.append({
            "kp_id": kp_id,
            "title": item["title"],
            "claim": item["claim"],
            "layer": item["layer"],
            "confidence": item["confidence"],
            "evidence_anchor": anchor,
            **({"reusable_concept": copy.deepcopy(item["reusable_concept"])} if "reusable_concept" in item else {}),
        })
        claims.append({
            "id": f"C-{index:03d}",
            "claim": item["claim"],
            "evidence_anchor": anchor,
            "confidence": item["confidence"],
        })
        coverage_units.append({
            "id": f"CU-{index:03d}",
            "text": item["claim"],
            "source_evidence": anchor,
            "source_block_ids": block_ids,
            "unit_types": ["semantic_knowledge"],
            "value_axes": [],
            "dispositions": ["source_claim", "knowledge_point", "summary"],
            "infographic_text": [],
        })

    relations = []
    for relation_index, relation in enumerate(plan.get("relations", [])):
        try:
            source_index = int(relation["source_index"])
            target_index = int(relation["target_index"])
        except (TypeError, ValueError) as exc:
            raise ValueError("relations: source_index and target_index must be one-based knowledge indexes") from exc
        if not 1 <= source_index <= len(knowledge) or not 1 <= target_index <= len(knowledge):
            raise ValueError("relations: source_index and target_index must reference submitted knowledge")
        relations.append({
            "source_kp": knowledge[source_index - 1]["kp_id"],
            "target_kp": knowledge[target_index - 1]["kp_id"],
            "relation": relation["relation"],
            "evidence_anchor": evidence(list(relation["evidence_block_ids"]), f"relations[{relation_index}]"),
            "confidence": relation["confidence"],
        })

    prior_knowledge = None
    if "prior_knowledge" in plan or plan.get("source_catalog_version") in SOURCE_CATALOG_VERSIONS:
        given = given_prior_knowledge
        compiled_links = []
        for link_index, link in enumerate(given.get("cross_links") or []):
            where = f"prior_knowledge.cross_links[{link_index}]"
            try:
                source_index = int(link["source_index"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{where}.source_index: must be a one-based knowledge index") from exc
            if not 1 <= source_index <= len(knowledge):
                raise ValueError(f"{where}.source_index: must reference submitted knowledge")
            block_ids = list(link.get("evidence_block_ids") or [])
            anchor = ""
            if not block_ids:
                quote_errors.append(f"cross_link_evidence_invalid: {where}.evidence_block_ids needs at least one source block of this source")
            else:
                try:
                    anchor = evidence(block_ids, where)
                except ValueError as exc:
                    quote_errors.append(f"cross_link_evidence_invalid: {where}: {exc}")
            compiled_links.append({
                "source_kp": knowledge[source_index - 1]["kp_id"], "target_kp_uid": link["target_kp_uid"],
                "relation_type": link["relation_type"], "insight": link["insight"],
                "evidence_block_ids": block_ids, "evidence_anchor": anchor,
                **{key: link[key] for key in ("concept_relation", "concept_relation_reason") if key in link},
            })
        prior_knowledge = {
            "decision": given.get("decision", ""), "reason": given.get("reason", ""),
            "cross_links": compiled_links, "not_linked": copy.deepcopy(given.get("not_linked") or []),
        }

    if quote_errors:
        raise ValueError("\n".join(quote_errors))
    metadata = _default_source_metadata(bundle)
    metadata.update(copy.deepcopy(plan.get("source_metadata") or {}))
    application = _default_application()
    application.update(copy.deepcopy(plan.get("application") or {}))
    return {
        "schema_version": VERSION,
        "input_fingerprint": plan["input_fingerprint"],
        **({"source_catalog_version": plan["source_catalog_version"]} if plan.get("source_catalog_version") else {}),
        "source_metadata": metadata,
        "analysis": {"summary": copy.deepcopy(plan["summary"]), "source_claims": claims},
        "coverage_units": coverage_units,
        "knowledge": knowledge,
        "relations": relations,
        "application": application,
        **({"prior_knowledge": prior_knowledge} if prior_knowledge is not None else {}),
        "asset_mode": ASSET_MODE_KNOWLEDGE,
        "infographics": [],
    }


def build_kp(plan: dict, package: Path, *, root: Path) -> dict:
    semantic_plan_version = str(plan.get("schema_version") or "") if isinstance(plan, dict) else ""
    if semantic_plan_version == COMPACT_VERSION:
        plan = materialize_compact_plan(plan, package)
    plan = _with_legacy_knowledge_use_default(plan)
    bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8"))
    errors = plan_errors_with_retrieval_aliases(plan, plan_schema(), str(bundle.get("title") or ""))
    errors += knowledge_use_issues(plan)
    if errors:
        raise ValueError("\n".join(errors))
    if plan["input_fingerprint"] != input_fingerprint(package, plan.get("source_catalog_version")):
        raise ValueError("input_fingerprint: accepted source changed; reread the current work order")
    if semantic_plan_version != COMPACT_VERSION and plan.get("source_catalog_version") in SOURCE_CATALOG_VERSIONS:
        issues = named_structure_issues(plan, package)
        if issues:
            raise ValueError("\n".join(issues))
    accepted_mode = bundle.get("asset_mode", ASSET_MODE_FORMAL)
    if plan.get("source_catalog_version") in SOURCE_CATALOG_VERSIONS:
        for index, point in enumerate(plan["knowledge"]):
            if "reusable_concept" not in point:
                raise ValueError(f"knowledge[{index}].reusable_concept: same-round decision required by the current analysis contract")
    asset_mode = plan.get("asset_mode", accepted_mode)
    if asset_mode not in {ASSET_MODE_FORMAL, ASSET_MODE_CASE, ASSET_MODE_KNOWLEDGE}:
        raise ValueError("asset_mode: unsupported value")
    if accepted_mode != asset_mode:
        raise ValueError("asset_mode: differs from accepted input bundle")
    if asset_mode == ASSET_MODE_FORMAL:
        if "case_evidence" in plan or not plan.get("infographics"):
            raise ValueError("formal_infographic requires a nonempty infographic plan and no case_evidence")
    elif asset_mode == ASSET_MODE_CASE:
        if bundle.get("source_input_kind") != "url":
            raise ValueError("case_evidence is only supported for accepted URL sources")
        if plan.get("infographics"):
            raise ValueError("case_evidence must not carry infographic pages")
        if not isinstance(plan.get("case_evidence"), dict):
            raise ValueError("case_evidence requires a method_card")
        try:
            package_case_manifest = package_manifest(package, bundle)
        except Exception as exc:
            raise ValueError(f"case_evidence manifest invalid: {exc}") from exc
    else:
        if "case_evidence" in plan or plan.get("infographics"):
            raise ValueError("knowledge_only requires no case_evidence and zero infographic pages")
    source_keys = ("source_id", "source_input_kind", "source_identity", "title", "title_source", "capture_date",
                   "canonical_body_sha256", "source_content_sha256", "source_image_sha256", "source_image_mime",
                   "source_image_dimensions", "source_url", "normalized_url", "source_url_hash", "source_profile", "anchors",
                   "asset_mode", "case_evidence_manifest_path", "case_evidence_manifest_sha256", "case_evidence_source_manifest_sha256")
    source = {key: copy.deepcopy(bundle[key]) for key in source_keys if key in bundle}
    source.update(normalised_retrieval_aliases(plan["source_metadata"]))
    # The mode is a compiler fact fixed at acceptance; a plan cannot choose it.
    source["source_retention_mode"] = source["retention_mode"] = (
        "original_image_required" if source["source_input_kind"] == "user_provided_image"
        else TRANSCRIPT_ARCHIVE_MODE if bundle.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE
        else "full_body_required")
    source["readability_status"] = {"url": "public_ok", "user_provided_text": "user_text_complete", "user_provided_image": "user_image_complete"}[source["source_input_kind"]]
    kp = {"schema_version": "knowledge_package_v2", "semantic_plan_version": semantic_plan_version or VERSION,
          **({"source_catalog_version": plan["source_catalog_version"]} if plan.get("source_catalog_version") else {}),
          "job_id": bundle["job_id"], "source": source,
          **{key: copy.deepcopy(plan[key]) for key in ("analysis", "coverage_units", "knowledge", "relations", "application", "infographics")},
          **({"prior_knowledge": copy.deepcopy(plan["prior_knowledge"])} if "prior_knowledge" in plan else {}),
          "projection": {"source_identity": source["source_identity"], "asset_mode": asset_mode}}
    normalize_knowledge_decisions(kp)
    source["asset_mode"] = asset_mode
    if asset_mode == ASSET_MODE_CASE:
        kp["case_evidence"] = {
            "manifest_path": bundle["case_evidence_manifest_path"],
            "manifest_sha256": bundle["case_evidence_manifest_sha256"],
            "creator": copy.deepcopy(package_case_manifest["creator"]),
            "screenshots": copy.deepcopy(package_case_manifest["screenshots"]),
            "technical_references": copy.deepcopy(package_case_manifest["technical_references"]),
            "reproduction": copy.deepcopy(package_case_manifest["reproduction"]),
            "method_card": copy.deepcopy(plan["case_evidence"]["method_card"]),
        }
    units = {unit["id"]: unit for unit in kp["coverage_units"]}
    if len(units) != len(kp["coverage_units"]):
        raise ValueError("coverage_units: duplicate IDs")
    page_ids = [page["page_id"] for page in kp["infographics"]]
    if len(set(page_ids)) != len(page_ids):
        raise ValueError("infographics: duplicate page IDs")
    for page in kp["infographics"]:
        page["status"] = "planned"
    bound_pages, binding_issues = materialize_infographic_page_bindings(
        kp["coverage_units"], kp["infographics"],
    )
    if binding_issues:
        raise ValueError("; ".join(issue["message"] for issue in binding_issues))
    kp["infographics"] = bound_pages
    for page in kp["infographics"]:
        if source["primary_work_status"] == "primary":
            page["title"] = page["primary_work_title"] = source.get("primary_work_title", "")
            page["title_source"] = "primary_work"
        elif source["title_source"] in {"user_supplied", "source_declared"}:
            page["title"] = reader_infographic_title(source["title"])
            page["title_source"] = source["title_source"]
        else:
            page["title_source"] = "source_derived"
            if not page.get("title") or not page.get("title_evidence"):
                raise ValueError("derived infographic title requires title and verbatim title_evidence")
    errors = content_errors(kp, package, root=root, strict=True)
    if errors:
        raise ValueError("\n".join(errors))
    return kp


def normalize_knowledge_decisions(kp: dict) -> None:
    """One normalisation owner for compact/full/case and legacy packages."""
    application = kp.get("application", {})
    application.setdefault("personal_relevance", "未知")
    application.setdefault("personal_relevance_reason", "未提供可核對的個人脈絡判斷。")
    decisions = [item.get("reusable_concept", {}).get("status") for item in kp.get("knowledge", [])]
    if "idea_candidate_status" not in application:
        application["idea_candidate_status"] = (
            "has_candidates" if "reusable" in decisions else
            "no_reusable_idea" if decisions and all(status == "not_reusable" for status in decisions) else
            "needs_review")


def content_errors(kp: dict, package: Path, *, root: Path | None = None, strict=False) -> list[str]:
    """Cheap semantic-shape/evidence checks, before any expensive image action."""
    errors = []
    explicit_strict = strict
    strict = strict or bool(kp.get("semantic_plan_version"))
    if root is None and strict:
        root = next((parent.parent for parent in package.parents if parent.name == "outputs"), None)
    ground_quotes = strict or (package / "input_bundle.json").is_file()
    body_path = package / "canonical_source.md"
    body = body_path.read_text(encoding="utf-8") if body_path.is_file() else ""
    source = kp.get("source")
    if not isinstance(source, dict):
        return ["source: expected object"]
    errors.extend(retrieval_alias_errors(source, str(source.get("title") or ""), path="source"))
    if (
        explicit_strict
        and body
        and "primary_work_status" in source
    ):
        errors.extend(validate_primary_work_metadata(source, body))
    image_ids = set()
    if source.get("source_input_kind") == "user_provided_image":
        analysis_path = package / "image_analysis.json"
        if analysis_path.is_file():
            image_analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            image_ids = {item["id"] for item in image_analysis.get("anchors", []) if isinstance(item, dict) and item.get("id")}
            bundle_path = package / "input_bundle.json"
            if bundle_path.is_file():
                bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
                original = (package / bundle.get("original_image_path", "")).resolve()
                if not original.is_relative_to(package.resolve()) or not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != bundle.get("source_image_sha256"):
                    errors.append("original_image: missing, changed or outside the accepted package")
                if canonical_analysis_sha256(image_analysis) != bundle.get("image_analysis_sha256"):
                    errors.append("image_analysis: differs from the accepted visual review")
                dimensions = bundle.get("source_image_dimensions", {})
                errors += ["image_analysis: " + issue["code"] for issue in validate_visual_analysis(image_analysis,
                    expected_image_sha256=bundle.get("source_image_sha256", ""),
                    expected_width=dimensions.get("width", 0), expected_height=dimensions.get("height", 0))]
                if source.get("canonical_body_sha256") != bundle.get("canonical_body_sha256"):
                    errors.append("image_analysis: canonical source differs from the accepted input")
        else:
            errors.append("image_analysis.json: complete original-image review required before planning")
    knowledge = kp.get("knowledge")
    if not isinstance(knowledge, list) or not knowledge:
        return errors + ["knowledge: nonempty array required"]
    seen = set()
    for index, item in enumerate(knowledge):
        prefix = f"knowledge[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix}: expected object")
            continue
        for key in ("kp_id", "claim", "evidence_anchor"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                errors.append(f"{prefix}.{key}: nonempty text required")
        identity = item.get("kp_id")
        if isinstance(identity, str):
            if not re.fullmatch(r"KP-[A-Za-z0-9_-]+", identity):
                errors.append(f"{prefix}.kp_id: must be a safe KP- identifier")
            if identity in seen:
                errors.append(f"{prefix}.kp_id: duplicate")
            seen.add(identity)
        if item.get("layer") not in LAYERS:
            errors.append(f"{prefix}.layer: unsupported value")
        if item.get("confidence") not in ("高", "中", "低"):
            errors.append(f"{prefix}.confidence: unsupported value")
        concept = item.get("reusable_concept")
        if kp.get("source_catalog_version") in SOURCE_CATALOG_VERSIONS and concept is None:
            errors.append(f"{prefix}.reusable_concept: current analysis requires a same-round decision")
        if concept is not None:
            errors.extend(schema_errors(concept, reusable_concept_schema(), f"{prefix}.reusable_concept"))
            if isinstance(concept, dict) and concept.get("status") == "reusable" and not str(concept.get("name") or "").strip():
                errors.append(f"{prefix}.reusable_concept.name: reusable idea needs an analysed name")
        if source.get("source_input_kind") == "user_provided_image":
            references = set(re.findall(r"\bIMG-[A-Za-z0-9_-]+\b", str(item.get("evidence_anchor") or "")))
            if not references or not references <= image_ids:
                errors.append(f"{prefix}.evidence_anchor: use existing IMG-* visual anchors")
    application = kp.get("application")
    if not isinstance(application, dict):
        errors.append("application: expected object")
    else:
        if application.get("personal_relevance", "未知") != "未知" and (
            not application.get("context_anchors") or not application.get("personal_relevance_reason")):
            errors.append("application.personal_relevance: needs reviewed context anchors and a reason")
        if application.get("idea_candidate_status") == "no_reusable_idea" and any(
            item.get("reusable_concept", {}).get("status") == "reusable" for item in knowledge if isinstance(item, dict)):
            errors.append("application.idea_candidate_status: contradicts reusable source concepts")
        for key in ("adoption_reason", "compounding_next_action", "taste_reason"):
            if not isinstance(application.get(key), str) or not application[key].strip():
                errors.append(f"application.{key}: nonempty text required")
        for key, allowed in (("knowledge_use", VALID_KNOWLEDGE_USE), ("compounding_insight_status", VALID_COMPOUNDING_STATUSES),
                             ("adoption_status", VALID_ADOPTION_STATUSES)):
            if application.get(key) not in allowed:
                errors.append(f"application.{key}: unsupported value {application.get(key)!r}")
        if strict:
            judgments = application.get("compounding_judgments", {})
            established = {key for key, value in judgments.items() if value.get("decision") == "成立"}
            pending = any(value.get("decision") == "需要更多脈絡" for value in judgments.values())
            declared = application.get("compounding_insight_status")
            if established != set(application.get("compounding_insight_types", [])):
                errors.append("application.compounding_judgments: established categories must match declared types")
            expected = "has_insight" if established else "needs_context" if pending else "no_compounding_insight"
            if declared != expected:
                errors.append("application.compounding_judgments: category decisions disagree with declared status")
            if declared == "has_insight" and not application.get("personalization_links"):
                errors.append("application.personalization_links: established insight requires a concrete connection")
            if declared == "no_compounding_insight" and application.get("personalization_links"):
                errors.append("application.personalization_links: no insight must not declare established connections")
            anchors = application.get("context_anchors", [])
            if application.get("compounding_insight_status") == "has_insight" and not anchors:
                errors.append("application.context_anchors: insight needs a real existing note and quote")
            for index, anchor in enumerate(anchors):
                path = (root / anchor["path"]).resolve() if root else None
                if not path or not path.is_relative_to((root / os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")).resolve()) or not path.is_file():
                    errors.append(f"application.context_anchors[{index}]: missing or outside Vault")
                elif anchor["quote"] not in path.read_text(encoding="utf-8"):
                    errors.append(f"application.context_anchors[{index}]: quote not in note")
    for index, relation in enumerate(kp.get("relations", [])):
        if not isinstance(relation, dict) or relation.get("source_kp") not in seen or relation.get("target_kp") not in seen:
            errors.append(f"relations[{index}]: endpoints must be knowledge IDs in this source")
    prior = kp.get("prior_knowledge")
    for index, link in enumerate(prior.get("cross_links", []) if isinstance(prior, dict) else []):
        if not isinstance(link, dict) or link.get("source_kp") not in seen:
            errors.append(f"prior_knowledge.cross_links[{index}]: source must be a knowledge ID in this source")
    if ground_quotes and source.get("source_input_kind") != "user_provided_image":
        points = [
            KnowledgePoint(
                str(item.get("kp_id") or f"KP-preflight-{index}"),
                str(item.get("claim") or ""),
                str(item.get("layer") or ""),
                str(item.get("confidence") or ""),
                str(item.get("evidence_anchor") or ""),
            )
            for index, item in enumerate(knowledge)
            if isinstance(item, dict)
        ]
        relations = [
            KnowledgeRelation(
                str(item.get("source_kp") or ""),
                str(item.get("target_kp") or ""),
                str(item.get("relation") or ""),
                str(item.get("evidence_anchor") or ""),
                str(item.get("confidence") or ""),
            )
            for item in kp.get("relations", [])
            if isinstance(item, dict)
        ]
        for point in points:
            try:
                validate_source_anchored_knowledge([point], [], body)
            except AdapterStop as exc:
                errors.append(str(exc))
        for relation in relations:
            try:
                validate_source_anchored_knowledge([], [relation], body)
            except AdapterStop as exc:
                errors.append(str(exc))
        for index, row in enumerate(kp.get("analysis", {}).get("source_claims", [])):
            if not row.get("evidence_anchor") or not canonical_body_contains_anchor(body, str(row["evidence_anchor"])):
                errors.append(f"analysis.source_claims[{index}].evidence_anchor: quote not in complete source")
    return errors
