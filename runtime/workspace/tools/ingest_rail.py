"""Fixed three-input execution rail over Compiler Worker v2.

There is no second writer or completion ledger. AI receives a typed work order
only when semantic analysis or the native Codex image tool is actually needed.
"""
from __future__ import annotations
import os

import argparse
import copy
import contextlib
import io
import json
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

try:
    from tools.management_exclusion import assert_management_idle
except ModuleNotFoundError:
    from management_exclusion import assert_management_idle  # type: ignore

try:
    from tools import compiler_worker_v2 as worker
    from tools import obsidian_vault_adapter as vault_adapter
    from tools.ingest_plan_contract import VERSION, build_kp, input_fingerprint, plan_schema
    from tools.image_source_contract import visual_analysis_template, VISUAL_TYPES, ANCHOR_KINDS, COVERAGE_DISPOSITIONS
except ModuleNotFoundError:
    import compiler_worker_v2 as worker
    import obsidian_vault_adapter as vault_adapter
    from ingest_plan_contract import VERSION, build_kp, input_fingerprint, plan_schema
    from image_source_contract import visual_analysis_template, VISUAL_TYPES, ANCHOR_KINDS, COVERAGE_DISPOSITIONS

ROOT = Path(__file__).resolve().parents[1]


def invoke(function, **kwargs):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = function(SimpleNamespace(**kwargs))
    result = json.loads(output.getvalue())
    if code or result.get("status") in {"blocked", "needs_review"}:
        raise worker.CompilerWorkerV2Error(json.dumps(result, ensure_ascii=False))
    return result


def paths(root, job_id):
    job = worker.read_json(worker.job_file(root, job_id))
    if not job.get("package_dir"):
        raise worker.CompilerWorkerV2Error("input bundle is not ready; resume source capture")
    package = worker.relative_to_root(root / job["package_dir"], root, "package")
    bundle = worker.read_json(package / "input_bundle.json")
    if any(job.get(key) != bundle.get(key) for key in ("job_id", "source_id", "source_identity", "source_input_kind")):
        raise worker.CompilerWorkerV2Error("job and input bundle identity mismatch")
    return job, package, package / "knowledge_package.json"


def continuation_order(order):
    """Make the agent handoff explicit without weakening any existing gate.

    needs_input describes a program boundary, not a request to the human.
    This is routing metadata, never proof of autonomous execution or completion.
    """
    agent_actions = {
        "retry_capture", "review_original_image", "prepare_semantic_plan",
        "compile_plan", "authorize_initial_image", "authorize_blocking_repair",
        "authorize_text_revision", "generate_image_once", "reconcile_image_result",
        "reconsider_quality_review", "project_and_readback",
    }
    status, action = order.get("status"), order.get("action")
    if status == "completed" and action == "stop" and order.get("completion"):
        continuation = {"owner": "none", "directive": "deliver_verified_result", "task_complete": True}
    elif status in {"ready", "needs_input"} and action in agent_actions:
        continuation = {
            "owner": "agent", "directive": "continue_same_job", "task_complete": False,
            "instructions": "Perform this work order within the user's existing scope, then submit and advance the same job. needs_input means the program needs agent work; it is not a human-input request or permission to end the task. Reconcile existing image actions before any dispatch. Stop only for verified completion or a concrete safety, authority, source, or runtime blocker; report the precise blocker without claiming completion.",
        }
    else:
        continuation = {"owner": "agent", "directive": "report_blocker", "task_complete": False,
                        "instructions": "Inspect and report the concrete blocker. Do not bypass gates, retry image generation blindly, or infer new authority."}
    return {**order, "continuation": continuation}


def work_order(root, job_id):
    return continuation_order(_work_order(root, job_id))


