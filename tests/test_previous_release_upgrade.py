"""Update a real previous-release workspace that holds a formally completed source.

The previous release installs the workspace and completes one synthetic text
source through its own ingest rail.  The old workspace's own updater then
installs the candidate, as /brain-update does.  The update must leave every
private file byte-identical, and the candidate code must then read, refresh
and serve that old entry.  Run offline with the dedicated acceptance Python:

    python -B tests/test_previous_release_upgrade.py --work-root <new short dir>
        --legacy-package <PUBLIC>/releases/<previous>/creative-second-brain --package <candidate>/creative-second-brain
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
SAMPLE_TITLE = "番茄工作法"
SAMPLE_TEXT = "# 番茄工作法\n\n番茄工作法把工作切成二十五分鐘的專注時段，每段之後休息五分鐘。\n\n連續完成四段之後，可以休息較長的時間。\n"
KNOWLEDGE_TITLE = "二十五分鐘專注加五分鐘休息"
READER_ENVIRONMENT = {"CREATIVE_SECOND_BRAIN_READER_IDLE_SECONDS": "30"}


def run(command: list[str], *, cwd: Path, expected: int = 0, environment: dict[str, str] | None = None) -> dict:
    env = dict(os.environ)
    env["BRAIN_UPDATE_TEMP_ROOT"] = str(cwd)
    env.update(environment or {})
    result = subprocess.run([PYTHON, "-B", *command], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, env=env, timeout=300)
    if result.returncode != expected:
        raise AssertionError(" ".join(command) + "\n" + result.stdout + result.stderr)
    return json.loads(result.stdout)


def version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def private_files(workspace: Path, scopes: list[str]) -> dict[str, str]:
    """Hash every file inside the private scopes the installed workspace declares."""
    result = {}
    for scope in scopes:
        base = workspace / scope.rstrip("/")
        if base.is_dir():
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    result[path.relative_to(workspace).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def assert_private_unchanged(before: dict[str, str], after: dict[str, str]) -> None:
    changed = sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))
    if changed:
        raise AssertionError("private files changed during update: " + ", ".join(changed))


def setup_previous(work: Path, legacy: Path) -> Path:
    workspace = work / "workspace"
    run([str(legacy / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(workspace),
         "--vault", "My Notes", "--display-name", "Previous release update"], cwd=work)
    for relative, content in {"My Notes/私人筆記/sentinel.md": "# 私人測試筆記\n\n更新不得改動。\n",
                              "inputs/private-sentinel.txt": "private sentinel\n"}.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return workspace


def complete_sample(workspace: Path) -> dict[str, str]:
    """Complete one synthetic source through the previous release's own rail entry."""
    brain = str(workspace / "tools" / "brain.py")
    sample = workspace / "inputs" / "previous-release-sample.md"
    sample.write_text(SAMPLE_TEXT, encoding="utf-8")
    order = run([brain, "rail", "start", "--text-file", "inputs/previous-release-sample.md", "--title", SAMPLE_TITLE], cwd=workspace)
    assert order["status"] == "needs_input" and order["action"] == "prepare_semantic_plan", order
    draft = workspace / order["draft_file"]
    plan = json.loads(draft.read_text(encoding="utf-8"))
    blocks = [line.split("\t", 1)[0] for name in order["reading_files"]
              for line in (workspace / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    plan["summary"] = ["番茄工作法以二十五分鐘專注加五分鐘休息為一段。"]
    plan["knowledge"] = [{"title": KNOWLEDGE_TITLE, "claim": "番茄工作法把工作切成二十五分鐘的專注時段，每段之後休息五分鐘。",
                          "layer": "原文主張", "confidence": "高", "evidence_block_ids": [blocks[1]],
                          "reusable_concept": {"status": "not_reusable", "reason": "合成樣本只供升級驗收。"}}]
    plan["application"] = {"knowledge_use": "學習材料"}
    draft.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checked = run([brain, "rail", "check-plan", "--job-id", order["job_id"], "--response-file", order["draft_file"]], cwd=workspace)
    assert checked["status"] == "passed", checked
    completed = run([brain, "rail", "submit-plan", "--job-id", order["job_id"], "--response-file", order["draft_file"]], cwd=workspace)
    assert completed["status"] == "completed" and completed["completion"]["production_entry_ready"], completed
    return {"job_id": order["job_id"], "source_id": order["source_id"]}


def assert_entry_readable(workspace: Path, vault: str, sample: dict[str, str]) -> dict:
    """The updated code reads the old job, refreshes its page and serves it."""
    brain = str(workspace / "tools" / "brain.py")
    source_id = sample["source_id"]
    inspected = run([brain, "rail", "inspect", "--job-id", sample["job_id"]], cwd=workspace)
    assert inspected["status"] == "completed" and inspected["completion"]["production_entry_ready"], inspected
    refreshed = run([brain, "outcome", "--incremental-source-id", source_id, "--json"], cwd=workspace)
    refresh = refreshed.get("completed_refresh") or {}
    assert refreshed["status"] == "ready" and refresh.get("status") == "ready", refreshed
    assert refresh.get("source_and_images_unchanged") and refresh.get("original_completion_preserved"), refreshed
    audit = run([str(workspace / "tools" / "outcome_page_quality_audit.py"), "--vault", vault, "--source-id", source_id, "--json"], cwd=workspace)
    assert audit["status"] == "ready" and audit["ready_count"] == audit["page_count"] == 1, audit
    home = run([brain, "open-home"], cwd=workspace, environment=READER_ENVIRONMENT)
    nebula = run([brain, "nebula"], cwd=workspace, environment=READER_ENVIRONMENT)
    assert home["status"] == nebula["status"] == "ready", (home, nebula)
    origin = home["url"].split("/index.html", 1)[0]
    opener = build_opener(ProxyHandler({}))
    pages = {}
    for name in (source_id + "_outcome.html", "index.html", "nebula.html"):
        page = opener.open(origin + "/" + quote(f"{vault}/outcome-pages/{name}"), timeout=5).read().decode("utf-8")
        assert source_id in page and SAMPLE_TITLE in page and KNOWLEDGE_TITLE in page, name
        pages[name] = len(page)
    return {"inspect": inspected["status"], "refresh": refresh["status"], "audit": audit["status"],
            "home": home["status"], "nebula": nebula["status"], "served_pages": pages}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--legacy-package", required=True, type=Path, help="the previous published releases/<version>/creative-second-brain")
    parser.add_argument("--package", required=True, type=Path, help="the built candidate creative-second-brain")
    args = parser.parse_args()
    work = args.work_root.absolute()
    package, legacy = args.package.absolute(), args.legacy_package.absolute()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    candidate_version = json.loads((package / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    legacy_version = json.loads((legacy / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    if version_key(legacy_version) >= version_key(candidate_version):
        raise SystemExit(f"legacy package {legacy_version} must precede candidate {candidate_version}")
    work.mkdir(parents=True, exist_ok=True)

    workspace = setup_previous(work, legacy)
    config = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
    assert config["package_version"] == legacy_version, config["package_version"]
    vault, scopes = config["vault"], config["management"]["private_exclusions"]
    sample = complete_sample(workspace)
    before = private_files(workspace, scopes)

    # /brain-update runs the updater the old workspace already has installed.
    updater = str(workspace / "tools" / "brain_update.py")
    checked = run([updater, "--workspace", str(workspace), "--package", str(package), "--check"], cwd=work)
    assert checked["status"] == "checked" and checked["candidate_version"] == candidate_version, checked
    updated = run([updater, "--workspace", str(workspace), "--package", str(package), "--apply"], cwd=work)
    assert updated["status"] == "updated" and updated["doctor"] == "ready", updated
    assert_private_unchanged(before, private_files(workspace, scopes))
    doctor = run([str(workspace / "tools" / "brain.py"), "doctor"], cwd=workspace)
    assert doctor["status"] == "ready" and doctor["package_version"] == candidate_version, doctor
    assert_private_unchanged(before, private_files(workspace, scopes))
    readback = assert_entry_readable(workspace, vault, sample)

    report = {"status": "passed", "legacy_version": legacy_version, "candidate_version": candidate_version,
              "workspace": str(workspace), "sample": sample, "private_files": len(before), "readback": readback,
              "coverage": ["previous-release install", "formal text source completed by the previous release",
                           "private files byte-identical after update and doctor", "updated doctor ready",
                           "updated rail reads the old job", "updated outcome refresh preserves the old completion",
                           "old entry served on its outcome page, catalogue and nebula"],
              "not_proven": ["image, infographic or attachment sources", "Obsidian UI", "live GitHub release download"]}
    (work / "previous-release-upgrade-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
