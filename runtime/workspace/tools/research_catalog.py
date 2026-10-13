"""Read-only catalogue for bounded research topics and their reusable material.

The registry remains the source of truth.  This module neither starts a run nor
changes a scheduler; it only exposes the small projection used by the reader,
selector and ask preparation.
"""
from __future__ import annotations

import argparse
import json
import re
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.outcome_page_generator import read_markdown_record, resolve_application_note
    from tools.research_program import SCHEMA, ProgramError, direct_command_for_run, load_topic, safe_id, topic_evidence_contract, validate_topic_definition
    from tools.workspace_paths import WorkspacePathError, vault_path
except ModuleNotFoundError:
    from outcome_page_generator import read_markdown_record, resolve_application_note  # type: ignore
    from research_program import SCHEMA, ProgramError, direct_command_for_run, load_topic, safe_id, topic_evidence_contract, validate_topic_definition  # type: ignore
    from workspace_paths import WorkspacePathError, vault_path  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
CATALOG_SCHEMA = "research_catalog_v1"
DEFAULT_AUTOMATION_PATHS = (Path.home() / ".codex" / "automations" / "automation" / "automation.toml",)
RESEARCH_ACTIONS = frozenset({"overview", "inspect", "create", "run", "resume", "configure", "schedule", "feedback"})
TOPIC_REQUIRED_ACTIONS = RESEARCH_ACTIONS - {"overview", "create"}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def query_terms(query: str) -> list[str]:
    """Use the selector's bounded CJK n-grams for the same local query meaning."""
    chunks = re.findall(r"[A-Za-z0-9_+-]+|[\u4e00-\u9fff]+", query.lower())
    terms: set[str] = set()
    for chunk in chunks:
        if len(chunk) <= 1:
            continue
        terms.add(chunk)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            for size in (2, 3, 4):
                if len(chunk) > size:
                    terms.update(chunk[index:index + size] for index in range(0, len(chunk) - size + 1))
    return sorted(terms, key=lambda item: (-len(item), item))


def match_terms(text: str, terms: Iterable[str]) -> set[str]:
    normalized = re.sub(r"\s+", "", text.casefold())
    return {term for term in terms if re.sub(r"\s+", "", term.casefold()) in normalized}


def topic_profile_text(topic: dict[str, Any]) -> str:
    """Only declared topic intent may broaden recall to that topic's own cases."""
    return json.dumps(
        {key: topic.get(key, "") for key in ("display_name", "purpose", "scope", "focus", "evidence", "output", "classifications")},
        ensure_ascii=False,
        sort_keys=True,
    )


def topic_profile_matches(topic: dict[str, Any], terms: Iterable[str]) -> set[str]:
    """Avoid treating one generic word such as AI as a topic-selection request."""
    matches = match_terms(topic_profile_text(topic), terms)
    meaningful = {term for term in matches if len(term) >= 3 or (term.isascii() and len(term) >= 2)}
    return matches if len(meaningful) >= 3 else set()


def vault_for(root: Path) -> Path:
    try:
        return vault_path(root)
    except WorkspacePathError as exc:
        raise ProgramError(str(exc)) from exc


