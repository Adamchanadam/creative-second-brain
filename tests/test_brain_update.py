"""Exercise the offline upgrade guard against the last pre-updater release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
LEGACY = ROOT.parents[1] / "releases" / "1.9.15" / "creative-second-brain"


def digest_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".brain-management" not in path.relative_to(root).parts and "__pycache__" not in path.parts
    }


def run(command: list[str], *, cwd: Path, expected: int = 0) -> dict:
    env = dict(os.environ)
    env["BRAIN_UPDATE_TEMP_ROOT"] = str(cwd)
    result = subprocess.run([PYTHON, "-B", *command], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env)
    if result.returncode != expected:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def setup(work: Path, legacy: Path, name: str) -> Path:
    workspace = work / name
    run([str(legacy / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(workspace), "--vault", "My Notes", "--display-name", name], cwd=work)
    for relative, content in {
        "inputs/sentinel.txt": "input", "outputs/sentinel.txt": "output", "receipts/sentinel.txt": "receipt", "registry/sentinel.txt": "registry",
        "My Notes/private-note.md": "# private", "My Notes/.obsidian/appearance.json": "{\"private\":true}",
    }.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return workspace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--legacy-package", type=Path, default=LEGACY)
    args = parser.parse_args()
    work = args.work_root.absolute()
    package, legacy = args.package.absolute(), args.legacy_package.absolute()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    if not legacy.is_dir():
        raise SystemExit("verified 1.9.15 legacy package is required")
    work.mkdir(parents=True, exist_ok=True)
    updater = package / "runtime" / "workspace" / "tools" / "brain_update.py"
    candidate_version = json.loads((package / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]

    workspace = setup(work, legacy, "upgrade")
    before = digest_tree(workspace)
    checked = run([str(updater), "--workspace", str(workspace), "--package", str(package), "--check"], cwd=work)
    assert checked["status"] == "checked" and checked["candidate_version"] == candidate_version and digest_tree(workspace) == before
    updated = run([str(updater), "--workspace", str(workspace), "--package", str(package), "--apply"], cwd=work)
    assert updated["status"] == "updated" and updated["manager_status"] == "ready" and updated["doctor"] == "ready"
    private = {path: value for path, value in digest_tree(workspace).items() if path.startswith(("inputs/", "outputs/", "receipts/", "registry/", "My Notes/"))}
    assert private == {path: value for path, value in before.items() if path.startswith(("inputs/", "outputs/", "receipts/", "registry/", "My Notes/"))}
    no_op = run([str(workspace / "tools" / "brain_update.py"), "--workspace", str(workspace), "--package", str(package), "--apply"], cwd=work)
    assert no_op["status"] == "up_to_date" and no_op["writes"] == 0

    downgrade_before = digest_tree(workspace)
    downgrade = run([str(workspace / "tools" / "brain_update.py"), "--workspace", str(workspace), "--package", str(legacy), "--check"], cwd=work, expected=2)
    assert downgrade["status"] == "blocked" and digest_tree(workspace) == downgrade_before

    conflict = setup(work, legacy, "conflict")
    (conflict / "tools" / "brain.py").write_text("changed", encoding="utf-8")
    conflict_before = digest_tree(conflict)
    blocked = run([str(updater), "--workspace", str(conflict), "--package", str(package), "--check"], cwd=work, expected=2)
    assert blocked["status"] == "blocked" and digest_tree(conflict) == conflict_before

    fake = work / "fake-package"; (fake / "profiles").mkdir(parents=True); (fake / "scripts").mkdir()
    (fake / "profiles" / "profiles.json").write_text('{"version":"1.9.18"}', encoding="utf-8")
    marker = work / "candidate-code-executed.txt"
    (fake / "scripts" / "verify_package.py").write_text("from pathlib import Path\nPath(r'" + str(marker).replace("\\", "\\\\") + "').write_text('executed')\n", encoding="utf-8")
    fake_before = digest_tree(conflict)
    rejected = run([str(updater), "--workspace", str(conflict), "--package", str(fake), "--check"], cwd=work, expected=2)
    assert rejected["status"] == "blocked" and not marker.exists() and digest_tree(conflict) == fake_before

    interrupted = setup(work, legacy, "interrupted")
    interrupted_before = digest_tree(interrupted)
    stopped = run([str(updater), "--workspace", str(interrupted), "--package", str(package), "--apply", "--test-interrupt", "after_target"], cwd=work, expected=2)
    assert stopped["status"] == "interrupted" and isinstance(stopped["transaction_id"], str) and isinstance(stopped["recovery_argv"], list)
    restored = run(stopped["recovery_argv"][2:], cwd=work)
    assert restored["status"] == "rolled_back" and digest_tree(interrupted) == interrupted_before

    print(json.dumps({"status": "passed", "coverage": ["1.9.15 offline bootstrap", "private sentinel preservation", "same-version no-op", "downgrade zero-write", "modified runtime conflict zero-write", "untrusted candidate code never executes", "interrupted apply rollback restores original bytes"], "not_proven": ["live GitHub release download", "POSIX filesystem lock" ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
