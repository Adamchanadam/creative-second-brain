"""Bounded, file-backed research program runner.

This runner deliberately owns task locks, reservations and counters only.  It
does not search, browse, write a second vault, or decide semantic quality.  A
completed Compiler receipt remains the sole evidence for accepting a case.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    from tools.management_exclusion import assert_management_idle
except ModuleNotFoundError:
    from management_exclusion import assert_management_idle  # type: ignore

try:
    from tools.source_identity import normalize_url
except ModuleNotFoundError:
    from source_identity import normalize_url  # type: ignore

try:
    from tools.case_evidence_contract import CaseEvidenceError, case_asset_paths, package_manifest, validate_selection_contract
    from tools.compiler_worker_v2 import (
        COMPLETION_SCHEMA_VERSION,
        CompilerWorkerV2Error,
        completed_receipt_readback,
        read_json as read_compiler_json,
        sha256_bytes,
    )
except ModuleNotFoundError:
    from case_evidence_contract import CaseEvidenceError, case_asset_paths, package_manifest, validate_selection_contract  # type: ignore
    from compiler_worker_v2 import (  # type: ignore
        COMPLETION_SCHEMA_VERSION,
        CompilerWorkerV2Error,
        completed_receipt_readback,
        read_json as read_compiler_json,
        sha256_bytes,
    )

try:
    from tools.obsidian_vault_adapter import AdapterStop, write_research_reuse_application
except ModuleNotFoundError:
    from obsidian_vault_adapter import AdapterStop, write_research_reuse_application  # type: ignore

try:
    from tools.workspace_paths import WorkspacePathError, vault_path
except ModuleNotFoundError:
    from workspace_paths import WorkspacePathError, vault_path  # type: ignore

try:
    from tools.research_schedule_contract import host_timezone_status, scheduler_status
except ModuleNotFoundError:
    from research_schedule_contract import host_timezone_status, scheduler_status  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "research_program_v1"
CASE_STATES = {"reserved", "committing"}
ACTION_LIMITS = {"search": "searches", "deep_read": "deep_reads", "supplement": "deep_reads"}
SUPPORTED_ASSET_MODES = {"case_evidence", "formal_infographic"}
SUPPORTED_SOURCE_PROFILES = {"article", "paper"}
SCHEDULED_QUOTA_LANE = "scheduled"
DIRECT_USER_QUOTA_LANE = "direct_user"
QUOTA_LANE_POLICY_VERSION = "scheduled_daily_direct_user_v2"


class ProgramError(ValueError):
    pass


def safe_id(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise ProgramError(f"invalid {label}")
    return value


def topic_dir(root: Path, topic_id: str) -> Path:
    try:
        return vault_path(root) / "_registry" / "research-programs" / safe_id(topic_id, "topic_id")
    except WorkspacePathError as exc:
        raise ProgramError(str(exc)) from exc


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProgramError(f"cannot read {path.name}") from exc
    if not isinstance(value, dict):
        raise ProgramError(f"{path.name} must be an object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    staging = path.with_suffix(path.suffix + ".staging")
    staging.write_text(encoded, encoding="utf-8")
    if staging.read_text(encoding="utf-8") != encoded:
        raise ProgramError("research program state readback failed")
    os.replace(staging, path)


@contextmanager
def program_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".program.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ProgramError("research program lock is held; resume or inspect the active run") from exc
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(f"pid={os.getpid()}\n")
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA,
        "accepted_cases": [],
        "withdrawn_cases": [],
        "active_cases": {},
        "discovery_slots": {},
        "rejected_cases": [],
        "rejected_discoveries": [],
        "day_runs": {},
        "day_run_history": {},
        "manual_allowances": {},
        "runs": {},
    }


def load_topic(root: Path, topic_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    directory = topic_dir(root, topic_id)
    config_path = directory / "topic.json"
    state_path = directory / "state.json"
    if not config_path.is_file():
        raise ProgramError("research topic is not initialized")
    config = read_json(config_path)
    if config.get("schema_version") != SCHEMA or config.get("topic_id") != topic_id:
        raise ProgramError("research topic configuration is invalid")
    state = read_json(state_path) if state_path.is_file() else initial_state()
    if state.get("schema_version") != SCHEMA:
        raise ProgramError("research topic state is invalid")
    # v1 state written before discovery slots existed stays safely resumable.
    state.setdefault("discovery_slots", {})
    state.setdefault("rejected_cases", [])
    state.setdefault("rejected_discoveries", [])
    state.setdefault("withdrawn_cases", [])
    state.setdefault("day_runs", {})
    state.setdefault("day_run_history", {})
    state.setdefault("manual_allowances", {})
    if not isinstance(state["discovery_slots"], dict):
        raise ProgramError("research discovery-slot state is invalid")
    if (
        not isinstance(state["rejected_cases"], list)
        or not isinstance(state["rejected_discoveries"], list)
        or not isinstance(state["withdrawn_cases"], list)
    ):
        raise ProgramError("research rejection state is invalid")
    if not isinstance(state["day_runs"], dict):
        raise ProgramError("research day-run state is invalid")
    if not isinstance(state["day_run_history"], dict):
        raise ProgramError("research day-run-history state is invalid")
    if not isinstance(state["manual_allowances"], dict):
        raise ProgramError("research manual-allowance state is invalid")
    validate_topic_definition(config)
    return directory, config, state


def local_day(value: str | None, timezone: str) -> tuple[str, str]:
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else datetime.now(tz=ZoneInfo(timezone))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=ZoneInfo(timezone))
        local = moment.astimezone(ZoneInfo(timezone))
    except Exception as exc:
        raise ProgramError("invalid --at or topic timezone") from exc
    return local.date().isoformat(), local.isoformat()


def event_times(args: argparse.Namespace, timezone: str) -> tuple[str, str, str | None]:
    """Keep an isolated test clock out of formal observed-time evidence."""
    if args.at and not getattr(args, "isolated_test_clock", False):
        raise ProgramError("--at is an isolated test clock; pass --isolated-test-clock or omit it for a formal run")
    day, effective_at = local_day(args.at, timezone)
    observed_at = datetime.now(ZoneInfo(timezone)).isoformat()
    return day, observed_at, effective_at if args.at else None


def active_cases(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    active = state.get("active_cases")
    if not isinstance(active, dict):
        raise ProgramError("research active-case state is invalid")
    return active


def accepted_cases(state: dict[str, Any]) -> list[dict[str, Any]]:
    accepted = state.get("accepted_cases")
    if not isinstance(accepted, list):
        raise ProgramError("research accepted-case state is invalid")
    return accepted


def withdrawn_cases(state: dict[str, Any]) -> list[dict[str, Any]]:
    withdrawn = state.get("withdrawn_cases")
    if not isinstance(withdrawn, list):
        raise ProgramError("research withdrawal state is invalid")
    return withdrawn


def method_verification_state(case: dict[str, Any]) -> str:
    """Return the strict reuse state without rewriting historical records."""
    record = case.get("method_verification")
    if isinstance(record, dict) and record.get("state") in {"verified", "not_assessed", "needs_revalidation"}:
        return str(record["state"])
    return "legacy_unreviewed"


def method_verification_summary(state: dict[str, Any]) -> dict[str, int]:
    summary = {"verified": 0, "not_assessed": 0, "needs_revalidation": 0, "legacy_unreviewed": 0}
    for case in accepted_cases(state):
        summary[method_verification_state(case)] += 1
    return summary


def discovery_slots(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    slots = state.get("discovery_slots")
    if not isinstance(slots, dict):
        raise ProgramError("research discovery-slot state is invalid")
    return slots


def reconcile_pending_for_day(state: dict[str, Any], day: str) -> None:
    """An unfinished case occupies today's slot before any new external action."""
    for case in active_cases(state).values():
        if case.get("status") in CASE_STATES:
            case["effective_day"] = day
    for slot in discovery_slots(state).values():
        if slot.get("status") == "reserved":
            slot["effective_day"] = day


def capacity(state: dict[str, Any], config: dict[str, Any], day: str) -> dict[str, int]:
    accepted = accepted_cases(state)
    active = [case for case in active_cases(state).values() if case.get("status") in CASE_STATES]
    discoveries = [slot for slot in discovery_slots(state).values() if slot.get("status") == "reserved"]
    scheduled_accepted = [case for case in accepted if quota_lane_for_record(state, case) == SCHEDULED_QUOTA_LANE]
    direct_accepted = [case for case in accepted if quota_lane_for_record(state, case) == DIRECT_USER_QUOTA_LANE]
    scheduled_active = [case for case in active if quota_lane_for_record(state, case) == SCHEDULED_QUOTA_LANE]
    direct_active = [case for case in active if quota_lane_for_record(state, case) == DIRECT_USER_QUOTA_LANE]
    scheduled_discoveries = [slot for slot in discoveries if quota_lane_for_record(state, slot) == SCHEDULED_QUOTA_LANE]
    direct_discoveries = [slot for slot in discoveries if quota_lane_for_record(state, slot) == DIRECT_USER_QUOTA_LANE]
    return {
        "daily_accepted": sum(case.get("accepted_day") == day for case in scheduled_accepted),
        "daily_reserved": sum(case.get("effective_day") == day for case in scheduled_active)
        + sum(slot.get("effective_day") == day for slot in scheduled_discoveries),
        "scheduled_daily_accepted": sum(case.get("accepted_day") == day for case in scheduled_accepted),
        "scheduled_daily_reserved": sum(case.get("effective_day") == day for case in scheduled_active)
        + sum(slot.get("effective_day") == day for slot in scheduled_discoveries),
        "direct_user_daily_accepted": sum(case.get("accepted_day") == day for case in direct_accepted),
        "direct_user_daily_reserved": sum(case.get("effective_day") == day for case in direct_active)
        + sum(slot.get("effective_day") == day for slot in direct_discoveries),
        "direct_user_total_accepted": len(direct_accepted),
        "direct_user_total_reserved": len(direct_active) + len(direct_discoveries),
        "total_accepted": len(accepted),
        "total_reserved": len(active) + len(discoveries),
        "daily_cap": int(config["daily_case_cap"]),
        "total_cap": int(config["total_case_cap"]),
    }


