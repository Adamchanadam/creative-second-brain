"""Focused PUBLIC portability boundaries for the 1.9.19 port."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "runtime" / "workspace" / "tools"
import sys
sys.path.insert(0, str(TOOLS))

import completion_revisions
import firecrawl_obsidian_pipeline as pipeline
import ingest_rail
from workspace_paths import WorkspacePathError, managed_vault_path, vault_path


def main() -> int:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        vault = root / "Notes" / "My Vault"
        vault.mkdir(parents=True)
        (root / "brain.json").write_text(json.dumps({"vault": "Notes/My Vault"}), encoding="utf-8")
        previous = os.environ.get("CREATIVE_SECOND_BRAIN_VAULT")
        os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = "Notes/My Vault"
        try:
            assert managed_vault_path(root) == vault.resolve()
            assert ingest_rail.vault_dir(root) == vault.resolve()
            (vault / "_registry").mkdir()
            (vault / "_registry" / "sources.jsonl").write_text('{"source_id":"KS-VS-0042"}\n', encoding="utf-8")
            assert pipeline.next_source_id(root) == "KS-VS-0043"
            note = vault / "knowledge-points" / "by-source" / "KS-VS-0042" / "KP-01.md"
            note.parent.mkdir(parents=True)
            note.write_text("# note\n", encoding="utf-8")
            relative, resolved, source_id, vault_name = completion_revisions._kp_note(root, note)
            assert relative == "Notes/My Vault/knowledge-points/by-source/KS-VS-0042/KP-01.md"
            assert resolved == note and source_id == "KS-VS-0042" and vault_name == "Notes/My Vault"
            for invalid in ("../outside", "C:/outside"):
                try:
                    vault_path(root, invalid)
                except WorkspacePathError:
                    pass
                else:
                    raise AssertionError(f"accepted outside vault: {invalid}")
        finally:
            if previous is None:
                os.environ.pop("CREATIVE_SECOND_BRAIN_VAULT", None)
            else:
                os.environ["CREATIVE_SECOND_BRAIN_VAULT"] = previous
    print(json.dumps({"status": "passed", "checks": ["nested custom vault", "next source ID", "completed note path", "outside vault rejection"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
