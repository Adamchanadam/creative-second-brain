"""Shared, fail-closed contract for a user-provided raster image source."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SUPPORTED_IMAGE_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
VISUAL_TYPES = {
    "infographic",
    "diagram",
    "chart",
    "screenshot",
    "slide",
    "document_scan",
    "photograph",
    "other",
}
ANCHOR_KINDS = {"title", "text", "quote", "table", "chart", "diagram", "footer", "image"}
COVERAGE_DISPOSITIONS = {"source_evidence", "knowledge_point", "application", "excluded"}


class ImageSourceContractError(ValueError):
    """An image source is unreadable or its visual analysis is not trustworthy."""


@dataclass(frozen=True)
class ImageInspection:
    sha256: str
    mime_type: str
    extension: str
    width: int
    height: int
    byte_length: int


def inspect_image(path: Path) -> ImageInspection:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ImageSourceContractError(f"image input is unreadable: {exc}") from exc
    if not raw:
        raise ImageSourceContractError("image input is empty")
    mime_type, width, height = identify_image(raw)
    extension = SUPPORTED_IMAGE_MIME_TYPES[mime_type]
    return ImageInspection(
        sha256=hashlib.sha256(raw).hexdigest(),
        mime_type=mime_type,
        extension=extension,
        width=width,
        height=height,
        byte_length=len(raw),
    )


def identify_image(raw: bytes) -> tuple[str, int, int]:
    if raw.startswith(b"\x89PNG\r\n\x1a\n") and len(raw) >= 24 and raw[12:16] == b"IHDR":
        width, height = struct.unpack(">II", raw[16:24])
        return valid_dimensions("image/png", width, height)
    if raw.startswith(b"\xff\xd8"):
        return jpeg_dimensions(raw)
    if raw.startswith(b"RIFF") and len(raw) >= 30 and raw[8:12] == b"WEBP":
        return webp_dimensions(raw)
    raise ImageSourceContractError("image input must be a readable PNG, JPEG, or WebP file")


def valid_dimensions(mime_type: str, width: int, height: int) -> tuple[str, int, int]:
    if width <= 0 or height <= 0:
        raise ImageSourceContractError("image input has invalid dimensions")
    return mime_type, width, height


def jpeg_dimensions(raw: bytes) -> tuple[str, int, int]:
    cursor = 2
    while cursor + 9 <= len(raw):
        if raw[cursor] != 0xFF:
            cursor += 1
            continue
        while cursor < len(raw) and raw[cursor] == 0xFF:
            cursor += 1
        if cursor >= len(raw):
            break
        marker = raw[cursor]
        cursor += 1
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if cursor + 2 > len(raw):
            break
        segment_length = struct.unpack(">H", raw[cursor:cursor + 2])[0]
        if segment_length < 2 or cursor + segment_length > len(raw):
            break
        if marker in {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }:
            if segment_length < 7:
                break
            height, width = struct.unpack(">HH", raw[cursor + 3:cursor + 7])
            return valid_dimensions("image/jpeg", width, height)
        cursor += segment_length
    raise ImageSourceContractError("JPEG input has no readable dimensions")


def webp_dimensions(raw: bytes) -> tuple[str, int, int]:
    chunk = raw[12:16]
    if chunk == b"VP8X" and len(raw) >= 30:
        width = 1 + int.from_bytes(raw[24:27], "little")
        height = 1 + int.from_bytes(raw[27:30], "little")
        return valid_dimensions("image/webp", width, height)
    if chunk == b"VP8 " and len(raw) >= 30 and raw[23:26] == b"\x9d\x01\x2a":
        width = struct.unpack("<H", raw[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", raw[28:30])[0] & 0x3FFF
        return valid_dimensions("image/webp", width, height)
    if chunk == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
        bits = int.from_bytes(raw[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return valid_dimensions("image/webp", width, height)
    raise ImageSourceContractError("WebP input has no readable dimensions")


def canonical_analysis_sha256(analysis: Mapping[str, Any]) -> str:
    canonical = json.dumps(analysis, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_visual_analysis(
    analysis: Mapping[str, Any],
    *,
    expected_image_sha256: str,
    expected_width: int,
    expected_height: int,
) -> list[dict[str, str]]:
    """Return all contract gaps; callers fail closed whenever this is non-empty."""
    issues: list[dict[str, str]] = []
    if analysis.get("source_image_sha256") != expected_image_sha256:
        issues.append(issue("source_image_sha256_mismatch", "視覺解讀沒有綁定到這張原圖的 SHA-256。"))
    if str(analysis.get("analysis_status") or "").strip() != "complete":
        issues.append(issue("analysis_incomplete", "視覺解讀的 analysis_status 必須是 complete。"))
    if str(analysis.get("checker_status") or "").strip() != "passed":
        issues.append(issue("checker_not_passed", "第二輪圖像校對 checker_status 必須是 passed。"))
    if not meaningful(analysis.get("checker_evidence")):
        issues.append(issue("checker_evidence_missing", "必須記錄第二輪對照原圖的具體校對證據。"))
    if str(analysis.get("visual_type") or "").strip() not in VISUAL_TYPES:
        issues.append(issue("visual_type_invalid", "visual_type 必須是受支持的圖片類型。"))
    if not meaningful(analysis.get("title")):
        issues.append(issue("title_missing", "圖片解讀缺少可讀標題。"))
    if not meaningful(analysis.get("summary")):
        issues.append(issue("summary_missing", "圖片解讀缺少一句定位／摘要。"))

    anchors = analysis.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        return issues + [issue("anchors_missing", "圖片解讀至少需要一個可回到原圖的視覺證據錨點。")]
    anchor_ids: set[str] = set()
    for index, anchor in enumerate(anchors, start=1):
        if not isinstance(anchor, Mapping):
            issues.append(issue("anchor_invalid", f"第 {index} 個錨點不是物件。"))
            continue
        anchor_id = str(anchor.get("id") or "").strip()
        if not anchor_id.startswith("IMG-") or anchor_id in anchor_ids:
            issues.append(issue("anchor_id_invalid", f"第 {index} 個錨點需要唯一 IMG- 開頭的 id。"))
        anchor_ids.add(anchor_id)
        if str(anchor.get("kind") or "").strip() not in ANCHOR_KINDS:
            issues.append(issue("anchor_kind_invalid", f"{anchor_id or f'第 {index} 個錨點'} 的 kind 不受支持。"))
        if not meaningful(anchor.get("evidence")):
            issues.append(issue("anchor_evidence_missing", f"{anchor_id or f'第 {index} 個錨點'} 缺少可見文字或視覺證據。"))
        issues.extend(region_issues(anchor.get("region"), anchor_id or f"第 {index} 個錨點"))

    transcription = analysis.get("transcription")
    if not isinstance(transcription, list) or not transcription:
        issues.append(issue("transcription_missing", "必須保存逐字轉寫；看不清處亦要明確標示。"))
    else:
        for index, item in enumerate(transcription, start=1):
            if not isinstance(item, Mapping):
                issues.append(issue("transcription_invalid", f"第 {index} 條轉寫不是物件。"))
                continue
            if str(item.get("anchor_id") or "") not in anchor_ids:
                issues.append(issue("transcription_anchor_missing", f"第 {index} 條轉寫沒有有效圖片錨點。"))
            if not meaningful(item.get("text")):
                issues.append(issue("transcription_text_missing", f"第 {index} 條轉寫缺少文字。"))
            if str(item.get("confidence") or "") not in {"high", "medium", "low", "unreadable"}:
                issues.append(issue("transcription_confidence_invalid", f"第 {index} 條轉寫缺少有效信心標示。"))

    claims = analysis.get("source_claims")
    if not isinstance(claims, list) or not claims:
        issues.append(issue("source_claims_missing", "必須列出可由圖片支持的主張或明確聲明圖片沒有主張。"))
    else:
        for index, claim in enumerate(claims, start=1):
            if not isinstance(claim, Mapping):
                issues.append(issue("source_claim_invalid", f"第 {index} 條來源主張不是物件。"))
                continue
            if not meaningful(claim.get("claim")):
                issues.append(issue("source_claim_text_missing", f"第 {index} 條來源主張缺少內容。"))
            if not anchor_reference_list(claim.get("anchor_ids"), anchor_ids):
                issues.append(issue("source_claim_anchor_missing", f"第 {index} 條來源主張沒有有效圖片錨點。"))
            if str(claim.get("verification_status") or "") not in {
                "image_visible", "externally_verified", "needs_external_verification",
            }:
                issues.append(issue("source_claim_verification_invalid", f"第 {index} 條來源主張缺少有效驗證狀態。"))

    coverage = analysis.get("coverage")
    if not isinstance(coverage, list) or not coverage:
        issues.append(issue("coverage_missing", "必須以覆蓋表說明每個重要錨點如何保留、轉化或排除。"))
    else:
        covered: set[str] = set()
        for index, row in enumerate(coverage, start=1):
            if not isinstance(row, Mapping):
                issues.append(issue("coverage_invalid", f"第 {index} 條覆蓋表不是物件。"))
                continue
            anchor_id = str(row.get("anchor_id") or "")
            covered.add(anchor_id)
            if anchor_id not in anchor_ids:
                issues.append(issue("coverage_anchor_missing", f"第 {index} 條覆蓋表沒有有效圖片錨點。"))
            if str(row.get("disposition") or "") not in COVERAGE_DISPOSITIONS:
                issues.append(issue("coverage_disposition_invalid", f"第 {index} 條覆蓋表缺少有效去向。"))
            if not meaningful(row.get("reason")):
                issues.append(issue("coverage_reason_missing", f"第 {index} 條覆蓋表缺少保留／轉化理由。"))
        uncovered = sorted(anchor_ids - covered)
        if uncovered:
            issues.append(issue("coverage_incomplete", f"以下圖片錨點未進入覆蓋表：{', '.join(uncovered)}。"))

    dimensions = analysis.get("image_dimensions")
    if not isinstance(dimensions, Mapping) or dimensions.get("width") != expected_width or dimensions.get("height") != expected_height:
        issues.append(issue("image_dimensions_mismatch", "視覺解讀的圖片尺寸必須與原圖一致。"))
    return issues


def visual_analysis_template(*, image_sha256: str, dimensions: dict) -> dict:
    """A deliberately unapproved response skeleton; never invent review results."""
    return {
        "source_image_sha256": image_sha256, "image_dimensions": dimensions,
        "analysis_status": "pending", "checker_status": "pending", "checker_evidence": "",
        "visual_type": "", "title": "", "summary": "",
        "anchors": [{"id": "IMG-001", "kind": "", "evidence": "",
                     "region": {"x": 0, "y": 0, "width": 1, "height": 1}}],
        "transcription": [{"anchor_id": "IMG-001", "text": "", "confidence": ""}],
        "source_claims": [{"claim": "", "anchor_ids": ["IMG-001"], "verification_status": ""}],
        "coverage": [{"anchor_id": "IMG-001", "disposition": "", "reason": ""}],
    }


def analysis_from_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImageSourceContractError(f"visual analysis JSON is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise ImageSourceContractError("visual analysis JSON must be an object")
    return value


def region_issues(value: object, label: str) -> list[dict[str, str]]:
    if not isinstance(value, Mapping):
        return [issue("anchor_region_missing", f"{label} 缺少 0–1 正規化圖片區域。")]
    required = ("x", "y", "width", "height")
    try:
        values = {key: float(value[key]) for key in required}
    except (KeyError, TypeError, ValueError):
        return [issue("anchor_region_invalid", f"{label} 的圖片區域格式無效。")]
    if not all(0 <= values[key] <= 1 for key in required) or values["width"] <= 0 or values["height"] <= 0:
        return [issue("anchor_region_invalid", f"{label} 的圖片區域必須在 0–1 內且有正面積。")]
    if values["x"] + values["width"] > 1 or values["y"] + values["height"] > 1:
        return [issue("anchor_region_invalid", f"{label} 的圖片區域超出原圖。")]
    return []


def anchor_reference_list(value: object, anchor_ids: set[str]) -> bool:
    return isinstance(value, list) and bool(value) and all(str(item) in anchor_ids for item in value)


def meaningful(value: object) -> bool:
    return isinstance(value, str) and len(value.strip()) >= 2 and value.strip().casefold() not in {"todo", "tbd", "待補", "unknown"}


def issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
