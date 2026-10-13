"""Focused OPS home projection check for preserved daily-home adaptations."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    brain = workspace / "tools" / "brain.py"
    result = subprocess.run(
        [sys.executable, "-B", str(brain), "guide"], cwd=workspace,
        text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=40,
    )
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    payload = json.loads(result.stdout)
    assert payload["status"] == "home_refreshed"
    home = (workspace / "index.html").read_text(encoding="utf-8")
    assert 'id="p-think"' in home and "$brain-think" in home
    assert 'id="p-nebula"' in home and "$brain-nebula" in home
    assert "開啟第二大腦首頁" in home
    assert home.count("創造型第二大腦 | Adam Chan") == 1
    print(json.dumps({"status": "passed", "checks": [
        "OPS think command", "OPS nebula command", "guide homepage wording", "single product footer",
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
