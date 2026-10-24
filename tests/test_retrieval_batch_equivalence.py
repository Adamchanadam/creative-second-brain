from __future__ import annotations

import copy
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
from tools import knowledge_material_selector as selector  # noqa: E402


class RetrievalBatchEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir()) / ("retrieval-batch-" + uuid.uuid4().hex[:12]); self.root.mkdir(parents=True); self.vault = self.root / "vault"; self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        for source_id, title in (("KS-ALPHA", "Precise Alpha"), ("KS-BETA", "Careful Beta")):
            path = self.vault / "sources" / f"{source_id}.md"; app = self.vault / "applications" / f"APP-{source_id}.md"; path.parent.mkdir(parents=True, exist_ok=True); app.parent.mkdir(parents=True, exist_ok=True); (self.vault / "_registry").mkdir(exist_ok=True)
            path.write_text(f'---\nsource_id: "{source_id}"\ntitle: "{title}"\nsource_retention_mode: "full_body_required"\n---\n\n{title}', encoding="utf-8"); app.write_text(f'---\nsource_id: "{source_id}"\n---\n', encoding="utf-8")
            with (self.vault / "_registry" / "sources.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({"source_id": source_id, "source_note_path": path.relative_to(self.vault).as_posix(), "status": "linked"}) + "\n")
        for name in ("knowledge_points.jsonl", "relations.jsonl"): (self.vault / "_registry" / name).touch(exist_ok=True)

    def test_batch_matches_individual_selection_and_builds_one_resolver(self) -> None:
        queries = ["Precise Alpha", "Careful Beta"]; individual = [selector.select_materials(query=query, vault=self.vault, limit=2) for query in queries]
        with patch.object(selector, "PackageMatchResolver", wraps=selector.PackageMatchResolver) as resolver: batched = selector.select_materials_many(queries=queries, vault=self.vault, limit=2)
        for result in [*individual, *batched]: result.pop("generated_at", None)
        self.assertEqual(batched, individual); self.assertEqual(resolver.call_count, 1)


if __name__ == "__main__": unittest.main()