def _work_order(root, job_id):
    pending = worker.read_json(worker.job_file(root, job_id))
    if not pending.get("package_dir"):
        return {"schema_version": "ingest_work_order_v1", "job_id": job_id,
                "source_id": pending["source_id"], "source_kind": pending["source_input_kind"],
                "status": "ready", "action": "retry_capture",
                "instructions": "Resume this same accepted job with advance; do not allocate another source or job."}
    job, package, kp_path = paths(root, job_id)
    bundle = worker.read_json(package / "input_bundle.json")
    base = {"schema_version": "ingest_work_order_v1", "job_id": job_id,
            "source_id": job["source_id"], "source_kind": job["source_input_kind"],
            "package_dir": package.relative_to(root).as_posix(),
            "first_accepted_at": job["first_accepted_at"],
            "enforcement_boundary": "official program entry points; semantic truth still requires actual source/image inspection"}
    if bundle.get("status") == "awaiting_image_analysis":
        return {**base, "status": "needs_input", "action": "review_original_image",
                "original_image": (package / bundle["original_image_path"]).relative_to(root).as_posix(),
                "source_image_sha256": bundle["source_image_sha256"],
                "required_source": "tools/image_source_contract.py:validate_visual_analysis",
                "response_template": visual_analysis_template(image_sha256=bundle["source_image_sha256"], dimensions=bundle["source_image_dimensions"]),
                "allowed_values": {"visual_type": sorted(VISUAL_TYPES), "anchor_kind": sorted(ANCHOR_KINDS), "coverage_disposition": sorted(COVERAGE_DISPOSITIONS), "transcription_confidence": ["high", "medium", "low", "unreadable"], "verification_status": ["image_visible", "externally_verified", "needs_external_verification"]},
                "instructions": "Inspect the entire original image, transcribe every legible region, mark unreadable regions, then perform a second visual check. Do not invent passed evidence.",
                "submit": ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "image-analysis", "--job-id", job_id, "--response-file", "<analysis.json>"]}
    if not kp_path.is_file():
        return {**base, "status": "needs_input", "action": "prepare_semantic_plan",
                "input_fingerprint": input_fingerprint(package),
                "canonical_source": (package / "canonical_source.md").relative_to(root).as_posix(),
                "accepted_source": bundle,
                "response_schema": plan_schema(),
                "required_reads": ["docs/Compiler Worker與Knowledge Package設計及驗收規格.md", "docs/資訊圖生成規格.md", "docs/URL到輸出模板規格.md"],
                "instructions": "Read the complete source. Write coherent standalone page copy before shortening it. Each page must explain actors, terms, conditions and outcomes. Choose structure from actual relationships, not a fixed number of blocks. Obtain isolated page readback and bind reader_review to reader_page_sha256 before submission; source fidelity is a separate check. Submit only schema-defined semantic decisions. Identity, full-body preservation, hashes, statuses, paths, content_map and visible_text are computed. Text/URL evidence uses exact source quotes; image knowledge evidence must name existing IMG-* anchors from image_analysis.json. Do not copy an old package. Existing-context insights require actual note paths and quotations.",
                "submit": ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "submit-plan", "--job-id", job_id, "--response-file", "<plan.json>"]}
    kp = worker.read_json(kp_path)
    worker.validate_job_package_binding(root, kp_path, kp)
    worker.text_revision.recover_action(worker, package)
    if (package / "completion_receipt.json").is_file():
        # This is a real hash/artifact readback, not a status-field shortcut.
        result = invoke(worker.command_project, root=root, kp_file=kp_path, write_vault=False, vault=None)
        receipt = worker.read_json(package / "completion_receipt.json")
        return {**base, "status": "completed", "action": "stop", "completion": result,
                "idea_candidates": receipt.get("idea_candidates"),
                "execution": receipt.get("execution"), "program_events": job.get("rail_events", [])}
    if bundle.get("asset_mode") == "case_evidence":
        if kp.get("infographics"):
            raise worker.CompilerWorkerV2Error("case evidence package cannot retain infographic pages")
        worker.require_case_ingest_plan(job, kp, package)
        return {**base, "status": "ready", "action": "project_and_readback"}
    if not job.get("execution_contract"):
        return {**base, "status": "ready", "action": "compile_plan"}
    worker.require_ingest_plan(job, kp)
    if all(page.get("status") == "receipt_verified" for page in kp.get("infographics", [])):
        return {**base, "status": "ready", "action": "project_and_readback"}
    action_path = worker.image_action_envelope_path(package)
    if action_path.is_file():
        action = worker.read_json(action_path)
        if action.get("payload_sha256") != worker.canonical_payload_sha256(action):
            raise worker.CompilerWorkerV2Error("image action hash mismatch")
        if action.get("state") == "awaiting_imagegen":
            return {**base, "status": "needs_input", "action": "reconcile_image_result",
                    "action_envelope": action_path.relative_to(root).as_posix(),
                    "expected_asset": action["expected_asset"],
                    "instructions": "An image action is already authorized. Inspect its runtime result and PNG before continuing; do not issue another image request on restart.",
                    "response_contract": action["response_contract"],
                    "response_template": image_response_template(root, action)}
    page = next(page for page in kp["infographics"] if page.get("status") != "receipt_verified")
    ledger_path = worker.image_attempt_ledger_path(package)
    ledger = worker.read_json(ledger_path) if ledger_path.is_file() else {}
    attempts = ledger.get("pages", {}).get(page["page_id"], {}).get("attempts", [])
    if attempts:
        latest = attempts[-1]
        if (
            latest.get("status") == "blocking_failure"
            and latest.get("failure_code") == "reverse_review_failed"
        ):
            try:
                reconsideration = worker.image_quality_reconsideration_template(
                    root,
                    kp_path,
                    page["page_id"],
                )
            except worker.CompilerWorkerV2Error:
                reconsideration = None
            if reconsideration and not reconsideration.get("already_reconsidered"):
                return {
                    **base,
                    "status": "needs_input",
                    "action": "reconsider_quality_review",
                    "page_id": page["page_id"],
                    "review_template": reconsideration,
                    "instructions": (
                        "This is a same-PNG core re-review after a quality-only failure. "
                        "Inspect the existing asset; do not call image generation or change the locked plan."
                    ),
                    "submit": [
                        "python", "-B", "tools/ingest_rail.py", "--root", str(root),
                        "image-reconsider", "--job-id", job_id,
                        "--response-file", "<core-review.json>",
                    ],
                }
        if (kp.get("revision") or {}).get("mode") == "same_job_text_revision":
            return {**base, "status": "blocked", "action": "inspect_failed_attempt", "page_id": page["page_id"], "reason": "the explicitly authorized text edit was used; preserve its cumulative history"}
        if len(attempts) == 1 and latest.get("status") == "blocking_failure" and latest.get("failure_code") in worker.IMAGEGEN_REPAIRABLE_FAILURES:
            return {**base, "status": "ready", "action": "authorize_blocking_repair", "page_id": page["page_id"], "failure": latest["failure_code"]}
        return {**base, "status": "blocked", "action": "inspect_failed_attempt", "page_id": page["page_id"], "reason": "attempts cannot be retried automatically"}
    if (kp.get("revision") or {}).get("mode") == "same_job_text_revision":
        return {**base, "status": "ready", "action": "authorize_text_revision", "page_id": page["page_id"]}
    return {**base, "status": "ready", "action": "authorize_initial_image", "page_id": page["page_id"]}


def measured(root, job_id, stage, operation, /, **kwargs):
    """Record actual program duration inside the existing job, including failures."""
    path = worker.job_file(root, job_id)
    job = worker.read_json(path)
    event_id = uuid.uuid4().hex
    event = {"id": event_id, "stage": stage, "started_at": worker.iso_now(), "status": "started"}
    job.setdefault("rail_events", []).append(event)
    worker.write_v2_job(root, job)
    started = time.perf_counter()
    try:
        result = operation(**kwargs)
    except Exception:
        status = "failed"
        raise
    else:
        status = "completed"
        return result
    finally:
        job = worker.read_json(path)
        row = next(row for row in job["rail_events"] if row["id"] == event_id)
        row.update(status=status, ended_at=worker.iso_now(), program_seconds=round(time.perf_counter() - started, 6))
        worker.write_v2_job(root, job)


def image_response_template(root, action):
    asset = worker.relative_to_root(root / action["expected_asset"], root, "authorized PNG")
    png_hash = worker.sha256_bytes(asset.read_bytes()) if asset.is_file() else ""
    result = {
        "schema_version": worker.V2_IMAGEGEN_RESPONSE_VERSION,
        **{key: action[key] for key in ("action_identity", "attempt_id", "provider", "model", "generation_request")},
        "asset_path": action["expected_asset"], "png_sha256": png_hash,
        "runtime_tool": "", "imagegen_dispatch_evidence": "", "outcome": "",
        "semantic_evidence": "", "failure_code": "",
        "page_number_readback": "", "footer_text_readback": "",
        "semantic_readback": {"inspection_method": "independent_image_readback",
            "formal_ingestion_allowed": None, "spec_read_receipt": {}, "review_axes": {}, "reader_review": "",
            "reader_comprehension": {"answer": "", "relationship_explanation": "", "unexplained_terms": None, "missing_context": None, "misleading_relations": None},
            "inspector_identity": "", "inspected_png_sha256": png_hash,
            "inspected_prompt_sha256": worker.sha256_text(action["prompt"]),
            "visible_text_readback": [], "unexpected_visible_text": None,
            "internal_process_text_detected": None},
    }
    if action.get("attempt_kind") in {"blocking_repair", "user_authorized_text_revision"}:
        result["imagegen_tool_arguments"] = {}  # Populate from the actual tool call, not the template.
    if action.get("attempt_kind") == "user_authorized_visual_repair":
        result["edit_prompt_sha256"] = action["edit_prompt_sha256"]
        result["semantic_readback"]["restricted_edit_review"] = {
            "all_text_preserved": None, "only_authorized_icons_removed": None,
            "reference_png_sha256": action["repair_authorization"]["reference_png_sha256"],
        }
    if action.get("attempt_kind") == "user_authorized_text_revision":
        result["semantic_readback"]["text_revision_review"] = {
            "accepted_style_preserved": None, "new_text_complete": None, "evidence": ""}
    return result


