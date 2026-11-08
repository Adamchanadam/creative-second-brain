"""One portable resolver for the configured workspace vault.

Runtime tools must not independently reinterpret ``CREATIVE_SECOND_BRAIN_VAULT``.
The installer permits a nested, named relative vault path, so this helper keeps
that behaviour while rejecting a drive, an absolute path or traversal.

Which vault a workspace uses is always asked of a named workspace root
(``configured_*``).  A few helpers build vault-relative text where no root is in
scope; they follow the process environment.  So each directly runnable tool calls
:func:`bind_process_vault` once, with the root it works on, before it does
anything else: that checks the root's ``brain.json`` against the environment and
then exports the declared vault to this process and the processes it starts.
"""
from __future__ import annotations

import os
import json
from pathlib import Path, PureWindowsPath


DEFAULT_VAULT = "knowledge-base-vault"
_ENV_VAULT = "CREATIVE_SECOND_BRAIN_VAULT"


class WorkspacePathError(ValueError):
    pass


def vault_relative_path(value: str | None = None) -> Path:
    """Validate a vault path; without a value, the environment's vault, else the default name.

    No workspace is named here, so ``brain.json`` is not consulted.  A directly
    run tool has already made the environment agree with its workspace
    (:func:`bind_process_vault`).
    """
    if value is None:
        return _legacy_default_relative_path()
    return _checked_relative_path(value)


def _legacy_default_relative_path() -> Path:
    # A root without brain.json (not installed, or a scratch root) keeps the
    # historical rule: the environment variable, else the default name.
    return _checked_relative_path(os.environ.get(_ENV_VAULT, DEFAULT_VAULT))


def _checked_relative_path(value: str) -> Path:
    raw = value.strip()
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
    """Resolve a vault inside ``root``; without a value, ``root``'s configured vault."""
    if value is None:
        return configured_vault_path(root)
    return _contained_vault_path(Path(root).resolve(), _checked_relative_path(value))


def _contained_vault_path(workspace: Path, relative: Path) -> Path:
    target = (workspace / relative).resolve(strict=False)
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise WorkspacePathError("configured vault escapes this workspace") from exc
    return target


def _declared_vault(workspace: Path) -> str:
    try:
        config = json.loads((workspace / "brain.json").read_text(encoding="utf-8-sig"))
        declared = config.get("vault") if isinstance(config, dict) else None
        if not isinstance(declared, str):
            raise WorkspacePathError("managed workspace has no valid vault setting")
        return declared
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, WorkspacePathError):
            raise
        raise WorkspacePathError("managed workspace vault setting is unreadable") from exc


def managed_vault_path(root: Path) -> Path:
    """Resolve the vault declared by this installed workspace's brain.json."""
    workspace = Path(root).resolve()
    try:
        return vault_path(workspace, _declared_vault(workspace))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, WorkspacePathError):
            raise
        raise WorkspacePathError("managed workspace vault setting is unreadable") from exc


def _has_managed_marker(workspace: Path) -> bool:
    try:
        (workspace / "brain.json").lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise WorkspacePathError("managed workspace vault marker cannot be confirmed") from exc
    return True


def configured_vault_relative_path(root: Path) -> Path:
    """The one rule for which vault a workspace uses (relative form).

    Installed (``root/brain.json`` present): the vault declared there.  An
    environment value that resolves elsewhere is refused, never silently
    preferred.  Not installed (no ``brain.json``): the environment value, else
    the default name.  An unreadable or malformed ``brain.json`` is an error.
    The workspace is always the caller's ``root``.
    """
    workspace = Path(root).resolve()
    if not _has_managed_marker(workspace):
        return _legacy_default_relative_path()
    declared = _checked_relative_path(_declared_vault(workspace))
    managed = _contained_vault_path(workspace, declared)
    env = os.environ.get(_ENV_VAULT)
    if env is not None and env.strip():
        try:
            from_env = _contained_vault_path(workspace, _checked_relative_path(env))
        except WorkspacePathError as exc:
            raise WorkspacePathError(
                f"{_ENV_VAULT} ({env!r}) is not usable and differs from this workspace's brain.json vault ({declared.as_posix()!r})"
            ) from exc
        if from_env != managed:
            raise WorkspacePathError(
                f"{_ENV_VAULT} ({env!r}) differs from this workspace's brain.json vault ({declared.as_posix()!r})"
            )
    return declared


def configured_vault_path(root: Path) -> Path:
    """Absolute form of :func:`configured_vault_relative_path`."""
    workspace = Path(root).resolve()
    return _contained_vault_path(workspace, configured_vault_relative_path(workspace))


def bind_process_vault(root: Path) -> Path:
    """Make this process, and the processes it starts, agree with ``root``'s vault; return it (relative).

    Call it once, with the root the tool works on, before anything reads a
    vault.  The root's rule is :func:`configured_vault_relative_path`, so an
    environment value that disagrees with ``brain.json`` is refused here, before
    any write.  Only an installed root (one with ``brain.json``) exports its
    vault; a root without it leaves the environment as it was.  A process that
    has bound one installed root refuses a second whose vault differs.
    """
    workspace = Path(root).resolve()
    relative = configured_vault_relative_path(workspace)
    if _has_managed_marker(workspace):
        os.environ[_ENV_VAULT] = relative.as_posix()
    return relative


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


def configured_vault_relative_posix(root: Path) -> str:
    return configured_vault_relative_path(root).as_posix()
