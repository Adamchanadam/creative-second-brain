"""Fixed three-input execution rail over Compiler Worker v2.

There is no second writer or completion ledger. AI receives a typed work order
only when semantic analysis or the native Codex image tool is actually needed.
"""
from __future__ import annotations
import os

import argparse
import copy
import contextlib
import html
import io
import json
import re
import shutil
import sys
import time
import unicodedata
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
    from tools.ingest_plan_contract import COMPACT_VERSION, VERSION, SOURCE_CATALOG_VERSION, SOURCE_CATALOG_VERSIONS, CURRENT_SOURCE_CATALOG_VERSION, SOURCE_EVIDENCE_FRAGMENT_SEPARATOR, TRANSCRIPT_ARCHIVE_MODE, build_kp, input_fingerprint, is_youtube_caption_transcript, plan_schema, semantic_response_schema, source_block_catalog
    from tools.ingest_plan_contract import RELATION_TYPES, PRIOR_DECISIONS, CONCEPT_RELATIONS, CONCEPT_RELATION_LINK_TYPES, blank_prior_knowledge, caption_reading_chunks
    from tools.obsidian_idea_retrieval import PRIOR_LINK_MIN_SCORE, existing_concept_names, prior_registry_fingerprint, related_prior_candidates
    from tools.image_source_contract import visual_analysis_template, VISUAL_TYPES, ANCHOR_KINDS, COVERAGE_DISPOSITIONS
    from tools.infographic_prompt_contract import reader_page_payload, reader_page_sha256, visible_text_from_prompt
    from tools.outcome_page_generator import web_markdown_reader_text
    from tools.outcome_page_quality_audit import content_repetition_issues, reader_surface_text_findings
    from tools.workspace_paths import vault_path
except ModuleNotFoundError:
    import compiler_worker_v2 as worker
    import obsidian_vault_adapter as vault_adapter
    from ingest_plan_contract import COMPACT_VERSION, VERSION, SOURCE_CATALOG_VERSION, SOURCE_CATALOG_VERSIONS, CURRENT_SOURCE_CATALOG_VERSION, SOURCE_EVIDENCE_FRAGMENT_SEPARATOR, TRANSCRIPT_ARCHIVE_MODE, build_kp, input_fingerprint, is_youtube_caption_transcript, plan_schema, semantic_response_schema, source_block_catalog
    from ingest_plan_contract import RELATION_TYPES, PRIOR_DECISIONS, CONCEPT_RELATIONS, CONCEPT_RELATION_LINK_TYPES, blank_prior_knowledge, caption_reading_chunks
    from obsidian_idea_retrieval import PRIOR_LINK_MIN_SCORE, existing_concept_names, prior_registry_fingerprint, related_prior_candidates
    from image_source_contract import visual_analysis_template, VISUAL_TYPES, ANCHOR_KINDS, COVERAGE_DISPOSITIONS
    from infographic_prompt_contract import reader_page_payload, reader_page_sha256, visible_text_from_prompt
    from outcome_page_generator import web_markdown_reader_text
    from outcome_page_quality_audit import content_repetition_issues, reader_surface_text_findings
    from workspace_paths import vault_path  # type: ignore

ROOT = Path(__file__).resolve().parents[1]

WORK_ORDER_DIR = "work_order"
WORK_ORDER_FILE = "prepare_semantic_plan.json"
DRAFT_PLAN_FILE = "plan.draft.json"
READING_FILE_MAX_CHARS = 12000
WORK_ORDER_ISSUED_STAGE = "work_order_issued"
LEGACY_ATTEMPT_STAGES = frozenset({"submit_plan", "image_analysis"})
# Stages that keep the failure reason (and the field paths when the error carries them).
ISSUE_RECORDED_STAGES = frozenset({
    "compile_plan", "authorize_initial_image", "authorize_blocking_repair", "authorize_text_revision", "image_result",
})
ATTEMPT_RECORDED_STAGES = LEGACY_ATTEMPT_STAGES | ISSUE_RECORDED_STAGES
FAILURE_MESSAGE_MAX_CHARS = 500
FAILURE_ISSUE_MAX_COUNT = 20
IMAGE_RESPONSE_DRAFT_FILE = "image-response.draft.json"
IMAGE_ANALYSIS_DRAFT_FILE = "image-analysis.draft.json"
CHECK_ERROR_MAX_COUNT = 50
# check-plan keeps one diagnostic line per run here while the plan is open; submit-plan copies the summary
# into its rail event, so the completion receipt can say how many checks the host needed (Compiler spec 7.2 item 8).
CHECK_ATTEMPT_LOG = "check_attempts.jsonl"
CHECK_ERROR_CODE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_ .-]{0,60}?)\s*:")
POINTER_INSTRUCTIONS = (
    "Read every file in reading_files in order (each line: block id, tab, text). "
    "In draft_file fill summary and knowledge (copy the sample item for more points), citing only block ids from the reading files. "
    "Allowed values and optional sections (relations, source_metadata, application) are in work_order_file. "
    "Run check until it passes, then submit. YouTube: cite the 1-2 chunks that best support each claim; the rendered quote must stay within 700 characters. "
    "Do not read specs, run doctor/tests, or write handoff/progress notes; when completed, stop and report the receipt execution."
)
VOLUME_CLASS_LIMITS = ((10000, "short"), (40000, "medium"), (100000, "long"))
# Concept compounding (prior knowledge).  Rules and error codes: Compiler spec section 7.2.
RELATED_KNOWLEDGE_FILE = "related-knowledge.md"
RELATED_CLAIM_CHARS = 70
RELATED_KNOWLEDGE_ROLE = (
    "Older concepts and knowledge points that may relate to this source. Decide every listed candidate in "
    "prior_knowledge (rules in the file header) within this same analysis; no extra round is needed."
)
PRIOR_QUERY_MAX = 30
PRIOR_QUERY_CHARS = 120
PRIOR_CANDIDATE_TOTAL = 8
# Every listed candidate (RC and RK, score above 0) must be decided; the threshold only labels strength.  One not_linked
# item may dismiss several candidates for the same reason: at most PRIOR_DISMISS_GROUP_MAX of them, and its reason must say
# what they are about (at least PRIOR_DISMISS_REASON_MIN_CHARS characters besides ids and punctuation).  Compiler spec section 7.2.
PRIOR_DISMISS_GROUP_MAX = 6
PRIOR_DISMISS_REASON_MIN_CHARS = 6
PRIOR_RC_JOB_KEY = "prior_knowledge_rc"  # the RC list and registry fingerprint stored in the job at first issue
CROSS_LINK_SAFETY_LIMIT = 12
INSIGHT_PARAPHRASE_OVERLAP = 0.6  # share of the insight's tokens that one claim already contains
INSIGHT_MIN_TOKENS = 4  # shorter insights are too small for the overlap measure to mean anything
# Trailing reference-type sections of a web article (Markdown heading levels 1-4,
# case-insensitive) are delivered as optional verification material.
REFERENCE_SECTION_HEADINGS = frozenset(name.casefold() for name in (
    "References", "Notes", "Sources", "Bibliography", "Citations", "External links", "Further reading", "See also",
    "參考資料", "參考文獻", "注釋", "註釋", "外部連結", "延伸閱讀", "相關條目",
))
REFERENCE_FILES_ROLE = (
    "Optional verification material (the trailing Notes/References sections as readable text). "
    "Read it only to verify or cite; its block ids may still be cited."
)
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,4})[ \t]+(.*?)[ \t]*$")
_CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_EDIT_MARKER = re.compile(r"\s*\\?\[\s*edit\s*\\?\]\s*$", re.IGNORECASE)


def volume_class(reading_chars_total):
    """Comparable-timing size class of the reading files: short < 10,000; medium < 40,000; long < 100,000; very_long above."""
    for limit, name in VOLUME_CLASS_LIMITS:
        if reading_chars_total < limit:
            return name
    return "very_long"

_LOCK_DEPTH = 0


@contextlib.contextmanager
def exclusive(root):
    """Hold the one compiler lock; a nested request from the same process joins it."""
    global _LOCK_DEPTH
    if _LOCK_DEPTH:
        yield
        return
    with worker.compiler_process_lock(root):
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1


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
        "prepare_infographic_revision",
        "compile_plan", "authorize_initial_image", "authorize_blocking_repair",
        "authorize_text_revision", "generate_image_once", "reconcile_image_result",
        "supplement_failure_evidence",
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


def catalog_version_for(job):
    """The source-catalog contract a job was issued under; older jobs stay on v2."""
    version = job.get("analysis_contract_version")
    return version if version in SOURCE_CATALOG_VERSIONS else SOURCE_CATALOG_VERSION


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
    catalog_version = catalog_version_for(job)
    bundle = worker.read_json(package / "input_bundle.json")
    base = {"schema_version": "ingest_work_order_v1", "job_id": job_id,
            "source_id": job["source_id"], "source_kind": job["source_input_kind"],
            "package_dir": package.relative_to(root).as_posix(),
            "first_accepted_at": job["first_accepted_at"],
            "enforcement_boundary": "official program entry points; semantic truth still requires actual source/image inspection"}
    if bundle.get("status") == "awaiting_image_analysis":
        asset_mode = bundle.get("asset_mode", worker.ASSET_MODE_FORMAL)
        visual_template = visual_analysis_template(image_sha256=bundle["source_image_sha256"], dimensions=bundle["source_image_dimensions"])
        draft_path = package / WORK_ORDER_DIR / IMAGE_ANALYSIS_DRAFT_FILE
        # Image analysis is a complete first submission.  A legacy combined
        # response remains accepted by submit_image_analysis for compatibility.
        response_template = {"image_analysis": visual_template} if asset_mode == worker.ASSET_MODE_KNOWLEDGE else visual_template
        deliver_image_analysis_draft(root, draft_path, response_template)
        return {**base, "status": "needs_input", "action": "review_original_image",
                "original_image": (package / bundle["original_image_path"]).relative_to(root).as_posix(),
                "source_image_sha256": bundle["source_image_sha256"],
                "required_source": "tools/image_source_contract.py:validate_visual_analysis",
                "response_template": response_template,
                "response_draft_file": draft_path.relative_to(root).as_posix(),
                "allowed_values": {"visual_type": sorted(VISUAL_TYPES), "anchor_kind": sorted(ANCHOR_KINDS), "coverage_disposition": sorted(COVERAGE_DISPOSITIONS), "transcription_confidence": ["high", "medium", "low", "unreadable"], "verification_status": ["image_visible", "externally_verified", "needs_external_verification"]},
                "instructions": (
                    "Inspect the entire original image, transcribe every legible region, mark unreadable regions, then perform a second visual check. Do not invent passed evidence. Fill response_draft_file in place (it already holds the response template inside the workspace; never write the response outside the project) and submit that file. "
                    + (
                        "For this knowledge_only job, submit only image_analysis first. After it is accepted, the next work order supplies the accepted IMG-* blocks and any related-knowledge candidates for semantic_plan. Copy each IMG-* ID exactly as printed there; never merge, extend or compute a range. A legacy combined image_analysis plus semantic_plan response remains accepted, but is not required."
                        if asset_mode == worker.ASSET_MODE_KNOWLEDGE else ""
                    )
                ),
                "submit": ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "image-analysis", "--job-id", job_id, "--response-file", str(draft_path)]}
    if not kp_path.is_file():
        asset_mode = bundle.get("asset_mode", worker.ASSET_MODE_FORMAL)
        if asset_mode == worker.ASSET_MODE_KNOWLEDGE:
            catalog = source_block_catalog(package, version=catalog_version)
            return deliver_semantic_work_order(root, job_id, package, base, catalog, {
                "input_fingerprint": input_fingerprint(package, catalog_version),
                "source_catalog_version": catalog_version,
                "canonical_source": (package / "canonical_source.md").relative_to(root).as_posix(),
                "accepted_source": bundle,
                "source_blocks_canonical_body_sha256": catalog["canonical_body_sha256"],
                "response_schema": semantic_response_schema(asset_mode),
                "required_reads": [],
                "instructions": (
                    "This accepted job is knowledge_only. The complete retained source is in the reading files listed in reading_files "
                    "(one line per source block: the block id, a tab, then the text); read every file in full and submit only the compact semantic schema. "
                    "When reference_files is listed, those files hold the trailing Notes/References sections as optional verification material: read them only to verify or cite. "
                    "summary and each knowledge item are required; each item must cite existing evidence_block_ids from those files. "
                    "Copy each ID exactly as printed before the TAB in a reading file; never merge, extend or compute a SPAN range — to cite adjacent blocks, list each ID. "
                    "Cover each explicit numbered step, Then/Now transition, case or phase block shown in the source when present; other retained blocks are not automatically analysed. Source metadata, relations and application context are optional extensions: include them whenever the source itself supports a real provenance, use, adoption, relationship, compounding or taste judgement; leave unknown context as needs_context rather than inventing it. "
                    "Echo source_catalog_version and the bound input_fingerprint (the draft already carries both). Cite enough adjacent blocks to support complete claims and necessary context; preserve timestamps. In this same analysis, provide reusable_concept status/reason for each point and an analysed name only when it is reusable; the KP title is not a concept name. Absence of cross-source insight does not rule out a single-source reusable idea. "
                    "When a paragraph-catalog heading has three or more knowledge points beneath it, add a parent point that cites the heading, keeps its source name and relates to every member, or record a reasoned structure_decisions dismissal. "
                    "The program owns identity, full-source retention, exact citations, IDs, coverage, zero-image status and conservative no-context defaults. "
                    "Do not claim every source block was semantically analysed or covered, do not invent IDs, and do not read image or engineering specifications for this normal route."
                ),
            })
        required_reads = ["docs/Compiler Worker與Knowledge Package設計及驗收規格.md", "docs/URL到輸出模板規格.md"]
        if asset_mode != worker.ASSET_MODE_KNOWLEDGE:
            required_reads.append("docs/資訊圖生成規格.md")
        return {**base, "status": "needs_input", "action": "prepare_semantic_plan",
                "input_fingerprint": input_fingerprint(package, catalog_version),
                "source_catalog_version": catalog_version,
                "canonical_source": (package / "canonical_source.md").relative_to(root).as_posix(),
                "accepted_source": bundle,
                "response_schema": semantic_response_schema(asset_mode),
                "required_reads": required_reads,
                "instructions": (
                    "Read the complete source. Submit only schema-defined semantic decisions. Identity, full-body preservation, hashes and statuses are computed. "
                    "Echo source_catalog_version and provide an evidence-grounded reusable_concept decision for every point in this same analysis; use reusable with an analysed name, not_reusable or needs_review with a concrete reason. No extra model round is needed. "
                    "Text/URL evidence uses exact source quotes; image knowledge evidence must name existing IMG-* anchors from image_analysis.json. "
                    "When a paragraph-catalog heading has three or more knowledge points beneath it, add a parent point that cites the heading, keeps its source name and relates to every member, or record a reasoned structure_decisions dismissal. "
                    "Do not copy an old package. Existing-context insights require actual note paths and quotations. "
                    + (
                        "This accepted job is knowledge_only: set asset_mode to knowledge_only, submit zero infographic pages, and do not read or invoke image-plan/imagegen contracts."
                        if asset_mode == worker.ASSET_MODE_KNOWLEDGE
                        else "Write coherent standalone page copy before shortening it. Each page must explain actors, terms, conditions and outcomes. Choose structure from actual relationships, obtain isolated page readback bound to reader_page_sha256, then submit the infographic plan."
                    )
                ),
                "submit": ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "submit-plan", "--job-id", job_id, "--response-file", "<plan.json>"]}
    kp = worker.read_json(kp_path)
    worker.validate_job_package_binding(root, kp_path, kp)
    worker.text_revision.recover_action(worker, package)
    if (package / "completion_receipt.json").is_file():
        # This is a real hash/artifact readback, not a status-field shortcut.
        result = invoke(worker.command_project, root=root, kp_file=kp_path, write_vault=False, vault=None)
        receipt = worker.read_json(package / "completion_receipt.json")
        try:
            from tools.completion_revisions import read_current_idea_candidates
        except ModuleNotFoundError:
            from completion_revisions import read_current_idea_candidates
        return {**base, "status": "completed", "action": "stop", "completion": result,
                "idea_candidates": read_current_idea_candidates(root, package, receipt),
                "execution": receipt.get("execution"), "program_events": job.get("rail_events", [])}
    if bundle.get("asset_mode") == "case_evidence":
        if kp.get("infographics"):
            raise worker.CompilerWorkerV2Error("case evidence package cannot retain infographic pages")
        worker.require_case_ingest_plan(job, kp, package)
        return {**base, "status": "ready", "action": "project_and_readback"}
    if bundle.get("asset_mode", worker.ASSET_MODE_FORMAL) == worker.ASSET_MODE_KNOWLEDGE:
        if kp.get("source", {}).get("asset_mode") != worker.ASSET_MODE_KNOWLEDGE:
            raise worker.CompilerWorkerV2Error("knowledge_only input bundle and Knowledge Package asset modes differ")
        if job.get("execution_contract"):
            worker.require_knowledge_ingest_plan(job, kp, package)
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
            return image_action_pointer(root, job_id, package, kp, action_path, action, image_response_template(root, action), {
                **base, "status": "needs_input", "action": "reconcile_image_result",
                "instructions": "An image action is already authorized. Inspect its runtime result and PNG before continuing; do not issue another image request on restart. " + IMAGE_POINTER_INSTRUCTIONS})
    page = next(page for page in kp["infographics"] if page.get("status") != "receipt_verified")
    ledger_path = worker.image_attempt_ledger_path(package)
    ledger = worker.read_json(ledger_path) if ledger_path.is_file() else {}
    attempts = ledger.get("pages", {}).get(page["page_id"], {}).get("attempts", [])
    if attempts:
        latest = attempts[-1]
        if (
            latest.get("status") == "blocking_failure"
            and latest.get("failure_code") in worker.IMAGEGEN_REPAIRABLE_FAILURES
            and worker.failure_evidence_needs_supplement(
                latest.get("semantic_readback"), str(latest.get("failure_code") or "")
            )
        ):
            if not action_path.is_file():
                return {**base, "status": "blocked", "action": "inspect_failed_attempt", "page_id": page["page_id"],
                        "reason": "failed attempt lacks its original action envelope for same-PNG evidence supplement"}
            completed_action = worker.read_json(action_path)
            if (
                completed_action.get("state") != "completed"
                or completed_action.get("page_id") != page["page_id"]
                or completed_action.get("action_identity") != latest.get("action_identity")
            ):
                return {**base, "status": "blocked", "action": "inspect_failed_attempt", "page_id": page["page_id"],
                        "reason": "failed attempt lacks a matching completed action for same-PNG evidence supplement"}
            return image_action_pointer(root, job_id, package, kp, action_path, completed_action, image_response_template(root, completed_action), {
                **base,
                "status": "needs_input",
                "action": "supplement_failure_evidence",
                "page_id": page["page_id"],
                "instructions": (
                    "Inspect the existing PNG. Re-submit this same image result with only the missing "
                    "failure_code and content_failure_evidence fields completed; retain the same action, "
                    "PNG hash, prompt hash, outcome and existing readback. Do not call image generation."
                ),
            })
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


