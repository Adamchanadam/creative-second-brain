"""Install a complete verified skill without replacing an existing skill or data."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from package_paths import assert_plain_path
from verify_package import verify
from delivery_contract import inspect_archive, verify_delivery_folder, verify_extracted_package, verify_known_archive_context, verify_known_delivery_context


def install(package: Path, skills_root: Path, apply: bool = False, delivery: Path | None = None, archive: Path | None = None) -> dict:
    package = package.absolute()
    target = skills_root.absolute() / "creative-second-brain"
    assert_plain_path(package)
    assert_plain_path(target)
    package = package.resolve()
    target = target.resolve()
    if delivery is not None:
        verified_delivery = verify_delivery_folder(delivery)
        if (delivery.absolute() / "creative-second-brain").resolve() != package:
            raise ValueError("delivery package location does not match requested package")
    elif archive is not None:
        known_context = verify_known_delivery_context(package)
        archive_context = verify_known_archive_context(archive)
        identity, _ = inspect_archive(archive)
        verify_extracted_package(package, identity)
        verified_delivery = {"archive": archive.name, "version": identity["version"], "standalone": True,
                             **(known_context or {}), **(archive_context or {})}
    else:
        verified_delivery = verify_known_delivery_context(package)
    manifest = verify(package)
    result = {"status": "planned", "skill": str(target), "writes": 0,
              "runtime_discovery": "not_verified", "workspace_created": False,
              **({"delivery": verified_delivery} if verified_delivery else {})}
    # A package already installed at its final target is the one overlap that
    # is safe to read back.  This lets an interrupted installer be retried
    # idempotently without treating the source package as an error.
    if target == package:
        result["status"] = "already_installed"
        return result
    if target.is_relative_to(package) or package.is_relative_to(target):
        raise ValueError("source and installation target must not overlap")
    if target.exists():
        if verify(target) != manifest:
            raise ValueError("existing skill differs; preserved without upgrade")
        result["status"] = "already_installed"
        return result
    if not apply:
        return result
    target.mkdir(parents=True, exist_ok=False)
    # Exclusive creates also leave interrupted installs visible; never erase or
    # repair a partial installation without a separate recovery decision.
    for relative in [*manifest["files"], "profile-build.json"]:
        source = package / relative
        destination = target / relative
        assert_plain_path(source)
        assert_plain_path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as handle:
            handle.write(source.read_bytes())
    if verify(target) != manifest:
        raise ValueError("installed package verification failed; partial target preserved")
    result.update(status="installed", writes=len(manifest["files"]) + 1)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", type=Path, default=Path.home() / ".agents" / "skills")
    parser.add_argument("--delivery", type=Path, help="verified delivery folder containing this package")
    parser.add_argument("--archive", type=Path, help="standalone ZIP already safely extracted to this package")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = install(Path(__file__).resolve().parents[1], args.skills_root, args.apply, args.delivery, args.archive)
        result["scope"] = "default_user" if args.skills_root == Path.home() / ".agents" / "skills" else "explicit_custom"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
