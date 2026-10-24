"""Installed-package counterexamples for portable runtime parity.

These exercise the parts that need a real named-vault workspace: case source-ID
recovery, the no-repair-after-writer rule, and QC routing for a custom vault.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def configured_vault(workspace: Path) -> tuple[Path, str]:
    config = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
    name = config.get("vault")
    if not isinstance(name, str):
        raise AssertionError("installed workspace has no named vault")
    return workspace / Path(name), name


def test_case_source_id_rebind(workspace: Path, vault: Path) -> None:
    from tools.obsidian_vault_adapter import AdapterStop, rebind_uncompleted_case_source_id

    old_id = "KS-VS-OLD"
    new_id = "KS-VS-NEW"
    identity = "url:case-rebind-fixture"
    source = vault / "sources" / "2026" / "10" / f"{old_id}_fixture.md"
    application = vault / "applications" / "2026" / "10" / f"APP-{old_id}_fixture.md"
    knowledge = vault / "knowledge-points" / "by-source" / old_id / "KP-VS-OLD-001.md"
    assets = vault / "assets" / old_id.lower() / "case-evidence"
    for path, text in ((source, "source"), (application, "application"), (knowledge, "knowledge"), (assets / "evidence.txt", "evidence")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    write_jsonl(vault / "_registry" / "sources.jsonl", [{
        "source_identity": identity, "source_id": old_id, "status": "linked",
    }])
    write_jsonl(vault / "_registry" / "knowledge_points.jsonl", [{
        "kp_uid": f"{old_id}:KP-VS-OLD-001", "source_identity": identity,
    }])
    write_jsonl(vault / "_registry" / "relations.jsonl", [{
        "relation_id": "REL-OLD", "source_identity": identity,
    }])

    result = rebind_uncompleted_case_source_id(
        vault,
        source_identity_value=identity,
        old_source_id=old_id,
        new_source_id=new_id,
        source_note=source,
        application_note=application,
        knowledge_points=[knowledge],
        case_asset_dir=assets,
    )
    assert result["status"] == "rebound" and result["source_registry_rows_removed"] == 1
    assert not source.exists() and not application.exists() and not knowledge.exists() and not assets.exists()
    assert (vault / "sources" / "2026" / "10" / f"{new_id}_fixture.md").is_file()
    assert (vault / "applications" / "2026" / "10" / f"APP-{new_id}_fixture.md").is_file()
    assert (vault / "knowledge-points" / "by-source" / new_id / "KP-VS-NEW-001.md").is_file()
    assert (vault / "assets" / new_id.lower() / "case-evidence" / "evidence.txt").is_file()
    assert not (vault / "_registry" / "knowledge_points.jsonl").read_text(encoding="utf-8").strip()
    assert not (vault / "_registry" / "relations.jsonl").read_text(encoding="utf-8").strip()

    resumed = rebind_uncompleted_case_source_id(
        vault,
        source_identity_value=identity,
        old_source_id=old_id,
        new_source_id=new_id,
        source_note=source,
        application_note=application,
        knowledge_points=[knowledge],
        case_asset_dir=assets,
        artifact_hashes={
            "sources/2026/10/KS-VS-OLD_fixture.md": hashlib.sha256(b"source").hexdigest(),
            "applications/2026/10/APP-KS-VS-OLD_fixture.md": hashlib.sha256(b"application").hexdigest(),
            "knowledge-points/by-source/KS-VS-OLD/KP-VS-OLD-001.md": hashlib.sha256(b"knowledge").hexdigest(),
            "assets/ks-vs-old/case-evidence/evidence.txt": hashlib.sha256(b"evidence").hexdigest(),
        },
    )
    assert resumed["status"] == "already_rebound"

    try:
        rebind_uncompleted_case_source_id(
            vault,
            source_identity_value=identity,
            old_source_id=old_id,
            new_source_id="KS-VS-OUTSIDE",
            source_note=vault.parent / f"{old_id}_outside.md",
            application_note=application,
            knowledge_points=[knowledge],
            case_asset_dir=assets,
        )
    except AdapterStop as exc:
        assert exc.status == "needs_review" and "escapes vault" in exc.message
    else:
        raise AssertionError("source-ID rebind accepted an artifact outside its vault")


def test_case_plan_repair_writer_guard(workspace: Path) -> None:
    from tools import compiler_worker_v2 as worker
    from tools import ingest_rail as rail

    package = workspace / "case-plan-repair-guard"
    package.mkdir(exist_ok=True)
    response = package / "replacement.json"
    checkpoint = {"stage": "project_and_readback", "status": "failed"}
    guard_cases = (
        {"status": "committing"},
        {"commit_adapter": {"status": "started"}},
        {"commit_outcome": {"status": "started"}},
        {"commit_reader_quality_audit": {"status": "started"}},
        {"commit_stage": "source_id_rebound"},
    )
    for changed in guard_cases:
        job = {"execution_contract": {"mode": "case_evidence_zero_imagegen"}, "rail_events": [checkpoint], **changed}
        with patch.object(rail, "paths", return_value=(job, package, package / "knowledge-package.json")):
            try:
                rail.repair_case_plan_before_write(workspace, "CASE-GUARD", response)
            except worker.CompilerWorkerV2Error as exc:
                assert "writer has started" in str(exc)
            else:
                raise AssertionError(f"writer-started plan repair was accepted: {changed}")
    assert not list(package.glob("*.json")), "writer-started repair wrote a replacement plan"


def test_rebind_retry_with_repeated_vault_name(workspace: Path) -> None:
    """The retry checkpoint must be relative to the passed vault, not an ancestor."""
    from tools.obsidian_vault_adapter import rebind_uncompleted_case_source_id

    vault = workspace / "vault" / "fixture" / "vault"
    old_id, new_id = "KS-VS-REPEAT-OLD", "KS-VS-REPEAT-NEW"
    source = vault / "sources" / "2026" / "10" / f"{old_id}_fixture.md"
    application = vault / "applications" / "2026" / "10" / f"APP-{old_id}_fixture.md"
    knowledge = vault / "knowledge-points" / "by-source" / old_id / "KP-VS-REPEAT-OLD-001.md"
    assets = vault / "assets" / old_id.lower() / "case-evidence"
    values = ((source, b"source"), (application, b"application"), (knowledge, b"knowledge"), (assets / "evidence.txt", b"evidence"))
    for path, content in values:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    common = {
        "source_identity_value": "url:repeated-vault-fixture",
        "old_source_id": old_id,
        "new_source_id": new_id,
        "source_note": source,
        "application_note": application,
        "knowledge_points": [knowledge],
        "case_asset_dir": assets,
    }
    assert rebind_uncompleted_case_source_id(vault, **common)["status"] == "rebound"
    replay = rebind_uncompleted_case_source_id(vault, **common, artifact_hashes={
        f"sources/2026/10/{old_id}_fixture.md": hashlib.sha256(b"source").hexdigest(),
        f"applications/2026/10/APP-{old_id}_fixture.md": hashlib.sha256(b"application").hexdigest(),
        f"knowledge-points/by-source/{old_id}/KP-VS-REPEAT-OLD-001.md": hashlib.sha256(b"knowledge").hexdigest(),
        f"assets/{old_id.lower()}/case-evidence/evidence.txt": hashlib.sha256(b"evidence").hexdigest(),
    })
    assert replay["status"] == "already_rebound"


def test_custom_vault_qc_routing(workspace: Path, vault_name: str) -> None:
    from tools.url_ingest_qc_router import build_qc_plan, classify_changed_paths

    plan = build_qc_plan(
        root=workspace,
        changed_paths=[
            f"{vault_name}/sources/example.md",
            f"{vault_name}/concepts/example.md",
            "docs/concepts/not-a-vault-note.md",
        ],
    )
    assert plan["changed_path_scopes"]["content"] == [f"{vault_name}/sources/example.md"]
    assert plan["changed_path_scopes"]["graph"] == [f"{vault_name}/concepts/example.md"]
    assert plan["changed_path_scopes"]["unknown"] == ["docs/concepts/not-a-vault-note.md"]
    default = classify_changed_paths(["knowledge-base-vault/sources/example.md"])
    assert default == {"content": ["knowledge-base-vault/sources/example.md"]}


def test_source_evidence_and_complete_bundle_contract(workspace: Path, vault: Path) -> None:
    from tools import ask_second_brain as ask
    from tools.ingest_plan_contract import content_errors
    from tools.obsidian_vault_adapter import AdapterStop, KnowledgePoint, KnowledgeRelation, parse_kp_rows, parse_relation_rows, validate_source_anchored_knowledge

    points = parse_kp_rows(
        r"""### 知識點草案
