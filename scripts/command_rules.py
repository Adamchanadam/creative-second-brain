"""Command-registry rules shared by the package scripts (role, entry kind, home menu membership).

Import rule (do not break it):
  An installed updater (1.9.22 to 1.9.25, ``tools/brain_update.py``) first runs the
  workspace's own ``tools/verify_package.py``, which puts the workspace's OLD
  ``package_paths`` and ``package_inventory`` (and, on the archive route,
  ``verify_package`` and ``delivery_contract``) into ``sys.modules``.  It then loads
  this package's scripts by path.  A module that is already in ``sys.modules`` is
  never re-read, so a package script that imports a NEW name from one of those
  modules fails with ImportError during an upgrade.

  Therefore:
  * package scripts may import from ``package_inventory`` and ``package_paths`` only
    the names those older versions already export;
  * any new shared helper lives in a NEW module with a name no older version has
    (this one), never in an existing module;
  * ``verify_package.py`` must not widen its imports (it must not import this module).
  ``tests/test_command_role_rule.py`` guards this statically.
"""
from __future__ import annotations

from typing import Any


COMMAND_ROLES = frozenset({"canonical", "compatibility_alias"})
ENTRY_KINDS = frozenset({"route", "read_only_query"})


def command_role(definition: Any) -> str:
    """The single rule that decides a daily command's role in the inventory.

    A command with ``alias_for`` is a ``compatibility_alias``; every other
    command is ``canonical``.  This formula is frozen: a workspace that already
    has an older updater installed (1.9.22 to 1.9.25) verifies a new package with
    this very formula, so the inventory role must always be derivable from it.
    Anything that is not about alias or canonical (for example a read-only
    query entry) is carried by ``entry_kind``, never by the role.
    """
    if not isinstance(definition, dict):
        raise ValueError("command registry entry is invalid")
    return "compatibility_alias" if definition.get("alias_for") else "canonical"


def entry_kind(definition: Any) -> str:
    """How a command behaves: ``route`` (default) or ``read_only_query``."""
    if not isinstance(definition, dict):
        raise ValueError("command registry entry is invalid")
    if "entry_kind" not in definition:
        return "route"
    kind = definition["entry_kind"]
    if kind not in ENTRY_KINDS:
        raise ValueError("command registry entry has an unknown entry_kind")
    return kind


def is_menu_command(definition: Any) -> bool:
    """The one rule for membership of the home menu, install guide and entry contract."""
    return command_role(definition) == "canonical" and entry_kind(definition) != "read_only_query"
