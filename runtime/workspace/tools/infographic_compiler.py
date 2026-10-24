"""Single compiler boundary for formal infographic plans.

The source analyzer decides the source-grounded coverage units. This module
turns the reviewed decision into one locked page contract shared by image
generation, receipt validation, and Vault projection.
"""

from __future__ import annotations

import hashlib
import json
import copy
import re
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from tools.infographic_prompt_contract import (
        INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
        render_gpt_image_2_prompt,
        visible_text_quality_errors,
        contextual_heading_labels,
    )
    from tools.reader_surface_guard import reader_source_attribution_label
except ModuleNotFoundError:
    from infographic_prompt_contract import (  # type: ignore
        INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
        render_gpt_image_2_prompt,
        visible_text_quality_errors,
        contextual_heading_labels,
    )
    from reader_surface_guard import reader_source_attribution_label  # type: ignore


INFOGRAPHIC_PLAN_SCHEMA_VERSION = "infographic_compiled_page_v2"
AUTHORITATIVE_SOURCE_TITLE_SOURCES = {"source_declared", "user_supplied"}
EDITORIAL_SERIES_TITLE_PREFIX = re.compile(r"^\s*本週精選\s*[：:]\s*")
PRIMARY_WORK_NON_WORK_TITLES = {
    "《Financial Times》",
    "《FT》",
}
PRIMARY_WORK_RELATION_PATTERN = re.compile(
    r"(?:精讀|解讀|導讀|讀書筆記|書摘|核心理念|"
    r"(?:本|這)(?:篇|段|支|部)?(?:文章|影片|內容).{0,24}(?:介紹|整理|解說)|"
    r"(?:作者|所著|著作).{0,12})"
)
COMPARISON_RELATION_PATTERN = re.compile(
    r"(?:比較|對照|並列|引用|提及|例如|包括).{0,36}"
)


def reader_footer_source_label(source: dict[str, Any]) -> str:
    """Derive a public citation label without exposing capture or review state."""

    source_kind = str(source.get("source_input_kind") or "").strip()
    fallback = "使用者提供文字" if source_kind == "user_provided_text" else "公開來源"
    citation = source.get("citation_evidence")
    if source_kind == "user_provided_text" and isinstance(citation, dict):
        original_url = str(citation.get("original_source_url") or "").strip()
        try:
            parsed_url = urlsplit(original_url)
        except ValueError:
            parsed_url = None
        cited_title = str(citation.get("title") or "").strip()
        publication = str(citation.get("publication") or "").strip()
        authors = reader_source_attribution_label(
            {"source_label": str(citation.get("authors") or "").strip()},
            fallback="",
        )
        if (
            citation.get("original_source_url_status") == "verified"
            and parsed_url is not None
            and parsed_url.scheme.casefold() == "https"
            and parsed_url.netloc
            and cited_title
        ):
            label = f"原文參照《{cited_title}》"
            if publication:
                label += f"／{publication}"
            if authors:
                label += f"\n原文作者：{authors}"
            safe_label = reader_source_attribution_label(
                {"source_label": label},
                fallback="",
            )
            if safe_label:
                return safe_label
    return reader_source_attribution_label(source, fallback=fallback)


def reader_infographic_title(value: object) -> str:
    """Drop the known editorial series label while preserving the source title."""

    title = str(value or "").strip()
    return EDITORIAL_SERIES_TITLE_PREFIX.sub("", title, count=1).strip()


INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES: dict[str, tuple[str, ...]] = {
    "事實真源": ("原文重點卡", "限制卡", "引用位置圖"),
    "工作流程": ("原文流程步驟圖", "決策路徑圖"),
    "學習材料": ("核心定義卡", "概念拆解圖", "結構層級圖"),
    "判斷材料": ("比較對立圖", "風險盲點圖", "證據階梯圖"),
    "品味材料": ("原文品味線索圖", "作品脈絡圖", "選擇特徵圖"),
    "行動材料": ("原文行動步驟圖", "驗證問題卡"),
    "噪音暫存": ("最小摘要卡",),
}

