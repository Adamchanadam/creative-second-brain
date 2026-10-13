from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA_VERSION = "0.2"
DEFAULT_ROOT = Path(__file__).resolve().parents[1]


LEDGER_TEMPLATE = {
    "任務類型": None,
    "影響範圍": None,
    "權威真源": None,
    "決策入口": None,
    "使用者可見結果": None,
    "必讀文件": None,
    "必跑檢查": None,
    "讀回證據": None,
    "失敗停止點": None,
    "不可聲稱": None,
    "未驗證範圍": None,
}


GOVERNANCE_EXPECTED_DOMAINS = {
    "Startup, work loop, persistence and closeout",
    "Current state, next action and blockers",
    "Chronological evidence",
    "Paths, roles, commands and integration coordinates",
    "Long-term rationale and trade-offs",
    "Task routing",
    "Cross-file sync obligations",
    "Detailed external-tool behavior and lifecycle",
    "Product north star, data ownership and invariant outcomes",
    "URL outcome package and vault writer",
    "Infographic coverage, generation, master and acceptance",
    "Compiler Worker v2 execution, Knowledge Package SSOT and cutover conditions",
    "App UX and reader-facing product behavior",
    "Intake identity/state, five reader slots and completion display",
    "Outcome-index schema, facets and pagination",
    "General validation model and ledger schema",
    "Scenario-to-source/check/stop routing",
    "Stage order and stage status",
    "Current acceptance result and evidence",
    "Tool catalogue",
    "Compatibility source skill orchestration",
    "Release and migration records",
    "Migration backups",
}

GOVERNANCE_DOMAIN_ANCHORS = {
    "Startup, work loop, persistence and closeout": "# Agent Handoff Kit Core Runtime",
    "Current state, next action and blockers": "## Current Baseline",
    "Chronological evidence": "# Session Log",
    "Paths, roles, commands and integration coordinates": "## Entry Points",
    "Long-term rationale and trade-offs": "## Architecture Choices",
    "Task routing": "# Rule Packs Router",
    "Cross-file sync obligations": "# Doc Sync Registry",
    "Detailed external-tool behavior and lifecycle": "# Integrations Pack",
    "Product north star, data ownership and invariant outcomes": "## 0. 文件性質與使用方式",
    "URL outcome package and vault writer": "# URL 到輸出模板規格",
    "Infographic coverage, generation, master and acceptance": "### 四道生成閘門",
    "Compiler Worker v2 execution, Knowledge Package SSOT and cutover conditions": "# Compiler Worker 與 Knowledge Package 設計及驗收規格",
    "Outcome-index schema, facets and pagination": "# 成果頁索引資料層設計",
    "General validation model and ledger schema": "## 5. 九層產品驗收",
    "Scenario-to-source/check/stop routing": "## 2. 情景路由表",
    "Stage order and stage status": "## 2. 全程階段總表",
    "Current acceptance result and evidence": "## 1. 當前總判斷",
    "Tool catalogue": "# 本機工具目錄",
    "Compatibility source skill orchestration": "# Source To Obsidian Ingest",
}


GOVERNANCE_UNIQUE_HEADINGS = {
    "## Governance Ownership Map": "dev/PROJECT_INDEX.md",
    "### 四道生成閘門": "docs/資訊圖生成規格.md",
    "## 5. 九層產品驗收": "docs/多層驗收機制.md",
    "## 2. 情景路由表": "docs/分層QC機制.md",
    "## 2. 全程階段總表": "docs/開發計劃與進度.md",
    "## 1. 當前總判斷": "docs/需求驗收追蹤表.md",
}

REQUIRED_CLI_ROUTE_KEYS = {
    "compiler-worker-v2", "governance", "infographic", "knowledge-retrieval", "ks-vs-inventory",
    "mainline-stage-gate", "obsidian-semantic-graph", "obsidian-write", "outcome-index", "outcome-page",
    "project-control-panel", "reader-layer", "release", "stage-one-acceptance", "url-ingest",
}


GOVERNANCE_REQUIRED_EXCLUSIONS = (
    "dev/governance_migrations/**/backup/**",
    "dev/SESSION_LOG_archive/**",
    "dev/qc/evidence/**",
)


GOVERNANCE_STALE_PHRASES = (
    "Governance consolidation and the matching product root fix are in progress",
    "finish the bounded code root fix and run the eight counterexamples",
    "Stages 0 and 4 are reopened and blocked",
    "one complete second-brain intake have passed",
    "Stage 5 exposes only qualified product views",
    "產品級 v5 方向",
    "Stage 3 is waiting for one genuine low-burden 使用者 action",
    "The formal pool is DISCOVERY-",
    "Formal effective 使用者 events and intake events remain zero",
    "三類真實 write/readback UAT 已完成",
    "三類真實 UAT 與真人閉環待驗收",
    "三類真實來源亦已完成",
    "尚欠一筆 使用者 真正選擇",
    "首張真實重做圖應走完整",
    "source_fields_ready:false",
    "下一步須回到核心入庫功能缺口盤點",
)

