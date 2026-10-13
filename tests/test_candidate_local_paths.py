"""Packaging-only guard against machine-local paths in a portable candidate."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    args = parser.parse_args()
    delivery = args.delivery.absolute()
    package = delivery / "creative-second-brain"
    checked = subprocess.run([sys.executable, "-B", str(package / "scripts" / "verify_delivery.py"),
                              "--delivery", str(delivery)], text=True, encoding="utf-8", errors="replace", capture_output=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    # Detect actual absolute host paths, including JSON-escaped Windows paths,
    # without mistaking source-code string escaping or HTTP URLs for a path.
    absolute_local_path = re.compile(
        r"(?i)(?:\b[a-z]:[\\/]+|\\\\[a-z0-9._-]+[\\/]+[a-z0-9._$ -]+|/(?:home|users)/[^/\s\"']+)"
    )
    for path in package.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".md", ".txt", ".py", ".js", ".html", ".yaml"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            assert absolute_local_path.search(text) is None, path
    brand = package / "runtime" / "workspace" / "assets" / "brand"
    for name in ("gold-silver-stardust.provenance.json", "creative-second-brain-nebula-logo-v4.json"):
        metadata = json.loads((brand / name).read_text(encoding="utf-8"))
        assert metadata.get("mode") and metadata.get("sha256") and metadata.get("source", "").startswith("Original generator output is not included")
    print(json.dumps({"status": "passed", "coverage": ["candidate package verification", "no machine-local paths", "portable brand provenance"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