# This executable registry mirrors docs/資訊圖生成規格.md section 6.
# Each graph type owns a structural kind and the fields that must exist before
# the prompt can be compiled. Fields ending in "_text" are reader-visible and
# therefore must also exist in the page visible_text whitelist.
INFOGRAPHIC_GRAPH_TYPE_CONTRACTS: dict[str, dict[str, Any]] = {
    "原文重點卡": {
        "content_use": "事實真源",
        "kind": "fact_cards",
        "collections": {
            "items": {"min_items": 1, "required_fields": ("heading_text", "body_text")},
        },
    },
    "限制卡": {
        "content_use": "事實真源",
        "kind": "constraint_cards",
        "collections": {
            "items": {"min_items": 1, "required_fields": ("rule_text", "condition_text")},
        },
    },
    "引用位置圖": {
        "content_use": "事實真源",
        "kind": "citation_map",
        "collections": {
            "items": {"min_items": 1, "required_fields": ("point_text", "location_text")},
        },
    },
    "原文流程步驟圖": {
        "content_use": "工作流程",
        "kind": "process_steps",
        "collections": {
            "steps": {"min_items": 2, "required_fields": ("step_text",)},
        },
    },
    "決策路徑圖": {
        "content_use": "工作流程",
        "kind": "decision_path",
        "collections": {
            "decisions": {
                "min_items": 1,
                "required_fields": ("question_text", "yes_text", "no_text"),
            },
        },
    },
    "核心定義卡": {
        "content_use": "學習材料",
        "kind": "concept_definition",
        "required_text_fields": ("definition_text",),
        "collections": {
            "parts": {"min_items": 2, "required_fields": ("part_text",)},
        },
    },
    "概念拆解圖": {
        "content_use": "學習材料",
        "kind": "concept_parts",
        "required_text_fields": ("center_text",),
        "collections": {
            "parts": {"min_items": 2, "required_fields": ("part_text", "relation")},
        },
    },
    "結構層級圖": {
        "content_use": "學習材料",
        "kind": "hierarchy_levels",
        "collections": {
            "levels": {
                "min_items": 2,
                "required_fields": ("level_text", "detail_text"),
            },
        },
    },
    "比較對立圖": {
        "content_use": "判斷材料",
        "kind": "comparison_rows",
        "required_text_fields": ("left_label", "right_label"),
        "collections": {
            "rows": {
                "min_items": 2,
                "required_fields": ("basis", "left_text", "right_text"),
            },
        },
    },
    "風險盲點圖": {
        "content_use": "判斷材料",
        "kind": "risk_map",
        "collections": {
            "items": {
                "min_items": 1,
                "required_fields": ("risk_text", "consequence_text"),
            },
        },
    },
    "證據階梯圖": {
        "content_use": "判斷材料",
        "kind": "evidence_ladder",
        "collections": {
            "levels": {
                "min_items": 2,
                "required_fields": ("evidence_text", "meaning_text"),
            },
        },
    },
    "原文品味線索圖": {
        "content_use": "品味材料",
        "kind": "taste_signals",
        "collections": {
            "signals": {
                "min_items": 2,
                "required_fields": ("signal_text", "meaning_text"),
            },
        },
    },
    "作品脈絡圖": {
        "content_use": "品味材料",
        "kind": "work_context",
        "collections": {
            "nodes": {"min_items": 2, "required_fields": ("id", "node_text")},
            "links": {"min_items": 1, "required_fields": ("from", "to", "relation")},
        },
    },
    "選擇特徵圖": {
        "content_use": "品味材料",
        "kind": "choice_features",
        "collections": {
            "rows": {
                "min_items": 2,
                "required_fields": ("feature_text", "choice_text", "meaning_text"),
            },
        },
    },
    "原文行動步驟圖": {
        "content_use": "行動材料",
        "kind": "action_steps",
        "collections": {
            "steps": {
                "min_items": 2,
                "required_fields": ("action_text", "check_text"),
            },
        },
    },
    "驗證問題卡": {
        "content_use": "行動材料",
        "kind": "verification_questions",
        "collections": {
            "questions": {
                "min_items": 1,
                "required_fields": ("question_text", "check_text"),
            },
        },
    },
    "最小摘要卡": {
        "content_use": "噪音暫存",
        "kind": "minimal_summary",
        "formal_generation_allowed": False,
        "collections": {
            "items": {"min_items": 1, "required_fields": ("text",)},
        },
    },
}


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _structure_visible_text(value: Any, *, key: str = "") -> list[str]:
    visible: list[str] = []
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if child_key.endswith("_text") and isinstance(child_value, str):
                text = child_value.strip()
                if text:
                    visible.append(text)
            else:
                visible.extend(_structure_visible_text(child_value, key=child_key))
    elif isinstance(value, list):
        for item in value:
            visible.extend(_structure_visible_text(item, key=key))
    return visible


