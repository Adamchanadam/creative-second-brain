"""Installed portable runtime entry for the complete Creative Second Brain."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener
from pathlib import Path, PureWindowsPath

from reader_service_lease import ReaderLease

ROOT = Path(__file__).resolve().parents[1]
CONFIG_SCHEMAS = {"creative_second_brain_workspace_v2", "creative_second_brain_workspace_v3"}


def emit(value: dict) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2)); return 0


def inside(value: str | Path) -> Path:
    raw = str(value).replace("\\", "/")
    path = Path(raw)
    windows_path = PureWindowsPath(raw)
    if (not raw.strip() or raw.startswith("/") or path.is_absolute() or windows_path.is_absolute()
            or windows_path.drive or ".." in path.parts):
        raise ValueError("path must be a workspace-relative path")
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError("path escapes the workspace")
    return target


def config() -> dict:
    marker = ROOT / "brain.json"
    if not marker.is_file():
        raise ValueError("not a configured workspace; run setup_workspace.py first")
    if (ROOT / ".package-init.json").exists():
        raise ValueError("workspace initialization is incomplete; preserve and inspect its marker")
    data = json.loads(marker.read_text(encoding="utf-8"))
    if data.get("schema") not in CONFIG_SCHEMAS or data.get("package_schema") != "creative_second_brain_portable_v1":
        raise ValueError("this entry only operates a workspace made by this package")
    if not isinstance(data.get("vault"), str) or not isinstance(data.get("managed_files"), dict) or not data["managed_files"]:
        raise ValueError("workspace configuration is incomplete")
    return data


_HASH = re.compile(r"[0-9a-f]{64}\Z")
_PRODUCT_VERSION = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_SHARED_SCHEMA = "creative_second_brain_shared_surface_v1"
_HOST_OVERLAY_SCHEMA = "creative_second_brain_host_runtime_overrides_v1"
_READER_SERVICE_GENERATION = "knowledge-nebula-server-v6"
_READER_STARTUP_SECONDS = 45.0
_READER_BUSY_RECOVERY_SECONDS = 10.0
_READER_GENERATION = re.compile(r"knowledge-nebula-server-v([1-9]\d*)\Z")


def _hash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _version_key(value: object) -> tuple[int, int, int]:
    match = _PRODUCT_VERSION.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("workspace product version is invalid")
    return tuple(int(part) for part in match.groups())


def installed_product_version(cfg: dict) -> str:
    value = cfg.get("package_version")
    _version_key(value)
    management = cfg.get("management")
    binding = management.get("package_binding") if isinstance(management, dict) else None
    if not isinstance(binding, dict):
        raise ValueError("workspace management package binding is malformed")
    if _version_key(binding.get("package_version")) != _version_key(value):
        raise ValueError("workspace product version disagrees with installed management binding")
    return value


def _shared_paths(managed: dict) -> set[str]:
    paths = {"tools/brain.py"}
    for rel in managed:
        parts = rel.split("/")
        if len(parts) == 4 and parts[:2] == [".agents", "skills"] and parts[2].startswith("brain-") and parts[3] == "SKILL.md":
            paths.add(rel)
    return paths


def _integrity_expectations(cfg: dict) -> tuple[dict, dict, dict]:
    managed = cfg["managed_files"]
    if not all(isinstance(path, str) and _hash(digest) for path, digest in managed.items()):
        raise ValueError("workspace managed-file contract is malformed")
    installed_version = _version_key(installed_product_version(cfg))
    expected = dict(managed)
    shared = cfg.get("shared_surface")
    shared_detail = {"status": "base"}
    paths = _shared_paths(managed)
    if shared is not None:
        if not isinstance(shared, dict) or shared.get("schema") != _SHARED_SCHEMA:
            raise ValueError("shared surface contract is malformed")
        files = shared.get("files")
        binding = shared.get("package_binding")
        if not isinstance(files, dict) or set(files) != paths or not all(_hash(digest) for digest in files.values()):
            raise ValueError("shared surface file contract is malformed")
        if not isinstance(binding, dict):
            raise ValueError("shared surface package binding is malformed")
        source_version = _version_key(binding.get("package_version"))
        if source_version < installed_version:
            raise ValueError("shared surface source version predates installed product version")
        expected.update(files)
        shared_detail = {"status": "active", "source_package_version": binding["package_version"], "files": sorted(files)}
    overlays = cfg.get("host_runtime_overrides")
    overlay_detail = []
    if overlays is not None:
        if not isinstance(overlays, dict) or overlays.get("schema") != _HOST_OVERLAY_SCHEMA or not isinstance(overlays.get("files"), dict):
            raise ValueError("host runtime override contract is malformed")
        for rel, value in overlays["files"].items():
            if not isinstance(rel, str) or rel not in managed or rel in paths or not isinstance(value, dict):
                raise ValueError("host runtime override path is invalid")
            base = value.get("base_sha256"); actual = value.get("host_sha256")
            if base != managed[rel] or not _hash(actual) or actual == base:
                raise ValueError("host runtime override hash is invalid")
            expected[rel] = actual
            overlay_detail.append(rel)
    return expected, shared_detail, {"count": len(overlay_detail), "files": sorted(overlay_detail)}


def doctor_result() -> dict:
    cfg = config(); changed = []; missing = []
    try:
        expected_files, shared_detail, overlay_detail = _integrity_expectations(cfg)
    except ValueError as error:
        return {"status": "blocked", "package_version": cfg.get("package_version"), "profile": cfg.get("profile"),
                "display_name": cfg.get("display_name"), "vault": cfg.get("vault"), "missing": [],
                "modified_runtime_files": [], "configuration_error": str(error),
                "boundary": "Local runtime integrity only; not image, source, semantic, Obsidian UI, or host slash-command acceptance."}
    for rel, expected in expected_files.items():
        path = inside(rel)
        if not path.is_file(): missing.append(rel); continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected: changed.append(rel)
    status = "ready" if not missing and not changed else "blocked"
    return {"status": status, "package_version": cfg.get("package_version"), "profile": cfg.get("profile"),
            "display_name": cfg.get("display_name"), "vault": cfg.get("vault"), "missing": missing,
            "modified_runtime_files": changed, "shared_surface": shared_detail,
            "host_runtime_overrides": overlay_detail,
            "boundary": "Local runtime integrity only; not image, source, semantic, Obsidian UI, or host slash-command acceptance."}


def doctor() -> int:
    result = doctor_result()
    emit(result)
    return 0 if result["status"] == "ready" else 1


def require_ready() -> dict:
    result = doctor_result()
    if result["status"] != "ready":
        raise ValueError("workspace preflight blocked; run doctor and repair the reported runtime boundary before an operation")
    # doctor_result deliberately returns a compact health report.  Entrypoints
    # below also need the complete installed contract (notably entry_contract
    # when open-home regenerates the derived home projection), so reload the
    # authoritative workspace marker only after the integrity gate passes.
    return config()


def context() -> int:
    readiness = require_ready(); jobs = []
    job_root = inside("outputs/compiler_worker_v2/_jobs")
    for path in sorted(job_root.glob("*.json")) if job_root.is_dir() else []:
        row = json.loads(path.read_text(encoding="utf-8"))
        if row.get("schema_version") != "compiler_worker_v2_job_v1": continue
        job_id = row.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            raise ValueError("invalid persisted job identity; preserve and inspect the original job")
        jobs.append({"job_id":job_id, "source_id":row.get("source_id"), "status":row.get("status"),
                     "path":path.relative_to(ROOT).as_posix(), "inspect_arguments":["text","inspect","--job-id",job_id]})
    return emit({"status":"ready", "profile":readiness["profile"], "vault":readiness["vault"],
                 "required_reading":["docs/README.md", "docs/Compiler Worker與Knowledge Package設計及驗收規格.md",
                                     "docs/Obsidian語義索引與關聯圖落地方案.md"], "jobs":jobs,
                 "chat_history_required":False,
                 "boundary":"Persisted jobs identify what to read; they do not authorize resuming without a current task. Full portable entries are in .agents/skills/brain-*."})


def compat(source_note: str) -> int:
    # Preview-only parser. Heading matching is exact and only removes the matched
    # application section from the displayed source body; no file is rewritten.
    text = inside(source_note).read_text(encoding="utf-8")
    legacy_heading = "## For " + "Ad" + "am" + ":個人化消化"
    headings = (legacy_heading, "## 個人化消化")
    position = min((p for h in headings if (p := text.find(h)) >= 0), default=-1)
    if position < 0:
        return emit({"status": "preview", "legacy_application_found": False, "source_body": text.strip(), "application": ""})
    end = text.find("\n## ", position + 3)
    end = len(text) if end < 0 else end
    block = text[position:end].strip()
    application = block.replace(block.splitlines()[0], "## 個人應用摘要", 1)
    source_body = (text[:position].rstrip() + "\n" + text[end:].lstrip()).strip()
    return emit({"status": "preview", "legacy_application_found": True, "source_body": source_body, "application": application,
                 "migration": "not performed"})


def home() -> int:
    """Report existing local reader entry points without writing or rebuilding."""
    cfg = require_ready()
    vault = Path(str(cfg["vault"]))
    paths = {
        "home": inside("index.html"),
        "outcome_index": inside(vault / "outcome-pages" / "index.html"),
    }
    missing = [name for name, path in paths.items() if not path.is_file()]
    return emit({
        "status": "ready" if not missing else "needs_repair",
        "writes": 0,
        "package_version": cfg["package_version"],
        "paths": {name: path.relative_to(ROOT).as_posix() for name, path in paths.items()},
        "missing": missing,
    })


def guide() -> int:
    """Refresh this workspace's daily home without an installed product skill."""
    cfg = require_ready()
    return emit(_refresh_home_projection(cfg))


