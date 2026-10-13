"""Focused fail-closed checks for optional sibling delivery proof files."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(python: Path, *args: str, cwd: Path, expected: int = 0) -> dict:
    result = subprocess.run([str(python), "-B", *args], cwd=cwd, text=True,
                            encoding="utf-8", errors="replace", capture_output=True)
    if result.returncode != expected:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    args = parser.parse_args()
    delivery = args.delivery.absolute()
    work = args.work_root.absolute()
    if work.exists():
        raise SystemExit("work root must be new")
    work.mkdir(parents=True)
    package = delivery / "creative-second-brain"
    archive_name = json.loads((delivery / "delivery-manifest.json").read_text(encoding="utf-8"))["archive"]
    archive = delivery / archive_name
    python = Path(sys.executable)

    # Baseline package and its complete external proof remain valid.
    assert run(python, str(package / "scripts" / "verify_delivery.py"), "--delivery", str(delivery), cwd=work)["status"] == "verified"

    # A lone checksum is not an optional hint: it is an incomplete proof and
    # must prevent temporary extraction as well as any later installer write.
    sums_only = work / "sums-only"
    shutil.copytree(delivery, sums_only)
    (sums_only / "delivery-manifest.json").unlink()
    (sums_only / "SHA256SUMS.txt").write_text("SHA256 (wrong.zip) = " + "0" * 64 + "\n", encoding="utf-8")
    sums_target = work / "sums-only-extract"
    assert run(python, str(sums_only / archive_name), "--extract-to", str(sums_target), cwd=work, expected=2)["status"] == "blocked"
    assert not sums_target.exists()

    # Outer metadata can be self-consistent while disagreeing with the ZIP's
    # immutable identity; this is equally unsafe before extraction.
    mismatch = work / "version-mismatch"
    shutil.copytree(delivery, mismatch)
    renamed = mismatch / "creative-second-brain-0.0.0.zip"
    (mismatch / archive_name).rename(renamed)
    marker = json.loads((mismatch / "delivery-manifest.json").read_text(encoding="utf-8"))
    marker.update({"version": "0.0.0", "archive": renamed.name, "archive_sha256": hashlib.sha256(renamed.read_bytes()).hexdigest()})
    (mismatch / "delivery-manifest.json").write_text(json.dumps(marker), encoding="utf-8")
    (mismatch / "SHA256SUMS.txt").write_text(f"SHA256 ({renamed.name}) = {marker['archive_sha256']}\n", encoding="utf-8")
    mismatch_target = work / "version-mismatch-extract"
    assert run(python, str(renamed), "--extract-to", str(mismatch_target), cwd=work, expected=2)["status"] == "blocked"
    assert not mismatch_target.exists()

    assert "node node_modules/playwright/cli.js install chromium" in (ROOT / "references" / "first-use.md").read_text(encoding="utf-8")
    print(json.dumps({"status": "passed", "coverage": ["complete external proof", "orphan checksum blocks", "outer and embedded version disagreement blocks"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