def validate_visual_structure(infographic: dict[str, Any]) -> list[str]:
    """Enforce the content-use, graph-type, and graph-specific structure gate."""

    content_use = str(infographic.get("content_use") or "").strip()
    graph_type = str(infographic.get("graph_type") or "").strip()
    structure = infographic.get("visual_structure")
    visible_text = {
        str(item).strip()
        for item in infographic.get("visible_text") or []
        if str(item).strip()
    }
    errors: list[str] = []

    if content_use not in INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES:
        errors.append("missing or unsupported infographic.content_use")
    if graph_type not in INFOGRAPHIC_GRAPH_TYPE_CONTRACTS:
        errors.append("missing or unsupported infographic.graph_type")
        return errors
    if (
        content_use in INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES
        and graph_type not in INFOGRAPHIC_CONTENT_USE_GRAPH_TYPES[content_use]
    ):
        errors.append(
            f"infographic.graph_type {graph_type} is not allowed for content_use {content_use}"
        )

    contract = INFOGRAPHIC_GRAPH_TYPE_CONTRACTS[graph_type]
    if contract.get("formal_generation_allowed") is False:
        errors.append(f"infographic.graph_type {graph_type} cannot enter formal image generation")
    if not isinstance(structure, dict):
        errors.append(f"infographic.graph_type {graph_type} requires visual_structure")
        return errors
    if structure.get("kind") != contract["kind"]:
        errors.append(
            f"infographic.graph_type {graph_type} requires visual_structure.kind={contract['kind']}"
        )

    for field in contract.get("required_text_fields", ()):
        if not str(structure.get(field) or "").strip():
            errors.append(f"infographic.graph_type {graph_type} missing visual_structure.{field}")

    for collection_name, collection_contract in contract.get("collections", {}).items():
        rows = structure.get(collection_name)
        minimum = int(collection_contract.get("min_items", 1))
        if not isinstance(rows, list):
            errors.append(
                f"infographic.graph_type {graph_type} requires at least "
                f"{minimum} visual_structure.{collection_name} rows"
            )
            continue
        if len(rows) < minimum:
            errors.append(
                f"infographic.graph_type {graph_type} requires at least "
                f"{minimum} visual_structure.{collection_name} rows"
            )
        for row_index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                errors.append(
                    f"infographic.graph_type {graph_type} "
                    f"{collection_name}[{row_index}] must be an object"
                )
                continue
            for field in collection_contract.get("required_fields", ()):
                if not str(row.get(field) or "").strip():
                    errors.append(
                        f"infographic.graph_type {graph_type} "
                        f"{collection_name}[{row_index}] missing {field}"
                    )

    for text in _structure_visible_text(structure):
        if text not in visible_text:
            errors.append(
                f"infographic visual_structure text is not in visible_text: {text}"
            )

    if structure.get("kind") == "comparison_rows":
        for row_index, row in enumerate(structure.get("rows") or [], start=1):
            if not isinstance(row, dict):
                continue
            left = str(row.get("left_text") or "").strip()
            right = str(row.get("right_text") or "").strip()
            if left and right and left == right:
                errors.append(
                    f"infographic comparison row {row_index} has identical left_text and right_text"
                )

    if structure.get("kind") == "work_context":
        node_ids = {
            str(item.get("id") or "").strip()
            for item in structure.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        }
        for link_index, link in enumerate(structure.get("links") or [], start=1):
            if not isinstance(link, dict):
                continue
            if (
                str(link.get("from") or "").strip() not in node_ids
                or str(link.get("to") or "").strip() not in node_ids
            ):
                errors.append(
                    f"infographic work_context link {link_index} references an unknown node"
                )
    return errors