def manual_allowances(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    allowances = state.get("manual_allowances")
    if not isinstance(allowances, dict):
        raise ProgramError("research manual-allowance state is invalid")
    return allowances


def day_run_history(state: dict[str, Any], day: str) -> list[str]:
    history = state["day_run_history"].setdefault(day, [])
    if not isinstance(history, list) or any(not isinstance(item, str) for item in history):
        raise ProgramError("research day-run-history entry is invalid")
    return history


def append_day_run_history(state: dict[str, Any], day: str, run_id: str) -> None:
    history = day_run_history(state, day)
    if run_id not in history:
        history.append(run_id)


def allowance_for_run(state: dict[str, Any], run: dict[str, Any]) -> dict[str, Any] | None:
    allowance_id = str(run.get("manual_allowance_id") or "")
    if not allowance_id:
        return None
    allowance = manual_allowances(state).get(allowance_id)
    if not isinstance(allowance, dict) or allowance.get("consumed_by_run_id") != run.get("run_id"):
        raise ProgramError("manual allowance does not belong to this run")
    return allowance


def direct_command_for_run(run: dict[str, Any]) -> dict[str, str] | None:
    """Read one recorded direct-user-command scope without inventing authority."""
    command = run.get("direct_command")
    if command is None:
        return None
    if not isinstance(command, dict):
        raise ProgramError("direct command record is invalid")
    source = str(command.get("source") or "").strip()
    recorded_at = str(command.get("recorded_at") or "").strip()
    if run.get("execution_mode") != "manual_research" or not source or not recorded_at:
        raise ProgramError("direct command record is invalid")
    original_mode = str(command.get("original_execution_mode") or "").strip()
    if original_mode and original_mode not in {"scheduled_research", "pilot_existing_candidate_engineering"}:
        raise ProgramError("direct command original execution mode is invalid")
    result = {"source": source, "recorded_at": recorded_at}
    if original_mode:
        result["original_execution_mode"] = original_mode
    return result


def quota_lane_for_run(run: dict[str, Any] | None) -> str:
    """A direct command never occupies one of the scheduled daily places."""
    return DIRECT_USER_QUOTA_LANE if isinstance(run, dict) and direct_command_for_run(run) is not None else SCHEDULED_QUOTA_LANE


def recorded_before_direct_command(record: dict[str, Any], direct_command: dict[str, str]) -> bool:
    """Historic terminal scheduled work remains scheduled after a later takeover."""
    terminal = record.get("status") in {"qualified", "rejected", "withdrawn"} or bool(record.get("accepted_day"))
    for key in ("accepted_at", "rejected_at"):
        value = str(record.get(key) or "").strip()
        if not value:
            continue
        try:
            recorded_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
            direct_at = datetime.fromisoformat(direct_command["recorded_at"].replace("Z", "+00:00"))
            return True if recorded_at.tzinfo is None or direct_at.tzinfo is None else recorded_at <= direct_at
        except (TypeError, ValueError):
            return True
    return terminal


def derived_quota_lane_for_record(state: dict[str, Any], record: dict[str, Any]) -> str:
    runs = state.get("runs")
    run = runs.get(str(record.get("run_id") or "")) if isinstance(runs, dict) else None
    if not isinstance(run, dict):
        return SCHEDULED_QUOTA_LANE
    direct = direct_command_for_run(run)
    if direct is None or recorded_before_direct_command(record, direct):
        return SCHEDULED_QUOTA_LANE
    return DIRECT_USER_QUOTA_LANE


def quota_lane_for_record(state: dict[str, Any], record: dict[str, Any]) -> str:
    recorded = record.get("quota_lane")
    return str(recorded) if recorded in {SCHEDULED_QUOTA_LANE, DIRECT_USER_QUOTA_LANE} else derived_quota_lane_for_record(state, record)


def reclassify_pending_direct_takeover(state: dict[str, Any], run_id: str) -> None:
    for case in active_cases(state).values():
        if case.get("run_id") == run_id and case.get("status") in CASE_STATES:
            case["quota_lane"] = DIRECT_USER_QUOTA_LANE
    for slot in discovery_slots(state).values():
        if slot.get("run_id") == run_id and slot.get("status") == "reserved":
            slot["quota_lane"] = DIRECT_USER_QUOTA_LANE


def action_budget_limit(state: dict[str, Any], config: dict[str, Any], run: dict[str, Any], budget_key: str) -> int | None:
    if direct_command_for_run(run) is not None:
        return None
    # Earlier record-only topic contracts deliberately record action counts
    # without using them as a source-work stop condition.
    if not isinstance(config.get("budget"), dict):
        return None
    allowance = allowance_for_run(state, run)
    if allowance is None:
        return int(config["budget"][budget_key])
    limits = allowance.get("limits")
    if not isinstance(limits, dict) or int(limits.get(budget_key, 0) or 0) < 1:
        raise ProgramError("manual allowance budget is invalid")
    return int(limits[budget_key])


def candidate_summary_target(state: dict[str, Any], config: dict[str, Any], run: dict[str, Any]) -> int | None:
    if direct_command_for_run(run) is not None:
        return None
    if not isinstance(config.get("budget"), dict):
        return None
    allowance = allowance_for_run(state, run)
    if allowance is None:
        return int(config["budget"]["candidate_summaries_target"])
    limits = allowance.get("limits")
    if not isinstance(limits, dict) or int(limits.get("candidate_summaries_target", 0) or 0) < 1:
        raise ProgramError("manual allowance candidate-summary target is invalid")
    return int(limits["candidate_summaries_target"])


def allowance_case_count(state: dict[str, Any], run_id: str) -> int:
    return (
        sum(case.get("run_id") == run_id and case.get("status") in CASE_STATES for case in active_cases(state).values())
        + sum(slot.get("run_id") == run_id and slot.get("status") == "reserved" for slot in discovery_slots(state).values())
        + sum(case.get("run_id") == run_id for case in accepted_cases(state))
    )


def require_manual_case_capacity(state: dict[str, Any], run: dict[str, Any]) -> None:
    if direct_command_for_run(run) is not None:
        return
    allowance = allowance_for_run(state, run)
    if allowance is None:
        return
    limits = allowance.get("limits")
    if not isinstance(limits, dict) or int(limits.get("cases", 0) or 0) < 1:
        raise ProgramError("manual allowance case capacity is invalid")
    if allowance_case_count(state, str(run.get("run_id") or "")) >= int(limits["cases"]):
        raise ProgramError("manual allowance case capacity is exhausted")


def run_has_unfinished_work(state: dict[str, Any], run_id: str) -> bool:
    if any(case.get("run_id") == run_id and case.get("status") in CASE_STATES for case in active_cases(state).values()):
        return True
    if any(slot.get("run_id") == run_id and slot.get("status") == "reserved" for slot in discovery_slots(state).values()):
        return True
    run = state["runs"].get(run_id, {})
    return isinstance(run, dict) and any(action.get("status") == "reserved" for action in run.get("actions", []))


def configured_classification_key(config: dict[str, Any]) -> str:
    """Keep the old UI/UX design family only as one profile's classifier."""
    classifications = config.get("classifications")
    key = classifications.get("primary_key") if isinstance(classifications, dict) else None
    return str(key or "design_family")


def case_classification(case: dict[str, Any]) -> str:
    """Read the generic field first, while remaining compatible with v1 state."""
    return str(case.get("classification") or case.get("design_family") or "")


def topic_evidence_contract(config: dict[str, Any]) -> dict[str, Any]:
    """Old topic records were case-evidence only; never reinterpret them as formal."""
    contract = config.get("evidence_contract")
    if not isinstance(contract, dict):
        return {
            "asset_mode": "case_evidence",
            "source_input_kinds": ["url"],
            "source_profiles": ["article"],
        }
    return contract


def topic_selection_contract(config: dict[str, Any]) -> dict[str, Any] | None:
    """Selection is opt-in so existing general research topics remain valid."""
    contract = config.get("selection_contract")
    if contract is None:
        return None
    try:
        return validate_selection_contract(contract)
    except CaseEvidenceError as exc:
        raise ProgramError(f"topic selection_contract is invalid: {exc}") from exc


def accepted_selection_contract(case: dict[str, Any]) -> dict[str, Any] | None:
    """Read the contract snapshot that qualified this case, never today's edit."""
    contract = case.get("selection_contract")
    if contract is None:
        return None
    try:
        return validate_selection_contract(contract)
    except CaseEvidenceError as exc:
        raise ProgramError(f"accepted case selection_contract is invalid: {exc}") from exc


def parse_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProgramError(f"{label} must be a JSON object") from exc
    if not isinstance(parsed, dict):
        raise ProgramError(f"{label} must be a JSON object")
    return parsed


def parse_string_list(value: object, label: str, *, allowed: set[str] | None = None) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ProgramError(f"{label} must be a non-empty list of strings")
    normalized = [item.strip() for item in value]
    if allowed is not None and any(item not in allowed for item in normalized):
        raise ProgramError(f"{label} contains an unsupported value")
    return normalized


def parse_case_classifications(value: str | None) -> dict[str, Any]:
    """Per-case facets are evidence labels, never copied from topic vocabulary."""
    if not value:
        return {}
    raw = parse_object(value, "classification_facets")
    allowed = {"design_families", "coverage_dimensions", "application_scenes", "tags", "values"}
    if set(raw) - allowed:
        raise ProgramError("classification_facets contains an unsupported key")
    result: dict[str, Any] = {}
    for key in ("design_families", "coverage_dimensions", "application_scenes", "tags"):
        if key in raw:
            result[key] = parse_string_list(raw[key], f"classification_facets.{key}")
    if "values" in raw:
        values = raw["values"]
        if not isinstance(values, dict) or not values:
            raise ProgramError("classification_facets.values must be a non-empty object")
        result["values"] = {str(key): parse_string_list(item, f"classification_facets.values.{key}") for key, item in values.items()}
    return result


def validate_topic_definition(config: dict[str, Any]) -> None:
    """A topic is a versioned contract, not a renamed copy of the UI/UX profile."""
    required_strings = ("profile_version", "purpose", "mode")
    if any(not str(config.get(key) or "").strip() for key in required_strings):
        raise ProgramError("topic requires profile_version, purpose and mode")
    for key in ("scope", "output_contract", "classifications"):
        if not isinstance(config.get(key), dict) or not config[key]:
            raise ProgramError(f"topic requires a non-empty {key} object")
    focus = config.get("focus")
    if not isinstance(focus, list) or not focus or any(not isinstance(item, str) or not item.strip() for item in focus):
        raise ProgramError("topic requires a non-empty focus list")
    contract = topic_evidence_contract(config)
    asset_mode = contract.get("asset_mode")
    if asset_mode not in SUPPORTED_ASSET_MODES:
        raise ProgramError("topic evidence_contract has an unsupported asset_mode")
    parse_string_list(contract.get("source_input_kinds"), "evidence_contract.source_input_kinds", allowed={"url"})
    parse_string_list(contract.get("source_profiles"), "evidence_contract.source_profiles", allowed=SUPPORTED_SOURCE_PROFILES)
    selection_contract = topic_selection_contract(config)
    if selection_contract is not None and asset_mode != "case_evidence":
        raise ProgramError("selection_contract requires case_evidence asset mode")
    budget = config.get("budget")
    if budget is not None and (
        not isinstance(budget, dict)
        or any(int(budget.get(key, 0) or 0) < 1 for key in ("searches", "candidate_summaries_target", "deep_reads"))
    ):
        raise ProgramError("topic budget must define positive searches, candidate_summaries_target and deep_reads when supplied")
    if config.get("mode") != "manual_research":
        raise ProgramError("portable research supports manual_research only; background scheduling belongs to OPS")


def require_capacity(state: dict[str, Any], config: dict[str, Any], day: str, run: dict[str, Any]) -> None:
    reconcile_pending_for_day(state, day)
    if direct_command_for_run(run) is not None:
        return
    counts = capacity(state, config, day)
    if counts["total_accepted"] + counts["total_reserved"] >= counts["total_cap"]:
        raise ProgramError("total case cap reached; zero external search is permitted")
    if counts["daily_accepted"] + counts["daily_reserved"] >= counts["daily_cap"]:
        raise ProgramError("today case capacity is reserved or completed; do not start new external research")


def case_identity(url: str) -> tuple[str, str]:
    canonical = normalize_url(url)
    return canonical, "url:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_run(state: dict[str, Any], run_id: str, config: dict[str, Any], day: str) -> dict[str, Any]:
    runs = state.get("runs")
    if not isinstance(runs, dict) or run_id not in runs or not isinstance(runs[run_id], dict):
        raise ProgramError("research run is not initialized")
    run = runs[run_id]
    mode = run.get("execution_mode", "manual_research")
    if mode != "manual_research":
        raise ProgramError("portable research run is not manual_research")
    direct_command = direct_command_for_run(run)
    if run.get("topic_id") != config.get("topic_id"):
        raise ProgramError("run topic contract drifted")
    recorded_profile = run.get("profile_version")
    current_profile = config.get("profile_version")
    if recorded_profile and current_profile and recorded_profile != current_profile:
        raise ProgramError("run profile version differs from the topic definition; do not silently inherit a revised contract")
    if state["day_runs"].get(day) != run_id:
        raise ProgramError("this day belongs to another research work unit; resume its run before external work")
    allowance = allowance_for_run(state, run)
    if allowance is not None:
        if mode != "manual_research":
            raise ProgramError("manual allowance is not valid for this run and day")
        if allowance.get("effective_day") != day and direct_command is None:
            raise ProgramError("manual allowance is not valid for this run and day")
        declared = allowance.get("declared_executor")
        if not isinstance(declared, dict) or run.get("declared_executor") != declared:
            raise ProgramError("manual allowance executor declaration differs from its approved record")
    run["last_checked_day"] = day
    return run


def enabled_topic_ids(root: Path) -> list[str]:
    """Read only explicit enabled topic profiles; never infer a topic."""
    registry = vault_path(root) / "_registry" / "research-programs"
    if not registry.is_dir():
        raise ProgramError("research registry is missing")
    enabled: list[str] = []
    for directory in sorted(registry.iterdir(), key=lambda item: item.name):
        if not directory.is_dir():
            continue
        try:
            directory.resolve().relative_to(registry.resolve())
            config = read_json(directory / "topic.json")
            topic_id = safe_id(str(config.get("topic_id") or ""), "topic_id")
            if topic_id != directory.name or config.get("schema_version") != SCHEMA:
                raise ProgramError("research topic configuration is invalid")
            validate_topic_definition(config)
        except (OSError, ProgramError) as exc:
            raise ProgramError(f"research registry entry {directory.name} is invalid") from exc
        if config.get("enabled") is True:
            enabled.append(topic_id)
    return enabled


def scheduler_test_inputs(args: argparse.Namespace) -> tuple[list[Path] | None, str | None]:
    automation = str(getattr(args, "test_automation_path", "") or "").strip()
    timezone = str(getattr(args, "test_host_timezone_id", "") or "").strip()
    if (automation or timezone) and not getattr(args, "isolated_test_clock", False):
        raise ProgramError("scheduler test inputs require --isolated-test-clock")
    return ([Path(automation)] if automation else None, timezone or None)


def scheduled_launch_context(root: Path, config: dict[str, Any], *, automation_paths: list[Path] | None = None, host_timezone_id: str | None = None) -> dict[str, Any]:
    schedule = config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {}
    expected_timezone = str(schedule.get("timezone") or config.get("timezone") or "")
    scheduler = scheduler_status(root, config, automation_paths=automation_paths)
    host_timezone = host_timezone_status(expected_timezone, host_timezone_id=host_timezone_id)
    enabled = enabled_topic_ids(root)
    reasons: list[str] = []
    if str(config.get("mode") or "") != "scheduled_research": reasons.append("topic_not_scheduled")
    if not config.get("enabled"): reasons.append("topic_paused")
    if str(config.get("timezone") or "") != expected_timezone: reasons.append("topic_timezone_contract_mismatch")
    if scheduler.get("state") != "active": reasons.append("scheduler_contract_not_active")
    if host_timezone.get("state") != "aligned": reasons.append("host_timezone_not_aligned")
    if enabled != [str(config.get("topic_id") or "")]: reasons.append("enabled_topic_not_unique")
    return {"scheduler": scheduler, "host_timezone": host_timezone, "enabled_topic_ids": enabled, "reason_codes": reasons}


def require_scheduled_launch_context(root: Path, config: dict[str, Any], args: argparse.Namespace) -> None:
    automation_paths, host_timezone_id = scheduler_test_inputs(args)
    context = scheduled_launch_context(root, config, automation_paths=automation_paths, host_timezone_id=host_timezone_id)
    if context["reason_codes"]:
        raise ProgramError("scheduled research start is blocked: " + ", ".join(context["reason_codes"]))


def runtime_evidence(run: dict[str, Any] | None, config: dict[str, Any]) -> dict[str, Any]:
    """Report recorded executor evidence without claiming a scheduler ran."""
    schedule = config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {}
    actual = run.get("actual_executor") if isinstance(run, dict) and isinstance(run.get("actual_executor"), dict) else {}
    status = str(actual.get("status") or "not_recorded")
    payload = {
        "status": status,
        "model": actual.get("model"),
        "reasoning_effort": actual.get("reasoning_effort"),
        "expected_model": schedule.get("model"),
        "expected_reasoning_effort": schedule.get("reasoning_effort"),
    }
    if status in {"unknown", "not_recorded"}:
        return {**payload, "state": "configured", "reason": "no per-run executor receipt is recorded"}
    if status == "verified":
        if actual.get("model") == schedule.get("model") and actual.get("reasoning_effort") == schedule.get("reasoning_effort"):
            return {**payload, "state": "verified", "reason": "verified executor matches the schedule contract"}
        return {**payload, "state": "mismatch", "reason": "verified executor differs from the schedule contract"}
    return {**payload, "state": "invalid", "reason": "scheduled runs accept only unknown or verified executor evidence"}


def pending_receipt_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for case in active_cases(state).values():
        if case.get("status") == "committing":
            items.append({"kind": "committing_case", "case_id": case.get("case_id"), "run_id": case.get("run_id")})
    runs = state.get("runs")
    if not isinstance(runs, dict):
        raise ProgramError("research run state is invalid")
    for run_id, run in runs.items():
        if not isinstance(run, dict) or not isinstance(run.get("actions"), list):
            raise ProgramError("research action state is invalid")
        for action in run["actions"]:
            if isinstance(action, dict) and action.get("status") == "reserved":
                items.append({"kind": "external_action", "run_id": run_id, "action_id": action.get("action_id"), "case_id": action.get("case_id"), "discovery_id": action.get("discovery_id")})
    return items


def preflight_view(root: Path, topic_id: str, *, automation_paths: list[Path] | None = None, host_timezone_id: str | None = None) -> dict[str, Any]:
    """Read a scheduled-start decision without writing a run or reservation."""
    _, config, state = load_topic(root, topic_id)
    day, checked_at = local_day(None, str(config.get("timezone") or ""))
    reconcile_pending_for_day(state, day)
    context = scheduled_launch_context(root, config, automation_paths=automation_paths, host_timezone_id=host_timezone_id)
    counts = capacity(state, config, day)
    run_id = state.get("day_runs", {}).get(day)
    runs = state.get("runs", {})
    run = runs.get(run_id) if isinstance(runs, dict) and isinstance(run_id, str) else None
    pending = pending_receipt_items(state)
    local_pending = [row for row in [*active_cases(state).values(), *discovery_slots(state).values()] if row.get("status") in CASE_STATES or row.get("status") == "reserved"]
    if pending:
        next_action, reason = "inspect_original_receipts", "an external action or writer outcome is unresolved"
    elif context["reason_codes"]:
        next_action, reason = "stop_new_research", "scheduled configuration is not currently usable"
    elif local_pending:
        owners = sorted({str(row.get("run_id") or "") for row in local_pending if row.get("run_id")})
        next_action, reason = ("resume_run", "existing reserved work must retain its original run") if len(owners) == 1 else ("stop_new_research", "pending work has multiple owners")
        run_id = owners[0] if len(owners) == 1 else run_id
    elif counts["total_accepted"] + counts["total_reserved"] >= counts["total_cap"] or counts["daily_accepted"] + counts["daily_reserved"] >= counts["daily_cap"]:
        next_action, reason = "zero_external_search", "case capacity is full; local evidence may still be read"
    elif run_id and isinstance(run, dict) and run_has_unfinished_work(state, run_id):
        next_action, reason = "resume_run", "today already owns a research run"
    else:
        next_action, reason, run_id = "begin_run", "configured local start may create today's first run", None
    args = ["--topic-id", topic_id] + (["--run-id", run_id] if next_action == "resume_run" and isinstance(run_id, str) else [])
    return {"status": "ready" if next_action != "stop_new_research" else "blocked", "read_only": True, "execution_authorized": False, "checked_at": checked_at, "day": day, "topic_id": topic_id, "next_action": next_action, "next_command": {"command": "begin-run", "args": args} if next_action in {"begin_run", "resume_run"} else None, "reason": reason, "reason_codes": context["reason_codes"], "scheduler": context["scheduler"], "host_timezone": context["host_timezone"], "enabled_topic_ids": context["enabled_topic_ids"], "capacity": counts, "run_id": run_id or "", "runtime_evidence": runtime_evidence(run if isinstance(run, dict) else None, config), "trigger_evidence": {"state": "not_observed", "reason": "a local preflight does not claim a natural scheduler trigger"}, "pending_receipt_items": pending, "pending_local_work": local_pending}


def command_preflight(args: argparse.Namespace) -> int:
    paths, timezone = scheduler_test_inputs(args)
    return output(preflight_view(args.root.resolve(), args.topic_id, automation_paths=paths, host_timezone_id=timezone))


def action_count(run: dict[str, Any], budget_key: str, day: str) -> int:
    recorded = sum(
        1 for action in run.get("actions", [])
        if action.get("budget_key") == budget_key and action.get("effective_day") == day
        and action.get("status") in {"reserved", "completed"}
    )
    inherited = run.get("inherited_observed_usage") if run.get("inherited_observed_usage_effective_day") == day else {}
    if not isinstance(inherited, dict):
        inherited = {}
    return recorded + int(inherited.get(budget_key, 0) or 0)


def observed_summary_count(run: dict[str, Any], day: str) -> int:
    inherited = run.get("inherited_observed_usage") if run.get("inherited_observed_usage_effective_day") == day else {}
    if not isinstance(inherited, dict):
        inherited = {}
    return int(inherited.get("candidate_summaries", 0) or 0) + sum(
        int(item.get("actual_results") or 0)
        for item in run.get("actions", [])
        if item.get("action") == "search" and item.get("effective_day") == day
        and item.get("status") == "completed"
    )


def output(payload: dict[str, Any]) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_init(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory = topic_dir(root, args.topic_id)
    with program_lock(directory):
        config_path = directory / "topic.json"
        if config_path.exists():
            raise ProgramError("research topic already exists; do not overwrite its cap or history")
        if args.daily_case_cap < 1 or args.total_case_cap < 1:
            raise ProgramError("case caps must be positive")
        scope = parse_object(args.scope, "scope")
        output_contract = parse_object(args.output_contract, "output_contract")
        classifications = parse_object(args.classifications, "classifications")
        evidence_contract = parse_object(args.evidence_contract, "evidence_contract")
        selection_contract = parse_object(args.selection_contract, "selection_contract") if args.selection_contract else None
        try:
            focus_value = json.loads(args.focus)
        except json.JSONDecodeError as exc:
            raise ProgramError("focus must be a JSON list") from exc
        focus = parse_string_list(focus_value, "focus")
        config = {
            "schema_version": SCHEMA,
            "topic_id": safe_id(args.topic_id, "topic_id"),
            "title": args.title.strip(),
            "profile_version": args.profile_version.strip(),
            "purpose": args.purpose.strip(),
            "scope": scope,
            "focus": focus,
            "evidence_contract": evidence_contract,
            "output_contract": output_contract,
            "classifications": classifications,
            "mode": args.mode,
            "timezone": args.timezone,
            "daily_case_cap": args.daily_case_cap,
            "total_case_cap": args.total_case_cap,
            "enabled": True,
            "budget": {
                "searches": args.budget_searches,
                "candidate_summaries_target": args.budget_candidate_summaries_target,
                "deep_reads": args.budget_deep_reads,
            },
        }
        if selection_contract is not None:
            config["selection_contract"] = selection_contract
        if not config["title"]:
            raise ProgramError("topic title is required")
        validate_topic_definition(config)
        write_json(config_path, config)
        write_json(directory / "state.json", initial_state())
    return output({"status": "ready", "topic": config, "registry": str(directory.relative_to(root))})


def manual_allowance_contract(args: argparse.Namespace, config: dict[str, Any], day: str) -> dict[str, Any]:
    authorization_source = str(args.authorization_source or "").strip()
    if not authorization_source:
        raise ProgramError("manual allowance requires an explicit authorization source")
    limits = {
        "cases": int(args.case_cap),
        "searches": int(args.budget_searches),
        "candidate_summaries_target": int(args.budget_candidate_summaries_target),
        "deep_reads": int(args.budget_deep_reads),
    }
    if any(value < 1 for value in limits.values()):
        raise ProgramError("manual allowance limits must be positive")
    if limits["cases"] > int(config["daily_case_cap"]):
        raise ProgramError("manual allowance case capacity cannot exceed the topic daily cap")
    for key in ("searches", "deep_reads", "candidate_summaries_target"):
        if limits[key] > int(config["budget"][key]):
            raise ProgramError(f"manual allowance {key} cannot exceed the topic budget")
    executor = {
        "model": safe_id(args.executor_model, "executor_model"),
        "reasoning_effort": safe_id(args.executor_reasoning_effort, "executor_reasoning_effort"),
    }
    return {
        "topic_id": str(config["topic_id"]),
        "effective_day": day,
        "parent_run_id": safe_id(args.parent_run_id, "parent_run_id"),
        "authorization_source": authorization_source,
        "limits": limits,
        "declared_executor": executor,
    }


def command_grant_manual_allowance(args: argparse.Namespace) -> int:
    """Record one user-authorized, same-day manual continuation without changing the topic plan."""
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, authorized_at, _ = event_times(args, str(config["timezone"]))
    allowance_id = safe_id(args.allowance_id, "allowance_id")
    contract = manual_allowance_contract(args, config, day)
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        allowances = manual_allowances(state)
        existing = allowances.get(allowance_id)
        if existing is not None:
            expected = {"allowance_id": allowance_id, **contract}
            comparable = {key: existing.get(key) for key in expected}
            if comparable != expected:
                raise ProgramError("manual allowance id already records a different authorization")
            return output({"status": "replayed", "allowance": existing, "day": day})
        if any(
            row.get("effective_day") == day and row.get("parent_run_id") == contract["parent_run_id"]
            for row in allowances.values() if isinstance(row, dict)
        ):
            raise ProgramError("this parent run already has a manual allowance for today")
        require_run(state, contract["parent_run_id"], config, day)
        allowance = {
            "allowance_id": allowance_id,
            **contract,
            "authorized_at": authorized_at,
            "status": "approved",
            "consumed_by_run_id": None,
        }
        allowances[allowance_id] = allowance
        write_json(directory / "state.json", state)
    return output({"status": "approved", "allowance": allowance, "day": day})


def command_begin(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, observed_at, test_effective_at = event_times(args, str(config["timezone"]))
    run_id = safe_id(args.run_id or f"RUN-{uuid.uuid4().hex[:16]}", "run_id")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        if not config.get("enabled"):
            raise ProgramError("research topic is paused")
        reconcile_pending_for_day(state, day)
        runs = state["runs"]
        day_runs = state["day_runs"]
        assigned = day_runs.get(day)
        manual_allowance_id = safe_id(args.manual_allowance_id, "manual_allowance_id") if args.manual_allowance_id else ""
        pending_owners = {
            str(row.get("run_id"))
            for row in [*active_cases(state).values(), *discovery_slots(state).values()]
            if row.get("status") in CASE_STATES or row.get("status") == "reserved"
        }
        execution_mode = args.execution_mode or str(config.get("mode") or "manual_research")
        if execution_mode != "manual_research":
            raise ProgramError("portable research supports manual_research only; background scheduling belongs to OPS")
        direct_source = str(args.direct_command_source or "").strip()
        if direct_source and execution_mode != "manual_research":
            raise ProgramError("direct user command requires manual_research execution mode")
        allowance: dict[str, Any] | None = None
        existing = runs.get(run_id)
        original_execution_mode = str(existing.get("execution_mode") or "") if isinstance(existing, dict) and existing.get("execution_mode") != execution_mode else ""
        direct_command = {
            "source": direct_source,
            "recorded_at": observed_at,
            **({"original_execution_mode": original_execution_mode} if original_execution_mode else {}),
        } if direct_source else None
        if existing is not None:
            if not isinstance(existing, dict):
                raise ProgramError("research run state is invalid")
            stored_mode = existing.get("execution_mode")
            takeover = (
                stored_mode in {"scheduled_research", "pilot_existing_candidate_engineering"}
                and execution_mode == "manual_research"
                and direct_command is not None
            )
            if stored_mode != execution_mode and not takeover:
                raise ProgramError("existing research run execution mode cannot change")
        existing_direct = direct_command_for_run(existing) if isinstance(existing, dict) else None
        if manual_allowance_id:
            if execution_mode != "manual_research":
                raise ProgramError("manual allowance requires manual_research execution mode")
            allowance = manual_allowances(state).get(manual_allowance_id)
            if not isinstance(allowance, dict):
                raise ProgramError("manual allowance is not authorized")
            if allowance.get("topic_id") != args.topic_id:
                raise ProgramError("manual allowance is not valid for this topic and day")
            if allowance.get("effective_day") != day and direct_command is None and existing_direct is None:
                raise ProgramError("manual allowance is not valid for this topic and day")
            declared = allowance.get("declared_executor")
            if not isinstance(declared, dict):
                raise ProgramError("manual allowance executor declaration is invalid")
            if args.executor_model and args.executor_model != declared.get("model"):
                raise ProgramError("manual allowance model differs from its approved declaration")
            if args.executor_reasoning_effort and args.executor_reasoning_effort != declared.get("reasoning_effort"):
                raise ProgramError("manual allowance reasoning effort differs from its approved declaration")
            if existing is not None:
                if not isinstance(existing, dict) or existing.get("manual_allowance_id") != manual_allowance_id or assigned != run_id:
                    raise ProgramError("manual allowance is already bound to another research work unit")
            else:
                parent_run_id = allowance.get("parent_run_id")
                if allowance.get("status") != "approved" or allowance.get("consumed_by_run_id") is not None:
                    raise ProgramError("manual allowance has already been consumed")
                if assigned != parent_run_id:
                    raise ProgramError("manual allowance must continue its assigned parent run on the same day")
                if run_has_unfinished_work(state, str(parent_run_id)) or pending_owners:
                    raise ProgramError("parent research work still has reserved work; do not switch to the manual allowance")
        else:
            if assigned and assigned != run_id and run_has_unfinished_work(state, assigned):
                raise ProgramError(f"today's research work unit is {assigned}; resume it instead of starting another budget")
            if not assigned and pending_owners and run_id not in pending_owners:
                owners = ", ".join(sorted(pending_owners))
                raise ProgramError(f"unfinished research belongs to {owners}; resume that run before external work")
        if run_id not in runs:
            declared_executor = allowance["declared_executor"] if allowance is not None else {
                "model": args.executor_model or "unspecified",
                "reasoning_effort": args.executor_reasoning_effort or "unspecified",
            }
            runs[run_id] = {
                "run_id": run_id,
                "topic_id": args.topic_id,
                "profile_version": str(config.get("profile_version") or "legacy-v1"),
                "execution_mode": execution_mode,
                "declared_executor": declared_executor,
                "actual_executor": {
                    "status": "not_observed" if allowance is not None else "unknown",
                    "model": None,
                    "reasoning_effort": None,
                    "evidence": "The manual allowance declares the intended executor; actual execution remains unobserved."
                    if allowance is not None else "A CLI declaration records a manual run choice; it does not prove external execution.",
                },
                "started_at": observed_at,
                "actions": [],
            }
            if allowance is not None:
                runs[run_id].update({
                    "manual_allowance_id": manual_allowance_id,
                    "parent_run_id": allowance["parent_run_id"],
                })
                allowance.update({"status": "consumed", "consumed_by_run_id": run_id, "consumed_at": observed_at})
            if direct_command is not None:
                runs[run_id]["direct_command"] = direct_command
            if test_effective_at:
                runs[run_id]["test_effective_at"] = test_effective_at
        elif direct_command is not None:
            existing_direct = direct_command_for_run(runs[run_id])
            if existing_direct is None:
                if runs[run_id].get("execution_mode") != execution_mode:
                    runs[run_id]["execution_mode"] = execution_mode
                runs[run_id]["direct_command"] = direct_command
            elif existing_direct["source"] != direct_source:
                raise ProgramError("existing direct user command source differs from its recorded scope")
        if direct_command is not None:
            reclassify_pending_direct_takeover(state, run_id)
        if assigned:
            append_day_run_history(state, day, assigned)
        day_runs[day] = run_id
        append_day_run_history(state, day, run_id)
        run = require_run(state, run_id, config, day)
        write_json(directory / "state.json", state)
    return output({"status": "ready", "run": run, "capacity": capacity(state, config, day), "day": day})


def command_reserve_case(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    case_id = safe_id(args.case_id, "case_id")
    classification = safe_id(args.classification or args.design_family or "", "classification")
    facets = parse_case_classifications(args.classification_facets)
    url, identity = case_identity(args.primary_url)
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        active = active_cases(state)
        if case_id in active:
            existing = active[case_id]
            if existing.get("source_identity") != identity or case_classification(existing) != classification:
                raise ProgramError("case_id is already bound to another source or classification")
            result = {"status": "resumed", "case": existing}
        else:
            if any(row.get("source_identity") == identity for row in [*accepted_cases(state), *withdrawn_cases(state)]):
                raise ProgramError("source identity is already qualified or withdrawn by this topic")
            if any(row.get("case_id") == case_id for row in withdrawn_cases(state)):
                raise ProgramError("case_id was withdrawn by this topic; preserve its history and use a new case")
            if any(case_classification(row) == classification for row in [*accepted_cases(state), *active.values()]):
                raise ProgramError("classification is already accepted or reserved; choose a distinct case")
            require_manual_case_capacity(state, run)
            require_capacity(state, config, day, run)
            active[case_id] = {
                "case_id": case_id, "run_id": args.run_id, "primary_url": url, "source_identity": identity,
                "classification_key": configured_classification_key(config), "classification": classification,
                "classifications": facets,
                "status": "reserved", "reserved_at": checked_at, "effective_day": day,
                "quota_lane": quota_lane_for_run(run),
            }
            if configured_classification_key(config) == "design_family":
                active[case_id]["design_family"] = classification
            result = {"status": "reserved", "case": active[case_id]}
        write_json(directory / "state.json", state)
    return output({**result, "capacity": capacity(state, config, day), "day": day})


def command_reserve_discovery(args: argparse.Namespace) -> int:
    """Reserve a case-cap slot before search, without inventing a source identity."""
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    discovery_id = safe_id(args.discovery_id, "discovery_id")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        slots = discovery_slots(state)
        if discovery_id in slots:
            existing = slots[discovery_id]
            if existing.get("run_id") != args.run_id:
                raise ProgramError("discovery_id is already owned by another run")
            result = {"status": "resumed", "discovery": existing}
        else:
            require_manual_case_capacity(state, run)
            require_capacity(state, config, day, run)
            slots[discovery_id] = {
                "discovery_id": discovery_id,
                "run_id": args.run_id,
                "status": "reserved",
                "reserved_at": checked_at,
                "effective_day": day,
                "quota_lane": quota_lane_for_run(run),
                "source_identity": None,
                "classification": None,
            }
            result = {"status": "reserved", "discovery": slots[discovery_id]}
        write_json(directory / "state.json", state)
    return output({**result, "capacity": capacity(state, config, day), "day": day,
                   "source_binding": "not yet known; no placeholder URL or classification is recorded"})


def command_bind_discovery(args: argparse.Namespace) -> int:
    """Bind a completed discovery slot to its first real primary URL and family."""
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    discovery_id = safe_id(args.discovery_id, "discovery_id")
    case_id = safe_id(args.case_id, "case_id")
    classification = safe_id(args.classification or args.design_family or "", "classification")
    facets = parse_case_classifications(args.classification_facets)
    url, identity = case_identity(args.primary_url)
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        slots = discovery_slots(state)
        slot = slots.get(discovery_id)
        if not slot or slot.get("status") != "reserved" or slot.get("run_id") != args.run_id:
            raise ProgramError("discovery slot is not reservable for this run")
        if not any(action.get("discovery_id") == discovery_id and action.get("action") == "search"
                   and action.get("status") == "completed" for action in run.get("actions", [])):
            raise ProgramError("discovery must complete a reserved search before source binding")
        active = active_cases(state)
        if case_id in active:
            existing = active[case_id]
            if existing.get("source_identity") != identity or case_classification(existing) != classification:
                raise ProgramError("case_id is already bound to another source or classification")
            result = {"status": "replayed", "case": existing}
        else:
            if any(row.get("source_identity") == identity for row in [*accepted_cases(state), *withdrawn_cases(state)]):
                raise ProgramError("source identity is already qualified or withdrawn by this topic")
            if any(row.get("case_id") == case_id for row in withdrawn_cases(state)):
                raise ProgramError("case_id was withdrawn by this topic; preserve its history and use a new case")
            if any(case_classification(row) == classification for row in [*accepted_cases(state), *active.values()]):
                raise ProgramError("classification is already accepted or reserved; choose a distinct case")
            # This is a transfer, not another reservation: capacity has already
            # been consumed by the discovery slot and remains unchanged.
            active[case_id] = {
                "case_id": case_id, "run_id": args.run_id, "primary_url": url, "source_identity": identity,
                "classification_key": configured_classification_key(config), "classification": classification,
                "classifications": facets,
                "status": "reserved", "reserved_at": checked_at,
                "effective_day": day, "quota_lane": quota_lane_for_run(run), "bound_from_discovery": discovery_id,
            }
            if configured_classification_key(config) == "design_family":
                active[case_id]["design_family"] = classification
            slot.update({"status": "bound", "bound_at": checked_at, "case_id": case_id,
                         "primary_url": url, "source_identity": identity, "classification": classification,
                         "classifications": facets})
            result = {"status": "bound", "case": active[case_id]}
        write_json(directory / "state.json", state)
    return output({**result, "capacity": capacity(state, config, day), "day": day})


def require_releasable_actions(run: dict[str, Any], owner_field: str, owner_id: str) -> None:
    if any(action.get(owner_field) == owner_id and action.get("status") == "reserved" for action in run.get("actions", [])):
        raise ProgramError("external action outcome is still unknown; complete its readback before releasing this reservation")


def command_reject_case(args: argparse.Namespace) -> int:
    """Release only a known-uncommitted case; preserve rejection evidence."""
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    reason = str(args.reason or "").strip()
    if not reason:
        raise ProgramError("case rejection reason is required")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        case = active_cases(state).get(args.case_id)
        if not case or case.get("status") != "reserved" or case.get("run_id") != args.run_id:
            raise ProgramError("only this run's uncommitted reserved case can be rejected")
        require_releasable_actions(run, "case_id", args.case_id)
        record = {**case, "status": "rejected", "rejected_at": checked_at, "rejection_reason": reason}
        state["rejected_cases"].append(record)
        del state["active_cases"][args.case_id]
        write_json(directory / "state.json", state)
    return output({"status": "rejected", "case": record, "capacity": capacity(state, config, day), "day": day})


def command_reject_discovery(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    reason = str(args.reason or "").strip()
    if not reason:
        raise ProgramError("discovery rejection reason is required")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        discovery = discovery_slots(state).get(args.discovery_id)
        if not discovery or discovery.get("status") != "reserved" or discovery.get("run_id") != args.run_id:
            raise ProgramError("only this run's unbound reserved discovery can be rejected")
        require_releasable_actions(run, "discovery_id", args.discovery_id)
        record = {**discovery, "status": "rejected", "rejected_at": checked_at, "rejection_reason": reason}
        state["rejected_discoveries"].append(record)
        del state["discovery_slots"][args.discovery_id]
        write_json(directory / "state.json", state)
    return output({"status": "rejected", "discovery": record, "capacity": capacity(state, config, day), "day": day})


def command_reserve_action(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    action = args.action
    if action not in ACTION_LIMITS:
        raise ProgramError("unsupported research action")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        if run.get("execution_mode") == "scheduled_research":
            require_scheduled_launch_context(root, config, args)
        has_case = bool(args.case_id)
        has_discovery = bool(args.discovery_id)
        if has_case == has_discovery:
            raise ProgramError("reserve exactly one case_id or discovery_id for an external action")
        record_owner: dict[str, Any]
        owner_fields: dict[str, str]
        if has_discovery:
            discovery_id = safe_id(args.discovery_id, "discovery_id")
            slot = discovery_slots(state).get(discovery_id)
            if not slot or slot.get("status") != "reserved" or slot.get("run_id") != args.run_id:
                raise ProgramError("discovery slot must be reserved before external search")
            if action != "search":
                if not any(
                    item.get("discovery_id") == discovery_id
                    and item.get("action") == "search"
                    and item.get("status") == "completed"
                    for item in run.get("actions", [])
                ):
                    raise ProgramError("discovery must complete search before candidate reading")
                candidate_url, candidate_identity = case_identity(args.candidate_url)
            record_owner = slot
            owner_fields = {"discovery_id": discovery_id}
        else:
            case_id = safe_id(args.case_id, "case_id")
            case = active_cases(state).get(case_id)
            if not case or case.get("status") not in CASE_STATES:
                raise ProgramError("case must be reserved before this external research action")
            if case.get("run_id") != args.run_id:
                raise ProgramError("case belongs to another research run; resume its owner before external work")
            record_owner = case
            owner_fields = {"case_id": case_id}
        reconcile_pending_for_day(state, day)
        counts = capacity(state, config, day)
        if counts["total_accepted"] >= counts["total_cap"] and direct_command_for_run(run) is None:
            raise ProgramError("total case cap reached; zero external search is permitted")
        budget_key = ACTION_LIMITS[action]
        budget_limit = action_budget_limit(state, config, run, budget_key)
        if budget_limit is not None and action_count(run, budget_key, day) >= budget_limit:
            raise ProgramError(f"{budget_key} budget is exhausted before dispatch")
        action_id = safe_id(args.action_id or f"ACT-{uuid.uuid4().hex[:16]}", "action_id")
        if any(row.get("action_id") == action_id for row in run["actions"]):
            raise ProgramError("action_id already exists")
        record = {"action_id": action_id, **owner_fields, "action": action, "budget_key": budget_key,
                  "status": "reserved", "reserved_at": checked_at, "effective_day": day}
        if has_discovery and action != "search":
            record.update({"candidate_url": candidate_url, "candidate_source_identity": candidate_identity})
        run["actions"].append(record)
        write_json(directory / "state.json", state)
    return output({"status": "reserved", "action": record, "budget_limit": budget_limit, "budget_used": action_count(run, budget_key, day), "day": day})


def command_complete_action(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    if args.actual_results < 0:
        raise ProgramError("actual result count cannot be negative")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        run = require_run(state, args.run_id, config, day)
        record = next((row for row in run["actions"] if row.get("action_id") == args.action_id), None)
        if not record:
            raise ProgramError("reserved action does not exist")
        if record.get("status") == "completed":
            result = {"status": "replayed", "action": record}
        elif record.get("status") != "reserved":
            raise ProgramError("reserved action is in an invalid state")
        else:
            record.update({"status": "completed", "completed_at": checked_at, "actual_results": args.actual_results})
            if record["action"] == "search":
                target = candidate_summary_target(state, config, run)
                cumulative = observed_summary_count(run, day)
                record["candidate_summary_target"] = target
                record["candidate_summary_target_exceeded"] = None if target is None else args.actual_results > target
                record["candidate_summary_cumulative_actual"] = cumulative
                record["candidate_summary_cumulative_target_exceeded"] = None if target is None else cumulative > target
            result = {"status": "completed", "action": record}
        write_json(directory / "state.json", state)
    observed = None
    if record.get("action") == "search":
        observed = {
            "target": record.get("candidate_summary_target"),
            "this_action": record.get("actual_results"),
            "cumulative_for_day": record.get("candidate_summary_cumulative_actual"),
            "target_exceeded": record.get("candidate_summary_cumulative_target_exceeded"),
        }
    return output({**result, "observed_candidate_summaries": observed,
                   "enforcement_note": "action count is enforced before dispatch; returned summary count is retained and observed cumulatively because the browser tool may return more than requested."})


def workspace_file(root: Path, raw: object, label: str) -> Path:
    value = str(raw or "").strip()
    if not value:
        raise ProgramError(f"{label} is missing")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProgramError(f"{label} escapes this workspace") from exc
    if not candidate.is_file():
        raise ProgramError(f"{label} is missing")
    return candidate


def workspace_directory(root: Path, raw: object, label: str) -> Path:
    """Resolve one declared workspace directory without allowing path escape."""
    value = str(raw or "").strip()
    if not value:
        raise ProgramError(f"{label} is missing")
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProgramError(f"{label} escapes this workspace") from exc
    if not candidate.is_dir():
        raise ProgramError(f"{label} is missing")
    return candidate


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def revalidation_receipt_package(root: Path, receipt_path: Path, label: str) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Read one immutable Compiler receipt and its exact Knowledge Package.

    This intentionally checks only package-local receipt bindings.  A superseded
    revision's Vault projections can legitimately be replaced by a child
    revision, so historical lineage must not require the older writer output to
    remain the current projection.
    """
    receipt = read_compiler_json(receipt_path)
    if receipt.get("schema_version") != COMPLETION_SCHEMA_VERSION or receipt.get("status") != "completed":
        raise ProgramError(f"{label} is not a completed Compiler v2 receipt")
    if receipt.get("payload_sha256") != canonical_json_sha256({key: value for key, value in receipt.items() if key != "payload_sha256"}):
        raise ProgramError(f"{label} payload hash mismatch")
    kp_path = workspace_file(root, receipt.get("knowledge_package_path"), f"{label} Knowledge Package")
    package = kp_path.parent
    if receipt_path != package / "completion_receipt.json":
        raise ProgramError(f"{label} is not the package's immutable receipt")
    if receipt.get("knowledge_package_sha256") != sha256_bytes(kp_path.read_bytes()):
        raise ProgramError(f"{label} Knowledge Package hash mismatch")
    kp = read_compiler_json(kp_path)
    return receipt, package, kp


def revalidation_identity_matches(
    receipt: dict[str, Any],
    kp: dict[str, Any],
    *,
    case_id: str,
    topic_id: str,
    source_id: str,
    source_identity: str,
) -> bool:
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    case_evidence = receipt.get("case_evidence") if isinstance(receipt.get("case_evidence"), dict) else {}
    return (
        receipt.get("source_id") == source_id
        and receipt.get("source_identity") == source_identity
        and source.get("source_id") == source_id
        and source.get("source_identity") == source_identity
        and case_evidence.get("case_id") == case_id
        and case_evidence.get("topic_id") == topic_id
        and case_evidence.get("case_qualified") is True
    )


def validate_revalidation_lineage(
    root: Path,
    receipt_path: Path,
    *,
    accepted_receipt_path: Path,
    accepted_receipt_sha256: str,
    case_id: str,
    topic_id: str,
    source_id: str,
    source_identity: str,
    selection_contract: dict[str, Any],
) -> list[str]:
    """Prove a bounded revision chain reaches the accepted immutable receipt.

    Every revision binds its immediate parent because that is what the Compiler
    actually prepares.  The program additionally requires every link to carry
    the current topic contract and immutable origin/plan evidence before it can
    restore the method-verification claim.
    """
    selection_hash = canonical_json_sha256(selection_contract)
    expected_receipt = accepted_receipt_path.resolve()
    expected_hash = str(accepted_receipt_sha256 or "")
    seen: set[Path] = set()
    lineage: list[str] = []
    current_path = receipt_path.resolve()

    for _depth in range(16):
        if current_path in seen:
            raise ProgramError("case revalidation lineage contains a receipt cycle")
        seen.add(current_path)
        receipt, package, kp = revalidation_receipt_package(root, current_path, "case revalidation receipt")
        if not revalidation_identity_matches(
            receipt, kp, case_id=case_id, topic_id=topic_id,
            source_id=source_id, source_identity=source_identity,
        ):
            raise ProgramError("case revalidation lineage identity differs from the accepted case")
        revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
        if revision.get("kind") != "case_revalidation":
            raise ProgramError("case revalidation lineage has a non-revision parent before the accepted receipt")
        revision_id = str(revision.get("revision_id") or "")
        if not revision_id or receipt.get("job_id") != kp.get("job_id"):
            raise ProgramError("case revalidation lineage revision/job binding is invalid")
        if (
            revision.get("selection_contract") != selection_contract
            or revision.get("selection_contract_sha256") != selection_hash
        ):
            raise ProgramError("case revalidation lineage does not bind the current topic contract")

        origin_path = package / "revision_origin.json"
        plan_path = package / "revalidation_plan.json"
        if not origin_path.is_file() or not plan_path.is_file():
            raise ProgramError("case revalidation lineage origin evidence is missing")
        origin = read_compiler_json(origin_path)
        plan = read_compiler_json(plan_path)
        if origin.get("payload_sha256") != canonical_json_sha256({key: value for key, value in origin.items() if key != "payload_sha256"}):
            raise ProgramError("case revalidation lineage origin payload hash mismatch")
        if plan.get("schema_version") != "case_revalidation_plan_v1":
            raise ProgramError("case revalidation lineage plan schema is invalid")
        artifact_hashes = {
            str(item.get("path") or ""): str(item.get("sha256") or "")
            for item in receipt.get("artifact_manifest", []) if isinstance(item, dict)
        }
        for required in (origin_path, plan_path):
            relative = required.relative_to(root).as_posix()
            if artifact_hashes.get(relative) != sha256_bytes(required.read_bytes()):
                raise ProgramError("case revalidation lineage receipt does not bind its origin evidence")

        parent_package = workspace_directory(root, origin.get("original_package"), "case revalidation original package")
        parent_kp_path = parent_package / "knowledge_package.json"
        if not parent_kp_path.is_file():
            raise ProgramError("case revalidation lineage parent Knowledge Package is missing")
        parent_receipt_path = workspace_file(root, origin.get("original_completion_path"), "case revalidation parent receipt")
        if parent_receipt_path != parent_package / "completion_receipt.json":
            raise ProgramError("case revalidation lineage parent receipt is outside its parent package")
        parent_receipt_hash = sha256_bytes(parent_receipt_path.read_bytes())
        if (
            origin.get("schema_version") != "case_revalidation_origin_v1"
            or origin.get("revision_id") != revision_id
            or origin.get("job_id") != kp.get("job_id")
            or origin.get("case_id") != case_id
            or origin.get("topic_id") != topic_id
            or origin.get("source_id") != source_id
            or origin.get("source_identity") != source_identity
            or origin.get("selection_contract") != selection_contract
            or origin.get("selection_contract_sha256") != selection_hash
            or origin.get("original_knowledge_package_sha256") != sha256_bytes(parent_kp_path.read_bytes())
            or origin.get("original_completion_receipt_sha256") != parent_receipt_hash
            or origin.get("revision_plan_sha256") != sha256_bytes(plan_path.read_bytes())
            or revision.get("origin_package") != origin.get("original_package")
            or workspace_directory(root, revision.get("origin_package"), "case revalidation revision parent package") != parent_package
            or workspace_file(root, revision.get("original_completion_path"), "case revalidation revision parent receipt") != parent_receipt_path
            or revision.get("original_completion_receipt_sha256") != parent_receipt_hash
            or revision.get("revision_plan_sha256") != sha256_bytes(plan_path.read_bytes())
            or plan.get("revision_id") != revision_id
            or plan.get("job_id") != kp.get("job_id")
            or plan.get("case_id") != case_id
            or plan.get("topic_id") != topic_id
            or plan.get("source_id") != source_id
            or plan.get("source_identity") != source_identity
            or plan.get("original_completion_receipt_sha256") != parent_receipt_hash
            or plan.get("selection_contract") != selection_contract
            or plan.get("selection_contract_sha256") != selection_hash
            or plan.get("canonical_source_sha256") != origin.get("canonical_source_sha256")
        ):
            raise ProgramError("case revalidation lineage link does not bind its exact parent")

        parent_receipt, _parent_package, parent_kp = revalidation_receipt_package(
            root, parent_receipt_path, "case revalidation parent receipt"
        )
        if not revalidation_identity_matches(
            parent_receipt, parent_kp, case_id=case_id, topic_id=topic_id,
            source_id=source_id, source_identity=source_identity,
        ):
            raise ProgramError("case revalidation lineage parent identity differs from the accepted case")
        lineage.append(revision_id)
        if parent_receipt_path == expected_receipt:
            if parent_receipt_hash != expected_hash:
                raise ProgramError("case revalidation lineage does not reach the accepted immutable receipt")
            parent_revision = parent_kp.get("revision") if isinstance(parent_kp.get("revision"), dict) else {}
            if parent_revision.get("kind") == "case_revalidation":
                raise ProgramError("accepted immutable receipt must be the chain root")
            return lineage
        current_path = parent_receipt_path

    raise ProgramError("case revalidation lineage exceeds the maximum supported depth")

def immutable_case_completion(
    root: Path,
    receipt_path: Path,
    *,
    expected_source_identity: str,
    expected_primary_url: str,
    expected_case_id: str,
    expected_topic_id: str,
    evidence_contract: dict[str, Any] | None = None,
    selection_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Trust only a Compiler-created completion, not a lookalike JSON receipt."""
    receipt = read_compiler_json(receipt_path)
    if receipt.get("schema_version") != COMPLETION_SCHEMA_VERSION or receipt.get("status") != "completed":
        raise ProgramError("completion receipt is not a completed Compiler v2 receipt")
    package_path = workspace_file(root, receipt.get("knowledge_package_path"), "receipt Knowledge Package")
    package = package_path.parent
    if receipt_path != package / "completion_receipt.json":
        raise ProgramError("completion receipt is not the package's immutable receipt")
    if receipt.get("knowledge_package_sha256") != sha256_bytes(package_path.read_bytes()):
        raise ProgramError("completion receipt Knowledge Package hash mismatch")
    kp = read_compiler_json(package_path)
    source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
    contract = evidence_contract or {
        "asset_mode": "case_evidence",
        "source_input_kinds": ["url"],
        "source_profiles": ["article"],
    }
    asset_mode = contract.get("asset_mode")
    if asset_mode not in SUPPORTED_ASSET_MODES:
        raise ProgramError("topic evidence contract has an unsupported asset mode")
    input_kinds = parse_string_list(contract.get("source_input_kinds"), "evidence_contract.source_input_kinds", allowed={"url"})
    profiles = parse_string_list(contract.get("source_profiles"), "evidence_contract.source_profiles", allowed=SUPPORTED_SOURCE_PROFILES)
    if (
        receipt.get("source_identity") != expected_source_identity
        or source.get("source_identity") != expected_source_identity
        or source.get("source_input_kind") not in input_kinds
        or source.get("source_url") != expected_primary_url
    ):
        raise ProgramError("completion receipt source does not match the reserved primary URL identity")
    if source.get("source_profile") not in profiles:
        raise ProgramError("completion receipt source profile is outside this topic's evidence contract")
    try:
        # Re-read package, job, source slice and every receipt artifact before
        # changing program counters; a handcrafted JSON is not an acceptance.
        completed = completed_receipt_readback(root, package, package_path, kp)
    except (CompilerWorkerV2Error, CaseEvidenceError, OSError, json.JSONDecodeError) as exc:
        raise ProgramError(f"immutable completion readback failed: {exc}") from exc
    if not completed:
        raise ProgramError("completion receipt did not pass immutable readback")
    if source.get("asset_mode") != asset_mode:
        raise ProgramError("completion receipt asset mode differs from the topic evidence contract")
    if asset_mode == "case_evidence":
        try:
            manifest = package_manifest(package, source, selection_contract=selection_contract)
        except (CaseEvidenceError, OSError, json.JSONDecodeError) as exc:
            raise ProgramError(f"immutable case-evidence readback failed: {exc}") from exc
        case_evidence = receipt.get("case_evidence") if isinstance(receipt.get("case_evidence"), dict) else {}
        if (
            case_evidence.get("asset_mode") != "case_evidence"
            or case_evidence.get("case_qualified") is not True
            or case_evidence.get("case_id") != expected_case_id
            or case_evidence.get("topic_id") != expected_topic_id
            or case_evidence.get("manifest_path") != source.get("case_evidence_manifest_path")
            or case_evidence.get("manifest_sha256") != source.get("case_evidence_manifest_sha256")
            or manifest.get("case_id") != expected_case_id
            or manifest.get("topic_id") != expected_topic_id
            or manifest.get("primary_source_url") != expected_primary_url
        ):
            raise ProgramError("completion receipt case evidence does not match the reserved case")
        artifacts = receipt.get("artifact_manifest")
        if not isinstance(artifacts, list):
            raise ProgramError("completion receipt artifact manifest is invalid")
        artifact_hashes = {
            str(item.get("path")): str(item.get("sha256"))
            for item in artifacts if isinstance(item, dict)
        }
        required_assets = [package / str(source["case_evidence_manifest_path"]), *case_asset_paths(package, manifest)]
        for asset in required_assets:
            relative = asset.relative_to(root).as_posix()
            if artifact_hashes.get(relative) != sha256_bytes(asset.read_bytes()):
                raise ProgramError("completion receipt does not bind every case evidence asset")
    return receipt


def command_complete_case(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    receipt_path = (root / args.completion_receipt).resolve()
    if root not in receipt_path.parents or not receipt_path.is_file():
        raise ProgramError("completion receipt must be an existing file inside this workspace")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        require_run(state, args.run_id, config, day)
        active = active_cases(state)
        case = active.get(args.case_id)
        receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        if not case:
            prior = next((row for row in accepted_cases(state) if row.get("case_id") == args.case_id), None)
            if prior and prior.get("completion_receipt_sha256") == receipt_hash:
                if method_verification_state(prior) != "verified":
                    raise ProgramError("case method verification is not current; preserve it and complete source-bound revalidation")
                immutable_case_completion(
                    root, receipt_path,
                    expected_source_identity=str(prior.get("source_identity") or ""),
                    expected_primary_url=str(prior.get("primary_url") or ""),
                    expected_case_id=args.case_id, expected_topic_id=args.topic_id,
                    evidence_contract=topic_evidence_contract(config),
                    selection_contract=accepted_selection_contract(prior),
                )
                return output({"status": "replayed", "case": prior, "day": day})
            raise ProgramError("case is not reserved")
        receipt = immutable_case_completion(
            root, receipt_path,
            expected_source_identity=str(case.get("source_identity") or ""),
            expected_primary_url=str(case.get("primary_url") or ""),
            expected_case_id=args.case_id, expected_topic_id=args.topic_id,
            evidence_contract=topic_evidence_contract(config),
            selection_contract=topic_selection_contract(config),
        )
        reconcile_pending_for_day(state, day)
        counts = capacity(state, config, day)
        if counts["daily_accepted"] >= counts["daily_cap"] and direct_command_for_run(run) is None:
            raise ProgramError("today cap is full before qualified completion; preserve reservation and inspect")
        case.update({"status": "committing", "commit_checked_at": checked_at})
        state["active_cases"][args.case_id] = case
        selection_contract = topic_selection_contract(config)
        method_state = "verified" if (
            asset_mode == "case_evidence"
            and selection_contract is not None
            and selection_contract.get("learning_alignment_required") is True
        ) else "not_assessed"
        accepted = {**case, "status": "qualified", "accepted_day": day, "accepted_at": checked_at,
                    "completion_receipt": receipt_path.relative_to(root).as_posix(), "completion_receipt_sha256": receipt_hash,
                    "source_id": receipt.get("source_id"), "source_identity": receipt.get("source_identity"),
                    "method_verification": {
                        "state": method_state,
                        "recorded_at": checked_at,
                        "selection_contract_version": selection_contract.get("version") if selection_contract else None,
                    }}
        if selection_contract is not None:
            accepted["selection_contract"] = selection_contract
        accepted_cases(state).append(accepted)
        del state["active_cases"][args.case_id]
        write_json(directory / "state.json", state)
    return output({"status": "qualified", "case": accepted, "capacity": capacity(state, config, day), "day": day})


def command_withdraw_case(args: argparse.Namespace) -> int:
    """Withdraw a qualified case without deleting its source or immutable receipt."""
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    case_id = safe_id(args.case_id, "case_id")
    reason = str(args.reason or "").strip()
    if not reason:
        raise ProgramError("withdrawal reason is required")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        accepted = next((case for case in accepted_cases(state) if case.get("case_id") == case_id), None)
        if accepted is None:
            prior = next((case for case in withdrawn_cases(state) if case.get("case_id") == case_id), None)
            if prior is None:
                raise ProgramError("case is not qualified for withdrawal")
            if prior.get("withdrawal_reason") != reason:
                raise ProgramError("case was already withdrawn with a different reason")
            return output({"status": "replayed", "case": prior, "capacity": capacity(state, config, day), "day": day})
        receipt_path = workspace_file(root, accepted.get("completion_receipt"), "accepted completion receipt")
        receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        if receipt_hash != str(accepted.get("completion_receipt_sha256") or ""):
            raise ProgramError("accepted completion receipt has changed; preserve it and inspect before withdrawal")
        # Qualification was already proven before this record entered
        # accepted_cases.  Withdrawal is a reversible state transition, not a
        # second acceptance under today's possibly revised topic contract.  A
        # stable receipt hash preserves the exact evidence that qualified it
        # and prevents a changed artifact from being silently withdrawn.
        record = {
            **accepted,
            "status": "withdrawn",
            "withdrawn_at": checked_at,
            "withdrawal_reason": reason,
            "withdrawal_receipt_sha256": receipt_hash,
        }
        accepted_cases(state).remove(accepted)
        withdrawn_cases(state).append(record)
        write_json(directory / "state.json", state)
    return output({"status": "withdrawn", "case": record, "capacity": capacity(state, config, day), "day": day})


def command_mark_case_needs_revalidation(args: argparse.Namespace) -> int:
    """Preserve accepted evidence and capacity while blocking a false method green."""
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    case_id = safe_id(args.case_id, "case_id")
    reason = str(args.reason or "").strip()
    if not reason:
        raise ProgramError("revalidation reason is required")
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        accepted = next((case for case in accepted_cases(state) if case.get("case_id") == case_id), None)
        if accepted is None:
            raise ProgramError("case is not qualified for method revalidation")
        current = accepted.get("method_verification")
        if isinstance(current, dict) and current.get("state") == "needs_revalidation":
            if current.get("reason") != reason:
                raise ProgramError("case is already marked needs_revalidation with a different reason")
            return output({"status": "replayed", "case": accepted, "capacity": capacity(state, config, day), "day": day})
        receipt_path = workspace_file(root, accepted.get("completion_receipt"), "accepted completion receipt")
        receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        if receipt_hash != str(accepted.get("completion_receipt_sha256") or ""):
            raise ProgramError("accepted completion receipt has changed; preserve it and inspect before revalidation")
        accepted["method_verification"] = {
            "state": "needs_revalidation",
            "reason": reason,
            "marked_at": checked_at,
            "completion_receipt_sha256": receipt_hash,
        }
        write_json(directory / "state.json", state)
    return output({"status": "needs_revalidation", "case": accepted, "capacity": capacity(state, config, day), "day": day})


def command_complete_case_revalidation(args: argparse.Namespace) -> int:
    """Restore a demoted method claim only from a new Compiler revision receipt.

    This is intentionally separate from ``complete-case``: an accepted source
    retains its old receipt and capacity, while a fresh revision must prove the
    current topic contract against new case evidence and a new writer readback.
    """
    root = args.root.resolve()
    directory, config, state = load_topic(root, args.topic_id)
    day, checked_at, _ = event_times(args, str(config["timezone"]))
    case_id = safe_id(args.case_id, "case_id")
    receipt_path = workspace_file(root, args.completion_receipt, "case revalidation completion receipt")
    receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        accepted = next((case for case in accepted_cases(state) if case.get("case_id") == case_id), None)
        if accepted is None:
            raise ProgramError("case is not an accepted source for revalidation")
        original_receipt_path = workspace_file(root, accepted.get("completion_receipt"), "accepted completion receipt")
        original_receipt_hash = hashlib.sha256(original_receipt_path.read_bytes()).hexdigest()
        if original_receipt_hash != str(accepted.get("completion_receipt_sha256") or ""):
            raise ProgramError("accepted completion receipt has changed; preserve it and inspect before revalidation")
        current = accepted.get("method_verification") if isinstance(accepted.get("method_verification"), dict) else {}
        current_state = method_verification_state(accepted)
        if current_state == "verified":
            revalidation = current.get("revalidation") if isinstance(current.get("revalidation"), dict) else {}
            if revalidation.get("completion_receipt_sha256") == receipt_hash:
                return output({
                    "status": "replayed", "case": accepted, "capacity": capacity(state, config, day),
                    "method_verification": method_verification_summary(state), "day": day,
                })
            raise ProgramError("case method is already verified by a different revalidation receipt")
        if current_state != "needs_revalidation":
            raise ProgramError("case method-verification state is not eligible for revalidation")
        selection_contract = topic_selection_contract(config)
        if selection_contract is None:
            raise ProgramError("topic selection contract is required for method revalidation")
        try:
            selection_contract = validate_selection_contract(selection_contract)
        except CaseEvidenceError as exc:
            raise ProgramError(f"topic selection contract is invalid: {exc}") from exc
        receipt = immutable_case_completion(
            root,
            receipt_path,
            expected_source_identity=str(accepted.get("source_identity") or ""),
            expected_primary_url=str(accepted.get("primary_url") or ""),
            expected_case_id=case_id,
            expected_topic_id=args.topic_id,
            evidence_contract=topic_evidence_contract(config),
            selection_contract=selection_contract,
        )
        kp_path = workspace_file(root, receipt.get("knowledge_package_path"), "revalidation Knowledge Package")
        package = kp_path.parent
        kp = read_compiler_json(kp_path)
        revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
        if revision.get("kind") != "case_revalidation":
            raise ProgramError("completion receipt is not a case-revalidation revision")
        lineage = validate_revalidation_lineage(
            root,
            receipt_path,
            accepted_receipt_path=original_receipt_path,
            accepted_receipt_sha256=original_receipt_hash,
            case_id=case_id,
            topic_id=args.topic_id,
            source_id=str(accepted.get("source_id") or ""),
            source_identity=str(accepted.get("source_identity") or ""),
            selection_contract=selection_contract,
        )
        source = kp.get("source") if isinstance(kp.get("source"), dict) else {}
        case_evidence = kp.get("case_evidence") if isinstance(kp.get("case_evidence"), dict) else {}
        if (
            source.get("case_evidence_manifest_sha256") != case_evidence.get("manifest_sha256")
            or not lineage
        ):
            raise ProgramError("case revalidation receipt does not bind its current case evidence")
        accepted["method_verification"] = {
            "state": "verified",
            "verified_at": checked_at,
            "completion_receipt_sha256": original_receipt_hash,
            "selection_contract_version": str(selection_contract.get("version") or "topic_specific"),
            "revalidation": {
                "revision_id": revision.get("revision_id"),
                "completion_receipt": receipt_path.relative_to(root).as_posix(),
                "completion_receipt_sha256": receipt_hash,
                "package": package.relative_to(root).as_posix(),
                "case_evidence_manifest_sha256": source.get("case_evidence_manifest_sha256"),
                "selection_contract_sha256": revision.get("selection_contract_sha256"),
                "original_completion_receipt_sha256": original_receipt_hash,
                "lineage_revision_ids": lineage,
            },
        }
        write_json(directory / "state.json", state)
    return output({
        "status": "verified", "case": accepted, "capacity": capacity(state, config, day),
        "method_verification": method_verification_summary(state), "day": day,
    })

def command_status(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    _, config, state = load_topic(root, args.topic_id)
    day, _, _ = event_times(args, str(config["timezone"]))
    reconcile_pending_for_day(state, day)
    counts = capacity(state, config, day)
    run_id = state.get("day_runs", {}).get(day)
    run = state.get("runs", {}).get(run_id) if run_id else None
    direct_command = direct_command_for_run(run) if isinstance(run, dict) else None
    automatic_zero_external_search = counts["total_accepted"] >= counts["total_cap"]
    return output({"status": "paused" if not config.get("enabled") else "ready", "topic": config, "capacity": counts, "method_verification": method_verification_summary(state), "accepted_cases": accepted_cases(state), "withdrawn_cases": withdrawn_cases(state), "active_cases": active_cases(state), "active_work_unit_run_id": run_id, "active_work_unit": run, "zero_external_search": automatic_zero_external_search and direct_command is None, "automatic_zero_external_search": automatic_zero_external_search})


def command_reconcile_quota_lanes(args: argparse.Namespace) -> int:
    """Persist the lane of historic records without rebinding their evidence."""
    root = args.root.resolve()
    directory, config, _ = load_topic(root, args.topic_id)
    day, _, _ = event_times(args, str(config["timezone"]))
    changed: dict[str, int] = {}
    with program_lock(directory):
        _, config, state = load_topic(root, args.topic_id)
        groups: dict[str, list[dict[str, Any]]] = {
            "accepted_cases": accepted_cases(state), "active_cases": list(active_cases(state).values()),
            "discovery_slots": list(discovery_slots(state).values()), "rejected_cases": state["rejected_cases"],
            "rejected_discoveries": state["rejected_discoveries"], "withdrawn_cases": withdrawn_cases(state),
        }
        for name, records in groups.items():
            updates = 0
            for record in records:
                if not isinstance(record, dict):
                    raise ProgramError(f"{name} record is invalid")
                lane = derived_quota_lane_for_record(state, record)
                if record.get("quota_lane") != lane:
                    record["quota_lane"] = lane
                    updates += 1
            if updates:
                changed[name] = updates
        if state.get("quota_lane_policy_version") != QUOTA_LANE_POLICY_VERSION:
            state["quota_lane_policy_version"] = QUOTA_LANE_POLICY_VERSION
            changed["policy"] = 1
        if changed:
            write_json(directory / "state.json", state)
    return output({"status": "reconciled" if changed else "already_reconciled", "topic_id": args.topic_id, "changed": changed, "capacity": capacity(state, config, day), "policy_version": QUOTA_LANE_POLICY_VERSION})


def command_record_application(args: argparse.Namespace) -> int:
    """Attach a bounded reuse trial without rebinding an accepted source."""
    root = args.root.resolve()
    manifest_path = workspace_file(root, args.trial_manifest, "reuse trial manifest")
    try:
        trial = read_json(manifest_path)
    except ProgramError as exc:
        raise ProgramError("reuse trial manifest is invalid") from exc
    if trial.get("schema_version") != "research_reuse_trial_v1":
        raise ProgramError("reuse trial manifest has an unsupported schema")
    source_id = str(args.source_id or "").strip()
    with program_lock(topic_dir(root, args.topic_id)):
        _, config, state = load_topic(root, args.topic_id)
        accepted = next((case for case in accepted_cases(state) if case.get("source_id") == source_id), None)
        if not accepted:
            raise ProgramError("reuse trial source is not an accepted case for this topic")
        if method_verification_state(accepted) != "verified":
            raise ProgramError("reuse trial source is not method-verified; complete source-bound revalidation first")
        receipt_value = str(accepted.get("completion_receipt") or "")
        receipt_path = workspace_file(root, receipt_value, "accepted completion receipt")
        receipt_hash = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        if receipt_hash != str(accepted.get("completion_receipt_sha256") or ""):
            raise ProgramError("accepted completion receipt has changed; do not attach a reuse trial")
        receipt = immutable_case_completion(
            root,
            receipt_path,
            expected_source_identity=str(accepted.get("source_identity") or ""),
            expected_primary_url=str(accepted.get("primary_url") or ""),
            expected_case_id=str(accepted.get("case_id") or ""),
            expected_topic_id=args.topic_id,
            evidence_contract=topic_evidence_contract(config),
            selection_contract=accepted_selection_contract(accepted),
        )
        if (
            str(trial.get("source_id") or "") != source_id
            or str(trial.get("topic_id") or "") != args.topic_id
            or str(trial.get("source_identity") or "") != str(receipt.get("source_identity") or "")
        ):
            raise ProgramError("reuse trial manifest does not bind this accepted source identity")
        try:
            written = write_research_reuse_application(
                root=root,
                vault=vault_path(root),
                source_id=source_id,
                source_identity=str(receipt.get("source_identity") or ""),
                topic_id=args.topic_id,
                trial=trial,
            )
        except AdapterStop as exc:
            raise ProgramError(f"reuse application {exc.status}: {exc.message}") from exc
    return output({
        "status": written["status"],
        "source_id": source_id,
        "application_note_path": written["application_note_path"],
        "original_application_note_path": written["original_application_note_path"],
        "completion_receipt": receipt_value,
        "completion_receipt_sha256": receipt_hash,
        "source_identity": str(receipt.get("source_identity") or ""),
        "source_count_change": 0,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-topic")
    init.add_argument("--topic-id", required=True); init.add_argument("--title", required=True)
    init.add_argument("--profile-version", required=True); init.add_argument("--purpose", required=True)
    init.add_argument("--scope", required=True); init.add_argument("--focus", required=True)
    init.add_argument("--evidence-contract", required=True); init.add_argument("--selection-contract"); init.add_argument("--output-contract", required=True)
    init.add_argument("--classifications", required=True); init.add_argument("--mode", choices=["manual_research"], required=True)
    init.add_argument("--timezone", default="Europe/London"); init.add_argument("--daily-case-cap", type=int, required=True); init.add_argument("--total-case-cap", type=int, required=True)
    init.add_argument("--budget-searches", type=int, default=0, help=argparse.SUPPRESS); init.add_argument("--budget-candidate-summaries-target", type=int, default=0, help=argparse.SUPPRESS); init.add_argument("--budget-deep-reads", type=int, default=0, help=argparse.SUPPRESS)
    init.set_defaults(func=command_init)
    grant = sub.add_parser("grant-manual-allowance"); grant.add_argument("--topic-id", required=True); grant.add_argument("--allowance-id", required=True); grant.add_argument("--parent-run-id", required=True); grant.add_argument("--authorization-source", required=True); grant.add_argument("--case-cap", type=int, required=True); grant.add_argument("--budget-searches", type=int, default=0, help=argparse.SUPPRESS); grant.add_argument("--budget-candidate-summaries-target", type=int, default=0, help=argparse.SUPPRESS); grant.add_argument("--budget-deep-reads", type=int, default=0, help=argparse.SUPPRESS); grant.add_argument("--executor-model", required=True); grant.add_argument("--executor-reasoning-effort", required=True); grant.add_argument("--at"); grant.add_argument("--isolated-test-clock", action="store_true"); grant.set_defaults(func=command_grant_manual_allowance)
    preflight = sub.add_parser("preflight"); preflight.add_argument("--topic-id", required=True); preflight.set_defaults(func=command_preflight)
    begin = sub.add_parser("begin-run"); begin.add_argument("--topic-id", required=True); begin.add_argument("--run-id"); begin.add_argument("--at"); begin.add_argument("--isolated-test-clock", action="store_true"); begin.add_argument("--execution-mode", choices=["manual_research"]); begin.add_argument("--executor-model"); begin.add_argument("--executor-reasoning-effort"); begin.add_argument("--manual-allowance-id"); begin.add_argument("--direct-command-source"); begin.set_defaults(func=command_begin)
    reserve_case = sub.add_parser("reserve-case"); reserve_case.add_argument("--topic-id", required=True); reserve_case.add_argument("--run-id", required=True); reserve_case.add_argument("--case-id", required=True); reserve_case.add_argument("--primary-url", required=True); reserve_case.add_argument("--classification"); reserve_case.add_argument("--classification-facets"); reserve_case.add_argument("--design-family"); reserve_case.add_argument("--at"); reserve_case.add_argument("--isolated-test-clock", action="store_true"); reserve_case.set_defaults(func=command_reserve_case)
    reserve_discovery = sub.add_parser("reserve-discovery"); reserve_discovery.add_argument("--topic-id", required=True); reserve_discovery.add_argument("--run-id", required=True); reserve_discovery.add_argument("--discovery-id", required=True); reserve_discovery.add_argument("--at"); reserve_discovery.add_argument("--isolated-test-clock", action="store_true"); reserve_discovery.set_defaults(func=command_reserve_discovery)
    bind_discovery = sub.add_parser("bind-discovery"); bind_discovery.add_argument("--topic-id", required=True); bind_discovery.add_argument("--run-id", required=True); bind_discovery.add_argument("--discovery-id", required=True); bind_discovery.add_argument("--case-id", required=True); bind_discovery.add_argument("--primary-url", required=True); bind_discovery.add_argument("--classification"); bind_discovery.add_argument("--classification-facets"); bind_discovery.add_argument("--design-family"); bind_discovery.add_argument("--at"); bind_discovery.add_argument("--isolated-test-clock", action="store_true"); bind_discovery.set_defaults(func=command_bind_discovery)
    reject_discovery = sub.add_parser("reject-discovery"); reject_discovery.add_argument("--topic-id", required=True); reject_discovery.add_argument("--run-id", required=True); reject_discovery.add_argument("--discovery-id", required=True); reject_discovery.add_argument("--reason", required=True); reject_discovery.add_argument("--at"); reject_discovery.add_argument("--isolated-test-clock", action="store_true"); reject_discovery.set_defaults(func=command_reject_discovery)
    reject_case = sub.add_parser("reject-case"); reject_case.add_argument("--topic-id", required=True); reject_case.add_argument("--run-id", required=True); reject_case.add_argument("--case-id", required=True); reject_case.add_argument("--reason", required=True); reject_case.add_argument("--at"); reject_case.add_argument("--isolated-test-clock", action="store_true"); reject_case.set_defaults(func=command_reject_case)
    reserve_action = sub.add_parser("reserve-action"); reserve_action.add_argument("--topic-id", required=True); reserve_action.add_argument("--run-id", required=True); reserve_action.add_argument("--case-id"); reserve_action.add_argument("--discovery-id"); reserve_action.add_argument("--candidate-url"); reserve_action.add_argument("--action", choices=sorted(ACTION_LIMITS), required=True); reserve_action.add_argument("--action-id"); reserve_action.add_argument("--at"); reserve_action.add_argument("--isolated-test-clock", action="store_true"); reserve_action.add_argument("--test-automation-path", default="", help=argparse.SUPPRESS); reserve_action.add_argument("--test-host-timezone-id", default="", help=argparse.SUPPRESS); reserve_action.set_defaults(func=command_reserve_action)
    complete_action = sub.add_parser("complete-action"); complete_action.add_argument("--topic-id", required=True); complete_action.add_argument("--run-id", required=True); complete_action.add_argument("--action-id", required=True); complete_action.add_argument("--actual-results", type=int, required=True); complete_action.add_argument("--at"); complete_action.add_argument("--isolated-test-clock", action="store_true"); complete_action.set_defaults(func=command_complete_action)
    complete_case = sub.add_parser("complete-case"); complete_case.add_argument("--topic-id", required=True); complete_case.add_argument("--run-id", required=True); complete_case.add_argument("--case-id", required=True); complete_case.add_argument("--completion-receipt", required=True); complete_case.add_argument("--at"); complete_case.add_argument("--isolated-test-clock", action="store_true"); complete_case.set_defaults(func=command_complete_case)
    withdraw_case = sub.add_parser("withdraw-case"); withdraw_case.add_argument("--topic-id", required=True); withdraw_case.add_argument("--case-id", required=True); withdraw_case.add_argument("--reason", required=True); withdraw_case.add_argument("--at"); withdraw_case.add_argument("--isolated-test-clock", action="store_true"); withdraw_case.set_defaults(func=command_withdraw_case)
    mark_revalidation = sub.add_parser("mark-case-needs-revalidation"); mark_revalidation.add_argument("--topic-id", required=True); mark_revalidation.add_argument("--case-id", required=True); mark_revalidation.add_argument("--reason", required=True); mark_revalidation.add_argument("--at"); mark_revalidation.add_argument("--isolated-test-clock", action="store_true"); mark_revalidation.set_defaults(func=command_mark_case_needs_revalidation)
    complete_revalidation = sub.add_parser("complete-case-revalidation"); complete_revalidation.add_argument("--topic-id", required=True); complete_revalidation.add_argument("--case-id", required=True); complete_revalidation.add_argument("--completion-receipt", required=True); complete_revalidation.add_argument("--at"); complete_revalidation.add_argument("--isolated-test-clock", action="store_true"); complete_revalidation.set_defaults(func=command_complete_case_revalidation)
    record_application = sub.add_parser("record-application"); record_application.add_argument("--topic-id", required=True); record_application.add_argument("--source-id", required=True); record_application.add_argument("--trial-manifest", required=True); record_application.set_defaults(func=command_record_application)
    status = sub.add_parser("status"); status.add_argument("--topic-id", required=True); status.add_argument("--at"); status.add_argument("--isolated-test-clock", action="store_true"); status.set_defaults(func=command_status)
    args = parser.parse_args()
    try:
        if args.command not in {"preflight", "status"}:
            assert_management_idle(args.root.resolve())
        return args.func(args)
    except (ProgramError, ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
