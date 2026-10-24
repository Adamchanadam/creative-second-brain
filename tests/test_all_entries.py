"""Exercise every portable brain entry without live external source retrieval."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
ENTRIES = (
    "brain-url", "brain-text", "brain-image", "brain-infographic", "brain-think", "brain-ask", "brain-grow", "brain-create",
    "brain-tidy", "brain-guide", "brain-nebula", "brain-check", "brain-resume", "brain-fix", "brain-research", "brain-update",
)

# Normal single-source intake and one-shot infographic entries are single-pass: they must not open a task progress card.
SINGLE_PASS_ENTRIES = ("brain-url", "brain-text", "brain-image", "brain-infographic")


def run(*items: str, cwd: Path, expected: int = 0) -> str:
    result = subprocess.run([PYTHON, "-B", *items], cwd=cwd, text=True,
                            encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError("expected exit " + str(expected) + ": " + " ".join(items) + "\n" + result.stdout + result.stderr)
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
    workspace = work / "my-brain"
    setup = package / "scripts" / "setup_workspace.py"
    run(str(setup), "--profile", "public-portable", "--workspace", str(workspace),
        "--vault", "My Notes", "--display-name", "Entry Test", cwd=work)
    brain = workspace / "tools" / "brain.py"
    registry = json.loads((package / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    children = {path.name for path in (workspace / ".agents" / "skills").iterdir() if path.is_dir()}
    assert children == set(ENTRIES)
    for entry in ENTRIES:
        skill = workspace / ".agents" / "skills" / entry / "SKILL.md"
        assert skill.is_file()
        content = skill.read_text(encoding="utf-8")
        if entry == "brain-ask":
            assert "舊名相容" in content and "../brain-think/SKILL.md" in content
        elif entry in SINGLE_PASS_ENTRIES:
            assert "不建立任務進度卡" in content and "🧭 任務進度卡" not in content and "execution" in content
        else:
            assert "🧭 任務進度卡" in content and "outputs/task-progress/current.md" in content
    assert "outputs/task-progress/current.md" in (workspace / "AGENTS.md").read_text(encoding="utf-8")
    # Controlled task-card dry-run: the shared entry contract names one location,
    # and a fresh workspace can create and read back its current card without
    # touching the vault or completing an intake.
    card = workspace / "outputs" / "task-progress" / "current.md"
    card.parent.mkdir(parents=True)
    card.write_text("# 🧭 任務進度卡\n\n| 目標 | 目前階段 | 直接成果 |\n|---|---|---|\n| dry-run | 1／2 `▰▱` | 已建立 |\n\n- 正在做甚麼：讀回入口規則。\n- 下一步：完成受控驗收。\n", encoding="utf-8")
    assert "1／2" in card.read_text(encoding="utf-8")

    # URL retrieval needs a user-selected, live source.  Its parser route is
    # covered without making an unrequested network request or a test import.
    assert "rail start --url" in registry["url"]["entry"]
    url_help = run(str(brain), "rail", "start", "--help", cwd=workspace)
    assert "--url" in url_help

    source = workspace / "inputs" / "source.md"
    source.write_text("# Portable entry test\n\nComplete text for a local work order.", encoding="utf-8")
    text = json.loads(run(str(brain), "rail", "start", "--text-file", "inputs/source.md", "--source-id", "KS-VS-9101", cwd=workspace))
    assert text["status"] == "needs_input" and text["source_kind"] == "user_provided_text"
    assert text["continuation"]["directive"] == "continue_same_job"
    assert text["continuation"]["owner"] == "agent" and text["continuation"]["task_complete"] is False
    image_source = workspace / "assets" / "brand" / "creative-second-brain-nebula-logo-v4.png"
    image = json.loads(run(str(brain), "rail", "start", "--image-file", str(image_source.relative_to(workspace)), "--source-id", "KS-VS-9102", cwd=workspace))
    assert image["status"] == "needs_input" and image["source_kind"] == "user_provided_image"
    assert image["continuation"]["directive"] == "continue_same_job"
    assert image["continuation"]["task_complete"] is False

    ask = json.loads(run(str(brain), "think", "prepare", "--question", "這份新庫目前有甚麼證據？", "--no-expand", "這是安裝驗收的固定問題", cwd=workspace))
    alias = json.loads(run(str(brain), "ask", "prepare", "--question", "這份新庫目前有甚麼證據？", "--no-expand", "這是安裝驗收的固定問題", cwd=workspace))
    grow = json.loads(run(str(brain), "think", "prepare", "--question", "評估這個來源是否值得保存？", "--no-expand", "這是安裝驗收的固定問題", cwd=workspace))
    assert ask["status"] == "insufficient_evidence" and grow["status"] == "insufficient_evidence"
    assert alias["status"] == ask["status"]
    assert "brain-think" in (workspace / ".agents" / "skills" / "brain-ask" / "SKILL.md").read_text(encoding="utf-8")
    create = run(str(brain), "material", "--query", "比較", cwd=workspace, expected=1)
    assert "candidates: 0" in create
    tidy = json.loads(run(str(brain), "tidy", "--acceptance", "--check", "--json", cwd=workspace, expected=1))
    assert tidy["status"] == "needs_review"

    version = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))["package_version"]
    home_paths = {
        "home": workspace / "index.html",
        "outcome_index": workspace / "My Notes" / "outcome-pages" / "index.html",
        "config": workspace / "brain.json",
    }
    home_before_lookup = {name: path.read_bytes() for name, path in home_paths.items()}
    opened = json.loads(run(str(brain), "home", cwd=workspace))
    assert opened == {
        "status": "ready", "writes": 0, "package_version": version,
        "paths": {
            "home": "index.html",
            "outcome_index": "My Notes/outcome-pages/index.html",
        },
        "missing": [],
    }
    assert {name: path.read_bytes() for name, path in home_paths.items()} == home_before_lookup
    bad_home = json.loads(run(str(brain), "home", "unexpected", cwd=workspace, expected=2))
    assert bad_home["status"] == "blocked" and "home takes no arguments" in bad_home["reason"]

    home_before_dry_run = (workspace / "index.html").read_bytes()
    refresh_plan = json.loads(run(str(setup), "--refresh-home", "--workspace", str(workspace), "--dry-run", cwd=work))
    assert refresh_plan == {"status":"home_refresh_planned", "writes":0, "historical_notes_rewritten":False}
    assert (workspace / "index.html").read_bytes() == home_before_dry_run

    refreshed = json.loads(run(str(brain), "guide", cwd=workspace))
    assert refreshed["status"] == "home_refreshed"
    home = (workspace / "index.html").read_text(encoding="utf-8")
    guide_skill = (workspace / ".agents" / "skills" / "brain-guide" / "SKILL.md").read_text(encoding="utf-8")
    assert f"目前使用：創造型第二大腦 v{version}" in home and "介面 v1.0.0" not in home
    nebula_skill = (workspace / ".agents" / "skills" / "brain-nebula" / "SKILL.md").read_text(encoding="utf-8")
    assert "若使用者只問目前版本" in guide_skill and "不要啟動服務或改檔" in guide_skill
    assert "`<workspace>/tools/brain.py open-home`" in guide_skill and "--serve" not in guide_skill and "``<workspace>" not in guide_skill
    assert "`<workspace>/tools/brain.py nebula`" in nebula_skill and "--serve" not in nebula_skill
    assert "index.html#prompts" in guide_skill and "立即向使用者提供可點擊 URL" not in guide_skill
    assert "正常開啟不自動抓 DOM、截圖或驗收 UI" in guide_skill
    assert "正常開啟不自動抓 DOM、截圖或驗收 UI" in nebula_skill
    assert "不可直接執行 `knowledge_nebula_server.py`" in guide_skill and "不得改產品或寫死 OS、路徑、執行器假設" in guide_skill
    checked = json.loads(run(str(brain), "doctor", cwd=workspace))
    resumed = json.loads(run(str(brain), "context", cwd=workspace))
    repaired = json.loads(run(str(brain), "rail", "inspect", "--job-id", text["job_id"], cwd=workspace))
    assert checked["status"] == "ready" and text["job_id"] in {job["job_id"] for job in resumed["jobs"]}
    assert repaired["job_id"] == text["job_id"]

    research = json.loads(run(str(brain), "research", "list", cwd=workspace))
    research_help = run(str(brain), "research-program", "--help", cwd=workspace)
    research_skill = (workspace / ".agents" / "skills" / "brain-research" / "SKILL.md").read_text(encoding="utf-8")
    assert research["topics"] == [] and "init-topic" in research_help and "docs/研究主題操作.md" in research_skill
    update_help = run(str(brain), "update", "--help", cwd=workspace)
    update_skill = (workspace / ".agents" / "skills" / "brain-update" / "SKILL.md").read_text(encoding="utf-8")
    assert "--workspace" in update_help and "--check" in update_help and "--apply" in update_help
    assert "tools/brain_update.py" in update_skill and "舊版 workspace 不可先要求 `doctor` 或 `context`" in update_skill
    assert "Adamchanadam/creative-second-brain" in update_skill and "不用 npm" in update_skill
    infographic = json.loads(run(str(brain), "rail", "add-infographic", cwd=workspace))
    infographic_skill = (workspace / ".agents" / "skills" / "brain-infographic" / "SKILL.md").read_text(encoding="utf-8")
    assert infographic["action"] == "select_infographic_source" and infographic["candidates"] == []
    assert "--source-id <知識頁ID>" in infographic_skill and "不自動畫" in infographic_skill
    report = {"status": "passed", "covered_entries": list(ENTRIES), "coverage": [
        "URL route parser", "text work order", "image work order", "canonical think and legacy ask alias", "grow", "create", "tidy",
        "guide refresh", "nebula entry route", "check", "resume", "fix inspect", "optional infographic route", "research list and workflow route", "safe update route", "task-progress-card dry-run",
    ], "not_proven": ["live user-selected URL capture", "host skill discovery", "native Obsidian", "new image generation"]}
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