def relative_vault_path(vault: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(vault.resolve()).as_posix()
    except (OSError, ValueError):
        return ""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at {path.name}:{line_number}") from exc
        if isinstance(value, dict):
            rows.append(value)
    return rows


def source_rows(vault: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in read_jsonl(vault / "_registry" / "sources.jsonl"):
        source_id = str(row.get("source_id") or "").strip()
        if source_id and source_id not in rows:
            rows[source_id] = row
    return rows


def list_topic_ids(root: Path) -> list[str]:
    directory = vault_for(root) / "_registry" / "research-programs"
    if not directory.is_dir():
        return []
    return sorted(path.name for path in directory.iterdir() if path.is_dir() and (path / "topic.json").is_file())


def string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return sorted({str(item).strip() for item in value if str(item).strip()})
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def ordered_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalized_classifications(raw: object, case: dict[str, Any] | None = None) -> dict[str, Any]:
    """Stable facet shape; callers can render only the list values they know."""
    source = raw if isinstance(raw, dict) else {}
    primary_key = str(source.get("primary_key") or "classification").strip()
    values = source.get("values") if isinstance(source.get("values"), dict) else {}
    normalized = {
        "primary_key": primary_key,
        "values": {str(key): string_list(value) for key, value in values.items()},
        "design_families": string_list(source.get("design_families")),
        "coverage_dimensions": string_list(source.get("coverage_dimensions")),
        "application_scenes": string_list(source.get("application_scenes")),
        "tags": string_list(source.get("tags")),
    }
    if case:
        case_key = str(case.get("classification_key") or primary_key).strip()
        case_value = str(case.get("classification") or case.get("design_family") or "").strip()
        if case_value:
            normalized["values"].setdefault(case_key, [])
            if case_value not in normalized["values"][case_key]:
                normalized["values"][case_key].append(case_value)
            if case_key == "design_family" and case_value not in normalized["design_families"]:
                normalized["design_families"].append(case_value)
    return normalized


def case_classifications(definition: object, case: dict[str, Any]) -> dict[str, Any]:
    """A source carries only classifications supported by that source/case.

    Topic values are an optional vocabulary, not evidence that every source has
    every facet.  The old `design_family` remains a compatible, evidenced case
    value.
    """
    base = definition if isinstance(definition, dict) else {}
    primary_key = str(base.get("primary_key") or "classification").strip()
    result = {
        "primary_key": str(case.get("classification_key") or primary_key),
        "values": {},
        "design_families": [],
        "coverage_dimensions": [],
        "application_scenes": [],
        "tags": [],
    }
    actual = case.get("classifications") if isinstance(case.get("classifications"), dict) else {}
    for key in ("design_families", "coverage_dimensions", "application_scenes", "tags"):
        result[key] = string_list(actual.get(key))
    actual_values = actual.get("values") if isinstance(actual.get("values"), dict) else {}
    result["values"] = {str(key): string_list(value) for key, value in actual_values.items()}
    case_value = str(case.get("classification") or case.get("design_family") or "").strip()
    if case_value:
        key = result["primary_key"]
        result["values"][key] = [case_value]
        if key == "design_family":
            result["design_families"] = [case_value]
    return result


def source_note_path(vault: Path, source_row: dict[str, Any]) -> str:
    raw = str(source_row.get("source_note_path") or "").strip()
    if not raw:
        return ""
    return relative_vault_path(vault, vault / raw)


def derived_application_records(vault: Path, source_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    application_root = vault / "applications"
    if not application_root.is_dir():
        return records
    # A separate hyphenated name cannot shadow the receipt-bound APP-<id>_<slug>.
    for path in sorted(application_root.glob(f"**/APP-{source_id}-reuse-*.md")):
        relative = relative_vault_path(vault, path)
        if not relative:
            continue
        fields, _body = read_markdown_record(path)
        if str(fields.get("derivation_kind") or "") != "research_reuse_trial":
            continue
        dimensions = ordered_string_list(fields.get("assessment_dimensions"))
        statuses = ordered_string_list(fields.get("assessment_statuses"))
        summaries = ordered_string_list(fields.get("assessment_summaries"))
        assessments = []
        for index, dimension in enumerate(dimensions):
            raw_status = statuses[index] if index < len(statuses) else ""
            status = raw_status.split(":", 1)[1] if raw_status.startswith(f"{dimension}:") else raw_status
            assessments.append({"dimension": dimension, "status": status, "summary": summaries[index] if index < len(summaries) else ""})
        by_dimension = {str(item["dimension"]): str(item["status"]) for item in assessments}
        records.append({
            "material_kind": "application",
            "application_id": str(fields.get("application_id") or path.stem),
            "title": str(fields.get("title") or path.stem),
            "application_note_path": relative,
            "source_id": source_id,
            "source_note_path": str(fields.get("source_entry") or ""),
            "original_application_note_path": str(fields.get("original_application_note") or ""),
            "research_topic_id": str(fields.get("research_topic_id") or ""),
            "trial_id": str(fields.get("trial_id") or ""),
            "status": str(fields.get("application_status") or ""),
            "reuse_context": {
                "method": str(fields.get("reuse_method") or ""),
                "citations": string_list(fields.get("evidence_paths")),
                "work_paths": string_list(fields.get("work_path")),
                "evidence_paths": string_list(fields.get("evidence_paths")),
                "related_applications": string_list(fields.get("related_applications")),
                "assessments": assessments,
                "technical_effectiveness": by_dimension.get("technical_effectiveness", ""),
                "aesthetic_quality": by_dimension.get("aesthetic_quality", ""),
                "context_fit": by_dimension.get("context_fit", ""),
                "lesson": str(fields.get("lesson") or ""),
                "limitations": string_list(fields.get("limitations")),
            },
        })
    return records


def canonical_application_path(vault: Path, source_id: str, row: dict[str, Any]) -> str:
    source_relative = source_note_path(vault, row)
    if not source_relative:
        return ""
    source_note = vault / source_relative
    try:
        return relative_vault_path(vault, resolve_application_note(vault, source_id, source_note))
    except SystemExit:
        return ""


def topic_source_bindings(root: Path, topic_ids: Iterable[str] | None = None) -> dict[str, dict[str, Any]]:
    """Source identity is kept singular even when it has several applications."""
    allowed = set(topic_ids or [])
    bindings: dict[str, dict[str, Any]] = {}
    for topic_id in list_topic_ids(root):
        if allowed and topic_id not in allowed:
            continue
        try:
            _, config, state = load_topic(root.resolve(), topic_id)
        except ProgramError:
            continue
        for case in state.get("accepted_cases", []):
            if not isinstance(case, dict):
                continue
            source_id = str(case.get("source_id") or "").strip()
            if not source_id:
                continue
            binding = bindings.setdefault(source_id, {"topic_ids": [], "classifications": []})
            if topic_id not in binding["topic_ids"]:
                binding["topic_ids"].append(topic_id)
            binding["classifications"].append(case_classifications(config.get("classifications"), case))
    return bindings


def scheduler_status(root: Path, config: dict[str, Any], automation_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    checked_at = now_iso()
    if str(config.get("mode") or "scheduled_research") != "scheduled_research":
        return {"state": "not_configured", "active": False, "checked_at": checked_at, "reason": "topic mode is manual_research"}
    schedule = config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {}
    name = str(schedule.get("automation_name") or "").strip()
    if not name:
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "topic has no scheduler identity"}
    candidates = list(automation_paths or DEFAULT_AUTOMATION_PATHS)
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return {"state": "missing", "active": False, "checked_at": checked_at, "reason": "no configured automation.toml was found"}
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in existing:
        try:
            record = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": f"cannot read automation record: {exc}"}
        if str(record.get("name") or "") == name:
            matches.append((path, record))
    if len(matches) != 1:
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "scheduler identity is absent or ambiguous"}
    path, record = matches[0]
    expected_time = str(schedule.get("time") or "")
    try:
        hour, minute = (int(value) for value in expected_time.split(":", 1))
    except (TypeError, ValueError):
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "topic schedule time is invalid", "path": str(path)}
    rrule_parts = {}
    for segment in str(record.get("rrule") or "").split(";"):
        if "=" in segment:
            key, value = segment.split("=", 1)
            rrule_parts[key] = value
    expected_cwd = str(root.resolve()).casefold()
    actual_cwds = [str(value).casefold() for value in record.get("cwds", []) if isinstance(value, str)]
    aligned = (
        str(record.get("model") or "") == str(schedule.get("model") or "")
        and str(record.get("reasoning_effort") or "") == str(schedule.get("reasoning_effort") or "")
        and expected_cwd in actual_cwds
        and str(record.get("kind") or "") == "cron"
        and str(schedule.get("frequency") or "") == "daily"
        and rrule_parts.get("FREQ") == "DAILY"
        and rrule_parts.get("BYHOUR") == str(hour)
        and rrule_parts.get("BYMINUTE") == str(minute)
        and str(record.get("execution_environment") or "") == "local"
    )
    if not aligned:
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "automation exists but does not match this topic contract", "path": str(path)}
    status = str(record.get("status") or "")
    if status == "ACTIVE":
        return {"state": "active", "active": True, "checked_at": checked_at, "reason": "matching automation status is ACTIVE", "path": str(path)}
    if status == "PAUSED":
        return {"state": "paused", "active": False, "checked_at": checked_at, "reason": "matching automation status is PAUSED", "path": str(path)}
    return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "matching automation has an unrecognized status", "path": str(path)}


