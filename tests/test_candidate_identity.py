"""Run the public acceptance matrix against one immutable built package."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(name: str, command: list[str], *, cwd: Path, evidence: Path) -> dict:
    result = subprocess.run([PYTHON, "-B", *command], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    (evidence / f"{name}.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (evidence / f"{name}.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise AssertionError(f"{name} failed with {result.returncode}; see {name}.stdout.txt and {name}.stderr.txt")
    raw = result.stdout.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    lines = result.stdout.splitlines()
    for index in range(len(lines) - 1, -1, -1):
        if not lines[index].lstrip().startswith("{"):
            continue
        try:
            return json.loads("\n".join(lines[index:]))
        except json.JSONDecodeError:
            continue
    raise AssertionError(f"{name} did not emit a final JSON result")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()
    work = args.work_root.absolute()
    if work.exists():
        raise SystemExit("work root must be new; existing candidate evidence is preserved")
    work.mkdir(parents=True)
    evidence = work / "evidence"; evidence.mkdir()
    build = work / "candidate-build"
    run("build", [str(ROOT / "scripts" / "build_profiles.py"), "--output", str(build), "--profile", "public-portable"], cwd=ROOT, evidence=evidence)
    package = build / "public-portable"
    manifest = package / "profile-build.json"
    manifest_sha = digest(manifest)
    verified = run("verify-before", [str(package / "scripts" / "verify_package.py"), "--package", str(package)], cwd=package, evidence=evidence)
    suites = [
        ("workspace-management", ROOT / "tests" / "test_workspace_management.py"),
        ("shared-surface", ROOT / "tests" / "test_shared_surface_sync.py"),
        ("all-entries", ROOT / "tests" / "test_all_entries.py"),
        ("research-lanes", ROOT / "tests" / "test_research_lanes.py"),
        ("candidate", ROOT / "tests" / "test_candidate.py"),
        ("installation", ROOT / "tests" / "test_installation.py"),
        ("prompt-parity", ROOT / "tests" / "test_prompt_parity.py"),
    ]
    results = {}
    for name, script in suites:
        results[name] = run(name, [str(script), "--work-root", str(work / name), "--package", str(package)], cwd=ROOT, evidence=evidence)
        if digest(manifest) != manifest_sha:
            raise AssertionError("candidate manifest changed during " + name)
    verified_after = run("verify-after", [str(package / "scripts" / "verify_package.py"), "--package", str(package)], cwd=package, evidence=evidence)
    if digest(manifest) != manifest_sha:
        raise AssertionError("candidate manifest changed after validation")
    report = {
        "status": "passed",
        "candidate_manifest_sha256": manifest_sha,
        "candidate_files": verified["files"],
        "same_manifest_before_after": verified == verified_after,
        "suites": {name: value.get("status", "passed") for name, value in results.items()},
        "coverage": ["one built candidate", "transaction and writer gates", "14 workspace entries", "research lane compatibility", "closed-package verification", "private runtime allow-list counterexample"],
    }
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
