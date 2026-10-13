"""Safely update one managed Creative Second Brain workspace.

This coordinator deliberately has no remote override.  It accepts only the
product's recorded GitHub release source, or a local candidate explicitly
given by the operator for an offline / first-access upgrade.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "tools" / "update-source.json"
SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class UpdateError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plain(path: Path) -> Path:
    for part in (path, *path.parents):
        try:
            meta = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or bool(getattr(meta, "st_file_attributes", 0) & 0x400):
            raise UpdateError("update path contains a link or junction")
    return path


def _read_source(workspace: Path) -> dict[str, Any]:
    try:
        value = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("installed update source is unreadable") from error
    required = {"schema", "repository", "latest_release_api", "release_page", "archive_name", "required_assets", "allowed_download_hosts"}
    if set(value) != required or value.get("schema") != "creative_second_brain_update_source_v1":
        raise UpdateError("installed update source is invalid")
    repository = value.get("repository")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise UpdateError("installed update repository is invalid")
    if value.get("latest_release_api") != f"https://api.github.com/repos/{repository}/releases/latest" or value.get("release_page") != f"https://github.com/{repository}/releases":
        raise UpdateError("installed update source is internally inconsistent")
    try:
        marker = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
        expected = marker.get("managed_files", {}).get("tools/update-source.json")
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        expected = None
    if expected is not None and (not isinstance(expected, str) or not SHA256.fullmatch(expected) or _sha(SOURCE_PATH) != expected):
        raise UpdateError("installed update source differs from the managed workspace record")
    if not isinstance(value["allowed_download_hosts"], list) or not all(isinstance(x, str) and x for x in value["allowed_download_hosts"]):
        raise UpdateError("installed update download host policy is invalid")
    return value


class _OfficialRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts: set[str]):
        super().__init__()
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in self.hosts:
            raise UpdateError("release asset redirect left the official GitHub hosts")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url: str, target: Path, hosts: set[str]) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.hostname not in hosts:
        raise UpdateError("update source URL is not an official HTTPS endpoint")
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "creative-second-brain-update"})
    try:
        with urllib.request.build_opener(_OfficialRedirects(hosts)).open(request, timeout=30) as response:
            final_host = urllib.parse.urlparse(response.geturl()).hostname
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https" or final.username or final.password or final.hostname not in hosts:
                raise UpdateError("release asset final URL left the official GitHub hosts")
            with target.open("xb") as output:
                shutil.copyfileobj(response, output)
    except (urllib.error.URLError, OSError) as error:
        raise UpdateError("official release download failed") from error


def _release_candidate(source: dict[str, Any], temporary: Path) -> tuple[Path, str, dict[str, Any]]:
    metadata = temporary / "release.json"
    _download(source["latest_release_api"], metadata, set(source["allowed_download_hosts"]))
    try:
        release = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("official release metadata is malformed") from error
    tag = release.get("tag_name")
    if release.get("draft") is not False or release.get("prerelease") is not False or not isinstance(tag, str) or not tag.startswith("v") or not SEMVER.fullmatch(tag[1:]):
        raise UpdateError("official latest release is not a stable vX.Y.Z release")
    version = tag[1:]
    expected_names = {item.format(version=version) for item in source["required_assets"]}
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("official latest release has no assets")
    selected: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict) or not isinstance(asset.get("name"), str):
            continue
        asset_name = asset["name"]
        if asset_name in expected_names:
            if asset_name in selected:
                raise UpdateError("official latest release has duplicate required assets")
            selected[asset_name] = asset
    if set(selected) != expected_names:
        raise UpdateError("official latest release is missing required assets")
    for asset_name in sorted(expected_names):
        asset = selected[asset_name]
        url = asset.get("browser_download_url")
        if not isinstance(url, str):
            raise UpdateError("official release asset has no download URL")
        target = temporary / asset_name
        _download(url, target, set(source["allowed_download_hosts"]))
        digest = asset.get("digest")
        if digest is not None:
            if not isinstance(digest, str) or not digest.startswith("sha256:") or not SHA256.fullmatch(digest[7:]) or _sha(target) != digest[7:]:
                raise UpdateError("GitHub release asset digest does not match")
    archive = temporary / source["archive_name"].format(version=version)
    _verify_external_release(archive, temporary / "delivery-manifest.json", temporary / "SHA256SUMS.txt", version)
    return archive, version, {"kind": "github_latest", "tag": tag, "release": source["release_page"]}


def _verify_external_release(archive: Path, manifest_path: Path, sums_path: Path, version: str) -> None:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sums = sums_path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UpdateError("release delivery proof is unreadable") from error
    expected_name = f"creative-second-brain-{version}.zip"
    actual = _sha(archive)
    if archive.name != expected_name or manifest.get("version") != version or manifest.get("archive") != expected_name or manifest.get("archive_sha256") != actual or sums != f"SHA256 ({expected_name}) = {actual}":
        raise UpdateError("release archive hash, name, or version does not match its delivery proof")


def _local_archive(archive: Path, temporary: Path) -> tuple[Path, str, dict[str, Any]]:
    archive = _plain(archive.absolute())
    if not archive.is_file():
        raise UpdateError("offline archive is missing")
    copied = temporary / archive.name
    shutil.copyfile(archive, copied)
    manifest = archive.parent / "delivery-manifest.json"
    sums = archive.parent / "SHA256SUMS.txt"
    if manifest.is_file() or sums.is_file():
        if not manifest.is_file() or not sums.is_file():
            raise UpdateError("offline archive delivery proof is incomplete")
        shutil.copyfile(manifest, temporary / manifest.name)
        shutil.copyfile(sums, temporary / sums.name)
        try:
            version = json.loads(manifest.read_text(encoding="utf-8"))["version"]
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as error:
            raise UpdateError("offline archive delivery proof is malformed") from error
        if not isinstance(version, str) or not SEMVER.fullmatch(version):
            raise UpdateError("offline archive version is invalid")
        _verify_external_release(copied, temporary / manifest.name, temporary / sums.name, version)
        return copied, version, {"kind": "offline_archive", "external_hash_verified": True}
    return copied, "", {"kind": "offline_archive", "external_hash_verified": False}


def _load_delivery_contract() -> Any:
    path = ROOT / "tools" / "delivery_contract.py"
    spec = importlib.util.spec_from_file_location("brain_update_delivery_contract", path)
    if not spec or not spec.loader:
        raise UpdateError("installed delivery verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verify_archive(archive: Path, temporary: Path) -> tuple[Path, str]:
    contract = _load_delivery_contract()
    extracted = temporary / "extracted"
    identity, package = contract.safe_extract_archive(archive, extracted)
    version = identity.get("version")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise UpdateError("candidate archive identity has an invalid version")
    return package, version


def _verify_package(package: Path) -> tuple[Path, str]:
    package = _plain(package.absolute())
    if not package.is_dir():
        raise UpdateError("offline package directory is missing")
    trusted = ROOT / "tools" / "verify_package.py"
    spec = importlib.util.spec_from_file_location("brain_update_trusted_verify_package", trusted)
    if not spec or not spec.loader:
        raise UpdateError("installed package verifier is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.verify(package)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise UpdateError("offline package integrity verification failed") from error
    try:
        version = json.loads((package / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError) as error:
        raise UpdateError("offline package profile is unreadable") from error
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise UpdateError("offline package version is invalid")
    return package, version


def _candidate(args: argparse.Namespace, temporary: Path) -> tuple[Path, str, dict[str, Any], Path | None]:
    if args.package is not None:
        package, version = _verify_package(args.package)
        return package, version, {"kind": "offline_package", "external_hash_verified": False}, None
    if args.archive is not None:
        archive, declared, source = _local_archive(args.archive, temporary)
        package, version = _verify_archive(archive, temporary)
        if declared and declared != version:
            raise UpdateError("offline archive and embedded package versions differ")
        return package, version, source, archive
    source = _read_source(args.workspace.absolute())
    archive, declared, origin = _release_candidate(source, temporary)
    package, version = _verify_archive(archive, temporary)
    if declared != version:
        raise UpdateError("GitHub tag and embedded package versions differ")
    return package, version, origin, archive


def _manager(package: Path) -> Any:
    path = package / "scripts" / "manage_workspace.py"
    spec = importlib.util.spec_from_file_location("brain_update_candidate_manager", path)
    if not spec or not spec.loader:
        raise UpdateError("candidate workspace manager is unavailable")
    module = importlib.util.module_from_spec(spec)
    scripts = str(path.parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    spec.loader.exec_module(module)
    return module


def _private_hashes(plan: dict[str, Any]) -> dict[str, str]:
    records = plan.get("private_preservation")
    if not isinstance(records, list):
        raise UpdateError("candidate update plan lacks private preservation evidence")
    result: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not isinstance(record.get("tree"), dict):
            raise UpdateError("candidate update private preservation evidence is malformed")
        digest = record["tree"].get("sha256")
        if not isinstance(digest, str):
            raise UpdateError("candidate update private preservation digest is malformed")
        result[record["path"]] = digest
    return result


def _recovery_argv(workspace: Path, transaction_id: str | None, args: argparse.Namespace) -> list[str] | None:
    if not transaction_id:
        return None
    result = [sys.executable, "-B", str(Path(__file__).resolve()), "--workspace", str(workspace), "--rollback", transaction_id]
    if args.package is not None:
        result.extend(["--package", str(args.package)])
    elif args.archive is not None:
        result.extend(["--archive", str(args.archive)])
    return result


def _interrupted_transaction(manager: Any, workspace: Path, transaction_id: object) -> str | None:
    if not isinstance(transaction_id, str) or not transaction_id:
        return None
    try:
        if manager.manager_lock_owner(workspace) != transaction_id:
            return None
        transaction = next(item for item in manager.txs(workspace) if item.name == transaction_id)
        state = manager.recorded_txstate(transaction, workspace)
    except (StopIteration, OSError, ValueError, TypeError):
        return None
    return transaction_id if state not in {"committed", "rolled_back"} else None


def _temporary_root() -> str | None:
    configured = os.environ.get("BRAIN_UPDATE_TEMP_ROOT")
    if not configured:
        return None
    root = _plain(Path(configured).absolute())
    if not root.is_dir():
        raise UpdateError("configured update temporary directory is unavailable")
    return str(root)


def execute(args: argparse.Namespace) -> dict[str, Any]:
    workspace = _plain(args.workspace.absolute())
    if not workspace.is_dir():
        raise UpdateError("existing workspace directory required")
    with tempfile.TemporaryDirectory(prefix="brain-update-", dir=_temporary_root()) as raw:
        temporary = Path(raw)
        package, candidate_version, origin, archive = _candidate(args, temporary)
        manager = _manager(package)
        if args.rollback:
            outcome = manager.rollback(workspace, args.rollback, args.test_interrupt)
            return {"status": outcome["status"], "transaction_id": args.rollback, "candidate_version": candidate_version, "outcome": outcome}
        plan = manager.plan("update", workspace, None, None)
        before = _private_hashes(plan)
        result: dict[str, Any] = {"status": plan["status"], "candidate_version": candidate_version, "origin": origin, "writes": plan.get("writes"), "conflicts": plan.get("conflicts"), "private_hashes": before, "plan_hash": plan.get("plan_hash")}
        if plan.get("status") != "planned":
            return result
        if not args.apply:
            result["status"] = "checked"
            return result
        if plan.get("writes") == 0:
            result["status"] = "up_to_date"
            return result
        try:
            outcome = manager.apply(plan, plan["plan_hash"], interrupt=args.test_interrupt)
        except Exception as error:
            transaction_id = _interrupted_transaction(manager, workspace, getattr(error, "transaction_id", None))
            if transaction_id is None:
                result.update({"status": "blocked", "reason": str(error)})
            else:
                result.update({"status": "interrupted", "reason": str(error), "transaction_id": transaction_id, "recovery_argv": _recovery_argv(workspace, transaction_id, args)})
            return result
        transaction_id = outcome.get("transaction_id")
        try:
            status = manager.status(workspace)
            after_plan = manager.plan("update", workspace, None, None)
            after = _private_hashes(after_plan)
            if status.get("status") != "ready" or after != before:
                raise UpdateError("updated workspace failed manager status or private preservation verification")
            python = json.loads((workspace / "brain.json").read_text(encoding="utf-8")).get("runtime_python")
            if not isinstance(python, str) or not python:
                raise UpdateError("updated workspace runtime Python is unavailable")
            doctor = subprocess.run([python, "-B", str(workspace / "tools" / "brain.py"), "doctor"], cwd=workspace, text=True, encoding="utf-8", errors="replace", capture_output=True)
            if doctor.returncode:
                raise UpdateError("updated workspace doctor failed")
        except (UpdateError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            result.update({"status": "postcommit_verification_failed", "reason": str(error), "transaction_id": transaction_id, "recovery_argv": _recovery_argv(workspace, transaction_id, args)})
            return result
        result.update({"status": "updated", "transaction_id": outcome.get("transaction_id"), "manager_status": status.get("status"), "private_hashes": after, "doctor": "ready"})
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    candidate = parser.add_mutually_exclusive_group()
    candidate.add_argument("--package", type=Path)
    candidate.add_argument("--archive", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", metavar="TRANSACTION_ID")
    parser.add_argument("--test-interrupt", choices=("after_intent", "after_stage", "after_target", "after_verified", "rollback_after_stage", "rollback_after_target"), default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not hasattr(args, "test_interrupt"):
        args.test_interrupt = ""
    if args.rollback and (args.check or args.apply):
        parser.error("--rollback cannot be combined with --check or --apply")
    try:
        output = execute(args)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if output["status"] in {"checked", "up_to_date", "updated", "rolled_back", "already_rolled_back"} else 2
    except (UpdateError, OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
