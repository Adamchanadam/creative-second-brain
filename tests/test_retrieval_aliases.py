from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir(): ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from tools import ingest_plan_contract as contract  # noqa: E402
from tools import knowledge_material_selector as selector  # noqa: E402


class RetrievalAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()) / ("aliases-" + uuid.uuid4().hex[:12]); self.root.mkdir(parents=True); self.vault = self.root / "vault"; self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_contract_rejects_invalid_aliases(self) -> None:
        aliases = ["x" * 81, "line\nbreak", "[[link]]", "has|pipe", "Source title", "Duplicate", "duplicate"]
        errors = contract.retrieval_alias_errors({"retrieval_aliases": aliases}, "Source title", path="source_metadata")
        self.assertTrue(errors); self.assertTrue(all(error.startswith("retrieval_alias_invalid:") for error in errors))

    def test_alias_ranks_as_source_title(self) -> None:
        self.write_source("KS-ALIAS", "General notes", ["Learning How to Learn"]); self.write_source("KS-DECOY", "Unrelated", [])
        result = selector.select_materials(query="Learning How to Learn", vault=self.vault, limit=3)
        self.assertEqual(result["candidates"][0]["source_id"], "KS-ALIAS")

    def test_nested_vault_matches_package_canonical_original(self) -> None:
        vault = self.root / "notes" / "vault"
        source = vault / "sources" / "KS-NESTED.md"
        application = vault / "applications" / "APP-KS-NESTED.md"
        registry = vault / "_registry"
        package = self.root / "outputs" / "compiler_worker_v2" / "nested-package"
        source.parent.mkdir(parents=True, exist_ok=True); application.parent.mkdir(parents=True, exist_ok=True); registry.mkdir(parents=True, exist_ok=True); package.mkdir(parents=True, exist_ok=True)
        source.write_text("---\nsource_id: KS-NESTED\ntitle: Ordinary summary\nsource_retention_mode: summarized_allowed\n---\n\nbrief notes", encoding="utf-8")
        application.write_text("---\nsource_id: KS-NESTED\n---\n", encoding="utf-8")
        (registry / "sources.jsonl").write_text(json.dumps({"source_id": "KS-NESTED", "source_note_path": "sources/KS-NESTED.md", "status": "linked"}) + "\n", encoding="utf-8")
        (registry / "relations.jsonl").touch()
        (package / "input_bundle.json").write_text(json.dumps({"source_retention_mode": "summarized_allowed"}), encoding="utf-8")
        (package / "canonical_source.md").write_text("quartzumbra uniquely retained original", encoding="utf-8")
        with patch.object(selector, "completed_v2_package_for_standard_outcome", return_value=package / "knowledge_package.json"):
            result = selector.select_materials(query="quartzumbra", vault=vault, limit=3)
        self.assertIn("KS-NESTED", [candidate["source_id"] for candidate in result["candidates"]])

    def write_source(self, source_id: str, title: str, aliases: list[str]) -> None:
        source = self.vault / "sources" / f"{source_id}.md"; app = self.vault / "applications" / f"APP-{source_id}.md"; registry = self.vault / "_registry"
        source.parent.mkdir(parents=True, exist_ok=True); app.parent.mkdir(parents=True, exist_ok=True); registry.mkdir(parents=True, exist_ok=True)
        aliases_yaml = "" if not aliases else "retrieval_aliases:\n" + "".join(f"  - {json.dumps(item)}\n" for item in aliases)
        source.write_text(f"---\nsource_id: {source_id}\ntitle: {json.dumps(title)}\nsource_retention_mode: full_body_required\n{aliases_yaml}---\n\nbody", encoding="utf-8")
        app.write_text(f"---\nsource_id: {source_id}\n---\n", encoding="utf-8")
        with (registry / "sources.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"source_id": source_id, "source_note_path": source.relative_to(self.vault).as_posix(), "status": "linked"}) + "\n")
        (registry / "relations.jsonl").touch(exist_ok=True)


if __name__ == "__main__": unittest.main()
