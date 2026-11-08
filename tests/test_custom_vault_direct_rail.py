"""A workspace with a user-named vault completes a source through the commands its work order prints.

Setup lets the user name the vault (stored in ``brain.json``).  The work order
prints ``python -B tools/ingest_rail.py --root <root> check-plan/submit-plan ...``,
which runs the rail directly, not through ``tools/brain.py`` (the only place the
vault name used to reach the environment).  This test installs a workspace with
``--vault "My Notes"``, starts a source through ``tools/brain.py rail start``,
then runs the work order's own ``check`` and ``submit`` commands verbatim with no
``CREATIVE_SECOND_BRAIN*`` variable at all.  The source must land in the named
vault and no default ``knowledge-base-vault`` may appear.  A variable that names a
different vault must be refused, and nothing may be written to any vault; that
holds as well when the command names the matching vault itself with ``--vault``.
The same read-only selection run through ``tools/brain.py`` and run directly must
read the same vault.

    python -B tests/test_custom_vault_direct_rail.py --package <candidate>/creative-second-brain --work-root <new short dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


PYTHON = sys.executable
VAULT = "My Notes"
OTHER_VAULT = "Other Notes"
DEFAULT_VAULT = "knowledge-base-vault"
SAMPLE_TITLE = "番茄工作法"
SAMPLE_TEXT = "# 番茄工作法\n\n番茄工作法把工作切成二十五分鐘的專注時段，每段之後休息五分鐘。\n\n連續完成四段之後，可以休息較長的時間。\n"
SAMPLE_CLAIM = "番茄工作法把工作切成二十五分鐘的專注時段，每段之後休息五分鐘。"
OTHER_TITLE = "間隔複習"
OTHER_TEXT = "# 間隔複習\n\n間隔複習把同一份內容分散在越來越長的時間點重看。\n\n每次重看之前先試著回想，再對答案。\n"
OTHER_CLAIM = "間隔複習把同一份內容分散在越來越長的時間點重看。"


TEMP_ROOT: list[Path] = []


def clean_environment(**extra: str) -> dict[str, str]:
    """The environment of a plain terminal: no variable the product itself would have exported."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("CREATIVE_SECOND_BRAIN")}
    if TEMP_ROOT:
        env["BRAIN_UPDATE_TEMP_ROOT"] = str(TEMP_ROOT[0])
    env.update(extra)
    return env


def run(command: list[str], *, cwd: Path, expected: int | tuple[int, ...] = 0, env: dict[str, str] | None = None) -> dict:
    environment = clean_environment() if env is None else env
    result = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True, env=environment, timeout=300)
    allowed = (expected,) if isinstance(expected, int) else expected
    if result.returncode not in allowed:
        raise AssertionError(" ".join(command) + f"\nexit {result.returncode}\n" + result.stdout + result.stderr)
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise AssertionError(" ".join(command) + "\n" + result.stdout + result.stderr) from exc
    payload["_exit"] = result.returncode
    return payload


def python_command(*arguments: str) -> list[str]:
    return [PYTHON, "-B", *arguments]


def printed_command(order: dict, key: str) -> list[str]:
    """The work order's own command, word for word; only the interpreter name is the running Python."""
    command = order[key]
    assert isinstance(command, list) and command[:3] == ["python", "-B", "tools/ingest_rail.py"], command
    assert "tools/brain.py" not in command, command
    return [PYTHON, *command[1:]]


def tree_digest(base: Path) -> dict[str, str]:
    if not base.is_dir():
        return {}
    return {path.relative_to(base).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(base.rglob("*")) if path.is_file()}


