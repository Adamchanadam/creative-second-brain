"""One portable resolver for the configured workspace vault.

Runtime tools must not independently reinterpret ``CREATIVE_SECOND_BRAIN_VAULT``.
The installer permits a nested, named relative vault path, so this helper keeps
that behaviour while rejecting a drive, an absolute path or traversal.
"""
from __future__ import annotations

import os
import json
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


def managed_vault_path(root: Path) -> Path:
    """Resolve the vault declared by this installed workspace's brain.json."""
    workspace = Path(root).resolve()
    try:
        config = json.loads((workspace / "brain.json").read_text(encoding="utf-8-sig"))
        declared = config.get("vault") if isinstance(config, dict) else None
        if not isinstance(declared, str):
            raise WorkspacePathError("managed workspace has no valid vault setting")
        return vault_path(workspace, declared)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, WorkspacePathError):
            raise
        raise WorkspacePathError("managed workspace vault setting is unreadable") from exc


def bound_vault_path(root: Path, vault: Path) -> Path:
    """Bind a caller-selected vault to its already selected workspace root.

    Installed workspaces bind through ``brain.json``.  Before installation,
    callers may explicitly select any vault that remains inside that root;
    there is no managed setting to compare it with.  A present or uncertain
    marker never becomes that pre-install case, so malformed managed state
    cannot silently fall back to an environment default.
    """
    workspace = Path(root).resolve()
    marker = workspace / "brain.json"
    try:
        marker.lstat()
    except FileNotFoundError:
        selected = Path(vault).resolve()
        try:
            selected.relative_to(workspace)
        except ValueError as exc:
            raise WorkspacePathError("selected vault escapes the explicit workspace root") from exc
        return selected
    except OSError as exc:
        raise WorkspacePathError("managed workspace vault marker cannot be confirmed") from exc

    expected = managed_vault_path(workspace)
    if Path(vault).resolve() != expected:
        raise WorkspacePathError("selected vault does not match the explicit workspace root")
    return expected


def vault_relative_posix(value: str | None = None) -> str:
    return vault_relative_path(value).as_posix()