def _refresh_home_projection(cfg: dict) -> dict:
    """Rebuild only the derived daily home from the installed runtime contract."""
    from management_exclusion import assert_management_idle
    assert_management_idle(ROOT)
    from workspace_home_renderer import render, validate_entries
    entries = validate_entries(cfg.get("entry_contract"))
    destination = render(runtime=ROOT, workspace=ROOT, vault_rel=str(cfg["vault"]),
                         display_name=str(cfg["display_name"]), package_version=str(cfg["package_version"]),
                         entries=entries)
    return {"status": "home_refreshed", "writes": 1,
            "path": destination.relative_to(ROOT).as_posix(), "historical_notes_rewritten": False}


def _reader_origin() -> tuple[str, str]:
    identity = hashlib.sha256(str(ROOT.resolve()).casefold().encode()).hexdigest()[:20]
    return identity, str(_reader_ports(identity)[0])


def _reader_ports(identity: str) -> tuple[int, ...]:
    try:
        from reader_port_policy import candidate_ports
    except ImportError as error:
        raise ValueError("reader port policy is unavailable") from error
    return candidate_ports(identity)


def _reader_request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None, timeout: float = 2.0) -> tuple[int, bytes]:
    request = Request(url, data=data, headers=headers or {}, method="POST" if data is not None else "GET")
    opener = build_opener(ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return int(response.status), response.read()


def _reader_status(origin: str, identity: str, *, timeout: float = .5) -> dict | None:
    try:
        status, body = _reader_request(origin + "/api/status", timeout=timeout)
        value = json.loads(body.decode("utf-8"))
        if status == 200 and value.get("app") == "knowledge-nebula" and value.get("identity") == identity:
            return value
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        pass
    return None


def _find_reader_state(origins: list[str], identity: str, *, deadline: float | None = None) -> tuple[str | None, dict | None]:
    try:
        from reader_port_policy import find_reader_state
    except ImportError as error:
        raise ValueError("reader port policy is unavailable") from error
    expected = [f"http://127.0.0.1:{port}" for port in _reader_ports(identity)]
    if origins != expected:
        raise ValueError("reader origin set is invalid")
    return find_reader_state(identity, lambda origin, timeout: _reader_status(origin, identity, timeout=timeout),
                             timeout=.5, deadline=deadline)


def _find_reader(origins: list[str], identity: str, *, deadline: float | None = None) -> str | None:
    origin, state = _find_reader_state(origins, identity, deadline=deadline)
    return origin if state and state.get("generation") == _READER_SERVICE_GENERATION and not state.get("retiring") else None


def _reader_generation_relation(active: object) -> int:
    """Return -1 only for a supported older reader; never request downgrade."""
    candidate = _READER_GENERATION.fullmatch(active) if isinstance(active, str) else None
    expected = _READER_GENERATION.fullmatch(_READER_SERVICE_GENERATION)
    if not candidate or not expected:
        raise ValueError("existing reader has an unsupported generation identity")
    return (int(candidate.group(1)) > int(expected.group(1))) - (int(candidate.group(1)) < int(expected.group(1)))


def _reader_page_token(origin: str, cfg: dict) -> str:
    nebula_path = Path(str(cfg["vault"])) / "outcome-pages" / "nebula.html"
    _, page = _reader_request(origin + "/" + quote(nebula_path.as_posix(), safe="/"), timeout=5)
    match = re.search(r"const LOCAL_SYNC=(\{.*?\});", page.decode("utf-8"), re.S)
    if not match:
        raise ValueError("current reader service did not issue a local sync token")
    token = json.loads(match.group(1)).get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("current reader service token is invalid")
    return token


def _handoff_existing_reader(origin: str, cfg: dict) -> bool:
    """Ask an older reader to drain writes; false means only a transport race."""
    try:
        token = _reader_page_token(origin, cfg)
        _, body = _reader_request(origin + "/api/handoff", data=json.dumps({"generation": _READER_SERVICE_GENERATION}).encode("utf-8"), headers={
            "Origin": origin, "X-Nebula-Token": token, "Content-Type": "application/json",
        }, timeout=20)
        result = json.loads(body.decode("utf-8"))
        if not isinstance(result, dict) or result.get("status") != "retiring":
            raise ValueError("reader handoff was not accepted")
        return True
    except HTTPError as error:
        if error.code == 409:
            # Another opener already closed admission.  Poll the same root;
            # never bind a second fallback listener while it drains.
            return True
        if error.code in {404, 405}:
            raise ValueError("existing reader is an older generation without safe handoff; close it through an authorized migration, then retry") from error
        raise ValueError("existing reader did not accept authenticated handoff: HTTP " + str(error.code)) from error
    except (URLError, OSError):
        # Another opener can complete the authenticated handoff between our
        # status read and token request.  The caller must recheck this same
        # root within its existing deadline before it can ever start a child.
        return False
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError("existing reader did not complete safe handoff: " + str(error)) from error


def _reader_refresh_job(origin: str, cfg: dict, *, deadline: float, resume_job: str | None = None) -> dict:
    """Submit one correlatable refresh and only ever poll that receipt."""
    requested_job_id = resume_job or uuid.uuid4().hex
    if not re.fullmatch(r"[a-f0-9]{32}", requested_job_id):
        raise ValueError("reader refresh job id is invalid")
    headers = {"Origin": origin, "X-Nebula-Token": _reader_page_token(origin, cfg), "Content-Type": "application/json"}
    if resume_job:
        job_id = resume_job
    else:
        try:
            _, body = _reader_request(origin + "/api/refresh-jobs", data=json.dumps({"job_id": requested_job_id}).encode("utf-8"),
                                      headers=headers, timeout=2)
            submitted = json.loads(body.decode("utf-8"))
            job_id = submitted.get("job_id") if isinstance(submitted, dict) else None
            if not isinstance(job_id, str):
                raise ValueError("reader refresh receipt was invalid")
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            # Delivery may already have happened.  Do not create another job;
            # the caller-owned id is the only safe receipt to inspect.
            job_id = requested_job_id
    last_poll_failed = False
    while time.monotonic() < deadline:
        try:
            _, body = _reader_request(origin + "/api/refresh-jobs/" + job_id, headers={"Origin": origin, "X-Nebula-Token": headers["X-Nebula-Token"]}, timeout=2)
            receipt = json.loads(body.decode("utf-8"))
        except HTTPError as error:
            if error.code == 404:
                raise ValueError("reader refresh receipt was not found for job " + job_id) from error
            raise ValueError("reader refresh receipt could not be read for job " + job_id) from error
        except (URLError, OSError):
            # A short local transport lapse says nothing about the accepted
            # work.  Keep polling this exact receipt until the one deadline.
            last_poll_failed = True
            time.sleep(.1)
            continue
        except (ValueError, json.JSONDecodeError) as error:
            raise ValueError("reader refresh receipt could not be read for job " + job_id) from error
        if not isinstance(receipt, dict) or receipt.get("job_id") != job_id:
            raise ValueError("reader refresh receipt was invalid")
        last_poll_failed = False
        status = receipt.get("status")
        if status == "ready":
            result = receipt.get("result")
            if not isinstance(result, dict):
                raise ValueError("reader refresh receipt was invalid")
            return {**result, "job_id": job_id}
        if status == "failed":
            raise ValueError("reader synchronization failed for job " + job_id)
        if status not in {"pending", "running"}:
            raise ValueError("reader refresh receipt has an unknown status")
        time.sleep(.1)
    if last_poll_failed:
        raise ValueError("reader is unavailable; refresh job outcome is unknown for job " + job_id)
    return {"status": "pending", "job_id": job_id}


def open_reader(target: str, *, resume_job: str | None = None) -> int:
    """Start/reuse the bounded loopback reader and return its exact local URL."""
    cfg = require_ready()
    identity, _ = _reader_origin()
    origins = [f"http://127.0.0.1:{port}" for port in _reader_ports(identity)]
    home_projection = None
    origin = None
    started = False
    deadline = time.monotonic() + _READER_STARTUP_SECONDS
    if resume_job:
        candidate, state = _find_reader_state(origins, identity, deadline=deadline)
        if state is None or candidate is None:
            raise ValueError("reader refresh submission outcome is unknown because the original reader service is unavailable")
        if _reader_generation_relation(state.get("generation")) != 0 or state.get("retiring"):
            raise ValueError("reader refresh submission outcome is unknown because the original reader service changed")
        origin = candidate
    while origin is None and time.monotonic() < deadline:
        candidate, state = _find_reader_state(origins, identity, deadline=deadline)
        if state is not None:
            relation = _reader_generation_relation(state.get("generation"))
            if relation == 0 and not state.get("retiring"):
                origin = candidate
                break
            if relation > 0:
                raise ValueError("existing reader is newer than this workspace; downgrade handoff is not allowed")

        # The lease owns a complete succession, rather than just spawning:
        # old discovery -> authenticated handoff -> old listener exit ->
        # verified successor. Concurrent openers only wait and re-check.
        lease = ReaderLease(ROOT, "startup")
        if not lease.acquire():
            time.sleep(.1)
            continue
        try:
            handoff_requested = False
            while time.monotonic() < deadline:
                candidate, state = _find_reader_state(origins, identity, deadline=deadline)
                if state is not None:
                    relation = _reader_generation_relation(state.get("generation"))
                    if relation == 0 and not state.get("retiring"):
                        origin = candidate
                        break
                    if relation > 0:
                        raise ValueError("existing reader is newer than this workspace; downgrade handoff is not allowed")
                    # False means only a listener-disappeared transport race.
                    # Retain the lease and re-read identity; do not infer a
                    # completed handoff or let a second opener race this one.
                    if not handoff_requested:
                        handoff_requested = _handoff_existing_reader(candidate, cfg)
                    time.sleep(.1)
                    continue

                if target == "home":
                    home_projection = _refresh_home_projection(cfg)
                command = [sys.executable, "-B", str(ROOT / "tools" / "knowledge_nebula_server.py"), "--root", str(ROOT),
                           "--no-browser", "--page", target, "--require-generation", _READER_SERVICE_GENERATION]
                idle_seconds = os.environ.get("CREATIVE_SECOND_BRAIN_READER_IDLE_SECONDS")
                if idle_seconds:
                    if not idle_seconds.isdigit() or not 2 <= int(idle_seconds) <= 3600:
                        raise ValueError("reader idle-seconds environment value is invalid")
                    command.extend(("--idle-seconds", idle_seconds))
                options: dict = {"cwd": ROOT, "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                if os.name == "nt":
                    options["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                else:
                    options["start_new_session"] = True
                try:
                    reader_process = subprocess.Popen(command, **options)
                except OSError as error:
                    raise ValueError("local reader service could not start: " + type(error).__name__) from error
                # Child takes the service lease, never startup. Keep startup
                # until an authenticated endpoint is available.
                while time.monotonic() < deadline:
                    origin = _find_reader(origins, identity, deadline=deadline)
                    if origin is not None:
                        started = reader_process.poll() is None
                        break
                    exit_code = reader_process.poll()
                    if exit_code is not None:
                        origin = _find_reader(origins, identity, deadline=time.monotonic() + .5)
                        if origin is not None:
                            break
                        raise ValueError("local reader service exited before readiness (exit code " + str(exit_code) + ")")
                    time.sleep(.1)
                if origin is not None:
                    break
                exit_code = reader_process.poll()
                if exit_code is not None:
                    raise ValueError("local reader service exited before readiness (exit code " + str(exit_code) + ")")
                raise ValueError("local reader service is still initializing after " + str(int(_READER_STARTUP_SECONDS))
                                 + " seconds; no verified URL was emitted")
        finally:
            lease.release()
        if origin is not None:
            break
    if origin is None:
        raise ValueError("local reader startup did not yield a verified URL within " + str(int(_READER_STARTUP_SECONDS)) + " seconds")
    if target == "home" and home_projection is None:
        home_projection = _refresh_home_projection(cfg)
    if started and resume_job:
        raise ValueError("reader refresh job cannot resume because this reader service was restarted")
    sync = ({"status": "started"} if started else _reader_refresh_job(
        origin, cfg, deadline=time.monotonic() + _READER_STARTUP_SECONDS, resume_job=resume_job))
    if home_projection is not None:
        sync["home_projection"] = home_projection
    if sync.get("status") == "pending":
        resume_action = "open-home" if target == "home" else "nebula"
        return emit({"status": "pending", "target": target, "job_id": sync["job_id"],
                     "service": "reused", "synchronization": sync,
                     "reason": "reader synchronization is still running; rerun " + resume_action + " with --resume-job " + sync["job_id"]})
    if target == "home":
        page_url = origin + "/index.html"
        _, page = _reader_request(page_url, timeout=5)
        if not re.search(r'\bid=["\']prompts["\']', page.decode("utf-8"), re.I):
            raise ValueError("home page does not contain the 常用操作 anchor")
        return emit({"status": "ready", "target": "home", "url": page_url + "#prompts", "fragment": "prompts",
                     "fragment_verified": True, "service": "started" if started else "reused", "synchronization": sync})
    nebula_path = quote((Path(str(cfg["vault"])) / "outcome-pages" / "nebula.html").as_posix(), safe="/")
    page_url = origin + "/" + nebula_path
    _, page = _reader_request(page_url, timeout=5)
    if b"knowledge-nebula-page" not in page and b"knowledge-nebula" not in page:
        raise ValueError("knowledge nebula page did not read back")
    return emit({"status": "ready", "target": "nebula", "url": page_url,
                 "service": "started" if started else "reused", "synchronization": sync})


def declared_options(script: Path) -> tuple[set[str], set[str]]:
    options = {"--help", "-h"}; paths = set()
    for node in ast.walk(ast.parse(script.read_text(encoding="utf-8-sig"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_argument": continue
        names = {arg.value for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-")}
        options.update(names)
        if any(item.arg == "type" and isinstance(item.value, ast.Name) and item.value.id == "Path" for item in node.keywords): paths.update(names)
    return options, paths


def dispatch(action: str, args: list[str]) -> int:
    cfg = require_ready(); vault = inside(cfg["vault"])
    from management_exclusion import assert_management_idle
    assert_management_idle(ROOT)
    if any(arg == flag or arg.startswith(flag + "=") for arg in args for flag in ("--root", "--vault", "--workspace")):
        raise ValueError("workspace and vault are fixed by this entry; do not override them")
    runners = {
        "text": ("ingest_rail.py", ["--root", str(ROOT)]),
        "rail": ("ingest_rail.py", ["--root", str(ROOT)]),
        # ``think`` is the canonical public route.  Keep ``ask`` as a direct
        # CLI compatibility alias; both dispatch to one formal archive flow.
        "think": ("ask_second_brain.py", ["--vault", str(vault)]),
        "ask": ("ask_second_brain.py", ["--vault", str(vault)]),
        "material": ("knowledge_material_selector.py", ["--vault", str(vault)]),
        "tidy": ("obsidian_semantic_maintenance.py", ["--vault", str(vault)]),
        "graph": ("obsidian_semantic_map_flow.py", ["--vault", str(vault)]),
        "retrieve": ("obsidian_idea_retrieval.py", ["--vault", str(vault)]),
        "outcome": ("outcome_page_generator.py", ["--vault", str(vault)]),
        "research": ("research_catalog.py", ["--root", str(ROOT)]),
        "research-program": ("research_program.py", ["--root", str(ROOT)]),
        "update": ("brain_update.py", ["--workspace", str(ROOT)]),
        "compat": ("firecrawl_obsidian_pipeline.py", ["--root", str(ROOT)]),
    }
    try:
        script, fixed = runners[action]
    except KeyError as error:
        raise ValueError("unsupported portable action") from error
    argv = [script, *fixed, *args]
    _, native_paths = declared_options(ROOT / "tools" / argv[0])
    # Some OPS parsers construct declared flags in loops, which cannot be
    # faithfully recovered from static AST.  The copied native parser remains
    # the single authority for unknown/abbreviated options; this wrapper only
    # owns workspace containment for path-bearing values.
    file_flags = ({"--text-file", "--image-file", "--response-file", "--packet", "--answer", "--review", "--suitability", "--output-dir"} | native_paths) - {"--root", "--vault", "--workspace"}
    for index, arg in enumerate(argv):
        if arg in file_flags and index + 1 < len(argv): argv[index + 1] = str(inside(argv[index + 1]))
        elif any(arg.startswith(flag + "=") for flag in file_flags):
            flag, value = arg.split("=", 1); argv[index] = flag + "=" + str(inside(value))
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
               CREATIVE_SECOND_BRAIN_VAULT=cfg["vault"], CREATIVE_SECOND_BRAIN_ROOT=str(ROOT))
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "tools" / argv[0]), *argv[1:]],
        cwd=ROOT, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    # Runtime tools may include their execution root in an otherwise useful
    # work order.  New users need workspace-relative instructions, not a
    # private machine path; the original files remain unchanged on disk.
    roots = (str(ROOT), str(ROOT).replace("\\", "\\\\"), str(vault), str(vault).replace("\\", "\\\\"))
    for stream, handle in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        if stream:
            projected = stream
            for value in roots[:2]:
                projected = projected.replace(value, ".")
            for value in roots[2:]:
                projected = projected.replace(value, cfg["vault"])
            handle.write(projected)
    return result.returncode


def obsidian(arguments: list[str]) -> int:
    """Run the portable read-only Obsidian bridge for this configured vault."""
    cfg = require_ready()
    raw = str(cfg["vault"]).replace("\\", "/")
    lexical = Path(raw)
    windows = PureWindowsPath(raw)
    if (not raw.strip() or raw.startswith("/") or lexical.is_absolute() or windows.is_absolute()
            or windows.drive or ".." in lexical.parts):
        raise ValueError("configured vault must be a workspace-relative path")
    vault = ROOT / lexical
    from obsidian_cli_adapter import execute
    return emit(execute(arguments, vault=vault))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("doctor", "context", "home", "guide", "open-home", "nebula", "obsidian", "text", "rail", "think", "ask", "material", "tidy", "graph", "retrieve", "outcome", "research", "research-program", "update", "compat", "legacy-preview"))
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    ns = parser.parse_args()
    try:
        if ns.action == "doctor":
            if ns.arguments: raise ValueError("doctor takes no arguments")
            return doctor()
        if ns.action == "context":
            if ns.arguments: raise ValueError("context takes no arguments")
            return context()
        if ns.action == "home":
            if ns.arguments: raise ValueError("home takes no arguments")
            return home()
        if ns.action == "guide":
            if ns.arguments: raise ValueError("guide takes no arguments")
            return guide()
        if ns.action in {"open-home", "nebula"}:
            if not ns.arguments:
                return open_reader("home" if ns.action == "open-home" else "nebula")
            if len(ns.arguments) != 2 or ns.arguments[0] != "--resume-job":
                raise ValueError(ns.action + " takes no arguments, except --resume-job <job-id>")
            return open_reader("home" if ns.action == "open-home" else "nebula", resume_job=ns.arguments[1])
        if ns.action == "legacy-preview":
            if len(ns.arguments) != 2 or ns.arguments[0] != "--source-note":
                raise ValueError("legacy-preview requires --source-note <workspace-relative-note>")
            config(); return compat(ns.arguments[1])
        if ns.action == "obsidian":
            return obsidian(ns.arguments)
        return dispatch(ns.action, ns.arguments)
    except (ValueError, OSError, json.JSONDecodeError) as error:
        return emit({"status": "blocked", "reason": str(error)}) or 2


if __name__ == "__main__":
    raise SystemExit(main())
