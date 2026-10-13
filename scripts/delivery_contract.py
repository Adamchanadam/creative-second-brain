"""Fail-closed verification and extraction for a portable delivery ZIP.

The archive contains the portable package plus a non-circular, embedded
identity record.  A sibling delivery folder can additionally verify the final
archive SHA-256; an archive cannot safely contain a hash of its own final
bytes.
"""
from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from package_paths import assert_plain_path
from verify_package import verify as trusted_verify_package

PACKAGE_NAME = "creative-second-brain"
SCHEMA = "creative_second_brain_delivery_identity_v1"
EMBEDDED_MANIFEST = "delivery-manifest.json"
EMBEDDED_SUMS = "SHA256SUMS.txt"
BOOTSTRAP_FILES = {"__main__.py", "delivery_contract.py", "package_paths.py", "package_inventory.py", "verify_package.py"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or member.is_absolute()
        or PureWindowsPath(name).drive
        or ".." in member.parts
        or member.as_posix() != name
    ):
        raise ValueError("unsafe ZIP entry")
    return member


def inspect_archive(archive: Path) -> tuple[dict, set[str]]:
    """Validate ZIP membership before anything is extracted or installed."""
    archive = archive.absolute()
    assert_plain_path(archive)
    if not archive.is_file():
        raise ValueError("delivery archive is missing")
    names: set[str] = set()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            member = _safe_member(item.filename)
            if item.is_dir():
                continue
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode) or (mode and not stat.S_ISREG(mode)):
                raise ValueError("unsafe ZIP link or special entry")
            folded = member.as_posix().casefold()
            if folded in {entry.casefold() for entry in names}:
                raise ValueError("case-colliding ZIP entries")
            names.add(member.as_posix())
        if EMBEDDED_MANIFEST not in names or EMBEDDED_SUMS not in names:
            raise ValueError("delivery ZIP identity records are missing")
        if any(
            name not in {EMBEDDED_MANIFEST, EMBEDDED_SUMS, *BOOTSTRAP_FILES}
            and not name.startswith(PACKAGE_NAME + "/")
            for name in names
        ):
            raise ValueError("unexpected ZIP root entry")
        try:
            identity = json.loads(bundle.read(EMBEDDED_MANIFEST).decode("utf-8"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("delivery ZIP identity record is unreadable") from error
        try:
            sums = bundle.read(EMBEDDED_SUMS).decode("utf-8").strip()
        except (KeyError, UnicodeError) as error:
            raise ValueError("delivery ZIP checksum record is unreadable") from error
    if (
        not isinstance(identity, dict)
        or identity.get("schema") != SCHEMA
        or identity.get("package") != PACKAGE_NAME
        or not isinstance(identity.get("version"), str)
        or identity.get("archive_integrity") != "external_sha256_only"
        or not isinstance(identity.get("profile_build_sha256"), str)
        or not isinstance(identity.get("bootstrap_files"), dict)
    ):
        raise ValueError("delivery ZIP identity record is invalid")
    expected_sum = f"SHA256 ({PACKAGE_NAME}/profile-build.json) = {identity['profile_build_sha256']}"
    if sums != expected_sum:
        raise ValueError("delivery ZIP checksum record does not match identity")
    if set(identity["bootstrap_files"]) != BOOTSTRAP_FILES:
        raise ValueError("delivery ZIP bootstrap record is incomplete")
    with zipfile.ZipFile(archive) as bundle:
        for name, expected in identity["bootstrap_files"].items():
            if not isinstance(expected, str) or sha256_bytes(bundle.read(name)) != expected:
                raise ValueError("delivery ZIP bootstrap file does not match identity")
    return identity, names


def verify_extracted_package(package: Path, identity: dict) -> dict:
    """Verify the package's own complete manifest after a safe extraction."""
    package = package.absolute()
    assert_plain_path(package)
    if package.name != PACKAGE_NAME:
        raise ValueError("extracted package name is invalid")
    marker = package / "profile-build.json"
    assert_plain_path(marker)
    if sha256(marker) != identity["profile_build_sha256"]:
        raise ValueError("package profile build does not match delivery identity")
    # The verifier belongs to the already trusted updater / bootstrap runtime,
    # not to the candidate package being checked.  Candidate code only becomes
    # executable after its full manifest has passed this byte-level gate.
    manifest = trusted_verify_package(package)
    profile = json.loads((package / "profiles" / "profiles.json").read_text(encoding="utf-8"))
    if manifest.get("version") != identity["version"] or profile.get("version") != identity["version"]:
        raise ValueError("delivery and package versions differ")
    return manifest


def verify_known_delivery_context(package: Path) -> dict | None:
    """Never ignore a sibling external delivery proof that is present but bad."""
    package = package.absolute()
    sibling = package.parent / "delivery-manifest.json"
    assert_plain_path(sibling)
    if not sibling.exists():
        return None
    try:
        marker = json.loads(sibling.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("known delivery manifest is unreadable") from error
    if marker.get("schema") == "creative_second_brain_local_delivery_v2":
        return verify_delivery_folder(package.parent)
    if marker.get("schema") == SCHEMA:
        sums = package.parent / EMBEDDED_SUMS
        assert_plain_path(sums)
        if not sums.is_file():
            raise ValueError("embedded delivery checksum record is missing")
        expected = f"SHA256 ({PACKAGE_NAME}/profile-build.json) = {marker.get('profile_build_sha256', '')}"
        if sums.read_text(encoding="utf-8").strip() != expected:
            raise ValueError("embedded delivery checksum record does not match identity")
        manifest = verify_extracted_package(package, marker)
        return {"version": marker["version"], "files": len(manifest["files"]), "embedded": True}
    raise ValueError("known delivery manifest has an unsupported schema")


def verify_delivery_folder(delivery: Path) -> dict:
    """Verify external archive SHA plus embedded identity and package bytes."""
    delivery = delivery.absolute()
    assert_plain_path(delivery)
    manifest_path = delivery / "delivery-manifest.json"
    assert_plain_path(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("external delivery manifest is unreadable") from error
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != "creative_second_brain_local_delivery_v2"
        or manifest.get("package") != PACKAGE_NAME
        or not isinstance(manifest.get("archive"), str)
        or not isinstance(manifest.get("version"), str)
    ):
        raise ValueError("external delivery manifest is invalid")
    archive = delivery / manifest["archive"]
    external = verify_known_archive_context(archive)
    identity, _ = inspect_archive(archive)
    package = delivery / PACKAGE_NAME
    package_manifest = verify_extracted_package(package, identity)
    if identity["version"] != manifest.get("version"):
        raise ValueError("external and embedded delivery versions differ")
    return {"version": identity["version"], "archive": archive.name, "files": len(package_manifest["files"]), **(external or {})}


def verify_known_archive_context(archive: Path) -> dict | None:
    """Fail closed when either adjacent external delivery proof is present."""
    archive = archive.absolute()
    assert_plain_path(archive)
    marker_path = archive.parent / "delivery-manifest.json"
    sums = archive.parent / "SHA256SUMS.txt"
    assert_plain_path(marker_path)
    assert_plain_path(sums)
    marker_exists = marker_path.exists()
    sums_exists = sums.exists()
    if not marker_exists and not sums_exists:
        return None
    if not marker_exists or not sums_exists:
        raise ValueError("known external delivery proof is incomplete")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("known external delivery manifest is unreadable") from error
    if marker.get("schema") != "creative_second_brain_local_delivery_v2":
        raise ValueError("known external delivery manifest has an unsupported schema")
    version = marker.get("version")
    expected = f"{PACKAGE_NAME}-{version}.zip" if isinstance(version, str) else ""
    if marker.get("archive") != expected or archive.name != expected:
        raise ValueError("known external delivery archive name is invalid")
    digest_value = marker.get("archive_sha256")
    if not isinstance(digest_value, str) or sha256(archive) != digest_value:
        raise ValueError("known external delivery archive SHA-256 does not match")
    if not sums.is_file() or sums.read_text(encoding="utf-8").strip() != f"SHA256 ({expected}) = {digest_value}":
        raise ValueError("known external delivery checksum record does not match")
    identity, _ = inspect_archive(archive)
    if identity["version"] != version:
        raise ValueError("known external and embedded delivery versions differ")
    return {"external_archive_verified": True}


def safe_extract_archive(archive: Path, destination: Path) -> tuple[dict, Path]:
    """Extract only after ZIP safety checks into a new, plain directory."""
    verify_known_archive_context(archive)
    identity, names = inspect_archive(archive)
    destination = destination.absolute()
    assert_plain_path(destination)
    if destination.exists():
        raise ValueError("safe extraction destination must be new")
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for name in sorted(names):
                member = _safe_member(name)
                target = destination.joinpath(*member.parts)
                assert_plain_path(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(name) as source, target.open("xb") as output:
                    output.write(source.read())
        package = destination / PACKAGE_NAME
        verify_extracted_package(package, identity)
        return identity, package
    except Exception:
        # Leave no partially trusted path for an installer to mistake as valid.
        # `destination` was proven new and was created exclusively above.
        import shutil
        shutil.rmtree(destination)
        raise