PROGRESS_DOCS = (
    "docs/開發計劃與進度.md",
    "docs/需求驗收追蹤表.md",
)

PROGRESS_DOC_FORBIDDEN_RULE_TOKENS = {
    "`1024x1536`": "infographic target canvas belongs to docs/資訊圖生成規格.md or the attempt contract",
    "品質方向 `high`": "infographic quality direction belongs to docs/資訊圖生成規格.md or the attempt contract",
}

TOOL_SURFACE_PROGRESS_PATTERNS = (
    (r"UAT\s*已完成", "tool catalogue/control surfaces must not store current UAT status"),
    (r"尚欠一筆", "tool catalogue/control surfaces must not store pending human-loop status"),
    (r"目前缺口", "tool catalogue/control surfaces must not store acceptance gaps"),
    (r"下一步須", "tool catalogue/control surfaces must not store next-step status"),
)

PROGRESS_SYNC_ROW = "Progress, acceptance result or completion-claim change"


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    tier: str
    scope: str
    truth_source: str
    required_reads: tuple[tuple[str, str], ...]
    decision_surfaces: tuple[dict[str, str], ...]
    validation_layers: tuple[str, ...]
    required_checks: tuple[str, ...]
    failure_stop_states: tuple[str, ...]
    forbidden_claims: tuple[str, ...]