def advance(root, job_id, vault):
    vault = vault if vault.is_absolute() else root / vault
    # A bounded loop; only retry_capture may call the URL acquisition adapters.
    for _ in range(4):
        order = work_order(root, job_id)
        action = order["action"]
        if action == "retry_capture":
            measured(root, job_id, "capture", capture, root=root, job=worker.read_json(worker.job_file(root, job_id)))
            continue
        _, _, kp_path = paths(root, job_id)
        if action == "compile_plan":
            measured(root, job_id, "compile_plan", invoke, function=worker.command_compile_infographics,
                     root=root, kp_file=kp_path, plan_repair_file=None)
        elif action in {"authorize_initial_image", "authorize_blocking_repair", "authorize_text_revision"}:
            result = measured(root, job_id, action, invoke, function=worker.command_imagegen_authorize,
                              root=root, kp_file=kp_path, page_id=order["page_id"],
                              reason={"authorize_initial_image": "initial", "authorize_blocking_repair": "blocking_repair", "authorize_text_revision": "user_authorized_text_revision"}[action],
                              failure=order.get("failure"))
            envelope = worker.read_json(root / result["action_envelope"])
            return continuation_order({**order, "status": "needs_input", "action": "generate_image_once",
                    "imagegen": envelope, "response_template": image_response_template(root, envelope),
                    "next": "For blocking_repair, pass imagegen.imagegen exactly imagegen_tool_arguments (not the original prompt), and copy the actual dispatched arguments into the response. Inspect the actual PNG, run inspect for its bound response template, and submit image-result. Re-running advance requires reconciliation, never another generation."})
        elif action == "project_and_readback":
            measured(root, job_id, action, invoke, function=worker.command_project,
                     root=root, kp_file=kp_path, write_vault=True, vault=vault)
        else:
            return order
    raise worker.CompilerWorkerV2Error("unexpected execution loop; inspect original job")


def refresh_case_evidence_before_plan(root, job_id):
    """Re-stage corrected case evidence only before its immutable plan is locked.

    URL capture and source identity remain untouched.  This narrowly repairs a
    case-manifest staging error before a semantic plan exists; afterwards the
    normal input fingerprint forces the planner to bind the corrected assets.
    """
    job, package, kp_path = paths(root, job_id)
    if job.get("execution_contract") or kp_path.exists() or (package / "completion_receipt.json").exists():
        raise worker.CompilerWorkerV2Error("case evidence is already plan-locked or completed; use evidence-bound repair")
    bundle_path = package / "input_bundle.json"
    bundle = worker.read_json(bundle_path)
    if bundle.get("asset_mode") != "case_evidence" or job.get("asset_mode") != "case_evidence":
        raise worker.CompilerWorkerV2Error("this job has no case evidence staging to refresh")
    capture = job.get("rail_capture") if isinstance(job.get("rail_capture"), dict) else {}
    raw_manifest = str(capture.get("case_evidence_file") or "")
    if not raw_manifest:
        raise worker.CompilerWorkerV2Error("case evidence source manifest is missing from the accepted job")
    manifest_path = worker.relative_to_root(root / raw_manifest, root, "case evidence manifest")
    try:
        manifest, _ = worker.validate_case_evidence_manifest(
            root, manifest_path, primary_url=str(bundle.get("normalized_url") or bundle.get("source_url") or "")
        )
    except worker.CaseEvidenceError as exc:
        raise worker.CompilerWorkerV2Error(f"case evidence manifest invalid: {exc}") from exc
    quote = str((manifest.get("creator") or {}).get("ai_evidence_quote") or "")
    canonical = (package / "canonical_source.md").read_text(encoding="utf-8")
    if quote not in canonical:
        raise worker.CompilerWorkerV2Error("case evidence creator AI quote is not exact in the accepted canonical source")
    previous = str(bundle.get("case_evidence_source_manifest_sha256") or "")
    try:
        binding = worker.stage_case_evidence_manifest(
            root, manifest_path, package, primary_url=str(bundle.get("normalized_url") or bundle.get("source_url") or "")
        )
    except worker.CaseEvidenceError as exc:
        raise worker.CompilerWorkerV2Error(f"case evidence staging failed: {exc}") from exc
    bundle.update(binding)
    worker.write_json(bundle_path, bundle)
    review_path = package / "review_packet.json"
    if review_path.is_file():
        review = worker.read_json(review_path)
        review.update(binding)
        worker.write_json(review_path, review)
    job["case_evidence_manifest_path"] = (package / binding["case_evidence_manifest_path"]).relative_to(root).as_posix()
    job.setdefault("case_evidence_pre_lock_refreshes", []).append({
        "previous_source_manifest_sha256": previous,
        "source_manifest_sha256": binding["case_evidence_source_manifest_sha256"],
        "reason": "corrected source manifest before semantic-plan lock",
    })
    worker.write_v2_job(root, job)
    return work_order(root, job_id)


