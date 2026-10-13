"""Acceptance for PUBLIC's manual-only research boundary."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
DAY = "2026-10-07T12:00:00+00:00"


def cli(*items: str, cwd: Path, expected: int = 0) -> str:
    result = subprocess.run([PYTHON, "-B", *items], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"expected {expected}, got {result.returncode}: {' '.join(items)}\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr


def program(workspace: Path, *items: str, expected: int = 0) -> dict:
    config = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
    env = dict(os.environ, CREATIVE_SECOND_BRAIN_VAULT=str(config["vault"]))
    result = subprocess.run([PYTHON, "-B", str(workspace / "tools" / "research_program.py"), "--root", str(workspace), *items], cwd=workspace, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"expected {expected}, got {result.returncode}: {' '.join(items)}\n{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


def init_args(mode: str) -> list[str]:
    return [
        "init-topic", "--topic-id", "manual-boundary", "--title", "Manual boundary fixture",
        "--profile-version", "fixture-v1", "--purpose", "Verify portable manual research only", "--scope", '{"boundary":"fixture"}',
        "--focus", '["manual evidence"]', "--evidence-contract", '{"asset_mode":"case_evidence","source_input_kinds":["url"],"source_profiles":["article"]}',
        "--output-contract", '{"kind":"fixture"}', "--classifications", '{"primary_key":"family"}',
        "--mode", mode, "--daily-case-cap", "3", "--total-case-cap", "3",
        "--budget-searches", "2", "--budget-candidate-summaries-target", "2", "--budget-deep-reads", "2",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    work = args.work_root.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    work.mkdir(parents=True, exist_ok=True)
    if args.package:
        package = args.package.resolve()
    else:
        build = work / "build"
        cli(str(ROOT / "scripts" / "build_profiles.py"), "--output", str(build), "--profile", "public-portable", cwd=ROOT)
        package = build / "public-portable"
    workspace = work / "workspace"
    cli(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(workspace), "--vault", "Private Notes", "--display-name", "Manual Fixture", cwd=work)
    tool = workspace / "tools" / "research_program.py"

    init_help = cli(str(tool), "--root", str(workspace), "init-topic", "--help", cwd=workspace)
    begin_help = cli(str(tool), "--root", str(workspace), "begin-run", "--help", cwd=workspace)
    assert "scheduled_research" not in init_help and "--schedule-time" not in init_help
    assert "scheduled_research" not in begin_help and "--test-automation-path" not in begin_help
    rejected = cli(str(tool), "--root", str(workspace), *init_args("scheduled_research"), cwd=workspace, expected=2)
    assert "invalid choice" in rejected

    topic = program(workspace, *init_args("manual_research"))
    assert topic["status"] == "ready" and topic["topic"]["mode"] == "manual_research"
    assert "schedule_contract" not in topic["topic"]
    rejected_begin = cli(str(tool), "--root", str(workspace), "begin-run", "--topic-id", "manual-boundary", "--execution-mode", "scheduled_research", cwd=workspace, expected=2)
    assert "invalid choice" in rejected_begin
    started = program(workspace, "begin-run", "--topic-id", "manual-boundary", "--run-id", "RUN-MANUAL", "--execution-mode", "manual_research", "--direct-command-source", "explicit user research request", "--at", DAY, "--isolated-test-clock")
    assert started["status"] == "ready" and started["run"]["execution_mode"] == "manual_research"
    status = program(workspace, "status", "--topic-id", "manual-boundary", "--at", DAY, "--isolated-test-clock")
    assert "quota_lanes" not in status and status["active_work_unit"]["execution_mode"] == "manual_research"
    print(json.dumps({"status": "passed", "coverage": ["PUBLIC init rejects scheduled_research", "PUBLIC begin rejects scheduled_research", "manual topic has no schedule contract", "manual run readback has no scheduler lane"], "not_proven": ["live external research", "host skill discovery"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
