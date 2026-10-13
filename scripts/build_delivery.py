"""Build one versioned local delivery folder from the public product source.

This is deliberately a local packaging step.  It never copies the formal
PUBLIC workspace, publishes anything, or writes outside its new output folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

from package_paths import assert_plain_path
from delivery_contract import BOOTSTRAP_FILES, EMBEDDED_MANIFEST, EMBEDDED_SUMS, PACKAGE_NAME, SCHEMA, sha256

ROOT = Path(__file__).resolve().parents[1]


def product_version() -> str:
    value = json.loads((ROOT / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(value, str) or not value:
        raise ValueError("product version is missing")
    return value


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_new_output(raw: Path) -> Path:
    assert_plain_path(ROOT)
    assert_plain_path(raw)
    output = raw.absolute().resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output):
        raise ValueError("delivery output must not overlap the product source")
    if output.exists():
        raise ValueError("delivery output must be a new directory")
    return output


def root_guide(package: Path, output: Path, archive_name: str) -> None:
    """Create the single novice entry page, pointing only at local contents."""
    guide = (package / "index.html").read_text(encoding="utf-8")
    required_replacements = {
        'src="runtime/': f'src="{PACKAGE_NAME}/runtime/',
        'href="install-prompt.txt"': f'href="{PACKAGE_NAME}/install-prompt.txt"',
        'src="assets/setup/': f'src="{PACKAGE_NAME}/assets/setup/',
        'href="assets/setup/': f'href="{PACKAGE_NAME}/assets/setup/',
        'url("runtime/': f'url("{PACKAGE_NAME}/runtime/',
    }
    optional_replacements = {
        'href="references/': f'href="{PACKAGE_NAME}/references/',
        'href="SKILL.md"': f'href="{PACKAGE_NAME}/SKILL.md"',
        'href="runtime/': f'href="{PACKAGE_NAME}/runtime/',
        'href="spec/': f'href="{PACKAGE_NAME}/spec/',
    }
    for old, new in required_replacements.items():
        if old not in guide:
            raise ValueError("delivery guide rewrite target missing: " + old)
        guide = guide.replace(old, new)
    for old, new in optional_replacements.items():
        if old in guide:
            guide = guide.replace(old, new)
    body = re.search(r"<body\b([^>]*)>", guide, flags=re.IGNORECASE)
    if body is None:
        raise ValueError("delivery guide body marker is missing")
    if "data-package-root=" not in body.group(1):
        tag = f'<body{body.group(1)} data-package-root="{PACKAGE_NAME}/">'
        guide = guide[:body.start()] + tag + guide[body.end():]
    if 'data-package-root="' not in guide:
        raise ValueError("delivery guide body marker is missing")
    note = (
        '<p class="callout"><strong>完整安裝包：</strong>'
        f'<a href="{archive_name}">下載 ZIP</a>，或把此頁旁的 '
        f'<code>{PACKAGE_NAME}</code> 資料夾連同本頁交給 Agent。'
        ' 安裝前可查看 <a href="SHA256SUMS.txt">SHA-256 核對碼</a>。</p>'
    )
    if "</header>" not in guide:
        raise ValueError("delivery guide header is missing")
    # Keep the product message first on every viewport; the download callout
    # belongs immediately after the hero, not ahead of it on small screens.
    guide = guide.replace("</header>", "</header>" + note, 1)
    (output / "index.html").write_text(guide, encoding="utf-8", newline="\n")


def zip_package(package: Path, output: Path, archive_name: str) -> Path:
    archive = output / archive_name
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(package.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}:
                handle.write(path, path.relative_to(package.parent).as_posix())
        for name in (EMBEDDED_MANIFEST, EMBEDDED_SUMS):
            handle.write(output / name, name)
        for name in sorted(BOOTSTRAP_FILES):
            source = ROOT / "scripts" / ("delivery_bootstrap.py" if name == "__main__.py" else name)
            handle.write(source, name)
    return archive


def embedded_identity(package: Path, version: str) -> None:
    """Add ZIP-verifiable identity without creating a self-hash cycle."""
    profile_build = package / "profile-build.json"
    identity = {
        "schema": SCHEMA,
        "package": PACKAGE_NAME,
        "version": version,
        "profile_build_sha256": sha256(profile_build),
        "archive_integrity": "external_sha256_only",
        "bootstrap_files": {
            name: sha256(ROOT / "scripts" / ("delivery_bootstrap.py" if name == "__main__.py" else name))
            for name in sorted(BOOTSTRAP_FILES)
        },
        "note": "A standalone ZIP verifies its package manifest; final archive bytes are verified when the sibling delivery manifest is available.",
    }
    (package.parent / EMBEDDED_MANIFEST).write_text(
        json.dumps(identity, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    (package.parent / EMBEDDED_SUMS).write_text(
        f"SHA256 ({PACKAGE_NAME}/profile-build.json) = {identity['profile_build_sha256']}\n",
        encoding="utf-8", newline="\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--staging-root", required=True, type=Path,
                        help="new temporary build folders are created here")
    parser.add_argument("--formal", action="store_true",
                        help="mark this new local output as a formal local delivery; never publishes it")
    args = parser.parse_args()
    output = safe_new_output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    assert_plain_path(output.parent)
    staging_root = args.staging_root.absolute()
    assert_plain_path(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = staging_root / ("creative-second-brain-delivery-" + uuid4().hex)
    assert_plain_path(stage)
    stage.mkdir()
    try:
        staged = stage / "profiles"
        command = [sys.executable, "-B", str(ROOT / "scripts" / "build_profiles.py"),
                   "--output", str(staged), "--profile", "public-portable"]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        if result.returncode:
            raise RuntimeError("profile build failed: " + result.stderr.strip())
        source_package = staged / "public-portable"
        if not source_package.is_dir():
            raise RuntimeError("profile build did not create the portable package")
        output.mkdir()
        package = output / PACKAGE_NAME
        shutil.copytree(source_package, package)
        version = product_version()
        archive_name = f"{PACKAGE_NAME}-{version}.zip"
        root_guide(package, output, archive_name)
        embedded_identity(package, version)
        archive = zip_package(package, output, archive_name)
        hashes = f"SHA256 ({archive_name}) = {digest(archive)}\n"
        (output / "SHA256SUMS.txt").write_text(hashes, encoding="utf-8", newline="\n")
        manifest = {
            "schema": "creative_second_brain_local_delivery_v2",
            "version": version,
            "package": PACKAGE_NAME,
            "archive": archive_name,
            "status": "local formal delivery; not published" if args.formal else "candidate local delivery; not a formal release",
            "archive_sha256": digest(archive),
            "files": {
                "index.html": digest(output / "index.html"),
                archive_name: digest(archive),
                "SHA256SUMS.txt": digest(output / "SHA256SUMS.txt"),
            },
        }
        (output / "delivery-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    finally:
        # This exact, unique folder is created by this command only.  A failed
        # clean-up must not remove or alter the independently built delivery.
        if stage.exists():
            shutil.rmtree(stage)
    print(json.dumps({"status": "built", "delivery": output.name, "version": product_version(), "package": PACKAGE_NAME,
                      "archive": f"{PACKAGE_NAME}-{product_version()}.zip", "published": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
