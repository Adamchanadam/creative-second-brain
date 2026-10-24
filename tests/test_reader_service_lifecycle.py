"""Focused process and safety checks for the single-workspace reader lease."""
from __future__ import annotations

import importlib.util
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "runtime" / "workspace" / "tools"
PYTHON = sys.executable


def load(name: str, path: Path):
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


lease_module = load("reader_service_lease_test", TOOLS / "reader_service_lease.py")
server_module = load("reader_service_server_test", TOOLS / "knowledge_nebula_server.py")


def generation_after(steps: int) -> str:
    """A generation newer than the shipped server by ``steps`` (never hard-coded)."""
    current = int(server_module.SERVICE_GENERATION.rsplit("-v", 1)[1])
    return "knowledge-nebula-server-v" + str(current + steps)


def make_workspace(base: Path, name: str) -> Path:
    root = base / name
    vault = root / "notes"
    (vault / "outcome-pages").mkdir(parents=True)
    (vault / "outcome-pages" / "nebula.html").write_text(
        '<!doctype html><main class="knowledge-nebula-page"></main><script>const LOCAL_SYNC=null;</script>', encoding="utf-8"
    )
    (root / "brain.json").write_text(json.dumps({"vault": "notes"}), encoding="utf-8")
    return root


def assert_process_leases(base: Path) -> None:
    first = make_workspace(base, "one")
    second = make_workspace(base, "two")
    held = lease_module.ReaderLease(first, "service")
    assert held.acquire()
    probe = (
        "import sys;sys.path.insert(0,sys.argv[1]);from reader_service_lease import ReaderLease;"
        "from pathlib import Path;l=ReaderLease(Path(sys.argv[2]),'service');print('held' if l.acquire() else 'blocked');l.release() if l.handle else None"
    )
    result = subprocess.run([PYTHON, "-B", "-c", probe, str(TOOLS), str(first)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "blocked"
    other = lease_module.ReaderLease(second, "service")
    assert other.acquire(), "a different canonical workspace must retain its own service lease"
    other.release()
    held.release()
    # A child that exits without release emulates a crash; OS locking must not
    # leave a stale PID marker that blocks the next normal launch.
    crash_probe = (
        "import os,sys;sys.path.insert(0,sys.argv[1]);from reader_service_lease import ReaderLease;"
        "from pathlib import Path;l=ReaderLease(Path(sys.argv[2]),'service');"
        "assert l.acquire();print('held',flush=True);os._exit(0)"
    )
    result = subprocess.run([PYTHON, "-B", "-c", crash_probe, str(TOOLS), str(first)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0 and result.stdout.strip() == "held"
    recovered = lease_module.ReaderLease(first, "service")
    assert recovered.acquire()
    recovered.release()


def request(url: str, body: bytes | None = None, headers: dict[str, str] | None = None):
    return build_opener(ProxyHandler({})).open(Request(url, data=body, headers=headers or {}, method="POST" if body is not None else "GET"), timeout=5)


def assert_handoff_safety(root: Path) -> None:
    server = server_module.NebulaServer(root, 0, 20)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        origin = server.origin
        try:
            request(origin + "/api/handoff", b'{}', {"Origin": origin})
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("unauthenticated handoff was accepted")
        assert server.retiring is False
        page = request(origin + "/notes/outcome-pages/nebula.html").read().decode("utf-8")
        token = json.loads(re.search(r"const LOCAL_SYNC=(\{.*?\});", page).group(1))["token"]
        try:
            request(origin + "/api/handoff", json.dumps({"generation": "knowledge-nebula-server-v4"}).encode(), {
                "Origin": origin, "X-Nebula-Token": token, "Content-Type": "application/json",
            })
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("reader accepted a downgrade handoff")
        assert server.retiring is False
        assert server.begin_write()
        done = threading.Event()
        result = []
        current_number = int(server_module.SERVICE_GENERATION.rsplit("-v", 1)[1])
        newer_generation = f"knowledge-nebula-server-v{current_number + 1}"
        def request_handoff():
            response = request(origin + "/api/handoff", json.dumps({"generation": newer_generation}).encode(), {
                "Origin": origin, "X-Nebula-Token": token, "Content-Type": "application/json",
            })
            result.append((response.status, json.loads(response.read().decode("utf-8"))))
            done.set()
        worker = threading.Thread(target=request_handoff, daemon=True)
        worker.start()
        time.sleep(.05)
        assert not done.is_set(), "handoff skipped an admitted write"
        assert server.begin_write() is False, "handoff admitted a new write after closing the gate"
        server.end_write()
        worker.join(timeout=2)
        assert done.is_set() and result == [(200, {"status": "retiring", "generation": server_module.SERVICE_GENERATION})]
        assert server.retiring is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert server.begin_write() is False, "closed listener admitted a late write"


def assert_legacy_is_not_replaced(root: Path) -> None:
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    brain = load("reader_service_brain_test", ROOT / "scripts" / "brain.py")
    cfg = {"vault": "notes", "entry_contract": []}
    with patch.object(brain, "require_ready", return_value=cfg), \
         patch.object(brain, "ROOT", root), \
         patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
         patch.object(brain, "_reader_ports", return_value=(54321,)), \
         patch.object(brain, "_find_reader_state", return_value=("http://127.0.0.1:54321", {"generation": "knowledge-nebula-server-v4"})), \
         patch.object(brain, "_handoff_existing_reader", side_effect=ValueError("existing reader is an older generation without safe handoff; close it through an authorized migration, then retry")), \
         patch.object(brain.subprocess, "Popen") as popen:
        try:
            brain.open_reader("nebula")
        except ValueError as error:
            assert "older generation without safe handoff" in str(error)
        else:
            raise AssertionError("legacy reader was silently replaced")
    popen.assert_not_called()


def assert_newer_reader_is_not_replaced(root: Path) -> None:
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    brain = load("reader_service_newer_test", ROOT / "scripts" / "brain.py")
    cfg = {"vault": "notes", "entry_contract": []}
    with patch.object(brain, "require_ready", return_value=cfg), \
         patch.object(brain, "ROOT", root), \
         patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
         patch.object(brain, "_reader_ports", return_value=(54321,)), \
         patch.object(brain, "_find_reader_state", return_value=("http://127.0.0.1:54321", {"generation": generation_after(1)})), \
         patch.object(brain, "_handoff_existing_reader") as handoff, \
         patch.object(brain.subprocess, "Popen") as popen:
        try:
            brain.open_reader("nebula")
        except ValueError as error:
            assert "downgrade handoff" in str(error)
        else:
            raise AssertionError("newer reader was silently replaced")
    handoff.assert_not_called()
    popen.assert_not_called()


def assert_concurrent_handoff_waits(root: Path) -> None:
    """A second opener seeing 409 waits for the same root to retire, not a new port."""
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    brain = load("reader_service_handoff_wait_test", ROOT / "scripts" / "brain.py")
    cfg = {"vault": "notes", "entry_contract": []}
    clock = {"now": 0.0}
    emitted: list[dict] = []
    states = iter((
        ("http://127.0.0.1:54321", {"generation": "knowledge-nebula-server-v4", "retiring": True}),
        ("http://127.0.0.1:54321", {"generation": "knowledge-nebula-server-v4", "retiring": True}),
        ("http://127.0.0.1:54321", {"generation": brain._READER_SERVICE_GENERATION, "retiring": False}),
    ))

    class AliveProcess:
        def poll(self): return None

    with patch.object(brain, "require_ready", return_value=cfg), \
         patch.object(brain, "ROOT", root), \
         patch.object(brain, "_reader_origin", return_value=("0123456789abcdef0123", "54321")), \
         patch.object(brain, "_reader_ports", return_value=(54321,)), \
         patch.object(brain, "_find_reader_state", side_effect=lambda *args, **kwargs: next(states)), \
         patch.object(brain, "_handoff_existing_reader", return_value=False) as handoff, \
         patch.object(brain, "_refresh_home_projection", return_value={"status": "home_refreshed"}), \
         patch.object(brain, "_reader_refresh_job", return_value={"status": "unchanged"}), \
         patch.object(brain.subprocess, "Popen", return_value=AliveProcess()) as popen, \
         patch.object(brain, "_reader_request", return_value=(200, b'<main id="prompts"></main>')), \
         patch.object(brain.time, "monotonic", side_effect=lambda: clock["now"]), \
         patch.object(brain.time, "sleep", side_effect=lambda seconds: clock.__setitem__("now", clock["now"] + seconds)), \
         patch.object(brain, "emit", side_effect=lambda value: emitted.append(value) or 0):
        assert brain.open_reader("home") == 0
    handoff.assert_called_once()
    popen.assert_not_called(), "a concurrent opener must reuse the successor, never bind another port"
    assert emitted[-1]["service"] == "reused"


def assert_handoff_transport_race_is_rechecked() -> None:
    if str(TOOLS) not in sys.path:
        sys.path.insert(0, str(TOOLS))
    brain = load("reader_service_transport_race_test", ROOT / "scripts" / "brain.py")
    with patch.object(brain, "_reader_page_token", side_effect=URLError("connection refused")):
        assert brain._handoff_existing_reader("http://127.0.0.1:54321", {"vault": "notes"}) is False
    with patch.object(brain, "_reader_page_token", return_value="fixture-token"), \
         patch.object(brain, "_reader_request", side_effect=URLError("connection refused")):
        assert brain._handoff_existing_reader("http://127.0.0.1:54321", {"vault": "notes"}) is False


def _start_process_reader(root: Path, generation: str) -> tuple[subprocess.Popen, str]:
    """Run the real server plus its service lease in a child process."""
    runner = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from pathlib import Path;import knowledge_nebula_server as server;"
        "from reader_service_lease import ReaderLease;"
        "root=Path(sys.argv[2]);server.SERVICE_GENERATION=sys.argv[3];"
        "lease=ReaderLease(root,'service');assert lease.acquire();"
        "instance=server.NebulaServer(root,0,30);"
        "print(json.dumps({'origin':instance.origin,'generation':server.SERVICE_GENERATION}),flush=True);"
        "\ntry: instance.serve_until_idle()\nfinally: instance.server_close();lease.release()"
    )
    process = subprocess.Popen([PYTHON, "-B", "-c", runner, str(TOOLS), str(root), generation],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    received: list[str] = []
    reader = threading.Thread(target=lambda: received.append(process.stdout.readline()), daemon=True)
    reader.start(); reader.join(timeout=8)
    if reader.is_alive():
        process.kill(); process.wait(timeout=5)
        raise AssertionError("real reader process did not expose a local origin")
    if not received[0]:
        stderr = process.stderr.read()
        raise AssertionError("real reader process failed before readiness: " + stderr)
    value = json.loads(received[0])
    return process, value["origin"]


def assert_real_generation_handoff(root: Path) -> None:
    # The retiring child keeps the module's current generation: its handoff
    # check compares against the generation bound when the module loaded.
    old_generation = server_module.SERVICE_GENERATION
    new_generation = "knowledge-nebula-server-v" + str(int(old_generation.rsplit("-v", 1)[1]) + 1)
    old, old_origin = _start_process_reader(root, old_generation)
    page = request(old_origin + "/notes/outcome-pages/nebula.html").read().decode("utf-8")
    token = json.loads(re.search(r"const LOCAL_SYNC=(\{.*?\});", page).group(1))["token"]
    retiring = request(old_origin + "/api/handoff", json.dumps({"generation": new_generation}).encode(), {
        "Origin": old_origin, "X-Nebula-Token": token, "Content-Type": "application/json",
    })
    assert json.loads(retiring.read().decode("utf-8"))["status"] == "retiring"
    assert old.wait(timeout=6) == 0, "retiring server did not release its service lease"
    new, new_origin = _start_process_reader(root, new_generation)
    status = json.loads(request(new_origin + "/api/status").read().decode("utf-8"))
    assert status["generation"] == new_generation and status["retiring"] is False
    page = request(new_origin + "/notes/outcome-pages/nebula.html").read().decode("utf-8")
    token = json.loads(re.search(r"const LOCAL_SYNC=(\{.*?\});", page).group(1))["token"]
    request(new_origin + "/api/handoff", json.dumps({"generation": generation_after(2)}).encode(), {
        "Origin": new_origin, "X-Nebula-Token": token, "Content-Type": "application/json",
    }).read()
    assert new.wait(timeout=6) == 0


def _retire_process_reader(process: subprocess.Popen, origin: str, target_generation: str) -> None:
    page = request(origin + "/notes/outcome-pages/nebula.html").read().decode("utf-8")
    token = json.loads(re.search(r"const LOCAL_SYNC=(\{.*?\});", page).group(1))["token"]
    request(origin + "/api/handoff", json.dumps({"generation": target_generation}).encode(), {
        "Origin": origin, "X-Nebula-Token": token, "Content-Type": "application/json",
    }).read()
    assert process.wait(timeout=6) == 0


def assert_real_roots_are_independent(base: Path) -> None:
    first = make_workspace(base, "listener-one")
    second = make_workspace(base, "listener-two")
    first_process, first_origin = _start_process_reader(first, server_module.SERVICE_GENERATION)
    second_process, second_origin = _start_process_reader(second, server_module.SERVICE_GENERATION)
    first_status = json.loads(request(first_origin + "/api/status").read().decode("utf-8"))
    second_status = json.loads(request(second_origin + "/api/status").read().decode("utf-8"))
    assert first_origin != second_origin and first_status["identity"] != second_status["identity"]
    _retire_process_reader(first_process, first_origin, generation_after(1))
    still_running = json.loads(request(second_origin + "/api/status").read().decode("utf-8"))
    assert still_running == second_status
    _retire_process_reader(second_process, second_origin, generation_after(1))


def assert_direct_server_does_not_reuse_retiring(root: Path) -> None:
    """The direct server route must not call a retiring listener reusable."""
    class Opener:
        def open(self, *args, **kwargs):
            return io.BytesIO(json.dumps({
                "app": "knowledge-nebula", "identity": hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:20],
                "generation": server_module.SERVICE_GENERATION, "retiring": True,
            }).encode("utf-8"))
    with patch.object(server_module, "candidate_ports", return_value=(54321,)), \
         patch.object(server_module, "build_opener", return_value=Opener()), \
         patch.object(sys, "argv", ["knowledge_nebula_server.py", "--root", str(root), "--no-browser"]):
        try:
            server_module.main()
        except SystemExit as error:
            assert "正在安全接替" in str(error)
        else:
            raise AssertionError("direct server treated a retiring listener as reusable")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="reader-service-lifecycle-") as temporary:
        base = Path(temporary)
        assert_process_leases(base)
        workspace = make_workspace(base, "handoff")
        assert_handoff_safety(workspace)
        workspace = make_workspace(base, "handoff-wait")
        assert_concurrent_handoff_waits(workspace)
        assert_handoff_transport_race_is_rechecked()
        workspace = make_workspace(base, "process-handoff")
        assert_real_generation_handoff(workspace)
        assert_real_roots_are_independent(base)
        workspace = make_workspace(base, "direct-retiring")
        assert_direct_server_does_not_reuse_retiring(workspace)
        workspace = make_workspace(base, "legacy")
        assert_legacy_is_not_replaced(workspace)
        workspace = make_workspace(base, "newer")
        assert_newer_reader_is_not_replaced(workspace)
    print(json.dumps({"status": "passed", "checks": [
        "same root OS service lease blocks a concurrent process",
        "different roots retain independent leases",
        "crashed holder recovers without stale PID cleanup",
        "unauthenticated handoff is rejected",
        "handoff closes write admission and drains an admitted write",
        "legacy v4 is not silently replaced or moved to another port",
        "newer reader is never asked to retire for a downgrade",
        "a concurrent handoff waits for same-root retirement before starting once",
        "handoff transport race returns to bounded same-root discovery",
        "real v5 service drains and releases before real v6 successor binds the same root",
        "two real roots retain separate listeners when either other root retires",
        "direct server rejects a retiring same-root listener instead of claiming reuse",
    ]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
