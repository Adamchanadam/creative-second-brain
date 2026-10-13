"""One portable resolver for the configured workspace vault.

Runtime tools must not independently reinterpret ``CREATIVE_SECOND_BRAIN_VAULT``.
The installer permits a nested, named relative vault path, so this helper keeps
that behaviour while rejecting a drive, an absolute path or traversal.
"""
from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath


DEFAULT_VAULT = "knowledge-base-vault"


class WorkspacePathError(ValueError):
    pass


def vault_relative_path(value: str | None = None) -> Path:
    raw = (value if value is not None else os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", DEFAULT_VAULT)).strip()
    normalized = raw.replace("\\", "/")
    relative = Path(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        # A slash-rooted value can look relative to a non-Windows Python
        # runtime while still being absolute when this portable package is run
        # on Windows.  Reject both path dialects before joining it to root.
        or normalized.startswith("/")
        or relative.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive
        or ".." in relative.parts
        or relative == Path(".")
    ):
        raise WorkspacePathError("configured vault must be a named relative path inside this workspace")
    return relative


def vault_path(root: Path, value: str | None = None) -> Path:
    workspace = root.resolve()
    target = (workspace / vault_relative_path(value)).resolve(strict=False)
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise WorkspacePathError("configured vault escapes this workspace") from exc
    return target


def vault_relative_posix(value: str | None = None) -> str:
    return vault_relative_path(value).as_posix()
