"""Focused fail-closed checks for the portable Obsidian CLI adapter."""
from __future__ import annotations

import contextlib
import argparse
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "workspace" / "tools"))
sys.path.insert(0, str(ROOT / "scripts"))
import obsidian_cli_adapter as adapter
import brain
import setup_workspace


class Runner:
    def __init__(self, vault: Path, *, report: Path | None = None, mode: str = "normal"):
        self.vault = vault
        self.report = report or vault
        self.mode = mode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        assert kwargs["shell"] is False and kwargs["timeout"] == adapter.TIMEOUT_SECONDS
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(argv, adapter.TIMEOUT_SECONDS)
        if self.mode == "error":
            return subprocess.CompletedProcess(argv, 1, "", "error")
        if self.mode == "empty":
            return subprocess.CompletedProcess(argv, 0, "", "")
        tail = argv[2:]
        if tail == ["vault", "info=path"]:
            return subprocess.CompletedProcess(argv, 0, str(self.report), "")
        if tail[:1] == ["search"]:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if tail[:1] == ["read"]:
            return subprocess.CompletedProcess(argv, 0, "# note", "")
        if tail[:1] == ["open"]:
            return subprocess.CompletedProcess(argv, 0, "requested", "")
        raise AssertionError("unexpected Obsidian arguments: " + repr(tail))


def request(cli: Path, action: str, *arguments: str) -> list[str]:
    return ["--cli", str(cli), "--vault-id", "vault-123", action, *arguments]


def expect_blocked(call, phrase: str) -> None:
    try:
        call()
    except ValueError as error:
        assert phrase in str(error), str(error)
    else:
        raise AssertionError("expected a blocked adapter request")


def tree(vault: Path) -> dict[str, bytes]:
    return {path.relative_to(vault).as_posix(): path.read_bytes() for path in vault.rglob("*") if path.is_file()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-root", type=Path)
    args = parser.parse_args()
    temp_root = args.temp_root
    if temp_root is not None and not temp_root.is_dir():
        raise ValueError("--temp-root must be an existing test-owned directory")
    with tempfile.TemporaryDirectory(prefix="obsidian-adapter-", dir=temp_root) as temporary:
        root = Path(temporary)
        vault = root / "vault"; vault.mkdir()
        note = vault / "notes" / "safe.md"; note.parent.mkdir(); note.write_text("# note", encoding="utf-8")
        cli = root / "obsidian-cli.exe"; cli.write_bytes(b"portable-test-cli")
        runner = Runner(vault)
        before = tree(vault)
        with patch.object(adapter.subprocess, "run", side_effect=runner):
            assert adapter.execute(request(cli, "check"), vault=vault)["status"] == "ready"
            assert adapter.execute(request(cli, "read", "--path", "notes/safe.md"), vault=vault)["content"] == "# note"
            assert adapter.execute(request(cli, "search", "--query", "one --command=delete"), vault=vault)["matches"] == []
            opened = adapter.execute(request(cli, "open", "--path", "notes/safe.md"), vault=vault)
            assert opened["status"] == "requested" and opened["ui_verified"] is False
        assert tree(vault) == before, "adapter operations must never write the vault"
        assert all(call[1] == "vault=vault-123" for call in runner.calls)
        assert ["search", "query=one --command=delete", "format=json"] in [call[2:] for call in runner.calls]

        for invalid in ("../safe.md", "C:/outside.md", ".obsidian/app.json"):
            with patch.object(adapter.subprocess, "run", side_effect=Runner(vault)):
                expect_blocked(lambda: adapter.execute(request(cli, "read", "--path", invalid), vault=vault), "note path")
        with patch.object(adapter.subprocess, "run", side_effect=Runner(vault)):
            expect_blocked(lambda: adapter.execute(request(cli, "delete"), vault=vault), "invalid Obsidian adapter request")
            expect_blocked(lambda: adapter.execute(["--cli", str(cli), "--vault-id", "same name", "check"], vault=vault), "vault ID")
        with patch.object(adapter.subprocess, "run", side_effect=Runner(vault, report=root)):
            expect_blocked(lambda: adapter.execute(request(cli, "check"), vault=vault), "does not match")
        expect_blocked(lambda: adapter.execute(request(root / "missing.exe", "check"), vault=vault), "unavailable")
        for mode, phrase in (("timeout", "timed out"), ("error", "rejected"), ("empty", "no output")):
            with patch.object(adapter.subprocess, "run", side_effect=Runner(vault, mode=mode)):
                expect_blocked(lambda: adapter.execute(request(cli, "check"), vault=vault), phrase)
        with patch.object(adapter.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, str(vault), ""),
            subprocess.CompletedProcess([], 0, '"not a result list"', ""),
        ]):
            expect_blocked(lambda: adapter.execute(request(cli, "search", "--query", "safe"), vault=vault), "invalid JSON result")
        with patch.object(adapter.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, str(vault), ""),
            subprocess.CompletedProcess([], 0, "Error: missing", ""),
        ]):
            expect_blocked(lambda: adapter.execute(request(cli, "read", "--path", "notes/safe.md"), vault=vault), "did not return")
        note.write_text("# note\n", encoding="utf-8")
        with patch.object(adapter.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, str(vault), ""),
            subprocess.CompletedProcess([], 0, "# note\n", ""),
        ]):
            assert adapter.execute(request(cli, "read", "--path", "notes/safe.md"), vault=vault)["content"] == "# note\n"
        empty = vault / "notes" / "empty.md"; empty.write_text("", encoding="utf-8")
        with patch.object(adapter.subprocess, "run", side_effect=[
            subprocess.CompletedProcess([], 0, str(vault), ""),
            subprocess.CompletedProcess([], 0, "", ""),
        ]):
            assert adapter.execute(request(cli, "read", "--path", "notes/empty.md"), vault=vault)["content"] == ""
        with patch.object(adapter.subprocess, "run", side_effect=Runner(vault)):
            expect_blocked(lambda: adapter.execute(request(cli, "read", "--path", "."), vault=vault), "note path")
        linked = vault / "linked.md"; linked.write_text("not followed", encoding="utf-8")
        with patch.object(adapter, "_is_link_or_junction", side_effect=lambda path: path.name == "linked.md"):
            expect_blocked(lambda: adapter._safe_note(vault, "linked.md"), "links or junctions")

        runner = Runner(vault)
        output = io.StringIO()
        with patch.object(brain, "ROOT", root), patch.object(brain, "require_ready", return_value={"vault": "vault"}), patch.object(adapter.subprocess, "run", side_effect=runner), contextlib.redirect_stdout(output):
            assert brain.obsidian(request(cli, "check")) == 0
        assert json.loads(output.getvalue())["vault_target_verified"] is True
        generated = setup_workspace.skill_entry_payloads("public-portable")
        skill_files = [content.decode("utf-8") for path, content in generated.items() if path.endswith("SKILL.md") and not path.endswith("brain-ask/SKILL.md")]
        assert len(skill_files) == 14
        assert all("tools/brain.py obsidian" in content and "Graph 保留原生 UI 手動入口" in content for content in skill_files)
    print(json.dumps({"status": "passed", "coverage": ["explicit vault ID and info=path verification", "read search open check only", "wrong vault, missing CLI, timeout, error and empty output", "traversal, link and injection rejection", "no writes and brain/setup consumer chain"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
