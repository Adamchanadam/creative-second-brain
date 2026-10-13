from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from tools.workspace_paths import WorkspacePathError, vault_relative_path
except ModuleNotFoundError:
    from workspace_paths import WorkspacePathError, vault_relative_path  # type: ignore


DEFAULT_ROOT = Path(__file__).resolve().parents[1]

SOURCE_KINDS = ("url", "user_text", "user_image")
PROFILES = ("article", "paper", "full_body")
PROFILE_SPEC_PATH = Path("docs") / "Compiler Worker與Knowledge Package設計及驗收規格.md"

EXECUTION_CONTRACT_VERSION = "source_ingest_execution_v1"


def execution_policy(profile: str, page_count: int) -> dict[str, Any]:
    """Executable projection of the compiler specification, section 7.1."""
    if profile not in PROFILES or page_count < 1:
        raise ValueError("invalid ingest profile or page count")
    target = None if profile == "full_body" else (1800 if profile == "paper" else (600 if page_count <= 2 else 1200))
    return {
        "schema_version": EXECUTION_CONTRACT_VERSION,
        "mode": "content_fast_path",
        "source_profile": profile,
        "page_count": page_count,
        "target_seconds": target,
        "normative_owner": PROFILE_SPEC_PATH.as_posix() + "#7.1",
        "over_target_action": "resume_remaining_required_work_only",
        "stage_order": ["input_bundle", "locked_plan", "image_receipts", "source_local_write_readback", "completed"],
        "scope_change": "one_source_grounded_blocking_plan_repair_before_image_authorization",
        "enforcement_boundary": "compiler entry points; not arbitrary shell, editor or model reasoning",
    }

FAST_PATH_EXCLUDED = (
    "pytest / full test suite",
    "python tools\\obsidian_vault_adapter.py --vault-health --json",
    "python tools\\obsidian_idea_retrieval.py --five-question-acceptance --write-receipts --check --json",
    "python tools\\ks_vs_inventory_check.py --json",
    "python tools\\validation_manifest.py --scenario governance --json",
    "Agent Handoff Kit doctor",
)

SOURCE_KIND_CHECKS = {
    "url": (
        "Read capture/review_packet source status; URL must be public_ok before formal write.",
        "Verify source_identity uses the normalized URL hash and contains no credential-bearing query.",
        "Before image authorization, confirm the complete readable article was extracted from the accepted HTML snapshot; partial_capture blocks formal write.",
    ),
    "user_text": (
        "Before accept-text, compare every section and paragraph of the pasted user text with the input file; formatting edits are allowed, summary replacement or omitted text is not.",
        "Verify source_capture.md raw bytes, metadata and source slice share the same SHA-256.",
        "Verify no original local absolute input path is persisted in outputs, vault, registry or reports.",
    ),
    "user_image": (
        "Verify original image bytes, MIME, dimensions and SHA-256 across metadata, review packet and vault asset.",
        "Verify IMG-* anchors, visual transcription, coverage table and second-round review before formal write.",
    ),
}

BASE_FAST_PATH_CHECKS = (
    "Start new URL/text/image intake with python -B tools/ingest_rail.py start; follow its single returned work order and semantic JSON Schema. Text and URL always preserve the full body.",
    "Submit reviewed semantic decisions with submit-plan; the compiler runs preflight before prompt writing and image authorization. Use plan-preflight only for a read-only diagnosis of a blocked package.",
    "Generate only the authorized page once with Codex imagegen; inspect the actual PNG and submit image-result. An outstanding action requires reconciliation, never automatic generation on restart.",
    "The rail performs source-local projection/write/readback, outcome audit and completion receipt in one call after the last image receipt. Read its completion and package-local candidate status; candidate generation is automatic and does not establish formal links.",
)

TOOL_TESTS = {
    "ingest_rail.py": "python -B tests\\ingest_rail.py",
    "ingest_plan_contract.py": "python -B tests\\ingest_rail.py",
    "compiler_worker_v2.py": "python -B tests\\compiler_worker_v2.py",
    "obsidian_vault_adapter.py": "python tests\\obsidian_adapter_failure_cases.py",
    "obsidian_semantic_maintenance.py": "python tests\\obsidian_semantic_maintenance.py",
    "obsidian_idea_retrieval.py": "python tests\\obsidian_idea_retrieval.py",
    "outcome_page_generator.py": "python tests\\outcome_page_generator.py",
    "outcome_page_quality_audit.py": "python tests\\outcome_page_quality_audit.py",
    "validation_manifest.py": "python tests\\validation_manifest.py",
    "project_control_panel.py": "python tests\\project_control_panel.py",
    "url_ingest_qc_router.py": "python tests\\url_ingest_qc_router.py",
}

