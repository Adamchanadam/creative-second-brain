"""Verify the local delivery through a freshly extracted ZIP package."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup

PYTHON = sys.executable
SOURCE_ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "think": "$brain-think [問題]", "url": "$brain-url [網址]", "text": "$brain-text\n[貼上原文]",
    "image": "$brain-image [附上一張圖片]", "grow": "$brain-grow [來源]", "create": "$brain-create [想做的成品]",
    "research": "$brain-research 查看目前研究主題", "tidy": "$brain-tidy", "guide": "$brain-guide", "nebula": "$brain-nebula",
    "check": "$brain-check [來源或工作編號]", "resume": "$brain-resume [來源或工作編號]",
    "fix": "$brain-fix [哪裏有甚麼問題]", "update": "$brain-update",
}


def run(*items: str, cwd: Path, expected: int = 0) -> str:
    result = subprocess.run([PYTHON, "-B", *items], cwd=cwd, text=True,
                            encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError("command failed: " + " ".join(items) + "\n" + result.stdout + result.stderr)
    return result.stdout


def assert_prompts(soup: BeautifulSoup, prefix: str) -> None:
    fields = soup.select(f'textarea[id^="{prefix}"]')
    assert len(fields) == len(PROMPTS)
    for key, expected in PROMPTS.items():
        field = soup.select_one("#" + prefix + key)
        assert field is not None and field.get_text() == expected, key


def assert_local_resources(soup: BeautifulSoup, html: str, root: Path) -> None:
    refs = [node.get(attribute) for node in soup.select("[src],[href]")
            for attribute in ("src", "href") if node.get(attribute)]
    refs.extend(re.findall(r"url\(\s*['\"]?([^'\"\s)]+)", html))
    for ref in refs:
        if ref.startswith(("#", "%23", "https://", "http://", "data:", "mailto:", "javascript:")):
            continue
        relative = ref.split("#", 1)[0].split("?", 1)[0]
        assert relative and (root / relative).is_file(), ref


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--public-root", type=Path,
                        help="optional formal PUBLIC root to read back without modifying it")
    args = parser.parse_args()
    delivery = args.delivery.resolve()
    work = args.work_root.resolve()
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    work.mkdir(parents=True, exist_ok=True)
    root_guide = delivery / "index.html"
    hashes = delivery / "SHA256SUMS.txt"
    manifest_file = delivery / "delivery-manifest.json"
    assert root_guide.is_file() and hashes.is_file() and manifest_file.is_file()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    version = manifest["version"]
    assert isinstance(version, str) and version
    archive = delivery / f"creative-second-brain-{version}.zip"
    assert archive.is_file()
    expected_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert expected_hash in hashes.read_text(encoding="utf-8")
    assert manifest["status"] == "candidate local delivery; not a formal release"
    assert manifest["archive_sha256"] == expected_hash
    assert manifest["files"][archive.name] == expected_hash

    extraction = work / "safe-extraction"
    run(str(SOURCE_ROOT / "scripts" / "verify_delivery.py"), "--archive", str(archive),
        "--extract-to", str(extraction), cwd=work)
    package = extraction / "creative-second-brain"
    run(str(package / "scripts" / "verify_package.py"), "--package", str(package), cwd=work)
    install_prompt = (package / "install-prompt.txt").read_text(encoding="utf-8")
    for key in PROMPTS:
        assert "$brain-" + key in install_prompt, key
    for required_instruction in ("一次問齊", "Python", "Obsidian", "命令列", "完整技能入口", "不發布", "Node", "Playwright", "case_reproduction_capture.js"):
        assert required_instruction in install_prompt, required_instruction
    for browser_asset in ("case_reproduction_capture.js", "package.json", "package-lock.json"):
        assert (package / "runtime" / "workspace" / "tools" / browser_asset).is_file(), browser_asset

    guide = BeautifulSoup(root_guide.read_text(encoding="utf-8"), "html.parser")
    assert guide.select_one('#install-command') is not None
    assert guide.select_one(f'a[href="{archive.name}"]') is not None
    assert guide.select_one('body[data-package-root="creative-second-brain/"]') is not None
    guide_text = guide.get_text(" ", strip=True)
    package_guide = BeautifulSoup((package / "index.html").read_text(encoding="utf-8"), "html.parser")
    package_guide_text = package_guide.get_text(" ", strip=True)
    assert_prompts(guide, "daily-")
    assert_prompts(package_guide, "daily-")
    assert guide.select_one('#obsidian-cli-example[src="creative-second-brain/assets/setup/obsidian-cli-1.13.7-zh-tw.png"]')
    assert package_guide.select_one('#obsidian-cli-example[src="assets/setup/obsidian-cli-1.13.7-zh-tw.png"]')
    for page, logo_path in ((guide, "creative-second-brain/runtime/workspace/assets/brand/creative-second-brain-nebula-logo-v4.png"),
                            (package_guide, "runtime/workspace/assets/brand/creative-second-brain-nebula-logo-v4.png")):
        logo = page.select_one("a.brand img")
        assert logo is not None and logo.get("src") == logo_path
    logo = package / "runtime" / "workspace" / "assets" / "brand" / "creative-second-brain-nebula-logo-v4.png"
    assert hashlib.sha256(logo.read_bytes()).hexdigest() == "961ea9c3a7dc7af9c3311a100995a7ad2aaa060232908e2195b4db27c60abeb9"
    assert "設定 → 關於 → 進階" in guide_text and "設定 → 關於 → 進階" in package_guide_text
    graph_experience = "核心主題、取捨、偏好線索與應用場景會用不同顏色閱讀"
    assert graph_experience in guide_text and graph_experience in package_guide_text
    assert "即使首份材料暫未有可連線的關係，概念仍會顯示" in guide_text
    assert "即使首份材料暫未有可連線的關係，概念仍會顯示" in package_guide_text
    for guide_page in (guide, package_guide):
        assert guide_page.select_one('#limits') is None
        assert guide_page.select_one('a[href="#limits"]') is None
        assert guide_page.select_one('#sources h2').get_text(strip=True) == "06 / 遇到問題怎麼辦"
        footer = guide_page.select_one('footer')
        assert footer is not None and footer.get_text(" ", strip=True) == "創造型第二大腦 | Adam Chan"
    # A public guide describes the package in hand, never a temporary
    # candidate or an internal delivery workflow.
    for forbidden in ("候選", "OPS", "packet", "命令表", "工程規格", "SKILL.md", "references/first-use"):
        assert forbidden not in guide_text, forbidden
        assert forbidden not in package_guide_text, forbidden
    assert_local_resources(guide, root_guide.read_text(encoding="utf-8"), delivery)

    # A formal PUBLIC root page is a generated view of one delivery, never a
    # separately hand-maintained guide.  Assemble a temporary root so every
    # local link can be read back without touching the real PUBLIC workspace.
    public_root = work / "public-root"
    release_root = public_root / "releases" / version
    shutil.copytree(delivery, release_root)
    public_index = public_root / "index.html"
    run(str(SOURCE_ROOT / "scripts" / "build_public_index.py"),
        "--release", str(release_root), "--output", str(public_index), cwd=work)
    public_guide = BeautifulSoup(public_index.read_text(encoding="utf-8"), "html.parser")
    assert public_guide.select_one(f'body[data-package-root="releases/{version}/creative-second-brain/"]')
    assert public_guide.select_one(f'a[href="releases/{version}/creative-second-brain-{version}.zip"]')
    assert public_guide.select_one(f'#obsidian-cli-example[src="releases/{version}/creative-second-brain/assets/setup/obsidian-cli-1.13.7-zh-tw.png"]')
    assert_local_resources(public_guide, public_index.read_text(encoding="utf-8"), public_root)
    public_text = public_guide.get_text(" ", strip=True)
    assert "候選" not in public_text and "C:\\Users\\adam" not in public_text

    # Rebuilding the same verified release is a byte-for-byte no-op.  A
    # different page claiming the same release, or an older source attempting
    # to replace a newer root page, must fail without changing the entry.
    public_before = public_index.read_bytes()
    repeated = json.loads(run(str(SOURCE_ROOT / "scripts" / "build_public_index.py"),
                              "--release", str(release_root), "--output", str(public_index), cwd=work))
    assert repeated["status"] == "unchanged" and public_index.read_bytes() == public_before
    same_version_different = public_before.replace("創造型第二大腦".encode(), "創造型第二大腦（不同內容）".encode(), 1)
    public_index.write_bytes(same_version_different)
    run(str(SOURCE_ROOT / "scripts" / "build_public_index.py"),
        "--release", str(release_root), "--output", str(public_index), "--replace-existing", cwd=work, expected=2)
    assert public_index.read_bytes() == same_version_different
    newer_version = "9.0.0"
    simulated_newer = public_before.replace(f"releases/{version}/".encode(), f"releases/{newer_version}/".encode())
    simulated_newer = simulated_newer.replace(f"creative-second-brain-{version}.zip".encode(), f"creative-second-brain-{newer_version}.zip".encode())
    public_index.write_bytes(simulated_newer)
    run(str(SOURCE_ROOT / "scripts" / "build_public_index.py"),
        "--release", str(release_root), "--output", str(public_index), "--replace-existing", cwd=work, expected=2)
    assert public_index.read_bytes() == simulated_newer
    public_index.write_bytes(public_before)

    # A later verified release must be able to replace only an earlier
    # generated root guide; arbitrary existing HTML remains protected.
    old_version = "1.5.0"
    simulated_old = public_index.read_text(encoding="utf-8")
    simulated_old = simulated_old.replace(f"releases/{version}/", f"releases/{old_version}/")
    simulated_old = simulated_old.replace(f"creative-second-brain-{version}.zip", f"creative-second-brain-{old_version}.zip")
    public_index.write_text(simulated_old, encoding="utf-8")
    run(str(SOURCE_ROOT / "scripts" / "build_public_index.py"),
        "--release", str(release_root), "--output", str(public_index), "--replace-existing", cwd=work)
    refreshed_public = BeautifulSoup(public_index.read_text(encoding="utf-8"), "html.parser")
    assert refreshed_public.select_one(f'body[data-package-root="releases/{version}/creative-second-brain/"]')
    protected_root = work / "protected-root" / "index.html"
    protected_root.parent.mkdir(parents=True)
    protected_root.write_text("<html><body>not a generated guide</body></html>", encoding="utf-8")
    run(str(SOURCE_ROOT / "scripts" / "build_public_index.py"),
        "--release", str(release_root), "--output", str(protected_root), "--replace-existing", cwd=work, expected=2)
    assert protected_root.read_text(encoding="utf-8") == "<html><body>not a generated guide</body></html>"

    if args.public_root:
        formal_root = args.public_root.resolve()
        formal_index = formal_root / "index.html"
        formal_guide = BeautifulSoup(formal_index.read_text(encoding="utf-8"), "html.parser")
        assert formal_guide.select_one(f'body[data-package-root="releases/{version}/creative-second-brain/"]')
        assert formal_guide.select_one(f'a[href="releases/{version}/creative-second-brain-{version}.zip"]')
        assert formal_guide.select_one(f'#obsidian-cli-example[src="releases/{version}/creative-second-brain/assets/setup/obsidian-cli-1.13.7-zh-tw.png"]')
        assert_local_resources(formal_guide, formal_index.read_text(encoding="utf-8"), formal_root)
        assert "backup/" not in formal_index.read_text(encoding="utf-8")

    workspace = work / "my-brain"
    setup = package / "scripts" / "setup_workspace.py"
    run(str(setup), "--profile", "public-portable", "--workspace", str(workspace),
        "--vault", "My Notes", "--display-name", "New User", cwd=work)
    brain = workspace / "tools" / "brain.py"
    doctor = json.loads(run(str(brain), "doctor", cwd=workspace))
    assert doctor["status"] == "ready" and doctor["vault"] == "My Notes"
    graph_settings = json.loads((workspace / "My Notes" / ".obsidian" / "graph.json").read_text(encoding="utf-8"))
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
    sys.path.insert(0, str(workspace))
    from tools.obsidian_vault_adapter import frontmatter_fields, project_concept_graph_for_sources
    graph_kp_dir = workspace / "My Notes" / "knowledge-points" / "by-source" / "DELIVERY-GRAPH"
    graph_kp_dir.mkdir(parents=True)
    for number, concept in enumerate(("資料保存 vs 立即行動的取捨", "如何把知識變成可用的工作流程"), start=1):
        (graph_kp_dir / f"KP-DELIVERY-GRAPH-{number}.md").write_text(
            "---\n"
            "type: knowledge_point\n"
            + f"knowledge_point: {json.dumps(concept, ensure_ascii=False)}\n"
            + f"kp_uid: DELIVERY-GRAPH:KP-{number}\n"
            "---\n\n正式交付測試用知識點。\n",
            encoding="utf-8",
        )
    (workspace / "My Notes" / "_registry" / "relations.jsonl").write_text(
        json.dumps({
            "relation_id": "REL-DELIVERY-GRAPH-1",
            "source_kp_uid": "DELIVERY-GRAPH:KP-1",
            "target_kp_uid": "DELIVERY-GRAPH:KP-2",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    graph_projection = project_concept_graph_for_sources(workspace / "My Notes", {"DELIVERY-GRAPH"})
    assert graph_projection["status"] == "ready"
    assert graph_projection["verification"]["passed"] is True
    graph_concepts = [frontmatter_fields(path) for path in (workspace / "My Notes" / "concepts").glob("*.md")]
    assert {str(fields.get("graph_group")) for fields in graph_concepts} == {"tension", "application_scene"}
    compiler_source = (workspace / "tools" / "compiler_worker_v2.py").read_text(encoding="utf-8")
    assert "project_concept_graph_for_sources(vault, {str(source[\"source_id\"])})" in compiler_source
    refresh = json.loads(run(str(brain), "guide", cwd=work))
    assert refresh["status"] == "home_refreshed" and refresh["writes"] == 1
    workspace_home = BeautifulSoup((workspace / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert_prompts(workspace_home, "public-command-")
    guide_skill = (workspace / ".agents" / "skills" / "brain-guide" / "SKILL.md").read_text(encoding="utf-8")
    research_skill = (workspace / ".agents" / "skills" / "brain-research" / "SKILL.md").read_text(encoding="utf-8")
    assert "tools/brain.py guide" in guide_skill and "<installed-skill>" not in guide_skill and "docs/研究主題操作.md" in research_skill
    for key, expected in PROMPTS.items():
        skill = workspace / ".agents" / "skills" / ("brain-" + key) / "agents" / "openai.yaml"
        assert skill.is_file() and json.dumps(expected, ensure_ascii=False) in skill.read_text(encoding="utf-8"), key
    (workspace / "inputs" / "source.md").write_text("# Complete source\n\nPortable verification text.", encoding="utf-8")
    order = run(str(brain), "text", "start", "--text-file", "inputs/source.md", "--source-id", "DELIVERY-1", cwd=workspace)
    assert "C:\\" not in order and "D:\\" not in order
    assert json.loads(order)["source_id"] == "DELIVERY-1"
    graph = run(str(brain), "graph", "--write", "--check", "--json", cwd=workspace)
    assert json.loads(graph)["status"] == "ready"
    assert "Adam" not in graph and "KS-VS-" not in graph
    map_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in (workspace / "My Notes" / "maps").rglob("*.md"))
    assert "品味網入口" not in map_text and "taste-intake" not in map_text

    research_integrity = json.loads(run(
        str(SOURCE_ROOT / "tests" / "test_research_integrity.py"), "--workspace", str(workspace), cwd=work
    ))
    assert research_integrity["status"] == "passed"

    print(json.dumps({"status": "passed", "coverage": [
        "root guide links", "generated PUBLIC root links", "formal PUBLIC root readback", "ZIP top-level boundary", "SHA-256", "fresh extraction",
        "package verification", "zero-to-ready installer prompt", "14 prompt fields in root/package/home", "Obsidian setup image and three steps", "guide section and footer content",
        "generated skill metadata", "new named workspace", "guide refresh route", "research workflow route",
        "text work order", "portable graph preset", "completed-source graph projection", "empty-safe graph",
        "fresh-ZIP custom-vault research integrity root-fix counterexamples",
    ], "not_proven": ["host skill discovery", "native Obsidian", "second physical computer"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