def reading_file_chunks(blocks):
    """Split source blocks into reading files without splitting or repeating any block.

    Each line is ``<block id><TAB><text>`` with line breaks in the text turned
    into spaces.  A file stays within READING_FILE_MAX_CHARS; one block that is
    longer than that limit becomes a file of its own.
    """
    files, lines, size = [], [], 0
    for block in blocks:
        text = str(block["text"]).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        line = f"{block['id']}\t{text}\n"
        if lines and size + len(line) > READING_FILE_MAX_CHARS:
            files.append("".join(lines))
            lines, size = [], 0
        lines.append(line)
        size += len(line)
    if lines:
        files.append("".join(lines))
    return files


def reference_heading_name(raw):
    """Plain, case-folded heading text: link targets, closing hashes, emphasis and an [edit] marker removed."""
    text = web_markdown_reader_text(raw)
    text = re.sub(r"[ \t]+#+$", "", text)
    text = _EDIT_MARKER.sub("", text)
    return text.strip(" \t*_:：").casefold()


def reference_section_start(canonical):
    """Offset of the trailing reference-type section in a Markdown body, or None.

    The earliest reference-type heading H (levels 1-4) such that every later
    heading at H's level or higher is reference-type too; everything from H to
    the end of the body is the trailing reference material.  Headings inside
    code fences are ignored.
    """
    headings, fence, offset = [], None, 0
    for line in canonical.split("\n"):
        text = line.rstrip("\r")
        opener = _CODE_FENCE.match(text)
        if fence:
            if opener and opener.group(1)[0] == fence[0] and len(opener.group(1)) >= len(fence) and not text.strip(" `~"):
                fence = None
        elif opener:
            fence = opener.group(1)
        else:
            match = _ATX_HEADING.match(text)
            if match and match.group(2).strip("# \t"):
                headings.append((len(match.group(1)), reference_heading_name(match.group(2)) in REFERENCE_SECTION_HEADINGS, offset))
        offset += len(line) + 1
    for index, (level, is_reference, begin) in enumerate(headings):
        if is_reference and all(later for deeper, later, _ in headings[index + 1:] if deeper <= level):
            return begin
    return None


def reading_view_blocks(package, bundle, blocks):
    """The blocks as the agent reads them: ``(required, reference)``, each ``{"id", "text"}``.

    Display only.  A web (URL, non-YouTube) body is shown the way the reader page
    shows it - Markdown link targets and images removed, link text kept - and the
    trailing reference-type sections are split out as optional verification
    material.  Block IDs, the catalog, the fingerprint, evidence text and the
    canonical body are never touched.  Every other source is returned as is.
    """
    canonical = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
    if (bundle.get("source_input_kind") != "url" or bundle.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE
            or is_youtube_caption_transcript(canonical)):
        return blocks, []
    begin = reference_section_start(canonical)
    required, reference = [], []
    for block in blocks:
        shown = {"id": block["id"], "text": web_markdown_reader_text(str(block["text"]))}
        in_reference = begin is not None and isinstance(block.get("start"), int) and block["start"] >= begin
        (reference if in_reference else required).append(shown)
    if not required:  # nothing but references: keep one complete required reading
        return reference, []
    return required, reference


def _json_text(payload):
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _differs(path, data):
    return not (path.is_file() and path.read_bytes() == data)


def draft_plan_template(catalog_version, fingerprint, *, prior_knowledge=False):
    """Blank compact plan: required parts only; optional sections stay out.

    ``prior_knowledge`` adds the empty section only when the work order offers older knowledge.
    """
    return {
        "schema_version": COMPACT_VERSION,
        "source_catalog_version": catalog_version,
        "input_fingerprint": fingerprint,
        "summary": [],
        "knowledge": [{
            "title": "", "claim": "", "layer": "", "confidence": "",
            "evidence_block_ids": [],
            "reusable_concept": {"status": "needs_review", "reason": ""},
        }],
        "structure_decisions": [],
        **({"prior_knowledge": blank_prior_knowledge()} if prior_knowledge else {}),
    }


def vault_dir(root):
    return vault_path(root)


def _first_sentence(text, limit=80):
    match = re.search(r"[。！？.!?]", text)
    return (text[:match.end()] if match else text)[:limit].strip()


def prior_work_order_queries(package, bundle):
    """Queries for older knowledge: the title, the headings and caption-chunk openings; never the body text."""
    queries = [str(bundle.get("title") or bundle.get("requested_title") or "")]
    try:
        canonical = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
    except OSError:
        canonical = ""
    rest = []
    if bundle.get("source_retention_mode") == TRANSCRIPT_ARCHIVE_MODE and is_youtube_caption_transcript(canonical):
        for chunk in caption_reading_chunks(canonical):
            rest.append(_first_sentence(re.sub(r"^\[[0-9:.]+\]\s*", "", str(chunk["text"]))))
    else:
        fenced = False
        for line in canonical.splitlines():
            if _CODE_FENCE.match(line):
                fenced = not fenced
            heading = None if fenced else re.match(r"^ {0,3}#{1,6}[ \t]+(.*?)[ \t#]*$", line)
            if heading:
                rest.append(re.sub(r"[\\*_`]", "", heading.group(1)).strip())
    if len(rest) > PRIOR_QUERY_MAX - 1:  # an even, deterministic sample
        step = len(rest) / (PRIOR_QUERY_MAX - 1)
        rest = [rest[int(index * step)] for index in range(PRIOR_QUERY_MAX - 1)]
    unique = []
    for query in queries + rest:
        query = query[:PRIOR_QUERY_CHARS].strip()
        if query and query not in unique:
            unique.append(query)
    return unique


