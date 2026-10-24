from __future__ import annotations

import html
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir(): ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
import obsidian_vault_adapter as adapter  # noqa: E402
import outcome_page_generator as generator  # noqa: E402
import outcome_page_quality_audit as audit  # noqa: E402

FENCE = "```"
NOTE = "---\ntype: \"source_note\"\nsource_retention_mode: \"full_body_required\"\n---\n# 來源\n\n## 正文保存區\n\n### Canonical article body\n\n```markdown\n# 一篇文章\n## 第一節\n內容一\n## 第二節\n內容二\n```\n\n## 知識用途分流\n\n- 學習材料\n"


class OutcomePageBodyParityTests(unittest.TestCase):
    def test_fenced_body_with_h2_headings_is_shown_whole(self) -> None:
        self.assertEqual(generator.reader_safe_source_body(NOTE), "# 原文正文\n\n```markdown\n# 一篇文章\n## 第一節\n內容一\n## 第二節\n內容二\n```")


class RetainedBodyAuditParityTests(unittest.TestCase):
    def test_quoted_frontmatter_mode_still_audits(self) -> None:
        body = adapter.extract_canonical_body_markdown(NOTE)
        self.assertEqual(audit.retained_source_issues('<pre class="note-code"><code>' + html.escape(body) + "</code></pre>", NOTE), [])
        self.assertEqual(audit.retained_source_issues("<p>沒有原文</p>", NOTE), ["retained full source body is incomplete in outcome page"])


if __name__ == "__main__": unittest.main()