SPEC_PATH_MARKERS = (
    "docs/分層qc機制.md",
    "docs/多層驗收機制.md",
    "docs/url到輸出模板規格.md",
    "docs/compiler worker與knowledge package設計及驗收規格.md",
    "tools/readme.md",
    "dev/project_index.md",
)

GRAPH_PATH_MARKERS = (
    "concepts/",
    "maps/",
    ".obsidian/graph.json",
)

CONTENT_PATH_MARKERS = (
    "sources/",
    "applications/",
    "knowledge-points/",
    "assets/",
    "outcome-pages/",
    "_registry/sources.jsonl",
    "_registry/knowledge_points.jsonl",
    "_registry/relations.jsonl",
    "outputs/compiler_worker_v2/",
    "outputs/obsidian_semantic_maintenance/",
)


def normalize_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("./").casefold()


def split_table_row(line: str) -> list[str]:
    return [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]


def load_profile_specs(root: Path = DEFAULT_ROOT) -> dict[str, dict[str, str]]:
    path = root / PROFILE_SPEC_PATH
    text = path.read_text(encoding="utf-8")
    section = text.split("### 3.1 來源 profile 與覆蓋策略", 1)[1].split("\n## ", 1)[0]
    specs: dict[str, dict[str, str]] = {}
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("| `"):
            continue
        cells = split_table_row(line)
        if len(cells) < 5:
            continue
        profile = cells[0]
        if profile in PROFILES:
            specs[profile] = {
                "when": cells[1],
                "coverage": cells[2],
                "default_qc": cells[3],
                "forbidden": cells[4],
            }
    time_section = text.split("## 7. 時間與使用者體驗契約", 1)[1].split("\n## ", 1)[0]
    for profile in PROFILES:
        marker = f"- `{profile}`"
        for raw_line in time_section.splitlines():
            line = raw_line.strip()
            if line.startswith(marker):
                specs.setdefault(profile, {})["time_target"] = line.lstrip("- ").strip()
                break
    missing = [profile for profile in PROFILES if profile not in specs or "time_target" not in specs[profile]]
    if missing:
        raise ValueError(f"source profile spec is incomplete: {', '.join(missing)}")
    return specs


def configured_vault_marker(root: Path) -> str:
    """Read the same portable vault selector used by runtime tools.

    QC classification must follow the installed workspace's named vault rather
    than a developer-local directory name.  A source tree without brain.json
    intentionally retains the portable default.
    """
    configured: str | None = None
    config_path = root / "brain.json"
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        configured = config.get("vault")
        if not isinstance(configured, str):
            raise ValueError("workspace brain.json has no valid vault path")
    try:
        return normalize_path(vault_relative_path(configured).as_posix()).rstrip("/")
    except WorkspacePathError as exc:
        raise ValueError(f"workspace vault path is invalid: {exc}") from exc


def vault_scoped_marker(normalized: str, vault_marker: str, marker: str) -> bool:
    relative = normalized.removeprefix(vault_marker + "/")
    return relative != normalized and (relative.startswith(marker) or relative == marker.rstrip("/"))


def classify_changed_paths(paths: list[str], *, vault_marker: str = "knowledge-base-vault") -> dict[str, list[str]]:
    scopes: dict[str, list[str]] = {
        "content": [],
        "tool": [],
        "spec_or_qc": [],
        "graph": [],
        "unknown": [],
    }
    for raw in paths:
        normalized = normalize_path(raw)
        if normalized.startswith("tools/") or normalized.startswith("tests/"):
            scopes["tool"].append(raw)
        elif any(normalized.startswith(marker) or normalized == marker for marker in SPEC_PATH_MARKERS):
            scopes["spec_or_qc"].append(raw)
        elif any(vault_scoped_marker(normalized, vault_marker, marker) for marker in GRAPH_PATH_MARKERS):
            scopes["graph"].append(raw)
        elif any(
            normalized.startswith(marker) or normalized == marker
            or vault_scoped_marker(normalized, vault_marker, marker)
            for marker in CONTENT_PATH_MARKERS
        ):
            scopes["content"].append(raw)
        else:
            scopes["unknown"].append(raw)
    return {key: values for key, values in scopes.items() if values}


def focused_tool_checks(paths: list[str]) -> list[str]:
    checks: list[str] = []
    py_files = [path for path in paths if normalize_path(path).endswith(".py")]
    if py_files:
        checks.append("python -m py_compile " + " ".join(py_files))
    for path in py_files:
        test = TOOL_TESTS.get(Path(path.replace("\\", "/")).name)
        if test and test not in checks:
            checks.append(test)
    if py_files and len(checks) == 1:
        checks.append("Run the nearest focused test for the touched tool only; do not run the full suite by default.")
    return checks