def visual_structure_instruction(
    graph_type: str,
    structure: dict[str, Any],
) -> str:
    """Render verified structure as placement instructions, not visible copy."""

    kind = str(structure.get("kind") or "")
    if kind == "comparison_rows":
        rows = " ".join(
            f"把「{row['left_text']}」與「{row['right_text']}」"
            "放在同一水平列並以箭咀連接。"
            for row in structure["rows"]
        )
        return (
            f"採用固定雙欄矩陣；左欄標題使用「{structure['left_label']}」，"
            f"右欄標題使用「{structure['right_label']}」。{rows}"
            "不得顯示數字、序號、列號或步驟標籤。"
        )
    if kind in {"fact_cards", "constraint_cards", "citation_map", "risk_map"}:
        collection = structure["items"]
        field_pairs = {
            "fact_cards": ("heading_text", "body_text"),
            "constraint_cards": ("rule_text", "condition_text"),
            "citation_map": ("point_text", "location_text"),
            "risk_map": ("risk_text", "consequence_text"),
        }
        first_field, second_field = field_pairs[kind]
        items = " ".join(
            f"每組卡片先顯示「{item[first_field]}」，下一層顯示「{item[second_field]}」。"
            for item in collection
        )
        return (
            f"使用手繪分區與非對稱平衡，保持一致的文字層級；分區按內容調整，避免完美等寬卡片。{items}"
            "不得顯示數字、序號、卡號或步驟標籤。"
        )
    if kind == "process_steps":
        steps = " ".join(
            f"沿箭咀依次顯示「{step['step_text']}」。"
            for step in structure["steps"]
        )
        content_density_rule = str(structure.get("content_density_rule") or "").strip()
        density_instruction = (
            "每一個主要分區都必須同時承載可見知識文字與服務理解的語義圖解；"
            "不得以大面積空白、純裝飾人物或無內容圖示代替一個內容分區。"
            if not content_density_rule
            else content_density_rule
        )
        return (
            "採用由上而下的流程；只用位置、箭咀或色塊表達先後。"
            f"{steps}{density_instruction}不得顯示數字、序號或步驟標籤。"
        )
    if kind == "decision_path":
        decisions = " ".join(
            f"以「{item['question_text']}」作判斷點；"
            f"兩條分支分別通往「{item['yes_text']}」與「{item['no_text']}」。"
            for item in structure["decisions"]
        )
        return f"採用清楚分支的決策路徑。{decisions}"
    if kind in {"concept_definition", "concept_parts"}:
        center_field = "definition_text" if kind == "concept_definition" else "center_text"
        parts = " ".join(
            f"以連線接到「{item['part_text']}」。"
            for item in structure["parts"]
        )
        return f"把「{structure[center_field]}」放在視覺中心，{parts}"
    if kind == "hierarchy_levels":
        levels = " ".join(
            f"上層顯示「{item['level_text']}」，鄰近說明「{item['detail_text']}」。"
            for item in structure["levels"]
        )
        return f"採用由上而下的結構層級。{levels}不得顯示數字、序號或層級標籤。"
    if kind == "evidence_ladder":
        levels = " ".join(
            f"並列「{item['evidence_text']}」與「{item['meaning_text']}」。"
            for item in structure["levels"]
        )
        return f"採用由下而上的證據階梯。{levels}不得顯示數字、序號或層級標籤。"
    if kind == "taste_signals":
        signals = " ".join(
            f"把「{item['signal_text']}」連到「{item['meaning_text']}」。"
            for item in structure["signals"]
        )
        return f"採用線索到意義的成組關係。{signals}"
    if kind == "work_context":
        labels = {
            str(item["id"]): str(item["node_text"])
            for item in structure["nodes"]
        }
        links = " ".join(
            f"連接「{labels[item['from']]}」與「{labels[item['to']]}」。"
            for item in structure["links"]
        )
        return f"採用作品脈絡關係圖，節點大小一致。{links}"
    if kind == "choice_features":
        rows = " ".join(
            f"同一水平列依次顯示「{item['feature_text']}」、"
            f"「{item['choice_text']}」與「{item['meaning_text']}」。"
            for item in structure["rows"]
        )
        return f"採用固定欄寬的選擇特徵矩陣。{rows}不得顯示數字、序號或列號。"
    if kind == "action_steps":
        steps = " ".join(
            f"依次顯示行動句「{item['action_text']}」，"
            f"下方顯示檢查點「{item['check_text']}」。"
            for item in structure["steps"]
        )
        return (
            "採用由上而下的行動步驟；只用位置、線條或色塊區分順序，"
            "不得顯示數字、序號或步驟標籤。"
            f"{steps}"
        )
    if kind == "verification_questions":
        questions = " ".join(
            f"問題「{item['question_text']}」下方顯示核對點「{item['check_text']}」。"
            for item in structure["questions"]
        )
        return f"採用逐題核對卡。{questions}"
    raise ValueError(f"unsupported infographic visual structure for {graph_type}")


def canonical_infographic_asset_filename(
    source_id: str,
    slug: str,
    page_index: int,
    page_count: int,
) -> str:
    """Return the stable Vault-facing filename used before the v2 drift."""

    if page_index < 1 or page_count < 1 or page_index > page_count:
        raise ValueError("invalid infographic page index/count")
    safe_source_id = source_id.strip().upper()
    if not re.fullmatch(r"KS-VS-[A-Z0-9-]+", safe_source_id):
        raise ValueError("invalid infographic source id")
    safe_slug = re.sub(r"[^a-z0-9]+", "_", slug.casefold()).strip("_")
    if not safe_slug:
        raise ValueError("infographic slug is empty after normalization")
    suffix = "_source.png" if page_count == 1 else f"_source_{page_index:02d}.png"
    return f"{safe_source_id}_{safe_slug}{suffix}"


def validate_primary_work_metadata(
    source: dict[str, Any],
    canonical_body: str,
) -> list[str]:
    """Validate the explicit primary-work classification for a new plan."""

    status = str(source.get("primary_work_status") or "").strip()
    title = str(source.get("primary_work_title") or "").strip()
    evidence = str(source.get("primary_work_evidence") or "").strip()
    candidates = detect_primary_work_candidates(canonical_body)
    if status not in {"primary", "none"}:
        return ["missing or unsupported source.primary_work_status"]
    if status == "none":
        errors = []
        if title or evidence:
            errors.append("source with no primary work must not retain primary work metadata")
        if len(candidates) == 1:
            errors.append("canonical source identifies one likely primary work but metadata says none")
        return errors
    errors = []
    if not title or title not in canonical_body:
        errors.append("source.primary_work_title is not supported by canonical source")
    if not evidence or evidence not in canonical_body:
        errors.append("source.primary_work_evidence is not supported by canonical source")
    elif title not in evidence or not PRIMARY_WORK_RELATION_PATTERN.search(evidence):
        errors.append("source.primary_work_evidence does not state a primary-work relationship")
    if len(candidates) > 1 and title not in candidates:
        errors.append("source.primary_work_title does not resolve the detected work candidates")
    if title and candidates and title not in candidates:
        errors.append("source.primary_work_title conflicts with canonical primary-work evidence")
    return errors


