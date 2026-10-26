"""Every reader surface uses the declared source-preview rule."""
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
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import knowledge_nebula  # noqa: E402
from tools.outcome_page_generator import outcome_index_entry  # noqa: E402
from tools.source_preview import source_preview_files  # noqa: E402


SOURCE_ID = "KS-VS-8801"


class SourcePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        self.root = base / ("source-preview-" + uuid.uuid4().hex[:12])
        self.vault = self.root / "vault"
        self.source = self.vault / "sources" / f"{SOURCE_ID}_fixture.md"
        self.application = self.vault / "applications" / f"APP-{SOURCE_ID}_fixture.md"
        self.page = self.vault / "outcome-pages" / f"{SOURCE_ID}_outcome.html"
        for path in (self.source, self.application, self.page):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def image(self, name: str) -> str:
        path = self.vault / "assets" / SOURCE_ID.lower() / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"declared image fixture")
        return path.relative_to(self.vault).as_posix()

    def write_notes(self, **overrides: object) -> None:
        fields: dict[str, object] = {
            "source_id": SOURCE_ID,
            "title": "來源預覽規則",
            "summary": "讀者只可用同一來源已宣告、仍在 vault 內的圖片。",
            "source_url": "https://example.test/preview",
            "source_input_kind": "url",
            "source_identity": "url:" + "a" * 64,
            "capture_date": "2026-10-25",
            "asset_mode": "knowledge_only",
            "infographics": [],
            "source_assets": [],
            "source_attachments": [],
        }
        fields.update(overrides)
        lines = ["---"]
        for key, value in fields.items():
            if isinstance(value, list):
                lines.append(f"{key}:")
                lines.extend("  - " + json.dumps(item, ensure_ascii=False) for item in value)
            else:
                lines.append(f"{key}: " + json.dumps(value, ensure_ascii=False))
        self.source.write_text("\n".join(lines) + "\n---\n\n## TL;DR\n\n預覽測試。\n", encoding="utf-8")
        self.application.write_text(
            "---\nsource_id: \"KS-VS-8801\"\nadoption_status: \"has_insight\"\n"
            "ai_review_confidence: \"高\"\ntaste_network_status: \"not_linked\"\n"
            "compounding_insight_status: \"needs_context\"\n---\n\n# 應用\n",
            encoding="utf-8",
        )
        self.page.write_text("<!doctype html><main>fixture</main>\n", encoding="utf-8")

    def test_selection_orders_declared_images_and_rejects_invalid_paths(self) -> None:
        infographic = self.image("infographic.PNG")
        original = self.image("original.JpG")
        attachment = self.image("attachment.gif")
        self.image("not-an-image.txt")
        self.write_notes(
            infographics=["../escape.png", "assets/ks-vs-8801/missing.webp", infographic],
            source_assets=[original, "assets/ks-vs-8801/not-an-image.txt"],
            source_attachments=[attachment, infographic],
        )
        previews = source_preview_files(
            self.vault,
            {
                "infographics": ["../escape.png", infographic],
                "source_assets": [original, "assets/ks-vs-8801/not-an-image.txt"],
                "source_attachments": [attachment, infographic],
            },
        )
        self.assertEqual([(item.path.name, item.kind) for item in previews], [
            ("infographic.PNG", "資訊圖"),
            ("original.JpG", "原始圖片"),
            ("attachment.gif", "來源附圖"),
        ])

    def test_original_source_image_is_used_by_outcome_index_and_nebula(self) -> None:
        original = self.image("original.jpeg")
        self.write_notes(source_assets=[original])
        entry = outcome_index_entry(SOURCE_ID, self.vault, self.page)
        self.assertEqual(entry["asset_path"], self.vault / original)
        previews = knowledge_nebula.preview_files(
            self.vault,
            {"infographics": [], "source_assets": [original], "source_attachments": []},
        )
        self.assertEqual(
            [(path.relative_to(self.vault).as_posix(), kind) for path, kind in previews],
            [(original, "原始圖片")],
        )


if __name__ == "__main__":
    unittest.main()
