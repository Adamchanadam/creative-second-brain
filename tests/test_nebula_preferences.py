"""Focused loopback checks for vault-scoped Nebula reader preferences."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import threading
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def load_server(path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("nebula_preferences_server", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def request(server, path: str, *, payload=None, token=True):
    headers = {"Origin": server.origin}
    if token:
        headers["X-Nebula-Token"] = server.token
    if payload is not None:
        headers["Content-Type"] = "application/json"
        payload = json.dumps(payload).encode("utf-8")
    opener = build_opener(ProxyHandler({}))
    return opener.open(Request(server.origin + path, data=payload, headers=headers), timeout=3)


def start(module, root: Path):
    server = module.NebulaServer(root, 0, idle_seconds=30)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(server, thread):
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def expect_http(status, action):
    try:
        action()
    except HTTPError as error:
        assert error.code == status
    else:
        raise AssertionError(f"expected HTTP {status}")


def workspace(root: Path, name: str) -> Path:
    target = root / name
    vault = target / "My Notes"
    vault.mkdir(parents=True)
    (target / "brain.json").write_text(json.dumps({"vault": "My Notes"}), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tools", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    args = parser.parse_args()
    scratch = args.scratch.resolve()
    if scratch.exists() and any(scratch.iterdir()):
        raise SystemExit("scratch must be new or empty")
    scratch.mkdir(parents=True, exist_ok=True)
    module = load_server(args.tools / "knowledge_nebula_server.py")
    root = workspace(scratch, "first")
    server, thread = start(module, root)
    checks = []
    try:
        expect_http(403, lambda: request(server, "/api/preferences", token=False))
        checks.append("preference API rejects requests without the per-service token")
        before = module.reader_input_stamp(root)
        initial = json.load(request(server, "/api/preferences"))["preferences"]
        assert initial == module.DEFAULT_PREFERENCES
        saved = {"palette": "amber", "reduced_motion": True, "entrance_enabled": False}
        result = json.load(request(server, "/api/preferences", payload=saved))["preferences"]
        assert result == {"schema": module.PREFERENCE_SCHEMA, **saved}
        assert module.reader_input_stamp(root) == before
        checks.append("preference write is vault-scoped and excluded from reader refresh inputs")
        expect_http(400, lambda: request(server, "/api/preferences", payload={"palette": "invalid", "reduced_motion": True, "entrance_enabled": True}))
        expect_http(400, lambda: request(server, "/api/preferences", payload={"palette": ["amber"], "reduced_motion": True, "entrance_enabled": True}))
        assert json.load(request(server, "/api/preferences"))["preferences"] == result
        checks.append("invalid preference payload leaves saved record unchanged")
        path = module.preference_path(server.vault)
        assert path.parent.name == ".reader" and path.is_file() and not list(path.parent.glob("*.tmp"))
    finally:
        stop(server, thread)
    second, second_thread = start(module, root)
    try:
        assert json.load(request(second, "/api/preferences"))["preferences"]["palette"] == "amber"
        assert second.server_port != server.server_port
        checks.append("preferences survive restart on a different loopback port")
    finally:
        stop(second, second_thread)
    isolated_root = workspace(scratch, "isolated")
    isolated, isolated_thread = start(module, isolated_root)
    try:
        assert json.load(request(isolated, "/api/preferences"))["preferences"] == module.DEFAULT_PREFERENCES
        checks.append("a different vault cannot inherit preferences")
    finally:
        stop(isolated, isolated_thread)
    corrupt_root = workspace(scratch, "corrupt")
    corrupt_path = corrupt_root / "My Notes" / ".reader" / "nebula-preferences.json"
    corrupt_path.parent.mkdir()
    corrupt_path.write_text("not-json", encoding="utf-8")
    corrupt, corrupt_thread = start(module, corrupt_root)
    try:
        original = corrupt_path.read_bytes()
        expect_http(422, lambda: request(corrupt, "/api/preferences"))
        expect_http(400, lambda: request(corrupt, "/api/preferences", payload={"palette": "violet", "reduced_motion": False, "entrance_enabled": True}))
        assert corrupt_path.read_bytes() == original
        checks.append("a corrupt saved record is reported and never replaced by defaults")
    finally:
        stop(corrupt, corrupt_thread)
    print(json.dumps({"status": "passed", "checks": checks}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