def work_status(state: dict[str, Any]) -> dict[str, Any]:
    active = state.get("active_cases")
    discoveries = state.get("discovery_slots")
    if not isinstance(active, dict) or not isinstance(discoveries, dict):
        return {"state": "unknown", "pending": False, "reason": "topic state has no readable pending-work collections"}
    pending = [key for key, row in active.items() if isinstance(row, dict) and row.get("status") in {"reserved", "committing"}]
    pending.extend(key for key, row in discoveries.items() if isinstance(row, dict) and row.get("status") == "reserved")
    if pending:
        return {"state": "pending", "pending": True, "reason": "reserved or committing work exists", "ids": sorted(pending)}
    return {"state": "none", "pending": False, "reason": "no reserved or committing work"}


def inspect_research_topic(root: Path, topic_id: str, *, automation_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    root = root.resolve()
    vault = vault_for(root)
    checked_at = now_iso()
    try:
        directory, config, state = load_topic(root, topic_id)
        if "profile_version" in config:
            validate_topic_definition(config)
    except (ProgramError, ValueError) as exc:
        return {
            "topic_id": topic_id, "registered": False, "checked_at": checked_at,
            "scheduler": {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "topic definition is unreadable"},
            "work": {"state": "unknown", "pending": False, "reason": "topic definition is unreadable"},
            "status": {"registered": False, "scheduler_active": False, "work_pending": False, "unknown": True},
            "warnings": [str(exc)],
        }
    sources = source_rows(vault)
    accepted = [case for case in state.get("accepted_cases", []) if isinstance(case, dict)]
    source_materials: list[dict[str, Any]] = []
    for case in accepted:
        source_id = str(case.get("source_id") or "").strip()
        row = sources.get(source_id, {})
        source_path = source_note_path(vault, row)
        canonical_path = canonical_application_path(vault, source_id, row) if row else ""
        derived = derived_application_records(vault, source_id)
        outcome = vault / "outcome-pages" / f"{source_id}_outcome.html"
        source_materials.append({
            "material_kind": "source",
            "source_id": source_id,
            "source_identity": str(case.get("source_identity") or row.get("source_identity") or ""),
            "source_input_kind": str(row.get("source_input_kind") or ""),
            "source_note_path": source_path,
            "application_note_path": canonical_path,
            "outcome_page_path": relative_vault_path(vault, outcome) if outcome.is_file() else "",
            "topic_ids": [topic_id],
            "classifications": case_classifications(config.get("classifications"), case),
            "latest_receipt": {"path": str(case.get("completion_receipt") or ""), "sha256": str(case.get("completion_receipt_sha256") or "")},
            "applications": derived,
        })
    scheduler = scheduler_status(root, config, automation_paths)
    work = work_status(state)
    all_applications = [app for material in source_materials for app in material["applications"]]
    day_runs = state.get("day_runs") if isinstance(state.get("day_runs"), dict) else {}
    latest_day = max((str(day) for day, run_id in day_runs.items() if isinstance(run_id, str)), default="")
    latest_run_id = str(day_runs.get(latest_day) or "") if latest_day else ""
    pending_run_ids = {
        str(row.get("run_id") or "")
        for collection in (state.get("active_cases", {}), state.get("discovery_slots", {}))
        if isinstance(collection, dict)
        for row in collection.values()
        if isinstance(row, dict) and row.get("status") in {"reserved", "committing"}
    }
    warnings: list[str] = []
    if not source_materials:
        warnings.append("topic has no accepted source mapping")
    return {
        "schema_version": CATALOG_SCHEMA,
        "topic_id": topic_id,
        "registered": True,
        "registration_path": (directory / "topic.json").relative_to(root).as_posix(),
        "checked_at": checked_at,
        "display_name": str(config.get("title") or topic_id),
        "profile_version": str(config.get("profile_version") or "legacy-v1"),
        "purpose": str(config.get("purpose") or ""),
        "scope": config.get("scope") if isinstance(config.get("scope"), dict) else {},
        "focus": string_list(config.get("focus")),
        "evidence": topic_evidence_contract(config),
        "output": config.get("output_contract") if isinstance(config.get("output_contract"), dict) else {},
        "classifications": normalized_classifications(config.get("classifications")),
        "mode": str(config.get("mode") or "scheduled_research"),
        "budget": config.get("budget") if isinstance(config.get("budget"), dict) else {},
        "schedule_contract": config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {},
        "daily_case_cap": int(config.get("daily_case_cap") or 0),
        "total_case_cap": int(config.get("total_case_cap") or 0),
        "runner": {
            "ready": bool(config.get("enabled")),
            "reason": "topic.enabled permits a local run only; it is not scheduler evidence",
        },
        "scheduler": scheduler,
        "work": work,
        "status": {
            "registered": True,
            "scheduler_active": bool(scheduler.get("active")),
            "work_pending": bool(work.get("pending")),
            "unknown": scheduler.get("state") == "unknown" or work.get("state") == "unknown",
            "overall": "unknown" if scheduler.get("state") == "unknown" or work.get("state") == "unknown" else (
                "work_pending" if work.get("pending") else ("scheduler_active" if scheduler.get("active") else "registered")
            ),
        },
        "materials": source_materials,
        "applications": all_applications,
        "runs": {
            "active_work_unit_run_id": sorted(pending_run_ids)[0] if len(pending_run_ids) == 1 else "",
            "latest_recorded_run_id": latest_run_id,
            "count": len(state.get("runs", {}) if isinstance(state.get("runs"), dict) else {}),
        },
        "warnings": warnings,
    }


def list_research_topics(root: Path, *, automation_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    generated_at = now_iso()
    topics = [inspect_research_topic(root, topic_id, automation_paths=automation_paths) for topic_id in list_topic_ids(root)]
    return {
        "schema_version": CATALOG_SCHEMA,
        "generated_at": generated_at,
        "checked_at": now_iso(),
        "topics": topics,
        "truth_boundary": "Read-only registry projection. topic.enabled and runner readiness are not scheduler evidence.",
    }


def resolution_base(status: str, *, action: str = "", topic_ref: str = "") -> dict[str, Any]:
    """Return a deliberately non-executable entry decision.

    The skill can use this response to decide which existing topic or work unit
    to show next.  It is not a grant to invoke the runner, alter a topic, or
    alter automation state.
    """
    return {
        "schema_version": CATALOG_SCHEMA,
        "status": status,
        "action": action,
        "topic_ref": topic_ref,
        "read_only": True,
        "execution_authorized": False,
    }


def overview_topics(listing: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep the entry overview small and free of material/profile projections."""
    result: list[dict[str, Any]] = []
    for topic in listing.get("topics", []):
        if not isinstance(topic, dict):
            continue
        result.append({
            "topic_id": str(topic.get("topic_id") or ""),
            "display_name": str(topic.get("display_name") or ""),
            "registered": bool(topic.get("registered")),
            "purpose": str(topic.get("purpose") or ""),
            "source_count": len(topic.get("materials", [])) if isinstance(topic.get("materials"), list) else 0,
            "work": topic.get("work") if isinstance(topic.get("work"), dict) else {"state": "unknown", "pending": False},
            "scheduler": topic.get("scheduler") if isinstance(topic.get("scheduler"), dict) else {"state": "unknown", "active": False},
        })
    return sorted(result, key=lambda item: item["topic_id"])


def safe_workspace_read_path(root: Path, path: Path, label: str) -> str:
    """Expose only paths that resolve under this caller's selected workspace."""
    root_resolved = root.resolve()
    try:
        return path.resolve().relative_to(root_resolved).as_posix()
    except (OSError, ValueError) as exc:
        raise ProgramError(f"{label} escapes this workspace") from exc


def validate_topic_read_boundaries(root: Path, state: dict[str, Any]) -> None:
    """Reject receipt references that would lead a later entry outside the root."""
    accepted = state.get("accepted_cases")
    if not isinstance(accepted, list):
        raise ProgramError("research accepted-case state is invalid")
    for case in accepted:
        if not isinstance(case, dict):
            raise ProgramError("research accepted-case record is invalid")
        receipt = str(case.get("completion_receipt") or "").strip()
        if receipt:
            safe_workspace_read_path(root, root / receipt, "accepted completion receipt path")


def pending_work_items(state: dict[str, Any]) -> list[dict[str, str]]:
    """Read pending runner-owned work without assigning, changing, or repairing it."""
    active = state.get("active_cases")
    discoveries = state.get("discovery_slots")
    runs = state.get("runs")
    if not isinstance(active, dict) or not isinstance(discoveries, dict) or not isinstance(runs, dict):
        raise ProgramError("research topic state has invalid pending-work collections")

    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def append(work_id: object, run_id: object, kind: str) -> None:
        item_id = safe_id(str(work_id or ""), "work_id")
        owner = safe_id(str(run_id or ""), "run_id")
        if owner not in runs or not isinstance(runs[owner], dict):
            raise ProgramError("pending work does not bind an initialized research run")
        key = (kind, item_id)
        if key not in seen:
            seen.add(key)
            items.append({"work_id": item_id, "work_kind": kind, "run_id": owner})

    for key, row in active.items():
        if not isinstance(row, dict):
            raise ProgramError("research active-case state is invalid")
        if row.get("status") in {"reserved", "committing"}:
            append(key, row.get("run_id"), "case")
    for key, row in discoveries.items():
        if not isinstance(row, dict):
            raise ProgramError("research discovery-slot state is invalid")
        if row.get("status") == "reserved":
            append(key, row.get("run_id"), "discovery")
    for run_id, run in runs.items():
        if not isinstance(run, dict):
            raise ProgramError("research run state is invalid")
        actions = run.get("actions", [])
        if not isinstance(actions, list):
            raise ProgramError("research run action state is invalid")
        for action in actions:
            if not isinstance(action, dict) or action.get("status") != "reserved":
                continue
            action_id = action.get("action_id")
            if action.get("case_id") in active or action.get("discovery_id") in discoveries:
                continue
            append(action_id, run_id, "action")
    return sorted(items, key=lambda item: (item["work_id"], item["work_kind"]))


def run_summary(config: dict[str, Any], state: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Return recorded executor/budget facts without guessing actual execution."""
    runs = state.get("runs")
    run = runs.get(run_id) if isinstance(runs, dict) else None
    if not isinstance(run, dict):
        raise ProgramError("pending work does not bind an initialized research run")
    declared = run.get("declared_executor") if isinstance(run.get("declared_executor"), dict) else {}
    actual = run.get("actual_executor") if isinstance(run.get("actual_executor"), dict) else {}
    schedule = config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {}
    actions = run.get("actions", [])
    if not isinstance(actions, list) or any(not isinstance(action, dict) for action in actions):
        raise ProgramError("research run action state is invalid")
    topic_budget = config.get("budget") if isinstance(config.get("budget"), dict) else {}
    budget_source = "topic_profile"
    budget: dict[str, Any] = dict(topic_budget)
    direct_command = direct_command_for_run(run)
    allowance_id = str(run.get("manual_allowance_id") or "")
    allowances = state.get("manual_allowances") if isinstance(state.get("manual_allowances"), dict) else {}
    allowance = allowances.get(allowance_id) if allowance_id else None
    if direct_command is not None:
        budget = {key: None for key in ("cases", "searches", "candidate_summaries_target", "deep_reads")}
        budget_source = "direct_user_command"
    elif allowance_id:
        if not isinstance(allowance, dict) or allowance.get("consumed_by_run_id") != run_id:
            raise ProgramError("run manual allowance is invalid")
        limits = allowance.get("limits")
        if not isinstance(limits, dict):
            raise ProgramError("run manual allowance limits are invalid")
        budget = {key: limits.get(key) for key in ("cases", "searches", "candidate_summaries_target", "deep_reads")}
        budget_source = "manual_allowance"
    recorded_actions = [
        {
            key: action.get(key)
            for key in ("action_id", "case_id", "discovery_id", "action", "budget_key", "status", "effective_day", "actual_results")
            if key in action
        }
        for action in actions
    ]
    return {
        "run_id": run_id,
        "execution_mode": str(run.get("execution_mode") or config.get("mode") or ""),
        "profile_version": str(run.get("profile_version") or config.get("profile_version") or ""),
        "declared_executor": {
            "model": str(declared.get("model") or ""),
            "reasoning_effort": str(declared.get("reasoning_effort") or ""),
        },
        "profile_executor": {
            "model": str(schedule.get("model") or ""),
            "reasoning_effort": str(schedule.get("reasoning_effort") or ""),
            "source": "topic schedule_contract" if schedule else "not configured",
        },
        "actual_executor": {
            "model": str(actual.get("model") or ""),
            "reasoning_effort": str(actual.get("reasoning_effort") or ""),
            "status": str(actual.get("status") or "unknown"),
        },
        "budget": budget,
        "budget_source": budget_source,
        "direct_command": direct_command,
        "recorded_usage": {
            "source": "original run state; resolver does not reset, reserve, or recalculate usage",
            "actions": recorded_actions,
            "inherited_observed_usage": run.get("inherited_observed_usage") if isinstance(run.get("inherited_observed_usage"), dict) else {},
            "inherited_observed_usage_effective_day": str(run.get("inherited_observed_usage_effective_day") or ""),
        },
    }


def topic_resolution_view(root: Path, topic_id: str, inspected: dict[str, Any]) -> dict[str, Any]:
    """Read and bound the one topic record a subsequent skill step may display."""
    directory, config, state = load_topic(root, topic_id)
    validate_topic_definition(config)
    validate_topic_read_boundaries(root, state)
    profile_path = safe_workspace_read_path(root, directory / "topic.json", "topic profile path")
    state_path = safe_workspace_read_path(root, directory / "state.json", "topic state path")
    schedule = config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {}
    return {
        "topic_id": topic_id,
        "display_name": str(config.get("title") or topic_id),
        "read_paths": {"profile": profile_path, "state": state_path},
        "profile": {
            "profile_version": str(config.get("profile_version") or ""),
            "purpose": str(config.get("purpose") or ""),
            "scope": config.get("scope") if isinstance(config.get("scope"), dict) else {},
            "focus": string_list(config.get("focus")),
            "evidence": topic_evidence_contract(config),
            "mode": str(config.get("mode") or ""),
            "budget": config.get("budget") if isinstance(config.get("budget"), dict) else {},
            "schedule": {
                key: str(schedule.get(key) or "")
                for key in ("frequency", "time", "timezone", "model", "reasoning_effort", "automation_name")
            },
        },
        "work": inspected.get("work") if isinstance(inspected.get("work"), dict) else {"state": "unknown", "pending": False},
        "scheduler": inspected.get("scheduler") if isinstance(inspected.get("scheduler"), dict) else {"state": "unknown", "active": False},
        "pending_work": pending_work_items(state),
        "_state": state,
        "_config": config,
    }


def feedback_target_index(inspected: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """Index only receipt-bound material already attributed to this topic."""
    index: dict[str, list[dict[str, str]]] = {}

    def add(target_id: object, target_kind: str, source_id: object, application_id: object = "", trial_id: object = "") -> None:
        stable_id = str(target_id or "").strip()
        if not stable_id:
            return
        index.setdefault(stable_id, []).append({
            "target_id": stable_id,
            "target_kind": target_kind,
            "source_id": str(source_id or ""),
            "application_id": str(application_id or ""),
            "trial_id": str(trial_id or ""),
        })

    for material in inspected.get("materials", []):
        if isinstance(material, dict):
            add(material.get("source_id"), "source", material.get("source_id"))
    for application in inspected.get("applications", []):
        if not isinstance(application, dict):
            continue
        # Sharing a source permits discovery, not feedback on another topic's work.
        if not application.get("research_topic_id") or application["research_topic_id"] != inspected.get("topic_id"):
            continue
        source_id = application.get("source_id")
        add(application.get("application_id"), "application", source_id, application.get("application_id"), application.get("trial_id"))
        add(application.get("trial_id"), "trial", source_id, application.get("application_id"), application.get("trial_id"))
    return index


def resolve_research_action(
    root: Path,
    *,
    action: str | None = None,
    topic_ref: str | None = None,
    work_id: str | None = None,
    target_refs: Iterable[str] | None = None,
    automation_paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Resolve one explicit research action to a safe, read-only next step.

    Resolution never picks a topic from a partial phrase, the latest topic, a
    single enabled topic, or a material/application title.  A caller must pass
    an exact topic ID or one unique full topic title before any topic-scoped
    action can become ready.
    """
    root = root.resolve()
    requested_action = str(action or "").strip()
    requested_topic = str(topic_ref or "").strip()
    requested_work = str(work_id or "").strip()
    requested_targets = [str(value).strip() for value in (target_refs or []) if str(value).strip()]
    listing = list_research_topics(root, automation_paths=automation_paths)
    overview = overview_topics(listing)

    if not requested_action and not requested_topic:
        result = resolution_base("overview", action="overview")
        result.update({
            "topics": overview,
            "available_actions": sorted(RESEARCH_ACTIONS),
            "next_gate": "requirements_required" if not overview else "select a topic by exact ID or full title",
        })
        return result

    effective_action = requested_action or "inspect"
    if effective_action not in RESEARCH_ACTIONS:
        result = resolution_base("action_invalid", action=effective_action, topic_ref=requested_topic)
        result.update({"available_actions": sorted(RESEARCH_ACTIONS), "reason": "unknown research action; no topic or work was selected"})
        return result
    if effective_action == "overview":
        result = resolution_base("overview", action="overview", topic_ref=requested_topic)
        result.update({"topics": overview, "available_actions": sorted(RESEARCH_ACTIONS)})
        return result
    if effective_action == "create":
        result = resolution_base("requirements_required", action="create", topic_ref=requested_topic)
        result.update({
            "required": ["purpose", "scope", "focus", "evidence", "output", "classification", "cadence", "cost_limit"],
            "reason": "create is a planning entry only; it neither creates a topic nor authorizes research or scheduling",
        })
        return result
    if effective_action in TOPIC_REQUIRED_ACTIONS and not requested_topic:
        result = resolution_base("topic_required", action=effective_action)
        result.update({"topics": overview, "reason": "an explicit topic ID or full title is required; no topic was inferred"})
        return result

    topic_ids = list_topic_ids(root)
    selected_id = requested_topic if requested_topic in topic_ids else ""
    if not selected_id:
        matches = [
            str(row.get("topic_id") or "")
            for row in listing.get("topics", [])
            if isinstance(row, dict) and bool(row.get("registered")) and str(row.get("display_name") or "") == requested_topic
        ]
        if len(matches) > 1:
            result = resolution_base("topic_ambiguous", action=effective_action, topic_ref=requested_topic)
            result.update({"candidate_topic_ids": sorted(matches), "reason": "the full title is shared; select a stable topic ID"})
            return result
        if len(matches) == 1:
            selected_id = matches[0]
    if not selected_id:
        result = resolution_base("topic_unknown", action=effective_action, topic_ref=requested_topic)
        result.update({"available_topic_ids": [item["topic_id"] for item in overview], "reason": "topic references never use partial, recent, enabled, material, or semantic matching"})
        return result

    inspected = next((row for row in listing.get("topics", []) if isinstance(row, dict) and row.get("topic_id") == selected_id), None)
    if not isinstance(inspected, dict) or not inspected.get("registered"):
        result = resolution_base("topic_invalid", action=effective_action, topic_ref=requested_topic)
        result.update({"topic_id": selected_id, "warnings": inspected.get("warnings", []) if isinstance(inspected, dict) else ["topic could not be read"]})
        return result
    try:
        selected = topic_resolution_view(root, selected_id, inspected)
    except (ProgramError, ValueError) as exc:
        result = resolution_base("topic_invalid", action=effective_action, topic_ref=requested_topic)
        result.update({"topic_id": selected_id, "warnings": [str(exc)]})
        return result
    if selected["work"].get("state") == "unknown" or selected["scheduler"].get("state") == "unknown":
        result = resolution_base("topic_invalid", action=effective_action, topic_ref=requested_topic)
        result.update({"topic_id": selected_id, "warnings": ["topic runtime state is unreadable or does not match its declared contract"]})
        return result

    pending = selected.pop("pending_work")
    state = selected.pop("_state")
    config = selected.pop("_config")
    if effective_action == "run" and pending:
        result = resolution_base("resume_required", action=effective_action, topic_ref=requested_topic)
        result.update({"topic": selected, "pending_work": pending, "reason": "existing pending work must be resumed; no new run or budget reset is permitted"})
        return result
    if effective_action == "resume":
        if not pending:
            result = resolution_base("no_pending", action=effective_action, topic_ref=requested_topic)
            result.update({"topic": selected, "reason": "this topic has no pending work to resume"})
            return result
        if requested_work:
            matches = [item for item in pending if item["work_id"] == requested_work]
            if len(matches) != 1:
                result = resolution_base("work_invalid", action=effective_action, topic_ref=requested_topic)
                result.update({"topic": selected, "pending_work": pending, "reason": "work_id must be this topic's pending work; cross-topic, completed, and unknown work is rejected"})
                return result
            chosen = matches[0]
        elif len(pending) == 1:
            chosen = pending[0]
        else:
            result = resolution_base("work_required", action=effective_action, topic_ref=requested_topic)
            result.update({"topic": selected, "pending_work": pending, "reason": "more than one pending work item exists; select its stable work_id"})
            return result
        result = resolution_base("ready", action=effective_action, topic_ref=requested_topic)
        result.update({
            "topic": selected,
            "work": {**chosen, "run": run_summary(config, state, chosen["run_id"])},
            "next_gate": "a separately authorized runner resume may use only this recorded work/run",
        })
        return result

    if effective_action == "feedback":
        target_index = feedback_target_index(inspected)
        if not requested_targets:
            result = resolution_base("feedback_target_required", action=effective_action, topic_ref=requested_topic)
            result.update({
                "topic": selected,
                "available_feedback_target_ids": sorted(target_index),
                "reason": "feedback must name one or more existing source, application, or trial IDs for this topic",
            })
            return result
        resolved_targets: list[dict[str, str]] = []
        for target_ref in requested_targets:
            matches = target_index.get(target_ref, [])
            if not matches:
                result = resolution_base("feedback_target_unknown", action=effective_action, topic_ref=requested_topic)
                result.update({"topic": selected, "target_ref": target_ref, "reason": "feedback targets never use partial, cross-topic, title, or semantic matching"})
                return result
            if len(matches) != 1:
                result = resolution_base("feedback_target_ambiguous", action=effective_action, topic_ref=requested_topic)
                result.update({"topic": selected, "target_ref": target_ref, "candidates": matches, "reason": "feedback target identity is not unique in this topic"})
                return result
            if matches[0] not in resolved_targets:
                resolved_targets.append(matches[0])
        result = resolution_base("ready", action=effective_action, topic_ref=requested_topic)
        result.update({
            "topic": selected,
            "feedback_targets": resolved_targets,
            "next_gate": "a separately authorized feedback write may address only these resolved topic-owned targets",
        })
        return result

    result = resolution_base("ready", action=effective_action, topic_ref=requested_topic)
    result.update({
        "topic": selected,
        "pending_work": pending,
        "next_gate": "this read-only resolution does not authorize runner, topic, or scheduler changes",
    })
    return result


def search_research_materials(
    root: Path,
    query: str = "",
    *,
    topic_ids: Iterable[str] | None = None,
    include_applications: bool = True,
    automation_paths: Iterable[Path] | None = None,
) -> dict[str, Any]:
    selected = set(topic_ids or [])
    topic_rows = [
        inspect_research_topic(root, topic_id, automation_paths=automation_paths)
        for topic_id in list_topic_ids(root)
        if not selected or topic_id in selected
    ]
    tokens = query_terms(query)
    materials: list[dict[str, Any]] = []
    for topic in topic_rows:
        profile_matches = topic_profile_matches(topic, tokens)
        for source in topic.get("materials", []):
            source_copy = {key: value for key, value in source.items() if key != "applications"}
            source_copy["topic_ids"] = sorted(set(source_copy.get("topic_ids", [])))
            blob = json.dumps(source_copy, ensure_ascii=False).casefold()
            applications = source.get("applications", []) if include_applications else []
            for app in applications:
                blob += " " + json.dumps(app, ensure_ascii=False).casefold()
            material_matches = match_terms(blob, tokens)
            if tokens and not material_matches and not profile_matches:
                continue
            materials.append(source_copy)
            if include_applications:
                for app in applications:
                    materials.append({
                        **{key: value for key, value in source_copy.items() if key not in {"material_kind", "latest_receipt"}},
                        **app,
                        "topic_ids": sorted(set(source_copy.get("topic_ids", []))),
                        "classifications": source_copy["classifications"],
                        "outcome_page_path": source_copy["outcome_page_path"],
                    })
    return {
        "schema_version": CATALOG_SCHEMA,
        "generated_at": now_iso(),
        "query": query,
        "query_mode": "mapping" if not tokens else "material_or_topic_profile_mapping",
        "materials": materials,
        "truth_boundary": "One source row remains one source identity; application rows are derivations, not additional sources.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list"); listing.add_argument("--json", action="store_true")
    inspect = sub.add_parser("inspect"); inspect.add_argument("--topic-id", required=True); inspect.add_argument("--json", action="store_true")
    search = sub.add_parser("search"); search.add_argument("--query", default=""); search.add_argument("--topic-id", action="append"); search.add_argument("--include-applications", action="store_true"); search.add_argument("--json", action="store_true")
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--action")
    resolve.add_argument("--topic-ref")
    resolve.add_argument("--work-id")
    resolve.add_argument("--target-ref", action="append")
    args = parser.parse_args()
    if args.command == "list":
        result = list_research_topics(args.root)
    elif args.command == "inspect":
        result = inspect_research_topic(args.root, args.topic_id)
    elif args.command == "resolve":
        result = resolve_research_action(
            args.root,
            action=args.action,
            topic_ref=args.topic_ref,
            work_id=args.work_id,
            target_refs=args.target_ref,
        )
    else:
        result = search_research_materials(args.root, args.query, topic_ids=args.topic_id, include_applications=args.include_applications)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
