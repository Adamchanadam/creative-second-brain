"""The outcome page keeps the retained original source verbatim (v1.9.25.1).

A retained source line that ends in a space is content, not layout: page clean-up must trim trailing whitespace
everywhere except inside the note's code blocks.  Ported from the OPS outcome-page generator checks; synthetic fixtures only.
"""
from __future__ import annotations

import html
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))

from tools.outcome_page_generator import generate_outcome_page, strip_trailing_whitespace  # noqa: E402

SCRATCH = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())


def write_fixture(vault: Path, source_id: str, title: str) -> None:
    safe_id = source_id.lower()
    asset = vault / "assets" / safe_id / "source.png"
    source = vault / "sources" / "2026" / "07" / f"{source_id}_fixture.md"
    app = vault / "applications" / "2026" / "07" / f"APP-{source_id}_fixture.md"
    kp_dir = vault / "knowledge-points" / "by-source" / source_id

    asset.parent.mkdir(parents=True, exist_ok=True)
    source.parent.mkdir(parents=True, exist_ok=True)
    app.parent.mkdir(parents=True, exist_ok=True)
    kp_dir.mkdir(parents=True, exist_ok=True)
    asset.write_bytes(b"\x89PNG\r\n\x1a\n")

    source.write_text(
        f"""---
source_id: "{source_id}"
title: "{title}"
summary: "這條成果用來驗收 100 條靜態分頁。"
source_url: "https://example.com/{source_id}"
infographics:
  - "assets/{safe_id}/source.png"
---

## TL;DR

測試內容。
""",
        encoding="utf-8",
    )
    app.write_text(
        f"""---
source_id: "{source_id}"
AI總判斷: "試行"
AI信心: "中"
品味: "不入網"
複利: "needs_context"
compounding_insight_status: "needs_context"
compounding_insight_types: []
compounding_insight_links: []
compounding_next_action: "用靜態分頁檢查大量成果能否掃讀。"
PNG: "assets/{safe_id}/source.png"
---

### 關係

這條成果用來驗收列表分頁。

### 洞見

大量成果需要分頁,不能塞進單一 HTML。

## 跨來源洞見判斷

- 補底:需要更多脈絡；需讀回既有成果，確認是否存在背景缺口。
- 加深:不成立；目前未加深既有知識點。
- 連接:不成立；目前未建立可驗證的跨來源扣連。
- 修正:不成立；目前未修正舊理解。
- 行動:不成立；目前未導出新的可驗證行動。
""",
        encoding="utf-8",
    )
    (kp_dir / "KP-TEST-001.md").write_text(
        f"""---
kp_id: "{source_id}:KP-001"
knowledge_point: "成果頁列表需要靜態分頁"
knowledge_type: "原則"
confidence: "高"
source_anchor: "測試錨點"
---
""",
        encoding="utf-8",
    )


class VerbatimOutcomePageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = SCRATCH / f"outcome-verbatim-{uuid4().hex[:10]}"
        self.root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_retained_source_code_block_preserves_verbatim_whitespace(self) -> None:
        # A retained source line ending in a space was trimmed by page clean-up, so the reader audit found
        # the retained full body incomplete and blocked the ingest.
        vault = self.root / "vault"
        source_id = "KS-TEST-0198-VERBATIM"
        write_fixture(vault, source_id, "原樣保留測試")
        source = next((vault / "sources").rglob(f"{source_id}_*.md"))
        canonical = (
            "來源：https://example.com/agents-judgment-calls \n"
            "第二行保留多行與特殊字元：& < > \" ' ✅  \n"
            "第三行。"
        )
        source.write_text(
            source.read_text(encoding="utf-8")
            + f"\n## 正文保存區\n\n### Canonical article body\n\n```markdown\n{canonical}\n```\n\n## 知識用途分流\n\n測試用途。\n",
            encoding="utf-8",
        )
        page = Path(generate_outcome_page(source_id, vault, self.root / "outcome" / f"{source_id}_outcome.html")["output"]).read_text(encoding="utf-8")
        rendered = [
            html.unescape(value)
            for value in re.findall(r'<pre class="note-code"><code>(.*?)</code></pre>', page, flags=re.S)
        ]
        self.assertEqual(rendered, [canonical])
        outside = re.sub(r'<pre class="note-code"><code>.*?</code></pre>', "", page, flags=re.S)
        self.assertTrue(all(line == line.rstrip() for line in outside.splitlines()))

    def test_strip_trailing_whitespace_unchanged_without_code_blocks(self) -> None:
        def previous(text: str) -> str:
            return "\n".join(line.rstrip() for line in text.splitlines()) + "\n"

        samples = [
            "a  \n  b\t\n\n",
            "<p>x </p>\r\n<p>y　</p>",
            "",
            "only\n",
            "<pre class=\"other\">keep? </pre>  \n",
        ]
        for sample in samples:
            self.assertEqual(strip_trailing_whitespace(sample), previous(sample), repr(sample))
        mixed = 'before  \n<pre class="note-code"><code>in 1 \nin 2  </code></pre>  \nafter \n'
        self.assertEqual(strip_trailing_whitespace(mixed), 'before\n<pre class="note-code"><code>in 1 \nin 2  </code></pre>\nafter\n')


if __name__ == "__main__":
    unittest.main()