def repair_case_plan_before_write(root, job_id, response_path):
    """Replace only a failed case KP before any writer result exists.

    A case plan is immutable once its writer has started.  The only recovery
    window is a recorded `project_and_readback` preflight failure with no
    completion receipt; the locked case evidence scope must still match.
    """
    job, package, kp_path = paths(root, job_id)
    contract = job.get("execution_contract") if isinstance(job.get("execution_contract"), dict) else {}
    if contract.get("mode") != "case_evidence_zero_imagegen":
        raise worker.CompilerWorkerV2Error("case plan repair requires a locked case-evidence job")
    if (package / "completion_receipt.json").exists():
        raise worker.CompilerWorkerV2Error("case plan is completed; use evidence-bound repair")
    if (
        job.get("status") == "committing"
        or any(job.get(field) for field in ("commit_adapter", "commit_outcome", "commit_reader_quality_audit"))
        or job.get("commit_stage") == "source_id_rebound"
    ):
        raise worker.CompilerWorkerV2Error(
            "case plan writer has started; use the recorded source-ID rebind recovery instead"
        )
    events = job.get("rail_events") if isinstance(job.get("rail_events"), list) else []
    if not any(event.get("stage") == "project_and_readback" and event.get("status") == "failed" for event in events if isinstance(event, dict)):
        raise worker.CompilerWorkerV2Error("case plan repair requires a recorded pre-writer project failure")
    previous = worker.read_json(kp_path)
    response = worker.read_json(response_path)
    source = previous.get("source") if isinstance(previous.get("source"), dict) else {}
    bundle_path = package / "input_bundle.json"
    bundle = worker.read_json(bundle_path)
    binding_fields = (
        "asset_mode",
        "case_evidence_manifest_path",
        "case_evidence_manifest_sha256",
        "case_evidence_source_manifest_sha256",
    )
    binding_repaired = any(bundle.get(field) != source.get(field) for field in binding_fields)
    if binding_repaired:
        # This repair window is deliberately narrower than a source refresh:
        # only a failed pre-writer revalidation whose locked scope still
        # validates may bring its mirrored input bundle up to that scope.
        scope = worker.case_execution_scope(job, previous, package)
        if worker.canonical_payload_sha256(scope) != contract.get("scope_sha256"):
            raise worker.CompilerWorkerV2Error("case plan repair cannot reconcile a changed case-evidence scope")
        prior_fingerprint = input_fingerprint(package)
        if response.get("input_fingerprint") != prior_fingerprint:
            raise worker.CompilerWorkerV2Error("case plan repair response does not bind the pre-repair input bundle")
        bundle.update({field: source.get(field) for field in binding_fields})
        worker.write_json(bundle_path, bundle)
        review_path = package / "review_packet.json"
        if review_path.is_file():
            review = worker.read_json(review_path)
            review.update({field: source.get(field) for field in binding_fields})
            worker.write_json(review_path, review)
        response["input_fingerprint"] = input_fingerprint(package)
    replacement = build_kp(response, package, root=root)
    # Revalidation provenance is a compiler-owned immutable binding.  A
    # pre-writer semantic-plan repair may update the application decision, but
    # cannot erase the original receipt and selection-contract evidence.
    if isinstance(previous.get("revision"), dict):
        replacement["revision"] = copy.deepcopy(previous["revision"])
    errors = worker.validate_kp(replacement, package)
    if errors:
        raise worker.CompilerWorkerV2Error("case plan repair preflight failed: " + "; ".join(errors))
    worker.require_case_ingest_plan(job, replacement, package)
    worker.write_json(kp_path, replacement)
    job.setdefault("case_plan_repairs", []).append({
        "reason": "replace failed pre-writer case KP after full validation",
        "previous_coverage_sha256": worker.canonical_payload_sha256({"units": previous.get("coverage_units")}),
        "replacement_coverage_sha256": worker.canonical_payload_sha256({"units": replacement.get("coverage_units")}),
        "previous_application_sha256": worker.canonical_payload_sha256(previous.get("application")),
        "replacement_application_sha256": worker.canonical_payload_sha256(replacement.get("application")),
        "revision_provenance_preserved": isinstance(previous.get("revision"), dict),
        "input_bundle_binding_repaired": binding_repaired,
    })
    worker.write_v2_job(root, job)
    return work_order(root, job_id)


def rebounded_case_plan(plan, old_source_id, new_source_id):
    """Return a copy whose local knowledge-point references use the new ID."""
    updated = copy.deepcopy(plan)
    old_fragment = old_source_id.removeprefix("KS-")
    new_fragment = new_source_id.removeprefix("KS-")
    kp_map = {}
    for item in updated.get("knowledge", []):
        if not isinstance(item, dict) or not isinstance(item.get("kp_id"), str):
            continue
        prior = item["kp_id"]
        if old_fragment in prior:
            current = prior.replace(old_fragment, new_fragment)
        elif new_fragment in prior:
            current = prior
        else:
            raise worker.CompilerWorkerV2Error("source-ID rebind knowledge point does not match its locked source ID")
        kp_map[prior] = current
        item["kp_id"] = current
    if len(set(kp_map.values())) != len(kp_map):
        raise worker.CompilerWorkerV2Error("source-ID rebind would duplicate knowledge-point IDs")
    for item in updated.get("relations", []):
        if not isinstance(item, dict):
            continue
        for field in ("source_kp", "target_kp"):
            value = item.get(field)
            if not isinstance(value, str):
                continue
            if value in kp_map:
                item[field] = kp_map[value]
            elif old_fragment in value:
                item[field] = value.replace(old_fragment, new_fragment)
    return updated

def rebind_checkpoint_artifacts(root, paths):
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": worker.sha256_bytes(path.read_bytes()) if path.is_file() else "",
        }
        for path in paths
    ]

def vault_relative_checkpoint_hashes(checkpoint, root, vault):
    vault_prefix = vault.relative_to(root)
    return {
        Path(item["path"]).relative_to(vault_prefix).as_posix(): item["sha256"]
        for item in checkpoint.get("artifact_hashes", [])
        if isinstance(item, dict) and item.get("path") and item.get("sha256")
    }

def _rebind_recovery_tree(path):
    """Return a deterministic hash map without following links outside its root."""
    if path.is_symlink():
        raise worker.CompilerWorkerV2Error("rebind recovery refuses symbolic-link artifacts")
    if path.is_file():
        return {"": worker.sha256_bytes(path.read_bytes())}
    if not path.is_dir():
        raise worker.CompilerWorkerV2Error("rebind recovery artifact is neither file nor directory")
    entries = {}
    for child in sorted(path.rglob("*")):
        if child.is_dir():
            continue
        if child.is_symlink():
            raise worker.CompilerWorkerV2Error("rebind recovery refuses symbolic-link children")
        entries[child.relative_to(path).as_posix()] = worker.sha256_bytes(child.read_bytes())
    if not entries:
        raise worker.CompilerWorkerV2Error("rebind recovery refuses an empty artifact directory")
    return entries

