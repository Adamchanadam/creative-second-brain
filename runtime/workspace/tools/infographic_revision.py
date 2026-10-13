"""Explicit same-job text revisions; immutable ancestors and cumulative attempts.

The compiler remains the entry point and writer. This module owns only the
revision transaction and its history/dispatch invariants.
"""
import copy
import json
import shutil
from pathlib import Path


def prepare(w, args, plan):
    root = args.root.resolve()
    old_path = w.relative_to_root(args.kp_file, root, "revision origin")
    old = w.read_json(old_path)
    job_id = old["job_id"]
    revision_id = w.validated_identifier(str(plan.get("revision_id") or ""), "revision_id")
    if args.revision_id and args.revision_id != revision_id:
        raise w.CompilerWorkerV2Error("revision id mismatch")
    if plan.get("job_id") != job_id or plan.get("source_id") != old["source"]["source_id"] or plan.get("source_identity") != old["source"]["source_identity"]:
        raise w.CompilerWorkerV2Error("text revision must retain the original job and source")
    auth = plan.get("authorization") or {}
    if auth.get("authority") != "current_user_message" or auth.get("additional_attempts_per_page") != 1 or not str(auth.get("user_instruction") or "").strip():
        raise w.CompilerWorkerV2Error("text revision requires explicit one-edit-per-page authorization")
    pages = plan.get("infographics")
    if not isinstance(pages, list) or [p.get("page_id") for p in pages] != [p["page_id"] for p in old["infographics"]] or any(p.get("status") != "planned" for p in pages):
        raise w.CompilerWorkerV2Error("same-job text revision must preserve page identities")
    receipt_path = old_path.parent / "completion_receipt.json"
    receipt = w.read_json(receipt_path)
    if receipt.get("status") != "completed" or receipt.get("payload_sha256") != w.canonical_payload_sha256(receipt) or receipt.get("knowledge_package_sha256") != w.sha256_bytes(old_path.read_bytes()):
        raise w.CompilerWorkerV2Error("revision requires immutable completed origin")
    target = w.relative_to_root(old_path.parent / "revisions" / revision_id, root, "revision package")
    job = w.read_json(w.job_file(root, job_id))
    plan_path = w.relative_to_root(args.plan_file, root, "revision plan")
    plan_hash = w.sha256_bytes(plan_path.read_bytes())
    origin_path = target / "revision_origin.json"
    if origin_path.exists():
        origin = w.read_json(origin_path)
        if origin.get("revision_plan_sha256") != plan_hash:
            raise w.CompilerWorkerV2Error("revision already exists with another plan")
        verify_origin(w, root, target)
        if job.get("package_dir") != target.relative_to(root).as_posix():
            # Recover only the prepared transaction whose live job is unchanged.
            if w.sha256_bytes(w.job_file(root, job_id).read_bytes()) != origin["previous_job_file_sha256"]:
                raise w.CompilerWorkerV2Error("interrupted revision job changed; cannot activate")
            for name, key in (("knowledge_package.json", "prepared_candidate_sha256"), ("revision_next_job.json", "prepared_next_job_sha256")):
                if w.canonical_payload_sha256(w.read_json(target / name)) != origin[key]:
                    raise w.CompilerWorkerV2Error("prepared revision changed before activation")
            w.mirror_revision_input_evidence(old_path.parent, target, job_id=job_id, source=old["source"])
            w.write_v2_job(root, w.read_json(target / "revision_next_job.json"))
        w.validate_job_package_binding(root, target / "knowledge_package.json", w.read_json(target / "knowledge_package.json"))
        return result(root, target, revision_id, False)
    pending_path = target / "revision_pending.json"
    if target.exists() and any(target.iterdir()):
        pending = w.read_json(pending_path) if pending_path.is_file() else {}
        if pending.get("revision_plan_sha256") != plan_hash or pending.get("previous_job_file_sha256") != w.sha256_bytes(w.job_file(root, job_id).read_bytes()):
            raise w.CompilerWorkerV2Error("incomplete revision staging has conflicting transaction evidence")
    w.completed_receipt_readback(root, old_path.parent, old_path, old)
    if job.get("package_dir") != old_path.parent.relative_to(root).as_posix() or job.get("status") != "completed":
        raise w.CompilerWorkerV2Error("only the currently completed job can be revised")
    candidate = copy.deepcopy(old)
    candidate["infographics"] = copy.deepcopy(pages)
    revised_coverage = plan.get("coverage_units")
    if not isinstance(revised_coverage, list) or len(revised_coverage) != len(old["coverage_units"]):
        raise w.CompilerWorkerV2Error("text revision must retain evidence coverage")
    for before, after in zip(old["coverage_units"], revised_coverage):
        if {k:v for k,v in before.items() if k not in {"infographic_text", "source_evidence"}} != {k:v for k,v in after.items() if k not in {"infographic_text", "source_evidence"}}:
            raise w.CompilerWorkerV2Error("text revision may refine copy and exact canonical citations only")
    candidate["coverage_units"] = copy.deepcopy(revised_coverage)
    # Source, analysis and evidence coverage remain immutable; this operation edits
    # presentation text, not the source or the accepted analysis.
    prior_revision = old.get("revision") or {}
    slug = prior_revision.get("source_slug") or old_path.parent.name.split("_", 1)[1]
    candidate["revision"] = {
        "kind": "infographic_revision", "mode": "same_job_text_revision",
        "revision_id": revision_id, "source_slug": slug,
        "asset_slug": w.bounded_slug(f"{slug}_{revision_id}"),
        "origin_package": prior_revision.get("origin_package") or old_path.parent.relative_to(root).as_posix(),
        "supersedes_completion_path": receipt_path.relative_to(root).as_posix(),
        "supersedes_completion_payload_sha256": receipt["payload_sha256"],
        "revision_plan_sha256": plan_hash,
    }
    issues = w.infographic_plan_preflight_issues(candidate, old_path.parent)
    if issues:
        raise w.InfographicPlanPreflightError(issues)
    ledger_path = w.image_attempt_ledger_path(old_path.parent)
    ledger = w.load_image_attempt_ledger(old_path.parent, old["source"]["source_identity"])
    history = {}
    references = {}
    for page in old["infographics"]:
        record = ledger["pages"][page["page_id"]]
        cumulative = copy.deepcopy(record.get("prior_attempts", []) + record["attempts"])
        for attempt in cumulative:
            attempt.setdefault("locked_prompt_sha256", record["locked_prompt_sha256"])
        if not cumulative or cumulative[-1].get("status") != "accepted":
            raise w.CompilerWorkerV2Error("text revision requires the accepted original PNG")
        check_sequence(w, cumulative)
        history[page["page_id"]] = cumulative
        asset = w.relative_to_root(old_path.parent / page["asset_path"], root, "accepted reference PNG")
        if w.sha256_bytes(asset.read_bytes()) != page["png_sha256"]:
            raise w.CompilerWorkerV2Error("accepted reference PNG changed")
        references[page["page_id"]] = {"path": asset.relative_to(root).as_posix(), "sha256": page["png_sha256"]}
    origin = {
        "schema_version": "same_job_text_revision_v1", "job_id": job_id,
        "revision_id": revision_id, "revision_plan_sha256": plan_hash,
        "authorization": auth, "original_package": old_path.parent.relative_to(root).as_posix(),
        "original_knowledge_package_sha256": w.sha256_bytes(old_path.read_bytes()),
        "original_completion_sha256": w.sha256_bytes(receipt_path.read_bytes()),
        "original_ledger_sha256": w.sha256_bytes(ledger_path.read_bytes()),
        "previous_job_file_sha256": w.sha256_bytes(w.job_file(root, job_id).read_bytes()),
        "history": history, "references": references,
    }
    origin["payload_sha256"] = w.canonical_payload_sha256(origin)
    target.mkdir(parents=True, exist_ok=True)
    def save_json(path, value):
        if path.exists():
            if w.read_json(path) != value:
                raise w.CompilerWorkerV2Error("prepared revision file changed: " + path.name)
        else:
            w.write_json(path, value)
    save_json(pending_path, {"revision_plan_sha256": plan_hash, "previous_job_file_sha256": origin["previous_job_file_sha256"]})
    w.mirror_revision_input_evidence(old_path.parent, target, job_id=job_id, source=old["source"])
    if (old_path.parent / "references").is_dir():
        for reference in (old_path.parent / "references").rglob("*"):
            if not reference.is_file():
                continue
            w.relative_to_root(reference, root, "supporting source")
            destination = target / reference.relative_to(old_path.parent)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.read_bytes() != reference.read_bytes():
                    raise w.CompilerWorkerV2Error("supporting source changed during preparation")
            else:
                shutil.copy2(reference, destination)
    save_json(target / "knowledge_package.json", candidate)
    # Byte-identical job snapshot keeps the historical completion replayable.
    previous_job = target / "revision_previous_job.json"
    if previous_job.exists():
        if previous_job.read_bytes() != w.job_file(root, job_id).read_bytes():
            raise w.CompilerWorkerV2Error("previous job snapshot changed")
    else:
        previous_job.write_bytes(w.job_file(root, job_id).read_bytes())
    next_job = copy.deepcopy(job)
    next_job.setdefault("revision_history", []).append({
        "package_dir": job["package_dir"],
        "snapshot_path": (target / "revision_previous_job.json").relative_to(root).as_posix(),
        "snapshot_sha256": origin["previous_job_file_sha256"],
        "knowledge_package_sha256": origin["original_knowledge_package_sha256"],
    })
    for key in list(next_job):
        if key.startswith("commit_") or key in {"execution_contract", "execution_policy_version", "completion_receipt_path", "completed_at", "images_ready_at", "outcome_recovery"}:
            next_job.pop(key)
    next_job.update(package_dir=target.relative_to(root).as_posix(), status="running", revision_id=revision_id, job_kind="infographic_revision")
    save_json(target / "revision_next_job.json", next_job)
    origin["prepared_candidate_sha256"] = w.canonical_payload_sha256(candidate)
    origin["prepared_next_job_sha256"] = w.canonical_payload_sha256(next_job)
    origin["payload_sha256"] = w.canonical_payload_sha256(origin)
    save_json(origin_path, origin)
    verify_origin(w, root, target)
    w.write_v2_job(root, next_job)
    return result(root, target, revision_id, True)


