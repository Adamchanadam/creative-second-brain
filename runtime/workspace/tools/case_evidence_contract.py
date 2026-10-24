"""Immutable evidence contract for a research-program case package.

The normal Compiler Worker source identity remains the sole identity.  This
module only validates and stages the additional evidence required when an
accepted URL is intentionally handled as a reproducible research case.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import zlib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ASSET_MODE_FORMAL = "formal_infographic"
ASSET_MODE_CASE = "case_evidence"
ASSET_MODE_KNOWLEDGE = "knowledge_only"
MANIFEST_VERSION = "case_evidence_manifest_v1"
MANIFEST_VERSION_V2 = "case_evidence_manifest_v2"
SELECTION_CONTRACT_SCHEMA = "research_topic_selection_contract_v1"
SOURCE_CAPTURE_SCHEMA = "case_source_capture_v1"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CaseEvidenceError(ValueError):
    pass


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _required_text(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise CaseEvidenceError(f"case evidence missing {label}")
    return result


def _safe_identifier(value: object, label: str) -> str:
    result = _required_text(value, label)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", result):
        raise CaseEvidenceError(f"case evidence has unsafe {label}")
    return result


def _inside(base: Path, raw: object, label: str) -> Path:
    value = _required_text(raw, label)
    path = (base / value).resolve(strict=False)
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise CaseEvidenceError(f"{label} escapes its evidence root") from exc
    if not path.is_file():
        raise CaseEvidenceError(f"{label} is missing")
    return path


def _inside_directory(base: Path, raw: object, label: str) -> Path:
    value = _required_text(raw, label)
    path = (base / value).resolve(strict=False)
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise CaseEvidenceError(f"{label} escapes its evidence root") from exc
    if not path.is_dir():
        raise CaseEvidenceError(f"{label} is missing")
    return path


def _verify_hashed_file(base: Path, item: dict[str, Any], path_key: str, hash_key: str, label: str) -> Path:
    path = _inside(base, item.get(path_key), label)
    expected = _required_text(item.get(hash_key), f"{label} {hash_key}")
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or sha256_file(path) != expected:
        raise CaseEvidenceError(f"{label} hash mismatch")
    return path


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Decode the complete non-interlaced PNG stream, not just its IHDR."""
    payload = path.read_bytes()
    if len(payload) < 45 or payload[:8] != PNG_SIGNATURE:
        raise CaseEvidenceError(f"{path.name} is not a real PNG screenshot")
    offset = len(PNG_SIGNATURE)
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(payload):
        if offset + 12 > len(payload):
            raise CaseEvidenceError(f"{path.name} has a truncated PNG chunk")
        length = struct.unpack(">I", payload[offset:offset + 4])[0]
        kind = payload[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(payload):
            raise CaseEvidenceError(f"{path.name} has a truncated PNG chunk")
        data = payload[offset + 8:offset + 8 + length]
        actual_crc = struct.unpack(">I", payload[offset + 8 + length:end])[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != actual_crc:
            raise CaseEvidenceError(f"{path.name} has an invalid PNG CRC")
        chunks.append((kind, data))
        offset = end
        if kind == b"IEND":
            break
    if offset != len(payload) or len(chunks) < 3 or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise CaseEvidenceError(f"{path.name} has an invalid PNG structure")
    ihdr = chunks[0][1]
    if len(ihdr) != 13:
        raise CaseEvidenceError(f"{path.name} has an invalid PNG header")
    width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", ihdr)
    if width < 16 or height < 16:
        raise CaseEvidenceError(f"{path.name} has unusable screenshot dimensions")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    if bit_depth != 8 or channels is None or compression != 0 or filter_method != 0 or interlace != 0:
        raise CaseEvidenceError(f"{path.name} is not a decodable screenshot PNG")
    compressed = b"".join(data for kind, data in chunks if kind == b"IDAT")
    if not compressed:
        raise CaseEvidenceError(f"{path.name} has no PNG image data")
    try:
        decoded = zlib.decompress(compressed)
    except zlib.error as exc:
        raise CaseEvidenceError(f"{path.name} PNG image data cannot be decoded") from exc
    expected = height * (1 + width * channels)
    if len(decoded) != expected:
        raise CaseEvidenceError(f"{path.name} PNG image data has an invalid decoded length")
    return width, height


def _validate_screenshot(
    base: Path,
    item: object,
    *,
    expected_roles: set[str],
    manifest_version: str,
) -> tuple[dict[str, Any], Path]:
    if not isinstance(item, dict):
        raise CaseEvidenceError("case evidence screenshot must be an object")
    role = _required_text(item.get("role"), "screenshot role")
    if role not in {"desktop", "mobile", "interaction", "comparison"}:
        raise CaseEvidenceError("case evidence screenshot role is unsupported")
    expected_roles.add(role)
    _safe_identifier(item.get("id"), "screenshot id")
    _required_text(item.get("source_url"), "screenshot source_url")
    if manifest_version == MANIFEST_VERSION_V2:
        origin = _required_text(item.get("evidence_origin"), "screenshot evidence_origin")
        if origin not in {"creator_article", "source_observation", "local_reproduction"}:
            raise CaseEvidenceError("case evidence screenshot evidence_origin is unsupported")
    _required_text(item.get("captured_at"), "screenshot captured_at")
    viewport = item.get("viewport")
    if not isinstance(viewport, dict) or not isinstance(viewport.get("width"), int) or not isinstance(viewport.get("height"), int):
        raise CaseEvidenceError("case evidence screenshot viewport is invalid")
    if viewport["width"] < 1 or viewport["height"] < 1:
        raise CaseEvidenceError("case evidence screenshot viewport is invalid")
    scale = viewport.get("device_scale_factor", 1)
    if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale <= 0:
        raise CaseEvidenceError("case evidence screenshot viewport scale is invalid")
    actions = item.get("actions")
    if not isinstance(actions, list) or not all(isinstance(value, str) and value.strip() for value in actions):
        raise CaseEvidenceError("case evidence screenshot actions are required")
    _required_text(item.get("visual_readback"), "screenshot visual_readback")
    path = _verify_hashed_file(base, item, "path", "sha256", "screenshot")
    pixels_width, pixels_height = _png_dimensions(path)
    if pixels_width < viewport["width"] * scale or pixels_height < viewport["height"] * scale:
        raise CaseEvidenceError("case evidence screenshot pixels do not cover the recorded viewport")
    return item, path


def _required_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise CaseEvidenceError(f"{label} must be true or false")
    return value


def validate_selection_contract(value: object) -> dict[str, Any]:
    """Validate the optional, topic-owned case selection contract.

    It deliberately validates evidence binding, not subjective taste.  A topic
    may define any criteria it needs; no fixed UI/UX vocabulary is embedded in
    the portable product.
    """
    if not isinstance(value, dict):
        raise CaseEvidenceError("selection_contract must be an object")
    # Older topic records predate the structured evidence contract. They
    # remain readable selection metadata; newly created case-evidence packages
    # still use the structured contract below.
    if value.get("schema_version") is None:
        owner = _required_text(value.get("owner"), "legacy selection_contract owner")
        version = _safe_identifier(value.get("version"), "selection_contract version")
        required = value.get("required_criteria")
        interaction = _safe_identifier(
            value.get("interaction_criterion"), "legacy selection_contract interaction_criterion"
        )
        if (
            not isinstance(required, list)
            or not required
            or any(
                not isinstance(item, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item)
                for item in required
            )
            or len(set(required)) != len(required)
            or interaction not in required
        ):
            raise CaseEvidenceError("legacy selection_contract criteria are invalid")
        return {
            "schema_version": "research_topic_selection_contract_legacy_v1",
            "version": version,
            "criteria": [
                {"id": item, "label": item, "evidence_roles": ["interaction"] if item == interaction else []}
                for item in required
            ],
            "source_interaction_required": True,
            "reproduction_required": True,
            "learning_alignment_required": False,
            "reproduction_criterion": None,
            "legacy_owner": owner,
            "legacy_compatibility": True,
        }
    # ``research_program`` persists the normalized result from the legacy
    # branch above beside an accepted case.  A later completion/readback must
    # be able to validate that same result instead of rejecting the program's
    # own normalized representation as an unknown user contract.
    if value.get("schema_version") not in {
        SELECTION_CONTRACT_SCHEMA,
        "research_topic_selection_contract_legacy_v1",
    }:
        raise CaseEvidenceError("selection_contract has an unsupported schema")
    version = _safe_identifier(value.get("version"), "selection_contract version")
    raw_criteria = value.get("criteria")
    if not isinstance(raw_criteria, list) or not raw_criteria:
        raise CaseEvidenceError("selection_contract requires criteria")
    criteria: list[dict[str, Any]] = []
    seen: set[str] = set()
    supported_roles = {"desktop", "mobile", "interaction", "comparison"}
    for raw in raw_criteria:
        if not isinstance(raw, dict):
            raise CaseEvidenceError("selection_contract criterion must be an object")
        criterion_id = _safe_identifier(raw.get("id"), "selection_contract criterion id")
        if criterion_id in seen:
            raise CaseEvidenceError("selection_contract criterion IDs must be unique")
        label = _required_text(raw.get("label"), "selection_contract criterion label")
        roles = raw.get("evidence_roles", [])
        if roles and (
            not isinstance(roles, list)
            or any(not isinstance(role, str) or role not in supported_roles for role in roles)
        ):
            raise CaseEvidenceError("selection_contract criterion evidence_roles are invalid")
        criteria.append({"id": criterion_id, "label": label, "evidence_roles": list(roles)})
        seen.add(criterion_id)
    learning_alignment_required = value.get("learning_alignment_required", False)
    if not isinstance(learning_alignment_required, bool):
        raise CaseEvidenceError("selection_contract learning_alignment_required must be true or false")
    reproduction_criterion = value.get("reproduction_criterion")
    if learning_alignment_required:
        reproduction_criterion = _safe_identifier(
            reproduction_criterion, "selection_contract reproduction_criterion"
        )
        if reproduction_criterion not in seen:
            raise CaseEvidenceError("selection_contract reproduction_criterion must be one of the topic criteria")
    elif reproduction_criterion is not None:
        raise CaseEvidenceError("selection_contract reproduction_criterion requires learning_alignment_required")
    normalized = {
        "schema_version": SELECTION_CONTRACT_SCHEMA,
        "version": version,
        "criteria": criteria,
        "source_interaction_required": _required_bool(
            value.get("source_interaction_required"), "selection_contract source_interaction_required"
        ),
        "reproduction_required": _required_bool(
            value.get("reproduction_required"), "selection_contract reproduction_required"
        ),
        "learning_alignment_required": learning_alignment_required,
        "reproduction_criterion": reproduction_criterion,
    }
    # A normalized legacy record is persisted beside accepted cases.  Its
    # second validation must be byte-for-byte stable: changing it to the
    # structured schema would change the evidence binding hash without any
    # change to the topic rule or evidence.
    if value.get("schema_version") == "research_topic_selection_contract_legacy_v1":
        legacy_owner = _required_text(value.get("legacy_owner"), "legacy selection_contract owner")
        if value.get("legacy_compatibility") is not True:
            raise CaseEvidenceError("legacy selection_contract compatibility must be true")
        return {
            **normalized,
            "schema_version": "research_topic_selection_contract_legacy_v1",
            "legacy_owner": legacy_owner,
            "legacy_compatibility": True,
        }
    return normalized


def _read_source_capture(
    base: Path,
    item: object,
    *,
    screenshots: dict[str, dict[str, Any]],
    expected_url: str,
) -> tuple[dict[str, Any], Path]:
    if not isinstance(item, dict):
        raise CaseEvidenceError("case evidence source_capture must be an object")
    receipt = _verify_hashed_file(base, item, "receipt_path", "receipt_sha256", "source capture receipt")
    try:
        data = json.loads(receipt.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseEvidenceError("source capture receipt is not valid JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != SOURCE_CAPTURE_SCHEMA or data.get("status") != "captured":
        raise CaseEvidenceError("source capture receipt has an unsupported schema or status")
    if data.get("source_url") != expected_url:
        raise CaseEvidenceError("source capture receipt source_url differs from its declared observation")
    action_id = _safe_identifier(data.get("action_id"), "source capture action_id")
    before = data.get("before")
    after = data.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise CaseEvidenceError("source capture receipt requires before and after states")
    before_id = _safe_identifier(before.get("screenshot_id"), "source capture before screenshot_id")
    after_id = _safe_identifier(after.get("screenshot_id"), "source capture after screenshot_id")
    if before_id not in screenshots or after_id not in screenshots:
        raise CaseEvidenceError("source capture receipt references an unknown screenshot")
    if screenshots[after_id].get("role") != "interaction":
        raise CaseEvidenceError("source capture after screenshot must have interaction role")
    if action_id not in screenshots[after_id].get("actions", []):
        raise CaseEvidenceError("source capture action is not bound to the interaction screenshot")
    before_state = _required_text(before.get("state"), "source capture before state")
    after_state = _required_text(after.get("state"), "source capture after state")
    if before_state == after_state or data.get("state_changed") is not True:
        raise CaseEvidenceError("source capture receipt does not prove a state change")
    for record, screenshot_id, label in ((before, before_id, "before"), (after, after_id, "after")):
        expected_hash = str(screenshots[screenshot_id].get("sha256") or "")
        if record.get("sha256") != expected_hash:
            raise CaseEvidenceError(f"source capture {label} screenshot hash differs from the manifest")
    return data, receipt


def _required_mechanism_ids(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CaseEvidenceError(f"{label} is required")
    identifiers = [_safe_identifier(item, label) for item in value]
    if len(set(identifiers)) != len(identifiers):
        raise CaseEvidenceError(f"{label} must not contain duplicates")
    return identifiers


def validate_learning_alignment(assessment: dict[str, Any], reproduction: dict[str, Any]) -> list[str]:
    """Require a declared, testable bridge from the chosen source method to local work.

    This does not pretend to score visual quality automatically.  It makes the
    reviewer name the source value, the mechanisms that cannot be traded away,
    the rejected shortcut, and the local observations that must prove them.
    """
    alignment = assessment.get("learning_alignment")
    if not isinstance(alignment, dict):
        raise CaseEvidenceError("selection_assessment requires learning_alignment")
    _safe_identifier(alignment.get("id"), "learning_alignment id")
    _required_text(alignment.get("objective"), "learning_alignment objective")
    _required_text(alignment.get("rejected_shortcut"), "learning_alignment rejected_shortcut")
    critical = alignment.get("critical_mechanisms")
    if not isinstance(critical, list) or not critical:
        raise CaseEvidenceError("learning_alignment critical_mechanisms are required")
    mechanism_ids: list[str] = []
    for item in critical:
        if not isinstance(item, dict):
            raise CaseEvidenceError("learning_alignment critical mechanism must be an object")
        mechanism_ids.append(_safe_identifier(item.get("id"), "learning_alignment critical mechanism id"))
        _required_text(item.get("source_basis"), "learning_alignment source_basis")
        _required_text(item.get("local_result"), "learning_alignment local_result")
    if len(set(mechanism_ids)) != len(mechanism_ids):
        raise CaseEvidenceError("learning_alignment critical mechanism IDs must be unique")
    unverified = alignment.get("unverified_source_methods")
    if not isinstance(unverified, list) or any(not isinstance(item, str) or not item.strip() for item in unverified):
        raise CaseEvidenceError("learning_alignment unverified_source_methods must be a text list")
    if reproduction.get("learning_alignment_id") != alignment.get("id"):
        raise CaseEvidenceError("reproduction learning_alignment_id does not match the assessment")
    reproduced = _required_mechanism_ids(
        reproduction.get("verified_mechanism_ids"), "reproduction verified_mechanism_ids"
    )
    if reproduced != mechanism_ids:
        raise CaseEvidenceError("reproduction does not cover every critical learning mechanism")
    return mechanism_ids


def _validate_learning_observations(
    manifest: dict[str, Any], action_log: dict[str, Any], browser_actions: list[str]
) -> None:
    assessment = manifest.get("selection_assessment")
    reproduction = manifest.get("reproduction")
    if not isinstance(assessment, dict) or not isinstance(reproduction, dict):
        # Structural assessment validation below owns the missing-assessment
        # message.  Do not make a missing assessment look like an action-log
        # error merely because the caller supplied a topic contract.
        return
    mechanism_ids = validate_learning_alignment(assessment, reproduction)
    observations = action_log.get("observations")
    records = observations.get("mechanisms") if isinstance(observations, dict) else None
    if not isinstance(records, list) or not records:
        raise CaseEvidenceError("reproduction action log requires mechanism observations")
    seen: set[str] = set()
    for item in records:
        if not isinstance(item, dict):
            raise CaseEvidenceError("reproduction mechanism observation must be an object")
        mechanism_id = _safe_identifier(item.get("id"), "reproduction mechanism observation id")
        if mechanism_id not in mechanism_ids or mechanism_id in seen:
            raise CaseEvidenceError("reproduction browser action log does not exactly cover the selected core methods")
        action_ids = item.get("action_ids")
        if not isinstance(action_ids, list) or not action_ids or any(
            not isinstance(action, str) or action not in browser_actions for action in action_ids
        ):
            raise CaseEvidenceError("reproduction mechanism observation must bind recorded browser actions")
        _required_text(item.get("expected_result"), "reproduction mechanism expected_result")
        _required_text(item.get("observed_result"), "reproduction mechanism observed_result")
        if item.get("state_changed") is not True:
            raise CaseEvidenceError("reproduction mechanism observation must record a real state change")
        seen.add(mechanism_id)
    if seen != set(mechanism_ids):
        raise CaseEvidenceError("reproduction browser action log does not exactly cover the selected core methods")


def validate_selection_assessment(
    manifest: dict[str, Any],
    contract: object,
    *,
    screenshots: dict[str, dict[str, Any]],
    source_capture: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind one case manifest to one versioned, topic-defined selection rule."""
    normalized = validate_selection_contract(contract)
    assessment = manifest.get("selection_assessment")
    if not isinstance(assessment, dict):
        raise CaseEvidenceError("case evidence requires a selection_assessment for this topic")
    if assessment.get("contract_version") != normalized["version"]:
        raise CaseEvidenceError("selection_assessment contract version differs from the topic")
    raw_criteria = assessment.get("criteria")
    if not isinstance(raw_criteria, list):
        raise CaseEvidenceError("selection_assessment criteria are required")
    expected = {str(item["id"]): item for item in normalized["criteria"]}
    seen: set[str] = set()
    for item in raw_criteria:
        if not isinstance(item, dict):
            raise CaseEvidenceError("selection_assessment criterion must be an object")
        criterion_id = _safe_identifier(item.get("id"), "selection_assessment criterion id")
        if criterion_id not in expected or criterion_id in seen:
            raise CaseEvidenceError("selection_assessment criterion IDs do not match the topic contract")
        _required_text(item.get("finding"), "selection_assessment finding")
        evidence_ids = item.get("evidence_screenshot_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(value, str) for value in evidence_ids):
            raise CaseEvidenceError("selection_assessment criterion evidence_screenshot_ids are required")
        if any(value not in screenshots for value in evidence_ids):
            raise CaseEvidenceError("selection_assessment references an unknown screenshot")
        required_roles = set(expected[criterion_id]["evidence_roles"])
        observed_roles = {str(screenshots[value].get("role") or "") for value in evidence_ids}
        if not required_roles.issubset(observed_roles):
            raise CaseEvidenceError("selection_assessment criterion lacks a required evidence role")
        seen.add(criterion_id)
    if seen != set(expected):
        raise CaseEvidenceError("selection_assessment does not address every topic criterion")
    if normalized["source_interaction_required"]:
        interaction = assessment.get("source_interaction")
        if not isinstance(interaction, dict) or source_capture is None:
            raise CaseEvidenceError("selection_assessment requires a verified source interaction capture")
        interaction_url = (
            manifest.get("source_observation", {}).get("url")
            if manifest.get("schema_version") == MANIFEST_VERSION_V2
            and isinstance(manifest.get("source_observation"), dict)
            else manifest.get("primary_source_url")
        )
        required_pairs = {
            "source_url": interaction_url,
            "action_id": source_capture.get("action_id"),
            "before_screenshot_id": source_capture.get("before", {}).get("screenshot_id"),
            "after_screenshot_id": source_capture.get("after", {}).get("screenshot_id"),
            "before_state": source_capture.get("before", {}).get("state"),
            "after_state": source_capture.get("after", {}).get("state"),
        }
        if interaction.get("state_changed") is not True or any(interaction.get(key) != value for key, value in required_pairs.items()):
            raise CaseEvidenceError("selection_assessment source interaction is not bound to the verified capture")
        if manifest.get("schema_version") == MANIFEST_VERSION_V2:
            interaction_item = next(
                (item for item in raw_criteria if isinstance(item, dict) and item.get("id") == "source_interaction"),
                None,
            )
            interaction_ids = interaction_item.get("evidence_screenshot_ids") if isinstance(interaction_item, dict) else None
            if not isinstance(interaction_ids, list) or any(
                screenshots[screenshot_id].get("evidence_origin") != "source_observation"
                or screenshots[screenshot_id].get("source_url") != interaction_url
                for screenshot_id in interaction_ids
            ):
                raise CaseEvidenceError("selection_assessment source interaction is not bound to source observation evidence")
    if normalized["reproduction_required"]:
        reproduction = assessment.get("reproduction")
        if not isinstance(reproduction, dict) or reproduction.get("verified") is not True:
            raise CaseEvidenceError("selection_assessment requires a verified reproduction")
        if reproduction.get("comparison_action_id") != manifest.get("reproduction", {}).get("comparison_action_id"):
            raise CaseEvidenceError("selection_assessment reproduction action differs from the evidence manifest")
        if manifest.get("schema_version") == MANIFEST_VERSION_V2 and normalized["learning_alignment_required"]:
            mechanism_ids = validate_learning_alignment(assessment, manifest.get("reproduction", {}))
            reproduction_criterion = normalized["reproduction_criterion"]
            method_item = next(
                (item for item in raw_criteria if isinstance(item, dict) and item.get("id") == reproduction_criterion),
                None,
            )
            if not isinstance(method_item, dict) or set(
                _required_mechanism_ids(method_item.get("mechanism_ids"), "reproduction criterion mechanism_ids")
            ) != set(mechanism_ids):
                raise CaseEvidenceError("selection_assessment reproduction criterion does not bind every critical learning mechanism")
    return normalized


def _validate_local_reproduction_url(value: object) -> str:
    url = _required_text(value, "reproduction local_url")
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or not parsed.port:
        raise CaseEvidenceError("reproduction local_url must be an explicit localhost HTTP URL")
    return url


def _validate_v2_url_roles(
    manifest: dict[str, Any],
    screenshots: dict[str, dict[str, Any]],
    *,
    primary_url: str,
    source_observation_url: str,
    local_reproduction_url: str,
) -> None:
    """Keep article, observed source UI and local rerun evidence distinct.

    Equal strings are allowed only when the evidence role actually describes the
    same surface.  The important boundary is the declared role, not a brittle
    assumption that every creator article and runnable product have different
    URLs.
    """
    expected_urls = {
        "creator_article": primary_url,
        "source_observation": source_observation_url,
        "local_reproduction": local_reproduction_url,
    }
    for screenshot in screenshots.values():
        origin = screenshot.get("evidence_origin")
        if screenshot.get("source_url") != expected_urls.get(origin):
            raise CaseEvidenceError("case evidence screenshot URL does not match its declared evidence_origin")


def validate_manifest(
    root: Path,
    manifest_path: Path,
    *,
    primary_url: str | None = None,
    evidence_root: Path | None = None,
    selection_contract: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Validate external staging evidence without copying it or trusting paths."""
    manifest_path = manifest_path.resolve()
    try:
        manifest_path.relative_to(root.resolve())
    except ValueError as exc:
        raise CaseEvidenceError("case evidence manifest escapes project root") from exc
    if not manifest_path.is_file():
        raise CaseEvidenceError("case evidence manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseEvidenceError("case evidence manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise CaseEvidenceError("case evidence manifest must be an object")
    manifest_version = manifest.get("schema_version")
    if manifest_version not in {MANIFEST_VERSION, MANIFEST_VERSION_V2} or manifest.get("asset_mode") != ASSET_MODE_CASE:
        raise CaseEvidenceError("case evidence manifest has an unsupported schema or asset mode")
    _safe_identifier(manifest.get("case_id"), "case_id")
    _safe_identifier(manifest.get("topic_id"), "topic_id")
    actual_url = _required_text(manifest.get("primary_source_url"), "primary_source_url")
    if primary_url and actual_url != primary_url:
        raise CaseEvidenceError("case evidence primary_source_url does not match the accepted URL")
    creator = manifest.get("creator")
    if not isinstance(creator, dict):
        raise CaseEvidenceError("case evidence creator is required")
    for field in ("name", "article_date", "ai_evidence_quote"):
        _required_text(creator.get(field), f"creator {field}")
    base = (evidence_root or manifest_path.parent).resolve()
    try:
        base.relative_to(root.resolve())
    except ValueError as exc:
        raise CaseEvidenceError("case evidence root escapes project root") from exc
    source_observation_url = ""
    local_reproduction_url = ""
    if manifest_version == MANIFEST_VERSION_V2:
        source_observation = manifest.get("source_observation")
        if not isinstance(source_observation, dict):
            raise CaseEvidenceError("case evidence v2 requires source_observation")
        source_observation_url = _required_text(source_observation.get("url"), "source_observation url")
        if source_observation.get("kind") != "public_observation":
            raise CaseEvidenceError("case evidence source_observation kind is unsupported")
    screenshots = manifest.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots:
        raise CaseEvidenceError("case evidence requires screenshots")
    roles: set[str] = set()
    screenshot_ids: set[str] = set()
    screenshot_records: dict[str, dict[str, Any]] = {}
    staged: dict[str, Path] = {}
    for item in screenshots:
        record, path = _validate_screenshot(
            base, item, expected_roles=roles, manifest_version=str(manifest_version)
        )
        if record["id"] in screenshot_ids:
            raise CaseEvidenceError("case evidence screenshot IDs must be unique")
        screenshot_ids.add(record["id"])
        screenshot_records[str(record["id"])] = record
        staged[f"screenshot:{record['id']}"] = path
    if not {"desktop", "mobile"}.issubset(roles):
        raise CaseEvidenceError("case evidence requires both desktop and mobile screenshots")
    if manifest.get("interaction_required") is True and "interaction" not in roles:
        raise CaseEvidenceError("case evidence requires an interaction screenshot")
    source_capture: dict[str, Any] | None = None
    if manifest.get("source_capture") is not None:
        source_capture, capture_path = _read_source_capture(
            base,
            manifest.get("source_capture"),
            screenshots=screenshot_records,
            expected_url=source_observation_url or actual_url,
        )
        staged["source_capture:receipt"] = capture_path
    references = manifest.get("technical_references")
    if not isinstance(references, list) or not references:
        raise CaseEvidenceError("case evidence requires at least one technical reference snapshot")
    for index, reference in enumerate(references, start=1):
        if not isinstance(reference, dict):
            raise CaseEvidenceError("technical reference must be an object")
        for field in ("url", "retrieved_at", "version", "purpose", "boundary"):
            _required_text(reference.get(field), f"technical reference {field}")
        staged[f"reference:{index}"] = _verify_hashed_file(base, reference, "snapshot_path", "snapshot_sha256", "technical reference snapshot")
    reproduction = manifest.get("reproduction")
    if not isinstance(reproduction, dict):
        raise CaseEvidenceError("case evidence reproduction receipt is required")
    for field in ("visual_readback", "comparison_action_id"):
        _required_text(reproduction.get(field), f"reproduction {field}")
    if manifest_version == MANIFEST_VERSION_V2:
        local_reproduction_url = _validate_local_reproduction_url(reproduction.get("local_url"))
    runner = reproduction.get("runner")
    if not isinstance(runner, dict) or runner.get("kind") != "python_http_server":
        raise CaseEvidenceError("reproduction runner must be the reviewed local python_http_server")
    argv = runner.get("argv")
    if not isinstance(argv, list) or len(argv) < 5 or not all(isinstance(item, str) and item.strip() for item in argv):
        raise CaseEvidenceError("reproduction runner argv is required")
    if argv[1:3] != ["-m", "http.server"] or "--directory" not in argv:
        raise CaseEvidenceError("reproduction runner argv is not an approved local static-server command")
    _inside_directory(base, runner.get("cwd"), "reproduction runner cwd")
    browser_actions = reproduction.get("browser_actions")
    if not isinstance(browser_actions, list) or not browser_actions or not all(isinstance(item, str) and item.strip() for item in browser_actions):
        raise CaseEvidenceError("reproduction browser_actions are required")
    staged["reproduction:entrypoint"] = _verify_hashed_file(
        base, reproduction, "entrypoint", "entrypoint_sha256", "reproduction executable entrypoint"
    )
    staged["reproduction:dependencies"] = _verify_hashed_file(
        base, reproduction, "dependency_snapshot_path", "dependency_snapshot_sha256", "reproduction dependency snapshot"
    )
    dependency_versions = reproduction.get("dependency_versions")
    if not isinstance(dependency_versions, dict) or not dependency_versions or not all(
        isinstance(name, str) and name.strip() and isinstance(version, str) and version.strip()
        for name, version in dependency_versions.items()
    ):
        raise CaseEvidenceError("reproduction dependency_versions are required")
    staged["reproduction:runtime-output"] = _verify_hashed_file(
        base, reproduction, "runtime_output_path", "runtime_output_sha256", "reproduction runtime output"
    )
    staged["reproduction:output"] = _verify_hashed_file(base, reproduction, "output_path", "output_sha256", "reproduction output")
    staged["reproduction:comparison"] = _verify_hashed_file(base, reproduction, "comparison_screenshot_path", "comparison_screenshot_sha256", "reproduction comparison screenshot")
    _png_dimensions(staged["reproduction:comparison"])
    try:
        runtime = json.loads(staged["reproduction:runtime-output"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseEvidenceError("reproduction runtime output is not valid JSON") from exc
    if not isinstance(runtime, dict) or runtime.get("schema_version") != "case_reproduction_runtime_v1":
        raise CaseEvidenceError("reproduction runtime output schema is invalid")
    if runtime.get("entrypoint_sha256") != reproduction.get("entrypoint_sha256") or runtime.get("dependency_snapshot_sha256") != reproduction.get("dependency_snapshot_sha256"):
        raise CaseEvidenceError("reproduction runtime output is not bound to executable inputs")
    if runtime.get("runner") != runner or runtime.get("comparison_screenshot_sha256") != reproduction.get("comparison_screenshot_sha256"):
        raise CaseEvidenceError("reproduction runtime output is not bound to runner or comparison screenshot")
    if runtime.get("comparison_action_id") != reproduction.get("comparison_action_id") or runtime.get("browser_actions") != browser_actions:
        raise CaseEvidenceError("reproduction runtime output is not bound to browser actions")
    if manifest_version == MANIFEST_VERSION_V2 and runtime.get("local_url") != local_reproduction_url:
        raise CaseEvidenceError("reproduction runtime output is not bound to local_url")
    action_log_owner = runtime if manifest_version == MANIFEST_VERSION_V2 else reproduction
    action_log_path = _verify_hashed_file(
        base, action_log_owner, "browser_action_log_path", "browser_action_log_sha256", "reproduction browser action log"
    )
    try:
        action_log = json.loads(action_log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseEvidenceError("reproduction browser action log is not valid JSON") from exc
    if not isinstance(action_log, dict) or action_log.get("schema_version") != "case_reproduction_browser_actions_v1":
        raise CaseEvidenceError("reproduction browser action log schema is invalid")
    if manifest_version == MANIFEST_VERSION_V2:
        if action_log.get("status") != "passed" or action_log.get("actions") != browser_actions:
            raise CaseEvidenceError("reproduction browser action log is not bound to the verified actions")
        if action_log.get("local_url") != local_reproduction_url:
            raise CaseEvidenceError("reproduction browser action log is not bound to local_url")
        screenshot = action_log.get("screenshot")
        if not isinstance(screenshot, dict) or screenshot.get("sha256") != reproduction.get("comparison_screenshot_sha256"):
            raise CaseEvidenceError("reproduction browser action log is not bound to comparison screenshot")
        if not isinstance(action_log.get("observations"), dict):
            raise CaseEvidenceError("reproduction browser action log lacks observed interaction state")
    normalized_selection_contract = (
        validate_selection_contract(selection_contract) if selection_contract is not None else None
    )
    if normalized_selection_contract is not None and normalized_selection_contract["learning_alignment_required"]:
        _validate_learning_observations(manifest, action_log, browser_actions)
    staged["reproduction:browser-action-log"] = action_log_path
    if runtime.get("exit_code") != 0:
        raise CaseEvidenceError("reproduction runtime output does not record a successful execution")
    if manifest_version == MANIFEST_VERSION_V2:
        _validate_v2_url_roles(
            manifest,
            screenshot_records,
            primary_url=actual_url,
            source_observation_url=source_observation_url,
            local_reproduction_url=local_reproduction_url,
        )
    if selection_contract is not None:
        validate_selection_assessment(
            manifest,
            selection_contract,
            screenshots=screenshot_records,
            source_capture=source_capture,
        )
    return manifest, staged


def stage_manifest(root: Path, manifest_path: Path, package: Path, *, primary_url: str) -> dict[str, Any]:
    """Copy only validated evidence into the package and rewrite paths locally."""
    manifest, assets = validate_manifest(root, manifest_path, primary_url=primary_url)
    target_root = package / "case_evidence"
    target_root.mkdir(parents=True, exist_ok=True)
    rewritten = json.loads(json.dumps(manifest, ensure_ascii=False))
    source_hash = sha256_file(manifest_path)
    staged_runtime: Path | None = None
    staged_browser_log: Path | None = None
    for key, path in assets.items():
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", path.name)
        destination = target_root / f"{key.replace(':', '-')}-{safe_name}"
        shutil.copy2(path, destination)
        if sha256_file(destination) != sha256_file(path):
            raise CaseEvidenceError("staged case evidence asset hash mismatch")
        if key.startswith("screenshot:"):
            for item in rewritten["screenshots"]:
                if item["id"] == key.split(":", 1)[1]:
                    item["path"] = destination.relative_to(package).as_posix()
        elif key.startswith("reference:"):
            rewritten["technical_references"][int(key.split(":", 1)[1]) - 1]["snapshot_path"] = destination.relative_to(package).as_posix()
        elif key == "source_capture:receipt":
            rewritten["source_capture"]["receipt_path"] = destination.relative_to(package).as_posix()
        elif key == "reproduction:entrypoint":
            rewritten["reproduction"]["entrypoint"] = destination.relative_to(package).as_posix()
        elif key == "reproduction:dependencies":
            rewritten["reproduction"]["dependency_snapshot_path"] = destination.relative_to(package).as_posix()
        elif key == "reproduction:runtime-output":
            rewritten["reproduction"]["runtime_output_path"] = destination.relative_to(package).as_posix()
            staged_runtime = destination
        elif key == "reproduction:browser-action-log":
            if rewritten.get("schema_version") == MANIFEST_VERSION_V2:
                staged_browser_log = destination
            else:
                rewritten["reproduction"]["browser_action_log_path"] = destination.relative_to(package).as_posix()
        elif key == "reproduction:output":
            rewritten["reproduction"]["output_path"] = destination.relative_to(package).as_posix()
        elif key == "reproduction:comparison":
            rewritten["reproduction"]["comparison_screenshot_path"] = destination.relative_to(package).as_posix()
        else:  # defensive: every staged key must be reflected in the manifest.
            raise CaseEvidenceError("case evidence staged an unsupported asset key")
    # The immutable source manifest records its own local cwd.  The copied
    # package serves from case_evidence instead, so rewrite both sides of the
    # runtime binding together rather than leaving a misleading runnable path.
    package_runner = rewritten["reproduction"]["runner"]
    package_runner["cwd"] = target_root.relative_to(package).as_posix()
    if staged_runtime is None:
        raise CaseEvidenceError("case evidence runtime output was not staged")
    try:
        runtime = json.loads(staged_runtime.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseEvidenceError("case evidence staged runtime output is invalid") from exc
    if not isinstance(runtime, dict):
        raise CaseEvidenceError("case evidence staged runtime output is invalid")
    runtime["runner"] = package_runner
    if rewritten.get("schema_version") == MANIFEST_VERSION_V2:
        if staged_browser_log is None:
            raise CaseEvidenceError("case evidence v2 browser action log was not staged")
        try:
            action_log = json.loads(staged_browser_log.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CaseEvidenceError("case evidence staged browser action log is invalid") from exc
        if not isinstance(action_log, dict) or not isinstance(action_log.get("screenshot"), dict):
            raise CaseEvidenceError("case evidence staged browser action log is invalid")
        action_log["screenshot"]["path"] = rewritten["reproduction"]["comparison_screenshot_path"]
        staged_browser_log.write_text(json.dumps(action_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        runtime["browser_action_log_path"] = staged_browser_log.relative_to(package).as_posix()
        runtime["browser_action_log_sha256"] = sha256_file(staged_browser_log)
    staged_runtime.write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rewritten["reproduction"]["runtime_output_sha256"] = sha256_file(staged_runtime)
    rewritten["source_manifest_sha256"] = source_hash
    rewritten["staged_from"] = manifest_path.relative_to(root).as_posix()
    target = target_root / "manifest.json"
    target.write_text(json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "asset_mode": ASSET_MODE_CASE,
        "case_evidence_manifest_path": target.relative_to(package).as_posix(),
        "case_evidence_manifest_sha256": sha256_file(target),
        "case_evidence_source_manifest_sha256": source_hash,
    }


def package_manifest(
    package: Path,
    source: dict[str, Any],
    *,
    selection_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read and revalidate staged evidence using package-local paths only."""
    raw = str(source.get("case_evidence_manifest_path") or "")
    expected = str(source.get("case_evidence_manifest_sha256") or "")
    if not raw or not expected:
        raise CaseEvidenceError("case evidence manifest binding is missing")
    path = _inside(package, raw, "case evidence manifest")
    if sha256_file(path) != expected:
        raise CaseEvidenceError("case evidence manifest drifted after acceptance")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CaseEvidenceError("staged case evidence manifest is invalid") from exc
    if manifest.get("asset_mode") != ASSET_MODE_CASE:
        raise CaseEvidenceError("staged case evidence asset mode drifted")
    if manifest.get("source_manifest_sha256") != source.get("case_evidence_source_manifest_sha256"):
        raise CaseEvidenceError("staged case evidence source-manifest binding drifted")
    # Paths now live below the package.  Re-use the validator by treating the
    # package as the evidence root and rewriting only the source manifest path.
    validate_manifest(
        package,
        path,
        primary_url=str(source.get("source_url") or source.get("normalized_url") or ""),
        evidence_root=package,
        selection_contract=selection_contract,
    )
    if str(manifest.get("creator", {}).get("ai_evidence_quote") or "") not in (package / "canonical_source.md").read_text(encoding="utf-8"):
        raise CaseEvidenceError("creator AI evidence quote is not in the accepted canonical source")
    return manifest


def case_asset_paths(package: Path, manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for item in manifest.get("screenshots", []):
        paths.append(_inside(package, item.get("path"), "case screenshot"))
    reproduction = manifest.get("reproduction", {})
    for key in ("entrypoint", "dependency_snapshot_path", "runtime_output_path", "output_path", "comparison_screenshot_path"):
        paths.append(_inside(package, reproduction.get(key), f"reproduction {key}"))
    if reproduction.get("browser_action_log_path") is not None:
        paths.append(_inside(package, reproduction.get("browser_action_log_path"), "reproduction browser action log"))
    source_capture = manifest.get("source_capture")
    if isinstance(source_capture, dict):
        paths.append(_inside(package, source_capture.get("receipt_path"), "source capture receipt"))
    for reference in manifest.get("technical_references", []):
        paths.append(_inside(package, reference.get("snapshot_path"), "technical reference snapshot"))
    return paths
