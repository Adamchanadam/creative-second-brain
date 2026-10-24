"""Transactional sync for the shared brain-* entry surface in a host-owned workspace."""
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

import manage_workspace as mg
from setup_workspace import skill_entry_payloads, workspace_config_bytes
from verify_package import verify

SCHEMA = "creative_second_brain_shared_surface_sync_v1"
SURFACE_SCHEMA = "creative_second_brain_shared_surface_v1"
OVERLAY_SCHEMA = "creative_second_brain_host_runtime_overrides_v1"


def emit(value: dict) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2)); return 0


def is_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def shared_payloads() -> tuple[dict[str, bytes], dict]:
    verify(mg.PACKAGE)
    inv = mg.inventory()
    payloads = {"tools/brain.py": (mg.PACKAGE / "scripts" / "brain.py").read_bytes()}
    entries = skill_entry_payloads("public-portable")
    wanted = {item["skill"] for item in inv["daily_entry_contract"]["managed_entries"]}
    selected = {path: value for path, value in entries.items() if path.endswith("/SKILL.md")}
    actual = {path.split("/")[2] for path in selected}
    if actual != wanted or len(selected) != len(wanted):
        raise mg.ManagerError("shared entry payload inventory is invalid")
    payloads.update(selected)
    return payloads, {"package_binding": mg.binding(), "hashes": {path: mg.sha_b(value) for path, value in payloads.items()}}


def parse_overrides(cfg: dict, paths: set[str]) -> dict[str, dict]:
    value = cfg.get("host_runtime_overrides")
    if value is None:
        return {}
    if not isinstance(value, dict) or value.get("schema") != OVERLAY_SCHEMA or not isinstance(value.get("files"), dict):
        raise mg.ManagerError("host runtime override contract is malformed")
    result = {}
    for rel, record in value["files"].items():
        if not isinstance(rel, str) or rel not in cfg["managed_files"] or rel in paths or not isinstance(record, dict):
            raise mg.ManagerError("host runtime override path is invalid")
        base, host = record.get("base_sha256"), record.get("host_sha256")
        if base != cfg["managed_files"][rel] or not is_hash(host) or host == base:
            raise mg.ManagerError("host runtime override hash is invalid")
        result[rel] = {"base_sha256": base, "host_sha256": host}
    return result


