"""One shared guard between managed-runtime writers and workspace updates."""
from __future__ import annotations

import json
from pathlib import Path


def assert_management_idle(root: Path) -> None:
    """Fail closed while a v2 manager owns the workspace mutation window.

    The lock is advisory for old installations.  New public writer routes call
    this guard before they create or mutate workspace records.  It never
    removes a lock, and an unreadable lock is treated as occupied.
    """
    lock = root / ".brain-management" / "manager.lock"
    if not lock.exists():
        return
    try:
        value = json.loads(lock.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("workspace management exclusion lock is unreadable; preserve it and retry later") from error
    if not isinstance(value, dict) or value.get("schema") != "brain_management_writer_exclusion_v1":
        raise ValueError("workspace management exclusion lock is unrecognized; preserve it and retry later")
    raise ValueError("workspace manager is applying a transaction; writer route is temporarily blocked")


def assert_management_idle_for_vault(vault: Path) -> None:
    """Reach the workspace manager guard from a directly imported vault writer.

    This does not claim to stop an already-running legacy writer.  It closes
    the direct-call gap for new runtime code before it starts a vault mutation.
    """
    try:
        start = vault.resolve()
    except OSError as error:
        raise ValueError("vault path is unreadable; preserve it and retry later") from error
    for candidate in (start, *start.parents):
        control = candidate / ".brain-management"
        if control.exists():
            assert_management_idle(candidate)
            return
