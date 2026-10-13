"""Focused, empty-vault checks for the portable first-install contract.

This intentionally does not create sample knowledge or exercise the unrelated
reader/UI suite.  It tests the fail-closed boundary before any installer or
workspace write is permitted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from urllib.request import urlopen
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(python: Path, *args: str, cwd: Path, expected: int = 0, environment: dict | None = None) -> dict:
    result = subprocess.run([str(python), "-B", *args], cwd=cwd, text=True,
                            encoding="utf-8", errors="replace", capture_output=True,
                            env={**os.environ, **(environment or {})})
    if result.returncode != expected:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--venv-python", required=True, type=Path)
    args = parser.parse_args()
    delivery = args.delivery.absolute()
    work = args.work_root.absolute()
    python = args.venv_python.absolute()
    if work.exists():
        raise SystemExit("work root must be new")
    work.mkdir(parents=True)
    archive = delivery / json.loads((delivery / "delivery-manifest.json").read_text(encoding="utf-8"))["archive"]

    # Complete delivery proof and a ZIP-only copy each verify before writes.
    package = delivery / "creative-second-brain"
    verified = run(python, str(package / "scripts" / "verify_delivery.py"), "--delivery", str(delivery), cwd=work)
    assert verified["status"] == "verified"
    isolated_zip = work / "only-the-zip.zip"
    shutil.copyfile(archive, isolated_zip)
    extract = work / "isolated-extract"
    zip_verified = run(python, str(isolated_zip), "--extract-to", str(extract), cwd=work)
    assert zip_verified["status"] == "verified_and_extracted"
    extracted = extract / "creative-second-brain"
    assert extracted.is_dir() and not (extract / "index.html").exists()
    for path in extracted.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt", ".py", ".js", ".html", ".yaml"}:
            content = path.read_text(encoding="utf-8", errors="replace")
            assert "C:/Users/adam" not in content and "C:\\Users\\adam" not in content, path
    unsafe_zip = work / "unsafe-path.zip"
    shutil.copyfile(archive, unsafe_zip)
    with zipfile.ZipFile(unsafe_zip, "a") as bundle:
        bundle.writestr("../outside.txt", "must not extract")
    unsafe_extract = work / "unsafe-extract"
    assert run(python, str(unsafe_zip), "--extract-to", str(unsafe_extract), cwd=work, expected=2)["status"] == "blocked"
    assert not unsafe_extract.exists()
    unsafe_link = work / "unsafe-link.zip"
    shutil.copyfile(archive, unsafe_link)
    with zipfile.ZipFile(unsafe_link, "a") as bundle:
        link = zipfile.ZipInfo("creative-second-brain/linked-file")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "outside")
    assert run(python, str(unsafe_link), "--extract-to", str(work / "unsafe-link-extract"), cwd=work, expected=2)["status"] == "blocked"

    # A known but mismatched sibling proof stops before installation writes.
    bad = work / "known-bad-delivery"
    shutil.copytree(delivery, bad)
    bad_manifest = json.loads((bad / "delivery-manifest.json").read_text(encoding="utf-8"))
    bad_manifest["archive_sha256"] = "0" * 64
    (bad / "delivery-manifest.json").write_text(json.dumps(bad_manifest), encoding="utf-8")
    target = work / "must-not-install"
    blocked = run(python, str(bad / "creative-second-brain" / "scripts" / "install_skill.py"),
                  "--delivery", str(bad), "--skills-root", str(target), "--apply", cwd=work, expected=2)
    assert blocked["status"] == "blocked" and not target.exists()
    archive_target = work / "must-not-install-from-archive"
    archive_blocked = run(python, str(bad / "creative-second-brain" / "scripts" / "install_skill.py"),
                          "--archive", str(bad / archive.name), "--skills-root", str(archive_target), "--apply", cwd=work, expected=2)
    assert archive_blocked["status"] == "blocked" and not archive_target.exists()
    assert run(python, str(bad / archive.name), "--extract-to", str(work / "bad-bootstrap-extract"), cwd=work, expected=2)["status"] == "blocked"
    assert run(python, str(bad / "creative-second-brain" / "scripts" / "verify_delivery.py"),
               "--archive", str(bad / archive.name), "--package", str(bad / "creative-second-brain"), cwd=work, expected=2)["status"] == "blocked"

    # Any partial outer proof is suspicious: a standalone ZIP may have neither
    # file, but once one sibling proof exists both must validate before writes.
    sums_only = work / "known-sums-only"
    shutil.copytree(delivery, sums_only)
    (sums_only / "delivery-manifest.json").unlink()
    (sums_only / "SHA256SUMS.txt").write_text("SHA256 (wrong.zip) = " + "0" * 64 + "\n", encoding="utf-8")
    sums_extract = work / "sums-only-extract"
    assert run(python, str(sums_only / archive.name), "--extract-to", str(sums_extract), cwd=work, expected=2)["status"] == "blocked"
    assert not sums_extract.exists()

    # An outer version/name/hash may be self-consistent yet still not describe
    # the embedded identity.  That disagreement must block before extraction.
    renamed = work / "outer-version-mismatch"
    shutil.copytree(delivery, renamed)
    renamed_archive = renamed / "creative-second-brain-0.0.0.zip"
    (renamed / archive.name).rename(renamed_archive)
    renamed_manifest = json.loads((renamed / "delivery-manifest.json").read_text(encoding="utf-8"))
    renamed_manifest.update({"version": "0.0.0", "archive": renamed_archive.name})
    renamed_manifest["archive_sha256"] = hashlib.sha256(renamed_archive.read_bytes()).hexdigest()
    (renamed / "delivery-manifest.json").write_text(json.dumps(renamed_manifest), encoding="utf-8")
    (renamed / "SHA256SUMS.txt").write_text(
        f"SHA256 ({renamed_archive.name}) = {renamed_manifest['archive_sha256']}\n", encoding="utf-8"
    )
    renamed_extract = work / "outer-version-mismatch-extract"
    assert run(python, str(renamed_archive), "--extract-to", str(renamed_extract), cwd=work, expected=2)["status"] == "blocked"
    assert not renamed_extract.exists()
    bad_version = work / "bad-version-delivery"
    shutil.copytree(delivery, bad_version)
    version_manifest = json.loads((bad_version / "delivery-manifest.json").read_text(encoding="utf-8"))
    version_manifest["version"] = "0.0.0"
    (bad_version / "delivery-manifest.json").write_text(json.dumps(version_manifest), encoding="utf-8")
    assert run(python, str(package / "scripts" / "verify_delivery.py"), "--delivery", str(bad_version), cwd=work, expected=2)["status"] == "blocked"

    # Basic is real locked Python readiness; complete remains unavailable until
    # workspace-local locked Playwright can launch Chromium.
    environment = run(python, str(extracted / "scripts" / "check_environment.py"),
                      "--workspace", str(extracted / "runtime" / "workspace"), cwd=work)
    assert environment["status"] == "ready"
    assert environment["python_dependencies"]["status"] == "ready"
    assert environment["installation"]["basic"] == "ready"
    assert environment["installation"]["image_generation"] == "requires_current_host_schema"
    assert environment["installation"]["overall"] == "basic_limited"
    missing_site = subprocess.run([str(python), "-S", "-B", str(extracted / "scripts" / "check_environment.py"),
                                   "--workspace", str(extracted / "runtime" / "workspace")], cwd=work, text=True,
                                  encoding="utf-8", errors="replace", capture_output=True)
    assert missing_site.returncode == 2 and json.loads(missing_site.stdout)["status"] == "blocked"

    # An empty workspace is enough to prove the installer contract and actual
    # loopback routes; it must never manufacture example knowledge.
    workspace = work / "empty-workspace"
    initialized = run(python, str(extracted / "scripts" / "setup_workspace.py"), "--profile", "public-portable",
                      "--workspace", str(workspace), "--vault", "Empty Notes", "--display-name", "Empty User", cwd=work)
    assert initialized["status"] == "initialized"
    canonical = ["guide", "url", "text", "image", "think", "create", "grow", "research", "resume", "check", "fix", "tidy", "nebula", "update"]
    for name in canonical:
        assert (workspace / ".agents" / "skills" / ("brain-" + name) / "agents" / "openai.yaml").is_file(), name
    assert (workspace / ".agents" / "skills" / "brain-ask" / "agents" / "openai.yaml").is_file()
    brain = workspace / "tools" / "brain.py"
    routes_env = {"CREATIVE_SECOND_BRAIN_READER_IDLE_SECONDS": "15"}
    home = run(python, str(brain), "open-home", cwd=workspace, environment=routes_env)
    nebula = run(python, str(brain), "nebula", cwd=workspace, environment=routes_env)
    assert home["status"] == "ready" and home["url"].endswith("/index.html#prompts") and home["fragment_verified"] is True
    assert nebula["status"] == "ready" and nebula["url"].endswith("/Empty%20Notes/outcome-pages/nebula.html")
    assert b'id="prompts"' in urlopen(home["url"].split("#", 1)[0], timeout=10).read()
    assert b"knowledge-nebula" in urlopen(nebula["url"], timeout=10).read()

    registry = json.loads((extracted / "spec" / "command-registry.json").read_text(encoding="utf-8"))
    entries = registry["commands"]
    names = [name for name, entry in sorted(entries.items(), key=lambda item: item[1].get("menu_order", 999))
             if "menu_order" in entry and not entry.get("alias_for")]
    assert names == ["guide", "url", "text", "image", "think", "create", "grow", "research", "resume", "check", "fix", "tidy", "nebula", "update"]
    assert entries["ask"].get("alias_for") == "think"
    capture = (extracted / "runtime" / "workspace" / "tools" / "case_reproduction_capture.js").read_text(encoding="utf-8")
    assert "lockedPlaywrightForCheck" in capture and "workspace-locked-chromium" in capture
    assert "Microsoft\\\\Edge\\\\Application" not in capture
    assert "global_playwright" not in capture[capture.index("async function check()"):]
    report = {"status": "passed", "coverage": ["delivery and ZIP-only verification", "candidate contains no machine-local paths", "known external mismatch blocks install", "version mismatch blocks delivery", "real venv direct requirements", "basic versus complete status", "empty workspace 14 metadata", "actual home and nebula routes", "canonical registry", "workspace-local Chromium check"],
              "not_proven": ["host slash recognition", "native Obsidian", "ImageGen generation", "browser setup when Chromium was not installed"]}
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
