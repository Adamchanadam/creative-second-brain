"""Create the PUBLIC root entry page from one verified local release."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from package_paths import assert_plain_path
from delivery_contract import verify_delivery_folder


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "creative-second-brain"
DELIVERY_SCHEMAS = {
    "creative_second_brain_local_delivery_v1",
    "creative_second_brain_local_delivery_v2",
}
GENERATED_ROOT_PATTERN = re.compile(
    r'data-package-root="releases/([^/"<>]+)/creative-second-brain/"'
)
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def product_version() -> str:
    version = json.loads((ROOT / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    version_key(version)
    return version


def version_key(value: object) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("product version is invalid")
    return tuple(int(part) for part in match.groups())


def build(release: Path, output: Path, *, replace_existing: bool = False) -> dict[str, object]:
    release = release.absolute().resolve()
    output = output.absolute()
    assert_plain_path(release)
    assert_plain_path(output)
    if output.name != "index.html":
        raise ValueError("PUBLIC root entry must be named index.html")

    version = product_version()
    archive = f"{PACKAGE}-{version}.zip"
    manifest_path = release / "delivery-manifest.json"
    guide_path = release / "index.html"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") not in DELIVERY_SCHEMAS:
        raise ValueError("release manifest has an unsupported schema")
    if manifest.get("schema") == "creative_second_brain_local_delivery_v2":
        verified = verify_delivery_folder(release)
        if verified.get("version") != version or verified.get("archive") != archive:
            raise ValueError("verified release does not match this product version")
    if (manifest.get("version") != version
            or manifest.get("archive") != archive or manifest.get("files", {}).get("index.html") != digest(guide_path)
            or manifest.get("files", {}).get(archive) != digest(release / archive)):
        raise ValueError("release manifest does not match this product version")

    prefix = f"releases/{version}/"
    guide = guide_path.read_text(encoding="utf-8")
    replacements = {
        f"{PACKAGE}/": prefix + f"{PACKAGE}/",
        f'href="{archive}"': f'href="{prefix}{archive}"',
        'href="SHA256SUMS.txt"': f'href="{prefix}SHA256SUMS.txt"',
    }
    for old, new in replacements.items():
        if old not in guide:
            raise ValueError("release guide rewrite target is missing: " + old)
        guide = guide.replace(old, new)
    if (f'data-package-root="{prefix}{PACKAGE}/"' not in guide
            or f'href="{prefix}{archive}"' not in guide
            or 'C:\\Users\\adam' in guide or '學習型知識庫系統_OPS' in guide):
        raise ValueError("PUBLIC root guide is not portable")
    guide_bytes = guide.encode("utf-8")

    previous_version = ""
    unchanged = False
    if output.exists():
        existing_bytes = output.read_bytes()
        existing = existing_bytes.decode("utf-8")
        match = GENERATED_ROOT_PATTERN.search(existing)
        if not match:
            raise ValueError("existing PUBLIC root is not a generated release guide; refusing replacement")
        previous_version = match.group(1)
        prior_key = version_key(previous_version)
        candidate_key = version_key(version)
        if prior_key > candidate_key:
            raise ValueError("existing PUBLIC root points to a newer product version; refusing downgrade")
        if previous_version == version:
            if existing_bytes == guide_bytes:
                unchanged = True
            else:
                raise ValueError("existing PUBLIC root has different content for this product version; refusing replacement")
        elif not replace_existing:
            raise ValueError("PUBLIC root entry already exists; pass --replace-existing after verifying a newer release")

    output.parent.mkdir(parents=True, exist_ok=True)
    if not previous_version:
        with output.open("xb") as handle:
            handle.write(guide_bytes)
    elif not unchanged:
        replacement = output.with_name(f".{output.name}.{version}.replace")
        assert_plain_path(replacement)
        if replacement.exists():
            raise ValueError("PUBLIC root replacement file already exists; inspect it before retrying")
        try:
            replacement.write_bytes(guide_bytes)
            replacement.replace(output)
        finally:
            if replacement.exists():
                replacement.unlink()
    return {"version": version, "replaced": bool(previous_version) and not unchanged,
            "previous_version": previous_version, "unchanged": unchanged}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--replace-existing", action="store_true",
                        help="replace only a prior generated root guide after a newer release has been verified")
    args = parser.parse_args()
    try:
        result = build(args.release, args.output, replace_existing=args.replace_existing)
    except (OSError, TypeError, ValueError, KeyError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "unchanged" if result["unchanged"] else "built", **result, "published": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
