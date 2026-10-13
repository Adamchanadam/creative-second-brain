"""Compatibility orchestration ledger for single-source ingest jobs.

This module is intentionally not a second source-ingest pipeline.  It records
Terminal/worker job identity, timing, resume and dispatch state; actual source
capture, review, image generation, vault writing and readback remain owned by
the existing firecrawl/Obsidian pipeline and adapter.

Compatibility boundary: controlled existing single-source work may still use
this ledger, but new compiler schema and
orchestration design route to
docs/Compiler Worker與Knowledge Package設計及驗收規格.md. Results produced here
do not prove that Compiler Worker v2 has reached production cutover.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Mapping
from types import SimpleNamespace
from urllib.parse import urldefrag, urlsplit, urlunsplit

try:
    from tools.image_source_contract import ImageSourceContractError, inspect_image
except ModuleNotFoundError:
    from image_source_contract import ImageSourceContractError, inspect_image


SCHEMA_VERSION = "source_ingest_job_v3"
WORKER_ENVELOPE_VERSION = "source_ingest_worker_envelope_v3"
DEFAULT_JOB_ROOT = Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")) / "_registry" / "source-ingest-jobs"
PIPELINE = Path(__file__).with_name("firecrawl_obsidian_pipeline.py")
JOB_TARGET_SECONDS = 600
# Ten minutes is the product SLA target, not a completion kill-switch: a clean
# late result must still be delivered with durable overtime evidence.
EXTERNAL_WAIT_GRACE_SECONDS = 0
JOB_DEADLINE_SECONDS = JOB_TARGET_SECONDS
ENFORCE_HARD_DEADLINE = False
DISPATCH_SLA_SECONDS = 60
LOCK_RETRY_COUNT = 100
LOCK_RETRY_SLEEP_SECONDS = 0.02
STAGES = ("capture", "source_analysis", "infographic", "writer_readback")
JOB_STATUSES = {"accepted", "queued", "running", "needs_review", "completed", "blocked", "failed"}
ACTIVE_STATUSES = {"accepted", "queued", "running", "needs_review"}
TERMINAL_STATUSES = {"completed", "blocked", "failed"}
RETENTION_MODES = {"summarized_allowed", "full_body_required", "original_image_required"}
WORKER_RUNTIMES = {"codex_app", "claude_code", "external_worker"}
SLA_RESULTS = {"pending", "met", "imagegen_grace", "missed"}
CONTENT_RESULTS = {"pending", "verified"}

ENVELOPE_FIELDS = {
    "tool",
    "contract_version",
    "project_root",
    "job_root",
    "job_id",
    "source_identity",
    "source_input_kind",
    "source_retention_mode",
    "worker_runtime",
    "worker_claim",
    "worker_task",
    "runbook_path",
    "first_accepted_at",
    "target_at",
    "deadline_at",
    "dispatch_request_path",
    "source_url",
    "user_text_sha256",
    "user_image_sha256",
    "staged_input_path",
    "payload_sha256",
}

SECRET_PATTERN = re.compile(
    r"(?:sk-(?:proj-)?|github_pat_|gh[opsubr]_|xox[abprs]-|AIza|AKIA|ya29\.|1//|ntn_|secret_)[A-Za-z0-9_./+=-]{8,}",
    re.IGNORECASE,
)


class SourceIngestJobError(ValueError):
    """The source-ingest job request is stale, unsafe, or unverifiable."""


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept, resume, dispatch and verify one source-ingest background job.")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root.")
    parser.add_argument("--job-root", type=Path, default=DEFAULT_JOB_ROOT, help="Durable job ledger root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    accept = subparsers.add_parser("accept", help="Accept one Terminal source-ingest request and return a durable job_id.")
    source = accept.add_mutually_exclusive_group(required=True)
    source.add_argument("--terminal-text", help='AI Terminal text such as "全文入庫: https://example.com/article".')
    source.add_argument("--url")
    source.add_argument("--user-text-file", type=Path)
    source.add_argument("--user-image-file", type=Path)
    accept.add_argument("--intent-text", default="", help="Original user intent text when --url or --user-text-file is used.")
    accept.add_argument(
        "--source-retention-mode",
        choices=("auto", "summarized_allowed", "full_body_required", "original_image_required"),
        default="auto",
    )
    accept.add_argument(
        "--worker-runtime",
        choices=sorted(WORKER_RUNTIMES),
        default="codex_app",
        help="Background-worker adapter. All adapters consume the same formal source-ingest envelope.",
    )
    accept.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    accept.add_argument("--json", action="store_true")

    dispatch = subparsers.add_parser("dispatch", help="Create a runtime-neutral worker envelope and dispatch request for an accepted job.")
    dispatch.add_argument("--job-id", required=True)
    dispatch.add_argument("--worker-envelope-out", type=Path)
    dispatch.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    dispatch.add_argument("--json", action="store_true")

    bind = subparsers.add_parser("bind-worker", help="Record the actual background runtime task that accepted one dispatch request.")
    bind.add_argument("--job-id", required=True)
    bind.add_argument("--worker-instance-id", required=True, help="Opaque Codex thread, Claude Code run, or equivalent runtime id.")
    bind.add_argument("--dispatch-evidence", required=True, help="Concrete runtime dispatch/readback evidence; never a credential.")
    bind.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    bind.add_argument("--json", action="store_true")

    worker = subparsers.add_parser("worker-readback", help="Validate a background-worker envelope without running ingest.")
    worker.add_argument("--worker-envelope", type=Path, required=True)
    worker.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    worker.add_argument("--json", action="store_true")

    worker_capture = subparsers.add_parser("worker-capture", help="Run the formal prepare/capture stage for one dispatched worker envelope.")
    worker_capture.add_argument("--worker-envelope", type=Path, required=True)
    worker_capture.add_argument("--out-root", type=Path, default=Path("outputs"))
    worker_capture.add_argument("--source-id", help="Optional source id for a controlled migration or test; normal jobs use the formal allocator.")
    worker_capture.add_argument("--capture-provider", choices=("auto", "firecrawl", "tavily", "browser-file"), default="auto")
    worker_capture.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    worker_capture.add_argument("--json", action="store_true")

    worker_analysis = subparsers.add_parser("worker-analysis-complete", help="Record a completed single AI analysis after validating the formal reviewed slice.")
    worker_analysis.add_argument("--worker-envelope", type=Path, required=True)
    worker_analysis.add_argument("--source-slice", type=Path, required=True)
    worker_analysis.add_argument("--duration-seconds", type=float, required=True, help="Worker monotonic elapsed time for the one analysis call.")
    worker_analysis.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    worker_analysis.add_argument("--json", action="store_true")

    status = subparsers.add_parser("status", help="Read one job and report its durable content and SLA state.")
    status.add_argument("--job-id", required=True)
    status.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    status.add_argument("--json", action="store_true")

    resume = subparsers.add_parser("resume", help="Resume one durable job identity without creating a duplicate.")
    resume.add_argument("--job-id", required=True)
    resume.add_argument(
        "--adopt-current-sla",
        action="store_true",
        help="Explicitly migrate a legacy blocked/failed job to the current 600-second non-blocking SLA without resetting first acceptance.",
    )
    resume.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    resume.add_argument("--json", action="store_true")

    stage = subparsers.add_parser("stage", help="Record one stage timing transition for a background worker.")
    stage.add_argument("--job-id", required=True)
    stage.add_argument("--stage", choices=STAGES, required=True)
    stage.add_argument("--stage-status", choices=("running", "completed", "needs_review", "blocked", "failed"), required=True)
    stage.add_argument("--worker-claim", required=True)
    stage.add_argument("--expected-work-version", type=int, required=True)
    stage.add_argument("--duration-seconds", type=float)
    stage.add_argument("--reason", default="")
    stage.add_argument("--retry-action", default="")
    stage.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    stage.add_argument("--json", action="store_true")

    complete = subparsers.add_parser("complete", help="Complete a job from an existing pipeline completion/readback report.")
    complete.add_argument("--job-id", required=True)
    complete.add_argument("--completion-report-file", type=Path, required=True)
    complete.add_argument("--worker-claim", required=True)
    complete.add_argument("--expected-work-version", type=int, required=True)
    complete.add_argument("--outcome-url", default="")
    complete.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    complete.add_argument("--json", action="store_true")

    external_wait = subparsers.add_parser(
        "external-wait",
        help="Legacy timing record. New 600-second SLA jobs do not permit external-wait grace.",
    )
    external_wait.add_argument("--job-id", required=True)
    external_wait.add_argument("--stage", choices=STAGES, required=True)
    external_wait.add_argument("--duration-seconds", type=float, required=True)
    external_wait.add_argument("--reason", required=True)
    external_wait.add_argument("--evidence", required=True)
    external_wait.add_argument("--worker-claim", required=True)
    external_wait.add_argument("--expected-work-version", type=int, required=True)
    external_wait.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    external_wait.add_argument("--json", action="store_true")

    run_one = subparsers.add_parser(
        "run-one-source-ingest",
        help="Run one reviewed source packet through the gpt-image-2 action/resume seam, source-local write/readback and incremental outcome.",
    )
    run_one.add_argument("--worker-envelope", type=Path, required=True)
    run_one.add_argument("--source-slice", type=Path, required=True)
    run_one.add_argument("--imagegen-response-file", type=Path, help="Actual gpt-image-2 response JSON for the locked action envelope.")
    run_one.add_argument("--blocking-repair-failure", choices=(
        "wrong_source_or_subject", "wrong_or_missing_locked_text", "semantic_contradiction",
        "internal_process_pollution", "cropped_content", "missing_footer",
    ), help="Explicitly request the sole failed-page gpt-image-2 repair; normal runs must omit this.")
    run_one.add_argument("--completion-report-out", type=Path, required=True)
    run_one.add_argument("--vault", type=Path, default=Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")))
    run_one.add_argument("--max-kp", type=int, default=6)
    run_one.add_argument("--now", help="UTC ISO timestamp for deterministic tests.")
    run_one.add_argument("--json", action="store_true")

    args = parser.parse_args()
    root = args.root.resolve()
    job_root = resolve_inside(root, args.job_root)

    try:
        if args.command == "accept":
            payload = accept_job(root, job_root, args)
        elif args.command == "dispatch":
            payload = dispatch_job(root, job_root, args)
        elif args.command == "bind-worker":
            payload = bind_worker(root, job_root, args)
        elif args.command == "worker-readback":
            payload = worker_readback(root, job_root, args)
        elif args.command == "worker-capture":
            payload = worker_capture_source(root, job_root, args)
        elif args.command == "worker-analysis-complete":
            payload = worker_analysis_complete(root, job_root, args)
        elif args.command == "status":
            payload = status_job(root, job_root, args.job_id, parse_now(args.now), mutate_deadline=True)
        elif args.command == "resume":
            payload = resume_job(
                root,
                job_root,
                args.job_id,
                parse_now(args.now),
                adopt_current_sla=bool(args.adopt_current_sla),
            )
        elif args.command == "stage":
            payload = record_stage(root, job_root, args)
        elif args.command == "complete":
            payload = complete_job(root, job_root, args)
        elif args.command == "external-wait":
            payload = record_external_wait(root, job_root, args)
        elif args.command == "run-one-source-ingest":
            payload = run_one_source_ingest(root, job_root, args)
        else:  # pragma: no cover - argparse prevents this.
            raise AssertionError(args.command)
    except SourceIngestJobError as exc:
        payload = {"status": "failed", "message": str(exc), "write_performed": False}
        print_json_or_text(payload, getattr(args, "json", False))
        return 1
    print_json_or_text(payload, getattr(args, "json", False))
    if payload.get("duplicate_job") is True and payload.get("write_performed") is False:
        return 0
    return 0 if payload.get("status") not in {"failed", "blocked"} else 1


def accept_job(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    now = parse_now(args.now)
    source = source_from_accept_args(args)
    source_identity = source["source_identity"]
    job_id = deterministic_job_id(source_identity)
    with ledger_lock(job_root):
        existing = next((job for job in latest_jobs(job_root).values() if job.get("source_identity") == source_identity), None)
        if existing:
            return {
                "status": str(existing["status"]),
                "message": "existing source-ingest job returned",
                "job": public_job(existing),
                "job_id": existing["job_id"],
                "source_identity": source_identity,
                "dispatch_sla_seconds": DISPATCH_SLA_SECONDS,
                "write_performed": False,
                "duplicate_job": True,
            }
        staged_input_path = ""
        if source["source_input_kind"] in {"user_provided_text", "user_provided_image"}:
            staged_input_path = stage_user_input_bytes(
                root=root,
                job_root=job_root,
                job_id=job_id,
                raw=source["_raw_bytes"],
                digest=source["user_text_sha256"] if source["source_input_kind"] == "user_provided_text" else source["user_image_sha256"],
                filename=source["_staged_filename"],
            )
        target = now + timedelta(seconds=JOB_TARGET_SECONDS)
        deadline = now + timedelta(seconds=JOB_DEADLINE_SECONDS)
        job = {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "source_identity": source_identity,
            "source_input_kind": source["source_input_kind"],
            "source_url": source.get("source_url", ""),
            "user_text_sha256": source.get("user_text_sha256", ""),
            "user_image_sha256": source.get("user_image_sha256", ""),
            "staged_input_path": staged_input_path,
            "source_retention_mode": retention_mode(args.source_retention_mode, source["intent_text"], source["source_input_kind"]),
            "worker_runtime": args.worker_runtime,
            "status": "accepted",
            "first_accepted_at": isoformat(now),
            "attempt_accepted_at": isoformat(now),
            "accepted_at": isoformat(now),
            "target_at": isoformat(target),
            "deadline_at": isoformat(deadline),
            "sla_target_seconds": JOB_TARGET_SECONDS,
            "enforce_hard_deadline": ENFORCE_HARD_DEADLINE,
            "sla_result": "pending",
            "sla_reason": "",
            "overtime_evidence": {},
            "content_result": "pending",
            "updated_at": isoformat(now),
            "current_stage": "accepted",
            "reason": "",
            "retry_action": "dispatch the selected background worker and resume this job",
            "outcome_url": "",
            "stage_timings": empty_stage_timings(),
            "external_wait_seconds": 0.0,
            "external_wait_evidence": [],
            "checkpoints": {},
            "worker_envelope_path": "",
            "dispatch_request_path": "",
            "worker_claim": "",
            "worker_instance_id": "",
            "worker_dispatch_evidence": "",
            "recovery_mode": False,
            "lease_expires_at": "",
            "attempt_number": 1,
        }
        stored = append_event_locked(job_root, "accepted", job, now, create_only=True)
    return {
        "status": "accepted",
        "message": "source-ingest job accepted; no background worker has been started; dispatch and bind an actual runtime worker before reporting background execution",
        "job": public_job(stored),
        "job_id": stored["job_id"],
        "source_identity": source_identity,
        "dispatch_sla_seconds": DISPATCH_SLA_SECONDS,
        "target_seconds": JOB_TARGET_SECONDS,
        "external_wait_grace_seconds": EXTERNAL_WAIT_GRACE_SECONDS,
        "deadline_seconds": JOB_DEADLINE_SECONDS,
        "write_performed": True,
        "duplicate_job": False,
    }


def dispatch_job(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    now = parse_now(args.now)
    with ledger_lock(job_root):
        job = require_job(job_root, args.job_id)
        if deadline_expired(job, now):
            failed = append_event_locked(job_root, "deadline_failed", deadline_failure_job(job, now), now)
            return {"status": failed["status"], "job": public_job(failed), "write_performed": True}
        if job["status"] == "queued":
            return {
                "status": "queued",
                "job_id": job["job_id"],
                "source_identity": job["source_identity"],
                "worker_envelope": str(resolve_inside(root, Path(str(job.get("worker_envelope_path", ""))))),
                "dispatch_request": str(resolve_inside(root, Path(str(job.get("dispatch_request_path", ""))))) if job.get("dispatch_request_path") else "",
                "worker_runtime": job.get("worker_runtime", "codex_app"),
                "worker_claim": job.get("worker_claim", ""),
                "job": public_job(job),
                "write_performed": False,
                "resume_existing_claim": True,
            }
        if job["status"] in {"running", "needs_review"}:
            return {"status": job["status"], "job": public_job(job), "write_performed": False, "resume_existing_claim": True}
        if job["status"] != "accepted":
            return {"status": job["status"], "job": public_job(job), "write_performed": False}
        out = args.worker_envelope_out or job_root / "worker-envelopes" / f"{job['job_id']}.json"
        envelope_path = resolve_inside(root, out)
        dispatch_path = resolve_inside(root, job_root / "worker-dispatches" / f"{job['job_id']}.json")
        queued = dict(job)
        queued["status"] = "queued"
        queued["current_stage"] = "queued_for_background_worker"
        queued["updated_at"] = isoformat(now)
        queued["worker_envelope_path"] = str(envelope_path.relative_to(root))
        queued["dispatch_request_path"] = str(dispatch_path.relative_to(root))
        queued["worker_claim"] = f"sij_claim_{uuid.uuid4().hex}"
        queued["lease_expires_at"] = queued["deadline_at"]
        queued["retry_action"] = f"dispatch the {queued['worker_runtime']} worker request and bind its actual runtime task id"
        envelope = build_worker_envelope(root, job_root, queued)
        envelope_path.parent.mkdir(parents=True, exist_ok=True)
        envelope_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        dispatch_request = build_worker_dispatch_request(root, queued, envelope_path)
        dispatch_path.parent.mkdir(parents=True, exist_ok=True)
        dispatch_path.write_text(json.dumps(dispatch_request, ensure_ascii=False, indent=2), encoding="utf-8")
        stored = append_event_locked(job_root, "queued", queued, now)
    return {
        "status": "queued",
        "job_id": stored["job_id"],
        "source_identity": stored["source_identity"],
        "worker_envelope": str(envelope_path),
        "dispatch_request": str(dispatch_path),
        "worker_runtime": stored["worker_runtime"],
        "worker_claim": stored["worker_claim"],
        "worker_task": envelope["worker_task"],
        "job": public_job(stored),
        "write_performed": True,
    }


def bind_worker(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Bind orchestration to a real background runtime without creating a second worker state."""

    now = parse_now(args.now)
    instance_id = str(args.worker_instance_id).strip()
    evidence = str(args.dispatch_evidence).strip()
    if not instance_id or len(evidence) < 8:
        raise SourceIngestJobError("worker binding requires an opaque runtime id and concrete dispatch evidence")
    with ledger_lock(job_root):
        job = require_job(job_root, args.job_id)
        if job["status"] not in {"queued", "running", "needs_review"}:
            raise SourceIngestJobError("only a queued or active job can bind a background worker")
        if job.get("worker_instance_id"):
            if job["worker_instance_id"] != instance_id:
                raise SourceIngestJobError("job is already bound to a different background worker")
            return {"status": job["status"], "job": public_job(job), "write_performed": False, "idempotent_binding": True}
        bound = dict(job)
        bound["worker_instance_id"] = instance_id
        bound["worker_dispatch_evidence"] = evidence
        bound["updated_at"] = isoformat(now)
        bound["retry_action"] = "background worker owns the next source-ingest stage"
        stored = append_event_locked(job_root, "worker_bound", bound, now)
    return {
        "status": stored["status"],
        "job_id": stored["job_id"],
        "worker_runtime": stored["worker_runtime"],
        "worker_instance_id": stored["worker_instance_id"],
        "job": public_job(stored),
        "write_performed": True,
    }