def _rebind_recovery_expected_tree(checkpoint, root, vault, source_relative):
    """Map one recorded old Vault artifact to its frozen file hashes."""
    source_prefix = source_relative.rstrip("/")
    vault_prefix = vault.relative_to(root).as_posix().rstrip("/")
    expected = {}
    for item in checkpoint.get("artifact_hashes", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        digest = item.get("sha256")
        if not isinstance(path, str) or not isinstance(digest, str) or not digest:
            continue
        if not path.startswith(vault_prefix + "/"):
            continue
        relative = path[len(vault_prefix) + 1:]
        if relative == source_prefix:
            expected[""] = digest
        elif relative.startswith(source_prefix + "/"):
            expected[relative[len(source_prefix) + 1:]] = digest
    return expected

def _rebind_recovery_vault_path(vault, relative, label):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise worker.CompilerWorkerV2Error(f"rebind recovery {label} path is invalid")
    candidate = (vault / relative).resolve()
    if not candidate.is_relative_to(vault.resolve()):
        raise worker.CompilerWorkerV2Error(f"rebind recovery {label} escapes the Vault")
    return candidate

def archive_rebound_partial_artifacts(root, job_id, vault, *, apply=False):
    """Archive only recorded rebind remnants before a fresh writer can duplicate them.

    A normal, incomplete rebind has frozen pre-move hashes and is archived as
    part of its continuation.  A completed legacy job has no such hashes, so
    this routine will only identify explicitly old-ID Markdown remnants and
    requires an explicit ``--apply`` command before moving them.
    """
    job, package, _ = paths(root, job_id)
    vault = vault if vault.is_absolute() else root / vault
    vault = worker.relative_to_root(vault, root, "vault")
    checkpoint = job.get("source_id_rebind_checkpoint")
    if not isinstance(checkpoint, dict):
        raise worker.CompilerWorkerV2Error("rebind recovery requires a recorded source-ID checkpoint")
    rebound = checkpoint.get("writer_rollback")
    moved = rebound.get("moved") if isinstance(rebound, dict) else None
    if not isinstance(moved, list):
        raise worker.CompilerWorkerV2Error("rebind recovery requires the recorded moved-artifact list")
    old_source_id = str(checkpoint.get("old_source_id") or "")
    new_source_id = str(checkpoint.get("new_source_id") or "")
    if not old_source_id or not new_source_id or old_source_id == new_source_id:
        raise worker.CompilerWorkerV2Error("rebind recovery checkpoint IDs are invalid")
    prior_recovery = checkpoint.get("partial_artifact_recovery")
    if isinstance(prior_recovery, dict) and prior_recovery.get("status") == "archived":
        return {**prior_recovery, "status": "already_archived"}

    completed = (package / "completion_receipt.json").is_file()
    canonical_paths = []
    if completed:
        adapter = job.get("commit_adapter") if isinstance(job.get("commit_adapter"), dict) else {}
        for key in ("source_note", "application_note"):
            value = adapter.get(key)
            if isinstance(value, str) and value:
                canonical_paths.append(worker.relative_to_root(root / value, root, f"canonical {key}"))
        for key in ("knowledge_points", "assets"):
            values = adapter.get(key) if isinstance(adapter.get(key), list) else []
            for value in values:
                if isinstance(value, str) and value:
                    canonical_paths.append(worker.relative_to_root(root / value, root, f"canonical {key}"))

    def is_canonical(path):
        return any(path == canonical or canonical.is_relative_to(path) for canonical in canonical_paths)

    candidates = []
    skipped = []
    seen = set()
    for item in moved:
        if not isinstance(item, dict):
            raise worker.CompilerWorkerV2Error("rebind recovery moved-artifact record is invalid")
        source_relative = item.get("from")
        active_relative = item.get("to")
        source = _rebind_recovery_vault_path(vault, source_relative, "source")
        active = _rebind_recovery_vault_path(vault, active_relative, "active")
        active_key = active.as_posix().lower()
        if active_key in seen:
            raise worker.CompilerWorkerV2Error("rebind recovery records a duplicate active artifact")
        seen.add(active_key)
        if completed and is_canonical(active):
            skipped.append({"path": active_relative, "reason": "current_canonical_artifact"})
            continue
        if not active.exists():
            skipped.append({"path": active_relative, "reason": "already_absent"})
            continue
        actual_tree = _rebind_recovery_tree(active)
        expected_tree = _rebind_recovery_expected_tree(checkpoint, root, vault, source_relative)
        if expected_tree:
            if actual_tree != expected_tree:
                raise worker.CompilerWorkerV2Error(
                    "rebind recovery active artifact hash differs from its frozen partial artifact"
                )
            evidence = "frozen_partial_hash"
        else:
            # The one historic job predates frozen rebind hashes.  Never use
            # filename alone: only a Markdown record which still declares the
            # prior source ID is safely identifiable as its stale remnant.
            if not completed or not checkpoint.get("legacy_recovery") or not active.is_file():
                raise worker.CompilerWorkerV2Error("rebind recovery lacks frozen artifact hashes")
            text = active.read_text(encoding="utf-8", errors="strict")
            if not re.search(rf"(?m)^(?:source_id|application_id):\s*\"?(?:APP-)?{re.escape(old_source_id)}\b", text):
                skipped.append({"path": active_relative, "reason": "legacy_identity_not_proven"})
                continue
            evidence = "legacy_declared_old_id"
        candidates.append({
            "source_path": source_relative,
            "active_path": active_relative,
            "active": active,
            "hashes": actual_tree,
            "evidence": evidence,
        })

    recovery_root = package / "rebind-recovery" / f"{old_source_id.lower()}-to-{new_source_id.lower()}"
    manifest = recovery_root / "manifest.json"
    summary = {
        "schema_version": "rebind_partial_artifact_recovery_v1",
        "job_id": job_id,
        "old_source_id": old_source_id,
        "new_source_id": new_source_id,
        "completed_legacy_job": completed,
        "archive_root": recovery_root.relative_to(root).as_posix(),
        "manifest": manifest.relative_to(root).as_posix(),
        "artifacts": [
            {
                "source_path": item["source_path"],
                "active_path": item["active_path"],
                "archive_path": (recovery_root / "vault" / item["active_path"]).relative_to(root).as_posix(),
                "hashes": item["hashes"],
                "evidence": item["evidence"],
            }
            for item in candidates
        ],
        "skipped": skipped,
    }
    if not apply:
        return {"status": "would_archive" if candidates else "nothing_to_archive", **summary}
    if not candidates:
        return {"status": "nothing_to_archive", **summary}

    moved_paths = []
    try:
        for item in candidates:
            destination = recovery_root / "vault" / item["active_path"]
            if destination.exists():
                raise worker.CompilerWorkerV2Error("rebind recovery archive destination already exists")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item["active"]), str(destination))
            moved_paths.append((item["active"], destination))
        payload = {**summary, "status": "archived", "archived_at": worker.iso_now()}
        worker.write_json(manifest, payload)
    except Exception:
        for source, destination in reversed(moved_paths):
            if destination.exists() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        raise
    checkpoint["partial_artifact_recovery"] = {
        "status": "archived",
        "manifest": manifest.relative_to(root).as_posix(),
        "artifact_count": len(candidates),
        "archived_at": payload["archived_at"],
    }
    job["source_id_rebind_checkpoint"] = checkpoint
    worker.write_v2_job(root, job)
    return payload

def update_rebound_package_source_id(package, old_source_id, new_source_id):
    for name in ("input_bundle.json", "source_capture_metadata.json", "review_packet.json", "receipt.json"):
        path = package / name
        if not path.is_file():
            continue
        payload = worker.read_json(path)
        value = payload.get("source_id")
        if value == old_source_id:
            payload["source_id"] = new_source_id
        elif value not in (None, "", new_source_id):
            raise worker.CompilerWorkerV2Error(f"source-ID rebind package field conflicts in {name}")
        worker.write_json(path, payload)