def result(root, target, revision_id, wrote):
    return {"status": "prepared", "revision_id": revision_id, "same_job": True,
            "package_dir": target.relative_to(root).as_posix(),
            "knowledge_package": (target / "knowledge_package.json").relative_to(root).as_posix(),
            "write_performed": wrote}


def check_sequence(w, attempts):
    if any(not isinstance(a, dict) or a.get("attempt_number") != i for i, a in enumerate(attempts, 1)):
        raise w.CompilerWorkerV2Error("cumulative attempt history is not contiguous")


def verify_origin(w, root, package):
    origin = w.read_json(package / "revision_origin.json")
    if origin.get("schema_version") != "same_job_text_revision_v1" or origin.get("payload_sha256") != w.canonical_payload_sha256(origin):
        raise w.CompilerWorkerV2Error("same-job revision origin binding mismatch")
    old = w.relative_to_root(root / origin["original_package"], root, "immutable revision ancestor")
    for name, key in (("knowledge_package.json", "original_knowledge_package_sha256"), ("completion_receipt.json", "original_completion_sha256"), ("imagegen_attempts.json", "original_ledger_sha256")):
        if w.sha256_bytes((old / name).read_bytes()) != origin[key]:
            raise w.CompilerWorkerV2Error("immutable revision ancestor changed: " + name)
    prior_kp = w.read_json(old / "knowledge_package.json")
    prior_ledger = w.read_json(old / "imagegen_attempts.json")
    if (prior_kp.get("revision") or {}).get("mode") == "same_job_text_revision":
        verify_origin(w, root, old)
    for page_id, history in origin["history"].items():
        record = prior_ledger["pages"][page_id]
        expected = copy.deepcopy(record.get("prior_attempts", []) + record["attempts"])
        for attempt in expected:
            attempt.setdefault("locked_prompt_sha256", record["locked_prompt_sha256"])
        if history != expected:
            raise w.CompilerWorkerV2Error("revision attempt history was reset or changed")
        check_sequence(w, history)
    return origin


