"""Offline acceptance for the full portable skill source.

All artifacts stay under --work-root.  This checks an exported package, not
the source workspace, so accidental OPS/PUBLIC runtime dependencies surface.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
COMMANDS = {
    "brain-url", "brain-text", "brain-image", "brain-think", "brain-ask", "brain-grow", "brain-create",
    "brain-tidy", "brain-guide", "brain-nebula", "brain-check", "brain-resume", "brain-fix", "brain-research", "brain-update",
}
PROMPTS = {
    "think": "$brain-think [問題]", "url": "$brain-url [網址]", "text": "$brain-text\n[貼上原文]",
    "image": "$brain-image [附上一張圖片]", "grow": "$brain-grow [來源]", "create": "$brain-create [想做的成品]",
    "research": "$brain-research 查看目前研究主題", "tidy": "$brain-tidy", "guide": "$brain-guide", "nebula": "$brain-nebula",
    "check": "$brain-check [來源或工作編號]", "resume": "$brain-resume [來源或工作編號]",
    "fix": "$brain-fix [哪裏有甚麼問題]", "update": "$brain-update",
}


def run(*args: str, cwd: Path, expected: int = 0) -> str:
    result = subprocess.run([PYTHON, "-B", *args], cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError(f"expected {expected}, got {result.returncode}: {' '.join(args)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    work = args.work_root.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    work.mkdir(parents=True, exist_ok=True)
    if args.package:
        package = args.package.resolve()
    else:
        build = work / "build"
        run(str(ROOT / "scripts" / "build_profiles.py"), "--output", str(build), "--profile", "public-portable", cwd=ROOT)
        package = build / "public-portable"
    run(str(package / "scripts" / "verify_package.py"), "--package", str(package), cwd=package)

    # The package is a closed delivery: no source OPS/PUBLIC path is needed.
    for file in package.rglob("*.py"):
        run("-c", "import ast, pathlib; ast.parse(pathlib.Path(r'''" + str(file) + "''').read_text(encoding='utf-8-sig'))", cwd=package)
    package_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in package.rglob("*") if path.is_file() and path.suffix in {".py", ".md", ".html", ".json"})
    assert "學習型知識庫系統_OPS" not in package_text and "學習型知識庫系統_PUBLIC" not in package_text
    assert "KS-VS-0025_how_openai_uses_codex" not in package_text and "跟 Adam 有何關係" not in package_text
    for asset in ("reader_theme.css", "thinking_map.css", "thinking_map.js", "outcome_catalog_assets.js"):
        assert (package / "runtime" / "workspace" / "tools" / asset).is_file(), asset
    for browser_asset in ("case_reproduction_capture.js", "package.json", "package-lock.json"):
        assert (package / "runtime" / "workspace" / "tools" / browser_asset).is_file(), browser_asset
    manifest_text = (package / "runtime" / "workspace" / "tools" / "validation_manifest.py").read_text(encoding="utf-8")
    assert "Taste evidence, event and candidate analysis" not in manifest_text
    assert "# 個人品味動態分析系統規格" not in manifest_text

    workspace = work / "workspace"
    setup = package / "scripts" / "setup_workspace.py"
    run(str(setup), "--profile", "public-portable", "--workspace", str(workspace), "--vault", "私人/創作庫", "--display-name", "測試用戶", "--dry-run", cwd=package)
    run(str(setup), "--profile", "public-portable", "--workspace", str(workspace), "--vault", "私人/創作庫", "--display-name", "測試用戶", cwd=package)
    brain = workspace / "tools" / "brain.py"
    doctor = json.loads(run(str(brain), "doctor", cwd=workspace))
    assert doctor["status"] == "ready" and doctor["vault"] == "私人/創作庫"
    vault = workspace / "私人" / "創作庫"
    graph_file = vault / ".obsidian" / "graph.json"
    graph_settings = json.loads(graph_file.read_text(encoding="utf-8"))
    assert graph_settings["search"] == "path:concepts"
    assert graph_settings["showOrphans"] is True
    assert graph_settings["nodeSizeMultiplier"] == 1 and graph_settings["lineSizeMultiplier"] == 1
    assert graph_settings["centerStrength"] == 0.518713248970312
    assert graph_settings["repelStrength"] == 10 and graph_settings["linkStrength"] == 1 and graph_settings["linkDistance"] == 300
    assert {group["query"] for group in graph_settings["colorGroups"]} == {
        "path:concepts [graph_group:hub]",
        "path:concepts [graph_group:tension]",
        "path:concepts [graph_group:taste_signal]",
        "path:concepts [graph_group:application_scene]",
    }
    installed = {path.name for path in (workspace / ".agents" / "skills").iterdir() if path.is_dir()}
    assert COMMANDS == installed
    home = (workspace / "index.html").read_text(encoding="utf-8")
    assert home.count('data-skill="brain-') == len(PROMPTS) and ">14 項</span>" in home
    assert "測試用戶 的第二大腦" in home and "本候選" not in home
    assert "creative-second-brain-nebula-logo-v4.png" in home
    assert home.count("創造型第二大腦 | Adam Chan") == 1
    from bs4 import BeautifulSoup
    home_soup = BeautifulSoup(home, "html.parser")
    for key, expected in PROMPTS.items():
        field = home_soup.select_one("#public-command-" + key)
        assert field is not None and field.get_text() == expected, key
    for command in COMMANDS:
        assert (workspace / ".agents" / "skills" / command / "SKILL.md").is_file()
    alias_skill = (workspace / ".agents" / "skills" / "brain-ask" / "SKILL.md").read_text(encoding="utf-8")
    assert "舊名相容" in alias_skill and "brain-think" in alias_skill
    for key, expected in PROMPTS.items():
        metadata = workspace / ".agents" / "skills" / ("brain-" + key) / "agents" / "openai.yaml"
        assert metadata.is_file() and json.dumps(expected, ensure_ascii=False) in metadata.read_text(encoding="utf-8"), key

    # The portable writer keeps formal idea types conservative, but gives every
    # generated concept a deterministic visual group for the graph preset.
    sys.path.insert(0, str(workspace))
    from tools.obsidian_vault_adapter import AdapterStop, assert_source_id_is_available, frontmatter_fields, project_concept_graph_for_sources
    orphan_dir = vault / "knowledge-points" / "by-source" / "ORPHAN-TEST"
    orphan_dir.mkdir(parents=True)
    orphan_dir.joinpath("KP-ORPHAN-1.md").write_text(
        "---\n"
        "type: knowledge_point\n"
        "knowledge_point: 首份材料的獨立觀點\n"
        "kp_uid: ORPHAN-TEST:KP-1\n"
        "---\n\n沒有關係的有效概念。\n",
        encoding="utf-8",
    )
    orphan_projection = project_concept_graph_for_sources(vault, {"ORPHAN-TEST"})
    assert orphan_projection["status"] == "ready"
    assert len(orphan_projection["concept_notes"]) == 1
    orphan_note = next(path for path in (vault / "concepts").glob("*.md") if "首份材料的獨立觀點" in path.read_text(encoding="utf-8"))
    assert "## 相關概念" not in orphan_note.read_text(encoding="utf-8")
    samples = {
        "跨來源概念整理": "hub",
        "資料保存 vs 立即行動的取捨": "tension",
        "偏好可驗證的資料整理": "taste_signal",
        "如何把知識變成可用的工作流程": "application_scene",
    }
    kp_dir = vault / "knowledge-points" / "by-source" / "GRAPH-TEST"
    kp_dir.mkdir(parents=True)
    for number, concept in enumerate(samples, start=1):
        (kp_dir / f"KP-GRAPH-{number}.md").write_text(
            "---\n"
            "type: knowledge_point\n"
            + f"knowledge_point: {json.dumps(concept, ensure_ascii=False)}\n"
            + f"kp_uid: GRAPH-TEST:KP-{number}\n"
            "---\n\n測試用知識點。\n",
            encoding="utf-8",
        )
    (vault / "_registry" / "relations.jsonl").write_text(
        json.dumps({
            "relation_id": "REL-GRAPH-1",
            "source_kp_uid": "GRAPH-TEST:KP-1",
            "target_kp_uid": "GRAPH-TEST:KP-2",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    graph_projection = project_concept_graph_for_sources(vault, {"GRAPH-TEST"})
    assert graph_projection["status"] == "ready"
    assert graph_projection["verification"]["passed"] is True
    generated = [frontmatter_fields(path) for path in (vault / "concepts").glob("*.md")]
    assert {str(fields.get("graph_group")) for fields in generated} == set(samples.values())
    assert all(fields.get("idea_type") == "concept" for fields in generated)
    assert any("## 相關概念" in path.read_text(encoding="utf-8") for path in (vault / "concepts").glob("*.md"))

    # Source identity deduplication is not enough: an explicitly supplied
    # stale source ID must not be able to claim another source's path.
    from tools import compiler_worker_v2 as worker
    collision_root = work / "source-id-collision"
    collision_vault = collision_root / "knowledge-base-vault"
    collision_registry = collision_vault / "_registry" / "sources.jsonl"
    collision_registry.parent.mkdir(parents=True)
    collision_registry.write_text(json.dumps({
        "source_id": "KS-VS-9999", "source_identity": "url:existing-source", "status": "linked",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        worker.ensure_accepted_job(
            root=collision_root, job_id="COLLISION-JOB", source_id="KS-VS-9999",
            source_input_kind="url", source_identity="url:other-source", source_url="https://example.com/other",
        )
    except worker.CompilerWorkerV2Error as exc:
        assert "already reserved" in str(exc)
    else:
        raise AssertionError("compiler accepted a source ID owned by a different source")
    try:
        assert_source_id_is_available(collision_vault, "KS-VS-9999", "url:other-source")
    except AdapterStop as exc:
        assert exc.status == "blocked" and "already linked" in exc.message
    else:
        raise AssertionError("writer accepted a source ID owned by a different source")

    # Exercise the real completion command's ordering: the source-local writer
    # must finish and pass its own readback before its concept graph is touched.
    compiler_root = work / "compiler-command"
    compiler_package = compiler_root / "package"
    compiler_package.mkdir(parents=True)
    compiler_vault = compiler_root / "vault"
    compiler_vault.mkdir()
    compiler_kp = compiler_package / "knowledge-package.json"
    compiler_kp.write_text(json.dumps({
        "job_id": "GRAPH-COMMAND-JOB",
        "source": {
            "source_id": "GRAPH-COMMAND",
            "source_identity": "graph-command-identity",
            "source_input_kind": "user_provided_text",
            "asset_mode": "formal_infographic",
        },
    }, ensure_ascii=False), encoding="utf-8")
    compiler_slice = compiler_package / "source.md"
    compiler_slice.write_text("# graph command fixture\n", encoding="utf-8")
    compiler_job = {"status": "ready", "first_accepted_at": "2026-10-06T00:00:00+00:00"}
    command_events: list[str] = []

    def source_local_writer(**_kwargs):
        command_events.append("writer")
        return {
            "status": "ready", "verification": {"passed": True},
            "source_identity": "graph-command-identity", "source_note": "sources/source.md",
            "application_note": "applications/application.md", "knowledge_points": [], "assets": [], "bases": [],
        }

    def graph_after_writer(_vault, _source_ids):
        command_events.append("graph")
        assert command_events == ["writer", "graph"]
        return {"status": "ready", "verification": {"passed": True}, "concept_notes": []}

    def summary(*_args, **_kwargs):
        return {"mode": "test"}

    with patch.multiple(
        worker,
        validate_job_package_binding=lambda *_args: compiler_job,
        validate_kp=lambda *_args: [],
        ensure_ingest_plan=lambda *_args: None,
        ingest_execution_summary=summary,
        project_source_slice=lambda *_args: compiler_slice,
        write_vault_and_readback=source_local_writer,
        project_concept_graph_for_sources=graph_after_writer,
        portable_tool_result=lambda value, **_kwargs: dict(value),
        outcome_recovery_checkpoint=lambda *_args: {},
        recover_incremental_outcome=lambda *_args: None,
        generate_incremental_outcome=lambda *_args: {"output_dir": "outputs", "html_index": "index.html", "obsidian_portal": "portal.md", "current_page": "page.md"},
        audit_outcome_reader_quality=lambda *_args, pending_revalidation_revision=None: {"status": "passed"},
        artifact_manifest=lambda *_args: {},
        build_coverage_audit=lambda *_args: {},
        write_current_infographic_pointer=lambda *_args: None,
        complete_v2_job=lambda *_args: None,
        write_v2_job=lambda *_args: None,
    ):
        assert worker.command_project(SimpleNamespace(root=compiler_root, kp_file=compiler_kp, write_vault=True, vault=compiler_vault)) == 0
    assert command_events == ["writer", "graph"]

    research = json.loads(run(str(brain), "research", "list", cwd=workspace))
    assert research["topics"] == []
    material = run(str(brain), "material", "--query", "創意", cwd=workspace, expected=1)
    assert "candidates: 0" in material
    shutil.copyfile(ROOT / "fixtures" / "legacy-for-adam.md", workspace / "inputs" / "complete.md")
    order_text = run(str(brain), "text", "start", "--text-file", "inputs/complete.md", "--source-id", "KS-VS-9001", cwd=workspace)
    assert "D:\\" not in order_text and "C:\\" not in order_text
    order = json.loads(order_text)
    assert order["status"] == "needs_input" and order["source_id"] == "KS-VS-9001"

    # A new graph must use the portable preset and not revive OPS or retired
    # product navigation.
    graph = run(str(brain), "graph", "--write", "--check", "--json", cwd=workspace)
    assert json.loads(graph)["status"] == "ready"
    assert "KS-VS-0025" not in graph and "Adam" not in graph
    graph_files = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in (vault / "maps").rglob("*.md"))
    assert "KS-VS-0025" not in graph_files and "Adam" not in graph_files
    assert "品味網入口" not in graph_files and "taste-intake" not in graph_files

    custom_graph = {"search": "path:my-custom-notes", "marker": "keep-user-choice"}
    graph_file.write_text(json.dumps(custom_graph, ensure_ascii=False), encoding="utf-8")
    preserved_projection = project_concept_graph_for_sources(vault, {"GRAPH-TEST"})
    assert preserved_projection["status"] == "ready"
    assert preserved_projection["graph_settings"]["custom_settings_preserved"] is True
    assert json.loads(graph_file.read_text(encoding="utf-8")) == custom_graph

    # Root-fix counterexamples must run against the installed package, not
    # merely against its source or a route-only prompt check.
    research_integrity = json.loads(run(
        str(ROOT / "tests" / "test_research_integrity.py"), "--workspace", str(workspace), cwd=package
    ))
    assert research_integrity["status"] == "passed"
    runtime_parity = json.loads(run(
        str(ROOT / "tests" / "test_runtime_portability_parity.py"), "--workspace", str(workspace), cwd=package
    ))
    assert runtime_parity["status"] == "passed"
    run(str(ROOT / "tests" / "test_ingest_rail_contract.py"),
        "--runtime-root", str(workspace), "--work-root", str(work / "rail-contract"), cwd=package)

    print(json.dumps({"status": "passed", "work_root": str(work), "coverage": [
        "exported closed package", "full brain entry set", "nested named vault", "research catalogue",
        "material selector", "text work-order without absolute paths", "portable graph preset, visible first orphan and source-local projection",
        "graph without OPS private seeds or retired taste navigation", "user graph settings preserved",
        "custom-vault research integrity root-fix counterexamples",
        "installed runtime portability parity counterexamples",
        "installed rail continuation and compiler-to-writer coverage contract counterexamples",
    ], "not_proven": ["native host discovery", "Obsidian UI", "live URL capture", "new image generation"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