def build_qc_plan(
    source_kind: str = "url",
    profile: str = "article",
    changed_paths: list[str] | None = None,
    batch: bool = False,
    release_claim: bool = False,
    root: Path = DEFAULT_ROOT,
) -> dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"unknown source kind: {source_kind}")
    if profile not in PROFILES:
        raise ValueError(f"unknown source profile: {profile}")

    changed_paths = changed_paths or []
    profile_specs = load_profile_specs(root)
    scopes = classify_changed_paths(changed_paths, vault_marker=configured_vault_marker(root))
    escalation_reasons: list[str] = []
    required_checks = [*SOURCE_KIND_CHECKS[source_kind], *BASE_FAST_PATH_CHECKS]

    profile_note = profile_specs[profile]
    required_checks.insert(2, f"Apply {profile} profile coverage: {profile_note['coverage']}")

    if "tool" in scopes:
        escalation_reasons.append("changed tool or test code")
        required_checks.extend(focused_tool_checks(scopes["tool"]))
    if "spec_or_qc" in scopes:
        escalation_reasons.append("changed durable spec, QC route or tool catalogue")
        required_checks.append("python tools\\validation_manifest.py --scenario url-ingest --json")
        required_checks.append("python tests\\validation_manifest.py")
    if "graph" in scopes:
        escalation_reasons.append("changed formal graph-facing notes or Obsidian graph setting")
        required_checks.append("python tools\\obsidian_semantic_map_flow.py --check --quality-limit 9999 --json")
    if batch:
        escalation_reasons.append("batch or historical inventory claim")
        required_checks.append("python tools\\ks_vs_inventory_check.py --json")
    if release_claim:
        escalation_reasons.append("release or external completion claim")
        required_checks.append("Use the release QC route; do not infer release readiness from ingest success.")
    if "unknown" in scopes:
        escalation_reasons.append("changed paths outside known content/tool/spec/graph surfaces")
        required_checks.append("Classify the unknown changed paths before claiming completion.")

    mode = "content_fast_path" if not escalation_reasons else "focused_escalation"
    return {
        "status": "ready" if "unknown" not in scopes else "needs_review",
        "mode": mode,
        "source_kind": source_kind,
        "source_profile": profile,
        "time_target": profile_note["time_target"],
        "rule_owners": {
            "qc_route": "docs/分層QC機制.md",
            "source_profile": PROFILE_SPEC_PATH.as_posix(),
            "source_package": "docs/URL到輸出模板規格.md",
        },
        "changed_path_scopes": scopes,
        "escalation_reasons": escalation_reasons,
        "required_checks": dedupe(required_checks),
        "do_not_run_by_default": list(FAST_PATH_EXCLUDED),
        "operator_phases": [
            "accept_and_build_one_source_plan",
            "compile_with_preflight",
            "per_page_image_receipts",
            "project_readback_and_source_candidate",
        ],
        "execution_gate": {
            "owner": PROFILE_SPEC_PATH.as_posix() + "#7.1",
            "entry": "ingest_rail.py start (includes preflight before writing)",
            "compiler_entry": "compiler_worker_v2.py compile-infographics",
            "normal_entry": "python -B tools/ingest_rail.py start --url <url> | --text-file <path> | --image-file <path>",
            "work_order_owner": PROFILE_SPEC_PATH.as_posix() + "#7.2",
            "required_before": ["imagegen-authorize", "imagegen-complete", "project --write-vault"],
            "automatic_evidence": ["locked source/profile/coverage/pages", "stage wall time", "resume next action"],
            "agent_rule": "One source reading and one content plan; reuse passed evidence. Extra research, redesign or checks require a concrete blocking defect, not an optional improvement.",
        },
        "rule": (
            "Content-only ingest uses source-local checks. Focused tests are added only when "
            "the same task changes tools, specs, graph structures, batch inventory or release surfaces."
        ),
    }


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Route single-source ingest QC without defaulting to heavy project checks.")
    parser.add_argument("--source-kind", choices=SOURCE_KINDS, default="url")
    parser.add_argument("--profile", choices=PROFILES, default="article")
    parser.add_argument("--changed-path", action="append", default=[])
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--release-claim", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    plan = build_qc_plan(
        source_kind=args.source_kind,
        profile=args.profile,
        changed_paths=list(args.changed_path),
        batch=args.batch,
        release_claim=args.release_claim,
    )
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        print(f"status: {plan['status']}")
        print(f"mode: {plan['mode']}")
        print("required checks:")
        for check in plan["required_checks"]:
            print(f"- {check}")
    return 0 if plan["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