def materialize_infographic_page_bindings(
    coverage_units: list[Any],
    infographics: list[Any],
) -> tuple[list[Any], list[dict[str, str]]]:
    """Derive page copy bindings from reviewed coverage without hiding authored drift.

    ``content_map``, ``visible_text`` and ``source_anchors`` are projections of
    selected coverage, rather than a second AI-authored rendition of the same
    facts. A nonempty legacy/manual value is still accepted when it is exactly
    the projection, but a differing value is reported before this materializer
    can replace it. The returned pages are always the program-derived form so
    every caller hashes the same reader page after accepting the draft.
    """
    pages = copy.deepcopy(infographics)
    units = {
        str(unit.get("id") or "").strip(): unit
        for unit in coverage_units
        if isinstance(unit, dict) and str(unit.get("id") or "").strip()
    }
    issues: list[dict[str, str]] = []

    def lines(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def dedupe(values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))

    def authored(value: Any) -> bool:
        return value not in (None, "", [], {})

    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("page_id") or index)
        mappings = []
        for coverage_id in lines(page.get("coverage_ids")):
            unit = units.get(coverage_id, {})
            mappings.append({
                "coverage_id": coverage_id,
                "source_evidence": str(unit.get("source_evidence") or "").strip(),
                "visible_text": lines(unit.get("infographic_text")),
            })
        visible = dedupe([
            line
            for mapping in mappings
            for line in mapping["visible_text"]
        ])
        anchors = dedupe([mapping["source_evidence"] for mapping in mappings])
        expected = {
            "content_map": mappings,
            "visible_text": visible,
            "source_anchors": anchors,
        }
        for field, value in expected.items():
            if authored(page.get(field)) and page.get(field) != value:
                issues.append({
                    "page_id": page_id,
                    "field": field,
                    "message": (
                        f"infographic page {page_id} authored {field} differs from "
                        "the deterministic reviewed-coverage binding"
                    ),
                })
            page[field] = value

    if len(pages) > 1 and isinstance(pages[0], dict):
        first = pages[0]
        # The roadmap's title and role are independently authored reader
        # summaries.  Only provide a missing compatibility default; replacing
        # a valid authored roadmap with focus/takeaway narrows the contract.
        if not authored(first.get("collection_roadmap")):
            first["collection_roadmap"] = [
                {
                    "page_number": index,
                    "page_id": str(page.get("page_id") or ""),
                    "page_title": str(page.get("page_focus") or ""),
                    "page_role": str(page.get("reader_takeaway") or ""),
                }
                for index, page in enumerate(pages, start=1)
                if isinstance(page, dict)
            ]
        if not first.get("collection_role"):
            first["collection_role"] = "orientation"
    return pages, issues


def detect_primary_work_candidates(canonical_body: str) -> list[str]:
    """Detect works explicitly framed as the source's main subject."""

    candidates: list[str] = []
    for match in re.finditer(r"《[^》\r\n]{1,120}》", canonical_body):
        title = match.group(0)
        if title in PRIMARY_WORK_NON_WORK_TITLES:
            continue
        start = max(0, match.start() - 90)
        end = min(len(canonical_body), match.end() + 90)
        context = canonical_body[start:end]
        before = canonical_body[start:match.start()]
        if (
            PRIMARY_WORK_RELATION_PATTERN.search(context)
            and not COMPARISON_RELATION_PATTERN.search(before[-45:])
        ):
            candidates.append(title)
    return list(dict.fromkeys(candidates))


def _coverage_dispositions(unit: dict[str, Any]) -> set[str]:
    raw = unit.get("dispositions", unit.get("disposition", []))
    if isinstance(raw, str):
        return {raw} if raw.strip() else set()
    if isinstance(raw, list):
        return {str(item) for item in raw if str(item).strip()}
    return set()


def _source_anchor_is_canonical(canonical_body: str, anchor: str) -> bool:
    """Reuse the source adapter's fragment-aware canonical anchor check.

    Importing this at module load would create a cycle because the adapter
    imports the canonical infographic filename from this module.  The
    validator is needed only while preflighting a reviewed page, after both
    modules have loaded.
    """

    try:
        from tools.obsidian_vault_adapter import canonical_body_contains_anchor
    except ModuleNotFoundError:
        from obsidian_vault_adapter import canonical_body_contains_anchor  # type: ignore
    return canonical_body_contains_anchor(canonical_body, anchor)


