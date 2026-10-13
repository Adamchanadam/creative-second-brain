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
from pathlib import Path
from typing import Any

try:
    from tools.case_evidence_contract import ASSET_MODE_CASE, ASSET_MODE_FORMAL, package_manifest
    from tools.infographic_compiler import INFOGRAPHIC_GRAPH_TYPE_CONTRACTS, reader_infographic_title
    from tools.obsidian_vault_adapter import VALID_SOURCE_TYPES, VALID_KNOWLEDGE_USE, VALID_COMPOUNDING_STATUSES, VALID_COMPOUNDING_TYPES
    from tools.image_source_contract import canonical_analysis_sha256, validate_visual_analysis
except ModuleNotFoundError:
    from case_evidence_contract import ASSET_MODE_CASE, ASSET_MODE_FORMAL, package_manifest
    from infographic_compiler import INFOGRAPHIC_GRAPH_TYPE_CONTRACTS, reader_infographic_title
    from obsidian_vault_adapter import VALID_SOURCE_TYPES, VALID_KNOWLEDGE_USE, VALID_COMPOUNDING_STATUSES, VALID_COMPOUNDING_TYPES
    from image_source_contract import canonical_analysis_sha256, validate_visual_analysis

VERSION = "ingest_semantic_plan_v1"


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
                    "citation_evidence": citation}, ["source_label", "source_type", "primary_work_status"])
    claim = obj({"id": TEXT, "claim": TEXT, "evidence_anchor": TEXT, "confidence": CONFIDENCE})
    unit = obj({"id": TEXT, "text": TEXT, "source_evidence": TEXT,
                "unit_types": array(TEXT, 1), "value_axes": TEXTS,
                "dispositions": array(text_schema(values=["source_claim", "knowledge_point", "summary", "infographic", "application", "relation", "omitted"]), 1),
                "infographic_text": TEXTS, "omission_reason": TEXT},
               ["id", "text", "source_evidence", "unit_types", "dispositions"])
    knowledge = obj({"kp_id": TEXT, "title": TEXT, "claim": TEXT,
                     "layer": text_schema(values=LAYERS), "confidence": CONFIDENCE, "evidence_anchor": TEXT})
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
        "source_metadata": metadata,
        "analysis": obj({"summary": array(TEXT, 1), "source_claims": array(claim, 1)}),
        "coverage_units": array(unit, 1), "knowledge": array(knowledge, 1),
        "relations": array(relation), "application": application,
        "asset_mode": text_schema(values=[ASSET_MODE_FORMAL, ASSET_MODE_CASE]),
        "case_evidence": case_evidence,
        "infographics": array(page),
    }
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **obj(
        properties,
        ["schema_version", "input_fingerprint", "source_metadata", "analysis", "coverage_units", "knowledge", "relations", "application", "infographics"],
    )}


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


