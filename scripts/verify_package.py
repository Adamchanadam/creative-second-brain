"""Check a built skill package against its manifest; integrity is not provenance."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from package_paths import assert_plain_path
from package_inventory import load_inventory, validate_provenance

SCHEMA = "creative_second_brain_profile_build_v1"
REQUIRED = {"SKILL.md", "index.html", "install-prompt.txt", "references/first-use.md",
            "scripts/install_skill.py", "scripts/verify_package.py", "scripts/verify_delivery.py", "scripts/delivery_contract.py", "scripts/check_environment.py",
            "scripts/setup_workspace.py", "scripts/manage_workspace.py", "scripts/sync_shared_surface.py", "scripts/package_paths.py", "scripts/brain.py",
            "scripts/package_inventory.py",
            "scripts/render_workspace_home.py", "scripts/render_install_guide.py",
            "assets/install-guide-template.html", "profiles/profiles.json", "spec/command-registry.json",
            "spec/ownership.md", "spec/package-inventory.json", "spec/update-source.json", "runtime/snapshot-provenance.json", "runtime/workspace/requirements.txt"}


def verify(root: Path) -> dict:
    root = root.absolute()
    assert_plain_path(root)
    marker = root / "profile-build.json"
    assert_plain_path(marker)
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    profile_file = root / "profiles" / "profiles.json"
    if not profile_file.is_file():
        raise ValueError("package profile is missing")
    expected_version = json.loads(profile_file.read_text(encoding="utf-8")).get("version")
    if not isinstance(expected_version, str) or not expected_version:
        raise ValueError("package profile version is invalid")
    if (manifest.get("schema") != SCHEMA or manifest.get("profile") != "public-portable"
            or manifest.get("version") != expected_version):
        raise ValueError("unsupported package manifest")
    files = manifest.get("files")
    if not isinstance(files, dict) or not REQUIRED.issubset(files):
        raise ValueError("package manifest is incomplete")
    seen = set()
    for relative, expected in files.items():
        rel = PurePosixPath(relative)
        if (not relative or rel.is_absolute() or PureWindowsPath(relative).drive
                or ".." in rel.parts or "\\" in relative or rel.as_posix() != relative
                or relative == "profile-build.json" or ":" in relative):
            raise ValueError("unsafe package entry")
        if relative.casefold() in seen:
            raise ValueError("case-colliding package entries")
        seen.add(relative.casefold())
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("invalid package hash")
        path = root / relative
        assert_plain_path(path)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("missing or changed package file: " + relative)
    actual = set()
    for path in root.rglob("*"):
        assert_plain_path(path)
        if path.is_file():
            relative = path.relative_to(root)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            actual.add(relative.as_posix())
    if actual != set(files) | {"profile-build.json"}:
        raise ValueError("unlisted package files")
    # The exported runtime owns package membership; do not derive completeness
    # from whichever files happened to survive packaging.
    inventory_path = root / "spec" / "package-inventory.json"
    inventory = load_inventory(root)
    contract = inventory.get("daily_entry_contract") if isinstance(inventory, dict) else None
    private = inventory.get("private_exclusions") if isinstance(inventory, dict) else None
    try:
        registry = json.loads((root / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError("command registry is unreadable") from error
    expected_entries = {
        key: "compatibility_alias" if definition.get("alias_for") else "canonical"
        for key, definition in registry.items()
        if isinstance(definition, dict) and isinstance(definition.get("menu_order"), int)
    }
    actual_entries = {
        item.get("command"): item.get("role")
        for item in contract["managed_entries"]
        if isinstance(item, dict)
    }
    if (
        inventory.get("schema") != "creative_second_brain_package_inventory_v1"
        or inventory.get("package") != "creative-second-brain"
        or inventory.get("profile") != "public-portable"
        or not isinstance(contract, dict)
        or not isinstance(contract.get("managed_entries"), list)
        or len(actual_entries) != len(contract["managed_entries"])
        or any(
            not isinstance(item, dict)
            or not isinstance(item.get("command"), str)
            or not isinstance(item.get("skill"), str)
            or not item["skill"].startswith("brain-")
            for item in contract["managed_entries"]
        )
        or len({item["skill"] for item in contract["managed_entries"]}) != len(contract["managed_entries"])
        or actual_entries != expected_entries
        or contract.get("package_only_skill") != "creative-second-brain"
        or not isinstance(private, list)
    ):
        raise ValueError("package ownership inventory is invalid")
    try:
        provenance = json.loads((root / "runtime/snapshot-provenance.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("runtime provenance is unreadable") from error
    if not isinstance(provenance, dict):
        raise ValueError("runtime provenance is invalid")
    runtime_files = validate_provenance(root, inventory, provenance)
    runtime_required = {"runtime/" + entry for entry in runtime_files}
    if not runtime_required.issubset(files):
        raise ValueError("package omits required public runtime files")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        manifest = verify(args.package)
        print(json.dumps({"status": "verified", "profile": manifest["profile"],
                          "files": len(manifest["files"]), "authenticity": "not established by hashes"}))
        return 0
    except (ValueError, OSError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