def validate_content_map(
    coverage_units: list[dict[str, Any]],
    infographics: list[dict[str, Any]],
    canonical_body: str,
) -> list[str]:
    """Require infographic-bound coverage units to own visible reader text."""

    units = {
        str(item.get("id") or "").strip(): item
        for item in coverage_units
        if isinstance(item, dict) and str(item.get("id") or "").strip()
        and "infographic" in _coverage_dispositions(item)
    }
    required_ids = set(units)
    mapped_ids: set[str] = set()
    errors: list[str] = []
    for coverage_id, unit in units.items():
        evidence = str(unit.get("source_evidence") or "").strip()
        if not evidence or not _source_anchor_is_canonical(canonical_body, evidence):
            errors.append(f"coverage unit {coverage_id} lacks canonical source evidence")
        reader_text = unit.get("infographic_text")
        if isinstance(reader_text, str):
            reader_lines = [reader_text.strip()] if reader_text.strip() else []
        elif isinstance(reader_text, list):
            reader_lines = [str(item).strip() for item in reader_text if str(item).strip()]
        else:
            reader_lines = []
        if not reader_lines:
            errors.append(f"coverage unit {coverage_id} lacks reviewed infographic_text")
    for page_index, infographic in enumerate(infographics, start=1):
        if not isinstance(infographic, dict):
            continue
        content_map = infographic.get("content_map")
        if not isinstance(content_map, list) or not content_map:
            errors.append(f"infographic {page_index} missing content_map")
            continue
        visible_text = {
            str(item).strip()
            for item in infographic.get("visible_text") or []
            if str(item).strip()
        }
        for mapping in content_map:
            if not isinstance(mapping, dict):
                errors.append(f"infographic {page_index} has invalid content_map row")
                continue
            coverage_id = str(mapping.get("coverage_id") or "").strip()
            source_evidence = str(mapping.get("source_evidence") or "").strip()
            lines = [
                str(item).strip()
                for item in mapping.get("visible_text") or []
                if str(item).strip()
            ]
            if coverage_id not in required_ids:
                errors.append(f"infographic {page_index} content_map references unknown coverage unit")
            elif coverage_id in mapped_ids:
                errors.append(f"coverage unit {coverage_id} is mapped more than once")
            else:
                mapped_ids.add(coverage_id)
            if not lines or any(line not in visible_text for line in lines):
                errors.append(f"infographic {page_index} content_map is not backed by visible_text")
            unit = units.get(coverage_id)
            if isinstance(unit, dict):
                unit_evidence = str(unit.get("source_evidence") or "").strip()
                if (
                    source_evidence != unit_evidence
                    or not _source_anchor_is_canonical(canonical_body, source_evidence)
                ):
                    errors.append(
                        f"infographic {page_index} content_map source evidence mismatch for {coverage_id}"
                    )
                reader_text = unit.get("infographic_text")
                allowed_lines = (
                    [reader_text.strip()]
                    if isinstance(reader_text, str) and reader_text.strip()
                    else [
                        str(item).strip()
                        for item in reader_text or []
                        if str(item).strip()
                    ]
                    if isinstance(reader_text, list)
                    else []
                )
                if lines != allowed_lines:
                    errors.append(
                        f"infographic {page_index} content_map reader text drift for {coverage_id}"
                    )
        mapped_visible_text = {
            str(line).strip()
            for mapping in content_map
            if isinstance(mapping, dict)
            for line in mapping.get("visible_text") or []
            if str(line).strip()
        }
        for unmapped_line in sorted(visible_text - mapped_visible_text):
            errors.append(
                f"infographic {page_index} visible text has no content_map coverage: {unmapped_line}"
            )
    for missing in sorted(required_ids - mapped_ids):
        errors.append(f"coverage unit {missing} has no infographic visible-text mapping")
    return errors