def plan(workspace: Path, requested: list[str] | None = None, held_lock_owner=None, admission_ticket=None, locked_guard_data=None) -> dict:
    workspace = workspace.absolute(); mg.plain(workspace)
    if not workspace.is_dir():
        raise mg.ManagerError("existing workspace directory required")
    cfg = mg.config(workspace, True)
    data, meta = shared_payloads(); paths = set(data)
    installed_version = mg.version_key(cfg.get("package_version"))
    source_version = mg.version_key(meta["package_binding"].get("package_version"))
    if source_version < installed_version:
        raise mg.ManagerError("shared entry source version predates installed product version")
    if any(mg.private(path, mg.prefixes(str(cfg["vault"]))) for path in paths):
        raise mg.ManagerError("shared surface overlaps a private scope")
    current_surface = cfg.get("shared_surface")
    prior_surface: dict[str, str] = {}
    if current_surface is not None:
        if not isinstance(current_surface, dict) or current_surface.get("schema") != SURFACE_SCHEMA or not isinstance(current_surface.get("files"), dict):
            raise mg.ManagerError("shared surface contract is malformed")
        prior_surface = current_surface["files"]
        if set(prior_surface) != paths or not all(is_hash(value) for value in prior_surface.values()):
            raise mg.ManagerError("shared surface file contract is malformed")
        binding = current_surface.get("package_binding")
        if not isinstance(binding, dict) or source_version < mg.version_key(binding.get("package_version")):
            raise mg.ManagerError("shared entry source version predates existing shared surface")
    admission = mg.control_admission(workspace, held_lock_owner, admission_ticket, locked_guard_data)
    overlays = parse_overrides(cfg, paths)
    wanted = set()
    for raw in requested or []:
        rel = mg.rel(raw).as_posix()
        if rel not in cfg["managed_files"] or rel in paths:
            raise mg.ManagerError("host runtime override is not an eligible non-shared managed file")
        wanted.add(rel)
    unknown_drift = []
    for rel, base in cfg["managed_files"].items():
        if rel in paths:
            continue
        target = mg.child(workspace, rel)
        actual = mg.sha(target) if target.is_file() else None
        if rel in wanted:
            if actual is None or actual == base:
                raise mg.ManagerError("host runtime override must name a modified regular managed file")
            overlays[rel] = {"base_sha256": base, "host_sha256": actual}
        record = overlays.get(rel)
        if record is not None:
            if actual != record["host_sha256"]:
                unknown_drift.append({"path": rel, "actual_sha256": actual, "expected_host_sha256": record["host_sha256"], "reason": "registered_host_runtime_changed"})
        elif actual != base:
            unknown_drift.append({"path": rel, "actual_sha256": actual, "expected_base_sha256": base, "reason": "unregistered_host_runtime_drift"})
    next_cfg = dict(cfg)
    # Shared-entry sync never upgrades the installed product.  A full managed
    # update owns that version transition after every common runtime file has
    # passed its own compatibility review.  Moving to v3 makes older v2-only
    # sync tools fail closed after this bounded transaction.
    next_cfg["schema"] = mg.CONFIG
    next_cfg["shared_surface"] = {"schema": SURFACE_SCHEMA, "package_binding": meta["package_binding"], "files": dict(sorted(meta["hashes"].items()))}
    if overlays:
        next_cfg["host_runtime_overrides"] = {"schema": OVERLAY_SCHEMA, "files": dict(sorted(overlays.items()))}
    else:
        next_cfg.pop("host_runtime_overrides", None)
    data["brain.json"] = workspace_config_bytes(next_cfg)
    expected = {path: mg.sha_b(value) for path, value in data.items()}
    actions = []
    for rel in sorted(expected):
        target = mg.child(workspace, rel); actual = mg.sha(target) if target.is_file() else None
        prior = mg.sha(workspace / "brain.json") if rel == "brain.json" else prior_surface.get(rel, cfg["managed_files"].get(rel))
        if actual == expected[rel]:
            action, disposition = "keep", "already_matches_candidate"
        elif rel != "brain.json" and actual == prior:
            action, disposition = "replace", "matches_recorded_shared_hash"
        elif rel == "brain.json" and actual == prior:
            action, disposition = "replace", "matches_current_configuration"
        else:
            action, disposition = "conflict", "preserve_unowned_or_modified_file"
        actions.append({"path": rel, "action": action, "disposition": disposition, "actual_sha256": actual, "prior_sha256": prior, "expected_sha256": expected[rel], "file_role": "root_configuration" if rel == "brain.json" else "shared_entry_surface"})
    incomplete = []
    for transaction in mg.txs(workspace):
        state = mg.recorded_txstate(transaction, workspace)
        if state not in {"committed", "rolled_back"}:
            incomplete.append({"transaction_id": transaction.name, "state": state})
    writer = mg.writer(workspace, str(cfg["vault"]))
    reasons = []
    if any(row["action"] == "conflict" for row in actions): reasons.append("shared_surface_conflicts")
    if unknown_drift: reasons.append("unregistered_or_changed_host_runtime")
    if incomplete: reasons.append("incomplete_management_transaction")
    if writer["status"] == "blocked": reasons.append("writer_record_uninspectable_or_locked")
    out = {"schema": SCHEMA, "status": "blocked" if reasons else "planned", "mode": "shared_surface", "workspace": str(workspace), "workspace_identity": str(workspace.resolve()), "vault": cfg["vault"], "package_binding": meta["package_binding"], "candidate_runtime_hashes": dict(sorted(meta["hashes"].items())), "installed_product_version": {"before": cfg["package_version"], "after": next_cfg["package_version"], "change": "unchanged_by_shared_entry_sync"}, "workspace_schema": {"before": cfg.get("schema"), "after": next_cfg["schema"]}, "adoption_resolution": {"status": "not_applicable"}, "update_resolution": {"status": "not_applicable"}, "actions": actions, "writes": sum(row["action"] == "replace" for row in actions), "configuration": next_cfg, "requested_host_runtime_overrides": sorted(wanted), "host_runtime_overrides": sorted(overlays), "unregistered_runtime_drift": unknown_drift, "writer_compatibility": writer, "incomplete_transactions": incomplete, "control_admission": admission, "blocking_reasons": reasons}
    signed = dict(out); signed.pop("manager_exclusion_lock", None)
    out["plan_hash"] = mg.sha_b(mg.canon(signed)); return out


