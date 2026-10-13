"""Narrow, read-only bridge to an explicitly identified Obsidian vault.

This module is intentionally the only owner of native Obsidian CLI calls in
the portable runtime. It never uses the active vault or active file defaults,
and it does not expose the CLI's write or arbitrary-command surface.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
from pathlib import Path, PureWindowsPath


TIMEOUT_SECONDS = 8
_VAULT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_ACTIONS = {"check", "read", "search", "open"}
_REPARSE_POINT = 0x0400


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid Obsidian adapter request: " + message)


def _is_link_or_junction(path: Path) -> bool:
    data = path.lstat()
    return stat.S_ISLNK(data.st_mode) or bool(getattr(data, "st_file_attributes", 0) & _REPARSE_POINT)


def _assert_plain_path(path: Path, *, directory: bool = False, file: bool = False) -> Path:
    """Reject links and junctions in a required existing path and its parents."""
    if not path.exists():
        raise ValueError("required Obsidian path is unavailable")
    absolute = path.absolute()
    for candidate in (absolute, *absolute.parents):
        if _is_link_or_junction(candidate):
            raise ValueError("Obsidian paths must not use links or junctions")
    resolved = absolute.resolve(strict=True)
    if directory and not resolved.is_dir():
        raise ValueError("configured Obsidian vault is not a directory")
    if file and not resolved.is_file():
        raise ValueError("requested Obsidian note is not a file")
    return resolved


def _safe_note(vault: Path, value: str) -> tuple[Path, str]:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("note path is required")
    raw = value.replace("\\", "/")
    lexical = Path(raw)
    windows = PureWindowsPath(raw)
    if (not lexical.parts or raw.startswith("/") or lexical.is_absolute() or windows.is_absolute() or windows.drive
            or any(part in {"", ".", ".."} for part in lexical.parts)
            or lexical.parts[0].casefold() == ".obsidian"):
        raise ValueError("note path must be a safe vault-relative file")
    target = vault / lexical
    note = _assert_plain_path(target, file=True)
    if not note.is_relative_to(vault):
        raise ValueError("note path escapes the configured vault")
    return note, lexical.as_posix()


def _safe_query(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value or any(ord(char) < 32 for char in value):
        raise ValueError("search query must be non-empty plain text")
    if len(value) > 256:
        raise ValueError("search query is too long")
    return value


def _safe_vault_id(value: str) -> str:
    if not isinstance(value, str) or _VAULT_ID.fullmatch(value) is None:
        raise ValueError("Obsidian vault ID must be an explicit safe ID, not a vault name")
    return value


def _safe_cli(value: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Obsidian CLI path is required")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("Obsidian CLI path must be explicit")
    return _assert_plain_path(path, file=True)


def _invoke(cli: Path, vault_id: str, arguments: list[str], *, allow_empty: bool = False,
            preserve_output: bool = False) -> str:
    try:
        result = subprocess.run(
            [str(cli), f"vault={vault_id}", *arguments],
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("Obsidian CLI timed out") from error
    except (FileNotFoundError, PermissionError, OSError) as error:
        raise ValueError("Obsidian CLI is unavailable") from error
    if result.returncode != 0 or result.stderr.strip():
        raise ValueError("Obsidian CLI rejected the requested operation")
    output = result.stdout
    if not allow_empty and not output.strip():
        raise ValueError("Obsidian CLI returned no output")
    return output if preserve_output else output.strip()


def _reported_vault_path(output: str) -> Path:
    lines = [line.strip().strip('"') for line in output.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ValueError("Obsidian CLI did not return one vault path")
    path = Path(lines[0])
    if not path.is_absolute():
        raise ValueError("Obsidian CLI did not return an absolute vault path")
    return _assert_plain_path(path, directory=True)


def _verify_target(cli: Path, vault_id: str, vault: Path) -> None:
    expected = _assert_plain_path(vault, directory=True)
    reported = _reported_vault_path(_invoke(cli, vault_id, ["vault", "info=path"]))
    if os.path.normcase(str(reported)) != os.path.normcase(str(expected)):
        raise ValueError("Obsidian vault ID does not match this workspace vault")


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(add_help=False)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--vault-id", required=True)
    parser.add_argument("action", choices=sorted(_ACTIONS))
    parser.add_argument("--path")
    parser.add_argument("--query")
    return parser


def execute(arguments: list[str], *, vault: Path) -> dict:
    """Run one fixed, read-only operation after verifying the exact vault."""
    request = _parser().parse_args(arguments)
    cli = _safe_cli(request.cli)
    vault_id = _safe_vault_id(request.vault_id)
    configured_vault = _assert_plain_path(vault, directory=True)
    _verify_target(cli, vault_id, configured_vault)
    base = {"action": request.action, "vault_target_verified": True, "writes": 0}

    if request.action == "check":
        if request.path is not None or request.query is not None:
            raise ValueError("check does not accept note paths or search queries")
        return {"status": "ready", **base}
    if request.action in {"read", "open"}:
        if request.query is not None:
            raise ValueError(request.action + " does not accept a search query")
        note, relative = _safe_note(configured_vault, request.path)
        operation = [request.action, "path=" + relative]
        if request.action == "open":
            operation.append("newtab")
        output = _invoke(cli, vault_id, operation, allow_empty=request.action == "read",
                         preserve_output=request.action == "read")
        if request.action == "open":
            return {"status": "requested", **base, "ui_verified": False}
        if output != note.read_text(encoding="utf-8"):
            raise ValueError("Obsidian CLI did not return the requested note")
        return {"status": "ready", **base, "content": output}
    if request.path is not None:
        raise ValueError("search does not accept a note path")
    query = _safe_query(request.query)
    try:
        matches = json.loads(_invoke(cli, vault_id, ["search", "query=" + query, "format=json"]))
    except json.JSONDecodeError as error:
        raise ValueError("Obsidian CLI search did not return JSON") from error
    if not isinstance(matches, list):
        raise ValueError("Obsidian CLI search returned an invalid JSON result")
    return {"status": "ready", **base, "matches": matches}