def input_fingerprint(package: Path) -> str:
    names = (
        "input_bundle.json",
        "canonical_source.md",
        "source_capture_metadata.json",
        "image_analysis.json",
        "case_evidence/manifest.json",
    )
    payload = {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
               for name in names if (package / name).is_file()}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def build_kp(plan: dict, package: Path, *, root: Path) -> dict:
    errors = schema_errors(plan, plan_schema())
    if errors:
        raise ValueError("\n".join(errors))
    if plan["input_fingerprint"] != input_fingerprint(package):
        raise ValueError("input_fingerprint: accepted source changed; reread the current work order")
    bundle = json.loads((package / "input_bundle.json").read_text(encoding="utf-8"))
    asset_mode = plan.get("asset_mode", ASSET_MODE_FORMAL)
    if asset_mode not in {ASSET_MODE_FORMAL, ASSET_MODE_CASE}:
        raise ValueError("asset_mode: unsupported value")
    accepted_mode = bundle.get("asset_mode", ASSET_MODE_FORMAL)
    if accepted_mode != asset_mode:
        raise ValueError("asset_mode: differs from accepted input bundle")
    if asset_mode == ASSET_MODE_FORMAL:
        if "case_evidence" in plan or not plan.get("infographics"):
            raise ValueError("formal_infographic requires a nonempty infographic plan and no case_evidence")
    else:
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
    source_keys = ("source_id", "source_input_kind", "source_identity", "title", "title_source", "capture_date",
                   "canonical_body_sha256", "source_content_sha256", "source_image_sha256", "source_image_mime",
                   "source_image_dimensions", "source_url", "normalized_url", "source_url_hash", "source_profile", "anchors",
                   "asset_mode", "case_evidence_manifest_path", "case_evidence_manifest_sha256", "case_evidence_source_manifest_sha256")
    source = {key: copy.deepcopy(bundle[key]) for key in source_keys if key in bundle}
    source.update(copy.deepcopy(plan["source_metadata"]))
    source["source_retention_mode"] = source["retention_mode"] = (
        "original_image_required" if source["source_input_kind"] == "user_provided_image" else "full_body_required")
    source["readability_status"] = {"url": "public_ok", "user_provided_text": "user_text_complete", "user_provided_image": "user_image_complete"}[source["source_input_kind"]]
    kp = {"schema_version": "knowledge_package_v2", "semantic_plan_version": VERSION,
          "job_id": bundle["job_id"], "source": source,
          **{key: copy.deepcopy(plan[key]) for key in ("analysis", "coverage_units", "knowledge", "relations", "application", "infographics")},
          "projection": {"source_identity": source["source_identity"], "asset_mode": asset_mode}}
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
        page["content_map"] = [{"coverage_id": key, "source_evidence": units.get(key, {}).get("source_evidence", ""),
                                "visible_text": units.get(key, {}).get("infographic_text", [])} for key in page["coverage_ids"]]
        page["visible_text"] = list(dict.fromkeys(line for row in page["content_map"] for line in row["visible_text"]))
        page["source_anchors"] = list(dict.fromkeys(row["source_evidence"] for row in page["content_map"]))
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
    if len(kp["infographics"]) > 1:
        first = kp["infographics"][0]
        first["collection_role"] = "orientation"
        first["collection_roadmap"] = [{"page_number": i, "page_id": page["page_id"], "page_title": page["page_focus"], "page_role": page["reader_takeaway"]}
                                       for i, page in enumerate(kp["infographics"], 1)]
        for key in ("collection_thesis", "collection_importance", "collection_roadmap_summary"):
            if first.get(key) and first[key] not in first["visible_text"]:
                first["visible_text"].append(first[key])
    errors = content_errors(kp, package, root=root, strict=True)
    if errors:
        raise ValueError("\n".join(errors))
    return kp


def content_errors(kp: dict, package: Path, *, root: Path | None = None, strict=False) -> list[str]:
    """Cheap semantic-shape/evidence checks, before any expensive image action."""
    errors = []
    strict = strict or bool(kp.get("semantic_plan_version"))
    if root is None and strict:
        root = next((parent.parent for parent in package.parents if parent.name == "outputs"), None)
    ground_quotes = strict or (package / "input_bundle.json").is_file()
    body_path = package / "canonical_source.md"
    body = body_path.read_text(encoding="utf-8") if body_path.is_file() else ""
    source = kp.get("source")
    if not isinstance(source, dict):
        return ["source: expected object"]
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
        return ["knowledge: nonempty array required"]
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
        if ground_quotes and source.get("source_input_kind") != "user_provided_image" and str(item.get("evidence_anchor") or "") not in body:
            errors.append(f"{prefix}.evidence_anchor: quote not in complete source")
        if source.get("source_input_kind") == "user_provided_image":
            references = set(re.findall(r"\bIMG-[A-Za-z0-9_-]+\b", str(item.get("evidence_anchor") or "")))
            if not references or not references <= image_ids:
                errors.append(f"{prefix}.evidence_anchor: use existing IMG-* visual anchors")
    application = kp.get("application")
    if not isinstance(application, dict):
        errors.append("application: expected object")
    else:
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
    if ground_quotes and source.get("source_input_kind") != "user_provided_image":
        for label, rows, key in (("analysis.source_claims", kp.get("analysis", {}).get("source_claims", []), "evidence_anchor"),
                                 ("relations", kp.get("relations", []), "evidence_anchor")):
            for index, row in enumerate(rows):
                if not row.get(key) or row[key] not in body:
                    errors.append(f"{label}[{index}].{key}: quote not in complete source")
    return errors