def plan_prior_queries(plan):
    """Each draft knowledge point's claim and analysed concept name."""
    queries = []
    for item in (plan.get("knowledge") if isinstance(plan, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        concept = item.get("reusable_concept") if isinstance(item.get("reusable_concept"), dict) else {}
        queries += [str(item.get("claim") or "")[:PRIOR_QUERY_CHARS], str(concept.get("name") or "")[:PRIOR_QUERY_CHARS]]
    return [query for query in queries if query.strip()]


def work_order_prior_candidates(root, package, bundle):
    return related_prior_candidates(vault_dir(root), prior_work_order_queries(package, bundle),
                                    exclude_source_id=str(bundle["source_id"]), total=PRIOR_CANDIDATE_TOTAL)


def stored_work_order_candidates(job):
    """The RC list stored in the job when its work order was first issued; None for a job from before it was stored."""
    record = job.get(PRIOR_RC_JOB_KEY) if isinstance(job, dict) else None
    candidates = record.get("candidates") if isinstance(record, dict) else None
    return candidates if isinstance(candidates, list) else None


def plan_prior_candidates(root, package, plan, job=None):
    """Candidates the check and the submit both use, as two parts that never renumber each other: ``(rc, rk)``.

    RC is the work order's own list, read back from the job (``job``) where it was stored at issue, so a source
    ingested meanwhile cannot change it; a job without a stored list recomputes it from the work-order queries.
    RK is what the draft's claims and concept names add now, never repeating an RC candidate.  Each part holds
    at most PRIOR_CANDIDATE_TOTAL.
    """
    bundle = worker.read_json(package / "input_bundle.json")
    stored = stored_work_order_candidates(job)
    offered = stored if stored is not None else work_order_prior_candidates(root, package, bundle)
    added = related_prior_candidates(vault_dir(root), plan_prior_queries(plan), exclude_source_id=str(bundle["source_id"]),
                                     total=PRIOR_CANDIDATE_TOTAL, id_prefix="RK",
                                     exclude_paths={item["concept_path"] for item in offered if item.get("concept_path")},
                                     exclude_kp_uids={row["kp_uid"] for item in offered if item.get("kind") == "kp" for row in item["prior_kps"]})
    return offered, added


def must_decide_flags(candidates):
    """Per candidate, in order: True when it needs a decision (Compiler spec section 7.2).

    Every listed candidate does (retrieval lists only scores above 0); the threshold is a strength label, not a filter.
    """
    return [item["score"] > 0 for item in candidates]


def with_must_decide(candidates):
    """Copies of the candidates carrying ``must_decide`` (the check output's ``related_candidates``)."""
    return [{**item, "must_decide": flag} for item, flag in zip(candidates, must_decide_flags(candidates))]


def render_related_knowledge(candidates):
    """The work-order file of older concepts and loose knowledge points (every candidate is listed in full)."""
    lines = [
        "# Related older knowledge",
        "",
        "Program-retrieved by title, heading and caption-opening words. Use it in this same analysis.",
        "- Every candidate listed below needs one decision in prior_knowledge. The score and ABOVE THRESHOLD / below threshold only show "
        "how strongly the words match; they do not choose which candidates to answer. check-plan also searches with your claims and adds "
        "RK candidates (related_candidates); decide those too.",
        "- Decide a candidate with a cross_links item to any of its knowledge points, or a not_linked item that names it "
        "(target_kp_uid = its candidate_id or a kp_uid, or its candidate_id or a kp_uid written in reason).",
        f"- When candidates are dismissed for the same reason, one not_linked item may name up to {PRIOR_DISMISS_GROUP_MAX} candidate_ids in its reason, "
        f"but the reason must say what those candidates are about and why this source does not link to them "
        f"(at least {PRIOR_DISMISS_REASON_MIN_CHARS} characters besides ids and punctuation).",
        "- decision linked: at least one cross_links item, and every other candidate dismissed with not_linked. "
        "decision no_meaningful_link: every candidate dismissed with not_linked and cross_links empty. "
        "The top-level reason is optional (a one-line summary) and does not count as naming a candidate.",
        "- cross_links item: source_index (1-based knowledge item of this plan), target_kp_uid, relation_type "
        f"({', '.join(RELATION_TYPES)}), insight (what the two points show together, not a rewording), evidence_block_ids (this source).",
        f"- supports or extends also need concept_relation: {CONCEPT_RELATIONS[0]} (that knowledge item's reusable_concept is status reusable "
        f"and named exactly as the candidate) or {CONCEPT_RELATIONS[1]} (add concept_relation_reason, one sentence).",
        f"- A candidate headed 未歸入概念的知識點 is a single older knowledge point that belongs to no concept yet: its link may only be "
        f"concept_relation {CONCEPT_RELATIONS[1]} (the reason may say the target has no concept yet); {CONCEPT_RELATIONS[0]} is an error.",
        "- Targets only from the lists below, never this source.",
    ]
    names = []
    for item in candidates:
        loose = item.get("kind") == "kp"
        lines += [
            "",
            f"## {item['candidate_id']} " + (f"未歸入概念的知識點（來源 {item['prior_kps'][0]['source_id']}）" if loose else item["concept_name"])
            + f" (score {item['score']}, " + ("ABOVE THRESHOLD" if item["above_threshold"] else "below threshold") + ")",
        ]
        for kp in item["prior_kps"]:
            claim = kp["claim"] if len(kp["claim"]) <= RELATED_CLAIM_CHARS else kp["claim"][:RELATED_CLAIM_CHARS] + "…"
            lines.append(f"- {kp['kp_uid']} [{kp['source_id']}] {claim}")
        if not loose:
            names.append(item["concept_name"])
    lines += ["", "## 以下概念名已存在於知識庫（可直接沿用，沿用時才需填 reusable_concept.understanding_delta；新概念名不要填）",
              " | ".join(names) or "（沒有）"]
    return "\n".join(lines) + "\n"


def deliver_image_analysis_draft(root, draft_path, template):
    """Put the original-image response template where the agent may legally write it.

    The agent fills this file in place and submits it; an existing draft (the
    agent's own work) is never overwritten, so a repeated inspect or advance is a read.
    """
    if draft_path.is_file():
        return
    with exclusive(root):
        assert_management_idle(root)
        if not draft_path.is_file():
            worker.write_json(draft_path, template)


def deliver_semantic_work_order(root, job_id, package, base, catalog, extra):
    """Write the knowledge_only work order to files and return the short pointer.

    The full order, the reading files and the one-time draft live under the
    package.  Identical content is never rewritten, so a repeated start,
    inspect or advance is a read; the first issue is recorded once as a
    ``work_order_issued`` rail event.
    """
    directory = package / WORK_ORDER_DIR
    order_path, draft_path = directory / WORK_ORDER_FILE, directory / DRAFT_PLAN_FILE
    required_blocks, reference_blocks = reading_view_blocks(package, extra["accepted_source"], catalog["blocks"])
    reading = reading_file_chunks(required_blocks)
    reference = reading_file_chunks(reference_blocks) if reference_blocks else []
    reading_paths = [directory / f"reading-{index:02d}.md" for index in range(1, len(reading) + 1)]
    reference_paths = [directory / f"reading-reference-{index:02d}.md" for index in range(1, len(reference) + 1)]
    relative = lambda path: path.relative_to(root).as_posix()
    check = ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "check-plan", "--job-id", job_id, "--response-file", str(draft_path)]
    submit = ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "submit-plan", "--job-id", job_id, "--response-file", str(draft_path)]
    issued = any(row.get("stage") == WORK_ORDER_ISSUED_STAGE for row in worker.read_json(worker.job_file(root, job_id)).get("rail_events", []))
    related_path = directory / RELATED_KNOWLEDGE_FILE
    related_text = None
    related_decided = None  # the RC list decided now, stored in the job with the first issue (also when empty)
    if not issued and not related_path.is_file():  # decided once, when the order is first issued
        related_decided = work_order_prior_candidates(root, package, extra["accepted_source"])
        if related_decided:
            related_text = render_related_knowledge(related_decided)
    related_offered = related_text is not None or related_path.is_file()
    reading_chars_total = sum(len(text) for text in reading)  # displayed required reading
    reference_chars = sum(len(text) for text in reference)  # displayed optional verification material
    reading_chars_displayed = reading_chars_total + reference_chars
    pointer_fields = {
        "work_order_file": relative(order_path),
        "reading_files": [relative(path) for path in reading_paths],
        "reading_chars_total": reading_chars_total,
        "block_count": len(catalog["blocks"]),
        "draft_file": relative(draft_path),
    }
    if related_offered:
        pointer_fields.update({"related_knowledge_file": relative(related_path), "related_knowledge_role": RELATED_KNOWLEDGE_ROLE})
    if reference_paths:
        pointer_fields.update({
            "reference_files": [relative(path) for path in reference_paths],
            "reference_chars": reference_chars,
            "reference_files_role": REFERENCE_FILES_ROLE,
        })
    full = {**base, "status": "needs_input", "action": "prepare_semantic_plan", **extra, **pointer_fields,
            "check": check, "submit": submit}
    writes = [(order_path, _json_text(full).encode("utf-8"))]
    writes += [(path, text.encode("utf-8")) for path, text in zip(reading_paths, reading)]
    writes += [(path, text.encode("utf-8")) for path, text in zip(reference_paths, reference)]
    if related_text is not None:
        writes.append((related_path, related_text.encode("utf-8")))
    pending = [(path, data) for path, data in writes if _differs(path, data)]
    if pending or not draft_path.is_file() or not issued:
        with exclusive(root):
            assert_management_idle(root)
            began, started = worker.iso_now(), time.perf_counter()
            for path, data in writes:
                if _differs(path, data):
                    worker.write_bytes(path, data)
            if not draft_path.is_file():  # never overwrite the agent's own draft
                worker.write_json(draft_path, draft_plan_template(extra["source_catalog_version"], extra["input_fingerprint"], prior_knowledge=related_offered))
            job = worker.read_json(worker.job_file(root, job_id))
            if not any(row.get("stage") == WORK_ORDER_ISSUED_STAGE for row in job.get("rail_events", [])):
                if related_decided is not None and PRIOR_RC_JOB_KEY not in job:
                    job[PRIOR_RC_JOB_KEY] = {"candidates": related_decided,
                                             "registry_fingerprint": prior_registry_fingerprint(vault_dir(root))}
                job.setdefault("rail_events", []).append({
                    "id": uuid.uuid4().hex, "stage": WORK_ORDER_ISSUED_STAGE, "action": "prepare_semantic_plan",
                    "status": "completed", "started_at": began, "ended_at": worker.iso_now(),
                    "program_seconds": round(time.perf_counter() - started, 6),
                    "reading_block_count": len(catalog["blocks"]), "reading_chars_total": reading_chars_total,
                    "reading_file_count": len(reading), "catalog_version": extra["source_catalog_version"],
                    "volume_class": volume_class(reading_chars_total),
                    "reading_chars_displayed": reading_chars_displayed, "reference_chars": reference_chars})
                worker.write_v2_job(root, job)
    return {**base, "status": "needs_input", "action": "prepare_semantic_plan", **pointer_fields,
            "volume_class": volume_class(reading_chars_total), "check": check, "submit": submit, "instructions": POINTER_INSTRUCTIONS}


def measured(root, job_id, stage, operation, /, **kwargs):
    """Record actual program duration inside the existing job, including failures."""
    path = worker.job_file(root, job_id)
    job = worker.read_json(path)
    event_id = uuid.uuid4().hex
    event = {"id": event_id, "stage": stage, "started_at": worker.iso_now(), "status": "started"}
    if stage in ATTEMPT_RECORDED_STAGES:
        event["attempt"] = 1 + sum(1 for row in job.get("rail_events", []) if row.get("stage") == stage)
    job.setdefault("rail_events", []).append(event)
    worker.write_v2_job(root, job)
    started = time.perf_counter()
    failure_diagnostic = None
    failure = None
    try:
        result = operation(**kwargs)
    except Exception as exc:
        status = "failed"
        diagnostic = getattr(exc, "diagnostic", None)
        if stage == "capture" and isinstance(diagnostic, dict):
            failure_diagnostic = diagnostic
        if stage in ATTEMPT_RECORDED_STAGES:
            # Error text only: the type and a bounded message, never a source passage.
            failure = {"type": type(exc).__name__, "message": str(exc)[:FAILURE_MESSAGE_MAX_CHARS]}
            if stage in ISSUE_RECORDED_STAGES:
                failure["issues"] = [str(issue.get("path") or "")[:FAILURE_MESSAGE_MAX_CHARS]
                                     for issue in (getattr(exc, "issues", None) or []) if isinstance(issue, dict)][:FAILURE_ISSUE_MAX_COUNT]
        raise
    else:
        status = "completed"
        return result
    finally:
        job = worker.read_json(path)
        row = next(row for row in job["rail_events"] if row["id"] == event_id)
        row.update(status=status, ended_at=worker.iso_now(), program_seconds=round(time.perf_counter() - started, 6))
        if failure_diagnostic is not None:
            row["capture_diagnostic"] = failure_diagnostic
        if failure is not None:
            row["failure"] = failure
        if stage == "submit_plan":
            checks = check_attempt_summary(root, job)
            if checks is not None:
                row["check_attempts"] = checks
        worker.write_v2_job(root, job)


IMAGE_POINTER_INSTRUCTIONS = (
    "The full locked prompt, generation policy and response contract are in action_envelope; the locked prompt alone is in locked_prompt_file. "
    "Inspect the actual PNG at expected_asset (png_sha256 is its current hash), complete response_draft_file, then run submit."
)


def _blank_png_hashes(template):
    blank = copy.deepcopy(template)
    blank["png_sha256"] = ""
    if isinstance(blank.get("semantic_readback"), dict):
        blank["semantic_readback"]["inspected_png_sha256"] = ""
    return blank


def image_response_draft_needs_write(path, template):
    """Write the draft once per action; refresh it only while it is still the untouched template."""
    if not path.is_file():
        return True
    try:
        existing = worker.read_json(path)
    except (OSError, ValueError):
        return False  # an agent's half-edited draft is never replaced
    if existing.get("action_identity") != template.get("action_identity"):
        return True  # left over from an earlier action
    return existing != template and existing == _blank_png_hashes(template)


def image_action_pointer(root, job_id, package, kp, action_path, action, template, order):
    """Short order for an authorized image action; the long material is already in files.

    The envelope (prompt, policy, response contract) and the locked prompt are
    existing package files.  The response template goes to work_order/ as a
    draft that is written once per action and never overwrites the agent's edits.
    """
    draft_path = package / WORK_ORDER_DIR / IMAGE_RESPONSE_DRAFT_FILE
    if image_response_draft_needs_write(draft_path, template):
        with exclusive(root):
            assert_management_idle(root)
            worker.write_json(draft_path, template)
    page = worker.infographic_page(kp, action["page_id"])
    prompt_value = str(page.get("locked_prompt_path") or "")
    pointer = {
        **order,
        "action_envelope": action_path.relative_to(root).as_posix(),
        "locked_prompt_file": (package / prompt_value).relative_to(root).as_posix() if prompt_value else "",
        "response_draft_file": draft_path.relative_to(root).as_posix(),
        "expected_asset": action["expected_asset"],
        "png_sha256": template["png_sha256"],
        "attempt_kind": action.get("attempt_kind", ""),
        "action_identity": action["action_identity"],
        "submit": ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "image-result",
                   "--job-id", job_id, "--response-file", str(draft_path)],
    }
    return pointer


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
            "internal_process_text_detected": None,
            # This is deliberately incomplete: an isolated reviewer must state
            # its actual result before the response can satisfy the conditional
            # flexible-body check in image_semantic_readback_errors().
            "semantic_alignment": {
                "status": "",
                "checked_prompt_visible_text_count": len(visible_text_from_prompt(action["prompt"])),
                "semantic_errors": [],
                "notes": "",
            },
            "failure_code": "",
            "content_failure_evidence": {
                "failure_code": "", "expected_locked_text": "",
                "observed_problem": "", "reader_impact": "",
            }},
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
            action_path = root / result["action_envelope"]
            envelope = worker.read_json(action_path)
            return continuation_order(image_action_pointer(
                root, job_id, kp_path.parent, worker.read_json(kp_path), action_path, envelope,
                image_response_template(root, envelope), {**order, "status": "needs_input", "action": "generate_image_once",
                    "next": "Generate once as described in the action envelope file. For blocking_repair, pass imagegen_tool_arguments exactly (not the original prompt) and copy the actual dispatched arguments into the response. Re-running advance requires reconciliation, never another generation. " + IMAGE_POINTER_INSTRUCTIONS}))
        elif action == "project_and_readback":
            measured(root, job_id, action, invoke, function=worker.command_project,
                     root=root, kp_file=kp_path, write_vault=True, vault=vault)
        else:
            return order
    raise worker.CompilerWorkerV2Error("unexpected execution loop; inspect original job")


READER_TEXT_FINDINGS = {
    "placeholder": ("contains placeholder text '{detail}'", "remove the placeholder wording"),
    "internal_language": ("contains the internal-language term '{detail}'", "say it in plain reader language without that term"),
    "forbidden_generic": ("contains the forbidden generic reader text '{detail}'", "replace the stock sentence with a statement about this source"),
    "forbidden_style": ("contains the forbidden reader style text '{detail}'", "rewrite it without that stock phrase"),
    "internal_message": ("exposes an internal processing message ({detail})", "describe the source content instead of capture, provenance or verification status"),
}