def finish_case_source_id_rebind(root, job_id, response_path, vault, checkpoint):
    """Idempotently complete the compiler side after the vault move is durable."""
    job, package, kp_path = paths(root, job_id)
    old_source_id = str(checkpoint["old_source_id"])
    new_source_id = str(checkpoint["new_source_id"])
    if str(job.get("source_identity") or "") != str(checkpoint.get("source_identity") or ""):
        raise worker.CompilerWorkerV2Error("source-ID rebind checkpoint source identity mismatch")
    recovery = archive_rebound_partial_artifacts(root, job_id, vault, apply=True)
    if recovery.get("status") in {"archived", "already_archived"}:
        checkpoint["partial_artifact_recovery"] = {
            "status": "archived",
            "manifest": recovery.get("manifest", ""),
            "artifact_count": len(recovery.get("artifacts", [])),
            "archived_at": recovery.get("archived_at", ""),
        }
    plan = rebounded_case_plan(worker.read_json(response_path), old_source_id, new_source_id)
    update_rebound_package_source_id(package, old_source_id, new_source_id)
    plan["input_fingerprint"] = input_fingerprint(package)
    replacement = build_kp(plan, package, root=root)
    errors = worker.validate_kp(replacement, package)
    if errors:
        raise worker.CompilerWorkerV2Error("source-ID rebind rebuilt plan failed: " + "; ".join(errors))
    job["source_id"] = new_source_id
    worker.require_case_ingest_plan(job, replacement, package)
    worker.write_json(response_path, plan)
    worker.write_json(kp_path, replacement)
    for field in ("commit_adapter", "commit_outcome", "commit_reader_quality_audit", "outcome_recovery"):
        job.pop(field, None)
    job.update({"status": "running", "commit_stage": "source_id_rebound", "updated_at": worker.iso_now()})
    checkpoint["state"] = "completed"
    job["source_id_rebind_checkpoint"] = checkpoint
    rebinds = job.setdefault("source_id_rebinds", [])
    if not any(
        item.get("old_source_id") == old_source_id and item.get("new_source_id") == new_source_id
        for item in rebinds if isinstance(item, dict)
    ):
        rebinds.append({
            "old_source_id": old_source_id,
            "new_source_id": new_source_id,
            "source_identity": job["source_identity"],
            "writer_rollback": checkpoint.get("writer_rollback", {}),
        })
    worker.write_v2_job(root, job)
    restored = worker.generate_incremental_outcome(root, vault, old_source_id)
    return {"rebind": checkpoint.get("writer_rollback", {}), "recovery": recovery, "restored_prior_outcome": restored,
            "next": advance(root, job_id, vault)}

def rebind_uncompleted_case_source_id(root, job_id, response_path, vault):
    """Repair one discovered source-ID collision without changing its source or case.

    The semantic plan, canonical source, case evidence, job ID, and research
    reservation stay the same.  Only the colliding human-facing source ID and
    its derived Vault paths are rebound before the completion receipt exists.
    """
    job, package, _ = paths(root, job_id)
    if (package / "completion_receipt.json").exists():
        raise worker.CompilerWorkerV2Error("completed cases cannot rebind their source ID")
    if not isinstance(job.get("execution_contract"), dict) or job["execution_contract"].get("mode") != "case_evidence_zero_imagegen":
        raise worker.CompilerWorkerV2Error("source-ID rebind requires a locked case-evidence job")
    source_identity = str(job.get("source_identity") or "")
    if not source_identity:
        raise worker.CompilerWorkerV2Error("source-ID rebind job identity is incomplete")
    vault = vault if vault.is_absolute() else root / vault
    vault = worker.relative_to_root(vault, root, "vault")
    checkpoint = job.get("source_id_rebind_checkpoint")
    # Recovery for the one pre-checkpoint partial run that exposed this bug.
    # New rebinds always create the checkpoint before their first vault change.
    if not isinstance(checkpoint, dict) and job.get("commit_stage") == "source_id_rebound":
        historic = next(
            (item for item in reversed(job.get("source_id_rebinds", []))
             if isinstance(item, dict) and item.get("new_source_id") == job.get("source_id")),
            None,
        )
        if not historic:
            raise worker.CompilerWorkerV2Error("source-ID rebind recovery lacks its original ID record")
        checkpoint = {
            "schema_version": "source_id_rebind_checkpoint_v1",
            "state": "vault_rebound",
            "old_source_id": historic["old_source_id"],
            "new_source_id": historic["new_source_id"],
            "source_identity": source_identity,
            "response_file": response_path.relative_to(root).as_posix(),
            "legacy_recovery": True,
            "writer_rollback": historic.get("writer_rollback", {}),
        }
        job["source_id_rebind_checkpoint"] = checkpoint
        worker.write_v2_job(root, job)

    if isinstance(checkpoint, dict):
        if checkpoint.get("source_identity") != source_identity:
            raise worker.CompilerWorkerV2Error("source-ID rebind checkpoint source identity mismatch")
        if checkpoint.get("response_file") != response_path.relative_to(root).as_posix():
            raise worker.CompilerWorkerV2Error("source-ID rebind must resume with its original semantic plan")
        state = checkpoint.get("state")
        if state == "prepared":
            rebound = vault_adapter.rebind_uncompleted_case_source_id(
                vault,
                source_identity_value=source_identity,
                old_source_id=str(checkpoint["old_source_id"]),
                new_source_id=str(checkpoint["new_source_id"]),
                source_note=worker.relative_to_root(root / checkpoint["source_note"], root, "source note"),
                application_note=worker.relative_to_root(root / checkpoint["application_note"], root, "application note"),
                knowledge_points=[worker.relative_to_root(root / path, root, "knowledge point") for path in checkpoint["knowledge_points"]],
                case_asset_dir=worker.relative_to_root(root / checkpoint["case_asset_dir"], root, "case asset directory"),
                artifact_hashes=vault_relative_checkpoint_hashes(checkpoint, root, vault),
            )
            checkpoint["state"] = "vault_rebound"
            checkpoint["writer_rollback"] = rebound
            job["source_id_rebind_checkpoint"] = checkpoint
            worker.write_v2_job(root, job)
        elif state not in {"vault_rebound", "completed"}:
            raise worker.CompilerWorkerV2Error("source-ID rebind checkpoint has an unsupported state")
        return finish_case_source_id_rebind(root, job_id, response_path, vault, checkpoint)

    old_source_id = str(job.get("source_id") or "")
    if not old_source_id or not worker.reserved_source_id_conflict(root, old_source_id, source_identity, job_id):
        raise worker.CompilerWorkerV2Error("source-ID rebind requires a real conflicting registry or job owner")
    new_source_id = next_source_id(root)
    if worker.reserved_source_id_conflict(root, new_source_id, source_identity, job_id):
        raise worker.CompilerWorkerV2Error("computed replacement source ID is already reserved")
    adapter_record = job.get("commit_adapter")
    if not isinstance(adapter_record, dict):
        raise worker.CompilerWorkerV2Error("source-ID rebind requires a recorded incomplete writer result")

    def resolve_recorded(value, label):
        if not isinstance(value, str) or not value:
            raise worker.CompilerWorkerV2Error(f"source-ID rebind is missing recorded {label}")
        return worker.relative_to_root(root / value, root, label)

    source_note = resolve_recorded(adapter_record.get("source_note"), "source note")
    application_note = resolve_recorded(adapter_record.get("application_note"), "application note")
    knowledge_points = [resolve_recorded(item, "knowledge point") for item in adapter_record.get("knowledge_points", [])]
    assets = [resolve_recorded(item, "case evidence asset") for item in adapter_record.get("assets", [])]
    if not knowledge_points or not assets:
        raise worker.CompilerWorkerV2Error("source-ID rebind requires recorded knowledge points and case evidence")
    case_asset_dir = assets[0].parent
    if any(path.parent != case_asset_dir for path in assets):
        raise worker.CompilerWorkerV2Error("source-ID rebind case evidence is not stored in one directory")

    plan = worker.read_json(response_path)
    if plan.get("input_fingerprint") != input_fingerprint(package):
        raise worker.CompilerWorkerV2Error("source-ID rebind semantic plan no longer matches the accepted package")
    candidate = rebounded_case_plan(plan, old_source_id, new_source_id)
    candidate_kp = build_kp(candidate, package, root=root)
    candidate_kp["source"]["source_id"] = new_source_id
    errors = worker.validate_kp(candidate_kp, package)
    if errors:
        raise worker.CompilerWorkerV2Error("source-ID rebind preflight failed: " + "; ".join(errors))
    worker.require_case_ingest_plan(job, candidate_kp, package)
    checkpoint = {
        "schema_version": "source_id_rebind_checkpoint_v1",
        "state": "prepared",
        "old_source_id": old_source_id,
        "new_source_id": new_source_id,
        "source_identity": source_identity,
        "response_file": response_path.relative_to(root).as_posix(),
        "source_note": source_note.relative_to(root).as_posix(),
        "application_note": application_note.relative_to(root).as_posix(),
        "knowledge_points": [path.relative_to(root).as_posix() for path in knowledge_points],
        "case_asset_dir": case_asset_dir.relative_to(root).as_posix(),
        "artifact_hashes": rebind_checkpoint_artifacts(root, [source_note, application_note, *knowledge_points, *assets]),
    }
    job["source_id_rebind_checkpoint"] = checkpoint
    worker.write_v2_job(root, job)
    rebound = vault_adapter.rebind_uncompleted_case_source_id(
        vault,
        source_identity_value=source_identity,
        old_source_id=old_source_id,
        new_source_id=new_source_id,
        source_note=source_note,
        application_note=application_note,
        knowledge_points=knowledge_points,
        case_asset_dir=case_asset_dir,
        artifact_hashes=vault_relative_checkpoint_hashes(checkpoint, root, vault),
    )
    checkpoint["state"] = "vault_rebound"
    checkpoint["writer_rollback"] = rebound
    job["source_id_rebind_checkpoint"] = checkpoint
    worker.write_v2_job(root, job)
    return finish_case_source_id_rebind(root, job_id, response_path, vault, checkpoint)

