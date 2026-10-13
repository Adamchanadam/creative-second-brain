"""ZIP entry point: safely extract a standalone delivery before installation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from delivery_contract import safe_extract_archive


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extract-to", required=True, type=Path,
                        help="a new temporary directory; no skills or workspace are written")
    args = parser.parse_args()
    try:
        identity, package = safe_extract_archive(Path(sys.argv[0]), args.extract_to)
        print(json.dumps({"status": "verified_and_extracted", "version": identity["version"],
                          "package": str(package), "writes": "temporary extraction only"}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
