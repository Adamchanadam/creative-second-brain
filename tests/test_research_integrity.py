"""Repeatable root-fix acceptance for portable research integrity.

Run this against a freshly installed workspace.  It covers the counterexamples
that previously escaped route-only checks: nested custom vaults, source-ID
ownership, completed-run handoff, withdrawal preservation, and the optional
selection / source-interaction evidence contract.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path


PYTHON = sys.executable
DAY = "2026-10-07T12:00:00+00:00"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")


def write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Create a valid, intentionally light or dark non-interlaced RGB PNG."""
    row = bytes(rgb) * width
    payload = b"".join(b"\x00" + row for _ in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + png_chunk(b"IDAT", zlib.compress(payload))
        + png_chunk(b"IEND", b"")
    )


def run_program(workspace: Path, *items: str, expected: int = 0) -> dict:
    env = dict(os.environ, CREATIVE_SECOND_BRAIN_VAULT=json.loads((workspace / "brain.json").read_text(encoding="utf-8"))["vault"])
    tool = workspace / "tools" / "research_program.py"
    result = subprocess.run(
        [PYTHON, "-B", str(tool), "--root", str(workspace), *items],
        cwd=workspace,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != expected:
        raise AssertionError(f"expected {expected}, got {result.returncode}: {' '.join(items)}\n{result.stdout}\n{result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"research program did not return JSON: {result.stdout}\n{result.stderr}") from exc


def init_topic(workspace: Path, topic_id: str) -> None:
    run_program(
        workspace,
        "init-topic",
        "--topic-id", topic_id,
        "--title", "Portable acceptance topic",
        "--profile-version", "test-v1",
        "--purpose", "Verify state integrity without a private profile",
        "--scope", '{"boundary":"test"}',
        "--focus", '["portable evidence"]',
        "--evidence-contract", '{"asset_mode":"formal_infographic","source_input_kinds":["url"],"source_profiles":["article"]}',
        "--output-contract", '{"kind":"test"}',
        "--classifications", '{"primary_key":"family"}',
        "--mode", "manual_research",
        "--daily-case-cap", "3",
        "--total-case-cap", "3",
        "--budget-searches", "2",
        "--budget-candidate-summaries-target", "2",
        "--budget-deep-reads", "2",
    )


def topic_state_path(workspace: Path, vault: Path, topic_id: str) -> Path:
    return vault / "_registry" / "research-programs" / topic_id / "state.json"


def require_rejected(workspace: Path, *items: str, contains: str) -> None:
    payload = run_program(workspace, *items, expected=2)
    assert payload["status"] == "blocked" and contains in payload["reason"], payload


def test_custom_vault_collision(workspace: Path, vault: Path) -> None:
    sys.path.insert(0, str(workspace))
    from tools import compiler_worker_v2 as worker
    from tools.obsidian_vault_adapter import AdapterStop, assert_source_id_is_available
    from tools.workspace_paths import WorkspacePathError, vault_path, vault_relative_path

    for unsafe in ("../outside", "C:/outside", "/outside", "\\\\server\\share", "."):
        try:
            vault_relative_path(unsafe)
        except WorkspacePathError:
            pass
        else:
            raise AssertionError(f"unsafe vault path was accepted: {unsafe}")
    assert vault_path(workspace) == vault
    registry = vault / "_registry" / "sources.jsonl"
    registry.write_text(json.dumps({
        "source_id": "PUBLIC-9999", "source_identity": "url:owned-source", "status": "linked",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        worker.ensure_accepted_job(
            root=workspace,
            job_id="CUSTOM-VAULT-COLLISION",
            source_id="PUBLIC-9999",
            source_input_kind="url",
            source_identity="url:other-source",
            source_url="https://example.test/other",
        )
    except worker.CompilerWorkerV2Error as exc:
        assert "already reserved" in str(exc)
    else:
        raise AssertionError("compiler accepted a source ID owned by another source")
    try:
        assert_source_id_is_available(vault, "PUBLIC-9999", "url:other-source")
    except AdapterStop as exc:
        assert exc.status == "blocked" and "already linked" in exc.message
    else:
        raise AssertionError("vault writer accepted a source ID owned by another source")
    assert not (workspace / "knowledge-base-vault").exists(), "a custom-vault test created the default vault"


def test_completed_run_and_withdrawal(workspace: Path, vault: Path) -> None:
    from tools.research_program import case_identity

    init_topic(workspace, "completed-run")
    first = run_program(
        workspace, "begin-run", "--topic-id", "completed-run", "--run-id", "RUN-FIRST",
        "--execution-mode", "manual_research", "--at", DAY, "--isolated-test-clock",
    )
    assert first["status"] == "ready"
    # A completed run with no reserved work must not occupy the next same-day
    # manual run.  This is the former false blocker.
    second = run_program(
        workspace, "begin-run", "--topic-id", "completed-run", "--run-id", "RUN-SECOND",
        "--execution-mode", "manual_research", "--at", DAY, "--isolated-test-clock",
    )
    assert second["status"] == "ready" and second["run"]["run_id"] == "RUN-SECOND"
    reserved = run_program(
        workspace, "reserve-case", "--topic-id", "completed-run", "--run-id", "RUN-SECOND",
        "--case-id", "CASE-PENDING", "--primary-url", "https://example.test/pending", "--classification", "family-pending",
        "--at", DAY, "--isolated-test-clock",
    )
    assert reserved["status"] == "reserved"
    require_rejected(
        workspace, "begin-run", "--topic-id", "completed-run", "--run-id", "RUN-THIRD",
        "--execution-mode", "manual_research", "--at", DAY, "--isolated-test-clock",
        contains="RUN-SECOND",
    )

    init_topic(workspace, "withdrawal")
    started = run_program(
        workspace, "begin-run", "--topic-id", "withdrawal", "--run-id", "RUN-WITHDRAW",
        "--execution-mode", "manual_research", "--at", DAY, "--isolated-test-clock",
    )
    assert started["status"] == "ready"
    receipt = workspace / "receipts" / "accepted-case.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text('{"historic":"accepted evidence"}\n', encoding="utf-8")
    _, identity = case_identity("https://example.test/withdraw")
    state_path = topic_state_path(workspace, vault, "withdrawal")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    accepted = {
        "case_id": "CASE-WITHDRAW",
        "run_id": "RUN-WITHDRAW",
        "status": "qualified",
        "source_id": "PUBLIC-CASE-1",
        "source_identity": identity,
        "primary_url": "https://example.test/withdraw",
        "classification": "family-withdraw",
        "accepted_day": "2026-10-07",
        "completion_receipt": receipt.relative_to(workspace).as_posix(),
        "completion_receipt_sha256": sha256(receipt),
    }
    state["accepted_cases"].append(accepted)
    write_json(state_path, state)
    withdrawn = run_program(
        workspace, "withdraw-case", "--topic-id", "withdrawal", "--case-id", "CASE-WITHDRAW",
        "--reason", "Selection no longer fits this topic", "--at", DAY, "--isolated-test-clock",
    )
    assert withdrawn["status"] == "withdrawn"
    assert withdrawn["capacity"]["total_accepted"] == 0
    assert withdrawn["case"]["completion_receipt_sha256"] == sha256(receipt)
    replay = run_program(
        workspace, "withdraw-case", "--topic-id", "withdrawal", "--case-id", "CASE-WITHDRAW",
        "--reason", "Selection no longer fits this topic", "--at", DAY, "--isolated-test-clock",
    )
    assert replay["status"] == "replayed"
    require_rejected(
        workspace, "withdraw-case", "--topic-id", "withdrawal", "--case-id", "CASE-WITHDRAW",
        "--reason", "Different history", "--at", DAY, "--isolated-test-clock",
        contains="different reason",
    )
    require_rejected(
        workspace, "reserve-case", "--topic-id", "withdrawal", "--run-id", "RUN-WITHDRAW",
        "--case-id", "CASE-REUSE", "--primary-url", "https://example.test/withdraw", "--classification", "family-reuse",
        "--at", DAY, "--isolated-test-clock",
        contains="qualified or withdrawn",
    )


def test_method_revalidation_preserves_capacity_and_blocks_reuse(workspace: Path, vault: Path) -> None:
    init_topic(workspace, "revalidation")
    receipt = workspace / "receipts" / "revalidation-case.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text('{"historic":"accepted evidence"}\n', encoding="utf-8")
    state_path = topic_state_path(workspace, vault, "revalidation")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["accepted_cases"].append({
        "case_id": "CASE-REVALIDATE", "status": "qualified", "source_id": "PUBLIC-CASE-2",
        "source_identity": "url:public-revalidation", "primary_url": "https://example.test/revalidate",
        "classification": "family-revalidation", "accepted_day": "2026-10-07",
        "completion_receipt": receipt.relative_to(workspace).as_posix(), "completion_receipt_sha256": sha256(receipt),
    })
    write_json(state_path, state)
    marked = run_program(
        workspace, "mark-case-needs-revalidation", "--topic-id", "revalidation", "--case-id", "CASE-REVALIDATE",
        "--reason", "The generic local panel did not demonstrate the selected source method.", "--at", DAY, "--isolated-test-clock",
    )
    assert marked["status"] == "needs_revalidation"
    assert marked["capacity"]["total_accepted"] == 1
    assert marked["case"]["method_verification"]["state"] == "needs_revalidation"
    assert marked["case"]["completion_receipt_sha256"] == sha256(receipt)
    status = run_program(workspace, "status", "--topic-id", "revalidation", "--at", DAY, "--isolated-test-clock")
    assert status["method_verification"]["needs_revalidation"] == 1
    assert status["capacity"]["total_accepted"] == 1
    replay = run_program(
        workspace, "mark-case-needs-revalidation", "--topic-id", "revalidation", "--case-id", "CASE-REVALIDATE",
        "--reason", "The generic local panel did not demonstrate the selected source method.", "--at", DAY, "--isolated-test-clock",
    )
    assert replay["status"] == "replayed"
    trial = workspace / "trials" / "blocked-reuse.json"
    write_json(trial, {"schema_version": "research_reuse_trial_v1"})
    require_rejected(
        workspace, "record-application", "--topic-id", "revalidation", "--source-id", "PUBLIC-CASE-2",
        "--trial-manifest", trial.relative_to(workspace).as_posix(),
        contains="not method-verified",
    )


def make_case_evidence(workspace: Path) -> tuple[Path, dict, dict, str]:
    evidence = workspace / "outputs" / "research-integrity-fixture"
    source_url = "https://example.test/source"
    screenshot_specs = [
        ("source-before", "desktop", "desktop-before.png", 400, 300, []),
        ("source-after", "interaction", "desktop-after.png", 400, 300, ["open-details"]),
        ("source-mobile", "mobile", "mobile-after.png", 320, 640, ["open-details"]),
    ]
    screenshots: list[dict] = []
    for index, (identifier, role, filename, width, height, actions) in enumerate(screenshot_specs):
        image = evidence / filename
        # A light source page is deliberately valid; luma is not evidence of
        # overlay absence or interaction quality.
        write_png(image, width, height, (248 - index, 247 - index, 244 - index))
        screenshots.append({
            "id": identifier,
            "role": role,
            "path": filename,
            "sha256": sha256(image),
            "source_url": source_url,
            "captured_at": "2026-10-07T12:00:00+00:00",
            "viewport": {"width": width, "height": height, "device_scale_factor": 1},
            "actions": actions,
            "visual_readback": f"{role} fixture readback",
        })
    entrypoint = evidence / "repro" / "index.html"
    dependency = evidence / "repro" / "requirements.txt"
    runtime = evidence / "repro" / "runtime.json"
    action_log = evidence / "repro" / "browser-action-log.json"
    output = evidence / "repro" / "output.txt"
    comparison = evidence / "repro" / "comparison.png"
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text("<!doctype html><title>fixture</title><button>Open</button>", encoding="utf-8")
    dependency.write_text("fixture==1\n", encoding="utf-8")
    output.write_text("fixture completed\n", encoding="utf-8")
    write_png(comparison, 400, 300, (240, 240, 240))
    runner = {
        "kind": "python_http_server",
        "argv": ["python", "-m", "http.server", "8765", "--directory", "repro"],
        "cwd": "repro",
    }
    action_log_payload = {
        "schema_version": "case_reproduction_browser_actions_v1",
        "observations": {
            "mechanisms": [{
                "id": "open-details",
                "action_ids": ["open-details"],
                "expected_result": "The selected details region opens.",
                "observed_result": "The local details region opened after the reviewed action.",
                "state_changed": True,
            }],
        },
    }
    write_json(action_log, action_log_payload)
    runtime_payload = {
        "schema_version": "case_reproduction_runtime_v1",
        "entrypoint_sha256": sha256(entrypoint),
        "dependency_snapshot_sha256": sha256(dependency),
        "runner": runner,
        "comparison_screenshot_sha256": sha256(comparison),
        "comparison_action_id": "open-details",
        "browser_actions": ["open-details"],
        "browser_action_log_sha256": sha256(action_log),
        "exit_code": 0,
    }
    write_json(runtime, runtime_payload)
    capture = evidence / "source-capture.json"
    capture_payload = {
        "schema_version": "case_source_capture_v1",
        "status": "captured",
        "source_url": source_url,
        "action_id": "open-details",
        "state_changed": True,
        "before": {"screenshot_id": "source-before", "state": "overview", "sha256": screenshots[0]["sha256"]},
        "after": {"screenshot_id": "source-after", "state": "details", "sha256": screenshots[1]["sha256"]},
    }
    write_json(capture, capture_payload)
    reference = evidence / "reference.txt"
    reference.write_text("reviewed technical reference\n", encoding="utf-8")
    contract = {
        "schema_version": "research_topic_selection_contract_v1",
        "version": "selection-v1",
        "criteria": [
            {"id": "visual", "label": "Visible source quality", "evidence_roles": ["desktop", "mobile"]},
            {"id": "interaction", "label": "Verified source interaction", "evidence_roles": ["interaction"]},
            {"id": "method", "label": "Selected core method", "evidence_roles": ["interaction"]},
        ],
        "source_interaction_required": True,
        "reproduction_required": True,
        "learning_alignment_required": True,
        "reproduction_criterion": "method",
    }
    manifest = {
        "schema_version": "case_evidence_manifest_v1",
        "asset_mode": "case_evidence",
        "case_id": "CASE-EVIDENCE",
        "topic_id": "portable-topic",
        "primary_source_url": source_url,
        "creator": {"name": "Fixture creator", "article_date": "2026-10-07", "ai_evidence_quote": "fixture evidence quote"},
        "screenshots": screenshots,
        "interaction_required": True,
        "source_capture": {"receipt_path": capture.name, "receipt_sha256": sha256(capture)},
        "technical_references": [{
            "url": "https://example.test/reference", "retrieved_at": "2026-10-07T12:00:00+00:00",
            "version": "1", "purpose": "fixture", "boundary": "local test only",
            "snapshot_path": reference.name, "snapshot_sha256": sha256(reference),
        }],
        "reproduction": {
            "visual_readback": "fixture comparison rendered",
            "comparison_action_id": "open-details",
            "runner": runner,
            "browser_actions": ["open-details"],
            "learning_alignment_id": "source-method-v1", "verified_mechanism_ids": ["open-details"],
            "browser_action_log_path": "repro/browser-action-log.json", "browser_action_log_sha256": sha256(action_log),
            "entrypoint": "repro/index.html", "entrypoint_sha256": sha256(entrypoint),
            "dependency_snapshot_path": "repro/requirements.txt", "dependency_snapshot_sha256": sha256(dependency),
            "dependency_versions": {"fixture": "1"},
            "runtime_output_path": "repro/runtime.json", "runtime_output_sha256": sha256(runtime),
            "output_path": "repro/output.txt", "output_sha256": sha256(output),
            "comparison_screenshot_path": "repro/comparison.png", "comparison_screenshot_sha256": sha256(comparison),
        },
        "selection_assessment": {
            "contract_version": "selection-v1",
            "criteria": [
                {"id": "visual", "finding": "visible on both viewports", "evidence_screenshot_ids": ["source-before", "source-mobile"]},
                {"id": "interaction", "finding": "details were opened", "evidence_screenshot_ids": ["source-after"]},
                {"id": "method", "finding": "the selected method has a local observation", "evidence_screenshot_ids": ["source-after"]},
            ],
            "source_interaction": {
                "source_url": source_url,
                "action_id": "open-details",
                "before_screenshot_id": "source-before",
                "after_screenshot_id": "source-after",
                "before_state": "overview",
                "after_state": "details",
                "state_changed": True,
            },
            "reproduction": {"verified": True, "comparison_action_id": "open-details"},
            "learning_alignment": {
                "id": "source-method-v1",
                "objective": "Recreate the selected source behavior rather than an ordinary panel.",
                "rejected_shortcut": "A page that merely opens and renders is not method evidence.",
                "critical_mechanisms": [{
                    "id": "open-details",
                    "source_basis": "The source interaction shows the details state after open-details.",
                    "local_result": "The local observation records the same reviewed state change.",
                }],
                "unverified_source_methods": [],
            },
        },
    }
    manifest_path = evidence / "case-evidence.json"
    write_json(manifest_path, manifest)
    return manifest_path, contract, manifest, source_url


def test_selection_and_capture_evidence(workspace: Path) -> None:
    from tools.case_evidence_contract import CaseEvidenceError, package_manifest, stage_manifest, validate_manifest

    manifest_path, contract, manifest, source_url = make_case_evidence(workspace)
    accepted, staged = validate_manifest(workspace, manifest_path, primary_url=source_url, selection_contract=contract)
    assert accepted["selection_assessment"]["source_interaction"]["state_changed"] is True
    assert "source_capture:receipt" in staged

    package = workspace / "outputs" / "research-integrity-package"
    package.mkdir(parents=True, exist_ok=True)
    (package / "canonical_source.md").write_text("fixture evidence quote\n", encoding="utf-8")
    binding = stage_manifest(workspace, manifest_path, package, primary_url=source_url)
    revalidated = package_manifest(
        package,
        {**binding, "source_url": source_url},
        selection_contract=contract,
    )
    assert revalidated["source_capture"]["receipt_path"].startswith("case_evidence/")

    no_capture = dict(manifest)
    no_capture.pop("source_capture")
    no_capture_path = manifest_path.with_name("case-evidence-no-capture.json")
    write_json(no_capture_path, no_capture)
    try:
        validate_manifest(workspace, no_capture_path, primary_url=source_url, selection_contract=contract)
    except CaseEvidenceError as exc:
        assert "source interaction capture" in str(exc)
    else:
        raise AssertionError("selection contract accepted a static-only case")

    bad_capture = json.loads((manifest_path.parent / "source-capture.json").read_text(encoding="utf-8"))
    bad_capture["state_changed"] = False
    bad_capture_path = manifest_path.with_name("source-capture-no-change.json")
    write_json(bad_capture_path, bad_capture)
    changed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    changed_manifest["source_capture"] = {"receipt_path": bad_capture_path.name, "receipt_sha256": sha256(bad_capture_path)}
    changed_path = manifest_path.with_name("case-evidence-no-change.json")
    write_json(changed_path, changed_manifest)
    try:
        validate_manifest(workspace, changed_path, primary_url=source_url, selection_contract=contract)
    except CaseEvidenceError as exc:
        assert "does not prove a state change" in str(exc)
    else:
        raise AssertionError("selection contract accepted an unchanged source interaction")

    # All ordinary runtime proof can be valid while the local page is still a
    # generic panel.  It must fail because its observed mechanism does not
    # cover the source method selected for learning.
    generic_log = manifest_path.parent / "repro" / "generic-browser-action-log.json"
    generic_log_payload = json.loads((manifest_path.parent / "repro" / "browser-action-log.json").read_text(encoding="utf-8"))
    generic_log_payload["observations"]["mechanisms"][0]["id"] = "generic-panel"
    generic_log_payload["observations"]["mechanisms"][0]["observed_result"] = "An ordinary details panel opened."
    write_json(generic_log, generic_log_payload)
    generic_runtime = manifest_path.parent / "repro" / "generic-runtime.json"
    generic_runtime_payload = json.loads((manifest_path.parent / "repro" / "runtime.json").read_text(encoding="utf-8"))
    generic_runtime_payload["browser_action_log_sha256"] = sha256(generic_log)
    write_json(generic_runtime, generic_runtime_payload)
    generic_manifest = json.loads(json.dumps(manifest))
    generic_manifest["reproduction"]["browser_action_log_path"] = "repro/generic-browser-action-log.json"
    generic_manifest["reproduction"]["browser_action_log_sha256"] = sha256(generic_log)
    generic_manifest["reproduction"]["runtime_output_path"] = "repro/generic-runtime.json"
    generic_manifest["reproduction"]["runtime_output_sha256"] = sha256(generic_runtime)
    generic_manifest["reproduction"]["verified_mechanism_ids"] = ["scroll-camera"]
    generic_manifest["selection_assessment"]["learning_alignment"]["critical_mechanisms"][0]["id"] = "scroll-camera"
    generic_manifest["selection_assessment"]["learning_alignment"]["critical_mechanisms"][0]["source_basis"] = "The source uses scroll-driven camera movement."
    generic_manifest["selection_assessment"]["learning_alignment"]["critical_mechanisms"][0]["local_result"] = "The local panel is presented as a camera movement."
    generic_path = manifest_path.with_name("case-evidence-generic-panel.json")
    write_json(generic_path, generic_manifest)
    try:
        validate_manifest(workspace, generic_path, primary_url=source_url, selection_contract=contract)
    except CaseEvidenceError as exc:
        assert "does not exactly cover the selected core methods" in str(exc)
    else:
        raise AssertionError("a generic runnable panel passed as the selected source method")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    config = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
    vault = workspace / config["vault"]
    if not vault.is_dir():
        raise SystemExit("workspace vault is missing")
    os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = config["vault"]
    test_custom_vault_collision(workspace, vault)
    test_completed_run_and_withdrawal(workspace, vault)
    test_method_revalidation_preserves_capacity_and_blocks_reuse(workspace, vault)
    test_selection_and_capture_evidence(workspace)
    print(json.dumps({
        "status": "passed",
        "coverage": [
            "nested custom-vault source-ID collision", "unsafe vault path rejection",
            "completed same-day run handoff", "unfinished-run blocker", "withdrawal receipt preservation and replay",
            "withdrawn-source reuse blocker", "method-revalidation capacity preservation and reuse blocker",
            "selection criteria evidence binding", "source interaction capture", "generic runnable-panel rejection", "package-local evidence revalidation",
        ],
        "not_proven": ["live external source", "native Obsidian", "host skill discovery"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
