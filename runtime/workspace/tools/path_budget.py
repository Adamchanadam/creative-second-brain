"""Shared Windows-safe path budget for generated local ingest outputs.

The repository keeps generated paths below the legacy 256-character Windows
limit. Atomic writers must validate their actual temporary filename as well as
the final filename, so a valid final path cannot fail only during replacement.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


WINDOWS_PATH_LIMIT = 256
ATOMIC_WRITE_HEADROOM = 40
MAX_GENERATED_PATH_CHARS = WINDOWS_PATH_LIMIT
MAX_OUTPUT_SLUG_CHARS = 40
MAX_OUTPUT_SOURCE_ID_CHARS = 20


class PathBudgetError(ValueError):
    """A generated path would not be safely writable on Windows."""


def bounded_slug(value: str, *, fallback: str = "source") -> str:
    """Return a stable, traceable filename slug within the shared budget."""

    candidate = str(value).strip() or fallback
    if len(candidate) <= MAX_OUTPUT_SLUG_CHARS:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
    prefix_length = MAX_OUTPUT_SLUG_CHARS - len(digest) - 1
    prefix = candidate[:prefix_length].rstrip("_.-")
    return f"{prefix}_{digest}" if prefix else digest


def require_output_source_id(value: str, *, label: str = "source_id") -> str:
    """Reject an overlong output identifier rather than silently changing it."""

    candidate = str(value).strip()
    if len(candidate) > MAX_OUTPUT_SOURCE_ID_CHARS:
        raise PathBudgetError(
            f"{label} exceeds the {MAX_OUTPUT_SOURCE_ID_CHARS}-character generated-output limit"
        )
    return candidate


def assert_generated_path(path: Path, *, label: str, reserve: int = 0) -> Path:
    """Ensure a generated path, plus any specified suffix, fits the budget."""

    resolved = Path(path).resolve(strict=False)
    path_length = len(str(resolved))
    if path_length + reserve > WINDOWS_PATH_LIMIT:
        raise PathBudgetError(
            f"{label} exceeds the Windows {WINDOWS_PATH_LIMIT}-character path limit "
            f"after reserving {reserve} temporary-write characters "
            f"({path_length} + {reserve})"
        )
    return resolved