def next_source_id(root):
    ids = []
    registry = root / os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault") / "_registry" / "sources.jsonl"
    if registry.is_file():
        for line in registry.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.append(str(json.loads(line).get("source_id", "")))
    for path in worker.job_root(root).glob("*.json"):
        ids.append(str(worker.read_json(path).get("source_id", "")))
    values = [int(match.group(1)) for value in ids if (match := re.fullmatch(r"KS-VS-(\d+)", value))]
    return f"KS-VS-{max(values, default=0) + 1:04d}"


def start(args):
    root = args.root.resolve()
    # Extension-based routing is explicit; no PDF/video/OCR summary masquerades as text.
    kind = "url" if args.url else "image" if args.image_file else "text"
    if kind == "text" and args.text_file.suffix.lower() not in {".txt", ".md", ".markdown"}:
        raise worker.CompilerWorkerV2Error("text input must be complete UTF-8 .txt/.md/.markdown; unsupported format needs an explicit complete-source conversion")
    source_kind = {"url": "url", "text": "user_provided_text", "image": "user_provided_image"}[kind]
    supplied_case_evidence = getattr(args, "case_evidence_file", None)
    if supplied_case_evidence and kind != "url":
        raise worker.CompilerWorkerV2Error("case evidence is only supported for a URL input")
    normalized_url = worker.normalize_url(args.url) if kind == "url" else ""
    case_evidence_file = (
        worker.relative_to_root(supplied_case_evidence, root, "case evidence manifest")
        if supplied_case_evidence
        else None
    )
    path = None
    if kind != "url":
        path = worker.relative_to_root(args.text_file if kind == "text" else args.image_file, root, "source input")
        raw = path.read_bytes()
        if kind == "text" and not raw.decode("utf-8-sig").strip():
            raise worker.CompilerWorkerV2Error("empty text input")
        if kind == "image":
            worker.inspect_image(path)
    identity = source_kind + ":" + (worker.sha256_text(normalized_url) if kind == "url" else worker.sha256_bytes(raw))
    existing = worker.latest_v2_job_for_source(root, identity)
    youtube_capture = None
    if existing:
        if (args.source_id and args.source_id != existing["source_id"]) or (args.job_id and args.job_id != existing["job_id"]):
            raise worker.CompilerWorkerV2Error("source already has an owner; use its original job and source IDs")
        if existing.get("package_dir"):
            return work_order(root, existing["job_id"])
        job = existing
    else:
        if kind == "url" and worker.is_youtube_url(normalized_url):
            try:
                youtube_capture = worker.capture_youtube_transcript(normalized_url, temporary_root=root / worker.DEFAULT_PACKAGE_ROOT / "_youtube_caption_tmp", timeout_seconds=60)
            except (worker.YoutubeCaptionsUnavailable, worker.YoutubeTranscriptError) as exc:
                return {"status": "blocked", "stage": "youtube_caption_preflight", "reason": str(exc),
                        "source_url": normalized_url, "writer_started": False, "compiler_job_created": False,
                        "write_performed": False}
        job = worker.ensure_accepted_job(root=root, source_id=args.source_id or next_source_id(root),
            job_id=args.job_id or f"INGEST-{uuid.uuid4().hex[:20]}", source_input_kind=source_kind,
            source_identity=identity, source_url=normalized_url)
    requested_capture = {
        "title": args.title,
        "slug": args.slug,
        "profile": args.profile,
        "capture_mode": getattr(args, "capture_mode", worker.URL_CAPTURE_API_FIRST),
        "case_evidence_file": case_evidence_file.relative_to(root).as_posix() if case_evidence_file else "",
    }
    existing_capture = job.get("rail_capture")
    if existing_capture:
        if str(existing_capture.get("case_evidence_file") or "") != requested_capture["case_evidence_file"]:
            raise worker.CompilerWorkerV2Error("accepted job cannot change its case evidence manifest")
        prior_capture_mode = existing_capture.get("capture_mode", worker.URL_CAPTURE_API_FIRST)
        if str(prior_capture_mode) != str(requested_capture["capture_mode"]):
            raise worker.CompilerWorkerV2Error("accepted job cannot change its capture mode")
    else:
        job["rail_capture"] = requested_capture
    worker.write_v2_job(root, job)
    if path and not job.get("accepted_input_path"):
        job = worker.stage_accepted_input(root, job, path, raw)
    measured(root, job["job_id"], "capture", capture, root=root, job=job, youtube_capture=youtube_capture)
    return work_order(root, job["job_id"])