def _infographic_string_list(infographic: dict[str, Any], field: str) -> list[str]:
    value = infographic.get(field)
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def validate_multi_page_orientation(infographics: list[dict[str, Any]]) -> list[str]:
    """Require multi-page sets to open with a real reader map."""

    pages = [item for item in infographics if isinstance(item, dict)]
    if len(pages) <= 1:
        return []

    errors: list[str] = []
    first = pages[0]
    if str(first.get("collection_role") or "").strip() != "orientation":
        errors.append(
            "multi-page infographic first page must declare collection_role=orientation"
        )

    first_visible_lines = _infographic_string_list(first, "visible_text")
    first_visible = set(first_visible_lines)
    for error in visible_text_quality_errors(first_visible_lines, contextual_labels=contextual_heading_labels(first)):
        errors.append("multi-page orientation first page " + error)
    for field in ("collection_thesis", "collection_importance", "collection_roadmap_summary"):
        value = str(first.get(field) or "").strip()
        if not value:
            errors.append(f"multi-page orientation first page missing {field}")
        elif value not in first_visible:
            errors.append(
                f"multi-page orientation {field} must appear in first page visible_text"
            )

    roadmap_summary = str(first.get("collection_roadmap_summary") or "").strip()
    if roadmap_summary and "->" not in roadmap_summary and "→" not in roadmap_summary:
        errors.append(
            "multi-page orientation collection_roadmap_summary must expose a reader-facing sequence"
        )

    roadmap = first.get("collection_roadmap")
    if not isinstance(roadmap, list) or len(roadmap) != len(pages):
        errors.append(
            "multi-page orientation collection_roadmap must list every infographic page"
        )
    elif roadmap:
        page_ids = [str(page.get("page_id") or "").strip() for page in pages]
        for index, row in enumerate(roadmap, start=1):
            if not isinstance(row, dict):
                errors.append(f"multi-page orientation roadmap[{index}] must be an object")
                continue
            if row.get("page_number") != index:
                errors.append(
                    f"multi-page orientation roadmap[{index}] must use page_number={index}"
                )
            page_id = str(row.get("page_id") or "").strip()
            if page_id != page_ids[index - 1]:
                errors.append(
                    f"multi-page orientation roadmap[{index}] must reference page_id {page_ids[index - 1]}"
                )
            if not str(row.get("page_title") or "").strip():
                errors.append(f"multi-page orientation roadmap[{index}] missing page_title")
            if not str(row.get("page_role") or "").strip():
                errors.append(f"multi-page orientation roadmap[{index}] missing page_role")

    for index, page in enumerate(pages, start=1):
        for field in ("reader_question", "reader_takeaway", "cross_page_handoff"):
            if not str(page.get(field) or "").strip():
                errors.append(f"multi-page infographic {index} missing {field}")
    return errors


def validate_reader_metadata(
    source: dict[str, Any],
    infographic: dict[str, Any],
    canonical_body: str,
) -> list[str]:
    """Ground free-form title, subtitle, anchors, and number labels."""

    errors: list[str] = []
    title_source = str(infographic.get("title_source") or "").strip()
    if title_source in {"source_derived", "ai_generated_from_source"}:
        title_evidence = str(infographic.get("title_evidence") or "").strip()
        if not title_evidence or title_evidence not in canonical_body:
            errors.append("derived infographic title lacks canonical title_evidence")
    subtitle = str(
        infographic.get("subtitle")
        or infographic.get("page_focus")
        or ""
    ).strip()
    if subtitle:
        subtitle_evidence = str(
            infographic.get("subtitle_evidence")
            or infographic.get("page_focus_evidence")
            or ""
        ).strip()
        if not subtitle_evidence or subtitle_evidence not in canonical_body:
            errors.append("infographic visible subtitle lacks canonical evidence")
    for anchor in infographic.get("source_anchors") or []:
        if not _source_anchor_is_canonical(canonical_body, str(anchor).strip()):
            errors.append(f"infographic source anchor is not canonical: {anchor}")
    number_labels = [
        str(item).strip()
        for item in infographic.get("number_labels") or []
        if str(item).strip()
    ]
    if number_labels:
        number_evidence = str(infographic.get("number_evidence") or "").strip()
        if not number_evidence or number_evidence not in canonical_body:
            errors.append("infographic number labels lack canonical number_evidence")
    return errors


def compile_page(
    *,
    source: dict[str, Any],
    slug: str,
    original_date: str | None,
    source_label: str,
    source_topic: str,
    lock_title: str,
    subtitle: str,
    archetype: str,
    content_use: str,
    graph_type: str,
    visual_structure: dict[str, Any],
    anchors: list[str],
    visible_lines: list[str],
    numbers: str,
    page_index: int,
    page_count: int,
    generated_on: str,
    navigation_lines: list[str] | None = None,
    prompt_contract_version: str = INFOGRAPHIC_PROMPT_CONTRACT_VERSION,
    reviewed_copy_lock: bool = False,
) -> dict[str, Any]:
    """Compile prompt and canonical asset name from one reviewed decision."""

    primary_work_status = str(source.get("primary_work_status") or "")
    primary_work_title = str(source.get("primary_work_title") or "") or None
    source_title = (
        reader_infographic_title(source.get("title"))
        if source.get("title_source") in AUTHORITATIVE_SOURCE_TITLE_SOURCES
        else None
    )
    if primary_work_status != "primary":
        lock_title = reader_infographic_title(lock_title)
    prompt_contract_version = str(
        prompt_contract_version or INFOGRAPHIC_PROMPT_CONTRACT_VERSION
    ).strip()
    prompt = render_gpt_image_2_prompt(
        source_title=source_title,
        source_label=source_label,
        original_date=original_date,
        source_topic=source_topic,
        lock_title=lock_title,
        subtitle=subtitle,
        archetype=archetype,
        layout_brief=visual_structure_instruction(graph_type, visual_structure),
        anchors=anchors,
        visible_lines=visible_lines,
        numbers=numbers,
        index=page_index,
        total=page_count,
        generated_on=generated_on,
        navigation_lines=navigation_lines,
        primary_work_title=primary_work_title,
        primary_work_status=primary_work_status,
        prompt_contract_version=prompt_contract_version,
        reviewed_copy_lock=reviewed_copy_lock,
        contextual_labels=contextual_heading_labels({"visible_text": visible_lines, "visual_structure": visual_structure}),
    )
    compiled = {
        "schema_version": INFOGRAPHIC_PLAN_SCHEMA_VERSION,
        "prompt_contract_version": prompt_contract_version,
        "content_use": content_use,
        "graph_type": graph_type,
        "visual_structure": visual_structure,
        "prompt": prompt,
        "asset_path": (
            Path("assets")
            / canonical_infographic_asset_filename(
                str(source.get("source_id") or ""),
                slug,
                page_index,
                page_count,
            )
        ).as_posix(),
    }
    compiled["compiled_plan_sha256"] = canonical_payload_sha256(compiled)
    return compiled


