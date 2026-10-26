"""One source-preview selection rule for reader surfaces.

The rule deliberately reads only declared, in-vault image files.  It never
discovers media by crawling asset folders, so a missing or malformed metadata
entry simply has no preview.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


@dataclass(frozen=True)
class SourcePreview:
    path: Path
    kind: str


def source_preview_files(
    vault: Path,
    source_fields: Mapping[str, object],
    app_fields: Mapping[str, object] | None = None,
) -> list[SourcePreview]:
    """Return the declared source previews in their one reader-facing order.

    ``app_fields`` remains an accepted argument so callers can pass the two
    note records they already hold.  The current, authoritative rule has no
    application-note PNG fallback: only source-owned images are previews.
    """
    del app_fields
    vault = vault.resolve()
    case_evidence = str(source_fields.get("asset_mode") or "").strip() == "case_evidence"
    ordered = (
        (("evidence_screenshots", "案例截圖") if case_evidence else ("infographics", "資訊圖")),
        ("source_assets", "原始圖片"),
        ("source_attachments", "來源附圖"),
    )
    previews: list[SourcePreview] = []
    seen: set[Path] = set()
    for field, kind in ordered:
        for relative in string_list(source_fields.get(field)):
            candidate = safe_image_file(vault, relative)
            if candidate is None or candidate in seen:
                continue
            seen.add(candidate)
            previews.append(SourcePreview(path=candidate, kind=kind))
    return previews


def first_source_preview(
    vault: Path,
    source_fields: Mapping[str, object],
    app_fields: Mapping[str, object] | None = None,
) -> Path | None:
    previews = source_preview_files(vault, source_fields, app_fields)
    return previews[0].path if previews else None


def string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if isinstance(value, str) and value.strip() and value.strip() != "[]":
        return [value.strip()]
    return []


def safe_image_file(vault: Path, relative: str) -> Path | None:
    raw = relative.replace("\\", "/")
    portable = Path(raw)
    if not raw or portable.is_absolute() or ":" in raw or ".." in portable.parts:
        return None
    try:
        candidate = (vault / portable).resolve()
    except OSError:
        return None
    if candidate == vault or vault not in candidate.parents:
        return None
    if candidate.suffix.lower() not in IMAGE_SUFFIXES or not candidate.is_file():
        return None
    return candidate
