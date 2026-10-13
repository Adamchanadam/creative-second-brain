"""Fresh-package checks for the shared local reader opening routes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
LOGO_SHA256 = "961ea9c3a7dc7af9c3311a100995a7ad2aaa060232908e2195b4db27c60abeb9"


def run(*items: str, cwd: Path, environment: dict[str, str] | None = None) -> dict:
    env = dict(os.environ)
    if environment:
        env.update(environment)
    result = subprocess.run([PYTHON, "-B", *items], cwd=cwd, env=env, text=True,
                            encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    if result.returncode:
        raise AssertionError("reader route failed: " + " ".join(items) + "\n" + result.stdout + result.stderr)
    return json.loads(result.stdout)


def run_parallel(items: list[tuple[str, ...]], *, cwd: Path, environment: dict[str, str] | None = None) -> list[dict]:
    env = dict(os.environ)
    if environment:
        env.update(environment)
    processes = [subprocess.Popen([PYTHON, "-B", *row], cwd=cwd, env=env, text=True,
                                  encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for row in items]
    results = []
    for process, row in zip(processes, items):
        stdout, stderr = process.communicate(timeout=60)
        if process.returncode:
            raise AssertionError("parallel reader route failed: " + " ".join(row) + "\n" + stdout + stderr)
        results.append(json.loads(stdout))
    return results


def fetch(url: str) -> bytes:
    return build_opener(ProxyHandler({})).open(url, timeout=5).read()


def load_brain(path: Path):
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("reader_lifecycle_brain", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def assert_lifecycle_recovery(brain) -> None:
    cfg = {"vault": "My Notes", "display_name": "Reader opening", "package_version": "1.9.11", "entry_contract": []}
    emitted: list[dict] = []
    launched = {"value": False}
    clock = {"now": 0.0}

    class AliveProcess:
        def poll(self):
            return None

    def delayed_status(origin, identity, *, timeout=.5):
        clock["now"] += .25
        if launched["value"] and clock["now"] > 30:
            return {
                "app": "knowledge-nebula",
                "identity": identity,
                "generation": brain._READER_SERVICE_GENERATION,
            }
        return None

    def delayed_popen(*args, **kwargs):
        launched["value"] = True
        return AliveProcess()

    with patch.object(brain, "require_ready", return_value=cfg), \
         patch.object(brain, "_refresh_home_projection", return_value={"status": "home_refreshed"}), \
         patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
         patch.object(brain, "_reader_status", side_effect=delayed_status), \
         patch.object(brain.subprocess, "Popen", side_effect=delayed_popen), \
         patch.object(brain, "_reader_request", return_value=(200, b'<main id="prompts"></main>')), \
         patch.object(brain.time, "monotonic", side_effect=lambda: clock["now"]), \
         patch.object(brain.time, "sleep", side_effect=lambda seconds: clock.__setitem__("now", clock["now"] + seconds)), \
         patch.object(brain, "emit", side_effect=lambda value: emitted.append(value) or 0):
        assert brain.open_reader("home") == 0
    assert 20 < clock["now"] < 45
    assert emitted[-1]["service"] == "started"
    assert emitted[-1]["url"].endswith("/index.html#prompts") and emitted[-1]["fragment_verified"] is True

    class ExitedProcess:
        def poll(self):
            return 7

    with patch.object(brain, "require_ready", return_value=cfg), \
         patch.object(brain, "_refresh_home_projection", return_value={"status": "home_refreshed"}), \
         patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
         patch.object(brain, "_reader_status", return_value=None), \
         patch.object(brain.subprocess, "Popen", return_value=ExitedProcess()):
        try:
            brain.open_reader("home")
        except ValueError as error:
            assert str(error) == "local reader service exited before readiness (exit code 7)"
        else:
            raise AssertionError("reader child exit was reported as ready")

    deadline_clock = {"now": 0.0}
    deadline_emits: list[dict] = []

    def never_ready(origin, identity, *, timeout=.5):
        deadline_clock["now"] += 2
        return None

    with patch.object(brain, "require_ready", return_value=cfg), \
         patch.object(brain, "_refresh_home_projection", return_value={"status": "home_refreshed"}), \
         patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
         patch.object(brain, "_reader_status", side_effect=never_ready), \
         patch.object(brain.subprocess, "Popen", return_value=AliveProcess()), \
         patch.object(brain.time, "monotonic", side_effect=lambda: deadline_clock["now"]), \
         patch.object(brain.time, "sleep", side_effect=lambda seconds: deadline_clock.__setitem__("now", deadline_clock["now"] + seconds)), \
         patch.object(brain, "emit", side_effect=lambda value: deadline_emits.append(value) or 0):
        try:
            brain.open_reader("home")
        except ValueError as error:
            assert str(error) == "local reader service is still initializing after 45 seconds; no verified URL was emitted"
        else:
            raise AssertionError("reader startup deadline emitted an unverified URL")
    assert deadline_clock["now"] >= brain._READER_STARTUP_SECONDS and not deadline_emits

    assert_refresh_receipts(brain, cfg)


def assert_refresh_receipts(brain, cfg: dict) -> None:
    """A lost submit reply is recovered through exactly the caller's one receipt."""
    from types import SimpleNamespace
    job_id = "a" * 32
    calls = {"post": 0, "get": 0}

    def lost_submit(url, *, data=None, headers=None, timeout=2):
        if data is not None:
            calls["post"] += 1
            raise URLError("response lost after accept")
        calls["get"] += 1
        status = "running" if calls["get"] == 1 else "ready"
        value = {"job_id": job_id, "status": status}
        if status == "ready":
            value["result"] = {"status": "ready", "reader_sync": {"historical_pages_rewritten": 0}}
        return 200, json.dumps(value).encode("utf-8")

    with patch.object(brain, "_reader_page_token", return_value="unit"), \
         patch.object(brain, "_reader_request", side_effect=lost_submit), \
         patch.object(brain.uuid, "uuid4", return_value=SimpleNamespace(hex=job_id)):
        result = brain._reader_refresh_job("http://127.0.0.1:54321", cfg, deadline=time.monotonic() + 2)
    assert result["job_id"] == job_id and result["reader_sync"]["historical_pages_rewritten"] == 0
    assert calls == {"post": 1, "get": 2}

    ticks = iter((0.0, 1.0))
    pending = {"job_id": job_id, "status": "running"}
    with patch.object(brain, "_reader_page_token", return_value="unit"), \
         patch.object(brain, "_reader_request", side_effect=[(202, json.dumps(pending).encode("utf-8")), (200, json.dumps(pending).encode("utf-8"))]), \
         patch.object(brain.time, "monotonic", side_effect=lambda: next(ticks)), \
         patch.object(brain.time, "sleep"):
        result = brain._reader_refresh_job("http://127.0.0.1:54321", cfg, deadline=.5)
    assert result == {"status": "pending", "job_id": job_id}

    from urllib.error import HTTPError
    with patch.object(brain, "_reader_page_token", return_value="unit"), \
         patch.object(brain, "_reader_request", side_effect=[(202, json.dumps(pending).encode("utf-8")), HTTPError("receipt", 404, "missing", None, None)]):
        try:
            brain._reader_refresh_job("http://127.0.0.1:54321", cfg, deadline=time.monotonic() + 1)
        except ValueError as error:
            assert "receipt was not found" in str(error) and job_id in str(error)
        else:
            raise AssertionError("missing receipt was reported as ready or pending")


