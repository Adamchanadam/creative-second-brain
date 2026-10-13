"""Check candidate versus explicit local-formal delivery labelling only."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path, staging: Path, *, formal: bool) -> dict:
    command = [sys.executable, "-B", str(ROOT / "scripts" / "build_delivery.py"),
               "--output", str(output), "--staging-root", str(staging)]
    if formal:
        command.append("--formal")
    result = subprocess.run(command, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr + result.stdout
    return json.loads((output / "delivery-manifest.json").read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-root", required=True, type=Path)
    args = parser.parse_args()
    if not args.temp_root.is_dir():
        raise ValueError("--temp-root must be an existing test-owned directory")
    with tempfile.TemporaryDirectory(prefix="delivery-status-", dir=args.temp_root) as temporary:
        root = Path(temporary)
        candidate = build(root / "candidate", root / "candidate-stage", formal=False)
        formal = build(root / "formal", root / "formal-stage", formal=True)
        assert candidate["status"] == "candidate local delivery; not a formal release"
        assert formal["status"] == "local formal delivery; not published"
        assert candidate["version"] == formal["version"] and candidate["package"] == formal["package"]
    print(json.dumps({"status": "passed", "coverage": ["candidate default", "explicit local formal", "not published"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