def historical_job(w, root, job, kp_path):
    expected = kp_path.parent.relative_to(root).as_posix()
    if job.get("package_dir") == expected:
        return job
    for item in job.get("revision_history", []):
        if item.get("package_dir") != expected:
            continue
        snapshot = w.relative_to_root(root / item["snapshot_path"], root, "historical job snapshot")
        if w.sha256_bytes(snapshot.read_bytes()) != item["snapshot_sha256"] or w.sha256_bytes(kp_path.read_bytes()) != item["knowledge_package_sha256"]:
            raise w.CompilerWorkerV2Error("historical job snapshot drift")
        return w.read_json(snapshot)
    return job


def dispatch_contract(w, root, package, infographic):
    origin = verify_origin(w, root, package)
    page_id = infographic["page_id"]
    ref = origin["references"][page_id]
    asset = w.relative_to_root(root / ref["path"], root, "text revision reference")
    if w.sha256_bytes(asset.read_bytes()) != ref["sha256"]:
        raise w.CompilerWorkerV2Error("text revision reference drift")
    prompt_path, _, _ = w.locked_infographic_context(package, infographic)
    prompt = ("Edit the supplied accepted infographic. Preserve its accepted Bauhaus hand-drawn style, palette, paper texture, canvas format, and visual hierarchy. "
              "Replace the old wording with ALL the newly approved text below. Fit complete sentences legibly; do not shorten, invent, omit, or merge clauses. "
              "Use the approved semantic grouping and reading order; reflow within the existing visual language as needed for comprehension. "
              "Remove obsolete old wording. The image is the style reference; the new locked text is authoritative.\n" + prompt_path.read_text(encoding="utf-8").rstrip("\n"))
    return {"edit_prompt": prompt, "edit_prompt_sha256": w.sha256_text(prompt),
            "referenced_image_path": ref["path"], "reference_png_sha256": ref["sha256"],
            "imagegen_tool_arguments": {"prompt": prompt, "referenced_image_paths": [str(asset.resolve())], "transparent_background": False}}