def write_plan(workspace: Path, order: dict, claim: str = SAMPLE_CLAIM) -> None:
    draft = workspace / order["draft_file"]
    plan = json.loads(draft.read_text(encoding="utf-8"))
    blocks = [line.split("\t", 1)[0] for name in order["reading_files"]
              for line in (workspace / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    plan["summary"] = ["番茄工作法以二十五分鐘專注加五分鐘休息為一段。"]
    knowledge = {"title": "二十五分鐘專注加五分鐘休息", "claim": claim,
                 "layer": "原文主張", "confidence": "高", "evidence_block_ids": [blocks[1]],
                 "reusable_concept": {"status": "not_reusable", "reason": "合成樣本只供直接執行驗收。"}}
    if "attribution" in json.dumps((plan.get("knowledge") or [{}])[0]):
        knowledge["attribution"] = {"origin": "source_author", "cited_work": "", "relay": ""}
    plan["knowledge"] = [knowledge]
    plan["application"] = {"knowledge_use": "學習材料"}
    draft.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path, help="the creative-second-brain package to install from")
    parser.add_argument("--work-root", required=True, type=Path, help="a new or empty short directory")
    args = parser.parse_args()
    work, package = args.work_root.absolute(), args.package.absolute()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    work.mkdir(parents=True, exist_ok=True)
    TEMP_ROOT.append(work)
    workspace = work / "workspace"
    version = json.loads((package / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]

    installed = run(python_command(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(workspace),
                                   "--vault", VAULT, "--display-name", "Custom vault direct rail"), cwd=work)
    assert installed["status"] == "initialized", installed
    config = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
    assert config["vault"] == VAULT, config

    (workspace / "inputs").mkdir(exist_ok=True)
    (workspace / "inputs" / "sample.md").write_text(SAMPLE_TEXT, encoding="utf-8")
    order = run(python_command("tools/brain.py", "rail", "start", "--text-file", "inputs/sample.md", "--title", SAMPLE_TITLE), cwd=workspace)
    assert order["status"] == "needs_input" and order["action"] == "prepare_semantic_plan", order
    source_id = order["source_id"]
    write_plan(workspace, order)
    check_command, submit_command = printed_command(order, "check"), printed_command(order, "submit")

    # The work order's own check and submit commands, run directly with no CREATIVE_SECOND_BRAIN* variable.
    checked = run(check_command, cwd=workspace)
    assert checked["status"] == "passed" and checked["errors"] == [] and checked["write_performed"] is False, checked
    completed = run(submit_command, cwd=workspace)
    assert completed["status"] == "completed", completed
    completion = completed["completion"]
    assert completion["status"] == "completed" and completion["production_entry_ready"] is True, completed

    notes = sorted((workspace / VAULT / "sources").rglob(f"{source_id}*.md"))
    assert notes, f"no source note for {source_id} under {VAULT}/sources"
    assert (workspace / VAULT / "outcome-pages" / f"{source_id}_outcome.html").is_file()
    assert (workspace / VAULT / "_registry" / "sources.jsonl").is_file()
    assert source_id in (workspace / VAULT / "_registry" / "sources.jsonl").read_text(encoding="utf-8")
    for name in (DEFAULT_VAULT, OTHER_VAULT):
        assert not (workspace / name).exists(), f"{name} must not exist in a workspace whose vault is {VAULT}"
    stray = [relative for relative in sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob(f"{source_id}*_source.md"))
             if not relative.startswith((VAULT + "/", "outputs/"))]
    assert not stray, stray

    # Resuming through the direct command is idempotent and still reads the named vault.
    again = run([PYTHON, "-B", "tools/ingest_rail.py", "--root", str(workspace), "inspect", "--job-id", order["job_id"]], cwd=workspace)
    assert again["status"] == "completed" and again["completion"]["production_entry_ready"] is True, again
    assert not (workspace / DEFAULT_VAULT).exists()

    # A read-only tool run with no --vault finds the named vault by itself.
    audit = run(python_command("tools/outcome_page_quality_audit.py", "--source-id", source_id, "--json"), cwd=workspace)
    assert audit["status"] == "ready" and audit["ready_count"] == audit["page_count"] == 1, audit
    assert not (workspace / DEFAULT_VAULT).exists()

    # Running through tools/brain.py (which exports the vault) and running the tool directly read the same vault.
    via_brain = run(python_command("tools/brain.py", "material", "--query", SAMPLE_TITLE, "--json"), cwd=workspace, expected=(0, 1))
    direct = run(python_command("tools/knowledge_material_selector.py", "--query", SAMPLE_TITLE, "--json"), cwd=workspace, expected=(0, 1))
    named = (workspace / VAULT).resolve()
    assert (workspace / via_brain["vault"]).resolve() == Path(direct["vault"]).resolve() == named, (via_brain["vault"], direct["vault"])
    direct_ids = [item["source_id"] for item in direct["candidates"]]
    assert direct_ids == [item["source_id"] for item in via_brain["candidates"]] and source_id in direct_ids, (direct_ids, via_brain["candidates"])
    assert not (workspace / DEFAULT_VAULT).exists()

    # Counter-example: a variable naming another vault is refused by the same direct commands, and nothing is written.
    (workspace / "inputs" / "other.md").write_text(OTHER_TEXT, encoding="utf-8")
    second = run(python_command("tools/brain.py", "rail", "start", "--text-file", "inputs/other.md", "--title", OTHER_TITLE), cwd=workspace)
    assert second["status"] == "needs_input" and second["source_id"] != source_id, second
    write_plan(workspace, second, OTHER_CLAIM)
    second_check, second_submit = printed_command(second, "check"), printed_command(second, "submit")
    assert run(second_check, cwd=workspace)["status"] == "passed"
    before = {name: tree_digest(workspace / name) for name in (VAULT, OTHER_VAULT, DEFAULT_VAULT)}
    hostile = clean_environment(CREATIVE_SECOND_BRAIN_VAULT=OTHER_VAULT)
    refused = run(second_submit, cwd=workspace, expected=(1, 2), env=hostile)
    assert refused["status"] == "blocked" and "CREATIVE_SECOND_BRAIN_VAULT" in str(refused.get("reason", "")), refused
    refused_check = run(second_check, cwd=workspace, expected=(1, 2), env=hostile)
    assert refused_check["status"] == "blocked" and "CREATIVE_SECOND_BRAIN_VAULT" in str(refused_check.get("reason", "")), refused_check
    for name in (OTHER_VAULT, DEFAULT_VAULT):
        assert not (workspace / name).exists(), f"{name} must not be created by a refused command"
    assert {name: tree_digest(workspace / name) for name in (VAULT, OTHER_VAULT, DEFAULT_VAULT)} == before, "a refused command wrote vault files"

    # Naming the matching vault with --vault does not switch the check off: the disagreeing variable is still refused, with no write.
    named_vault = ["--vault", VAULT]
    assert run(second_check + named_vault, cwd=workspace)["status"] == "passed", "the matching --vault is accepted when nothing disagrees"
    inspect_command = [PYTHON, "-B", "tools/ingest_rail.py", "--root", str(workspace), "inspect", "--job-id", second["job_id"]]
    for command in (second_submit, second_check, inspect_command):
        refused_named = run(command + named_vault, cwd=workspace, expected=(1, 2), env=hostile)
        said = str(refused_named.get("reason", "")) + str(refused_named.get("errors", ""))
        assert refused_named["status"] in {"blocked", "failed"} and "CREATIVE_SECOND_BRAIN_VAULT" in said, refused_named
    for name in (OTHER_VAULT, DEFAULT_VAULT):
        assert not (workspace / name).exists(), f"{name} must not be created by a refused command that names --vault"
    assert {name: tree_digest(workspace / name) for name in (VAULT, OTHER_VAULT, DEFAULT_VAULT)} == before, "a refused command with --vault wrote vault files"

    report = {"status": "passed", "package_version": version, "vault": VAULT, "source_id": source_id,
              "source_notes": [path.relative_to(workspace).as_posix() for path in notes],
              "refused_other_vault": refused.get("reason"),
              "coverage": ["named vault installed", "work order check and submit run verbatim without CREATIVE_SECOND_BRAIN* variables",
                           "completed with production_entry_ready", "source note under the named vault, no default vault",
                           "a different vault variable is refused and writes nothing",
                           "the same refusal when the command names the matching --vault itself",
                           "tools/brain.py and a direct run read the same vault"]}
    (work / "custom-vault-direct-rail-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
