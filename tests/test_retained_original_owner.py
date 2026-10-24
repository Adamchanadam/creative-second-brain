from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir(): ROOT = ROOT / "runtime" / "workspace"
TOOLS = ROOT / "tools"; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(TOOLS))
OWNER = "obsidian_vault_adapter.py"
READERS = {
    ("completion_revisions.py", "original_body_for_current_source_review"): {"retained_original_location", "read_retained_original"},
    ("completion_revisions.py", "validate"): {"retained_original_location", "read_retained_original"},
    ("knowledge_material_selector.py", "canonical_source_text_for_matching"): {"retained_original_location"},
    ("ask_second_brain.py", "project_packet_note"): {"retained_note_body_offsets"},
    ("outcome_page_generator.py", "reader_safe_source_body"): {"retained_note_body_section"},
    ("outcome_page_quality_audit.py", "retained_source_issues"): {"retained_original_location", "read_retained_original"},
}


def called_names(node: ast.AST) -> set[str]:
    return {child.func.id if isinstance(child.func, ast.Name) else child.func.attr for child in ast.walk(node) if isinstance(child, ast.Call) and isinstance(child.func, (ast.Name, ast.Attribute))}


class OwnerTests(unittest.TestCase):
    def test_mapping_and_all_completed_source_readers_use_owner(self) -> None:
        from tools import obsidian_vault_adapter as adapter
        self.assertEqual(adapter.retained_original_location("full_body_required"), "source_note_body")
        for mode in ("original_image_required", "transcript_archive_required", "summarized_allowed", None): self.assertEqual(adapter.retained_original_location(mode), "package_canonical")
        owners = {node.name for node in ast.parse((TOOLS / OWNER).read_text(encoding="utf-8")).body if isinstance(node, ast.FunctionDef)}
        for (filename, function), required_owner_functions in READERS.items():
            path = TOOLS / filename
            self.assertTrue(path.is_file(), f"required reader is missing: {filename}")
            functions = {node.name: node for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(node, ast.FunctionDef)}
            self.assertIn(function, functions)
            self.assertTrue(required_owner_functions <= owners, f"owner lacks designated function for {filename}:{function}")
            self.assertTrue(called_names(functions[function]) & required_owner_functions, f"{filename}:{function}")


if __name__ == "__main__": unittest.main()