def reader_visible_plan_items(plan, kp):
    """``(plan field path, reader-visible text, cited block ids or None)`` for the plan strings the reader page shows.

    Evidence is shown as the quoted source text, so its problems are fixed in
    the cited block ids (compact plan) or the evidence anchor (full plan).  The
    list is the set of fields measured to reach the outcome page text: summary,
    each claim and its evidence, and the application reasons and links.  Relations,
    source metadata, knowledge titles and concept reasons never reach the page text,
    so a word in them cannot block the completion receipt and is not checked here.
    """
    compact = kp.get("semantic_plan_version") == COMPACT_VERSION
    items = []
    for index, text in enumerate((kp.get("analysis") or {}).get("summary") or []):
        items.append((f"summary[{index}]" if compact else f"analysis.summary[{index}]", text, None))
    for index, item in enumerate(kp.get("knowledge") or []):
        ids = list(plan["knowledge"][index].get("evidence_block_ids") or []) if compact else None
        items.append((f"knowledge[{index}].claim", item.get("claim"), None))
        items.append((f"knowledge[{index}].evidence_block_ids" if compact else f"knowledge[{index}].evidence_anchor", item.get("evidence_anchor"), ids))
    application = plan.get("application") if isinstance(plan.get("application"), dict) else {}
    for key in ("adoption_reason", "compounding_next_action", "taste_reason"):
        items.append((f"application.{key}", application.get(key), None))
    for index, text in enumerate(application.get("personalization_links") or []):
        items.append((f"application.personalization_links[{index}]", text, None))
    judgments = application.get("compounding_judgments") if isinstance(application.get("compounding_judgments"), dict) else {}
    for kind, row in judgments.items():
        items.append((f"application.compounding_judgments.{kind}.reason", row.get("reason") if isinstance(row, dict) else None, None))
    return [(path, text, ids) for path, text, ids in items if isinstance(text, str) and text.strip()]


def reader_text_displayed(text):
    """The text as the reader page shows it, with whitespace collapsed like the audit's visible text."""
    return re.sub(r"\s+", " ", web_markdown_reader_text(str(text))).strip()


def reader_text_preflight_issues(plan, kp):
    """Run the outcome reader-quality audit's text checks on the strings the page will show.

    The audit that blocks the completion receipt runs only after the plan is
    locked and the notes are written.  The checks decided by the plan's own
    text (placeholder, internal language, forbidden generic/style text and
    reader-surface internal messages) run here instead, on the same projection
    the page uses, so a defect surfaces while the plan can still be corrected.
    One line per finding, with the field path and the fix; nothing is written.
    """
    lines, seen = [], set()
    for path, text, ids in reader_visible_plan_items(plan, kp):
        shown = reader_text_displayed(text)
        for kind, detail in reader_surface_text_findings(shown):
            if (path, kind, detail) in seen:
                continue
            seen.add((path, kind, detail))
            what, fix = READER_TEXT_FINDINGS[kind]
            where = ""
            if ids is not None:
                fragments = str(text).split(SOURCE_EVIDENCE_FRAGMENT_SEPARATOR)
                if len(fragments) == len(ids):
                    hit = [block for block, fragment in zip(ids, fragments) if (kind, detail) in reader_surface_text_findings(reader_text_displayed(fragment))]
                else:
                    hit = []
                where = f" (cited block {', '.join(hit)})" if hit else f" (in one of the cited blocks {', '.join(ids)})"
                fix = "cite other or adjacent blocks that do not contain it"
            lines.append(f"{path}: the reader page would show text that {what.format(detail=detail)}{where}; "
                         f"the outcome reader-quality audit would block the completion receipt after the plan is locked. Fix: {fix}.")
    return lines + repeated_claim_issues(kp)


def repeated_claim_issues(kp):
    """Knowledge-point claims the reader page would show twice (the audit's repeated / contained visible content).

    Every claim is the heading of its own card, so a claim equal to or inside another claim is
    flagged by the audit's own ``content_repetition_issues`` on those headings.  Only this
    claim-against-claim part of that check is decided by the plan alone.
    """
    claims = [(index, str(item.get("claim") or "")) for index, item in enumerate(kp.get("knowledge") or [])]
    cards = lambda texts: "".join(f"<h3>{html.escape(text)}</h3>" for text in texts)
    if not content_repetition_issues(cards(text for _, text in claims)):
        return []
    lines = []
    for position, (index, text) in enumerate(claims):
        earlier = next((other for other, before in claims[:position] if content_repetition_issues(cards([before, text]))), None)
        if earlier is not None:
            lines.append(f"knowledge[{index}].claim: the reader page would show this claim twice because it repeats or contains the claim of "
                         f"knowledge[{earlier}]; the outcome reader-quality audit would block the completion receipt after the plan is locked. "
                         "Fix: give each knowledge point its own claim wording, or merge the two points.")
    return lines


def application_rendered_fields(kp):
    """``(plan field path, text, in the compounding section)`` for the plan strings the writer puts into the application note.

    The same strings the source-slice renderer places under the application
    heading (compounding_judgments, personalization_links and the next action
    sit in its compounding section).  Strings that never reach that note, such
    as context_anchors, are left out so a quoted word in them cannot block a plan.
    """
    application = kp.get("application") if isinstance(kp.get("application"), dict) else {}
    compact = kp.get("semantic_plan_version") == COMPACT_VERSION
    items = []
    summary = (kp.get("analysis") or {}).get("summary") or []
    shown = next(((index, text) for index, text in enumerate(summary) if isinstance(text, str) and text.strip()), None)
    if shown:  # the first summary item is the note's reader summary under 洞見
        items.append((f"summary[{shown[0]}]" if compact else f"analysis.summary[{shown[0]}]", shown[1], False))
    for key in ("personal_relevance_reason", "adoption_reason", "taste_reason", "analysis_markdown"):
        items.append((f"application.{key}", application.get(key), False))
    items.append(("application.compounding_next_action", application.get("compounding_next_action"), True))
    for index, text in enumerate(application.get("personalization_links") or []):
        items.append((f"application.personalization_links[{index}]", text, True))
    judgments = application.get("compounding_judgments") if isinstance(application.get("compounding_judgments"), dict) else {}
    for kind, row in judgments.items():
        items.append((f"application.compounding_judgments.{kind}.reason", row.get("reason") if isinstance(row, dict) else None, True))
    if str(application.get("analysis_markdown") or "").strip():  # the renderer shows the source lists only with the analysis
        for key, field in (("supporting_sources", "title"), ("supporting_sources", "supporting_sections"), ("related_sources", "note")):
            for index, row in enumerate(application.get(key) or []):
                items.append((f"application.{key}[{index}].{field}", row.get(field) if isinstance(row, dict) else None, False))
    return [(path, text, compounding) for path, text, compounding in items if isinstance(text, str) and text.strip()]


def reader_placeholder_paths(plan, kp):
    """Plan field paths ``reader_text_preflight_issues`` already reports as placeholder text (so they are not reported twice)."""
    return frozenset(path for path, text, _ in reader_visible_plan_items(plan, kp)
                     if any(kind == "placeholder" for kind, _ in reader_surface_text_findings(reader_text_displayed(text))))


def application_writer_preflight_issues(kp, already_reported=frozenset()):
    """Run the Obsidian writer's application-note gates on the candidate application, before the plan is locked.

    The writer blocks only after the plan is locked and the note is rendered
    (``application_draft_incomplete``, the reader-facing residue part of
    ``compounding_insight_incomplete`` and ``taste_link_evidence_missing``).
    These gates decide on plan-authored text alone, so they run here with the
    adapter's own functions and term lists, one line per finding with the field
    path; nothing is written.  Gates that need the rendered note or other
    package files are not repeated here.  A field whose placeholder the reader
    text check already names (``already_reported``) is not named a second time.
    """
    lines = []
    for path, text, compounding in application_rendered_fields(kp):
        if path not in already_reported and vault_adapter.has_application_placeholders(text):
            marker = next((m for m in vault_adapter.APPLICATION_DRAFT_PLACEHOLDERS if m.casefold() in text.casefold()), "pending")
            lines.append(f"{path}: the application note would still contain the draft placeholder '{marker}'; the Obsidian writer "
                         "(application_draft_incomplete) would block the formal write after the plan is locked. "
                         "Fix: write the finished judgment, or say in plain words that it is unknown, without a placeholder.")
        residue = vault_adapter.application_reader_residue_terms(text) if compounding else []
        if residue:
            lines.append(f"{path}: the compounding judgment would contain the unfinished-draft wording '{residue[0]}'; the Obsidian writer "
                         "(compounding_insight_incomplete) would block the formal write after the plan is locked. "
                         "Fix: state the judgment itself without that wording.")
    application = kp.get("application") if isinstance(kp.get("application"), dict) else {}
    if isinstance(kp.get("prior_knowledge"), dict):
        cross_source_issue = vault_adapter.has_insight_cross_source_issue(
            str(application.get("compounding_insight_status") or ""), len(kp["prior_knowledge"].get("cross_links") or []))
        if cross_source_issue:
            lines.append(f"application.compounding_insight_status: {cross_source_issue}")
    if application.get("taste_link_status") == "linked":
        section = f"- 狀態:linked\n- 一句原因:{application.get('taste_reason') or ''}"
        if not (vault_adapter.concrete_list_items(section) + vault_adapter.prefixed_list_items(section, "扣連")):
            lines.append("application.taste_link_status: 'linked' needs concrete taste links, but a plan cannot carry any into the note; the Obsidian writer "
                         "(taste_link_evidence_missing) would block the formal write after the plan is locked. "
                         "Fix: use taste_needs_context, exploration_needs_context or not_applicable.")
    return lines


def _normal_concept_name(name):
    return re.sub(r"[\s\W_]+", "", unicodedata.normalize("NFKC", str(name)).casefold())