def capture(root, job, youtube_capture=None):
    options = job.get("rail_capture", {})
    common = {"root": root, "source_id": job["source_id"], "job_id": job["job_id"],
              "title": options.get("title"), "slug": options.get("slug"), "profile": options.get("profile", "article")}
    kind = job["source_input_kind"]
    if kind == "url":
        case_evidence_file = options.get("case_evidence_file")
        return invoke(
            worker.command_accept_url,
            **common,
            url=job["normalized_url"],
            case_evidence_file=(worker.relative_to_root(root / case_evidence_file, root, "case evidence manifest") if case_evidence_file else None),
            capture_mode=options.get("capture_mode", worker.URL_CAPTURE_API_FIRST),
            timeout_seconds=30,
            youtube_capture=youtube_capture,
        )
    source_path = worker.relative_to_root(root / job["accepted_input_path"], root, "accepted input")
    if worker.sha256_bytes(source_path.read_bytes()) != job["source_identity"].split(":", 1)[1]:
        raise worker.CompilerWorkerV2Error("accepted source bytes changed before capture")
    if kind == "user_provided_text":
        return invoke(worker.command_accept_text, **common, text_file=source_path)
    return invoke(worker.command_accept_image, **common, image_file=source_path, image_analysis_file=None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema")
    begin = commands.add_parser("start")
    inputs = begin.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--url")
    inputs.add_argument("--text-file", type=Path)
    inputs.add_argument("--image-file", type=Path)
    for name in ("source-id", "job-id", "title", "slug"):
        begin.add_argument("--" + name)
    begin.add_argument("--profile", choices=("article", "paper"), default="article")
    begin.add_argument("--case-evidence-file", type=Path)
    begin.add_argument("--capture-mode", choices=(worker.URL_CAPTURE_API_FIRST, worker.URL_CAPTURE_DIRECT_HTML_ONLY), default=worker.URL_CAPTURE_API_FIRST)
    for command in ("inspect", "advance", "amend-application", "submit-plan", "image-analysis", "image-result", "image-reconsider", "refresh-outcome", "refresh-case-evidence", "repair-case-plan", "rebind-source-id", "rebind-recovery"):
        sub = commands.add_parser(command)
        sub.add_argument("--job-id", required=True)
        if command in {"amend-application", "submit-plan", "image-analysis", "image-result", "image-reconsider", "repair-case-plan", "rebind-source-id"}:
            sub.add_argument("--response-file", type=Path, required=True)
        if command == "rebind-recovery":
            sub.add_argument("--apply", action="store_true", help="move only proven stale partial artifacts into the job recovery package")
        sub.add_argument("--vault", type=Path, default=Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")))
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command not in {"schema", "inspect"} and not (args.command == "rebind-recovery" and not args.apply):
            assert_management_idle(root)
        if args.command == "schema":
            result = plan_schema()
        elif args.command == "inspect":
            result = work_order(root, args.job_id)
        else:
            with worker.compiler_process_lock(root):
                if args.command == "start":
                    result = start(args)
                elif args.command == "advance":
                    result = advance(root, args.job_id, args.vault)
                else:
                    job, package, kp_path = paths(root, args.job_id)
                    response_path = worker.relative_to_root(args.response_file, root, "semantic response") if hasattr(args, "response_file") else None
                    if args.command == "amend-application":
                        result = worker.amend_application(root, kp_path, response_path)
                    elif args.command == "refresh-outcome":
                        result = worker.refresh_completed_outcome(root, kp_path)
                    elif args.command == "refresh-case-evidence":
                        result = refresh_case_evidence_before_plan(root, args.job_id)
                    elif args.command == "repair-case-plan":
                        result = repair_case_plan_before_write(root, args.job_id, response_path)
                    elif args.command == "rebind-source-id":
                        result = rebind_uncompleted_case_source_id(root, args.job_id, response_path, args.vault)
                    elif args.command == "rebind-recovery":
                        result = archive_rebound_partial_artifacts(root, args.job_id, args.vault, apply=args.apply)
                    elif args.command == "submit-plan":
                        if job.get("execution_contract") or (package / "completion_receipt.json").exists():
                            raise worker.CompilerWorkerV2Error("plan already locked; use evidence-bound compiler repair/revision")
                        plan = worker.read_json(response_path)
                        kp = build_kp(plan, package, root=root)
                        if kp.get("source", {}).get("asset_mode", "formal_infographic") == "formal_infographic":
                            issues = worker.infographic_plan_preflight_issues(kp, package, phase="plan")
                            if issues:
                                raise worker.InfographicPlanPreflightError(issues)
                        else:
                            errors = worker.validate_kp(kp, package)
                            if errors:
                                raise worker.CompilerWorkerV2Error("case plan preflight failed: " + "; ".join(errors))
                        worker.write_json(kp_path, kp)
                        if kp.get("source", {}).get("asset_mode") == "case_evidence":
                            worker.lock_case_ingest_plan(job, kp, package)
                            worker.write_v2_job(root, job)
                        result = advance(root, args.job_id, args.vault)
                    elif args.command == "image-analysis":
                        bundle = worker.read_json(package / "input_bundle.json")
                        if bundle.get("status") != "awaiting_image_analysis":
                            raise worker.CompilerWorkerV2Error("this job is not awaiting image analysis")
                        invoke(worker.command_accept_image, root=root, source_id=job["source_id"], job_id=args.job_id,
                               image_file=package / bundle["original_image_path"], image_analysis_file=response_path,
                               title=bundle.get("requested_title"), slug=package.name.split("_", 1)[1], profile=job.get("source_profile", "article"))
                        result = work_order(root, args.job_id)
                    elif args.command == "image-reconsider":
                        measured(
                            root,
                            args.job_id,
                            "image_quality_reconsideration",
                            invoke,
                            function=worker.command_imagegen_reconsider_quality,
                            root=root,
                            kp_file=kp_path,
                            page_id=worker.read_json(response_path).get("page_id"),
                            review_file=response_path,
                        )
                        result = advance(root, args.job_id, args.vault)
                    else:
                        measured(root, args.job_id, "image_result", invoke, function=worker.command_imagegen_complete,
                                 root=root, kp_file=kp_path, response_file=response_path)
                        result = advance(root, args.job_id, args.vault)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("status") == "blocked" else 0
    except worker.InfographicPlanPreflightError as exc:
        print(json.dumps({"status": "blocked", "stage": "plan_preflight", "issues": exc.issues, "write_performed": False}, ensure_ascii=False))
        return 2
    except (worker.CompilerWorkerV2Error, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
