from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir(): ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
from tools import knowledge_material_selector as selector  # noqa: E402


class RetrievalRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()) / ("retrieval-ranking-" + uuid.uuid4().hex[:12]); self.root.mkdir(parents=True); self.vault = self.root / "vault"; self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write_source(self, source_id: str, title: str, body: str = "") -> None:
        source = self.vault / "sources" / f"{source_id}.md"; app = self.vault / "applications" / f"APP-{source_id}.md"; registry = self.vault / "_registry"
        source.parent.mkdir(parents=True, exist_ok=True); app.parent.mkdir(parents=True, exist_ok=True); registry.mkdir(parents=True, exist_ok=True)
        source.write_text(f'---\nsource_id: "{source_id}"\ntitle: "{title}"\nsource_retention_mode: "full_body_required"\n---\n\n{body}', encoding="utf-8"); app.write_text(f'---\nsource_id: "{source_id}"\n---\n', encoding="utf-8")
        with (registry / "sources.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({"source_id": source_id, "source_note_path": source.relative_to(self.vault).as_posix(), "status": "linked"}) + "\n")
        for name in ("knowledge_points.jsonl", "relations.jsonl"): (registry / name).touch(exist_ok=True)

    def test_stopwords_and_inflections_keep_expected_candidates(self) -> None:
        self.write_source("KS-STOP", "plain notes", "said it to the team"); self.write_source("KS-INFLECT", "change notes", "becoming reliable")
        self.assertEqual(selector.select_materials(query="the to", vault=self.vault)["candidate_count"], 0)
        self.assertEqual(selector.select_materials(query="AI", vault=self.vault)["candidate_count"], 0)
        self.assertEqual(selector.select_materials(query="become", vault=self.vault)["candidates"][0]["source_id"], "KS-INFLECT")
        self.assertEqual(selector.selection_tokens("怎樣學習得更有效？"), ["學習", "有效"])

    def test_exact_title_ranks_first(self) -> None:
        self.write_source("KS-TITLE", "Exact Short Alpha Plan", "short evidence"); self.write_source("KS-OTHER", "general notes", "other")
        self.assertEqual(selector.select_materials(query="Exact Short Alpha Plan", vault=self.vault)["candidates"][0]["source_id"], "KS-TITLE")


if __name__ == "__main__": unittest.main()
