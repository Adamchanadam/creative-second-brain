"""Synthetic acceptance for the v3 managed-workspace ownership contract."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
ENTRIES = {"brain-ask", "brain-think", "brain-check", "brain-create", "brain-fix", "brain-grow", "brain-guide", "brain-image", "brain-nebula", "brain-research", "brain-resume", "brain-text", "brain-tidy", "brain-url"}


def digest(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def config_bytes(value: dict) -> bytes: return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
def hashes(root: Path, control: bool = False) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts and (control or ".brain-management" not in p.parts)}


def run(*items: str, cwd: Path, expected: int = 0) -> dict:
    result = subprocess.run([PYTHON, "-B", *items], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"expected {expected}, got {result.returncode}: {' '.join(items)}\n{result.stdout}\n{result.stderr}")
    try: return json.loads(result.stdout)
    except json.JSONDecodeError as error: raise AssertionError(f"expected JSON result: {result.stdout}\n{result.stderr}") from error


def setup(package: Path, workspace: Path, vault: str = "Vault") -> None:
    run(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(workspace), "--vault", vault, "--display-name", "Synthetic", cwd=package)


def proof_for(plan: dict, destination: Path) -> Path:
    destination.write_bytes(config_bytes({"schema":"brain_bootstrap_quiescence_proof_v1", "workspace":plan["workspace_identity"], "writers_quiescent":True, "writer_assessment_sha256":plan["writer_compatibility"]["assessment_sha256"], "checked_at":"2026-10-07T00:00:00Z", "evidence":["synthetic fixture: no live writer is running"]}))
    return destination


def plan_adopt(manager: Path, workspace: Path, vault: str, proof: Path | None = None, resolution: Path | None = None, expected: int = 0) -> dict:
    argv = [str(manager), "plan-adopt", "--workspace", str(workspace), "--vault", vault, "--display-name", "Synthetic existing workspace"]
    if proof: argv += ["--bootstrap-quiescence-proof", str(proof)]
    if resolution: argv += ["--adoption-resolution", str(resolution)]
    return run(*argv, cwd=workspace, expected=expected)


def apply(manager: Path, mode: str, workspace: Path, plan: dict, proof: Path | None = None, interrupt: str = "", resolution: Path | None = None, update_resolution: Path | None = None, expected: int = 0) -> dict:
    argv = [str(manager), "apply", "--mode", mode, "--workspace", str(workspace), "--vault", plan["vault"], "--display-name", plan["display_name"], "--plan-hash", plan["plan_hash"]]
    if proof: argv += ["--bootstrap-quiescence-proof", str(proof)]
    if resolution: argv += ["--adoption-resolution", str(resolution)]
    if update_resolution: argv += ["--update-resolution", str(update_resolution)]
    if interrupt: argv += ["--test-interrupt", interrupt]
    return run(*argv, cwd=workspace, expected=expected)


def adoption_resolution_for(plan: dict, destination: Path) -> Path:
    entries = []
    for row in plan["conflicts"]:
        entries.append({
            "path": row["path"],
            "before_sha256": row["actual_sha256"],
            "after_sha256": row["expected_sha256"],
            "classification": "approved_public_core_ownership_takeover",
            "review": {"reviewed_at": "2026-10-07T00:00:00Z", "rationale": "Synthetic exact-byte ownership review."},
        })
    destination.write_bytes(config_bytes({
        "schema": "creative_second_brain_adoption_resolution_v1",
        "workspace": plan["workspace_identity"],
        "vault": plan["vault"],
        "package_binding": plan["package_binding"],
        "entries": entries,
    }))
    return destination


def update_resolution_for(plan: dict, destination: Path) -> Path:
    entries = []
    for row in plan["conflicts"]:
        entries.append({
            "path": row["path"],
            "recorded_prior_sha256": row["prior_sha256"],
            "before_sha256": row["actual_sha256"],
            "after_sha256": row["expected_sha256"],
            "classification": "approved_public_managed_runtime_replacement" if row["prior_sha256"] is not None else "approved_public_existing_runtime_reconciliation",
            "review": {"reviewed_at": "2026-10-08T00:00:00Z", "rationale": "Synthetic exact-byte runtime review."},
        })
    destination.write_bytes(config_bytes({
        "schema": "creative_second_brain_update_resolution_v1",
        "mode": "update",
        "workspace": plan["workspace_identity"],
        "vault": plan["vault"],
        "package_binding": plan["package_binding"],
        "entries": entries,
    }))
    return destination


def host_adaptation_resolution_for(plan: dict, destination: Path, target: str) -> Path:
    entries = []
    for row in plan["conflicts"]:
        entries.append({
            "path": row["path"],
            "recorded_prior_sha256": row["prior_sha256"],
            "before_sha256": row["actual_sha256"],
            "after_sha256": row["expected_sha256"],
            "classification": "approved_host_runtime_adaptation" if row["path"] == target else ("approved_public_managed_runtime_replacement" if row["prior_sha256"] is not None else "approved_public_existing_runtime_reconciliation"),
            "review": {"reviewed_at": "2026-10-11T00:00:00Z", "rationale": "Synthetic candidate-bound host adaptation review."},
        })
    destination.write_bytes(config_bytes({
        "schema": "creative_second_brain_update_resolution_v1",
        "mode": "update",
        "workspace": plan["workspace_identity"],
        "vault": plan["vault"],
        "package_binding": plan["package_binding"],
        "entries": entries,
    }))
    return destination


def legacy_workspace(root: Path, name: str, status: str = "completed", invalid: bytes | None = None) -> Path:
    workspace = root / name; vault = workspace / "custom-vault"
    (vault / ".obsidian").mkdir(parents=True); (vault / ".obsidian" / "graph.json").write_text('{"private":true}\n', encoding="utf-8")
    (vault / "_registry" / "research-programs").mkdir(parents=True); (vault / "_registry" / "sources.jsonl").write_text('{"source_id":"PRIVATE"}\n', encoding="utf-8")
    for relative in ("inputs/private.txt", "outputs/receipt.txt", "connections/reference.json", "jobs/current.json", "registry/current.json", "receipts/current.json"):
        target = workspace / relative; target.parent.mkdir(parents=True, exist_ok=True); target.write_text("private\n", encoding="utf-8")
    jobs = workspace / "outputs" / "compiler_worker_v2" / "_jobs"; jobs.mkdir(parents=True, exist_ok=True)
    value = {"schema_version":"compiler_worker_v2_job_v1", "job_id":"CURRENT-JOB", "source_id":"PRIVATE", "status":status}
    (jobs / "CURRENT-JOB.json").write_bytes(invalid if invalid is not None else (json.dumps(value) + "\n").encode("utf-8"))
    for retired in ("brain-taste", "brain-canvas"):
        target = workspace / ".agents" / "skills" / retired; target.mkdir(parents=True, exist_ok=True); (target / "SKILL.md").write_text("legacy private entry\n", encoding="utf-8")
    return workspace


def reserved_research(workspace: Path, vault: str, topic_id: str = "TEST_TOPIC") -> None:
    directory = workspace / vault / "_registry" / "research-programs" / topic_id
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("topic.json").write_bytes(config_bytes({"schema_version":"research_program_v1", "topic_id":topic_id}))
    directory.joinpath("state.json").write_bytes(config_bytes({
        "schema_version":"research_program_v1", "accepted_cases":[], "withdrawn_cases":[],
        "active_cases":{"CASE-1":{"case_id":"CASE-1", "run_id":"RUN-1", "status":"reserved"}},
        "discovery_slots":{}, "rejected_cases":[], "rejected_discoveries":[], "day_runs":{},
        "day_run_history":{}, "manual_allowances":{},
        "runs":{"RUN-1":{"run_id":"RUN-1", "actions":[]}},
    }))


def bound_research(workspace: Path, vault: str, *, matching_case: bool = True) -> None:
    directory = workspace / vault / "_registry" / "research-programs" / "BOUND"
    directory.mkdir(parents=True, exist_ok=True)
    directory.joinpath("topic.json").write_bytes(config_bytes({"schema_version":"research_program_v1", "topic_id":"BOUND"}))
    source_identity = "url:bound-source"
    record = {"case_id":"CASE-BOUND", "run_id":"RUN-BOUND", "source_identity":source_identity, "status":"qualified"}
    records = [record] if matching_case else []
    directory.joinpath("state.json").write_bytes(config_bytes({
        "schema_version":"research_program_v1", "accepted_cases":records, "withdrawn_cases":[],
        "active_cases":{}, "discovery_slots":{"DISC-BOUND":{"discovery_id":"DISC-BOUND", "run_id":"RUN-BOUND", "status":"bound", "case_id":"CASE-BOUND", "source_identity":source_identity}},
        "rejected_cases":[], "rejected_discoveries":[], "day_runs":{}, "day_run_history":{}, "manual_allowances":{},
        "runs":{"RUN-BOUND":{"run_id":"RUN-BOUND", "actions":[]}},
    }))


def make_prior_managed_bytes(workspace: Path, relative: str = "tools/brain.py") -> None:
    target = workspace / relative; target.write_bytes(target.read_bytes() + b"\n# synthetic prior release\n")
    value = json.loads((workspace / "brain.json").read_text(encoding="utf-8")); value["managed_files"][relative] = digest(target.read_bytes()); (workspace / "brain.json").write_bytes(config_bytes(value))


def tx_id(workspace: Path) -> str:
    values = sorted((workspace / ".brain-management" / "transactions").iterdir()); assert len(values) == 1; return values[0].name
def latest_tx_id(workspace: Path) -> str:
    values = sorted((workspace / ".brain-management" / "transactions").iterdir()); assert values; return values[-1].name
def rollback(manager: Path, workspace: Path, transaction: str, interrupt: str = "", expected: int = 0) -> dict:
    argv = [str(manager), "rollback", "--workspace", str(workspace), "--transaction-id", transaction]
    if interrupt: argv += ["--test-interrupt", interrupt]
    return run(*argv, cwd=workspace, expected=expected)


def rebind_completed_history(workspace: Path, transaction: str) -> None:
    tx = workspace / ".brain-management" / "transactions" / transaction; intent_path = tx / "intent.json"
    intent = json.loads(intent_path.read_text(encoding="utf-8")); intent["package_binding"]["package_version"] = "historical-public-version"; intent.pop("adoption_resolution", None); intent_path.write_bytes(config_bytes(intent))
    rehash_transaction_checkpoints(tx)


def rehash_transaction_checkpoints(tx: Path) -> None:
    intent_hash = digest((tx / "intent.json").read_bytes()); previous = ""
    for checkpoint in sorted((tx / "checkpoints").glob("*.json")):
        value = json.loads(checkpoint.read_text(encoding="utf-8")); value["intent_sha256"] = intent_hash; value["previous_checkpoint_sha256"] = previous; checkpoint.write_bytes(config_bytes(value)); previous = digest(checkpoint.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--work-root", type=Path, required=True); parser.add_argument("--package", type=Path); args = parser.parse_args()
    work = args.work_root.absolute(); work.mkdir(parents=True, exist_ok=False)
    if args.package:
        package = args.package.resolve()
    else:
        build = work / "candidate"; run(str(ROOT / "scripts" / "build_profiles.py"), "--output", str(build), "--profile", "public-portable", cwd=ROOT)
        package = build / "public-portable"
    run(str(package / "scripts" / "verify_package.py"), "--package", str(package), cwd=package); manager = package / "scripts" / "manage_workspace.py"

    # New setup and v3 manager share the same inventory and encoding contract.
    fresh = work / "fresh"; setup(package, fresh)
    marker = json.loads((fresh / "brain.json").read_text(encoding="utf-8")); assert marker["schema"] == "creative_second_brain_workspace_v3" and marker["management"]["schema"] == "creative_second_brain_workspace_manager_v2"
    version_parts = marker["package_version"].split(".")
    assert len(version_parts) == 3 and all(part.isdigit() for part in version_parts)
    newer_version = ".".join(version_parts[:2] + [str(int(version_parts[2]) + 1)])
    assert {p.name for p in (fresh / ".agents" / "skills").iterdir() if p.is_dir()} == ENTRIES
    for entry in ENTRIES:
        assert "tools/brain.py" in (fresh / ".agents" / "skills" / entry / "SKILL.md").read_text(encoding="utf-8")
    home = (fresh / "index.html").read_text(encoding="utf-8")
    assert "$creative-second-brain" not in home and all("$" + entry in home for entry in ENTRIES)
    assert f"目前使用：創造型第二大腦 v{marker['package_version']}" in home and "介面 v1.0.0" not in home
    assert run(str(manager), "status", "--workspace", str(fresh), cwd=fresh)["status"] == "ready"
    no_op = run(str(manager), "plan-update", "--workspace", str(fresh), cwd=fresh); assert no_op["status"] == "planned" and no_op["writes"] == 0 and not no_op["conflicts"]
    assert apply(manager, "update", fresh, no_op)["status"] == "applied"
    assert run(str(manager), "status", "--workspace", str(fresh), cwd=fresh)["status"] == "ready" and not (fresh / ".brain-management" / "manager.lock").exists()
    assert run(str(fresh / "tools" / "brain.py"), "doctor", cwd=fresh)["status"] == "ready"

    # A v2 workspace remains a migration input for the current manager, but
    # the completed transaction writes v3 and rollback restores its exact v2
    # bytes.  Old v2-only managers therefore cannot take over after migration.
    v2 = work / "v2-migration"; setup(package, v2)
    v2_marker = json.loads((v2 / "brain.json").read_text(encoding="utf-8")); v2_marker["schema"] = "creative_second_brain_workspace_v2"; (v2 / "brain.json").write_bytes(config_bytes(v2_marker)); v2_before = hashes(v2)
    v2_plan = run(str(manager), "plan-update", "--workspace", str(v2), cwd=v2); assert v2_plan["status"] == "planned" and v2_plan["configuration"]["schema"] == "creative_second_brain_workspace_v3"
    assert apply(manager, "update", v2, v2_plan, interrupt="after_target", expected=2)["status"] == "blocked"
    assert rollback(manager, v2, latest_tx_id(v2))["status"] == "rolled_back" and hashes(v2) == v2_before
    v2_retry = run(str(manager), "plan-update", "--workspace", str(v2), cwd=v2); assert apply(manager, "update", v2, v2_retry)["status"] == "applied"
    assert json.loads((v2 / "brain.json").read_text(encoding="utf-8"))["schema"] == "creative_second_brain_workspace_v3"
    v3_before = hashes(v2)
    assert plan_adopt(manager, v2, "Vault", expected=2)["status"] == "blocked" and hashes(v2) == v3_before

    # A manager package can repair a valid v2 marker, but it cannot downgrade
    # an already-installed product or erase a newer shared-entry provenance.
    newer_product = work / "newer-product"; setup(package, newer_product)
    newer_marker = json.loads((newer_product / "brain.json").read_text(encoding="utf-8")); newer_marker["package_version"] = newer_version; (newer_product / "brain.json").write_bytes(config_bytes(newer_marker)); newer_before = hashes(newer_product, control=True)
    newer_blocked = run(str(manager), "plan-update", "--workspace", str(newer_product), cwd=newer_product, expected=2)
    assert newer_blocked["reason"] == "candidate product version predates installed product version" and hashes(newer_product, control=True) == newer_before
    shared_ahead = work / "shared-ahead"; setup(package, shared_ahead)
    shared_marker = json.loads((shared_ahead / "brain.json").read_text(encoding="utf-8")); shared_paths = ["tools/brain.py"] + sorted(path for path in shared_marker["managed_files"] if path.startswith(".agents/skills/brain-") and path.endswith("/SKILL.md"))
    shared_marker["shared_surface"] = {"schema":"creative_second_brain_shared_surface_v1", "package_binding":{"package_version":newer_version}, "files":{path:digest((shared_ahead / path).read_bytes()) for path in shared_paths}}
    (shared_ahead / "brain.json").write_bytes(config_bytes(shared_marker)); shared_ahead_before = hashes(shared_ahead, control=True)
    shared_ahead_blocked = run(str(manager), "plan-update", "--workspace", str(shared_ahead), cwd=shared_ahead, expected=2)
    assert shared_ahead_blocked["reason"] == "candidate product version predates existing shared surface source" and hashes(shared_ahead, control=True) == shared_ahead_before

    # A v1 marker must not be treated as a managed update target.
    v1 = work / "legacy-v1"; shutil.copytree(fresh, v1); legacy = json.loads((v1 / "brain.json").read_text(encoding="utf-8")); legacy["schema"] = "creative_second_brain_workspace_v1"; (v1 / "brain.json").write_bytes(config_bytes(legacy)); before_v1 = hashes(v1)
    assert run(str(manager), "plan-update", "--workspace", str(v1), cwd=v1, expected=2)["status"] == "blocked" and hashes(v1) == before_v1

    # First adoption preserves private scopes and retired local entries.
    adopted = legacy_workspace(work, "adopted"); protected_before = hashes(adopted)
    without_proof = plan_adopt(manager, adopted, "custom-vault", expected=2); assert "bootstrap_writer_quiescence_not_proven" in without_proof["blocking_reasons"]
    adoption_proof = proof_for(without_proof, work / "adoption-proof.json"); adoption = plan_adopt(manager, adopted, "custom-vault", adoption_proof)
    assert adoption["status"] == "planned" and adoption["writer_compatibility"]["completed_job_count"] == 1 and hashes(adopted) == protected_before
    assert apply(manager, "adopt", adopted, adoption, adoption_proof)["status"] == "applied"
    assert (adopted / ".agents" / "skills" / "brain-taste" / "SKILL.md").is_file() and (adopted / ".agents" / "skills" / "brain-canvas" / "SKILL.md").is_file()
    protected_after = {k:v for k,v in hashes(adopted).items() if not k.startswith((".agents/", "assets/", "docs/", "tools/", "brain.json", "requirements.txt"))}
    assert protected_after == {k:v for k,v in protected_before.items() if not k.startswith((".agents/", "assets/", "docs/", "tools/", "brain.json", "requirements.txt"))}
    assert run(str(adopted / "tools" / "brain.py"), "context", cwd=adopted)["jobs"][0]["job_id"] == "CURRENT-JOB"

    # A first adoption may only replace pre-existing Public-owned paths after
    # an exact, complete reviewed resolution.  The record binds the current
    # workspace, candidate package, every before/after hash, and survives in
    # the transaction intent for recovery audit.
    resolved = legacy_workspace(work, "resolved-adoption")
    conflict_target = resolved / "tools" / "brain.py"; conflict_target.parent.mkdir(parents=True, exist_ok=True); conflict_target.write_text("legacy unowned entry\n", encoding="utf-8")
    pending_resolution = plan_adopt(manager, resolved, "custom-vault", expected=2)
    resolution_proof = proof_for(pending_resolution, work / "resolution-proof.json")
    blocked_resolution = plan_adopt(manager, resolved, "custom-vault", resolution_proof, expected=2)
    assert blocked_resolution["adoption_resolution"]["status"] == "required" and [row["path"] for row in blocked_resolution["conflicts"]] == ["tools/brain.py"]
    resolution_before = hashes(resolved, control=True)
    resolution_path = adoption_resolution_for(blocked_resolution, work / "resolution.json")
    forged_resolution = json.loads(resolution_path.read_text(encoding="utf-8")); forged_resolution["entries"].append(dict(forged_resolution["entries"][0], path="custom-vault/private.md")); resolution_path.write_bytes(config_bytes(forged_resolution))
    assert plan_adopt(manager, resolved, "custom-vault", resolution_proof, resolution_path, expected=2)["status"] == "blocked" and hashes(resolved, control=True) == resolution_before
    for missing_workspace in ("", None):
        malformed_resolution = json.loads(adoption_resolution_for(blocked_resolution, resolution_path).read_text(encoding="utf-8")); malformed_resolution["workspace"] = missing_workspace; resolution_path.write_bytes(config_bytes(malformed_resolution))
        assert plan_adopt(manager, resolved, "custom-vault", resolution_proof, resolution_path, expected=2)["status"] == "blocked" and hashes(resolved, control=True) == resolution_before
    resolution_path = adoption_resolution_for(blocked_resolution, resolution_path)
    approved_resolution = plan_adopt(manager, resolved, "custom-vault", resolution_proof, resolution_path)
    assert approved_resolution["status"] == "planned" and approved_resolution["adoption_resolution"]["status"] == "accepted" and not approved_resolution["conflicts"]
    assert apply(manager, "adopt", resolved, approved_resolution, resolution_proof, resolution=resolution_path)["status"] == "applied"
    resolution_intent = json.loads((resolved / ".brain-management" / "transactions" / tx_id(resolved) / "intent.json").read_text(encoding="utf-8"))
    assert resolution_intent["adoption_resolution"]["sha256"] == digest(resolution_path.read_bytes()) and run(str(manager), "status", "--workspace", str(resolved), cwd=resolved)["status"] == "ready"

    # An unknown existing root configuration can carry local integration
    # settings.  This generic first-adoption mechanism must never replace it;
    # such a configuration needs its own migration owner and acceptance.
    config_conflict = legacy_workspace(work, "root-config-conflict")
    (config_conflict / "brain.json").write_text('{"schema":"legacy-private-config"}\n', encoding="utf-8")
    config_seed = plan_adopt(manager, config_conflict, "custom-vault", expected=2); config_proof = proof_for(config_seed, work / "root-config-proof.json")
    config_blocked = plan_adopt(manager, config_conflict, "custom-vault", config_proof, expected=2); config_resolution = adoption_resolution_for(config_blocked, work / "root-config-resolution.json"); config_before = hashes(config_conflict, control=True)
    assert plan_adopt(manager, config_conflict, "custom-vault", config_proof, config_resolution, expected=2)["status"] == "blocked" and hashes(config_conflict, control=True) == config_before

    # A stale resolution cannot become an ownership override after one byte of
    # the reviewed conflict changes; apply must fail before creating control
    # state or replacing the independently changed file.
    stale = legacy_workspace(work, "stale-resolution")
    stale_target = stale / "tools" / "brain.py"; stale_target.parent.mkdir(parents=True, exist_ok=True); stale_target.write_text("legacy before review\n", encoding="utf-8")
    stale_seed = plan_adopt(manager, stale, "custom-vault", expected=2); stale_proof = proof_for(stale_seed, work / "stale-resolution-proof.json")
    stale_blocked = plan_adopt(manager, stale, "custom-vault", stale_proof, expected=2); stale_resolution = adoption_resolution_for(stale_blocked, work / "stale-resolution.json")
    stale_plan = plan_adopt(manager, stale, "custom-vault", stale_proof, stale_resolution)
    stale_target.write_text("independent change after review\n", encoding="utf-8"); stale_before = hashes(stale, control=True)
    assert apply(manager, "adopt", stale, stale_plan, stale_proof, resolution=stale_resolution, expected=2)["status"] == "blocked" and hashes(stale, control=True) == stale_before

    # Recorded old bytes update; an unrecorded local edit is preserved as conflict.
    make_prior_managed_bytes(adopted); update = run(str(manager), "plan-update", "--workspace", str(adopted), cwd=adopted)
    assert update["status"] == "planned" and any(row["action"] == "replace" for row in update["actions"])
    assert apply(manager, "update", adopted, update)["status"] == "applied" and run(str(manager), "status", "--workspace", str(adopted), cwd=adopted)["status"] == "ready"
    target = adopted / "tools" / "brain.py"; target.write_bytes(target.read_bytes() + b"\n# unowned edit\n")
    conflict = run(str(manager), "plan-update", "--workspace", str(adopted), cwd=adopted, expected=2); assert any(row["path"] == "tools/brain.py" for row in conflict["conflicts"])
    update_resolution = update_resolution_for(conflict, work / "update-resolution.json")
    assert run(str(manager), "apply", "--mode", "update", "--workspace", str(adopted), "--plan-hash", "0" * 64, "--adoption-resolution", str(update_resolution), cwd=adopted, expected=2)["status"] == "blocked"
    resolved_update = run(str(manager), "plan-update", "--workspace", str(adopted), "--update-resolution", str(update_resolution), cwd=adopted)
    assert resolved_update["status"] == "planned" and resolved_update["update_resolution"]["status"] == "accepted" and not resolved_update["conflicts"]
    update_result = apply(manager, "update", adopted, resolved_update, update_resolution=update_resolution); assert update_result["status"] == "applied"
    update_intent = json.loads((adopted / ".brain-management" / "transactions" / update_result["transaction_id"] / "intent.json").read_text(encoding="utf-8"))
    assert update_intent["update_resolution"]["sha256"] == digest(update_resolution.read_bytes()) and run(str(manager), "status", "--workspace", str(adopted), cwd=adopted)["status"] == "ready"
    recovery = work / "update-resolution-recovery"; setup(package, recovery); recovery_target = recovery / "tools" / "brain.py"; recovery_target.write_bytes(recovery_target.read_bytes() + b"\n# reviewed local runtime\n")
    recovery_conflict = run(str(manager), "plan-update", "--workspace", str(recovery), cwd=recovery, expected=2); recovery_resolution = update_resolution_for(recovery_conflict, work / "update-recovery-resolution.json")
    recovery_plan = run(str(manager), "plan-update", "--workspace", str(recovery), "--update-resolution", str(recovery_resolution), cwd=recovery); recovery_before = hashes(recovery)
    assert apply(manager, "update", recovery, recovery_plan, interrupt="after_intent", update_resolution=recovery_resolution, expected=2)["status"] == "blocked"
    assert rollback(manager, recovery, tx_id(recovery))["status"] == "rolled_back" and hashes(recovery) == recovery_before
    ownership = work / "update-resolution-ownership"; setup(package, ownership); ownership_target = ownership / "tools" / "brain.py"; ownership_target.write_bytes(ownership_target.read_bytes() + b"\n# reviewed local runtime\n")
    ownership_conflict = run(str(manager), "plan-update", "--workspace", str(ownership), cwd=ownership, expected=2); ownership_resolution = update_resolution_for(ownership_conflict, work / "update-ownership-resolution.json")
    ownership_plan = run(str(manager), "plan-update", "--workspace", str(ownership), "--update-resolution", str(ownership_resolution), cwd=ownership)
    assert apply(manager, "update", ownership, ownership_plan, interrupt="after_intent", update_resolution=ownership_resolution, expected=2)["status"] == "blocked"
    ownership_config = json.loads((ownership / "brain.json").read_text(encoding="utf-8")); ownership_config["managed_files"]["tools/brain.py"] = "0" * 64; (ownership / "brain.json").write_bytes(config_bytes(ownership_config)); ownership_before = hashes(ownership, control=True)
    assert rollback(manager, ownership, tx_id(ownership), expected=2)["status"] == "blocked" and hashes(ownership, control=True) == ownership_before

    # A later package can reconcile one exact existing runtime file that was
    # never recorded in the old config.  It remains update-only and must not
    # become a broad takeover mechanism.
    reconcile = work / "update-existing-unowned"; setup(package, reconcile)
    reconcile_config = json.loads((reconcile / "brain.json").read_text(encoding="utf-8"))
    reconcile_path = "tools/youtube_transcript_capture.py"
    assert reconcile_path in reconcile_config["managed_files"]
    del reconcile_config["managed_files"][reconcile_path]
    (reconcile / "brain.json").write_bytes(config_bytes(reconcile_config))
    reconcile_target = reconcile / reconcile_path
    reconcile_target.write_bytes(reconcile_target.read_bytes() + b"\n# existing unowned runtime\n")
    reconcile_before = hashes(reconcile)
    reconcile_conflict = run(str(manager), "plan-update", "--workspace", str(reconcile), cwd=reconcile, expected=2)
    reconcile_row = next(row for row in reconcile_conflict["conflicts"] if row["path"] == reconcile_path)
    assert reconcile_row["prior_sha256"] is None and reconcile_conflict["update_resolution"]["status"] == "required"
    reconcile_resolution = update_resolution_for(reconcile_conflict, work / "update-existing-unowned-resolution.json")
    wrong_class = json.loads(reconcile_resolution.read_text(encoding="utf-8")); wrong_class["entries"][0]["classification"] = "approved_public_managed_runtime_replacement"; (work / "update-existing-unowned-wrong-class.json").write_bytes(config_bytes(wrong_class))
    assert run(str(manager), "plan-update", "--workspace", str(reconcile), "--update-resolution", str(work / "update-existing-unowned-wrong-class.json"), cwd=reconcile, expected=2)["status"] == "blocked"
    for interruption in ("after_intent", "after_stage", "after_target", "after_verified"):
        reconcile_plan = run(str(manager), "plan-update", "--workspace", str(reconcile), "--update-resolution", str(reconcile_resolution), cwd=reconcile)
        transaction_root = reconcile / ".brain-management" / "transactions"
        prior_transactions = {item.name for item in transaction_root.iterdir()} if transaction_root.is_dir() else set()
        assert apply(manager, "update", reconcile, reconcile_plan, interrupt=interruption, update_resolution=reconcile_resolution, expected=2)["status"] == "blocked"
        fresh_transactions = {item.name for item in transaction_root.iterdir()} - prior_transactions
        assert len(fresh_transactions) == 1
        assert rollback(manager, reconcile, next(iter(fresh_transactions)))["status"] == "rolled_back" and hashes(reconcile) == reconcile_before
    reconcile_plan = run(str(manager), "plan-update", "--workspace", str(reconcile), "--update-resolution", str(reconcile_resolution), cwd=reconcile)
    assert apply(manager, "update", reconcile, reconcile_plan, update_resolution=reconcile_resolution)["status"] == "applied"
    reconciled_config = json.loads((reconcile / "brain.json").read_text(encoding="utf-8"))
    replay_update = run(str(manager), "plan-update", "--workspace", str(reconcile), cwd=reconcile)
    assert reconcile_path in reconciled_config["managed_files"] and replay_update["status"] == "planned" and replay_update["update_resolution"]["status"] == "not_required" and run(str(manager), "status", "--workspace", str(reconcile), cwd=reconcile)["status"] == "ready"
    target.write_bytes(target.read_bytes() + b"\n# reviewed drift\n")
    stale_conflict = run(str(manager), "plan-update", "--workspace", str(adopted), cwd=adopted, expected=2)
    stale_resolution = update_resolution_for(stale_conflict, work / "stale-update-resolution.json")
    forged_resolution = json.loads(stale_resolution.read_text(encoding="utf-8")); forged_resolution["entries"].append(dict(forged_resolution["entries"][0], path="brain.json")); (work / "forged-update-resolution.json").write_bytes(config_bytes(forged_resolution))
    assert run(str(manager), "plan-update", "--workspace", str(adopted), "--update-resolution", str(work / "forged-update-resolution.json"), cwd=adopted, expected=2)["status"] == "blocked"
    stale_plan = run(str(manager), "plan-update", "--workspace", str(adopted), "--update-resolution", str(stale_resolution), cwd=adopted)
    target.write_bytes(target.read_bytes() + b"\n# changed after review\n"); stale_before = hashes(adopted, control=True)
    assert apply(manager, "update", adopted, stale_plan, update_resolution=stale_resolution, expected=2)["status"] == "blocked" and hashes(adopted, control=True) == stale_before

    # A reviewed host adaptation keeps one generic runtime byte only when the
    # resolution binds the workspace, candidate, old base and host byte.
    host = work / "host-adaptation"; setup(package, host)
    host_target = host / "tools" / "daily_home.py"; host_target.write_bytes(host_target.read_bytes() + b"\n# reviewed host adaptation\n")
    host_before = hashes(host)
    host_blocked = run(str(manager), "plan-update", "--workspace", str(host), cwd=host, expected=2)
    assert [row["path"] for row in host_blocked["conflicts"]] == ["tools/daily_home.py"]
    host_resolution = host_adaptation_resolution_for(host_blocked, work / "host-adaptation-resolution.json", "tools/daily_home.py")
    host_plan = run(str(manager), "plan-update", "--workspace", str(host), "--update-resolution", str(host_resolution), cwd=host)
    host_row = next(row for row in host_plan["actions"] if row["path"] == "tools/daily_home.py")
    assert host_row["action"] == "keep_host" and host_plan["configuration"]["host_runtime_overrides"]["files"]["tools/daily_home.py"]["host_sha256"] == digest(host_target.read_bytes())
    assert apply(manager, "update", host, host_plan, interrupt="after_target", update_resolution=host_resolution, expected=2)["status"] == "blocked"
    assert rollback(manager, host, latest_tx_id(host))["status"] == "rolled_back" and hashes(host) == host_before
    host_plan = run(str(manager), "plan-update", "--workspace", str(host), "--update-resolution", str(host_resolution), cwd=host)
    assert apply(manager, "update", host, host_plan, update_resolution=host_resolution)["status"] == "applied"
    host_config = json.loads((host / "brain.json").read_text(encoding="utf-8"))
    assert host_target.read_bytes().endswith(b"# reviewed host adaptation\n") and host_config["host_runtime_overrides"]["files"]["tools/daily_home.py"]["base_sha256"] == host_plan["candidate_runtime_hashes"]["tools/daily_home.py"]
    assert run(str(manager), "status", "--workspace", str(host), cwd=host)["status"] == "ready"
    host_repeat = run(str(manager), "plan-update", "--workspace", str(host), cwd=host)
    assert host_repeat["status"] == "planned" and host_repeat["writes"] == 0 and next(row for row in host_repeat["actions"] if row["path"] == "tools/daily_home.py")["action"] == "keep_host"
    # A subsequent reviewed host edit must rebind the existing adaptation,
    # rather than forcing an unsafe manual brain.json edit or a generic reset.
    host_target.write_bytes(host_target.read_bytes() + b"# reviewed host adaptation revision\n")
    rebind_conflict = run(str(manager), "plan-update", "--workspace", str(host), cwd=host, expected=2)
    rebind_resolution = host_adaptation_resolution_for(rebind_conflict, work / "host-adaptation-rebind-resolution.json", "tools/daily_home.py")
    rebind_plan = run(str(manager), "plan-update", "--workspace", str(host), "--update-resolution", str(rebind_resolution), cwd=host)
    assert next(row for row in rebind_plan["actions"] if row["path"] == "tools/daily_home.py")["action"] == "keep_host"
    assert apply(manager, "update", host, rebind_plan, update_resolution=rebind_resolution)["status"] == "applied"
    assert run(str(manager), "status", "--workspace", str(host), cwd=host)["status"] == "ready"
    host_target.write_bytes(host_target.read_bytes() + b"# changed before host review\n")
    stale_conflict = run(str(manager), "plan-update", "--workspace", str(host), cwd=host, expected=2)
    stale_resolution = host_adaptation_resolution_for(stale_conflict, work / "stale-host-adaptation-resolution.json", "tools/daily_home.py")
    host_target.write_bytes(host_target.read_bytes() + b"# changed after host review\n"); stale_before = hashes(host, control=True)
    assert run(str(manager), "plan-update", "--workspace", str(host), "--update-resolution", str(stale_resolution), cwd=host, expected=2)["status"] == "blocked" and hashes(host, control=True) == stale_before
    restricted = work / "restricted-host-adaptation"; setup(package, restricted)
    restricted_target = restricted / "tools" / "brain.py"; restricted_target.write_bytes(restricted_target.read_bytes() + b"\n# must never be a host adaptation\n")
    restricted_blocked = run(str(manager), "plan-update", "--workspace", str(restricted), cwd=restricted, expected=2)
    restricted_resolution = host_adaptation_resolution_for(restricted_blocked, work / "restricted-host-adaptation-resolution.json", "tools/brain.py")
    assert run(str(manager), "plan-update", "--workspace", str(restricted), "--update-resolution", str(restricted_resolution), cwd=restricted, expected=2)["status"] == "blocked"

    # A completed old release journal is evidence, not a live mutation right.
    # It must not prevent the next Public package from producing a fresh plan.
    historical = work / "historical-binding"; setup(package, historical); make_prior_managed_bytes(historical)
    historical_update = run(str(manager), "plan-update", "--workspace", str(historical), cwd=historical); assert apply(manager, "update", historical, historical_update)["status"] == "applied"
    rebind_completed_history(historical, tx_id(historical)); historical_before = hashes(historical, control=True)
    assert run(str(manager), "status", "--workspace", str(historical), cwd=historical)["status"] == "ready"
    assert run(str(manager, ), "plan-update", "--workspace", str(historical), cwd=historical)["status"] == "planned" and hashes(historical, control=True) == historical_before
    empty_intent = work / "empty-intent-workspace"; shutil.copytree(historical, empty_intent); empty_tx = tx_id(empty_intent); empty_path = empty_intent / ".brain-management" / "transactions" / empty_tx / "intent.json"; empty_value = json.loads(empty_path.read_text(encoding="utf-8")); empty_value["workspace"] = ""; empty_path.write_bytes(config_bytes(empty_value)); rehash_transaction_checkpoints(empty_path.parent); empty_before = hashes(empty_intent, control=True)
    assert run(str(manager), "status", "--workspace", str(empty_intent), cwd=empty_intent, expected=2)["status"] == "blocked" and run(str(manager), "plan-update", "--workspace", str(empty_intent), cwd=empty_intent, expected=2)["status"] == "blocked" and hashes(empty_intent, control=True) == empty_before

    # Job records are either identity-preserving checkpoints, schema-owned nonjobs, or fail-closed evidence.
    for job_status in ("accepted", "queued", "running", "awaiting_imagegen", "blocked", "committing"):
        workspace = legacy_workspace(work, "job-" + job_status, job_status); pending = plan_adopt(manager, workspace, "custom-vault", expected=2)
        assert pending["writer_compatibility"]["status"] == "compatible" and pending["writer_compatibility"]["unfinished_jobs"][0]["status"] == job_status
        assert plan_adopt(manager, workspace, "custom-vault", proof_for(pending, work / ("proof-" + job_status + ".json")))["status"] == "planned"
    for name, raw, reason in (("corrupt",b"{bad","unreadable_or_invalid_json"),("nonobject",b"[]","job_record_is_not_an_object"),("unknownschema",b'{"schema_version":"future","job_id":"x","status":"completed"}',"unknown_or_incompatible_job_schema"),("missingstate",b'{"schema_version":"compiler_worker_v2_job_v1","job_id":"x"}',"job_record_missing_required_state")):
        workspace = legacy_workspace(work, "bad-" + name, invalid=raw); before = hashes(workspace); blocked = plan_adopt(manager, workspace, "custom-vault", expected=2)
        assert blocked["writer_compatibility"]["status"] == "blocked" and blocked["writer_compatibility"]["uninspectable_jobs"][0]["reason"] == reason and hashes(workspace) == before
    invalid_status = legacy_workspace(work, "bad-status", "INVALID_CORRUPT_STATUS"); before = hashes(invalid_status); blocked = plan_adopt(manager, invalid_status, "custom-vault", expected=2)
    assert blocked["writer_compatibility"]["status"] == "blocked" and blocked["writer_compatibility"]["uninspectable_jobs"][0]["reason"] == "unknown_compiler_job_status" and hashes(invalid_status) == before
    status_corrupt = work / "status-corrupt"; setup(package, status_corrupt); bad_job = status_corrupt / "outputs" / "compiler_worker_v2" / "_jobs" / "bad.json"; bad_job.parent.mkdir(parents=True, exist_ok=True); bad_job.write_bytes(b"{bad")
    assert run(str(manager), "status", "--workspace", str(status_corrupt), cwd=status_corrupt, expected=2)["status"] == "blocked"
    foreign = legacy_workspace(work, "recognized-non-job"); (foreign / "outputs" / "compiler_worker_v2" / "_jobs" / "revision.json").write_text('{"schema_version":"infographic_revision_plan_v1"}', encoding="utf-8")
    foreign_plan = plan_adopt(manager, foreign, "custom-vault", expected=2); assert foreign_plan["writer_compatibility"]["recognized_non_job_records"]
    assert plan_adopt(manager, foreign, "custom-vault", proof_for(foreign_plan, work / "foreign-proof.json"))["status"] == "planned"

    # A reserved research state blocks even when no process lock exists.  The
    # assessment binds both records and their original hashes into its output.
    research = legacy_workspace(work, "research-reserved"); reserved_research(research, "custom-vault"); gated = plan_adopt(manager, research, "custom-vault", expected=2)
    assert "research_active_hard_gate" in gated["writer_compatibility"]["reason_codes"] and gated["writer_compatibility"]["active_research"][0]["topic_id"] == "TEST_TOPIC"
    assert plan_adopt(manager, research, "custom-vault", proof_for(gated, work / "research-proof.json"), expected=2)["status"] == "blocked"
    managed_research = work / "managed-research"; setup(package, managed_research); reserved_research(managed_research, "Vault")
    assert run(str(manager), "status", "--workspace", str(managed_research), cwd=managed_research, expected=2)["status"] == "blocked"

    # A completed transfer leaves a bound discovery as audit history.  It is
    # quiescent only when its case/run/source identity can be read back.
    bound = legacy_workspace(work, "research-bound"); bound_research(bound, "custom-vault")
    bound_seed = plan_adopt(manager, bound, "custom-vault", expected=2)
    assert bound_seed["writer_compatibility"]["status"] == "compatible" and not bound_seed["writer_compatibility"]["active_research"]
    bound_proof = proof_for(bound_seed, work / "bound-proof.json")
    assert plan_adopt(manager, bound, "custom-vault", bound_proof)["status"] == "planned"
    broken_bound = legacy_workspace(work, "research-bound-broken"); bound_research(broken_bound, "custom-vault", matching_case=False)
    blocked_bound = plan_adopt(manager, broken_bound, "custom-vault", expected=2)
    assert blocked_bound["writer_compatibility"]["status"] == "blocked" and "research_record_uninspectable" in blocked_bound["writer_compatibility"]["reason_codes"]

    # Foreign manager ownership blocks every v2 mutation surface.
    locking = work / "locking"; setup(package, locking); lock = locking / ".brain-management" / "manager.lock"; lock.parent.mkdir(parents=True); lock.write_bytes(config_bytes({"schema":"brain_management_writer_exclusion_v1", "transaction_id":"txn-other", "workspace":str(locking.resolve())}))
    assert run(str(manager), "status", "--workspace", str(locking), cwd=locking, expected=2)["status"] == "blocked"
    assert run(str(manager), "plan-update", "--workspace", str(locking), cwd=locking, expected=2)["status"] == "blocked"
    assert run(str(locking / "tools" / "brain.py"), "guide", cwd=locking, expected=2)["status"] == "blocked"
    source = locking / "writer-source.txt"; source.write_text("writer guard\n", encoding="utf-8")
    assert run(str(locking / "tools" / "research_program.py"), "--root", str(locking), "init-topic", "--topic-id", "guard", "--title", "guard", "--profile-version", "1", "--purpose", "guard", "--scope", "guard", "--focus", "[]", "--evidence-contract", "{}", "--output-contract", "{}", "--classifications", "[]", "--mode", "manual_research", "--daily-case-cap", "1", "--total-case-cap", "1", cwd=locking, expected=2)["status"] == "blocked"
    assert run(str(locking / "tools" / "ingest_rail.py"), "--root", str(locking), "start", "--text-file", str(source), cwd=locking, expected=2)["status"] == "blocked"
    compiler_guard = subprocess.run([PYTHON, "-B", str(locking / "tools" / "compiler_worker_v2.py"), "--root", str(locking), "accept-job", "--text-file", str(source), "--source-id", "guard"], cwd=locking, text=True, encoding="utf-8", errors="replace", capture_output=True)
    assert compiler_guard.returncode == 2 and json.loads(compiler_guard.stderr)["status"] == "blocked"
    direct_adapter = subprocess.run([PYTHON, "-B", "-c", "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd() / 'tools')); from obsidian_vault_adapter import write_base_files; write_base_files(Path.cwd() / 'Vault')"], cwd=locking, text=True, encoding="utf-8", errors="replace", capture_output=True)
    assert direct_adapter.returncode != 0 and "workspace manager is applying" in direct_adapter.stderr

    # Existing control state is authenticated before every public mutation
    # entrance.  A missing, empty, foreign, or malformed guard may never be
    # created, repaired, or otherwise changed by status, plan/apply, or
    # rollback.  Apply deliberately receives a ticket minted before mutation
    # so this also proves admission-ticket invalidation under a race.
    guard_cases = {
        "missing": lambda guard, workspace: guard.unlink(),
        "empty": lambda guard, workspace: guard.write_bytes(b""),
        "foreign": lambda guard, workspace: guard.write_bytes(config_bytes({"schema":"creative_second_brain_manager_guard_v1", "workspace":str((workspace.parent / "another-workspace").resolve())})),
        "malformed": lambda guard, workspace: guard.write_bytes(b"not-a-guard\\n"),
    }
    for label, mutate_guard in guard_cases.items():
        workspace = work / ("guard-" + label); setup(package, workspace); make_prior_managed_bytes(workspace)
        bootstrap = run(str(manager), "plan-update", "--workspace", str(workspace), cwd=workspace); assert apply(manager, "update", workspace, bootstrap)["status"] == "applied"
        make_prior_managed_bytes(workspace)
        stale = run(str(manager), "plan-update", "--workspace", str(workspace), cwd=workspace)
        guard = workspace / ".brain-management" / "manager.guard"; mutate_guard(guard, workspace); baseline = hashes(workspace, control=True)
        assert run(str(manager), "status", "--workspace", str(workspace), cwd=workspace, expected=2)["status"] == "blocked" and hashes(workspace, control=True) == baseline
        assert run(str(manager), "plan-update", "--workspace", str(workspace), cwd=workspace, expected=2)["status"] == "blocked" and hashes(workspace, control=True) == baseline
        assert apply(manager, "update", workspace, stale, expected=2)["status"] == "blocked" and hashes(workspace, control=True) == baseline

        rollback_workspace = work / ("guard-rollback-" + label); setup(package, rollback_workspace); make_prior_managed_bytes(rollback_workspace)
        rollback_bootstrap = run(str(manager), "plan-update", "--workspace", str(rollback_workspace), cwd=rollback_workspace); assert apply(manager, "update", rollback_workspace, rollback_bootstrap)["status"] == "applied"
        make_prior_managed_bytes(rollback_workspace)
        pending = run(str(manager), "plan-update", "--workspace", str(rollback_workspace), cwd=rollback_workspace)
        assert apply(manager, "update", rollback_workspace, pending, interrupt="after_intent", expected=2)["status"] == "blocked"
        transaction = latest_tx_id(rollback_workspace); rollback_guard = rollback_workspace / ".brain-management" / "manager.guard"; mutate_guard(rollback_guard, rollback_workspace); rollback_baseline = hashes(rollback_workspace, control=True)
        assert rollback(manager, rollback_workspace, transaction, expected=2)["status"] == "blocked" and hashes(rollback_workspace, control=True) == rollback_baseline

    # Every persisted interruption has a deterministic, byte-exact rollback.
    for interruption in ("after_intent", "after_stage", "after_target"):
        workspace = work / ("tx-" + interruption); setup(package, workspace); make_prior_managed_bytes(workspace); before = hashes(workspace)
        update = run(str(manager), "plan-update", "--workspace", str(workspace), cwd=workspace); assert apply(manager, "update", workspace, update, interrupt=interruption, expected=2)["status"] == "blocked"
        transaction = tx_id(workspace)
        if interruption == "after_intent":
            checkpoint = json.loads((workspace / ".brain-management" / "transactions" / transaction / "checkpoints" / "000001.json").read_text(encoding="utf-8"))
            assert checkpoint["state"] == "intent" and not any((workspace / ".brain-management" / "transactions" / transaction / "backups").rglob("*"))
        if interruption == "after_target": assert rollback(manager, workspace, transaction, "rollback_after_target", expected=2)["status"] == "blocked"
        assert rollback(manager, workspace, transaction)["status"] == "rolled_back" and hashes(workspace) == before and rollback(manager, workspace, transaction)["status"] == "already_rolled_back"

    add_workspace = legacy_workspace(work, "tx-add"); add_before = hashes(add_workspace); add_blocked = plan_adopt(manager, add_workspace, "custom-vault", expected=2); add_proof = proof_for(add_blocked, work / "add-proof.json"); add_plan = plan_adopt(manager, add_workspace, "custom-vault", add_proof)
    assert apply(manager, "adopt", add_workspace, add_plan, add_proof, "after_target", expected=2)["status"] == "blocked" and rollback(manager, add_workspace, tx_id(add_workspace))["status"] == "rolled_back" and hashes(add_workspace) == add_before

    remove_workspace = work / "tx-remove"; setup(package, remove_workspace); removed = remove_workspace / "tools" / "old-managed.py"; removed.write_text("old\n", encoding="utf-8")
    removal = json.loads((remove_workspace / "brain.json").read_text(encoding="utf-8")); removal["managed_files"]["tools/old-managed.py"] = digest(removed.read_bytes()); (remove_workspace / "brain.json").write_bytes(config_bytes(removal)); remove_before = hashes(remove_workspace)
    remove_plan = run(str(manager), "plan-update", "--workspace", str(remove_workspace), cwd=remove_workspace); assert any(row["action"] == "remove" for row in remove_plan["actions"])
    assert apply(manager, "update", remove_workspace, remove_plan)["status"] == "applied" and not removed.exists() and rollback(manager, remove_workspace, tx_id(remove_workspace))["status"] == "rolled_back" and hashes(remove_workspace) == remove_before

    third_party = work / "tx-third-party"; setup(package, third_party); make_prior_managed_bytes(third_party); third_plan = run(str(manager), "plan-update", "--workspace", str(third_party), cwd=third_party); apply(manager, "update", third_party, third_plan, interrupt="after_target", expected=2); (third_party / "brain.json").write_text("foreign\n", encoding="utf-8")
    assert rollback(manager, third_party, tx_id(third_party), expected=2)["status"] == "blocked" and (third_party / "brain.json").read_text(encoding="utf-8") == "foreign\n"

    foreign_lock = work / "tx-foreign-lock"; setup(package, foreign_lock); make_prior_managed_bytes(foreign_lock); foreign_plan = run(str(manager), "plan-update", "--workspace", str(foreign_lock), cwd=foreign_lock); baseline = hashes(foreign_lock)
    apply(manager, "update", foreign_lock, foreign_plan, interrupt="after_intent", expected=2); transaction = tx_id(foreign_lock); lock = foreign_lock / ".brain-management" / "manager.lock"; lock.write_bytes(config_bytes({"schema":"brain_management_writer_exclusion_v1", "transaction_id":"txn-" + "f" * 32, "workspace":str(foreign_lock.resolve())}))
    assert rollback(manager, foreign_lock, transaction, expected=2)["status"] == "blocked" and hashes(foreign_lock) == baseline and lock.exists()

    foreign_stage = work / "tx-foreign-stage"; setup(package, foreign_stage); make_prior_managed_bytes(foreign_stage); stage_plan = run(str(manager), "plan-update", "--workspace", str(foreign_stage), cwd=foreign_stage); apply(manager, "update", foreign_stage, stage_plan, interrupt="after_target", expected=2); transaction = tx_id(foreign_stage)
    intent = json.loads((foreign_stage / ".brain-management" / "transactions" / transaction / "intent.json").read_text(encoding="utf-8")); changed = next(row for row in intent["actions"] if row["action"] != "keep"); target = foreign_stage / changed["path"]; target_before = target.read_bytes() if target.exists() else b""; staged = target.with_name("." + target.name + ".brain-mgmt-foreign"); staged.write_bytes(b"foreign")
    assert rollback(manager, foreign_stage, transaction, expected=2)["status"] == "blocked" and (target.read_bytes() if target.exists() else b"") == target_before and staged.exists()

    malformed = work / "malformed-transaction"; setup(package, malformed); baseline = hashes(malformed); (malformed / ".brain-management" / "transactions" / "txn-foreign").mkdir(parents=True)
    assert run(str(manager), "status", "--workspace", str(malformed), cwd=malformed, expected=2)["status"] == "blocked" and hashes(malformed) == baseline

    # A syntactically self-consistent foreign transaction must not turn a
    # private vault file into a removable managed target.
    private_tx = work / "tx-private-scope"; setup(package, private_tx); make_prior_managed_bytes(private_tx)
    bootstrap = run(str(manager), "plan-update", "--workspace", str(private_tx), cwd=private_tx); assert apply(manager, "update", private_tx, bootstrap)["status"] == "applied"
    private_file = private_tx / "Vault" / "private.md"; private_file.write_text("private fixture\\n", encoding="utf-8")
    binding_plan = run(str(manager), "plan-update", "--workspace", str(private_tx), cwd=private_tx)
    transaction = "txn-" + "9" * 32; tx = private_tx / ".brain-management" / "transactions" / transaction; (tx / "backups").mkdir(parents=True); (tx / "checkpoints").mkdir()
    forged = {"schema":"creative_second_brain_management_transaction_v2", "transaction_id":transaction, "workspace":str(private_tx.resolve()), "plan_hash":"a" * 64, "package_binding":binding_plan["package_binding"], "vault":"Vault", "actions":[{"path":"Vault/private.md", "action":"add", "prior_sha256":None, "expected_sha256":digest(private_file.read_bytes()), "prior_exists":False, "file_role":"release_runtime_or_entry"}]}
    (tx / "intent.json").write_bytes(config_bytes(forged)); forged_hash = digest((tx / "intent.json").read_bytes())
    (tx / "checkpoints" / "000001.json").write_bytes(config_bytes({"schema":"creative_second_brain_management_checkpoint_v2", "transaction_id":transaction, "intent_sha256":forged_hash, "sequence":1, "previous_checkpoint_sha256":"", "state":"intent", "actions":{"Vault/private.md":"pending"}}))
    private_before = hashes(private_tx, control=True)
    assert rollback(manager, private_tx, transaction, expected=2)["status"] == "blocked" and private_file.exists() and hashes(private_tx, control=True) == private_before

    print(json.dumps({"status":"passed", "checks":["fresh_v2", "adopt_update", "adoption_resolution_exact_hash", "update_resolution_exact_hash", "update_resolution_recovery_ownership", "update_existing_unowned_reconciliation", "job_fail_closed", "research_hard_gate", "writer_boundary", "control_admission_zero_write", "private_transaction_scope", "transaction_recovery", "candidate_bound_host_adaptation"]}, ensure_ascii=False)); return 0


if __name__ == "__main__": raise SystemExit(main())