def _insight_tokens(text):
    """English words and Chinese character bigrams; the unit of the paraphrase measure."""
    text = unicodedata.normalize("NFKC", str(text)).casefold()
    tokens = set(re.findall(r"[a-z0-9]{2,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.update(run[index:index + 2] for index in range(len(run) - 1))
    return tokens


def _paraphrase_share(insight, claim):
    wanted = _insight_tokens(insight)
    return len(wanted & _insight_tokens(claim)) / len(wanted) if len(wanted) >= INSIGHT_MIN_TOKENS else 0.0


def _names(text, token):
    """True when ``token`` stands alone in ``text`` (so RC-1 is not found inside RC-10)."""
    return bool(token) and re.search(r"(?<![A-Za-z0-9_:-])" + re.escape(token) + r"(?![A-Za-z0-9_:-])", str(text or "")) is not None


def relation_vocabulary_issues(where, relation):
    """The relation_type_invalid line for a relation outside RELATION_TYPES, else []; sole owner (P1 plan check and P4 backfill)."""
    if relation in RELATION_TYPES:
        return []
    return [f"relation_type_invalid: {where}.relation_type {relation!r} must be one of {', '.join(RELATION_TYPES)}."]


def insight_content_issues(where, insight, claims):
    """The paraphrase and stock-wording lines of one link's insight against the claims it joins, else []; sole owner (P1 and P4)."""
    issues = []
    if any(_paraphrase_share(insight, claim) >= INSIGHT_PARAPHRASE_OVERLAP for claim in claims if claim):
        issues.append(f"cross_link_insight_paraphrase: {where}.insight mostly repeats one of the two claims; say what the two points show together "
                      "that neither says alone.")
    stock = [detail for kind, detail in reader_surface_text_findings(re.sub(r"\s+", " ", insight).strip())
             if kind in {"forbidden_generic", "forbidden_style", "placeholder"}]
    if stock:
        issues.append(f"cross_link_insight_generic: {where}.insight contains the stock or placeholder wording '{stock[0]}'; state the specific insight.")
    return issues


def prior_knowledge_issues(kp, candidates, *, source_id, concept_names, concept_exists):
    """One line per problem in the plan's prior_knowledge and concept names; pure, writes nothing.

    Codes and meaning: Compiler spec section 7.2.  Applies only to compact knowledge_only plans.
    """
    if kp.get("semantic_plan_version") != COMPACT_VERSION:
        return []
    prior = kp.get("prior_knowledge") if isinstance(kp.get("prior_knowledge"), dict) else {}
    links, not_linked = prior.get("cross_links") or [], prior.get("not_linked") or []
    decision, reason = str(prior.get("decision") or ""), str(prior.get("reason") or "")
    knowledge = [item for item in kp.get("knowledge") or [] if isinstance(item, dict)]
    claims = {item.get("kp_id"): str(item.get("claim") or "") for item in knowledge}
    concepts = {item.get("kp_id"): item["reusable_concept"] if isinstance(item.get("reusable_concept"), dict) else {} for item in knowledge}
    position = {item.get("kp_id"): index for index, item in enumerate(kp.get("knowledge") or [])}
    offered = {row["kp_uid"]: row for item in candidates for row in item["prior_kps"]}
    owner = {row["kp_uid"]: item["concept_name"] for item in candidates if item.get("kind") != "kp" for row in item["prior_kps"]}
    unconcepted = {row["kp_uid"] for item in candidates if item.get("kind") == "kp" for row in item["prior_kps"]}  # no concept yet
    over = [item for item, must in zip(candidates, must_decide_flags(candidates)) if must]  # every listed candidate needs a decision
    issues = []

    def label(item):
        what = f"未歸入概念的知識點 {item['prior_kps'][0]['kp_uid']}" if item.get("kind") == "kp" else item["concept_name"]
        return f"{item['candidate_id']} {what} (score {item['score']})"

    def handles(item):
        return [item["candidate_id"], *[row["kp_uid"] for row in item["prior_kps"]]]

    def listing(items):  # the ids first, so a long list still reads when the line is cut
        return ", ".join(item["candidate_id"] for item in items) + " -- " + "; ".join(label(item) for item in items)

    def named_by(row, item):
        """True when this not_linked item names the candidate: target_kp_uid is its id or kp_uid, or the reason writes one."""
        return any(str(row.get("target_kp_uid") or "").strip() == handle or _names(row.get("reason"), handle) for handle in handles(item))

    linked_targets = {str(link.get("target_kp_uid") or "") for link in links}
    undecided = [item for item in over
                 if not linked_targets & {row["kp_uid"] for row in item["prior_kps"]}
                 and not any(named_by(row, item) for row in not_linked)]
    if over and decision not in PRIOR_DECISIONS:
        issues.append(
            "prior_knowledge_decision_missing: " + str(len(over)) + " older candidate(s) need a decision: " + listing(over)
            + (". decision must be linked or no_meaningful_link; cross_links and not_linked do not replace it."
               if decision or links or not_linked else
               ". Add prior_knowledge with a decision: linked (a cross_links item for the related ones and a not_linked item for each of the rest) "
               "or no_meaningful_link (a not_linked item for every candidate and no cross_links)."))
    elif decision == "linked":
        if not links:
            issues.append("prior_knowledge_decision_conflict: decision is linked but cross_links is empty.")
        if undecided:
            issues.append("prior_knowledge_decision_missing: no cross_links item or not_linked item covers the candidate(s) " + listing(undecided)
                          + ". Each listed candidate needs one cross_link to any of its knowledge points, or one not_linked item that names "
                          "its candidate_id or a kp_uid (in target_kp_uid or reason).")
    elif decision == "no_meaningful_link":
        if links:
            issues.append("prior_knowledge_decision_conflict: decision is no_meaningful_link but cross_links is not empty.")
        if undecided:
            issues.append("prior_knowledge_decision_missing: no_meaningful_link needs every listed candidate dismissed by a not_linked item, "
                          "and none names " + listing(undecided) + f". Name each by candidate_id or kp_uid in target_kp_uid or in the reason of a "
                          f"not_linked item (one item may name up to {PRIOR_DISMISS_GROUP_MAX} candidates dismissed for the same reason); "
                          "the top-level reason does not count.")
    if over:
        kp_uids = sorted({row["kp_uid"] for item in candidates for row in item["prior_kps"]}, key=len, reverse=True)
        for index, row in enumerate(not_linked):
            where = f"prior_knowledge.not_linked[{index}]"
            named = [item for item in over if named_by(row, item)]
            ids = ", ".join(item["candidate_id"] for item in named) or "no candidate"
            if len(named) > PRIOR_DISMISS_GROUP_MAX:
                issues.append(f"prior_knowledge_dismiss_group_too_large: {where} names {len(named)} candidates ({ids}); one not_linked item may "
                              f"dismiss at most {PRIOR_DISMISS_GROUP_MAX}. Split it into items of {PRIOR_DISMISS_GROUP_MAX} or fewer, each with its own reason.")
            rest = str(row.get("reason") or "")
            for kp_uid in kp_uids:
                rest = rest.replace(kp_uid, "")
            rest = re.sub(r"R[CK]-\d+", "", rest)
            rest = "".join(char for char in rest if not char.isspace() and not unicodedata.category(char).startswith(("P", "Z")))
            if len(rest) < PRIOR_DISMISS_REASON_MIN_CHARS:
                issues.append(f"prior_knowledge_dismiss_reason_thin: {where} ({ids}) has {len(rest)} character(s) besides ids and punctuation in its reason "
                              f"(at least {PRIOR_DISMISS_REASON_MIN_CHARS}); write what these candidates are about and why this source does not link to them.")
    if len(links) > CROSS_LINK_SAFETY_LIMIT:
        issues.append(f"cross_link_limit: {len(links)} cross_links exceed the safety limit {CROSS_LINK_SAFETY_LIMIT}; keep the links that carry weight and move the rest to not_linked.")
    seen = set()
    for index, link in enumerate(links):
        where = f"prior_knowledge.cross_links[{index}]"
        target, relation, insight = str(link.get("target_kp_uid") or ""), str(link.get("relation_type") or ""), str(link.get("insight") or "")
        if target.split(":", 1)[0] == source_id:
            issues.append(f"cross_link_self_source: {where}.target_kp_uid {target} belongs to this source; link only to knowledge of an older source.")
        elif target not in offered:
            issues.append(f"cross_link_target_not_offered: {where}.target_kp_uid {target} is not a prior knowledge point of the current candidates "
                          f"({', '.join(sorted(offered)) or 'none'}).")
        issues.extend(relation_vocabulary_issues(where, relation))
        if relation in CONCEPT_RELATION_LINK_TYPES:
            concept_relation = str(link.get("concept_relation") or "").strip()
            if concept_relation not in CONCEPT_RELATIONS or (concept_relation == "distinct" and not str(link.get("concept_relation_reason") or "").strip()):
                issues.append(f"concept_relation_missing: {where} is {relation}, so it needs concept_relation ({' or '.join(CONCEPT_RELATIONS)}); "
                              "distinct also needs concept_relation_reason (one sentence on why the concepts stay apart).")
            elif concept_relation == "same_concept" and target in unconcepted:
                issues.append(f"concept_relation_name_mismatch: {where} says same_concept, but {target} belongs to no concept yet (the candidate is "
                              "an 未歸入概念的知識點), so there is no concept name to match; declare it distinct and say in "
                              "concept_relation_reason that the target has no concept yet.")
            elif concept_relation == "same_concept" and target in owner:
                new_concept = concepts.get(link.get("source_kp")) or {}
                if new_concept.get("status") != "reusable" or str(new_concept.get("name") or "").strip() != owner[target]:
                    issues.append(f"concept_relation_name_mismatch: {where} says same_concept, so knowledge[{position.get(link.get('source_kp'), '?')}]"
                                  f".reusable_concept must be status reusable and named exactly {owner[target]!r}, the concept of {target}; "
                                  "use that name, or declare the link distinct.")
        key = (link.get("source_kp"), target, relation)
        if key in seen:
            issues.append(f"cross_link_duplicate: {where} repeats the link {key[0]} -> {target} ({relation}).")
        seen.add(key)
        new_claim, old_claim = claims.get(link.get("source_kp"), ""), str((offered.get(target) or {}).get("claim") or "")
        issues.extend(insight_content_issues(where, insight, (new_claim, old_claim)))
    for index, item in enumerate(kp.get("knowledge") or []):
        concept = item.get("reusable_concept") if isinstance(item, dict) and isinstance(item.get("reusable_concept"), dict) else {}
        name = str(concept.get("name") or "").strip()
        if concept.get("status") != "reusable" or not name:
            continue
        near = [existing for existing in sorted(concept_names)
                if existing != name and _normal_concept_name(existing) == _normal_concept_name(name)]
        if near:
            issues.append(f"concept_name_near_duplicate: knowledge[{index}].reusable_concept.name {name!r} only differs in case, spacing or punctuation "
                          f"from the existing concept {near[0]!r}; use the existing name exactly.")
        else:
            exists = name in concept_names or concept_exists(name)
            has_delta = bool(str(concept.get("understanding_delta") or "").strip())
            if exists and not has_delta:
                issues.append(f"understanding_delta_missing: knowledge[{index}].reusable_concept.name {name!r} already exists in the vault; "
                              "add understanding_delta saying what this source confirms or changes about it.")
            elif has_delta and not exists:
                issues.append(f"understanding_delta_unexpected: knowledge[{index}].reusable_concept.name {name!r} is not an existing concept, "
                              "so there is no earlier understanding to change; remove understanding_delta (it is only for a name that already exists).")
    return issues


def validate_plan(root, job_id, response_path, prior_out=None):
    """Run every submit-plan validation and return the checked package; write nothing.

    submit-plan and check-plan both go through this one function, so a plan
    that passes the check cannot fail a validation the submit would have run.
    That includes the reader-visible text checks of the outcome reader-quality
    audit, which would otherwise block the completion receipt only after the
    plan is locked, and the writer's application-note gates that decide on the
    plan's own text (see ``application_writer_preflight_issues``).
    """
    job, package, kp_path = paths(root, job_id)
    if job.get("execution_contract") or (package / "completion_receipt.json").exists():
        raise worker.CompilerWorkerV2Error("plan already locked; use evidence-bound compiler repair/revision")
    plan = worker.read_json(response_path)
    if isinstance(plan, dict):  # candidates first, so the check can show them even when another rule fails
        candidates = [item for part in plan_prior_candidates(root, package, plan, job) for item in part]  # RC, then RK
        if prior_out is not None:
            prior_out["candidates"] = with_must_decide(candidates)
    else:
        candidates = []
    if job.get("analysis_contract_version") in SOURCE_CATALOG_VERSIONS and plan.get("source_catalog_version") != job["analysis_contract_version"]:
        raise worker.CompilerWorkerV2Error("current work order requires source_catalog_version and same-round concept decisions")
    kp = build_kp(plan, package, root=root)
    if kp.get("source", {}).get("asset_mode", worker.ASSET_MODE_FORMAL) == worker.ASSET_MODE_FORMAL:
        issues = worker.infographic_plan_preflight_issues(kp, package, phase="plan")
        if issues:
            raise worker.InfographicPlanPreflightError(issues)
    else:
        errors = worker.validate_kp(kp, package)
        if errors:
            raise worker.CompilerWorkerV2Error("semantic plan preflight failed: " + "; ".join(errors))
    vault = vault_dir(root)
    reader_issues = reader_text_preflight_issues(plan, kp) + application_writer_preflight_issues(kp, reader_placeholder_paths(plan, kp))
    reader_issues = prior_knowledge_issues(
        kp, candidates, source_id=str(kp["source"]["source_id"]), concept_names=existing_concept_names(vault),
        concept_exists=lambda name: _concept_note_exists(vault, name)) + reader_issues
    if reader_issues:
        raise worker.CompilerWorkerV2Error("\n".join(reader_issues))
    return job, package, kp_path, kp


def _concept_note_exists(vault, name):
    try:
        return vault_adapter.reusable_concept_note_path(vault, name).exists()
    except vault_adapter.AdapterStop:
        return False


def check_error_code(message):
    """The code-like head of a check error (``understanding_delta_unexpected: ...``), else its first words."""
    match = CHECK_ERROR_CODE_RE.match(str(message))
    return match.group(1).strip() if match else str(message).strip()[:40]


def _open_plan_work_order(root, job_id):
    """The work_order folder of a job whose plan is still open, else None."""
    job = worker.read_json(worker.job_file(root, job_id))
    if job.get("execution_contract") or not job.get("package_dir"):
        return None
    package = worker.relative_to_root(root / job["package_dir"], root, "package")
    folder = package / "work_order"
    return folder if folder.is_dir() and not (package / "completion_receipt.json").exists() else None


def log_check_attempt(root, job_id, status, errors):
    """Append one diagnostic line (time, status, error codes) for an open plan; never fails the check."""
    try:
        folder = _open_plan_work_order(root, job_id)
        if folder is None:
            return
        line = {"at": worker.iso_now(), "status": status, "error_count": len(errors),
                "error_codes": sorted({check_error_code(error) for error in errors})[:FAILURE_ISSUE_MAX_COUNT]}
        with open(folder / CHECK_ATTEMPT_LOG, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except (OSError, ValueError, KeyError, worker.CompilerWorkerV2Error):
        return


def check_attempt_summary(root, job):
    """Rounds, failed rounds and error-code counts from a job's check log; ``None`` when there is no log."""
    try:
        package = worker.relative_to_root(root / job["package_dir"], root, "package")
        lines = (package / "work_order" / CHECK_ATTEMPT_LOG).read_text(encoding="utf-8").splitlines()
    except (OSError, KeyError, TypeError, worker.CompilerWorkerV2Error):
        return None
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    codes = {}
    for row in rows:
        for code in row.get("error_codes") or []:
            codes[str(code)] = codes.get(str(code), 0) + 1
    return {"rounds": len(rows), "failed_rounds": sum(1 for row in rows if row.get("status") != "passed"),
            "error_codes": dict(sorted(codes.items(), key=lambda item: (-item[1], item[0]))[:FAILURE_ISSUE_MAX_COUNT])}


def check_plan(root, job_id, response_path):
    """Dry-run of submit-plan: the same validation, with no job, event, package or vault write.

    The one exception is a diagnostic line in ``work_order/check_attempts.jsonl`` while the plan is open
    (``log_check_attempt``); ``write_performed`` stays false because no plan, job or vault state changes.

    ``related_candidates`` (the older candidates the validation used: RC read back from the job, RK searched now) is listed only when there are any.
    """
    prior = {}
    try:
        validate_plan(root, job_id, response_path, prior)
    except worker.InfographicPlanPreflightError as exc:
        errors = [str(issue.get("message") or issue) for issue in exc.issues]
    except (worker.CompilerWorkerV2Error, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        errors = [line for line in str(exc).split("\n") if line.strip()] or [type(exc).__name__]
    else:
        log_check_attempt(root, job_id, "passed", [])
        return {"status": "passed", "job_id": job_id, "errors": [], "write_performed": False,
                **({"related_candidates": prior["candidates"]} if prior.get("candidates") else {})}
    log_check_attempt(root, job_id, "failed", errors)
    shown = [line[:FAILURE_MESSAGE_MAX_CHARS] for line in errors[:CHECK_ERROR_MAX_COUNT]]
    return {"status": "failed", "job_id": job_id, "errors": shown,
            "error_count": len(errors), "write_performed": False,
            **({"related_candidates": prior["candidates"]} if prior.get("candidates") else {})}


def prior_knowledge_offer(candidates, existing_names=()):
    """What the vault offered when the plan was accepted; the completion receipt reports it (Compiler spec section 7.2).

    ``existing_names`` are the plan's reusable concept names that already existed then (merged, not created).
    ``above_threshold`` counts the candidates at or above the threshold; ``must_decide`` those that needed a decision (all of them).
    """
    return {"candidates_offered": len(candidates),
            "above_threshold": sum(1 for item in candidates if item["above_threshold"]),
            "must_decide": sum(must_decide_flags(candidates)),
            "existing_concept_names": sorted(existing_names)} if candidates else {}


def existing_reusable_names(root, kp):
    """The reusable concept names of this plan that exist in the vault now."""
    vault = vault_dir(root)
    existing = existing_concept_names(vault)
    names = {str(item["reusable_concept"].get("name") or "").strip() for item in kp.get("knowledge") or []
             if isinstance(item, dict) and isinstance(item.get("reusable_concept"), dict) and item["reusable_concept"].get("status") == "reusable"}
    return {name for name in names if name and (name in existing or _concept_note_exists(vault, name))}


def submit_plan(root, job_id, response_path):
    """Validate and persist one semantic plan before the normal resume path.

    Keeping this operation separate from ``advance`` gives the job one honest
    success/failure event for semantic submission.  It does not create a
    second writer or completion path.  When older knowledge was offered, the
    job also keeps the offer's counts (``prior_knowledge_offer``).
    """
    prior = {}
    job, package, kp_path, kp = validate_plan(root, job_id, response_path, prior)
    offer = prior_knowledge_offer(prior.get("candidates") or [], existing_reusable_names(root, kp) if prior.get("candidates") else ())
    if offer:
        job["prior_knowledge_offer"] = offer
    worker.write_json(kp_path, kp)
    asset_mode = kp.get("source", {}).get("asset_mode")
    if asset_mode == worker.ASSET_MODE_CASE:
        worker.lock_case_ingest_plan(job, kp, package)
        worker.write_v2_job(root, job)
    elif asset_mode == worker.ASSET_MODE_KNOWLEDGE:
        worker.lock_knowledge_ingest_plan(job, kp, package)
        worker.write_v2_job(root, job)
    elif offer:
        worker.write_v2_job(root, job)
    return {"knowledge_package": kp_path.relative_to(root).as_posix(), "asset_mode": asset_mode}


def submit_image_analysis(root, job_id, response_path):
    """Accept original-image analysis and its optional compact normal plan.

    The combined response still uses the established semantic compiler and
    writer.  If only semantic submission fails, accepted image analysis stays
    in the package for a source-local repair; the original is not re-read.
    """
    job, package, _ = paths(root, job_id)
    bundle = worker.read_json(package / "input_bundle.json")
    if bundle.get("status") != "awaiting_image_analysis":
        raise worker.CompilerWorkerV2Error("this job is not awaiting image analysis")
    response = worker.read_json(response_path)
    semantic_plan = None
    analysis_path = response_path
    if isinstance(response, dict) and ("image_analysis" in response or "semantic_plan" in response):
        if set(response) not in ({"image_analysis"}, {"image_analysis", "semantic_plan"}):
            raise worker.CompilerWorkerV2Error("image response requires image_analysis, with optional semantic_plan")
        if bundle.get("asset_mode") != worker.ASSET_MODE_KNOWLEDGE:
            raise worker.CompilerWorkerV2Error("wrapped image response is only available for knowledge_only")
        if not isinstance(response["image_analysis"], dict):
            raise worker.CompilerWorkerV2Error("image_analysis must be an object")
        if "semantic_plan" in response:
            if not isinstance(response["semantic_plan"], dict):
                raise worker.CompilerWorkerV2Error("semantic_plan must be an object")
            semantic_plan = copy.deepcopy(response["semantic_plan"])
            if semantic_plan.get("schema_version") != COMPACT_VERSION or "input_fingerprint" in semantic_plan:
                raise worker.CompilerWorkerV2Error("combined semantic plan must use compact schema and omit input_fingerprint")
        analysis_path = package / "image_analysis_submission.json"
        worker.write_json(analysis_path, response["image_analysis"])
    measured(
        root, job_id, "image_analysis", invoke,
        function=worker.command_accept_image, root=root, source_id=job["source_id"], job_id=job_id,
        image_file=package / bundle["original_image_path"], image_analysis_file=analysis_path,
        title=bundle.get("requested_title"), slug=package.name.split("_", 1)[1],
        profile=job.get("source_profile", "article"), asset_mode=bundle.get("asset_mode", worker.ASSET_MODE_FORMAL),
    )
    if semantic_plan is not None:
        _, accepted_package, _ = paths(root, job_id)
        semantic_plan["input_fingerprint"] = input_fingerprint(accepted_package, semantic_plan.get("source_catalog_version"))
        semantic_path = accepted_package / "image_semantic_submission.json"
        worker.write_json(semantic_path, semantic_plan)
        measured(root, job_id, "submit_plan", submit_plan, root=root, job_id=job_id, response_path=semantic_path)
    return work_order(root, job_id)


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
    # The rail is the new-start entry: persist the complete textual result
    # first unless the caller deliberately chooses the infographic route.
    # A legacy case-evidence request still selects its formal route when no
    # mode was supplied; an explicit incompatible mode remains an error.
    requested_asset_mode = getattr(args, "asset_mode", None)
    asset_mode = str(
        requested_asset_mode
        or (worker.ASSET_MODE_FORMAL if supplied_case_evidence else worker.ASSET_MODE_KNOWLEDGE)
    )
    if asset_mode not in {worker.ASSET_MODE_FORMAL, worker.ASSET_MODE_KNOWLEDGE}:
        raise worker.CompilerWorkerV2Error("start asset_mode is invalid")
    if supplied_case_evidence and kind != "url":
        raise worker.CompilerWorkerV2Error("case evidence is only supported for a URL input")
    if supplied_case_evidence and asset_mode != worker.ASSET_MODE_FORMAL:
        raise worker.CompilerWorkerV2Error("case evidence cannot be combined with knowledge_only")
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
                        "capture_diagnostic": exc.diagnostic(),
                        "source_url": normalized_url, "writer_started": False, "compiler_job_created": False,
                        "write_performed": False}
        job = worker.ensure_accepted_job(root=root, source_id=args.source_id or next_source_id(root),
            job_id=args.job_id or f"INGEST-{uuid.uuid4().hex[:20]}", source_input_kind=source_kind,
            source_identity=identity, source_url=normalized_url)
        job["analysis_contract_version"] = CURRENT_SOURCE_CATALOG_VERSION
    requested_capture = {
        "title": args.title,
        "slug": args.slug,
        "profile": args.profile,
        "capture_mode": getattr(args, "capture_mode", worker.URL_CAPTURE_API_FIRST),
        "asset_mode": asset_mode,
        "case_evidence_file": case_evidence_file.relative_to(root).as_posix() if case_evidence_file else "",
    }
    existing_capture = job.get("rail_capture")
    if existing_capture:
        if str(existing_capture.get("case_evidence_file") or "") != requested_capture["case_evidence_file"]:
            raise worker.CompilerWorkerV2Error("accepted job cannot change its case evidence manifest")
        prior_capture_mode = existing_capture.get("capture_mode", worker.URL_CAPTURE_API_FIRST)
        if str(prior_capture_mode) != str(requested_capture["capture_mode"]):
            raise worker.CompilerWorkerV2Error("accepted job cannot change its capture mode")
        if str(existing_capture.get("asset_mode", worker.ASSET_MODE_FORMAL)) != asset_mode:
            raise worker.CompilerWorkerV2Error("accepted job cannot change its asset mode")
    else:
        job["rail_capture"] = requested_capture
    worker.write_v2_job(root, job)
    if path and not job.get("accepted_input_path"):
        job = worker.stage_accepted_input(root, job, path, raw)
    measured(root, job["job_id"], "capture", capture, root=root, job=job, youtube_capture=youtube_capture)
    return work_order(root, job["job_id"])


def completed_source_job(root: Path, source_id: str, *, allow_infographic_resume: bool = False):
    """Resolve the single completed non-revision owner for add-infographic."""

    worker.validated_identifier(source_id, "source_id")
    matches = []
    for path in sorted(worker.job_root(root).glob("*.json")):
        job = worker.read_json(path)
        if (
            job.get("job_kind") not in worker.REVISION_JOB_KINDS
            and job.get("source_id") == source_id
        ):
            matches.append(job)
    if len(matches) != 1:
        raise worker.CompilerWorkerV2Error(
            "add-infographic requires exactly one accepted source owner"
        )
    job = matches[0]
    if job.get("status") != "completed" or not job.get("package_dir"):
        raise worker.CompilerWorkerV2Error(
            "add-infographic requires a completed text-first source"
        )
    package = worker.relative_to_root(root / job["package_dir"], root, "completed source package")
    kp_path = package / "knowledge_package.json"
    if not kp_path.is_file():
        raise worker.CompilerWorkerV2Error("completed text-first Knowledge Package is missing")
    kp = worker.read_json(kp_path)
    worker.validate_job_package_binding(root, kp_path, kp)
    if (kp.get("source") or {}).get("asset_mode", worker.ASSET_MODE_FORMAL) not in {
        worker.ASSET_MODE_KNOWLEDGE,
        worker.ASSET_MODE_FORMAL,
    }:
        raise worker.CompilerWorkerV2Error(
            "add-infographic requires a completed knowledge_only or formal_infographic source"
        )
    try:
        completed = worker.completed_receipt_readback(root, package, kp_path, kp)
    except worker.CompilerWorkerV2Error:
        if not allow_infographic_resume:
            raise
        existing = active_infographic_addition(root, package)
        if existing is None:
            raise
        child_kp_path, child_kp = existing
        child_job_id = str(child_kp.get("job_id") or "")
        if not child_job_id or not worker.job_file(root, child_job_id).is_file():
            raise
        # This reuses the child binding's narrow parent-drift verification;
        # it still rejects any drift outside the adapter-proven child output.
        worker.validate_job_package_binding(root, child_kp_path, child_kp)
        completed = {"status": "completed"}
    if completed is None:
        raise worker.CompilerWorkerV2Error("completed text-first receipt is missing")
    return job, package, kp_path, kp


def active_infographic_addition(root: Path, package: Path):
    candidates = []
    revisions = package / "revisions"
    if not revisions.is_dir():
        return None
    for kp_path in sorted(revisions.glob("*/knowledge_package.json")):
        kp = worker.read_json(kp_path)
        revision = kp.get("revision") if isinstance(kp.get("revision"), dict) else {}
        child_job_path = worker.job_file(root, str(kp.get("job_id") or ""))
        child_job = worker.read_json(child_job_path) if child_job_path.is_file() else {}
        if (
            revision.get("kind") == "infographic_revision"
            and revision.get("mode") == "knowledge_only_to_infographic"
            and child_job.get("status") != "completed"
        ):
            candidates.append((kp_path, kp))
    if len(candidates) > 1:
        raise worker.CompilerWorkerV2Error("add-infographic found multiple revision packages for one source")
    return candidates[0] if candidates else None


def list_infographic_candidates(root: Path):
    """List completed text-first sources that still have no current image."""

    candidates = []
    for path in sorted(worker.job_root(root).glob("*.json")):
        job = worker.read_json(path)
        if job.get("job_kind") in worker.REVISION_JOB_KINDS or job.get("status") != "completed":
            continue
        source_id = str(job.get("source_id") or "")
        try:
            _, package, kp_path, kp = completed_source_job(root, source_id)
            if (
                (kp.get("source") or {}).get("asset_mode") == worker.ASSET_MODE_KNOWLEDGE
                and worker.read_current_infographic_pointer(root, package, kp) is None
            ):
                candidates.append({
                    "source_id": source_id,
                    "job_id": job.get("job_id"),
                    "title": (kp.get("source") or {}).get("title"),
                    "knowledge_package": kp_path.relative_to(root).as_posix(),
                })
        except worker.CompilerWorkerV2Error:
            continue
    return candidates


INFOGRAPHIC_ORDER_FILE = "prepare_infographic_revision.json"
INFOGRAPHIC_DRAFT_FILE = "infographic-plan.draft.json"
INFOGRAPHIC_SPEC = "docs/資訊圖生成規格.md"
INFOGRAPHIC_ENFORCED_SECTIONS = {
    "docs/Compiler Worker與Knowledge Package設計及驗收規格.md": [
        "第 5 節 資訊圖：完整覆蓋而非強行壓縮",
        "第 7.1 節第 2 項（明示正式資訊圖才鎖圖面計劃）及第 3 項（例外必須是具體阻塞）",
    ],
    "docs/URL到輸出模板規格.md": ["第 6 節 資訊圖成果引用"],
}
INFOGRAPHIC_POINTER_INSTRUCTIONS = (
    "Read the one required spec in full (new generation requires it). In draft_file edit coverage_units only to add "
    "'infographic' plus reviewed infographic_text to the chosen units, and fill the page(s) (copy the page for more pages); "
    "everything else in the draft stays as delivered. work_order_file lists every page field, the spec sections the program enforces "
    "and the reader-review steps. Run check until it passes: it prints each page's reader text and reader_page_sha256 for the isolated "
    "reader review and writes nothing. Submit only after a pass. Do not read the other specs, run doctor/tests, or write handoff notes."
)
INFOGRAPHIC_PAGE_FIELDS = {
    "authored": [
        "title (see title_rule)", "page_focus", "page_focus_evidence (verbatim from canonical_source.md)", "layout_archetype",
        "content_use", "graph_type", "visual_structure", "coverage_ids",
        "reader_question", "reader_takeaway", "cross_page_handoff", "number_labels / number_evidence (only when numbers are shown)",
        "reader_review (from an isolated reader; page_sha256 comes from check-infographic-plan)",
    ],
    "delivered_by_program": [
        "page_id", "status=planned", "content_map", "visible_text", "source_anchors",
        "multi-page collection_role and collection_roadmap",
    ],
    "multi_page_first_page": [
        "collection_role=orientation", "collection_thesis", "collection_importance", "collection_roadmap_summary (with an arrow sequence)",
        "collection_roadmap (one row per page: page_number, page_id, page_title, page_role)",
    ],
    "title_rule": (
        "When source.primary_work_status is primary, title is the verbatim primary_work_title. Otherwise write the reader-form title "
        "(check-infographic-plan reports the exact form if compile would rewrite it, because that changes reader_page_sha256); "
        "a title not taken from the source title needs title_source=source_derived and a verbatim title_evidence."
    ),
    "content_map_hint": "check-infographic-plan returns the program-derived content_map, visible_text and source_anchors from reviewed coverage. Legacy hand-filled copies are accepted only when exactly identical; visual_structure text lines must already occur in the reviewed coverage text.",
}
RUNTIME_ONLY_CHECKS = (
    "job and package binding, knowledge-only origin verification and the execution-plan lock need the revision job, "
    "and the image-action and attempt-ledger guards need an image action; they run at compile_plan / authorize time."
)


def infographic_page_skeleton():
    """One page with every field the plan preflight reads; nothing AI-authored is filled in."""
    return {
        "page_id": "IG-001", "status": "planned", "title": "", "title_source": "", "title_evidence": "",
        "page_focus": "", "page_focus_evidence": "", "layout_archetype": "", "content_use": "", "graph_type": "",
        "visual_structure": {}, "coverage_ids": [], "content_map": [], "visible_text": [], "source_anchors": [],
        "reader_question": "", "reader_takeaway": "", "cross_page_handoff": "", "number_labels": [], "number_evidence": "",
        "reader_review": {
            "page_sha256": "", "method": "", "reviewer_id": "", "verdict": "", "answer": "", "relationship_explanation": "",
            "unexplained_terms": [], "missing_context": [], "misleading_relations": [],
        },
    }


def revision_directories(package):
    revisions = package / "revisions"
    return sorted(path.name for path in revisions.iterdir() if path.is_dir()) if revisions.is_dir() else []


def next_revision_id(root, package):
    """A short program-chosen revision id that no package or job already uses."""
    taken = set(revision_directories(package))
    number = len(taken) + 1
    while True:
        candidate = f"IG-R{number}"
        if candidate not in taken and not worker.job_file(root, f"CW2-{candidate}").exists():
            return candidate
        number += 1


def infographic_draft_plan(parent_kp, revision_mode, revision_id):
    source = parent_kp.get("source") or {}
    return {
        "schema_version": worker.INFOGRAPHIC_REVISION_PLAN_VERSION,
        "mode": revision_mode,
        "revision_id": revision_id,
        "job_id": f"CW2-{revision_id}",
        "source_id": source.get("source_id"),
        "source_identity": source.get("source_identity"),
        "source_updates": {},
        "coverage_units": copy.deepcopy(parent_kp.get("coverage_units")),
        "infographics": [infographic_page_skeleton()],
    }


def deliver_infographic_work_order(root, parent_job, package, parent_kp_path, parent_kp, source_id, revision_mode, rebuild):
    """Write the add-infographic work order to files and return the short pointer.

    The full order and the one-time draft live under the parent package's
    work_order/.  Identical content is never rewritten and an existing draft is
    never overwritten; the first issue is recorded once per revision cycle as a
    ``work_order_issued`` event on the parent job.
    """
    directory = package / WORK_ORDER_DIR
    order_path, draft_path = directory / INFOGRAPHIC_ORDER_FILE, directory / INFOGRAPHIC_DRAFT_FILE
    relative = lambda path: path.relative_to(root).as_posix()
    flags = ["--rebuild"] if rebuild else []
    check = ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "check-infographic-plan",
             "--source-id", source_id, "--response-file", str(draft_path), *flags]
    submit = ["python", "-B", "tools/ingest_rail.py", "--root", str(root), "add-infographic",
              "--source-id", source_id, "--response-file", str(draft_path), *flags]
    revision_id = None
    if draft_path.is_file():
        try:
            revision_id = worker.read_json(draft_path).get("revision_id")
        except (OSError, ValueError):
            revision_id = None
    revision_id = revision_id or next_revision_id(root, package)
    revision_mode_contract = {
        "schema_version": worker.INFOGRAPHIC_REVISION_PLAN_VERSION,
        "mode": revision_mode,
        "required_fields": ["revision_id", "job_id", "source_id", "source_identity", "coverage_units", "infographics"],
        "coverage_units": "retain every parent ID, source text/evidence/block IDs and non-image disposition exactly; only selected units may add one infographic disposition plus reviewed infographic_text",
        "source_updates": "must be omitted or {} for a knowledge-only parent",
        "infographics": "new planned pages only; use the normal formal infographic contract",
    }
    full = {
        "schema_version": "ingest_work_order_v1", "status": "needs_input", "action": "prepare_infographic_revision",
        "source_id": source_id, "rebuild": rebuild,
        "parent_knowledge_package": relative(parent_kp_path),
        "required_reads": [INFOGRAPHIC_SPEC],
        "program_enforced_sections": {
            "note": "The program enforces these sections; read only the one required spec above, not these documents in full.",
            "sections": INFOGRAPHIC_ENFORCED_SECTIONS,
        },
        "revision_plan_contract": revision_mode_contract,
        "page_fields": INFOGRAPHIC_PAGE_FIELDS,
        "work_order_file": relative(order_path), "draft_file": relative(draft_path),
        "check": check, "submit": submit,
        "not_checked_before_submit": RUNTIME_ONLY_CHECKS,
        "instructions": INFOGRAPHIC_POINTER_INSTRUCTIONS,
    }
    order_bytes = _json_text(full).encode("utf-8")
    cycle = len(revision_directories(package))
    issued = any(row.get("stage") == WORK_ORDER_ISSUED_STAGE and row.get("action") == worker.INFOGRAPHIC_WORK_ORDER_ACTION
                 and row.get("revisions_before") == cycle for row in parent_job.get("rail_events", []))
    if _differs(order_path, order_bytes) or not draft_path.is_file() or not issued:
        with exclusive(root):
            assert_management_idle(root)
            began, started = worker.iso_now(), time.perf_counter()
            if _differs(order_path, order_bytes):
                worker.write_bytes(order_path, order_bytes)
            if not draft_path.is_file():  # never overwrite the agent's own draft
                worker.write_json(draft_path, infographic_draft_plan(parent_kp, revision_mode, revision_id))
            job = worker.read_json(worker.job_file(root, parent_job["job_id"]))
            if not any(row.get("stage") == WORK_ORDER_ISSUED_STAGE and row.get("action") == worker.INFOGRAPHIC_WORK_ORDER_ACTION
                       and row.get("revisions_before") == cycle for row in job.get("rail_events", [])):
                job.setdefault("rail_events", []).append({
                    "id": uuid.uuid4().hex, "stage": WORK_ORDER_ISSUED_STAGE, "action": worker.INFOGRAPHIC_WORK_ORDER_ACTION,
                    "status": "completed", "started_at": began, "ended_at": worker.iso_now(),
                    "program_seconds": round(time.perf_counter() - started, 6),
                    "revisions_before": cycle, "rebuild": bool(rebuild)})
                worker.write_v2_job(root, job)
    return continuation_order({
        "status": "needs_input", "action": "prepare_infographic_revision", "source_id": source_id, "rebuild": rebuild,
        "work_order_file": relative(order_path), "draft_file": relative(draft_path),
        "required_reads": [INFOGRAPHIC_SPEC], "check": check, "submit": submit,
        "instructions": INFOGRAPHIC_POINTER_INSTRUCTIONS,
    })


def carry_infographic_work_order_issued(root, parent_job_id, child_job_id, child_revision_id, package):
    """Copy the parent's work_order_issued event into the new revision job, once.

    Only the event of this revision's own cycle is carried, so a later --rebuild
    never inherits an earlier cycle's issue time.
    """
    child = worker.read_json(worker.job_file(root, child_job_id))
    if any(row.get("stage") == WORK_ORDER_ISSUED_STAGE and row.get("action") == worker.INFOGRAPHIC_WORK_ORDER_ACTION
           for row in child.get("rail_events", [])):
        return
    parent = worker.read_json(worker.job_file(root, parent_job_id))
    cycle = len([name for name in revision_directories(package) if name != child_revision_id])
    event = next((row for row in reversed(parent.get("rail_events", []))
                  if row.get("stage") == WORK_ORDER_ISSUED_STAGE and row.get("action") == worker.INFOGRAPHIC_WORK_ORDER_ACTION
                  and row.get("revisions_before") == cycle), None)
    if event is None:
        return
    child.setdefault("rail_events", []).insert(0, {**event, "carried_from_job": parent_job_id})
    worker.write_v2_job(root, child)


def resolve_infographic_parent(root: Path, source_id: str, rebuild: bool):
    """Shared start of add-infographic and its zero-write check.

    Returns ``(early_order, context)``: an early order stops the flow before any
    package, job or event is created; otherwise the context names the parent.
    """
    parent_job, package, parent_kp_path, parent_kp = completed_source_job(
        root, source_id, allow_infographic_resume=True,
    )
    parent_source = parent_kp.get("source") or {}
    try:
        parent_bundle = worker.read_json(package / "input_bundle.json")
    except (OSError, ValueError):
        parent_bundle = {}
    if worker.TRANSCRIPT_ARCHIVE_MODE in {
        (parent_bundle or {}).get("source_retention_mode"),
        parent_source.get("retention_mode"),
        parent_source.get("source_retention_mode"),
    }:
        # The complete transcript lives only in the package archive, so a formal
        # infographic would have no verbatim source body to stand on. Stop before
        # any package, job or event is created.
        return {
            "status": "blocked", "action": "stop", "source_id": source_id,
            "write_performed": False,
            "reason": "字幕存檔來源不支援補圖：完整字幕只在工作包存檔，正式資訊圖需要逐字來源正文。",
        }, None
    parent_asset_mode = parent_source.get(
        "asset_mode", worker.ASSET_MODE_FORMAL,
    )
    if parent_asset_mode == worker.ASSET_MODE_FORMAL and not rebuild:
        completion = worker.completed_receipt_readback(root, package, parent_kp_path, parent_kp)
        if completion is None:
            raise worker.CompilerWorkerV2Error("completed formal infographic receipt is missing")
        completion_path = package / "completion_receipt.json"
        return continuation_order({
            "status": "completed", "action": "stop", "source_id": source_id,
            "already_exists": True, "write_performed": False,
            "completion": {
                "readback": "verified",
                "completion_receipt": completion_path.relative_to(root).as_posix(),
                "completion_payload_sha256": completion["payload_sha256"],
            },
            "next": "The existing verified infographic remains current; use --rebuild only for an explicit formal revision.",
        }), None
    pointer = worker.read_current_infographic_pointer(root, package, parent_kp)
    if pointer is not None and not rebuild:
        return continuation_order({
            "status": "completed", "action": "stop", "source_id": source_id,
            "already_exists": True, "write_performed": False,
            "infographic_current_pointer": pointer,
            "completion": {
                "readback": "verified",
                "completion_receipt": pointer["current_completion_path"],
                "completion_payload_sha256": pointer["current_completion_payload_sha256"],
            },
        }), None
    return None, SimpleNamespace(
        parent_job=parent_job, package=package, parent_kp_path=parent_kp_path, parent_kp=parent_kp,
        parent_asset_mode=parent_asset_mode, pointer=pointer,
        revision_mode=(
            "knowledge_only_to_infographic"
            if parent_asset_mode == worker.ASSET_MODE_KNOWLEDGE
            else "formal_infographic_revision"
        ),
    )


def require_revision_plan_binding(plan, revision_id, pointer, rebuild):
    """The rail-level plan checks both add-infographic --response-file and the zero-write check run first."""
    if revision_id and plan.get("revision_id") != revision_id:
        raise worker.CompilerWorkerV2Error("revision id does not match the infographic revision plan")
    if pointer is not None and rebuild and plan.get("revision_id") == pointer.get("current_revision_id"):
        raise worker.CompilerWorkerV2Error(
            "rebuilding an existing infographic requires a new revision_id"
        )


def add_infographic(
    root: Path,
    source_id: str | None,
    response_file: Path | None,
    revision_id: str | None,
    *,
    rebuild: bool = False,
):
    """Open or resume the one formal infographic child of a text-first source."""

    if not source_id:
        return {
            "status": "ready", "action": "select_infographic_source",
            "write_performed": False,
            "candidates": list_infographic_candidates(root),
            "next": "Choose one listed source_id, then rerun add-infographic with that source_id.",
        }
    early, parent = resolve_infographic_parent(root, source_id, rebuild)
    if early is not None:
        return early
    package, parent_kp_path, parent_kp = parent.package, parent.parent_kp_path, parent.parent_kp
    existing = active_infographic_addition(root, package)
    if existing is not None:
        child_kp_path, child_kp = existing
        child_job_id = str(child_kp.get("job_id") or "")
        if not worker.job_file(root, child_job_id).is_file():
            origin_path = child_kp_path.parent / "revision_origin.json"
            origin = worker.read_json(origin_path) if origin_path.is_file() else {}
            plan_value = str(origin.get("revision_plan_path") or "")
            if not plan_value:
                raise worker.CompilerWorkerV2Error(
                    "incomplete infographic revision has no accepted plan to resume"
                )
            result = invoke(
                worker.command_prepare_infographic_revision,
                root=root,
                kp_file=parent_kp_path,
                plan_file=worker.relative_to_root(root / plan_value, root, "infographic revision plan"),
                revision_id=str((child_kp.get("revision") or {}).get("revision_id") or ""),
                recover_incomplete=False,
            )
            return accepted_revision_order(root, parent, result)
        return work_order(root, child_job_id)
    if response_file is None:
        return deliver_infographic_work_order(
            root, parent.parent_job, package, parent_kp_path, parent_kp, source_id, parent.revision_mode, rebuild,
        )
    plan_path = worker.relative_to_root(response_file, root, "infographic revision plan")
    plan = worker.read_json(plan_path)
    require_revision_plan_binding(plan, revision_id, parent.pointer, rebuild)
    result = invoke(
        worker.command_prepare_infographic_revision,
        root=root,
        kp_file=parent_kp_path,
        plan_file=plan_path,
        revision_id=revision_id,
        recover_incomplete=False,
    )
    return accepted_revision_order(root, parent, result)


def accepted_revision_order(root, parent, prepared):
    """Carry the planning-window event into the new revision job, then return its work order."""
    child_kp = worker.read_json(root / prepared["knowledge_package"])
    child_job_id = str(child_kp.get("job_id") or "")
    carry_infographic_work_order_issued(
        root, parent.parent_job["job_id"], child_job_id,
        str((child_kp.get("revision") or {}).get("revision_id") or ""), parent.package,
    )
    return work_order(root, child_job_id)


def infographic_check_issue(path, message, fix="", *, code="plan", page_id="", coverage_id=""):
    return {"code": code, "path": path, "page_id": page_id, "coverage_id": coverage_id,
            "message": str(message)[:FAILURE_MESSAGE_MAX_CHARS], "fix": fix}


def revision_collision_message(root, package, revision_id, job_id, plan_path):
    """Read-only twin of prepare's collision rule: an existing package is fine only for this very plan."""
    target = package / "revisions" / revision_id
    if target.exists():
        origin_path = target / "revision_origin.json"
        origin = worker.read_json(origin_path) if origin_path.is_file() else {}
        if origin.get("revision_plan_sha256") == worker.sha256_bytes(plan_path.read_bytes()):
            return ""
        return "infographic revision package already exists with different content; choose a new revision_id"
    if worker.job_file(root, job_id).exists():
        return "infographic revision job_id already exists; choose a new revision_id"
    return ""


def check_infographic_plan(root, source_id, response_file, *, revision_id=None, rebuild=False):
    """Zero-write dry run of ``add-infographic --response-file``.

    It runs the prepare validations the submit path runs, then the compile
    plan preflight on the revision Knowledge Package the submit would write,
    and returns the reader text and reader_page_sha256 of every page.
    """
    issues, pages, context = [], [], {}
    try:
        early, parent = resolve_infographic_parent(root, source_id, rebuild)
        if early is not None:
            issues.append(infographic_check_issue("source_id", early.get("reason") or "no infographic is needed: the source already has a current infographic", code="source_state"))
        elif active_infographic_addition(root, parent.package) is not None:
            issues.append(infographic_check_issue("source_id", "an infographic revision is already prepared for this source; continue its job instead of submitting a plan", code="source_state"))
        else:
            plan_path = worker.relative_to_root(response_file, root, "infographic revision plan")
            plan = worker.read_json(plan_path)
            context = _check_prepare_validations(root, parent, plan, plan_path, revision_id, rebuild, issues)
            if context.get("candidate") is not None:
                pages = _check_compile_preflight(root, parent, plan, context, issues)
    except (worker.CompilerWorkerV2Error, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        lines = [line for line in str(exc).split("\n") if line.strip()] or [type(exc).__name__]
        issues.extend(infographic_check_issue("response_file", line, code="unreadable") for line in lines)
    shown = issues[:CHECK_ERROR_MAX_COUNT]
    result = {
        "status": "failed" if issues else "passed", "source_id": source_id,
        "revision_id": context.get("revision_id"), "issue_count": len(issues), "issues": shown,
        "pages": pages, "write_performed": False,
    }
    if not issues:
        result["next"] = "Submit with the add-infographic command from the work order; the reader text and sha above are the ones the submit accepts."
    return result


def _revision_error_path(message):
    text = str(message).lower()
    if "completion receipt" in text:
        return "completion_receipt"
    if "job_id" in text:
        return "job_id"
    if "source metadata" in text:
        return "source_updates"
    if "original source" in text:
        return "source_id"
    if "revision id" in text or "revision_id" in text or "rebuilding" in text:
        return "revision_id"
    return "plan"


def _check_prepare_validations(root, parent, plan, plan_path, revision_id, rebuild, issues):
    """The validations add-infographic --response-file runs before preparing the revision, collected without a write."""
    package, parent_kp = parent.package, parent.parent_kp
    if plan.get("schema_version") != worker.INFOGRAPHIC_REVISION_PLAN_VERSION:
        issues.append(infographic_check_issue("schema_version", "unsupported infographic revision plan", code="revision_plan"))
        return {}
    if plan.get("mode") not in {"knowledge_only_to_infographic", "formal_infographic_revision"}:
        issues.append(infographic_check_issue("mode", "check-infographic-plan covers knowledge_only_to_infographic and formal_infographic_revision plans", code="revision_plan"))
        return {}
    try:
        require_revision_plan_binding(plan, revision_id, parent.pointer, rebuild)
        completion = worker.require_original_infographic_completion(
            parent.parent_kp_path, package / "completion_receipt.json", root=root,
        )
        rid, source, revision_mode, knowledge_only = worker.infographic_revision_identity(parent_kp, plan, revision_id)
        job_id = worker.validated_identifier(str(plan.get("job_id") or f"CW2-{rid}"), "job_id")
    except worker.CompilerWorkerV2Error as exc:
        issues.append(infographic_check_issue(_revision_error_path(exc), exc, code="revision_plan"))
        return {}
    for item in worker.infographic_revision_content_issues(parent_kp, plan, knowledge_only):
        issues.append(infographic_check_issue(item["path"], item["message"], code="revision_plan"))
    collision = revision_collision_message(root, package, rid, job_id, plan_path)
    if collision:
        issues.append(infographic_check_issue("revision_id", collision, code="revision_plan"))
    context = {"revision_id": rid}
    if not (isinstance(plan.get("coverage_units"), list) and isinstance(plan.get("infographics"), list)):
        return context
    try:
        context["candidate"] = worker.build_infographic_revision_kp(
            root, parent_kp, package, package / "completion_receipt.json", completion,
            coverage_units=plan["coverage_units"], infographics=plan["infographics"],
            source_updates=plan.get("source_updates") if isinstance(plan.get("source_updates"), dict) else {},
            job_id=job_id, revision_id=rid, revision_mode=revision_mode, knowledge_only_to_infographic=knowledge_only,
            plan_sha256=worker.sha256_bytes(plan_path.read_bytes()),
            original_semantic_content_sha256=worker.revision_semantic_content_sha256(parent_kp) if knowledge_only else None,
        )
    except worker.CompilerWorkerV2Error as exc:
        if not issues:  # a content defect above already explains it
            issues.append(infographic_check_issue("coverage_units", exc, code="revision_plan"))
    return context


def _check_compile_preflight(root, parent, plan, context, issues):
    """The compile-time plan preflight on the candidate revision package, plus per-page reader text and sha."""
    package, candidate = parent.package, context["candidate"]
    plan_issues = worker.infographic_plan_preflight_issues(candidate, package, phase="plan")
    issues.extend(plan_issues)
    seen = {issue["message"] for issue in issues}
    source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
    pages_in = [page for page in candidate.get("infographics") or [] if isinstance(page, dict)]
    canonical_body = (package / "canonical_source.md").read_text(encoding="utf-8").rstrip("\n")
    for message in worker.infographic_plan_contract_errors(candidate, package, source, canonical_body, pages_in):
        if message not in seen:  # the preflight normally reports every one of these with a field path
            seen.add(message)
            issues.append(infographic_check_issue("infographics", message, "Correct the named contract field.", code="plan_contract"))
    if all(issue.get("code") == "reader_comprehension" for issue in issues):
        # The page rendering compile would run next (title rule, prompt contract); in memory only, nothing is locked or written.
        dry = copy.deepcopy(candidate)
        dry_source = dry.get("source") if isinstance(dry.get("source"), dict) else {}
        dry_pages = [page for page in dry.get("infographics") or [] if isinstance(page, dict)]
        for page in dry_pages:
            worker.apply_compile_page_rules(dry_source, page, worker.date.today().isoformat())
        for page in dry_pages:
            try:
                worker.compiled_infographic_page(dry, package, page)
            except worker.CompilerWorkerV2Error as exc:
                page_id = str(page.get("page_id") or "")
                issues.append(infographic_check_issue(f"infographics[{page_id}]", exc, "Correct the page fields named in the message.", code="compile_page", page_id=page_id))
    templates = {item["page_id"]: item for item in worker.infographic_plan_text_template(candidate)}
    pages = []
    for page in pages_in:
        page_id = str(page.get("page_id") or "")
        sha = reader_page_sha256(page)
        entry = {
            "page_id": page_id, "reader_page": reader_page_payload(page), "reader_page_sha256": sha,
            "reader_review_matches": isinstance(page.get("reader_review"), dict) and page["reader_review"].get("page_sha256") == sha,
        }
        if source.get("primary_work_status") != "primary" and page.get("title"):
            normalized = worker.reader_infographic_title(page.get("title"))
            if normalized != page.get("title"):
                entry["compile_rewrites_title_to"] = normalized
                issues.append(infographic_check_issue(
                    f"infographics[{page_id}].title",
                    f"{page_id} title would be rewritten at compile to {normalized!r}, which changes reader_page_sha256",
                    "Write the reader-form title in the plan, then re-run the isolated reader review on the new hash.",
                    code="title_not_reader_form", page_id=page_id))
        template = templates.get(page_id)
        if template:
            entry["text_template"] = {key: template[key] for key in ("content_map", "visible_text", "missing_reviewed_coverage_ids")}
        pages.append(entry)
    return pages


def capture(root, job, youtube_capture=None):
    options = job.get("rail_capture", {})
    common = {"root": root, "source_id": job["source_id"], "job_id": job["job_id"],
              "title": options.get("title"), "slug": options.get("slug"), "profile": options.get("profile", "article"),
              "asset_mode": options.get("asset_mode", worker.ASSET_MODE_FORMAL)}
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
    begin.add_argument("--asset-mode", choices=(worker.ASSET_MODE_FORMAL, worker.ASSET_MODE_KNOWLEDGE))
    begin.add_argument("--capture-mode", choices=(worker.URL_CAPTURE_API_FIRST, worker.URL_CAPTURE_DIRECT_HTML_ONLY), default=worker.URL_CAPTURE_API_FIRST)
    add_infographic_command = commands.add_parser("add-infographic")
    add_infographic_command.add_argument("--source-id")
    add_infographic_command.add_argument("--response-file", type=Path)
    add_infographic_command.add_argument("--revision-id")
    add_infographic_command.add_argument("--rebuild", action="store_true")
    check_infographic = commands.add_parser("check-infographic-plan")
    check_infographic.add_argument("--source-id", required=True)
    check_infographic.add_argument("--response-file", type=Path, required=True)
    check_infographic.add_argument("--revision-id")
    check_infographic.add_argument("--rebuild", action="store_true")
    for command in ("prepare-content-revision", "apply-content-revision"):
        revision = commands.add_parser(command)
        revision.add_argument("--job-id", required=True)
        if command == "prepare-content-revision":
            revision.add_argument("--response-file", type=Path, required=True)
            revision.add_argument("--authorization", required=True)
            revision.add_argument("--reason", required=True)
        else:
            revision.add_argument("--revision-file", type=Path, required=True)
            revision.add_argument("--review-file", type=Path, required=True)
            revision.add_argument("--dry-run", action="store_true")
    recovery = commands.add_parser("prepare-infographic-recovery")
    recovery.add_argument("--job-id", required=True, help="the infographic child job written by the old full writer")
    recovery.add_argument("--evidence-transaction", type=Path, action="append", required=True)
    recovery.add_argument("--authorization", required=True)
    recovery.add_argument("--reason", required=True)
    for command in ("inspect", "advance", "amend-application", "submit-plan", "check-plan", "image-analysis", "image-result", "image-reconsider", "refresh-outcome", "refresh-case-evidence", "repair-case-plan", "rebind-source-id", "rebind-recovery"):
        sub = commands.add_parser(command)
        sub.add_argument("--job-id", required=True)
        if command in {"amend-application", "submit-plan", "check-plan", "image-analysis", "image-result", "image-reconsider", "repair-case-plan", "rebind-source-id"}:
            sub.add_argument("--response-file", type=Path, required=True)
        if command == "rebind-recovery":
            sub.add_argument("--apply", action="store_true", help="move only proven stale partial artifacts into the job recovery package")
        sub.add_argument("--vault", type=Path, default=Path(os.environ.get("CREATIVE_SECOND_BRAIN_VAULT", "knowledge-base-vault")))
    args = parser.parse_args()
    root = args.root.resolve()
    try:
        if args.command not in {"schema", "inspect", "check-plan", "check-infographic-plan"} and not (args.command == "rebind-recovery" and not args.apply):
            assert_management_idle(root)
        if args.command == "schema":
            result = plan_schema()
        elif args.command == "inspect":
            result = work_order(root, args.job_id)
        elif args.command == "check-plan":
            result = check_plan(root, args.job_id, worker.relative_to_root(args.response_file, root, "semantic response"))
        elif args.command == "check-infographic-plan":
            result = check_infographic_plan(root, args.source_id, args.response_file,
                                            revision_id=args.revision_id, rebuild=args.rebuild)
        else:
            with exclusive(root):
                if args.command == "start":
                    result = start(args)
                elif args.command == "advance":
                    result = advance(root, args.job_id, args.vault)
                elif args.command == "add-infographic":
                    result = add_infographic(
                        root, args.source_id, args.response_file, args.revision_id,
                        rebuild=args.rebuild,
                    )
                elif args.command == "prepare-infographic-recovery":
                    try:
                        from . import completion_revisions
                    except ImportError:
                        import completion_revisions
                    job, package, kp_path = paths(root, args.job_id)
                    result = completion_revisions.prepare_infographic_recovery(
                        root, kp_path,
                        evidence_transactions=[worker.relative_to_root(value, root, "recovery evidence transaction") for value in args.evidence_transaction],
                        authorization=args.authorization, reason=args.reason)
                elif args.command in {"prepare-content-revision", "apply-content-revision"}:
                    try:
                        from . import completion_revisions
                    except ImportError:
                        import completion_revisions
                    job, package, kp_path = paths(root, args.job_id)
                    if args.command == "prepare-content-revision":
                        result = completion_revisions.prepare_content_revision(root, kp_path, args.response_file,
                            authorization=args.authorization, reason=args.reason)
                    else:
                        journal = worker.relative_to_root(args.revision_file, root, "content revision transaction")
                        transaction = worker.read_json(journal)
                        if transaction.get("kp_path") != kp_path.relative_to(root).as_posix():
                            raise ValueError("content revision belongs to another job")
                        result = completion_revisions.apply_content_revision(root, journal, args.review_file, dry_run=args.dry_run)
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
                        measured(root, args.job_id, "submit_plan", submit_plan,
                                 root=root, job_id=args.job_id, response_path=response_path)
                        result = advance(root, args.job_id, args.vault)
                    elif args.command == "image-analysis":
                        result = submit_image_analysis(root, args.job_id, response_path)
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
        return 2 if result.get("status") == "blocked" else 1 if result.get("status") == "failed" else 0
    except worker.InfographicPlanPreflightError as exc:
        print(json.dumps({"status": "blocked", "stage": "plan_preflight", "issues": exc.issues, "write_performed": False}, ensure_ascii=False))
        return 2
    except (worker.CompilerWorkerV2Error, ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