def correction_contract(w, root, package, kp, page, prior, authorization):
    expected = {'schema': 'infographic_text_correction_authorization_v1',
                'authorized_by': 'user', 'job_id': kp['job_id'],
                'source_identity': kp['source']['source_identity'], 'page_id': page['page_id'],
                'failed_action_identity': prior[-1]['action_identity'],
                'failed_png_sha256': prior[-1]['png_sha256'],
                'previous_attempts_sha256': w.sha256_text(json.dumps(prior, ensure_ascii=False, sort_keys=True)),
                'additional_attempts': 1, 'preserve_all_other_content': True}
    if (not isinstance(authorization, dict) or any(authorization.get(k) != v for k,v in expected.items())
            or len(str(authorization.get('user_request') or '')) < 6
            or prior[-1].get('status') != 'blocking_failure'
            or prior[-1].get('failure_code') != 'wrong_or_missing_locked_text'):
        raise w.CompilerWorkerV2Error('additional text correction requires explicit authorization bound to the retained failure')
    prompt_path, output, _ = w.locked_infographic_context(package, page)
    locked = prompt_path.read_text(encoding='utf-8').rstrip('\n')
    text = str(authorization.get('restore_text') or '').strip()
    if not text or not any(text in line for line in w.visible_text_from_prompt(locked)) or text not in str(prior[-1].get('semantic_evidence') or ''):
        raise w.CompilerWorkerV2Error('correction text must match both locked copy and observed failure')
    reference = w.relative_to_root(root / authorization['reference_path'], root, 'failed text reference')
    if reference.resolve() == output.resolve():
        raise w.CompilerWorkerV2Error('correction reference must be archived separately from the output')
    if not reference.is_file() or w.sha256_bytes(reference.read_bytes()) != expected['failed_png_sha256']:
        raise w.CompilerWorkerV2Error('failed text reference changed')
    prompt = ('Edit this infographic only to restore the missing exact text: 「' + text + '」. '
              'Place it with its associated sentence or flow, so the missing context is explicit. '
              'Preserve every other existing word, sentence, punctuation, footer, signature, illustration, style, colour and layout. '
              'Do not regenerate the whole composition or shorten any wording. The full approved text below is verification context; only restore the missing text.\n' + locked)
    return {'edit_prompt':prompt, 'edit_prompt_sha256':w.sha256_text(prompt),
            'referenced_image_path':authorization['reference_path'], 'reference_png_sha256':expected['failed_png_sha256'],
            'correction_authorization':authorization,
            'imagegen_tool_arguments':{'prompt':prompt,'referenced_image_paths':[str(reference.resolve())],'transparent_background':False}}


