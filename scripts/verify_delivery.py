"""Verify a portable delivery before any skills or workspace files are written."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from delivery_contract import inspect_archive, safe_extract_archive, verify_delivery_folder, verify_extracted_package, verify_known_archive_context, verify_known_delivery_context


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--delivery", type=Path, help="delivery folder containing ZIP and package")
    group.add_argument("--archive", type=Path, help="standalone delivery ZIP")
    parser.add_argument("--extract-to", type=Path, help="new temporary directory for safe standalone extraction")
    parser.add_argument("--package", type=Path, help="already safely extracted package")
    args = parser.parse_args()
    try:
        if args.delivery:
            if args.extract_to or args.package:
                raise ValueError("--delivery does not accept extraction arguments")
            result = verify_delivery_folder(args.delivery)
        elif args.extract_to:
            identity, package = safe_extract_archive(args.archive, args.extract_to)
            result = {"version": identity["version"], "archive": args.archive.name, "package": str(package)}
        else:
            if args.package is None:
                raise ValueError("standalone ZIP requires --extract-to or --package")
            verify_known_delivery_context(args.package)
            verify_known_archive_context(args.archive)
            identity, _ = inspect_archive(args.archive)
            manifest = verify_extracted_package(args.package, identity)
            result = {"version": identity["version"], "archive": args.archive.name, "files": len(manifest["files"])}
        print(json.dumps({"status": "verified", **result}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
