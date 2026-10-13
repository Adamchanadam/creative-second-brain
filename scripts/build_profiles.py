"""Generate profile packages from this public product source only."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
from pathlib import Path
from render_install_guide import render as render_guide
from export_runtime import export_runtime

ROOT = Path(__file__).resolve().parents[1]
PROFILES = json.loads((ROOT / "profiles" / "profiles.json").read_text(encoding="utf-8"))
PACKAGE_SCRIPTS = {
    "brain.py", "package_paths.py", "check_environment.py", "install_skill.py",
    "render_workspace_home.py", "render_install_guide.py", "setup_workspace.py", "manage_workspace.py", "sync_shared_surface.py", "verify_package.py", "verify_delivery.py", "delivery_contract.py",
    "package_inventory.py",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_plain(path: Path) -> None:
    for node in (path, *path.parents):
        try: meta = node.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, "st_file_attributes", 0) & 0x400):
            raise ValueError("profile build paths cannot contain a link or junction")


def assert_plain_tree(path: Path) -> None:
    assert_plain(path)
    if path.is_dir():
        for child in path.rglob("*"):
            assert_plain(child)


def copy_source_package(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for item in ("SKILL.md", "install-prompt.txt", "references", "assets", "profiles", "spec"):
        source = ROOT / item; target = destination / item
        assert_plain_tree(source)
        if source.is_file(): shutil.copyfile(source, target)
        else: shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))
    scripts = ROOT / "scripts"; target_scripts = destination / "scripts"
    assert_plain_tree(scripts); target_scripts.mkdir()
    for name in sorted(PACKAGE_SCRIPTS):
        source = scripts / name
        if not source.is_file(): raise ValueError("required package script is missing: " + name)
        shutil.copyfile(source, target_scripts / name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES["profiles"]), action="append")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(); selected = args.profile or sorted(PROFILES["profiles"])
    raw_output = args.output.absolute(); assert_plain(ROOT); assert_plain(raw_output)
    output = raw_output.resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        raise SystemExit("blocked: build output must not overlap the product source")
    if output.exists() and any(output.iterdir()):
        raise SystemExit("blocked: output must be new or empty; generated profiles are never overwritten")
    plan = {"status":"planned" if args.dry_run else "built", "profiles":selected, "source":"public product source only"}
    if args.dry_run: print(json.dumps(plan, ensure_ascii=False, indent=2)); return 0
    output.mkdir(parents=True, exist_ok=True)
    for profile in selected:
        target = output / profile
        copy_source_package(target)
        export_runtime(target / "runtime")
        render_guide(target, target / "index.html")
        manifest = {"schema":"creative_second_brain_profile_build_v1", "profile":profile,
                    "version":PROFILES["version"], "files":{}}
        for path in target.rglob("*"):
            if path.is_file(): manifest["files"][path.relative_to(target).as_posix()] = digest(path)
        (target / "profile-build.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