def authorize(w, root, package, kp, infographic, record, authorization=None):
    origin = verify_origin(w, root, package)
    if record["attempts"]:
        if not authorization:
            raise w.CompilerWorkerV2Error("explicit text revision has used its one authorized edit")
        return correction_contract(w, root, package, kp, infographic, record['attempts'], authorization)
    record["prior_attempts"] = copy.deepcopy(origin["history"][infographic["page_id"]])
    return dispatch_contract(w, root, package, infographic)


def validate_record(w, root, package, record):
    origin = verify_origin(w, root, package)
    history = origin["history"][record["page_id"]]
    if record.get("prior_attempts") != history:
        raise w.CompilerWorkerV2Error("text revision must retain all prior attempts")
    attempts = record["attempts"]
    if not attempts or any(a.get('attempt_number') != len(history)+i+1 or a.get('attempt_kind') != 'user_authorized_text_revision' for i,a in enumerate(attempts)):
        raise w.CompilerWorkerV2Error("text revision attempt count or kind mismatch")
    kp = w.read_json(package / "knowledge_package.json")
    page = w.infographic_page(kp, record["page_id"])
    for i,attempt in enumerate(attempts):
        expected = (dispatch_contract(w, root, package, page) if i == 0 else
                    correction_contract(w, root, package, kp, page, attempts[:i], (attempt.get('text_revision_dispatch') or {}).get('correction_authorization')))
        if attempt.get("text_revision_dispatch") != expected:
            raise w.CompilerWorkerV2Error("text revision dispatch contract changed or missing")
        if attempt.get("status") == "accepted":
            if not isinstance(attempt.get("imagegen_tool_arguments"), dict) or attempt["imagegen_tool_arguments"] != expected["imagegen_tool_arguments"]:
                raise w.CompilerWorkerV2Error("text revision dispatch did not use the approved wording")
            review = (attempt.get("semantic_readback") or {}).get("text_revision_review") or {}
            if review.get("accepted_style_preserved") is not True or review.get("new_text_complete") is not True or not str(review.get("evidence") or "").strip():
                raise w.CompilerWorkerV2Error("text revision lacks actual-image preservation review")


def count(record):
    return len(record.get("prior_attempts", [])) + len(record["attempts"])


def workspace(package):
    for parent in package.parents:
        if (parent / "brain.json").is_file() and (parent / "tools/compiler_worker_v2.py").is_file():
            return parent
    raise ValueError("revision package is outside a configured workspace")


def persist_action(w, package, ledger, envelope):
    journal_path = package / "text_revision_authorization.json"
    journal = {"state": "pending", "ledger": ledger, "envelope": envelope,
               "previous_ledger": w.read_json(w.image_attempt_ledger_path(package)) if w.image_attempt_ledger_path(package).exists() else None,
               "previous_envelope": w.read_json(w.image_action_envelope_path(package)) if w.image_action_envelope_path(package).exists() else None}
    journal["payload_sha256"] = w.canonical_payload_sha256(journal)
    w.write_json(journal_path, journal)
    recover_action(w, package)


def recover_action(w, package):
    path = package / "text_revision_authorization.json"
    if not path.is_file():
        return
    journal = w.read_json(path)
    if journal.get("payload_sha256") != w.canonical_payload_sha256(journal):
        raise w.CompilerWorkerV2Error("text revision authorization journal changed")
    if journal.get("state") == "committed":
        return
    for target, key, previous in ((w.image_attempt_ledger_path(package), "ledger", "previous_ledger"), (w.image_action_envelope_path(package), "envelope", "previous_envelope")):
        actual = w.read_json(target) if target.exists() else None
        if actual != journal[key] and actual != journal[previous]:
            raise w.CompilerWorkerV2Error("interrupted authorization conflicts with saved action")
    for target, key in ((w.image_attempt_ledger_path(package), "ledger"), (w.image_action_envelope_path(package), "envelope")):
        if not target.exists() or w.read_json(target) != journal[key]:
            w.write_json(target, journal[key])
    journal["state"] = "committed"
    journal["payload_sha256"] = w.canonical_payload_sha256(journal)
    w.write_json(path, journal)