def compile_infographic_page(
    *,
    source: dict[str, Any],
    infographic: dict[str, Any],
    slug: str,
    page_index: int,
    page_count: int,
) -> dict[str, Any]:
    """Compile one reviewed page plan without accepting hand-written prompt state."""

    page_id = str(infographic.get("page_id") or "").strip()
    title = str(infographic.get("title") or "").strip()
    title_source = str(infographic.get("title_source") or "").strip()
    page_focus = str(infographic.get("page_focus") or "").strip()
    archetype = str(infographic.get("layout_archetype") or "").strip()
    primary_work_status = str(source.get("primary_work_status") or "").strip()
    if primary_work_status != "primary":
        title = reader_infographic_title(title)
    source_label = str(source.get("source_label") or "").strip()
    footer_source_label = reader_footer_source_label(source)
    visible_lines = [
        str(item).strip()
        for item in infographic.get("visible_text") or []
        if str(item).strip()
    ]
    navigation_lines = _infographic_string_list(infographic, "navigation_text")
    structure_errors = validate_visual_structure(infographic)
    if structure_errors:
        raise ValueError("; ".join(structure_errors))
    content_use = str(infographic.get("content_use") or "").strip()
    graph_type = str(infographic.get("graph_type") or "").strip()
    visual_structure = infographic["visual_structure"]
    assert isinstance(visual_structure, dict)
    anchors = [
        str(item).strip()
        for item in infographic.get("source_anchors") or []
        if str(item).strip()
    ]
    number_labels = [
        str(item).strip()
        for item in infographic.get("number_labels") or []
        if str(item).strip()
    ]
    missing = [
        field
        for field, value in (
            ("source.source_label", source_label),
            ("infographic.page_id", page_id),
            ("infographic.title", title),
            ("infographic.page_focus", page_focus),
            ("infographic.layout_archetype", archetype),
            ("infographic.visible_text", visible_lines),
            ("infographic.source_anchors", anchors),
        )
        if not value
    ]
    if missing:
        raise ValueError("missing infographic compile fields: " + ", ".join(missing))

    primary_work_title = str(source.get("primary_work_title") or "").strip()
    source_title = str(source.get("title") or "").strip()
    if primary_work_status == "primary":
        if title != primary_work_title or title_source != "primary_work":
            raise ValueError("primary work must be the infographic title")
    elif source.get("title_source") in AUTHORITATIVE_SOURCE_TITLE_SOURCES:
        if (
            title != reader_infographic_title(source_title)
            or title_source != source.get("title_source")
        ):
            raise ValueError("authoritative source title must be the infographic title")
    elif title_source not in {"source_derived", "ai_generated_from_source"}:
        raise ValueError("derived infographic title has unsupported title source")

    prompt_contract_version = str(
        infographic.get("prompt_contract_version")
        or INFOGRAPHIC_PROMPT_CONTRACT_VERSION
    ).strip()
    compiled = compile_page(
        source=source,
        slug=slug,
        original_date=str(source.get("original_date") or "").strip() or None,
        source_label=footer_source_label,
        source_topic=str(infographic.get("source_topic") or page_focus).strip(),
        lock_title=title,
        subtitle=str(infographic.get("subtitle") or page_focus).strip(),
        archetype=archetype,
        content_use=content_use,
        graph_type=graph_type,
        visual_structure=visual_structure,
        anchors=anchors,
        visible_lines=visible_lines,
        numbers="；".join(number_labels),
        page_index=page_index,
        page_count=page_count,
        generated_on=str(infographic.get("generated_on") or date.today().isoformat()),
        navigation_lines=navigation_lines,
        prompt_contract_version=prompt_contract_version,
        reviewed_copy_lock=bool(infographic.get("reader_review")),
    )
    compiled["page_id"] = page_id
    compiled["source_identity"] = str(source.get("source_identity") or "")
    compiled["compiled_plan_sha256"] = canonical_payload_sha256(
        {
            key: value
            for key, value in compiled.items()
            if key != "compiled_plan_sha256"
        }
    )
    return compiled