def apply(plan_value: dict, expected_hash: str, interrupt: str = "") -> dict:
    if plan_value.get("status") != "planned" or plan_value.get("plan_hash") != expected_hash:
        raise mg.ManagerError("blocked or changed plan cannot apply")
    workspace = Path(plan_value["workspace"]).absolute(); requested = plan_value.get("requested_host_runtime_overrides", [])
    fresh = plan(workspace, requested)
    if fresh.get("status") != "planned" or fresh.get("plan_hash") != expected_hash:
        raise mg.ManagerError("package, tree, configuration or writer facts changed")
    ticket = plan_value.get("control_admission")
    if not isinstance(ticket, dict):
        raise mg.ManagerError("plan lacks management admission")
    transaction_id = "txn-" + uuid.uuid4().hex; begun = False; release = True
    with mg.manager_lease(workspace, transaction_id, ticket) as (_, locked_guard_data):
        try:
            fresh = plan(workspace, requested, transaction_id, ticket, locked_guard_data)
            if fresh.get("status") != "planned" or fresh.get("plan_hash") != expected_hash:
                raise mg.ManagerError("package, tree, configuration or writer facts changed")
            transaction, intent, intent_hash, states = mg.begin(workspace, fresh, transaction_id); begun = True
            if interrupt == "after_intent": raise mg.ManagerError("test interruption after intent")
            mg.cp(transaction, workspace, intent_hash, "preparing", states)
            for row in intent["actions"]:
                if row["action"] == "replace":
                    source = mg.child(workspace, row["path"]); backup = mg.child(transaction / "backups", row["path"]); backup.parent.mkdir(parents=True, exist_ok=True)
                    if not source.is_file() or mg.sha(source) != row["prior_sha256"]: raise mg.ManagerError("target changed before backup")
                    with source.open("rb") as reader, backup.open("xb") as writer:
                        writer.write(reader.read()); writer.flush(); os.fsync(writer.fileno())
                    if mg.sha(backup) != row["prior_sha256"]: raise mg.ManagerError("backup mismatch")
                    states[row["path"]] = "prepared"
            mg.cp(transaction, workspace, intent_hash, "prepared", states)
            if all(row["action"] == "keep" for row in intent["actions"]): mg.cp(transaction, workspace, intent_hash, "applying", states)
            data, _ = shared_payloads()
            for row in intent["actions"]:
                if row["action"] == "keep": states[row["path"]] = "verified"; continue
                target = mg.child(workspace, row["path"]); states[row["path"]] = "prepared"; mg.cp(transaction, workspace, intent_hash, "applying", states, {"path": row["path"]})
                payload = workspace_config_bytes(fresh["configuration"]) if row["path"] == "brain.json" else data[row["path"]]
                if mg.sha_b(payload) != row["expected_sha256"]: raise mg.ManagerError("candidate payload drift")
                mg.stage(target, transaction.name, payload, "target", interrupt == "after_stage")
                if interrupt == "after_target": raise mg.ManagerError("test interruption after target mutation")
                states[row["path"]] = "applied"; mg.cp(transaction, workspace, intent_hash, "applying", states, {"path": row["path"]})
            for row in intent["actions"]:
                target = mg.child(workspace, row["path"])
                if not target.is_file() or mg.sha(target) != row["expected_sha256"]: raise mg.ManagerError("post-action readback failed")
                states[row["path"]] = "verified"
            mg.check_stages(workspace, transaction, intent); mg.cp(transaction, workspace, intent_hash, "verified", states)
            if interrupt == "after_verified": raise mg.ManagerError("test interruption before commit")
            mg.cp(transaction, workspace, intent_hash, "committed", states)
            return {"status": "applied", "transaction_id": transaction.name, "writes": fresh["writes"]}
        except BaseException:
            release = not begun
            raise
        finally:
            if release: mg.release_manager_lock(workspace, transaction_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("plan"); p.add_argument("--workspace", type=Path, required=True); p.add_argument("--host-runtime-override", action="append", default=[])
    p = sub.add_parser("apply"); p.add_argument("--workspace", type=Path, required=True); p.add_argument("--plan-hash", required=True); p.add_argument("--host-runtime-override", action="append", default=[]); p.add_argument("--test-interrupt", choices=("after_intent", "after_stage", "after_target", "after_verified"), default="")
    args = parser.parse_args()
    try:
        plan_value = plan(args.workspace, args.host_runtime_override)
        if args.command == "plan": out = plan_value
        else: out = apply(plan_value, args.plan_hash, args.test_interrupt)
        emit(out)
        return 0 if out["status"] != "blocked" else 2
    except (mg.ManagerError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        return emit({"status": "blocked", "reason": str(error)}) or 2


if __name__ == "__main__":
    raise SystemExit(main())