def worker_readback(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    envelope_path = resolve_inside(root, args.worker_envelope)
    envelope = json.loads(envelope_path.read_text(encoding="utf-8-sig"))
    parsed = validate_worker_envelope(envelope, expected_root=root)
    if path_key(resolve_inside(root, Path(str(parsed["job_root"])))) != path_key(job_root):
        raise SourceIngestJobError("worker envelope job_root does not match this command")
    job = require_job(job_root, parsed["job_id"])
    if job["source_identity"] != parsed["source_identity"]:
        raise SourceIngestJobError("worker envelope source_identity does not match the durable job")
    if job.get("worker_claim") != parsed.get("worker_claim"):
        raise SourceIngestJobError("worker envelope claim does not match the durable job")
    job, _ = apply_deadline_if_needed(job_root, job, parse_now(args.now), mutate=False)
    staged_readback: dict[str, object] = {"required": False}
    if job["source_input_kind"] in {"user_provided_text", "user_provided_image"}:
        staged_path = resolve_inside(root, Path(str(job.get("staged_input_path", ""))))
        if str(parsed.get("staged_input_path", "")) != str(job.get("staged_input_path", "")):
            raise SourceIngestJobError("worker envelope staged input path does not match the durable job")
        staged_hash = hashlib.sha256(staged_path.read_bytes()).hexdigest()
        expected_hash_key = "user_text_sha256" if job["source_input_kind"] == "user_provided_text" else "user_image_sha256"
        if staged_hash != job.get(expected_hash_key):
            raise SourceIngestJobError("staged source input hash does not match the durable job")
        staged_readback = {
            "required": True,
            "source_input_kind": job["source_input_kind"],
            "staged_input_path": job["staged_input_path"],
            "sha256": staged_hash,
        }
    return {
        "status": "ready",
        "worker_task": parsed["worker_task"],
        "runbook_path": parsed["runbook_path"],
        "job_id": job["job_id"],
        "source_identity": job["source_identity"],
        "source_input_kind": job["source_input_kind"],
        "source_retention_mode": job["source_retention_mode"],
        "worker_runtime": job.get("worker_runtime", "codex_app"),
        "source_url": job.get("source_url", ""),
        "staged_input_path": job.get("staged_input_path", ""),
        "job_status": job["status"],
        "worker_claim": job.get("worker_claim", ""),
        "work_version": job.get("work_version"),
        "previous_event_id": job.get("previous_event_id", ""),
        "current_event_id": job.get("current_event_id", ""),
        "first_accepted_at": job["first_accepted_at"],
        "target_at": job["target_at"],
        "deadline_at": job["deadline_at"],
        "content_result": job.get("content_result", "pending"),
        "sla_result": job.get("sla_result", "pending"),
        "sla_reason": job.get("sla_reason", ""),
        "staged_input_readback": staged_readback,
        "write_performed": False,
        "envelope_readback_ready": True,
        "background_dispatch_bound": bool(job.get("worker_instance_id")),
        "worker_instance_id": job.get("worker_instance_id", ""),
    }


def worker_capture_source(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Run the real formal prepare command and record its capture wall span.

    This is intentionally the first worker operation, so a later reviewed-slice
    runner cannot pretend the preceding capture was instantaneous.
    """

    started = time.monotonic()
    readback = worker_readback(root, job_root, SimpleNamespace(worker_envelope=args.worker_envelope, now=args.now))
    job_id = str(readback["job_id"])
    claim = str(readback["worker_claim"])
    version = int(readback["work_version"])
    job = require_job(job_root, job_id)
    timing = nested_get(job, ("stage_timings", "capture"))
    if isinstance(timing, Mapping) and timing.get("status") == "completed":
        raise SourceIngestJobError("capture is already completed; resume from the recorded prepare packet")
    running = record_stage(root, job_root, SimpleNamespace(
        job_id=job_id, stage="capture", stage_status="running", worker_claim=claim,
        expected_work_version=version, duration_seconds=None, reason="", retry_action="", now=worker_now(args.now, started),
    ))
    version = int(running["job"]["work_version"])
    command = prepare_command_for_worker(root, readback, args)
    try:
        prepared = run_pipeline_json(root, command)
    except (SourceIngestJobError, OSError, ValueError, subprocess.SubprocessError) as exc:
        blocked = record_stage(root, job_root, SimpleNamespace(
            job_id=job_id, stage="capture", stage_status="blocked", worker_claim=claim,
            expected_work_version=version, duration_seconds=time.monotonic() - started, reason=str(exc),
            retry_action="inspect the formal capture response, then explicitly resume this same job", now=worker_now(args.now, started),
        ))
        return {"status": "blocked", "job_id": job_id, "job": public_job(blocked["job"]), "write_performed": True}
    source_slice_value = str(prepared.get("source_slice") or "")
    if not source_slice_value:
        raise SourceIngestJobError("formal prepare did not return a source slice")
    source_slice = resolve_inside(root, Path(source_slice_value))
    completed = record_stage(root, job_root, SimpleNamespace(
        job_id=job_id, stage="capture", stage_status="completed", worker_claim=claim,
        expected_work_version=version, duration_seconds=time.monotonic() - started, reason="", retry_action="", now=worker_now(args.now, started),
    ))
    version = int(completed["job"]["work_version"])
    checkpoint_paths = [
        source_slice,
        source_slice.parent / "source_capture_metadata.json",
        source_slice.parent / "review_packet.json",
    ]
    checkpoint = record_checkpoint(
        root, job_root, job_id, claim, version, "capture", checkpoint_paths,
        now=parse_now(worker_now(args.now, started)), reused=False,
    )
    return {
        "status": "needs_review",
        "job_id": job_id,
        "job": public_job(checkpoint),
        "prepare": prepared,
        "source_slice": str(source_slice.relative_to(root)),
        "next_action": "perform one complete AI analysis in the formal review packet/source slice, then call worker-analysis-complete",
        "write_performed": True,
    }


def worker_analysis_complete(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Bind one worker's measured analysis to the reviewed packet before imagegen."""

    started = time.monotonic()
    readback = worker_readback(root, job_root, SimpleNamespace(worker_envelope=args.worker_envelope, now=args.now))
    job_id = str(readback["job_id"])
    claim = str(readback["worker_claim"])
    version = int(readback["work_version"])
    source_slice = resolve_inside(root, args.source_slice)
    running = record_stage(root, job_root, SimpleNamespace(
        job_id=job_id, stage="source_analysis", stage_status="running", worker_claim=claim,
        expected_work_version=version, duration_seconds=None, reason="", retry_action="", now=worker_now(args.now, started),
    ))
    version = int(running["job"]["work_version"])
    try:
        packet = validate_reviewed_packet(root, source_slice, readback)
        analysis = validate_reviewed_slice(source_slice)
    except (SourceIngestJobError, OSError, ValueError) as exc:
        blocked = record_stage(root, job_root, SimpleNamespace(
            job_id=job_id, stage="source_analysis", stage_status="blocked", worker_claim=claim,
            expected_work_version=version, duration_seconds=validate_stage_duration(args.duration_seconds), reason=str(exc),
            retry_action="repair only the formal analysis packet, then explicitly resume this same job", now=worker_now(args.now, started),
        ))
        return {"status": "blocked", "job_id": job_id, "job": public_job(blocked["job"]), "write_performed": True}
    completed = record_stage(root, job_root, SimpleNamespace(
        job_id=job_id, stage="source_analysis", stage_status="completed", worker_claim=claim,
        expected_work_version=version, duration_seconds=validate_stage_duration(args.duration_seconds), reason="", retry_action="", now=worker_now(args.now, started),
    ))
    version = int(completed["job"]["work_version"])
    checkpoint = record_checkpoint(
        root, job_root, job_id, claim, version, "source_analysis", [source_slice],
        now=parse_now(worker_now(args.now, started)), reused=False,
    )
    return {
        "status": "ready",
        "job_id": job_id,
        "job": public_job(checkpoint),
        "packet": packet,
        "analysis": analysis,
        "source_slice": str(source_slice.relative_to(root)),
        "next_action": "run run-one-source-ingest to preflight the locked gpt-image-2 action",
        "write_performed": True,
    }


def prepare_command_for_worker(root: Path, readback: Mapping[str, Any], args: argparse.Namespace) -> list[str]:
    command = [sys.executable, str(PIPELINE), "--root", str(root), "prepare"]
    source_kind = str(readback["source_input_kind"])
    if source_kind == "url":
        command.extend(["--url", str(readback["source_url"]), "--capture-provider", str(args.capture_provider)])
    elif source_kind == "user_provided_text":
        command.extend(["--user-text-file", str(readback["staged_input_path"]), "--source-type", "使用者提供文字"])
    elif source_kind == "user_provided_image":
        command.extend(["--user-image-file", str(readback["staged_input_path"]), "--source-type", "使用者提供圖片"])
    else:  # pragma: no cover - envelope validation makes this unreachable.
        raise SourceIngestJobError("unsupported worker source input kind")
    if args.source_id:
        command.extend(["--source-id", str(args.source_id)])
    command.extend([
        "--source-retention-mode", str(readback["source_retention_mode"]),
        "--out-root", str(resolve_inside(root, args.out_root).relative_to(root)),
        "--json",
    ])
    return command


def worker_now(fixed_now: str | None, started: float) -> str:
    if fixed_now:
        return isoformat(parse_now(fixed_now) + timedelta(seconds=time.monotonic() - started))
    return isoformat(datetime.now(timezone.utc))


def status_job(root: Path, job_root: Path, job_id: str, now: datetime, *, mutate_deadline: bool) -> dict[str, Any]:
    job = require_job(job_root, job_id)
    job, deadline_event = apply_deadline_if_needed(job_root, job, now, mutate=mutate_deadline)
    return {
        "status": job["status"],
        "job_id": job["job_id"],
        "source_identity": job["source_identity"],
        "job": public_job(job),
        "write_performed": bool(deadline_event),
    }


def resume_job(
    root: Path,
    job_root: Path,
    job_id: str,
    now: datetime,
    *,
    adopt_current_sla: bool = False,
) -> dict[str, Any]:
    with ledger_lock(job_root):
        job = require_job(job_root, job_id)
        write_performed = False
        if adopt_current_sla:
            adopted = adopt_current_sla_policy(job, now)
            job = append_event_locked(
                job_root,
                "sla_policy_adopted",
                adopted,
                now,
                expected_work_version=int(job.get("work_version", 0)),
            )
            write_performed = True
        if deadline_expired(job, now):
            job = append_event_locked(job_root, "deadline_failed", deadline_failure_job(job, now), now)
            write_performed = True
        if job["status"] in {"blocked", "failed"}:
            resumed = resume_attempt_job(job, now)
            job = append_event_locked(
                job_root,
                "attempt_resumed",
                resumed,
                now,
                expected_work_version=int(job.get("work_version", 0)),
            )
            return {
                "status": job["status"],
                "job_id": job["job_id"],
                "source_identity": job["source_identity"],
                "job": public_job(job),
                "next_action": f"dispatch the {job.get('worker_runtime', 'codex_app')} worker request",
                "write_performed": True,
                "resumed_attempt": True,
            }
        public = public_job(job)
    next_action = {
        "accepted": f"dispatch the {public.get('worker_runtime', 'codex_app')} worker request",
        "queued": "bind the queued envelope to its actual background worker task",
        "running": f"continue current stage: {public['current_stage']}",
        "needs_review": public.get("retry_action") or "perform required review action",
        "completed": "open outcome URL",
        "blocked": public.get("retry_action") or "resolve blocker and re-dispatch",
        "failed": public.get("retry_action") or "inspect failure and resume this job",
    }.get(str(public["status"]), "inspect job")
    return {
        "status": public["status"],
        "job_id": public["job_id"],
        "source_identity": public["source_identity"],
        "job": public,
        "next_action": next_action,
        "write_performed": write_performed,
    }


def adopt_current_sla_policy(job: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Explicitly migrate a recoverable legacy job without concealing its history.

    This is intentionally not part of ordinary duplicate accept or resume.  The
    operator has to request it for a blocked/failed job created under the old
    hard deadline, and the old timing facts stay in the immutable event chain.
    """
    if job.get("status") not in {"blocked", "failed"}:
        raise SourceIngestJobError("current SLA adoption is allowed only for a blocked or failed legacy job")
    if job.get("enforce_hard_deadline") is False and int(job.get("sla_target_seconds") or 0) == JOB_TARGET_SECONDS:
        raise SourceIngestJobError("job already uses the current non-blocking SLA policy")
    first_accepted_at = str(job.get("first_accepted_at") or job.get("accepted_at") or "")
    if not first_accepted_at:
        raise SourceIngestJobError("legacy job has no first_accepted_at for SLA migration")
    first = parse_now(first_accepted_at)
    updated = dict(job)
    migrations = list(updated.get("sla_policy_migrations") or [])
    migrations.append(
        {
            "adopted_at": isoformat(now),
            "reason": "explicit_operator_requested_current_sla_policy",
            "previous_target_at": str(job.get("target_at") or ""),
            "previous_deadline_at": str(job.get("deadline_at") or ""),
            "previous_enforce_hard_deadline": bool(job.get("enforce_hard_deadline", True)),
            "new_target_at": isoformat(first + timedelta(seconds=JOB_TARGET_SECONDS)),
            "new_sla_target_seconds": JOB_TARGET_SECONDS,
            "new_enforce_hard_deadline": False,
        }
    )
    updated["sla_policy_migrations"] = migrations
    updated["target_at"] = isoformat(first + timedelta(seconds=JOB_TARGET_SECONDS))
    updated["deadline_at"] = updated["target_at"]
    updated["sla_target_seconds"] = JOB_TARGET_SECONDS
    updated["enforce_hard_deadline"] = False
    updated["updated_at"] = isoformat(now)
    return updated


def record_stage(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    now = parse_now(args.now)
    with ledger_lock(job_root):
        job = require_job(job_root, args.job_id)
        if job["status"] in TERMINAL_STATUSES:
            raise SourceIngestJobError("cannot record a stage on a terminal job")
        if not args.worker_claim or args.worker_claim != job.get("worker_claim"):
            raise SourceIngestJobError("worker claim does not match the durable job")
        if completed_stage_idempotent(job, args.stage, args.stage_status):
            return {"status": job["status"], "job": public_job(job), "write_performed": False, "idempotent_stage": True}
        validate_worker_cas(job, args.worker_claim, args.expected_work_version)
        validate_stage_transition(job, args.stage, args.stage_status)
        updated = dict(job)
        timings = json.loads(json.dumps(updated["stage_timings"]))
        timing = timings[args.stage]
        if args.stage_status == "running":
            timing["status"] = "running"
            timing["started_at"] = timing.get("started_at") or isoformat(now)
            if timing.get("completed_at") and timing.get("elapsed_seconds") is not None:
                timing["completed_at"] = ""
                timing["elapsed_seconds"] = None
            updated["status"] = "running"
            updated["current_stage"] = args.stage
            updated["retry_action"] = "resume this source-ingest job"
        elif args.stage_status == "completed":
            duration = validate_stage_duration(args.duration_seconds)
            timing["status"] = "completed"
            timing["started_at"] = timing.get("started_at") or isoformat(now)
            timing["completed_at"] = isoformat(now)
            timing["elapsed_seconds"] = duration
            updated["status"] = "running"
            updated["current_stage"] = next_stage_after(args.stage) or args.stage
            updated["retry_action"] = "continue the next source-ingest stage"
        else:
            if not args.reason or not args.retry_action:
                raise SourceIngestJobError("blocked/failed/needs_review stage records require reason and retry_action")
            duration = validate_stage_duration(args.duration_seconds)
            updated["status"] = args.stage_status
            updated["current_stage"] = args.stage
            updated["reason"] = args.reason
            updated["retry_action"] = args.retry_action
            timing["status"] = args.stage_status
            timing["started_at"] = timing.get("started_at") or isoformat(now)
            timing["completed_at"] = isoformat(now)
            timing["elapsed_seconds"] = duration
        updated["stage_timings"] = timings
        updated["updated_at"] = isoformat(now)
        stored = append_event_locked(
            job_root,
            f"stage_{args.stage}_{args.stage_status}",
            updated,
            now,
            expected_worker_claim=args.worker_claim,
            expected_work_version=args.expected_work_version,
        )
    return {"status": stored["status"], "job": public_job(stored), "write_performed": True}


def complete_job(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    now = parse_now(args.now)
    report_path = resolve_inside(root, args.completion_report_file)
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    with ledger_lock(job_root):
        job = require_job(job_root, args.job_id)
        if deadline_expired(job, now):
            failed = append_event_locked(job_root, "deadline_failed", deadline_failure_job(job, now), now)
            return {"status": failed["status"], "job": public_job(failed), "write_performed": True}
        validate_worker_cas(job, args.worker_claim, args.expected_work_version)
        validate_required_stage_timings(job)
        outcome_url = validate_pipeline_completion_report(report, job, args.outcome_url)
        completed = dict(job)
        completed["status"] = "completed"
        completed["current_stage"] = "completed"
        completed["updated_at"] = isoformat(now)
        completed["reason"] = "existing pipeline completion/readback verified"
        completed["retry_action"] = ""
        completed["outcome_url"] = outcome_url
        completed["completed_at"] = isoformat(now)
        completed["first_accepted_to_completed_seconds"] = elapsed_seconds(str(completed["first_accepted_at"]), completed["completed_at"])
        completed["attempt_wall_elapsed_seconds"] = elapsed_seconds(str(completed["attempt_accepted_at"]), completed["completed_at"])
        completed["content_result"] = "verified"
        completed["sla_result"] = sla_result_for_completion(completed)
        completed["sla_reason"] = sla_reason_for_completion(completed)
        completed["overtime_evidence"] = overtime_evidence_for_completion(completed)
        stored = append_event_locked(
            job_root,
            "completed",
            completed,
            now,
            expected_worker_claim=args.worker_claim,
            expected_work_version=args.expected_work_version,
        )
    return {
        "status": "completed",
        "job_id": stored["job_id"],
        "source_identity": stored["source_identity"],
        "outcome_url": outcome_url,
        "job": public_job(stored),
        "completion_report_job_readback": {
            "job_id": stored["job_id"],
            "status": stored["status"],
            "accepted_at": stored["accepted_at"],
            "first_accepted_at": stored.get("first_accepted_at", stored["accepted_at"]),
            "attempt_accepted_at": stored.get("attempt_accepted_at", stored["accepted_at"]),
            "completed_at": stored.get("completed_at", ""),
            "first_accepted_to_completed_seconds": stored.get("first_accepted_to_completed_seconds"),
            "attempt_wall_elapsed_seconds": stored.get("attempt_wall_elapsed_seconds"),
            "deadline_at": stored["deadline_at"],
            "content_result": stored["content_result"],
            "sla_result": stored["sla_result"],
            "sla_reason": stored.get("sla_reason", ""),
            "overtime_evidence": stored.get("overtime_evidence", {}),
            "stage_timings": stored["stage_timings"],
            "total_stage_elapsed_seconds": total_stage_elapsed_seconds(stored["stage_timings"]),
            "outcome_url": outcome_url,
        },
        "write_performed": True,
    }


def sla_result_for_completion(job: Mapping[str, Any]) -> str:
    elapsed = float(job.get("first_accepted_to_completed_seconds") or 0.0)
    if elapsed <= sla_target_seconds(job):
        return "met"
    return "missed"


def sla_reason_for_completion(job: Mapping[str, Any]) -> str:
    result = sla_result_for_completion(job)
    if result == "met":
        return f"completed within the {sla_target_seconds(job)}-second SLA target"
    evidence = overtime_evidence_for_completion(job)
    return (
        f"completed after the {evidence['target_seconds']}-second SLA target; "
        f"overrun={evidence['overrun_seconds']:.3f}s; "
        f"longest_stage={evidence['longest_stage']} ({evidence['longest_stage_seconds']:.3f}s)"
    )


def sla_target_seconds(job: Mapping[str, Any]) -> int:
    configured = job.get("sla_target_seconds")
    if isinstance(configured, int) and configured > 0:
        return configured
    try:
        return max(1, round(elapsed_seconds(str(job["first_accepted_at"]), str(job["target_at"]))))
    except (KeyError, TypeError, ValueError):
        return JOB_TARGET_SECONDS


def overtime_evidence_for_completion(job: Mapping[str, Any]) -> dict[str, Any]:
    elapsed = float(job.get("first_accepted_to_completed_seconds") or 0.0)
    target = sla_target_seconds(job)
    if elapsed <= target:
        return {}
    timings = job.get("stage_timings") if isinstance(job.get("stage_timings"), Mapping) else {}
    stage_seconds = {
        stage: float(timing.get("elapsed_seconds") or 0.0)
        for stage, timing in timings.items()
        if isinstance(timing, Mapping)
    }
    longest_stage = max(stage_seconds, key=stage_seconds.get, default="unrecorded")
    return {
        "target_seconds": target,
        "elapsed_seconds": elapsed,
        "overrun_seconds": elapsed - target,
        "stage_elapsed_seconds": stage_seconds,
        "longest_stage": longest_stage,
        "longest_stage_seconds": stage_seconds.get(longest_stage, 0.0),
        "external_wait_seconds": float(job.get("external_wait_seconds") or 0.0),
        "external_wait_evidence": job.get("external_wait_evidence", []),
    }


def record_external_wait(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    now = parse_now(args.now)
    with ledger_lock(job_root):
        job = require_job(job_root, args.job_id)
        if deadline_expired(job, now):
            failed = append_event_locked(job_root, "deadline_failed", deadline_failure_job(job, now), now)
            return {"status": failed["status"], "job": public_job(failed), "write_performed": True}
        if job["status"] in TERMINAL_STATUSES:
            raise SourceIngestJobError("cannot record external waiting on a terminal job")
        if EXTERNAL_WAIT_GRACE_SECONDS == 0:
            raise SourceIngestJobError("the 600-second SLA has no external-wait grace")
        validate_worker_cas(job, args.worker_claim, args.expected_work_version)
        if job.get("current_stage") != args.stage or nested_get(job, ("stage_timings", args.stage, "status")) != "running":
            raise SourceIngestJobError("external waiting must be recorded for the currently running stage")
        duration = validate_stage_duration(args.duration_seconds)
        reason = str(args.reason).strip()
        evidence = str(args.evidence).strip()
        if len(reason) < 4 or len(evidence) < 8:
            raise SourceIngestJobError("external waiting requires a concrete reason and evidence")
        if args.stage != "infographic" or not re.search(r"gpt[-_ ]?image[-_ ]?2|image_gen", f"{reason} {evidence}", re.IGNORECASE):
            raise SourceIngestJobError("only evidenced gpt-image-2/network waiting in the infographic stage may use grace")
        total = float(job.get("external_wait_seconds") or 0.0) + duration
        if total > EXTERNAL_WAIT_GRACE_SECONDS:
            raise SourceIngestJobError(f"external waiting exceeds the {EXTERNAL_WAIT_GRACE_SECONDS}-second grace ceiling")
        hard_deadline_at = parse_now(str(job["deadline_at"]))
        if now > hard_deadline_at and not job.get("recovery_mode"):
            failed = append_event_locked(job_root, "deadline_failed", deadline_failure_job(job, now), now)
            return {"status": failed["status"], "job": public_job(failed), "write_performed": True}
        updated = dict(job)
        updated["external_wait_seconds"] = total
        entries = list(updated.get("external_wait_evidence") or [])
        entries.append({"stage": args.stage, "duration_seconds": duration, "reason": reason, "evidence": evidence, "recorded_at": isoformat(now)})
        updated["external_wait_evidence"] = entries
        updated["updated_at"] = isoformat(now)
        stored = append_event_locked(
            job_root,
            "external_wait_recorded",
            updated,
            now,
            expected_worker_claim=args.worker_claim,
            expected_work_version=args.expected_work_version,
        )
    return {"status": stored["status"], "job": public_job(stored), "write_performed": True}


def record_observed_imagegen_wait(
    root: Path,
    job_root: Path,
    *,
    job_id: str,
    worker_claim: str,
    expected_work_version: int,
    response_file: Path,
    duration_seconds: float,
    evidence: str,
    now: datetime,
) -> dict[str, Any]:
    """Persist factual gpt-image-2 wait evidence without treating it as SLA grace.

    The current SLA has no grace: a real provider wait must be visible in a
    late completion report, not rejected before writer/readback. The response
    digest makes this replay-safe if a worker stops after imagegen validation.
    """
    duration = validate_stage_duration(duration_seconds)
    text_evidence = evidence.strip()
    if duration > 0 and len(text_evidence) < 8:
        raise SourceIngestJobError("observed gpt-image-2 wait requires durable evidence")
    response_sha256 = hashlib.sha256(response_file.read_bytes()).hexdigest()
    with ledger_lock(job_root):
        job = require_job(job_root, job_id)
        validate_worker_cas(job, worker_claim, expected_work_version)
        if job.get("current_stage") != "infographic" or nested_get(job, ("stage_timings", "infographic", "status")) != "running":
            raise SourceIngestJobError("observed gpt-image-2 wait must be recorded while infographic is running")
        entries = list(job.get("external_wait_evidence") or [])
        if any(isinstance(item, Mapping) and item.get("response_sha256") == response_sha256 for item in entries):
            return {"status": job["status"], "job": public_job(job), "write_performed": False, "idempotent": True}
        updated = dict(job)
        updated["external_wait_seconds"] = float(updated.get("external_wait_seconds") or 0.0) + duration
        entries.append(
            {
                "stage": "infographic",
                "duration_seconds": duration,
                "reason": "observed gpt-image-2 generation/network wait",
                "evidence": text_evidence,
                "response_sha256": response_sha256,
                "recorded_at": isoformat(now),
                "counts_as_sla_grace": False,
            }
        )
        updated["external_wait_evidence"] = entries
        updated["updated_at"] = isoformat(now)
        stored = append_event_locked(
            job_root,
            "observed_imagegen_wait_recorded",
            updated,
            now,
            expected_worker_claim=worker_claim,
            expected_work_version=expected_work_version,
        )
    return {"status": stored["status"], "job": public_job(stored), "write_performed": True}


def run_one_source_ingest(root: Path, job_root: Path, args: argparse.Namespace) -> dict[str, Any]:
    """Execute the bounded mechanical half of one reviewed source worker.

    The compatible background worker supplies exactly one reviewed source slice from the formal prepare
    packet. This command owns bounded stage execution, timings, checkpoints,
    source-local writer/readback and final job completion. It intentionally
    does not manufacture the AI analysis or an infographic; actual
    gpt-image-2 generation belongs to the action-envelope worker seam.
    """
    started = time.monotonic()
    envelope_args = SimpleNamespace(worker_envelope=args.worker_envelope, now=args.now)
    readback = worker_readback(root, job_root, envelope_args)
    source_slice = resolve_inside(root, args.source_slice)
    report_path = resolve_inside(root, args.completion_report_out)
    vault = resolve_inside(root, args.vault)
    job_id = str(readback["job_id"])
    claim = str(readback["worker_claim"])
    version = int(readback["work_version"])
    prior_job = require_job(job_root, job_id)
    prior_checkpoints = prior_job.get("checkpoints") if isinstance(prior_job.get("checkpoints"), dict) else {}
    stage_results: list[dict[str, object]] = []

    def now_value() -> str:
        if args.now:
            return isoformat(parse_now(args.now) + timedelta(seconds=time.monotonic() - started))
        return isoformat(datetime.now(timezone.utc))

    def run_stage(stage: str, action, checkpoint_paths: list[Path], *, reuse_action=None, before_complete=None) -> bool:
        nonlocal version
        current_job = require_job(job_root, job_id)
        current_timing = nested_get(current_job, ("stage_timings", stage))
        reusable = checkpoint_is_reusable(root, prior_checkpoints, stage, checkpoint_paths, current_job["source_identity"])
        stage_action = reuse_action if reusable and reuse_action is not None else action
        if isinstance(current_timing, Mapping) and current_timing.get("status") == "completed":
            try:
                action_result = stage_action()
            except (SourceIngestJobError, OSError, ValueError, subprocess.SubprocessError) as exc:
                raise SourceIngestJobError(f"completed checkpoint for {stage} is no longer valid: {exc}") from exc
            stage_results.append({"stage": stage, "status": "reused_checkpoint", "result": action_result})
            version = int(current_job["work_version"])
            return True
        stage_started = time.monotonic()
        running = record_stage(root, job_root, SimpleNamespace(
            job_id=job_id,
            stage=stage,
            stage_status="running",
            worker_claim=claim,
            expected_work_version=version,
            duration_seconds=None,
            reason="",
            retry_action="",
            now=now_value(),
        ))
        version = int(running["job"]["work_version"])
        try:
            action_result = stage_action()
        except (SourceIngestJobError, OSError, ValueError, subprocess.SubprocessError) as exc:
            blocked = record_stage(root, job_root, SimpleNamespace(
                job_id=job_id,
                stage=stage,
                stage_status="blocked",
                worker_claim=claim,
                expected_work_version=version,
                duration_seconds=time.monotonic() - stage_started,
                reason=str(exc),
                retry_action="inspect the recorded stage evidence, repair only this source, then explicitly resume the same job",
                now=now_value(),
            ))
            stage_results.append({"stage": stage, "status": "blocked", "reason": str(exc)})
            version = int(blocked["job"]["work_version"])
            return False
        if before_complete is not None:
            version = int(before_complete(action_result, stage_started, version))
        completed = record_stage(root, job_root, SimpleNamespace(
            job_id=job_id,
            stage=stage,
            stage_status="completed",
            worker_claim=claim,
            expected_work_version=version,
            duration_seconds=0.0 if reusable else time.monotonic() - stage_started,
            reason="",
            retry_action="",
            now=now_value(),
        ))
        version = int(completed["job"]["work_version"])
        checkpoint = record_checkpoint(
            root,
            job_root,
            job_id,
            claim,
            version,
            stage,
            checkpoint_paths,
            now=parse_now(now_value()),
            reused=reusable,
        )
        version = int(checkpoint["work_version"])
        stage_results.append({"stage": stage, "status": "reused_checkpoint" if reusable else "completed", "result": action_result})
        return True

    if not run_stage("capture", lambda: validate_reviewed_packet(root, source_slice, readback), [source_slice.parent / "source_capture_metadata.json", source_slice.parent / "review_packet.json"]):
        return run_one_failed(job_root, job_id, stage_results)
    if not run_stage("source_analysis", lambda: validate_reviewed_slice(source_slice), [source_slice]):
        return run_one_failed(job_root, job_id, stage_results)
    current_infographic = nested_get(require_job(job_root, job_id), ("stage_timings", "infographic"))
    if args.imagegen_response_file:
        response_file = resolve_inside(root, args.imagegen_response_file)
        stage_started = time.monotonic()
        running = record_stage(root, job_root, SimpleNamespace(
            job_id=job_id, stage="infographic", stage_status="running", worker_claim=claim,
            expected_work_version=version, duration_seconds=None, reason="", retry_action="", now=now_value(),
        ))
        version = int(running["job"]["work_version"])
        try:
            imagegen_result = complete_gpt_imagegen_action(root, source_slice, response_file)
        except (SourceIngestJobError, OSError, ValueError, subprocess.SubprocessError) as exc:
            blocked = record_stage(root, job_root, SimpleNamespace(
                job_id=job_id, stage="infographic", stage_status="blocked", worker_claim=claim,
                expected_work_version=version, duration_seconds=time.monotonic() - stage_started, reason=str(exc),
                retry_action="inspect the locked gpt-image-2 action and explicitly resume the same job after repairing its direct blocker", now=now_value(),
            ))
            stage_results.append({"stage": "infographic", "status": "blocked", "reason": str(exc)})
            return run_one_failed(job_root, job_id, stage_results)
        if imagegen_result.get("status") != "ready":
            failure = ""
            results = imagegen_result.get("results")
            if isinstance(results, list):
                failures = [str(item.get("failure") or "") for item in results if isinstance(item, Mapping) and item.get("failure")]
                failure = failures[0] if failures else ""
            waiting = record_stage(root, job_root, SimpleNamespace(
                job_id=job_id, stage="infographic", stage_status="needs_review", worker_claim=claim,
                expected_work_version=version, duration_seconds=time.monotonic() - stage_started,
                reason="gpt-image-2 returned a mechanical blocking infographic failure",
                retry_action=(
                    "create at most one blocking_retry action "
                    f"for failure={failure}; it must contain only failed pages. Otherwise leave this same job blocked for explicit resume."
                ), now=now_value(),
            ))
            stage_results.append({"stage": "infographic", "status": "needs_review", "result": imagegen_result})
            return {
                "status": "needs_review", "job_id": job_id, "job": public_job(waiting["job"]),
                "imagegen_result": imagegen_result, "retry_action": waiting["job"]["retry_action"],
                "worker_execution": {"stage_results": stage_results, "mode": "gpt_image_2_blocking_repair_only"}, "write_performed": True,
            }
        wait_seconds = float(imagegen_result.get("imagegen_wait_seconds") or 0.0)
        if wait_seconds > 0:
            observed_wait = record_observed_imagegen_wait(
                root,
                job_root,
                job_id=job_id,
                worker_claim=claim,
                expected_work_version=version,
                response_file=response_file,
                duration_seconds=wait_seconds,
                evidence=str(imagegen_result.get("imagegen_wait_evidence") or ""),
                now=parse_now(now_value()),
            )
            version = int(observed_wait["job"]["work_version"])
        completed_stage = record_stage(root, job_root, SimpleNamespace(
            job_id=job_id, stage="infographic", stage_status="completed", worker_claim=claim,
            expected_work_version=version, duration_seconds=time.monotonic() - stage_started + wait_seconds,
            reason="", retry_action="", now=now_value(),
        ))
        version = int(completed_stage["job"]["work_version"])
        checkpoint = record_checkpoint(
            root, job_root, job_id, claim, version, "infographic",
            [source_slice.parent / "imagegen_attempts.json", source_slice.parent / "imagegen_action_envelope.json"], now=parse_now(now_value()),
        )
        version = int(checkpoint["work_version"])
        stage_results.append({"stage": "infographic", "status": "completed", "result": imagegen_result})
    else:
        try:
            ready_infographic = validate_ready_gpt_imagegen(root, source_slice)
        except SourceIngestJobError:
            if (
                isinstance(current_infographic, Mapping)
                and current_infographic.get("status") == "needs_review"
                and not args.blocking_repair_failure
            ):
                action_path = source_slice.parent / "imagegen_action_envelope.json"
                return {
                    "status": "needs_review",
                    "job_id": job_id,
                    "job": public_job(require_job(job_root, job_id)),
                    "imagegen_action_envelope": str(action_path.relative_to(root)) if action_path.exists() else "",
                    "retry_action": "send the locked imagegen action envelope to the configured gpt-image-2 worker; save actual PNGs and response JSON, then re-run this same job",
                    "worker_execution": {"stage_results": stage_results, "mode": "awaiting_gpt_image_2"},
                    "write_performed": False,
                }
            stage_started = time.monotonic()
            running = record_stage(root, job_root, SimpleNamespace(
                job_id=job_id,
                stage="infographic",
                stage_status="running",
                worker_claim=claim,
                expected_work_version=version,
                duration_seconds=None,
                reason="",
                retry_action="",
                now=now_value(),
            ))
            version = int(running["job"]["work_version"])
            try:
                action = prepare_gpt_imagegen_action(
                    root,
                    source_slice,
                    require_job(job_root, job_id),
                    blocking_repair_failure=str(args.blocking_repair_failure or ""),
                )
            except (SourceIngestJobError, OSError, ValueError, subprocess.SubprocessError) as exc:
                blocked = record_stage(root, job_root, SimpleNamespace(
                    job_id=job_id,
                    stage="infographic",
                    stage_status="blocked",
                    worker_claim=claim,
                    expected_work_version=version,
                    duration_seconds=time.monotonic() - stage_started,
                    reason=str(exc),
                    retry_action="repair only the locked infographic brief and explicitly resume the same job",
                    now=now_value(),
                ))
                stage_results.append({"stage": "infographic", "status": "blocked", "reason": str(exc)})
                return run_one_failed(job_root, job_id, stage_results)
            waiting = record_stage(root, job_root, SimpleNamespace(
                job_id=job_id,
                stage="infographic",
                stage_status="needs_review",
                worker_claim=claim,
                expected_work_version=version,
                duration_seconds=time.monotonic() - stage_started,
                reason=("awaiting explicit failed-page gpt-image-2 blocking repair" if args.blocking_repair_failure else "awaiting actual gpt-image-2 generation result"),
                retry_action="send imagegen_action_envelope to the configured gpt-image-2 worker; save actual PNGs and response JSON, then re-run this same job with --imagegen-response-file",
                now=now_value(),
            ))
            version = int(waiting["job"]["work_version"])
            action_path = resolve_inside(root, Path(str(action["action_envelope"])))
            checkpoint = record_checkpoint(
                root, job_root, job_id, claim, version, "infographic", [action_path], now=parse_now(now_value())
            )
            stage_results.append({"stage": "infographic", "status": "awaiting_imagegen", "result": action})
            return {
                "status": "needs_review",
                "job_id": job_id,
                "job": public_job(checkpoint),
                "imagegen_action_envelope": action["action_envelope"],
                "provider": action.get("provider"),
                "model": action.get("model"),
                "retry_action": "configured gpt-image-2 worker must execute the locked action and resume this same job with its response JSON",
                "worker_execution": {"stage_results": stage_results, "mode": "awaiting_gpt_image_2"},
                "write_performed": True,
            }
        if not run_stage(
            "infographic",
            lambda: ready_infographic,
            [source_slice.parent / "imagegen_attempts.json"],
        ):
            return run_one_failed(job_root, job_id, stage_results)
    if not run_stage(
        "writer_readback",
        lambda: run_source_local_ingest(root, source_slice, vault, args.max_kp, job_id, job_root, report_path),
        [report_path],
        reuse_action=lambda: validate_existing_completion_report(report_path, str(readback["source_identity"])),
    ):
        return run_one_failed(job_root, job_id, stage_results)
    completed = complete_job(root, job_root, SimpleNamespace(
        job_id=job_id,
        completion_report_file=report_path,
        worker_claim=claim,
        expected_work_version=version,
        outcome_url="",
        now=now_value(),
    ))
    completed["worker_execution"] = {
        "mode": "reviewed_packet_then_bounded_pipeline_runner",
        "stage_results": stage_results,
        "runner_monotonic_seconds": time.monotonic() - started,
        "wall_time_contract": "first_accepted_at to completed_at is authoritative; stage sums are diagnostics only",
    }
    return completed


def run_one_failed(job_root: Path, job_id: str, stage_results: list[dict[str, object]]) -> dict[str, Any]:
    job = require_job(job_root, job_id)
    return {
        "status": job["status"],
        "job_id": job_id,
        "job": public_job(job),
        "worker_execution": {"stage_results": stage_results, "mode": "reviewed_packet_then_bounded_pipeline_runner"},
        "write_performed": True,
    }


def validate_reviewed_packet(root: Path, source_slice: Path, worker: Mapping[str, Any]) -> dict[str, object]:
    if not source_slice.exists():
        raise SourceIngestJobError("reviewed source slice is missing")
    metadata_path = source_slice.parent / "source_capture_metadata.json"
    review_path = source_slice.parent / "review_packet.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        review = json.loads(review_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceIngestJobError(f"formal prepare packet is unreadable: {exc}") from exc
    for label, packet in (("metadata", metadata), ("review packet", review)):
        if not isinstance(packet, dict):
            raise SourceIngestJobError(f"{label} is not an object")
        if packet.get("source_identity") != worker["source_identity"]:
            raise SourceIngestJobError(f"{label} source identity does not match the worker envelope")
        if packet.get("source_input_kind") != worker["source_input_kind"]:
            raise SourceIngestJobError(f"{label} source input kind does not match the worker envelope")
        if packet.get("source_retention_mode") != worker["source_retention_mode"]:
            raise SourceIngestJobError(f"{label} retention mode does not match the worker envelope")
    return {"packet": str(source_slice.parent), "source_identity": worker["source_identity"]}


def validate_reviewed_slice(source_slice: Path) -> dict[str, object]:
    text = source_slice.read_text(encoding="utf-8-sig")
    if "| AI 來源分析狀態 | complete |" not in text:
        raise SourceIngestJobError("reviewed source slice does not declare complete AI source analysis")
    analysis_scope = text.split("## 資訊圖", 1)[0]
    if re.search(r"待 AI|待補|\bpending\b|\bTODO\b", analysis_scope, flags=re.IGNORECASE):
        raise SourceIngestJobError("reviewed source slice still contains placeholders")
    try:
        from tools.obsidian_vault_adapter import (
            application_metadata,
            compounding_metadata_issues,
            learning_check_gaps,
            parse_slice,
            slice_enum_violations,
        )
    except ModuleNotFoundError:  # pragma: no cover - direct script invocation.
        from obsidian_vault_adapter import (  # type: ignore
            application_metadata,
            compounding_metadata_issues,
            learning_check_gaps,
            parse_slice,
            slice_enum_violations,
        )
    data = parse_slice(source_slice, max_kp=6)
    content_issues = [
        str(item.get("message") or item.get("field"))
        for item in slice_enum_violations(data)
    ]
    content_issues.extend(
        f"missing learning check {check_type}"
        for check_type in learning_check_gaps(text)
    )
    content_issues.extend(compounding_metadata_issues(application_metadata(data)))
    if content_issues:
        raise SourceIngestJobError(
            "reviewed source slice content contract failed: " + "; ".join(content_issues[:12])
        )
    try:
        from tools.firecrawl_obsidian_pipeline import extract_infographic_blocks, infographic_preflight
    except ModuleNotFoundError:  # pragma: no cover - direct script invocation.
        from firecrawl_obsidian_pipeline import extract_infographic_blocks, infographic_preflight  # type: ignore
    preflight = infographic_preflight(text, extract_infographic_blocks(text))
    if preflight.get("status") != "ready":
        codes = [str(item.get("code")) for item in preflight.get("issues", []) if isinstance(item, Mapping)]
        raise SourceIngestJobError(
            "reviewed source slice infographic preflight failed: " + ", ".join(codes[:8])
        )
    return {
        "source_slice": str(source_slice),
        "analysis_status": "complete",
        "content_contract": "passed",
        "infographic_preflight": preflight,
    }


def run_pipeline_json(root: Path, command: list[str]) -> dict[str, object]:
    run = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise SourceIngestJobError(f"pipeline step did not return JSON: {run.stdout[:300]}") from exc
    if run.returncode != 0:
        raise SourceIngestJobError(str(payload.get("message") or payload.get("reason") or payload.get("status")))
    return payload


def validate_ready_gpt_imagegen(root: Path, source_slice: Path) -> dict[str, object]:
    try:
        from tools.firecrawl_obsidian_pipeline import validate_imagegen_attempts_for_ingest
    except ModuleNotFoundError:  # pragma: no cover - direct script invocation.
        from firecrawl_obsidian_pipeline import validate_imagegen_attempts_for_ingest  # type: ignore
    try:
        return validate_imagegen_attempts_for_ingest(root, source_slice)
    except Exception as exc:
        raise SourceIngestJobError(f"gpt-image-2 infographic is not ready: {exc}") from exc


def prepare_gpt_imagegen_action(
    root: Path,
    source_slice: Path,
    job: Mapping[str, Any],
    *,
    blocking_repair_failure: str = "",
) -> dict[str, object]:
    command = [
        sys.executable,
        str(PIPELINE),
        "--root", str(root),
        "imagegen-action-envelope",
        "--source-slice", str(source_slice.relative_to(root)),
        "--job-id", str(job["job_id"]),
    ]
    if blocking_repair_failure:
        command.extend(["--reason", "blocking_retry", "--failure", blocking_repair_failure])
        if job.get("enforce_hard_deadline", True):
            command.extend([
                "--first-accepted-at", str(job["first_accepted_at"]),
                "--hard-deadline-at", str(job["deadline_at"]),
            ])
    command.extend([
        "--json",
    ])
    return run_pipeline_json(root, command)


def complete_gpt_imagegen_action(root: Path, source_slice: Path, response_file: Path) -> dict[str, object]:
    command = [
        sys.executable,
        str(PIPELINE),
        "--root", str(root),
        "imagegen-complete",
        "--source-slice", str(source_slice.relative_to(root)),
        "--response-file", str(response_file.relative_to(root)),
        "--json",
    ]
    run = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise SourceIngestJobError(f"gpt-image-2 completion did not return JSON: {run.stdout[:300]}") from exc
    if run.returncode != 0 and payload.get("status") != "needs_review":
        raise SourceIngestJobError(str(payload.get("message") or payload.get("reason") or payload.get("status")))
    return payload


def run_source_local_ingest(
    root: Path,
    source_slice: Path,
    vault: Path,
    max_kp: int,
    job_id: str,
    job_root: Path,
    report_path: Path,
) -> dict[str, object]:
    command = [
        sys.executable,
        str(PIPELINE),
        "--root", str(root),
        "ingest",
        "--source-slice", str(source_slice.relative_to(root)),
        "--vault", str(vault.relative_to(root)),
        "--max-kp", str(max_kp),
        "--write",
        "--source-ingest-job-id", job_id,
        "--source-ingest-job-root", str(job_root.relative_to(root)),
        "--completion-report-out", str(report_path.relative_to(root)),
        "--json",
    ]
    run = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise SourceIngestJobError(f"source-local ingest did not return JSON: {run.stdout[:300]}") from exc
    if run.returncode != 0 or payload.get("status") != "ready" or not report_path.exists():
        raise SourceIngestJobError(f"source-local ingest is not ready: {payload.get('message') or payload.get('status')}")
    return payload


def validate_existing_completion_report(report_path: Path, source_identity: str) -> dict[str, object]:
    try:
        report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceIngestJobError(f"writer/readback checkpoint is unreadable: {exc}") from exc
    if report.get("status") != "ready" or nested_get(report, ("completion_report", "status")) != "ready":
        raise SourceIngestJobError("writer/readback checkpoint is not a ready formal completion report")
    report_identity = nested_get(report, ("write", "source_identity")) or nested_get(report, ("dry_run", "source_identity"))
    if report_identity != source_identity:
        raise SourceIngestJobError("writer/readback checkpoint source identity does not match this job")
    return {"completion_report": str(report_path), "checkpoint_readback": "ready"}


def checkpoint_is_reusable(
    root: Path,
    checkpoints: Mapping[str, Any],
    stage: str,
    paths: list[Path],
    source_identity: str,
) -> bool:
    checkpoint = checkpoints.get(stage)
    if not isinstance(checkpoint, Mapping) or checkpoint.get("source_identity") != source_identity:
        return False
    recorded_paths = checkpoint.get("paths")
    if not isinstance(recorded_paths, list) or not recorded_paths:
        return False
    expected = {
        str(path.resolve().relative_to(root))
        for path in paths
        if path.exists() and root in path.resolve().parents
    }
    return bool(expected) and set(str(value) for value in recorded_paths) == expected


def record_checkpoint(
    root: Path,
    job_root: Path,
    job_id: str,
    worker_claim: str,
    expected_work_version: int,
    stage: str,
    paths: list[Path],
    *,
    now: datetime,
    reused: bool = False,
) -> dict[str, Any]:
    with ledger_lock(job_root):
        job = require_job(job_root, job_id)
        validate_worker_cas(job, worker_claim, expected_work_version)
        updated = dict(job)
        checkpoints = dict(updated.get("checkpoints") or {})
        checkpoints[stage] = {
            "recorded_at": isoformat(now),
            "paths": [str(path.resolve().relative_to(root)) for path in paths if path.exists() and root in path.resolve().parents],
            "source_identity": job["source_identity"],
            "reused_in_attempt": int(job.get("attempt_number", 1)) if reused else None,
        }
        updated["checkpoints"] = checkpoints
        updated["updated_at"] = isoformat(now)
        return append_event_locked(
            job_root,
            f"checkpoint_{stage}",
            updated,
            now,
            expected_worker_claim=worker_claim,
            expected_work_version=expected_work_version,
        )


def source_from_accept_args(args: argparse.Namespace) -> dict[str, Any]:
    intent = args.intent_text or args.terminal_text or ""
    if args.terminal_text:
        url = first_url(args.terminal_text)
        if not url:
            raise SourceIngestJobError("terminal text must contain a URL, or use --user-text-file")
        return {
            "source_input_kind": "url",
            "source_url": url,
            "source_identity": url_source_identity(url),
            "intent_text": args.terminal_text,
        }
    if args.url:
        return {
            "source_input_kind": "url",
            "source_url": args.url,
            "source_identity": url_source_identity(args.url),
            "intent_text": intent,
        }
    if args.user_text_file:
        raw = args.user_text_file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        return {
            "source_input_kind": "user_provided_text",
            "user_text_sha256": digest,
            "source_identity": f"user_provided_text:{digest}",
            "intent_text": intent,
            "_raw_bytes": raw,
            "_staged_filename": "input.md",
        }
    if not args.user_image_file:
        raise SourceIngestJobError("provide one URL, user text file, or user image file")
    try:
        inspection = inspect_image(args.user_image_file)
    except (ImageSourceContractError, OSError) as exc:
        raise SourceIngestJobError(f"user image is not a supported PNG, JPEG, or WebP: {exc}") from exc
    raw = args.user_image_file.read_bytes()
    return {
        "source_input_kind": "user_provided_image",
        "user_image_sha256": inspection.sha256,
        "source_identity": f"user_provided_image:{inspection.sha256}",
        "intent_text": intent,
        "_raw_bytes": raw,
        "_staged_filename": f"input{inspection.extension}",
    }


def stage_user_input_bytes(*, root: Path, job_root: Path, job_id: str, raw: bytes, digest: str, filename: str) -> str:
    spool_dir = resolve_inside(root, job_root / "spool" / job_id)
    spool_dir.mkdir(parents=True, exist_ok=True)
    staged = spool_dir / filename
    staged.write_bytes(raw)
    if hashlib.sha256(staged.read_bytes()).hexdigest() != digest:
        raise SourceIngestJobError("staged source input hash mismatch after write")
    return str(staged.relative_to(root))


def validate_pipeline_completion_report(report: Mapping[str, Any], job: Mapping[str, Any], supplied_outcome_url: str) -> str:
    if report.get("status") != "ready":
        raise SourceIngestJobError("pipeline report status is not ready")
    completion = report.get("completion_report")
    if not isinstance(completion, Mapping) or completion.get("status") != "ready":
        raise SourceIngestJobError("pipeline completion_report is not ready")
    source_identity = nested_get(report, ("write", "source_identity")) or nested_get(report, ("dry_run", "source_identity"))
    if source_identity != job["source_identity"]:
        raise SourceIngestJobError("pipeline report source_identity does not match this job")
    verification = nested_get(report, ("write", "verification"))
    if not isinstance(verification, Mapping) or verification.get("passed") is not True:
        raise SourceIngestJobError("pipeline report does not include passed writer readback verification")
    checks = completion.get("checks")
    if not isinstance(checks, list) or not any(
        isinstance(row, Mapping) and row.get("check") == "write_readback" and row.get("status") == "passed"
        for row in checks
    ):
        raise SourceIngestJobError("pipeline completion_report does not prove write/readback")
    if job.get("source_retention_mode") == "full_body_required":
        if verification.get("full_body_readback") is not True:
            raise SourceIngestJobError("full_body_required job lacks full_body_readback evidence")
        full_body = nested_get(completion, ("evidence", "full_body"))
        if not isinstance(full_body, Mapping) or full_body.get("readback_sha256") != full_body.get("canonical_body_sha256"):
            raise SourceIngestJobError("full_body_required job lacks matching canonical/readback body hash")
    outcome_url = supplied_outcome_url or str(nested_get(completion, ("evidence", "detail_page")) or "")
    if not outcome_url:
        raise SourceIngestJobError("pipeline completion_report has no outcome URL/detail page")
    return outcome_url


def build_worker_envelope(root: Path, job_root: Path, job: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool": "source-ingest",
        "contract_version": WORKER_ENVELOPE_VERSION,
        "project_root": str(root),
        "job_root": str(job_root.relative_to(root)),
        "job_id": job["job_id"],
        "source_identity": job["source_identity"],
        "source_input_kind": job["source_input_kind"],
        "source_url": job.get("source_url", ""),
        "user_text_sha256": job.get("user_text_sha256", ""),
        "user_image_sha256": job.get("user_image_sha256", ""),
        "source_retention_mode": job["source_retention_mode"],
        "worker_runtime": job.get("worker_runtime", "codex_app"),
        "worker_claim": job.get("worker_claim", ""),
        "worker_task": "run-single-source-ingest",
        "runbook_path": "skills/url-to-obsidian-ingest/references/runbook.md",
        "first_accepted_at": job["first_accepted_at"],
        "target_at": job["target_at"],
        "deadline_at": job["deadline_at"],
        "dispatch_request_path": job.get("dispatch_request_path", ""),
        "staged_input_path": job.get("staged_input_path", ""),
    }
    payload["payload_sha256"] = payload_sha256(payload)
    validate_worker_envelope(payload, expected_root=root)
    return payload


def build_worker_dispatch_request(root: Path, job: Mapping[str, Any], envelope_path: Path) -> dict[str, Any]:
    """A provider-neutral request for an app/runtime that can actually start a worker.

    The repository deliberately does not launch Codex, Claude Code, or imagegen via
    subprocess.  The caller uses this request with its verified runtime API, then
    records the resulting opaque runtime id through ``bind-worker``.
    """

    runtime = str(job.get("worker_runtime", "codex_app"))
    envelope_relative = str(envelope_path.relative_to(root))
    return {
        "schema_version": "source_ingest_worker_dispatch_v1",
        "job_id": job["job_id"],
        "worker_runtime": runtime,
        "worker_envelope": envelope_relative,
        "runbook_path": "skills/url-to-obsidian-ingest/references/runbook.md",
        "first_accepted_at": job["first_accepted_at"],
        "target_at": job["target_at"],
        "deadline_at": job["deadline_at"],
        "enforce_hard_deadline": bool(job.get("enforce_hard_deadline", True)),
        "task_prompt": worker_task_prompt(runtime, envelope_relative),
        "dispatch_contract": (
            "Runtime must create a real background task, pass this prompt unchanged, then call bind-worker with the opaque task id. "
            "The worker owns capture through outcome; it must not create a second vault writer or content state."
        ),
    }


def worker_task_prompt(runtime: str, envelope_relative: str) -> str:
    runtime_label = {
        "codex_app": "Codex background task",
        "claude_code": "Claude Code background task",
        "external_worker": "compatible background worker",
    }[runtime]
    return (
        f"You are the {runtime_label} for exactly one source-ingest job. Read `{envelope_relative}` and its runbook. "
        "Run the full source flow, not only run-one: use worker-capture for the declared URL/text/image; perform one complete AI analysis into the formal review packet/source slice; call worker-analysis-complete with the actual monotonic analysis duration; then run-one for preflight, locked gpt-image-2 action, source-local writer/readback, and incremental outcome. "
        "Never read an original absolute user path, never create another writer/content state, never use template PNGs, and stop with the durable job reason/retry action only if a formal gate/provider/data error prevents completion. A clean result after the SLA target must still be delivered with overtime evidence."
    )


def validate_worker_envelope(envelope: Mapping[str, Any], *, expected_root: Path) -> dict[str, Any]:
    payload = dict(envelope)
    if set(payload) != ENVELOPE_FIELDS:
        raise SourceIngestJobError("worker envelope schema mismatch")
    if payload["tool"] != "source-ingest" or payload["contract_version"] != WORKER_ENVELOPE_VERSION:
        raise SourceIngestJobError("unsupported worker envelope")
    if resolve_inside(expected_root, Path(str(payload["project_root"]))) != expected_root:
        raise SourceIngestJobError("worker envelope project_root mismatch")
    if payload["source_retention_mode"] not in RETENTION_MODES:
        raise SourceIngestJobError("worker envelope source_retention_mode is invalid")
    if not isinstance(payload["worker_claim"], str) or not payload["worker_claim"].startswith("sij_claim_"):
        raise SourceIngestJobError("worker envelope is missing a valid worker claim")
    if payload["worker_runtime"] not in WORKER_RUNTIMES:
        raise SourceIngestJobError("worker envelope runtime is invalid")
    if payload["worker_task"] != "run-single-source-ingest":
        raise SourceIngestJobError("worker envelope task is not a single-source full worker task")
    runbook = resolve_inside(expected_root, Path(str(payload["runbook_path"])))
    if not runbook.exists():
        raise SourceIngestJobError("worker envelope runbook_path does not exist")
    if parse_now(str(payload["target_at"])) > parse_now(str(payload["deadline_at"])):
        raise SourceIngestJobError("worker envelope target/deadline order is invalid")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if SECRET_PATTERN.search(serialized):
        raise SourceIngestJobError("worker envelope must not contain credentials")
    if payload["payload_sha256"] != payload_sha256(payload):
        raise SourceIngestJobError("worker envelope payload hash mismatch")
    return payload


def payload_sha256(payload: Mapping[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "payload_sha256"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def apply_deadline_if_needed(job_root: Path, job: dict[str, Any], now: datetime, *, mutate: bool) -> tuple[dict[str, Any], bool]:
    if job["status"] in TERMINAL_STATUSES:
        return job, False
    if not deadline_expired(job, now):
        return job, False
    failed = deadline_failure_job(job, now)
    if mutate:
        failed = append_event(
            job_root,
            "deadline_failed",
            failed,
            now,
            expected_work_version=int(job.get("work_version", 0)),
            expected_worker_claim=str(job.get("worker_claim", "")) or None,
        )
    return failed, mutate


def deadline_expired(job: Mapping[str, Any], now: datetime) -> bool:
    if job["status"] in TERMINAL_STATUSES:
        return False
    if job.get("enforce_hard_deadline") is False:
        return False
    # The first-accepted hard ceiling is absolute for normal execution.  A user
    # can explicitly resume a failed job for recovery, but its SLA remains missed.
    if job.get("recovery_mode") is True:
        return False
    return now > parse_now(str(job["deadline_at"]))


def deadline_failure_job(job: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    failed = dict(job)
    failed["status"] = "failed"
    failed["sla_result"] = "missed"
    failed["current_stage"] = job.get("current_stage") or "unknown"
    target_at = parse_now(str(job["target_at"]))
    used_grace = float(job.get("external_wait_seconds") or 0.0)
    failed["reason"] = "hard_deadline_exceeded"
    failed["retry_action"] = (
        "inspect the current stage and any AI/network wait evidence; explicitly resume this same job after fixing the blocker"
    )
    failed["deadline_evidence"] = {
        "target_at": isoformat(target_at),
        "grace_expires_at": isoformat(target_at),
        "deadline_at": job["deadline_at"],
        "external_wait_seconds": used_grace,
        "external_wait_evidence_count": len(job.get("external_wait_evidence") or []),
    }
    failed["updated_at"] = isoformat(now)
    return failed


def resume_attempt_job(job: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    resumed = dict(job)
    resumed["status"] = "accepted"
    resumed["attempt_accepted_at"] = isoformat(now)
    resumed["accepted_at"] = isoformat(now)
    # target_at/deadline_at are first-accepted SLA facts and must never be reset.
    resumed["updated_at"] = isoformat(now)
    resumed["current_stage"] = "accepted"
    resumed["reason"] = ""
    resumed["retry_action"] = f"dispatch the {resumed.get('worker_runtime', 'codex_app')} worker request and resume this job"
    resumed["stage_timings"] = empty_stage_timings()
    resumed["worker_envelope_path"] = ""
    resumed["dispatch_request_path"] = ""
    resumed["worker_claim"] = ""
    resumed["worker_instance_id"] = ""
    resumed["worker_dispatch_evidence"] = ""
    resumed["lease_expires_at"] = ""
    resumed["attempt_number"] = int(job.get("attempt_number", 1)) + 1
    resumed["recovery_mode"] = bool(job.get("enforce_hard_deadline", True)) and now > parse_now(str(job["deadline_at"]))
    if resumed["recovery_mode"]:
        resumed["sla_result"] = "missed"
    return resumed


@contextmanager
def ledger_lock(job_root: Path):
    """Cross-process ledger lock that releases automatically on process exit."""

    job_root.mkdir(parents=True, exist_ok=True)
    lock_path = job_root / ".jobs.lock"
    with lock_path.open("a+b") as fh:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        acquired = False
        for _ in range(LOCK_RETRY_COUNT):
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - exercised on non-Windows runners only.
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(LOCK_RETRY_SLEEP_SECONDS)
        if not acquired:
            raise SourceIngestJobError("source-ingest job ledger is locked; retry this job")
        try:
            yield
        finally:
            try:
                fh.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on non-Windows runners only.
                    import fcntl

                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def append_event(
    job_root: Path,
    event_type: str,
    job: Mapping[str, Any],
    now: datetime,
    *,
    expected_work_version: int | None = None,
    expected_worker_claim: str | None = None,
    create_only: bool = False,
) -> dict[str, Any]:
    with ledger_lock(job_root):
        return append_event_locked(
            job_root,
            event_type,
            job,
            now,
            expected_work_version=expected_work_version,
            expected_worker_claim=expected_worker_claim,
            create_only=create_only,
        )


def append_event_locked(
    job_root: Path,
    event_type: str,
    job: Mapping[str, Any],
    now: datetime,
    *,
    expected_work_version: int | None = None,
    expected_worker_claim: str | None = None,
    create_only: bool = False,
) -> dict[str, Any]:
    events = job_root / "jobs.jsonl"
    latest = latest_jobs(job_root).get(str(job["job_id"]))
    if create_only and latest is not None:
        raise SourceIngestJobError("source-ingest job already exists")
    if expected_work_version is not None:
        if latest is None:
            raise SourceIngestJobError("source-ingest job not found")
        if expected_work_version != int(latest.get("work_version", 0)):
            raise SourceIngestJobError("stale source-ingest job work_version")
        if expected_worker_claim is not None and expected_worker_claim != latest.get("worker_claim"):
            raise SourceIngestJobError("worker claim does not match the durable job")
    work_version = int(latest.get("work_version", 0)) + 1 if latest else 1
    previous_event_id = str(latest.get("current_event_id") or "") if latest else ""
    event_id = f"sij_evt_{uuid.uuid4().hex}"
    stored_job = dict(job)
    stored_job["work_version"] = work_version
    stored_job["previous_event_id"] = previous_event_id
    stored_job["current_event_id"] = event_id
    event = {
        "event_id": event_id,
        "previous_event_id": previous_event_id,
        "event_type": event_type,
        "recorded_at": isoformat(now),
        "job_id": stored_job["job_id"],
        "source_identity": stored_job["source_identity"],
        "job": stored_job,
    }
    with events.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return stored_job


def latest_jobs(job_root: Path) -> dict[str, dict[str, Any]]:
    events = job_root / "jobs.jsonl"
    latest: dict[str, dict[str, Any]] = {}
    if not events.exists():
        return latest
    for line in events.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        job = event.get("job")
        if isinstance(job, dict):
            latest[str(job["job_id"])] = job
    return latest


def latest_by_source_identity(job_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for job in latest_jobs(job_root).values():
        result[str(job["source_identity"])] = job
    return result


def require_job(job_root: Path, job_id: str) -> dict[str, Any]:
    job = latest_jobs(job_root).get(job_id)
    if job is None:
        raise SourceIngestJobError("source-ingest job not found")
    return job


def public_job(job: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": job["schema_version"],
        "job_id": job["job_id"],
        "source_identity": job["source_identity"],
        "source_input_kind": job["source_input_kind"],
        "source_url": job.get("source_url", ""),
        "user_text_sha256": job.get("user_text_sha256", ""),
        "user_image_sha256": job.get("user_image_sha256", ""),
        "source_retention_mode": job["source_retention_mode"],
        "worker_runtime": job.get("worker_runtime", "codex_app"),
        "work_version": job.get("work_version", 0),
        "previous_event_id": job.get("previous_event_id", ""),
        "current_event_id": job.get("current_event_id", ""),
        "attempt_number": int(job.get("attempt_number", 1)),
        "status": job["status"],
        "first_accepted_at": job.get("first_accepted_at", job["accepted_at"]),
        "attempt_accepted_at": job.get("attempt_accepted_at", job["accepted_at"]),
        "accepted_at": job["accepted_at"],
        "target_at": job.get("target_at", job["deadline_at"]),
        "deadline_at": job["deadline_at"],
        "sla_target_seconds": sla_target_seconds(job),
        "enforce_hard_deadline": bool(job.get("enforce_hard_deadline", True)),
        "content_result": job.get("content_result", "pending"),
        "sla_result": job.get("sla_result", "pending"),
        "sla_reason": job.get("sla_reason", ""),
        "overtime_evidence": job.get("overtime_evidence", {}),
        "updated_at": job["updated_at"],
        "current_stage": job["current_stage"],
        "stage_timings": job["stage_timings"],
        "external_wait_seconds": float(job.get("external_wait_seconds") or 0.0),
        "external_wait_evidence": job.get("external_wait_evidence", []),
        "checkpoints": job.get("checkpoints", {}),
        "completed_at": job.get("completed_at", ""),
        "first_accepted_to_completed_seconds": job.get("first_accepted_to_completed_seconds"),
        "attempt_wall_elapsed_seconds": job.get("attempt_wall_elapsed_seconds"),
        "reason": job.get("reason", ""),
        "retry_action": job.get("retry_action", ""),
        "outcome_url": job.get("outcome_url", ""),
        "worker_envelope_path": job.get("worker_envelope_path", ""),
        "dispatch_request_path": job.get("dispatch_request_path", ""),
        "worker_claim": job.get("worker_claim", ""),
        "worker_instance_id": job.get("worker_instance_id", ""),
        "lease_expires_at": job.get("lease_expires_at", ""),
        "staged_input_path": job.get("staged_input_path", ""),
        "recovery_mode": bool(job.get("recovery_mode")),
    }


def empty_stage_timings() -> dict[str, dict[str, Any]]:
    return {
        stage: {"status": "pending", "started_at": "", "completed_at": "", "elapsed_seconds": None}
        for stage in STAGES
    }


def next_stage_after(stage: str) -> str:
    try:
        return STAGES[STAGES.index(stage) + 1]
    except (ValueError, IndexError):
        return ""


def retention_mode(mode: str, intent_text: str, source_input_kind: str = "url") -> str:
    if source_input_kind == "user_provided_image":
        if mode not in {"auto", "summarized_allowed", "original_image_required"}:
            raise SourceIngestJobError("user-provided image must use original_image_required retention, not full_body_required")
        return "original_image_required"
    if mode == "summarized_allowed":
        raise SourceIngestJobError("new text/URL jobs require full_body_required; summarized_allowed is historical only")
    if mode in RETENTION_MODES:
        return mode
    return "full_body_required"


def validate_worker_cas(job: Mapping[str, Any], worker_claim: str, expected_work_version: int) -> None:
    if not worker_claim or worker_claim != job.get("worker_claim"):
        raise SourceIngestJobError("worker claim does not match the durable job")
    if expected_work_version != int(job.get("work_version", 0)):
        raise SourceIngestJobError("stale source-ingest job work_version")


def completed_stage_idempotent(job: Mapping[str, Any], stage: str, stage_status: str) -> bool:
    timing = nested_get(job, ("stage_timings", stage))
    if not isinstance(timing, Mapping):
        return False
    return stage_status == "completed" and timing.get("status") == "completed"


def validate_stage_transition(job: Mapping[str, Any], stage: str, stage_status: str) -> None:
    timings = job.get("stage_timings")
    if not isinstance(timings, Mapping):
        raise SourceIngestJobError("job has no stage timings")
    first_open = None
    for candidate in STAGES:
        timing = timings.get(candidate)
        if not isinstance(timing, Mapping):
            raise SourceIngestJobError(f"job stage timing is missing: {candidate}")
        if timing.get("status") == "completed":
            continue
        first_open = candidate
        break
    if first_open is None:
        raise SourceIngestJobError("all stage timings are already completed")
    if stage != first_open:
        raise SourceIngestJobError(f"stage order violation: expected {first_open} before {stage}")
    timing = timings.get(stage)
    if not isinstance(timing, Mapping):
        raise SourceIngestJobError(f"job stage timing is missing: {stage}")
    if timing.get("status") not in {"pending", "running"}:
        if stage_status == "running" and timing.get("status") == "needs_review":
            return
        raise SourceIngestJobError(f"stage cannot transition from {timing.get('status')}: {stage}")
    if stage_status == "running" and timing.get("status") == "running":
        raise SourceIngestJobError(f"stage is already running: {stage}")


def validate_stage_duration(value: float | None) -> float:
    if value is None:
        raise SourceIngestJobError("completed/blocked stage records require monotonic duration_seconds")
    if value < 0:
        raise SourceIngestJobError("stage duration_seconds must be non-negative")
    return float(value)


def validate_required_stage_timings(job: Mapping[str, Any]) -> None:
    timings = job.get("stage_timings")
    if not isinstance(timings, Mapping):
        raise SourceIngestJobError("job has no stage timings")
    for stage in STAGES:
        timing = timings.get(stage)
        if not isinstance(timing, Mapping) or timing.get("status") != "completed":
            raise SourceIngestJobError(f"cannot complete job before stage timing is completed: {stage}")
        elapsed = timing.get("elapsed_seconds")
        if not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise SourceIngestJobError(f"stage timing is missing a non-negative monotonic duration: {stage}")


def total_stage_elapsed_seconds(timings: Mapping[str, Any]) -> float:
    total = 0.0
    for stage in STAGES:
        timing = timings.get(stage)
        if isinstance(timing, Mapping) and isinstance(timing.get("elapsed_seconds"), (int, float)):
            total += float(timing["elapsed_seconds"])
    return total


def first_url(text: str) -> str:
    match = re.search(r"https?://[^\s<>\"]+", text)
    return match.group(0).rstrip("。)，,") if match else ""


def url_source_identity(url: str) -> str:
    return "url:" + hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()


def canonical_url(value: str) -> str:
    stripped = value.strip()
    without_fragment, _ = urldefrag(stripped)
    parsed = urlsplit(without_fragment)
    if not parsed.scheme or not parsed.netloc:
        return without_fragment.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/") or "/", parsed.query, ""))


def deterministic_job_id(source_identity: str) -> str:
    return "SIJ-" + hashlib.sha256(source_identity.encode("utf-8")).hexdigest()[:16]


def nested_get(value: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def elapsed_seconds(start: str, end: str) -> float:
    return max(0.0, (parse_now(end) - parse_now(start)).total_seconds())


def resolve_inside(root: Path, value: Path) -> Path:
    target = (value if value.is_absolute() else root / value).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SourceIngestJobError(f"refusing path outside project root: {target}") from exc
    return target


def path_key(path: Path) -> str:
    return str(path).replace("\\", "/").rstrip("/").casefold()


def print_json_or_text(payload: Mapping[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{payload.get('status')}: {payload.get('job_id') or payload.get('message')}")


if __name__ == "__main__":
    sys.exit(main())
