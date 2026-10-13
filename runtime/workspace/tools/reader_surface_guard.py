from __future__ import annotations

import re
from typing import Any, Mapping


INTERNAL_READER_SURFACE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "source_provenance_processing_status",
        re.compile(
            r"(?:來源|本文|本次|這份(?:文字|內容)?).{0,24}"
            r"(?:使用者|使用者|用戶).{0,24}(?:提供|貼上|輸入|摘要).{0,36}"
            r"(?:未|沒有|未有).{0,8}(?:外部)?(?:核對|校對|查證)",
            re.IGNORECASE,
        ),
    ),
    (
        "external_verification_processing_status",
        re.compile(
            r"(?:未|沒有|未有).{0,8}(?:另行|外部|獨立)?(?:核對|校對|查證).{0,28}"
            r"(?:原始(?:影片|文章|發布|來源)|原文|發布者|身份|產品現況|官方)",
            re.IGNORECASE,
        ),
    ),
    (
        "source_boundary_unit_metadata",
        re.compile(
            r"\b(?:source_provenance|source_boundary|source_identity|source_input_kind|"
            r"canonical_body_sha256|source_content_sha256)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "infographic_internal_validation_metadata",
        re.compile(
            r"(?:"
            r"PNG\s*(?:生成方式|語義驗收|SHA-256|尺寸)"
            r"|圖文鎖定稿(?:校對)?"
            r"|語氣口徑檢查"
            r"|圖面邊界"
            r"|圖示語義清單"
            r"|重複文字\s*/\s*數字檢查"
            r"|正文敘事充足性檢查"
            r"|visual\s+QC"
            r"|自動視覺安全檢查"
            r"|工作\s*job"
            r"|鎖定提示"
            r"|候選\s*PNG"
            r"|語義驗收\s*:\s*(?:pending|待|未讀原文)"
            r")",
            re.IGNORECASE,
        ),
    ),
)

FOOTER_INTERNAL_STATUS_PATTERN = re.compile(
    r"(?:原始(?:發布頁|來源|文章|影片)|發布者|作者|來源).{0,32}"
    r"(?:未提供|待核實|未核實|未外部核對)"
    r"|(?:未提供|待核實|未核實|未外部核對).{0,32}"
    r"(?:原始(?:發布頁|來源|文章|影片)|發布者|作者|來源)",
    re.IGNORECASE,
)


def reader_surface_internal_message_errors(
    visible_items: list[Any],
    *,
    context: str = "reader_surface",
) -> list[str]:
    errors: list[str] = []
    for index, raw_item in enumerate(visible_items, start=1):
        text = str(raw_item or "").strip()
        if not text:
            continue
        for code, pattern in INTERNAL_READER_SURFACE_PATTERNS:
            if pattern.search(text):
                errors.append(
                    f"{context}[{index}] exposes internal processing message "
                    f"({code}): {text}"
                )
                break
    return errors


def has_reader_surface_internal_message(text: str) -> bool:
    return bool(
        reader_surface_internal_message_errors(
            [text],
            context="reader_surface",
        )
    )


def footer_internal_status_errors(text: str, *, context: str = "footer") -> list[str]:
    """Reject capture or verification status only when it is used as a public footer."""

    value = str(text or "").strip()
    if value and FOOTER_INTERNAL_STATUS_PATTERN.search(value):
        return [
            f"{context} exposes internal processing message "
            f"(source_availability_or_verification_status): {value}"
        ]
    return []


def reader_safe_source_value(value: object, *, fallback: str = "") -> str:
    """Return one reader-safe source value, never capture/review status text."""

    label = str(value or "").strip()
    if (
        not label
        or reader_surface_internal_message_errors([label], context="footer_source_label")
        or footer_internal_status_errors(label, context="footer_source_label")
    ):
        return fallback
    return label


def public_footer_source_label(source_label: str, *, fallback: str = "來源資料") -> str:
    """Keep provenance-processing state out of the public image surface."""

    return reader_safe_source_value(source_label, fallback=fallback)


def reader_source_attribution_label(
    source: Mapping[str, object],
    *,
    fallback: str = "來源資料",
) -> str:
    """Select the one concise attribution suitable for reader-facing surfaces.

    ``source_input_kind`` identifies delivery and storage, not authorship.  A
    source label may contain necessary audit caveats that are unsafe to show on
    a reader surface; in that case this deliberately falls through to a
    separately declared original author or publication instead of exposing the
    delivery kind as the article's source.
    """

    for field in (
        "footer_source_label",
        "source_label",
        "attribution_label",
        "_original_author",
        "author",
        "_original_publication",
        "publication",
    ):
        label = reader_safe_source_value(source.get(field), fallback="")
        if label:
            return label
    return fallback


def reader_source_attribution_details(
    source: Mapping[str, object],
) -> tuple[str, str, str]:
    """Return safe publication, author and date values for a detailed citation."""

    def first_safe(*fields: str) -> str:
        for field in fields:
            value = reader_safe_source_value(source.get(field), fallback="")
            if value:
                return value
        return ""

    return (
        first_safe("_original_publication", "publication"),
        first_safe("_original_author", "author"),
        first_safe("_original_date", "original_date"),
    )
