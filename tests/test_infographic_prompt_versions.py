"""Prompt versions and their public visual specification stay synchronized."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))
from tools import infographic_prompt_contract as prompt


class InfographicPromptVersions(unittest.TestCase):
    def test_v35_and_v36_are_frozen_while_v37_relaxes_only_footer_presentation(self) -> None:
        older = prompt.template_prompt_for_contract(prompt.INFOGRAPHIC_OLDER_PROMPT_CONTRACT_VERSION)
        legacy = prompt.template_prompt_for_contract(prompt.INFOGRAPHIC_LEGACY_PROMPT_CONTRACT_VERSION)
        current = prompt.template_prompt_for_contract(prompt.INFOGRAPHIC_PROMPT_CONTRACT_VERSION)
        self.assertNotIn("3D 漸變色只附著", older)
        self.assertIn("3D 漸變色只附著於實際語義分區", legacy)
        self.assertIn("3D 漸變色只附著於實際語義分區", current)
        self.assertIn("不能遮蓋文字、取代關係表達", current)
        self.assertIn("可保留既有品牌字樣 (創造型 2nd Brain)", current)
        self.assertIn("不是逐字驗收條件", current)

    def test_spec_contract_keeps_style_advisory_and_footer_provenance_mandatory(self) -> None:
        contract = prompt.load_infographic_reverse_review_contract()
        self.assertEqual(contract["style_enforcement"], "advisory_only")
        self.assertNotIn("non_bauhaus_or_generic_card_style", contract["blocking_failures"])
        self.assertEqual(prompt.INFOGRAPHIC_STYLE_BASIS, contract["required_style_basis"])
        self.assertIn("footer_signature", contract["required_review_axes"])
        self.assertIn("missing_footer_provenance", contract["blocking_failures"])
        self.assertIn("來源資料", contract["footer_provenance_rule"])
        self.assertEqual(prompt.infographic_footer_text("Example Source", "2026-10-12"), "來源:Example Source　資訊圖:2026-10-12")

    def test_historic_footer_signature_axis_remains_accepted(self) -> None:
        review = {
            "spec_read_receipt": {
                "spec_path": "docs/資訊圖生成規格.md",
                "spec_full_sha256": "a" * 64,
                "spec_chars": "100",
            },
            "review_axes": {
                "source_fidelity": "passed",
                "visible_text_line_review": "passed",
                "relationship_integrity": "passed",
                "footer_signature": "passed",
            },
            "formal_ingestion_allowed": True,
            "reader_review": "Historical independent readback remains valid.",
        }
        self.assertEqual(
            prompt.image_reverse_review_errors(review, allow_historical_spec_receipt=True),
            [],
        )


if __name__ == "__main__":
    unittest.main()
