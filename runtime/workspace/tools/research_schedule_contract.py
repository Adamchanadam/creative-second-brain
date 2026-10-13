"""Read-only checks for one bounded scheduled-research contract."""
from __future__ import annotations

import os
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_AUTOMATION_PATHS = (Path.home() / ".codex" / "automations" / "automation" / "automation.toml",)
WINDOWS_TIMEZONE_IDS = {"Europe/London": frozenset({"GMT Standard Time"})}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def parse_rrule(value: object) -> dict[str, str]:
    result: dict[str, str] = {}
    for segment in str(value or "").split(";"):
        if "=" in segment:
            key, item = segment.split("=", 1)
            result[key] = item
    return result


def scheduler_status(root: Path, config: dict[str, Any], automation_paths: Iterable[Path] | None = None) -> dict[str, Any]:
    """Return configuration evidence only; it never claims an observed trigger."""
    checked_at = now_iso()
    if str(config.get("mode") or "scheduled_research") != "scheduled_research":
        return {"state": "not_configured", "active": False, "checked_at": checked_at, "reason": "topic mode is manual_research"}
    schedule = config.get("schedule_contract") if isinstance(config.get("schedule_contract"), dict) else {}
    name = str(schedule.get("automation_name") or "").strip()
    if not name:
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "topic has no scheduler identity"}
    existing = [path for path in list(automation_paths or DEFAULT_AUTOMATION_PATHS) if path.is_file()]
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
    try:
        hour, minute = (int(value) for value in str(schedule.get("time") or "").split(":", 1))
    except (TypeError, ValueError):
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "topic schedule time is invalid"}
    rrule = parse_rrule(record.get("rrule"))
    actual_cwds = [str(Path(value).resolve()).casefold() for value in record.get("cwds", []) if isinstance(value, str)]
    aligned = (
        str(record.get("model") or "") == str(schedule.get("model") or "")
        and str(record.get("reasoning_effort") or "") == str(schedule.get("reasoning_effort") or "")
        and actual_cwds == [str(root.resolve()).casefold()]
        and str(record.get("kind") or "") == "cron"
        and str(schedule.get("frequency") or "") == "daily"
        and rrule.get("FREQ") == "DAILY" and rrule.get("BYHOUR") == str(hour) and rrule.get("BYMINUTE") == str(minute)
        and rrule.get("BYSECOND") in {None, "0"} and str(record.get("execution_environment") or "") == "local"
    )
    if not aligned:
        return {"state": "unknown", "active": False, "checked_at": checked_at, "reason": "automation exists but does not match this topic contract"}
    status = str(record.get("status") or "")
    base = {"checked_at": checked_at, "automation_id": str(record.get("id") or ""), "declared_executor": {"model": str(record.get("model") or ""), "reasoning_effort": str(record.get("reasoning_effort") or "")}}
    if status == "ACTIVE":
        return {"state": "active", "active": True, "reason": "matching automation is active", **base}
    if status == "PAUSED":
        return {"state": "paused", "active": False, "reason": "matching automation is paused", **base}
    return {"state": "unknown", "active": False, "reason": "matching automation has no usable status", **base}


def detect_host_timezone_id() -> str | None:
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(["tzutil", "/g"], check=False, capture_output=True, text=True, encoding="utf-8", timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def host_timezone_status(expected_timezone: str, *, host_timezone_id: str | None = None) -> dict[str, Any]:
    observed = str(host_timezone_id or detect_host_timezone_id() or "").strip()
    expected = WINDOWS_TIMEZONE_IDS.get(expected_timezone)
    if not expected:
        return {"state": "unknown", "expected_timezone": expected_timezone, "observed_timezone_id": observed, "reason": "no DST-aware host timezone mapping is configured"}
    if not observed:
        return {"state": "unknown", "expected_timezone": expected_timezone, "observed_timezone_id": "", "reason": "host timezone could not be read"}
    if observed in expected:
        return {"state": "aligned", "expected_timezone": expected_timezone, "observed_timezone_id": observed, "reason": "host timezone uses the required DST-aware mapping"}
    return {"state": "mismatch", "expected_timezone": expected_timezone, "observed_timezone_id": observed, "reason": "host timezone does not provide the required DST-aware schedule semantics"}