| 知識點 ID | 內容 | 類型 | 信心 | 證據 |
|---|---|---|---|---|
| KP-01 | 3D \| scroll method | 原文方法 | 高 | The complete source sentence has enough context to preserve the mechanism and its evidence boundary. |
"""
    )
    relations = parse_relation_rows(
        r"""### 關係草案
| 來源知識點 | 目標知識點 | 關係 | 證據 | 信心 |
|---|---|---|---|---|
| KP-01 | KP-02 | supports | The complete source sentence has enough context to preserve the mechanism and its evidence boundary. | 高 |
"""
    )
    assert points[0].text == "3D | scroll method" and relations[0].evidence.endswith("boundary.")
    canonical = "The complete source sentence has enough context to preserve the mechanism and its evidence boundary.\n短句  可證。\nThe source describes a deliberate and repeatable review method."
    validate_source_anchored_knowledge(points, relations, canonical)
    short_anchor = "短句 可證。"
    paraphrase_anchor = "The source describes a deliberate and repeatable review method."
    validate_source_anchored_knowledge(
        [
            KnowledgePoint("KP-02", "短句已被來源支持。", "原文方法", "高", short_anchor),
            KnowledgePoint("KP-03", "審閱方法可重複進行。", "原文方法", "高", paraphrase_anchor),
        ],
        [KnowledgeRelation("KP-02", "KP-03", "supports", short_anchor, "高")],
        canonical,
    )

    package = workspace / "outputs" / "evidence-anchor-fixture"
    package.mkdir(parents=True, exist_ok=True)
    (package / "canonical_source.md").write_text(canonical, encoding="utf-8")
    (package / "input_bundle.json").write_text("{}\n", encoding="utf-8")

    def preflight_payload(anchor: str, source_claim_anchor: str | None = None) -> dict:
        return {
            "source": {"source_input_kind": "url"},
            "knowledge": [{"kp_id": "KP-02", "claim": "短句已被來源支持。", "layer": "原文方法", "confidence": "高", "evidence_anchor": anchor}],
            "relations": [{"source_kp": "KP-02", "target_kp": "KP-02", "relation": "supports", "confidence": "高", "evidence_anchor": anchor}],
            "analysis": {"source_claims": [{"evidence_anchor": source_claim_anchor or anchor}]},
            "application": {"adoption_reason": "fixture", "compounding_next_action": "fixture", "taste_reason": "fixture", "knowledge_use": "學習材料", "compounding_insight_status": "no_compounding_insight", "adoption_status": "暫存"},
        }

    assert content_errors(preflight_payload(short_anchor), package) == []
    assert content_errors(preflight_payload(short_anchor, "短句\r\n可證。"), package) == []
    assert content_errors(preflight_payload(paraphrase_anchor), package) == []
    for invalid_anchor in ("", "待補", "not present in the canonical source"):
        try:
            validate_source_anchored_knowledge([KnowledgePoint("KP-04", "claim", "原文方法", "高", invalid_anchor)], [], canonical)
        except AdapterStop:
            pass
        else:
            raise AssertionError(f"writer accepted invalid source anchor: {invalid_anchor!r}")
        assert content_errors(preflight_payload(invalid_anchor), package)

    image_payload = preflight_payload("IMG-001")
    image_payload["source"] = {"source_input_kind": "user_provided_image"}
    with patch("tools.ingest_plan_contract.validate_source_anchored_knowledge", side_effect=AssertionError("text gate used for image route")):
        assert content_errors(image_payload, package)

    graph = {"must_read_notes": [], "missing_or_weak_links": [], "path_text": "fixture"}
    bundle = []
    for index in range(12):
        path = vault / "sources" / f"bundle-{index}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"bundle evidence {index}", encoding="utf-8")
        bundle.append(f"sources/bundle-{index}.md")
    with patch.object(ask, "select_materials", return_value={"candidates": [{"must_read": bundle}], "blocked_reasons": []}), patch.object(ask, "retrieve_question", return_value=graph):
        packet = ask.prepare("bundle", vault, limit=1)
    assert packet["status"] == "context_ready" and len(packet["evidence"]) == 12 and packet["deferred_required_paths"] == []

    oversized = []
    for index in range(17):
        path = vault / "sources" / f"oversized-{index}.md"
        path.write_text(f"oversized evidence {index}", encoding="utf-8")
        oversized.append(f"sources/oversized-{index}.md")
    with patch.object(ask, "select_materials", return_value={"candidates": [{"must_read": oversized}], "blocked_reasons": []}), patch.object(ask, "retrieve_question", return_value=graph):
        incomplete = ask.prepare("oversized", vault, limit=1)
    assert incomplete["status"] == "needs_review" and incomplete["deferred_required_paths"]
    try:
        ask.review_answer(incomplete, {"session_id": incomplete["session_id"], "claims": [{"kind": "gap", "text": "partial packet"}]})
    except ValueError as exc:
        assert "incomplete required source bundle" in str(exc)
    else:
        raise AssertionError("partial source bundle was accepted for an answer")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    vault, vault_name = configured_vault(workspace)
    old_env = os.environ.get("CREATIVE_SECOND_BRAIN_VAULT")
    os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = vault_name
    sys.path.insert(0, str(workspace))
    try:
        test_case_source_id_rebind(workspace, vault)
        test_case_plan_repair_writer_guard(workspace)
        test_rebind_retry_with_repeated_vault_name(workspace)
        test_custom_vault_qc_routing(workspace, vault_name)
        test_source_evidence_and_complete_bundle_contract(workspace, vault)
    finally:
        if old_env is None:
            os.environ.pop("CREATIVE_SECOND_BRAIN_VAULT", None)
        else:
            os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = old_env
    print(json.dumps({
        "status": "passed",
        "coverage": [
            "installed custom-vault case source-ID rebind and retry",
            "checkpoint retry with a repeated vault basename in an ancestor",
            "writer-started case-plan repair refusal",
            "custom vault QC routing without a hard-coded vault name",
            "escaped KP table cells, canonical source-anchor gate, and complete source-bundle ask packets",
        ],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
