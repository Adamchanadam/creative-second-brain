"""Isolated installer regressions. Artifacts are retained in a new task-owned directory."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def digest_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def blocked(call):
    try:
        call()
    except (ValueError, OSError):
        return
    raise AssertionError("unsafe request was accepted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--package", type=Path)
    args = parser.parse_args()
    work = args.work_root.absolute()
    work.mkdir(parents=True, exist_ok=False)
    if args.package:
        package = args.package.resolve()
    else:
        build = work / "build"
        subprocess.run([sys.executable, "-B", str(ROOT / "scripts/build_profiles.py"), "--output", str(build)], check=True)
        package = build / "public-portable"
    sys.path.insert(0, str(package / "scripts"))
    # brain.py normally lives beside these dependencies after workspace setup.
    # This package-level test must use that same package's runtime, not the host.
    sys.path.insert(0, str(package / "runtime" / "workspace" / "tools"))
    from brain import inside
    from install_skill import install
    from setup_workspace import plain_child
    from verify_package import verify
    for unsafe_path in ("/outside", "\\\\server\\share", "C:/outside", "../outside"):
        blocked(lambda unsafe_path=unsafe_path: plain_child(work, unsafe_path))
        blocked(lambda unsafe_path=unsafe_path: inside(unsafe_path))
    manifest = verify(package)
    for browser_asset in ("case_reproduction_capture.js", "package.json", "package-lock.json"):
        assert (package / "runtime" / "workspace" / "tools" / browser_asset).is_file(), browser_asset
    skills = work / "user skills"
    assert install(package, skills)["writes"] == 0 and not skills.exists()
    installed = install(package, skills, True)
    target = Path(installed["skill"])
    assert installed["status"] == "installed" and verify(target) == manifest
    before = digest_tree(target)
    assert install(package, skills, True)["status"] == "already_installed"
    assert digest_tree(target) == before
    assert install(target, target.parent, True)["status"] == "already_installed"
    assert digest_tree(target) == before
    (target / "user-note.txt").write_text("preserve me", encoding="utf-8")
    conflict = digest_tree(target)
    blocked(lambda: install(package, skills, True))
    assert digest_tree(target) == conflict
    blocked(lambda: install(package, package / "nested", True))
    assert not (package / "nested").exists()
    incomplete = work / "partial" / "creative-second-brain"
    incomplete.mkdir(parents=True)
    (incomplete / "keep.txt").write_text("interrupted install", encoding="utf-8")
    blocked(lambda: install(package, incomplete.parent, True))
    assert (incomplete / "keep.txt").read_text() == "interrupted install"
    for entry in ("../outside.txt", "C:/outside.txt", "/outside.txt", "runtime/../outside.txt", "x:stream", "x\\y"):
        bad = work / ("unsafe-" + str(len(list(work.glob("unsafe-*")))))
        shutil.copytree(package, bad)
        data = json.loads((bad / "profile-build.json").read_text(encoding="utf-8"))
        data["files"][entry] = "0" * 64
        (bad / "profile-build.json").write_text(json.dumps(data), encoding="utf-8")
        blocked(lambda: verify(bad))
    changed = work / "changed"
    shutil.copytree(package, changed)
    (changed / "SKILL.md").write_text("tampered", encoding="utf-8")
    blocked(lambda: install(changed, work / "must-not-exist", True))
    assert not (work / "must-not-exist").exists()
    cropped = work / "cropped"
    shutil.copytree(package, cropped)
    omitted = "runtime/workspace/tools/ask_second_brain.py"
    (cropped / omitted).unlink()
    data = json.loads((cropped / "profile-build.json").read_text(encoding="utf-8"))
    del data["files"][omitted]
    (cropped / "profile-build.json").write_text(json.dumps(data), encoding="utf-8")
    blocked(lambda: install(cropped, work / "cropped-target", True))
    assert not (work / "cropped-target").exists()
    # Even if an attacker rewrites both provenance and the package manifest,
    # the canonical inventory allow-list rejects a private runtime subtree.
    private = work / "rebuilt-private"
    shutil.copytree(package, private)
    payload = private / "runtime" / "workspace" / "outputs" / "private.txt"
    payload.parent.mkdir(parents=True, exist_ok=True); payload.write_text("private", encoding="utf-8")
    provenance_path = private / "runtime" / "snapshot-provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["runtime_files"].append("workspace/outputs/private.txt")
    provenance["source_hashes"]["workspace/outputs/private.txt"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    provenance_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rebuilt = json.loads((private / "profile-build.json").read_text(encoding="utf-8"))
    rebuilt["files"]["runtime/workspace/outputs/private.txt"] = hashlib.sha256(payload.read_bytes()).hexdigest()
    rebuilt["files"]["runtime/snapshot-provenance.json"] = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
    (private / "profile-build.json").write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    blocked(lambda: verify(private))
    with patch("install_skill.assert_plain_path", side_effect=ValueError("junction")):
        blocked(lambda: install(package, work / "linked", True))
    assert not (work / "linked").exists()
    from bs4 import BeautifulSoup
    soup = BeautifulSoup((package / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert soup.select_one("#install-command").text == (package / "install-prompt.txt").read_text(encoding="utf-8").strip()
    for instruction in ("Node", "Playwright", "case_reproduction_capture.js"):
        assert instruction in soup.select_one("#install-command").text, instruction
    # One installer prompt plus the fifteen current portable daily entries.
    assert len(soup.select("button.copy")) == 16
    assert "brain-reader" in soup.select_one("body").get("class", [])
    assert "03 / 15 個日常入口" in soup.get_text()
    for field in soup.select("textarea"):
        assert field.get("id") and field.get("name")
        assert field.get("aria-label") or soup.find("label", attrs={"for": field["id"]})
    for link in soup.select("a[href]"):
        href = link["href"]
        if href.startswith("#"):
            assert soup.find(id=href[1:]), href
        elif not href.startswith(("https://", "http://")):
            assert (package / href).is_file(), href
    report = {"status": "passed", "package_files": len(manifest["files"]),
              "coverage": ["dry-run zero writes", "complete install", "idempotent retry", "conflict preserved",
                           "partial preserved", "overlap blocked", "traversal blocked", "tamper blocked",
                           "cropped runtime blocked despite rebuilt manifest", "mocked linked target rejected", "browser-capture installer payload", "HTML prompt parity and local links"],
              "not_proven": ["host discovery", "native Obsidian", "second physical computer"]}
    (work / "result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