def _table_rows(section: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        rows.append(cells)
    return rows


def parse_qc_routes(text: str) -> dict[str, dict[str, str]]:
    heading = "## 2. 情景路由表"
    if text.splitlines().count(heading) != 1:
        raise ValueError("QC route heading must appear exactly once")
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    rows = _table_rows(section)
    if not rows or rows[0] != ["情景", "CLI 識別鍵", "先讀真源", "必跑檢查", "失敗時停在哪裡"]:
        raise ValueError("QC route table header is invalid")
    routes: dict[str, dict[str, str]] = {}
    for cells in rows[1:]:
        if len(cells) != 5:
            raise ValueError(f"QC route row must have five cells: {cells}")
        name, cli_key, sources, checks, stop = cells
        if name in routes:
            raise ValueError(f"duplicate QC route: {name}")
        if not name or not sources or not checks or not stop:
            raise ValueError(f"QC route contains an empty required cell: {name or '<unnamed>'}")
        if cli_key != "—" and (not cli_key or cli_key in {route["cli_key"] for route in routes.values()}):
            raise ValueError(f"duplicate or empty CLI route key: {cli_key}")
        routes[name] = {"cli_key": cli_key, "sources": sources, "checks": checks, "stop": stop}
    return routes


def _registered_paths(cell: str) -> tuple[tuple[str, str], ...]:
    paths: list[tuple[str, str]] = []
    for token in re.findall(r"`([^`]+)`", cell):
        normalized = token.replace("\\", "/")
        if normalized.startswith(("AGENTS.md", "dev/", "docs/", "tools/", "skills/", "outputs/", "tests/", "學習型")):
            paths.append((normalized, "由 QC 路由指定的必讀真源"))
    return tuple(dict.fromkeys(paths))


def _command_fragments(cell: str) -> tuple[str, ...]:
    return (cell,)


def _validation_layers(root: Path) -> tuple[str, ...]:
    path = root / "docs" / "多層驗收機制.md"
    if not path.is_file():
        return ()
    text = path.read_text(encoding="utf-8")
    return tuple(re.findall(r"^### 5\.\d+\s+(.+)$", text, flags=re.MULTILINE))


def load_scenarios(root: Path) -> dict[str, Scenario]:
    route_path = root / "docs" / "分層QC機制.md"
    if not route_path.is_file():
        return {}
    routes = parse_qc_routes(route_path.read_text(encoding="utf-8"))
    layers = _validation_layers(root)
    scenarios: dict[str, Scenario] = {}
    for route_name, route in routes.items():
        key = route["cli_key"]
        if key == "—":
            continue
        stop = route["stop"]
        scenarios[key] = Scenario(
            key=key,
            title=route_name,
            tier="產品級",
            scope=route_name,
            truth_source=route["sources"],
            required_reads=_registered_paths(route["sources"]),
            decision_surfaces=(
                {
                    "name": route_name,
                    "role": "AI + 使用者（需要真人判斷時）",
                    "decision": "按唯一 QC 路由判斷是否可繼續",
                    "required_evidence": route["checks"],
                    "recovery_path": stop,
                },
            ),
            validation_layers=layers,
            required_checks=_command_fragments(route["checks"]),
            failure_stop_states=(stop,),
            forbidden_claims=(f"未清除以下停止條件前不可宣告完成：{stop}",),
        )
    return scenarios


def parse_governance_ownership_map(text: str) -> tuple[list[dict[str, str]], list[str]]:
    heading = "## Governance Ownership Map"
    if text.splitlines().count(heading) != 1:
        return [], ["ownership_map_heading_must_appear_once"]
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for cells in _table_rows(section)[1:]:
        if len(cells) != 3:
            errors.append("ownership_row_must_have_three_cells")
            continue
        domain, authority, allowance = cells
        if domain in seen:
            errors.append(f"duplicate_rule_domain:{domain}")
        seen.add(domain)
        if not authority:
            errors.append(f"missing_sole_authority:{domain}")
        rows.append({"domain": domain, "authority": authority, "allowance": allowance})
    errors.extend(f"missing_rule_domain:{domain}" for domain in sorted(GOVERNANCE_EXPECTED_DOMAINS - seen))
    return rows, errors


def _active_governance_files(root: Path) -> list[Path]:
    files = [
        root / "AGENTS.md",
        root / "START_NEXT_SESSION_PROMPT.txt",
        root / "學習型知識庫_開發初始需求書.md",
        root / "tools" / "README.md",
        root / "skills" / "url-to-obsidian-ingest" / "SKILL.md",
        root / "skills" / "url-to-obsidian-ingest" / "references" / "runbook.md",
        root / "skills" / "url-to-obsidian-ingest" / "agents" / "openai.yaml",
    ]
    files.extend((root / "dev").glob("*.md"))
    files.extend((root / "dev" / "rules").glob("*.md"))
    files.extend((root / "docs").glob("*.md"))
    files.extend((root / "docs" / "releases").glob("*.md"))
    files.extend((root / "outputs").glob("*.md"))
    return sorted({path for path in files if path.is_file() and path.name != "SESSION_LOG.md"})


def validate_governance_baseline(root: Path) -> dict[str, Any]:
    root = root.resolve()
    errors: list[dict[str, str]] = []
    index_path = root / "dev" / "PROJECT_INDEX.md"
    if not index_path.is_file():
        return {"status": "needs_review", "errors": [{"reason": "missing_project_index"}]}

    index_text = index_path.read_text(encoding="utf-8")
    rows, map_errors = parse_governance_ownership_map(index_text)
    errors.extend({"reason": error} for error in map_errors)
    if "`TBD`" in index_text:
        errors.append({"reason": "project_index_contains_unresolved_path"})
    for excluded in GOVERNANCE_REQUIRED_EXCLUSIONS:
        if excluded not in index_text:
            errors.append({"reason": "governance_exclusion_not_registered", "path": excluded})
    for dead in ("tests/skill_套件.py", "agent-handoff-kit-guide.html"):
        if dead in index_text:
            errors.append({"reason": "project_index_contains_dead_reference", "path": dead})

    sync_path = root / "dev" / "DOC_SYNC_REGISTRY.md"
    sync_text = sync_path.read_text(encoding="utf-8") if sync_path.is_file() else ""
    if PROGRESS_SYNC_ROW not in sync_text:
        errors.append({"reason": "progress_sync_rule_missing", "path": "dev/DOC_SYNC_REGISTRY.md"})

    for row in rows:
        authorities = re.findall(r"`([^`]+)`", row["authority"])
        if not authorities:
            errors.append({"reason": "authority_has_no_registered_path", "domain": row["domain"]})
        for authority in authorities:
            matches = list(root.glob(authority)) if "*" in authority else [root / authority]
            if not any(path.exists() for path in matches):
                errors.append({"reason": "authority_path_unreachable", "domain": row["domain"], "path": authority})
        anchor = GOVERNANCE_DOMAIN_ANCHORS.get(row["domain"])
        primary = root / authorities[0] if authorities and "*" not in authorities[0] else None
        if anchor and (primary is None or not primary.is_file() or anchor not in primary.read_text(encoding="utf-8")):
            errors.append({"reason": "authority_domain_mismatch", "domain": row["domain"], "anchor": anchor})

    active_files = _active_governance_files(root)
    texts = {path: path.read_text(encoding="utf-8") for path in active_files}
    for heading, expected_rel in GOVERNANCE_UNIQUE_HEADINGS.items():
        matches = [path for path, text in texts.items() if heading in text]
        expected = root / expected_rel
        if matches != [expected]:
            errors.append({
                "reason": "authoritative_heading_not_unique",
                "heading": heading,
                "expected": expected_rel,
                "matches": ";".join(str(path.relative_to(root)) for path in matches),
            })

    for path, text in texts.items():
        rel = str(path.relative_to(root)).replace("\\", "/")
        if path != index_path and "dev/governance_migrations/" in text and "/backup/" in text:
            errors.append({"reason": "active_document_links_migration_backup", "path": rel})
        for phrase in GOVERNANCE_STALE_PHRASES:
            if phrase in text:
                errors.append({"reason": "stale_current_state", "path": rel, "phrase": phrase})

    for rel in PROGRESS_DOCS:
        path = root / rel
        if not path.is_file():
            errors.append({"reason": "progress_authority_missing", "path": rel})
            continue
        text = path.read_text(encoding="utf-8")
        for token, owner in PROGRESS_DOC_FORBIDDEN_RULE_TOKENS.items():
            if token in text:
                errors.append({
                    "reason": "progress_doc_redefines_product_rule",
                    "path": rel,
                    "token": token,
                    "owner": owner,
                })

    for rel in ("tools/README.md", "tools/project_control_panel.py"):
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, meaning in TOOL_SURFACE_PROGRESS_PATTERNS:
            if re.search(pattern, text):
                errors.append({
                    "reason": "tool_surface_contains_static_progress_claim",
                    "path": rel,
                    "pattern": pattern,
                    "meaning": meaning,
                })

    multi_path = root / "docs" / "多層驗收機制.md"
    multi_text = multi_path.read_text(encoding="utf-8") if multi_path.is_file() else ""
    route_boundary = multi_text.split("## 6. 情景路由邊界", 1)[1] if "## 6. 情景路由邊界" in multi_text else ""
    if not route_boundary or "### 6." in route_boundary or "必驗:" in route_boundary:
        errors.append({"reason": "general_validation_redefines_scenario_routes"})

    entry_section = index_text.split("## Entry Points", 1)[1].split("\n## ", 1)[0]
    for forbidden in ("completion_report", "SHA-256", "formal_write_allowed", "max_candidates_per_asset"):
        if forbidden in entry_section:
            errors.append({"reason": "project_index_redefines_product_rule", "token": forbidden})
    if "| Runbook | `tools/README.md`" in entry_section:
        errors.append({"reason": "tool_catalog_mislabeled_as_runbook"})

    decisions_path = root / "dev" / "PROJECT_DECISIONS.md"
    decisions_text = decisions_path.read_text(encoding="utf-8") if decisions_path.is_file() else ""
    for executable in ("authorized=true", "attempt_id", "pending → capturing", "Firecrawl 優先", "每張資產最多"):
        if executable in decisions_text:
            errors.append({"reason": "decision_log_contains_executable_rule", "token": executable})

    tool_boundary_files = [
        root / "tools" / "README.md",
        root / "skills" / "url-to-obsidian-ingest" / "SKILL.md",
        root / "skills" / "url-to-obsidian-ingest" / "references" / "runbook.md",
        root / "skills" / "url-to-obsidian-ingest" / "agents" / "openai.yaml",
    ]
    next_action_files = [path for path in tool_boundary_files if path.is_file() and "next_action" in path.read_text(encoding="utf-8")]
    if next_action_files:
        errors.append({"reason": "tool_surfaces_redefine_resume_boundary", "paths": ";".join(str(p.relative_to(root)) for p in next_action_files)})

    route_path = root / "docs" / "分層QC機制.md"
    try:
        routes = parse_qc_routes(route_path.read_text(encoding="utf-8"))
        cli_keys = {route["cli_key"] for route in routes.values() if route["cli_key"] != "—"}
        if cli_keys != REQUIRED_CLI_ROUTE_KEYS:
            errors.append({
                "reason": "cli_qc_route_key_drift",
                "missing": ";".join(sorted(REQUIRED_CLI_ROUTE_KEYS - cli_keys)),
                "unexpected": ";".join(sorted(cli_keys - REQUIRED_CLI_ROUTE_KEYS)),
            })
        if "docs/個人知識與品味狀態頁_v5產品規格.md" in route_path.read_text(encoding="utf-8"):
            errors.append({"reason": "retired_spec_used_by_current_qc_route"})
    except (OSError, ValueError) as exc:
        errors.append({"reason": "qc_route_parse_failed", "detail": str(exc)})

    readme_path = root / "tools" / "README.md"
    if readme_path.is_file() and len(readme_path.read_text(encoding="utf-8").splitlines()) > 120:
        errors.append({"reason": "tool_catalog_redefines_workflow"})

    control_panel_source = root / "tools" / "project_control_panel.py"
    if control_panel_source.is_file() and "docs/個人知識與品味狀態頁_v5產品規格.md" in control_panel_source.read_text(encoding="utf-8"):
        errors.append({"reason": "retired_spec_used_by_current_control_panel"})

    handoff_path = root / "dev" / "SESSION_HANDOFF.md"
    prompt_path = root / "START_NEXT_SESSION_PROMPT.txt"
    if handoff_path.is_file() and prompt_path.is_file():
        handoff_text = handoff_path.read_text(encoding="utf-8")
        marker = "## Next Session Opening Message"
        opening = ""
        if marker in handoff_text:
            tail = handoff_text.split(marker, 1)[1]
            match = re.search(r"```text\s*\n(.*?)\n```", tail, re.DOTALL)
            opening = match.group(1).strip() if match else ""
        if not opening or opening != prompt_path.read_text(encoding="utf-8").strip():
            errors.append({"reason": "startup_prompt_mirror_drift"})

    return {
        "status": "ready" if not errors else "needs_review",
        "ownership_rows": len(rows),
        "active_files_checked": len(active_files),
        "excluded_historical_paths": list(GOVERNANCE_REQUIRED_EXCLUSIONS),
        "errors": errors,
    }


SCENARIOS = load_scenarios(DEFAULT_ROOT)


def build_manifest(scenario_key: str, *, root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    root = root.resolve()
    if not (root / "docs" / "分層QC機制.md").is_file():
        raise ValueError(f"QC route source missing under requested root: {root}")
    governance_root = root
    scenarios = load_scenarios(root)
    if scenario_key not in scenarios:
        raise ValueError(f"unknown or unrouted scenario: {scenario_key}; supported: {', '.join(sorted(scenarios))}")
    scenario = scenarios[scenario_key]
    reads: list[dict[str, Any]] = []
    missing: list[str] = []
    for path_text, role in scenario.required_reads:
        path = governance_root / path_text
        exists = path.exists()
        reads.append({"path": path_text, "role": role, "exists": exists})
        if not exists:
            missing.append(path_text)
    blocked = [{"reason": "missing_required_read", "path": path} for path in missing]
    governance = None
    if scenario_key == "governance" and not missing:
        governance = validate_governance_baseline(governance_root)
        blocked.extend(governance["errors"])
    return {
        "status": "ready" if not blocked else "needs_review",
        "mode": "dry_run",
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "scenario": scenario.key,
        "title": scenario.title,
        "tier": scenario.tier,
        "scope": scenario.scope,
        "root": str(root.resolve()),
        "truth_source": scenario.truth_source,
        "truth_boundary": "This manifest projects docs/分層QC機制.md. It does not define product rules or write data.",
        "ledger_template": LEDGER_TEMPLATE,
        "required_reads": reads,
        "missing_required_reads": missing,
        "decision_surfaces": list(scenario.decision_surfaces),
        "validation_layers": list(scenario.validation_layers),
        "required_checks": list(scenario.required_checks),
        "failure_stop_states": list(scenario.failure_stop_states),
        "forbidden_claims": list(scenario.forbidden_claims),
        "governance_baseline": governance,
        "blocked_reasons": blocked,
    }


def build_embedded_manifest(scenario_key: str, *, root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    governance_root = root if (root / "docs" / "分層QC機制.md").is_file() else DEFAULT_ROOT
    manifest = build_manifest(scenario_key, root=governance_root)
    return {key: value for key, value in manifest.items() if key not in {"mode", "root", "scope", "truth_boundary", "ledger_template"}}


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS)


def print_human(result: dict[str, Any]) -> None:
    if result["mode"] == "list":
        print("supported scenarios:")
        for scenario in result["scenarios"]:
            print(f"- {scenario['key']}: {scenario['title']} ({scenario['tier']})")
        return
    print(f"status: {result['status']}")
    print(f"scenario: {result['scenario']} - {result['title']}")
    for check in result["required_checks"]:
        print(f"- {check}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Project the authoritative QC route for one scenario.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.list:
        result: dict[str, Any] = {
            "status": "ready",
            "mode": "list",
            "scenarios": [{"key": item.key, "title": item.title, "tier": item.tier} for item in SCENARIOS.values()],
        }
    else:
        if not args.scenario:
            parser.error("--scenario is required unless --list is used")
        result = build_manifest(args.scenario, root=args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "", end="" if args.json else "")
    if not args.json:
        print_human(result)
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
