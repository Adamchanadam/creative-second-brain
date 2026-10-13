"""Focused v2 release verification for the generated PUBLIC root guide."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*arguments: str, expected: int = 0) -> dict:
    result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "build_public_index.py"), *arguments],
                            text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == expected, result.stderr + result.stdout
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--temp-root", required=True, type=Path)
    args = parser.parse_args()
    if not args.temp_root.is_dir():
        raise ValueError("--temp-root must be an existing test-owned directory")
    version = json.loads((ROOT / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    with tempfile.TemporaryDirectory(prefix="public-index-", dir=args.temp_root) as temporary:
        work = Path(temporary)
        release = work / "release"; shutil.copytree(args.delivery, release)
        output = work / "index.html"
        built = run("--release", str(release), "--output", str(output))
        assert built["status"] == "built" and built["version"] == version
        assert f'data-package-root="releases/{version}/creative-second-brain/"' in output.read_text(encoding="utf-8")
        assert run("--release", str(release), "--output", str(output))["status"] == "unchanged"
        manifest = release / "delivery-manifest.json"
        data = json.loads(manifest.read_text(encoding="utf-8")); data["archive_sha256"] = "0" * 64
        manifest.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
        blocked_root = work / "bad-proof" / "index.html"
        blocked = run("--release", str(release), "--output", str(blocked_root), expected=2)
        assert blocked["status"] == "blocked" and "SHA-256" in blocked["reason"]
        assert not blocked_root.exists()
    print(json.dumps({"status": "passed", "coverage": ["v2 verified delivery root guide", "unchanged repeat", "tampered v2 archive proof rejected"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
