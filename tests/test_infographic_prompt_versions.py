"""Prompt v3.6 and its public visual specification stay synchronized."""
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
    def test_v35_is_frozen_and_v36_has_semantic_3d_guidance(self) -> None:
        legacy = prompt.template_prompt_for_contract(prompt.INFOGRAPHIC_LEGACY_PROMPT_CONTRACT_VERSION)
        current = prompt.template_prompt_for_contract(prompt.INFOGRAPHIC_PROMPT_CONTRACT_VERSION)
        self.assertNotIn("3D 漸變色只附著", legacy)
        self.assertIn("3D 漸變色只附著於實際語義分區", current)
        self.assertIn("不能遮蓋文字、取代關係表達", current)

    def test_spec_contract_keeps_style_advisory_and_footer_stable(self) -> None:
        contract = prompt.load_infographic_reverse_review_contract()
        self.assertEqual(contract["style_enforcement"], "advisory_only")
        self.assertNotIn("non_bauhaus_or_generic_card_style", contract["blocking_failures"])
        self.assertEqual(prompt.INFOGRAPHIC_STYLE_BASIS, contract["required_style_basis"])
        self.assertEqual(contract["required_footer_signature"], "(創造型 2nd Brain)")


if __name__ == "__main__":
    unittest.main()
