"""Stage the editable portable runtime from this product source only."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from package_paths import assert_plain_path
from package_inventory import digest, listed_runtime_files, load_inventory, validate_compiled_home_alias_metadata


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
INVENTORY_PATH = ROOT / "spec" / "package-inventory.json"
WORKSPACE_VERIFICATION_COPIES = {
    "workspace/tools/delivery_contract.py": ROOT / "scripts" / "delivery_contract.py",
    "workspace/tools/package_inventory.py": ROOT / "scripts" / "package_inventory.py",
    "workspace/tools/package_paths.py": ROOT / "scripts" / "package_paths.py",
    "workspace/tools/verify_package.py": ROOT / "scripts" / "verify_package.py",
    "workspace/tools/update-source.json": ROOT / "spec" / "update-source.json",
}


def export_runtime(destination: Path) -> dict:
    if destination.exists():
        raise ValueError("runtime destination already exists; preserve it and use a new build folder")
    if not (RUNTIME / "workspace").is_dir():
        raise ValueError("public runtime core is missing")

    inventory = load_inventory(ROOT)
    validate_compiled_home_alias_metadata(ROOT)
    # Copy only files selected by the canonical public allow-list.  A rebuilt
    # manifest or provenance file cannot turn an arbitrary runtime subtree
    # into a delivery member because this source-side selection never walks it.
    listed = listed_runtime_files(RUNTIME, inventory)
    if set(WORKSPACE_VERIFICATION_COPIES).intersection(listed):
        raise ValueError("workspace verification copy duplicates a runtime source member")
    destination.mkdir(parents=True, exist_ok=False)
    for relative in listed:
        source = RUNTIME / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for relative, source in WORKSPACE_VERIFICATION_COPIES.items():
        assert_plain_path(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    exported = sorted([*listed, *WORKSPACE_VERIFICATION_COPIES], key=str.casefold)
    provenance = {
        "schema": "creative_second_brain_runtime_core_v1",
        "role": "editable public product core",
        "runtime_files": exported,
        "source_hashes": {relative: digest(destination / relative) for relative in exported},
        "ownership_inventory_sha256": digest(INVENTORY_PATH),
        "boundary": "Builds copy this source-local core and do not read another workspace.",
    }
    (destination / "snapshot-provenance.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export_runtime(args.output)
        print(json.dumps({"status": "exported", "files": len(result["runtime_files"])}, ensure_ascii=False))
        return 0
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