def assert_port_policy(path: Path) -> None:
    spec = importlib.util.spec_from_file_location("reader_port_policy", path)
    policy = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(policy)
    identity = "1234567890abcdef1234"
    ports = policy.candidate_ports(identity)
    primary = 49152 + int(identity[:6], 16) % 10000
    assert ports[:10] == tuple(range(primary, primary + 10))
    assert len(ports) > 10 and len(set(ports)) == len(ports)
    assert all(policy.LOWEST_PORT <= port <= policy.HIGHEST_PORT for port in ports)
    selected_ports = (50101, 50102, 50103)
    observations = []

    def delayed_probe(origin: str, timeout: float) -> dict | None:
        observations.append((origin, timeout))
        if origin.endswith(":50101"):
            time.sleep(.06)
            return {"app": "knowledge-nebula", "identity": "foreign"}
        if origin.endswith(":50102"):
            time.sleep(timeout)
            return None
        time.sleep(.02)
        return {"app": "knowledge-nebula", "identity": identity, "generation": "knowledge-nebula-server-v6"}

    started = time.monotonic()
    with patch.object(policy, "candidate_ports", return_value=selected_ports):
        origin, state = policy.find_reader_state(identity, delayed_probe, timeout=.15, deadline=started + .25)
    assert origin == "http://127.0.0.1:50103" and state and state["identity"] == identity
    assert len(observations) == len(selected_ports)
    assert time.monotonic() - started < .23, "dead ports must not be probed serially"

    def same_identity_probe(origin: str, timeout: float) -> dict:
        if origin.endswith(":50101"):
            time.sleep(.06)
            generation = "knowledge-nebula-server-v5"
        else:
            time.sleep(.01)
            generation = "knowledge-nebula-server-v6"
        return {"app": "knowledge-nebula", "identity": identity, "generation": generation}

    with patch.object(policy, "candidate_ports", return_value=selected_ports[:2]):
        origin, state = policy.find_reader_state(identity, same_identity_probe, timeout=.1)
    assert origin == "http://127.0.0.1:50101" and state and state["generation"].endswith("v5")

    def blocked_probe(origin: str, timeout: float) -> dict:
        time.sleep(timeout + .01)
        return {"app": "knowledge-nebula", "identity": identity}

    started = time.monotonic()
    with patch.object(policy, "candidate_ports", return_value=selected_ports[:2]):
        origin, state = policy.find_reader_state(identity, blocked_probe, timeout=.2, deadline=started + .03)
    assert origin is None and state is None
    assert time.monotonic() - started < .12, "reader discovery ignored its deadline"


