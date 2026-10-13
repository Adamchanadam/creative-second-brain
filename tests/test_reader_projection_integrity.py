"""Focused regression checks for reader projection publication and startup truth."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "runtime" / "workspace" / "tools"
sys.path.insert(0, str(TOOLS))

import knowledge_nebula_server as server
import knowledge_nebula
import outcome_catalog
import outcome_page_generator
import reader_shell
import thinking_archive


def catalogue_document(label: str) -> str:
    return (
        '<!doctype html><html><head><title>' + label + '</title></head><body>'
        '<main><h1>' + label + '</h1></main>'
        '<script type="application/json" id="catalog-data">{}</script></body></html>'
    )


def assert_catalogue_publication(work: Path) -> None:
    vault, output = work / "vault", work / "vault" / "outcome-pages"
    output.mkdir(parents=True)
    paths = outcome_catalog.write_catalog_pages([], output, 8, vault=vault)
    assert {path.name for path in paths} == {"index.html", "knowledge.html", "thinking.html"}
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert "<main" in content and 'id="catalog-data"' in content and "brain-site-nav" in content

    # A leftover file from an interrupted process is harmless: OS locking, not
    # exclusive file creation, decides whether the next writer may continue.
    (output / ".reader-projection.lock").write_text("former writer\n", encoding="utf-8")
    with reader_shell.catalogue_projection_lock(output):
        probe = (
            "from pathlib import Path; import sys; "
            "sys.path.insert(0, sys.argv[2]); import outcome_catalog; "
            "\ntry:\n outcome_catalog.write_catalog_pages([], Path(sys.argv[1]), 8, vault=Path(sys.argv[1]).parent)"
            "\nexcept ValueError as error:\n print(str(error))\nelse:\n raise SystemExit('writer was not excluded')"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe, str(output), str(TOOLS)],
            text=True, encoding="utf-8", errors="replace", capture_output=True, check=True,
        )
        assert "another reader projection is active" in result.stdout
    outcome_catalog.write_catalog_pages([], output, 8, vault=vault)

    # A read from the live filename observes a complete old document until the
    # replacement point.  Replacing this helper with path.write_text makes the
    # hook disappear and this test fail, so it detects a torn-write regression.
    page, old, new = output / "atomic.html", "<html><main>old</main></html>", "<html><main>new</main></html>"
    page.write_text(old, encoding="utf-8")
    observed = {"during": False}
    replace = reader_shell.os.replace

    def verify_replace(pending, target):
        observed["during"] = Path(target).read_text(encoding="utf-8") == old and Path(pending).read_text(encoding="utf-8") == new
        return replace(pending, target)

    with patch.object(reader_shell.os, "replace", side_effect=verify_replace):
        reader_shell.atomic_reader_write(page, new)
    assert observed["during"] and page.read_text(encoding="utf-8") == new


def assert_reproject_rejects_incomplete_and_stale_pages(work: Path) -> None:
    vault, output = work / "stale-vault", work / "stale-vault" / "outcome-pages"
    output.mkdir(parents=True)
    knowledge = output / "knowledge.html"
    knowledge.write_text("", encoding="utf-8")
    try:
        reader_shell.refresh_current_reader_visuals(vault)
    except ValueError as error:
        assert "reader page is empty" in str(error)
    else:
        raise AssertionError("an empty catalogue became a visual-only shell")
    assert knowledge.read_text(encoding="utf-8") == ""

    original, replacement = catalogue_document("before"), catalogue_document("newer")
    knowledge.write_text(original, encoding="utf-8")
    outer_write = reader_shell.atomic_reader_write
    writer_blocked = {"value": False}

    def actual_writer_between_compare_and_replace(page, rendered):
        try:
            thinking_archive.atomic_text(vault, Path(page), replacement)
        except ValueError as error:
            assert "another reader projection is active" in str(error)
            writer_blocked["value"] = True
        else:
            raise AssertionError("an actual thinking writer bypassed the shared shell lock")
        outer_write(page, rendered)

    with patch.object(reader_shell, "atomic_reader_write", side_effect=actual_writer_between_compare_and_replace):
        reader_shell.refresh_current_reader_visuals(vault)
    assert writer_blocked["value"] and "newer" not in knowledge.read_text(encoding="utf-8")
    thinking_archive.atomic_text(vault, knowledge, replacement)
    assert knowledge.read_text(encoding="utf-8") == replacement

    retired = output / "KS-VS-0001_outcome.html"
    retired.write_text('<a href="../taste-app.html">品味誌</a>', encoding="utf-8")
    assert outcome_page_generator.cleanup_retired_taste_routes(output) == [retired.resolve()]
    assert "taste-app" not in retired.read_text(encoding="utf-8")


def assert_server_never_reports_ready_after_reader_failure(work: Path) -> None:
    root, vault = work / "server-root", work / "server-root" / "vault"
    vault.mkdir(parents=True)
    (root / "brain.json").write_text(json.dumps({"vault": "vault"}), encoding="utf-8")

    class FakeServer:
        closed = False

        def __init__(self, root, port, idle):
            self.root = Path(root)
            self.page = Path(root) / "vault" / "outcome-pages" / "nebula.html"
            self.last_active = 0

        def server_close(self):
            self.closed = True

        def begin_write(self):
            return True

        def end_write(self):
            return None

        def refresh_projections(self):
            return server.refresh_reader_projections(self.root)

        def serve_until_idle(self):
            raise AssertionError("a failed reader projection was served as ready")

    def unavailable(*args, **kwargs):
        raise OSError("no existing local reader")

    with patch.object(server, "candidate_ports", return_value=(54321,)), \
         patch.object(server, "NebulaServer", FakeServer), \
         patch.object(server, "refresh_reader_projections", side_effect=ValueError("catalogue incomplete")), \
         patch.object(server, "build_opener", return_value=SimpleNamespace(open=unavailable)), \
         patch.object(sys, "argv", ["knowledge_nebula_server.py", "--root", str(root), "--no-browser"]):
        try:
            server.main()
        except ValueError as error:
            assert str(error) == "catalogue incomplete"
        else:
            raise AssertionError("reader startup emitted ready despite an incomplete catalogue")


def assert_refresh_build_ownership_and_receipts(work: Path) -> None:
    root, vault = work / "refresh-root", work / "refresh-root" / "vault"
    (vault / "outcome-pages").mkdir(parents=True)
    (root / "brain.json").write_text(json.dumps({"vault": "vault"}), encoding="utf-8")
    observed: dict[str, object] = {}

    def refresh_index(**kwargs):
        observed.update(kwargs)
        return {"status": "ready", "source_count": 1, "historical_pages_rewritten": 0}

    with patch.object(outcome_page_generator, "refresh_outcome_index", side_effect=refresh_index), \
         patch.object(reader_shell, "refresh_current_reader_visuals", return_value={}), \
         patch.object(knowledge_nebula, "build", return_value={"output": "vault/outcome-pages/nebula.html"}) as build:
        server.refresh_reader_projections(root)
    assert observed["defer_nebula"] is True and build.call_count == 1

    output = vault / "outcome-pages"
    with patch.object(outcome_page_generator, "research_catalog_projection", return_value={"topics": [], "materials": [], "snapshot": ""}), \
         patch.object(outcome_page_generator, "enrich_research_catalog_entries", return_value=[]), \
         patch.object(outcome_catalog, "write_catalog_pages", return_value=[output / "index.html"]), \
         patch.object(knowledge_nebula, "refresh_installed") as installed:
        outcome_page_generator.write_paginated_index([], output, 8, vault=vault)
    installed.assert_called_once_with(vault, output)

    reader = server.NebulaServer(root, 0, 60)
    entered, release = threading.Event(), threading.Event()
    calls = {"refresh": 0}

    def slow_refresh():
        calls["refresh"] += 1
        entered.set()
        assert release.wait(2)
        return {"status": "ready", "reader_sync": {"historical_pages_rewritten": 0}}

    try:
        with patch.object(reader, "refresh_projections", side_effect=slow_refresh):
            first, created = reader.submit_refresh_job("a" * 32)
            assert created and first["status"] == "pending"
            worker = threading.Thread(target=reader._run_refresh_job, args=("a" * 32,))
            worker.start()
            assert entered.wait(1)
            second, created_again = reader.submit_refresh_job("b" * 32)
            assert not created_again and second["job_id"] == "a" * 32 and second["status"] == "running"
            release.set()
            worker.join(2)
        assert calls == {"refresh": 1}
        assert reader.refresh_job("a" * 32)["status"] == "ready"
        assert reader.refresh_job("c" * 32) is None
        reader.retiring = True
        try:
            reader.submit_refresh_job("d" * 32)
        except ValueError as error:
            assert "retiring" in str(error)
        else:
            raise AssertionError("retiring reader accepted a new refresh receipt")
    finally:
        reader.server_close()


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        assert_catalogue_publication(work)
        assert_reproject_rejects_incomplete_and_stale_pages(work)
        assert_server_never_reports_ready_after_reader_failure(work)
        assert_refresh_build_ownership_and_receipts(work)
    print(json.dumps({"status": "passed", "checks": [
        "catalogues publish atomically and a reader observes a whole document during replacement",
        "empty catalogues are not wrapped as reader shells",
        "a true thinking writer cannot interleave after shell compare and before replacement",
        "outcome, thinking and retired-route writers use the common atomic writer and lock",
        "reader startup never emits ready after projection failure",
        "server refresh defers the catalogue-owned nebula build and performs one final build",
        "direct catalogue callers retain their default nebula build",
        "one refresh receipt survives a lost submit reply, rejects concurrent duplicate work and never reports an unknown receipt as ready",
    ]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
