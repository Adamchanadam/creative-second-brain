"""Regression checks for full application text and relationship projections."""
from pathlib import Path
import json, sys, tempfile, unittest
ROOT = Path(__file__).resolve().parents[1]
if (ROOT / 'runtime' / 'workspace').is_dir():
    ROOT = ROOT / 'runtime' / 'workspace'
sys.path.insert(0, str(ROOT / 'tools'))
import compiler_worker_v2 as worker
import ingest_plan_contract as contract
import obsidian_vault_adapter as adapter
import outcome_page_generator as reader


class ApplicationEnrichment(unittest.TestCase):
    def test_analysis_shape_rejects_silent_summary_substitution(self):
        schema = contract.plan_schema()['properties']['analysis']
        self.assertTrue(contract.schema_errors({'summary': 42, 'source_claims': []}, schema))

    def test_analysis_stays_inside_application_section(self):
        with tempfile.TemporaryDirectory() as d:
            lines = worker.render_application_enrichment({'analysis_markdown': '# 深讀\n\n## 核心\n\n完整正文'}, Path(d))
            rendered = '\n'.join(lines)
            self.assertIn('#### 核心', rendered)
            self.assertNotIn('\n## ', rendered)

    def test_related_sources_are_bidirectional_and_escaped(self):
        with tempfile.TemporaryDirectory() as d:
            vault = Path(d)
            (vault / 'applications').mkdir(); (vault / 'sources').mkdir()
            for sid in ['KS-VS-A', 'KS-VS-B']:
                (vault/'sources'/f'{sid}_sample.md').write_text(f'---\nsource_id: "{sid}"\ntitle: "測試 <標題>"\n---\n正文', encoding='utf-8')
            relations = json.dumps([{'source_id':'KS-VS-B','note':'互補 <解讀>'}, None], ensure_ascii=False)
            (vault/'applications/APP-KS-VS-A_sample.md').write_text('---\nsource_id: "KS-VS-A"\nrelated_sources_json: '+json.dumps(relations, ensure_ascii=False)+'\n---\n正文', encoding='utf-8')
            a=reader.related_source_block(vault,'KS-VS-A',vault/'outcome-pages')
            b=reader.related_source_block(vault,'KS-VS-B',vault/'outcome-pages')
            self.assertIn('KS-VS-B_outcome.html',a); self.assertIn('KS-VS-A_outcome.html',b)
            self.assertIn('&lt;解讀&gt;',a)

    def test_knowledge_evidence_is_closed_and_escaped(self):
        cards = reader.knowledge_point_cards([
            {'knowledge_point': '標題 <保留>', 'knowledge_type': '來源主張', 'confidence': '高', 'source_anchor': '原文 <script>alert(1)</script>'},
            {'knowledge_point': '沒有依據', 'knowledge_type': '推論', 'confidence': '中', 'source_anchor': ''},
        ])
        self.assertIn('<details class="kp-evidence">', cards)
        self.assertIn('<summary>查看原文依據</summary>', cards)
        self.assertNotIn('<details class="kp-evidence" open', cards)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', cards)
        self.assertEqual(cards.count('<details class="kp-evidence">'), 1)
        self.assertIn('來源主張', cards)
        self.assertIn('信心 高', cards)

    def test_pending_protocol_explanation_is_not_a_draft(self):
        self.assertFalse(adapter.has_application_placeholders('處理 pending、slow_down、denied、expired。'))
        for text in ['pending','- 狀態: pending','| 採納 | pending |','1. pending','| 項目 | 說明 | pending |','狀態：**pending**','狀態：`pending`','待 AI 補寫','待補']:
            self.assertTrue(adapter.has_application_placeholders(text),text)


if __name__ == '__main__':
    unittest.main()