def install_generation_fixture(workspace: Path, generation: str) -> Path:
    """Change only an isolated installed workspace into a newer reader fixture."""
    brain = workspace / "tools" / "brain.py"
    server = workspace / "tools" / "knowledge_nebula_server.py"
    for path in (brain, server):
        content = path.read_text(encoding="utf-8")
        match = re.search(r"knowledge-nebula-server-v[1-9]\d*", content)
        assert match is not None
        path.write_text(content[:match.start()] + generation + content[match.end():], encoding="utf-8")
    # This is an isolated test-only runtime upgrade.  Preserve the installed
    # integrity contract by rebinding just the two changed fixture bytes.
    config_path = workspace / "brain.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    for path in (brain, server):
        config["managed_files"][path.relative_to(workspace).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return brain


def assert_generation_upgrade(package: Path, work: Path, environment: dict[str, str], name: str) -> None:
    upgrade_workspace = work / name
    if upgrade_workspace.exists():
        raise AssertionError("upgrade fixture must be new; preserved evidence is never overwritten")
    run(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(upgrade_workspace),
        "--vault", "Upgrade Notes", "--display-name", "Reader upgrade", cwd=work)
    old_brain = upgrade_workspace / "tools" / "brain.py"
    old_home = run(str(old_brain), "open-home", cwd=upgrade_workspace, environment=environment)
    new_brain = install_generation_fixture(upgrade_workspace, "knowledge-nebula-server-v7")
    upgraded_first, upgraded_second = run_parallel([(str(new_brain), "open-home"), (str(new_brain), "open-home")], cwd=upgrade_workspace, environment=environment)
    assert sorted((upgraded_first["service"], upgraded_second["service"])) == ["reused", "started"]
    assert upgraded_first["url"] == upgraded_second["url"] == old_home["url"]
    assert upgraded_first["url"].endswith("/index.html#prompts")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--upgrade-only", action="store_true")
    args = parser.parse_args()
    work = args.work_root.resolve()
    if args.upgrade_only:
        if not args.package:
            raise SystemExit("--upgrade-only requires the already-built package path")
        package = args.package.resolve()
        if not package.is_dir():
            raise SystemExit("upgrade package is missing")
        if work.exists() and any(work.iterdir()):
            raise SystemExit("upgrade work root must be new or empty; existing evidence is preserved")
        work.mkdir(parents=True, exist_ok=True)
        environment = {"CREATIVE_SECOND_BRAIN_READER_IDLE_SECONDS": "15"}
        assert_generation_upgrade(package, work, environment, "upgrade-only")
        report = {"status": "passed", "checks": ["two real newer brain openers safely hand off one older same-root reader and reuse one successor URL"]}
        (work / "upgrade-only-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
        return 0
    if work.exists() and any(work.iterdir()):
        raise SystemExit("work root must be new or empty; existing evidence is preserved")
    work.mkdir(parents=True, exist_ok=True)
    if args.package:
        package = args.package.resolve()
    else:
        built = work / "build"
        result = subprocess.run([PYTHON, "-B", str(ROOT / "scripts" / "build_profiles.py"), "--output", str(built),
                                 "--profile", "public-portable"], cwd=ROOT, text=True, encoding="utf-8", capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        package = built / "public-portable"
    workspace = work / "my brain"
    run(str(package / "scripts" / "setup_workspace.py"), "--profile", "public-portable", "--workspace", str(workspace),
        "--vault", "My Notes", "--display-name", "Reader opening", cwd=work)
    brain = workspace / "tools" / "brain.py"
    assert_port_policy(workspace / "tools" / "reader_port_policy.py")
    installed_brain = load_brain(brain)
    assert_lifecycle_recovery(installed_brain)
    # One bounded concurrent sweep still needs enough idle time for its reader.
    environment = {"CREATIVE_SECOND_BRAIN_READER_IDLE_SECONDS": "15"}
    home_cold, home_parallel = run_parallel([(str(brain), "open-home"), (str(brain), "open-home")], cwd=workspace, environment=environment)
    server = workspace / "tools" / "knowledge_nebula_server.py"
    server_repeat = run(str(server), "--root", str(workspace), "--no-browser", "--page", "nebula",
                        "--require-generation", "knowledge-nebula-server-v6", cwd=workspace, environment=environment)
    nebula_repeat = run(str(brain), "nebula", cwd=workspace, environment=environment)
    home_repeat = run(str(brain), "open-home", cwd=workspace, environment=environment)
    assert home_cold["status"] == "ready" and home_parallel["status"] == "ready"
    assert sorted((home_cold["service"], home_parallel["service"])) == ["reused", "started"]
    assert home_parallel["url"] == home_cold["url"], "same workspace concurrent opening must reuse one listener"
    assert home_cold["url"].endswith("/index.html#prompts") and home_cold["fragment_verified"] is True
    assert home_cold["synchronization"]["home_projection"]["status"] == "home_refreshed"
    assert server_repeat["status"] == "already-running"
    assert server_repeat["url"].endswith("/My%20Notes/outcome-pages/nebula.html")
    assert nebula_repeat["status"] == "ready" and nebula_repeat["service"] == "reused"
    assert nebula_repeat["url"].endswith("/My%20Notes/outcome-pages/nebula.html")
    assert home_repeat["service"] == "reused" and home_repeat["synchronization"]["reader_sync"]["historical_pages_rewritten"] == 0
    home_page = fetch(home_cold["url"].split("#", 1)[0]).decode("utf-8")
    nebula_page = fetch(nebula_repeat["url"])
    assert 'id="prompts"' in home_page
    assert "$brain-think [問題]" in home_page
    assert "舊名相容" in home_page and "$brain-ask [問題]" in home_page
    assert "creative-second-brain-nebula-logo-v4.png" in home_page
    assert hashlib.sha256((workspace / "assets" / "brand" / "creative-second-brain-nebula-logo-v4.png").read_bytes()).hexdigest() == LOGO_SHA256
    assert home_page.count("創造型第二大腦 | Adam Chan") == 1
    assert nebula_page.decode("utf-8").count("創造型第二大腦 | Adam Chan") == 1
    assert b"knowledge-nebula" in nebula_page
    # A real supported upgrade first opens v5, then two newer brain entry
    # processes race to take over the same workspace.  The service lease and
    # authenticated handoff must yield one successor URL, never a fallback.
    assert_generation_upgrade(package, work, environment, "upgrade brain")
    origin = home_cold["url"].split("/index.html", 1)[0]
    time.sleep(17)
    try:
        fetch(origin + "/api/status")
    except (URLError, TimeoutError, OSError):
        closed = True
    else:
        closed = False
    assert closed, "the test-owned reader service did not close after its configured idle period"
    report = {"status": "passed", "checks": [
        "concurrent cold home openings reuse one bounded loopback service", "server entry reuses the existing nebula service", "repeat nebula reuses and synchronizes service",
        "slow readiness beyond 20 seconds preserves the child and returns a verified URL", "child startup exit has an exact safe diagnosis",
        "startup deadline emits no unverified URL",
        "busy reuse waits once and retry remains bounded",
        "shared reader port policy preserves primary ports and adds bounded deterministic fallbacks",
        "two real newer brain openers safely hand off one older same-root reader and reuse one successor URL",
        "repeat home returns verified #prompts URL", "canonical think command and its legacy-only alias are visible", "fresh installed nebula logo hash and home path",
        "task-owned short-lived service closes",
    ]}
    (work / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
