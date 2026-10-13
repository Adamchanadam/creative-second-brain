"""Canonical public-package ownership and runtime allow-list contract."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from package_paths import assert_plain_path


INVENTORY_SCHEMA = "creative_second_brain_package_inventory_v1"
RUNTIME_PREFIX = "workspace/"
HOME_ALIAS_RELATIVE = "workspace/assets/guide/command-aliases.json"


def compiled_home_alias_metadata(root: Path) -> bytes:
    """Compile the one legacy home alias from the canonical command registry."""
    try:
        registry = json.loads((root / "spec" / "command-registry.json").read_text(encoding="utf-8"))
        alias = registry["commands"]["ask"]
        values = {key: alias[key] for key in ("home_title", "host_prompt", "purpose")}
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("command registry alias metadata is unreadable") from error
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ValueError("command registry alias metadata is invalid")
    payload = {"schema": "creative_second_brain_home_aliases_v1", "aliases": {"think": values}}
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def validate_compiled_home_alias_metadata(root: Path) -> None:
    path = root / "runtime" / HOME_ALIAS_RELATIVE
    assert_plain_path(path)
    try:
        actual = path.read_bytes()
    except OSError as error:
        raise ValueError("compiled home alias metadata is missing") from error
    if actual != compiled_home_alias_metadata(root):
        raise ValueError("compiled home alias metadata does not match command registry")


def digest(path: Path) -> str:
    assert_plain_path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_inventory(root: Path) -> dict[str, Any]:
    path = root / "spec" / "package-inventory.json"
    assert_plain_path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("package ownership inventory is unreadable") from error
    if not isinstance(value, dict) or value.get("schema") != INVENTORY_SCHEMA:
        raise ValueError("package ownership inventory is invalid")
    if value.get("package") != "creative-second-brain" or value.get("profile") != "public-portable":
        raise ValueError("package ownership inventory has the wrong product identity")
    return value


def runtime_allowlist(inventory: dict[str, Any]) -> tuple[str, ...]:
    runtime = inventory.get("runtime")
    if not isinstance(runtime, dict) or runtime.get("source_root") != "runtime":
        raise ValueError("package inventory does not define the public runtime")
    allowlist = runtime.get("public_runtime_allowlist")
    if not isinstance(allowlist, list) or not allowlist:
        raise ValueError("package inventory lacks a public runtime allow-list")
    result: list[str] = []
    for item in allowlist:
        if not isinstance(item, str) or not item.startswith(RUNTIME_PREFIX):
            raise ValueError("public runtime allow-list has an unsafe member")
        relative = PurePosixPath(item)
        if relative.is_absolute() or ".." in relative.parts or "\\" in item or ":" in item:
            raise ValueError("public runtime allow-list has an unsafe member")
        if item not in result:
            result.append(item)
    if not any(item == "workspace/requirements.txt" for item in result):
        raise ValueError("public runtime allow-list omits requirements")
    return tuple(result)


def is_runtime_member_allowed(relative: str, allowlist: tuple[str, ...]) -> bool:
    return any(relative == item or (item.endswith("/") and relative.startswith(item)) for item in allowlist)


def listed_runtime_files(runtime_root: Path, inventory: dict[str, Any]) -> list[str]:
    """Return exactly the public runtime files; fail if source has an unlisted one."""
    assert_plain_path(runtime_root)
    allowlist = runtime_allowlist(inventory)
    files: list[str] = []
    for path in sorted(runtime_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        assert_plain_path(path)
        if not path.is_file():
            continue
        relative = path.relative_to(runtime_root).as_posix()
        if "__pycache__" in PurePosixPath(relative).parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if relative == "snapshot-provenance.json":
            continue
        if not is_runtime_member_allowed(relative, allowlist):
            raise ValueError("public runtime contains an unlisted member: " + relative)
        files.append(relative)
    if not files or "workspace/requirements.txt" not in files:
        raise ValueError("public runtime is incomplete")
    return files


def validate_provenance(root: Path, inventory: dict[str, Any], provenance: dict[str, Any]) -> list[str]:
    if provenance.get("schema") != "creative_second_brain_runtime_core_v1":
        raise ValueError("runtime provenance is invalid")
    if provenance.get("ownership_inventory_sha256") != digest(root / "spec" / "package-inventory.json"):
        raise ValueError("runtime provenance does not bind the ownership inventory")
    validate_compiled_home_alias_metadata(root)
    runtime_root = root / "runtime"
    expected = listed_runtime_files(runtime_root, inventory)
    recorded = provenance.get("runtime_files")
    if not isinstance(recorded, list) or recorded != expected:
        raise ValueError("runtime provenance does not match the public allow-list")
    source_hashes = provenance.get("source_hashes")
    if not isinstance(source_hashes, dict) or set(source_hashes) != set(expected):
        raise ValueError("runtime provenance hashes are incomplete")
    for relative in expected:
        actual = digest(runtime_root / relative)
        if source_hashes.get(relative) != actual:
            raise ValueError("runtime provenance hash mismatch: " + relative)
    return expected
