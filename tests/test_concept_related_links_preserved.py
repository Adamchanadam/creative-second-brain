"""Concept projections retain established links except to withdrawn concepts."""
from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT / "tools"))
import obsidian_vault_adapter as adapter


def synthetic_root(prefix: str) -> Path:
    """Avoid Windows sandbox private-temp ACLs while remaining under BRAIN_TEST_TMPDIR."""
    base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or os.environ.get("TEMP") or ".")
    base.mkdir(parents=True, exist_ok=True)
    root = base / f"{prefix[:1]}{uuid.uuid4().hex[:4]}"
    root.mkdir()
    return root


class ConceptRelatedLinksPreservedTests(unittest.TestCase):
    """A normal projection must not erase generated related-concept links."""

    CONCEPT = "AI 採用成效衡量"
    OLD_SOURCE = "SRC-OLD"
    NEW_SOURCE = "SRC-NEW"
    OLD_RECORDS = ("KP-LEGACY-A", "KP-LEGACY-B")
    NEW_RECORD = "KP-NEW"
    REVISION_SOURCE = "SRC-REVISION"
    REVISION_A = "修訂概念 A"
    REVISION_B = "修訂概念 B"
    REVISION_C = "未修訂概念 C"
    REVISION_A_RECORD = "KP-REVISION-A"
    REVISION_B_RECORD = "KP-REVISION-B"
    LINKS = (
        "[[Agent 採用證據需要外推限制]]",
        "[[Agent 擴張可完成工作的範圍]]",
        "[[高階 AI 採用需要信任、防護欄和團隊決策速度]]",
        "[[人的角色由操作者轉向監督者]]",
    )

    def setUp(self) -> None:
        self.vault = synthetic_root("concept-links") / "vault"
        (self.vault / "concepts").mkdir(parents=True)

    def write_kp(self, source_id: str, kp_id: str, *, reusable: bool = False,
                 concept_name: str | None = None) -> Path:
        path = self.vault / "knowledge-points" / "by-source" / source_id / f"{kp_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = {
            "type": "knowledge_point",
            "kp_id": kp_id,
            "kp_uid": f"{source_id}:{kp_id}",
            "source_id": source_id,
            "source_note": f"{source_id}_source",
            "knowledge_point": f"{kp_id} 的定義。",
        }
        if reusable:
            fields.update(
                reusable_concept_status="reusable",
                reusable_concept_name=concept_name or self.CONCEPT,
                reusable_concept_reason="fixture reusable concept",
            )
        path.write_text(adapter.render_frontmatter(fields) + "# KP\n", encoding="utf-8")
        return path

    def legacy_page(self, related_concepts: tuple[str, ...] | None = LINKS, *,
                    concept_name: str | None = None, records: tuple[str, ...] | None = None,
                    source_id: str | None = None) -> str:
        concept_name = concept_name or self.CONCEPT
        records = records or self.OLD_RECORDS
        source_id = source_id or self.OLD_SOURCE
        lines = [
            "---", 'type: "concept"', f'concept: "{concept_name}"',
            'concept_status: "graph_node"', 'graph_layer: "概念層"',
            'graph_role: "概念"',
            'graph_question: "它和哪些概念同時出現，是否形成新的主題或問題？"',
            'graph_scope: "concept_links_only"', "source_records:",
            f'  - "{source_id}_source"', "application_records:",
            f'  - "APP-{source_id}"', "knowledge_point_records:",
            *[f'  - "{record}"' for record in records],
        ]
        if related_concepts is not None:
            lines.extend(("related_concepts:", *[f'  - "{link}"' for link in related_concepts])) if related_concepts else lines.append("related_concepts: []")
        lines.extend((
            'updated: "2026-10-22"', "---", "", f"# {concept_name}", "",
            "## 定義來源", "", "- 概念定義。", "", "## 知識點記錄", "",
            *[f"- `{record}`" for record in records], "", "## 相關概念", "",
            *[f"- {link}" for link in related_concepts or ()], "",
        ))
        return "\n".join(lines)

    def write_existing_generated_page(self, related_concepts: tuple[str, ...] | None = LINKS, *,
                                      concept_name: str | None = None, records: tuple[str, ...] | None = None,
                                      source_id: str | None = None) -> Path:
        concept_name = concept_name or self.CONCEPT
        records = records or self.OLD_RECORDS
        source_id = source_id or self.OLD_SOURCE
        path = self.vault / "concepts" / f"{concept_name}.md"
        path.write_text(self.legacy_page(related_concepts, concept_name=concept_name, records=records,
                                         source_id=source_id), encoding="utf-8")
        for record in records:
            self.write_kp(source_id, record)
        return path

    def project_new_source(self) -> None:
        self.write_kp(self.NEW_SOURCE, self.NEW_RECORD, reusable=True)
        adapter.write_concept_notes(self.vault, source_ids={self.NEW_SOURCE})

    def write_new_reusable_point(self) -> None:
        self.write_kp(self.NEW_SOURCE, self.NEW_RECORD, reusable=True)

    def rendered_fields(self, path: Path) -> dict:
        return adapter.frontmatter_fields_from_text(path.read_text(encoding="utf-8"))

    @staticmethod
    def field_list(fields: dict, key: str) -> list[str]:
        value = fields.get(key)
        if value is None:
            return []
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]

    def stage_scoped_revision(self, links: tuple[str, ...], *, keep_b: bool) -> tuple[dict, dict]:
        a_path = self.write_existing_generated_page(
            links, concept_name=self.REVISION_A, records=(self.REVISION_A_RECORD,), source_id=self.REVISION_SOURCE,
        )
        b_path = self.write_existing_generated_page(
            (), concept_name=self.REVISION_B, records=(self.REVISION_B_RECORD,), source_id=self.REVISION_SOURCE,
        )
        a_kp = self.write_kp(self.REVISION_SOURCE, self.REVISION_A_RECORD, reusable=True,
                             concept_name=self.REVISION_A)
        candidate_fields = {a_kp: adapter.frontmatter_fields(a_kp)}
        b_kp = self.write_kp(self.REVISION_SOURCE, self.REVISION_B_RECORD, reusable=keep_b,
                             concept_name=self.REVISION_B)
        if keep_b:
            candidate_fields[b_kp] = adapter.frontmatter_fields(b_kp)
        concepts = adapter.write_concept_notes(
            self.vault, source_ids={self.REVISION_SOURCE}, candidate_fields=candidate_fields, render_only=True,
        )
        adapter.scoped_concept_revision(
            self.vault, self.REVISION_SOURCE, {self.REVISION_A_RECORD, self.REVISION_B_RECORD}, concepts,
        )
        return (
            adapter.frontmatter_fields_from_text(concepts[a_path].decode("utf-8")),
            adapter.frontmatter_fields_from_text(concepts[b_path].decode("utf-8")),
        )

    def test_existing_links_round_trip_when_all_old_records_exist(self) -> None:
        concept = self.write_existing_generated_page()
        self.write_new_reusable_point()
        self.project_new_source()
        first = concept.read_bytes()
        fields = self.rendered_fields(concept)
        self.assertEqual(set(self.field_list(fields, "related_concepts")), set(self.LINKS))
        self.assertIn("## 相關概念\n\n", concept.read_text(encoding="utf-8"))
        for link in self.LINKS:
            self.assertIn(f"- {link}\n", concept.read_text(encoding="utf-8"))
        self.assertEqual(set(self.field_list(fields, "knowledge_point_records")), {*self.OLD_RECORDS, self.NEW_RECORD})
        self.project_new_source()
        self.assertEqual(concept.read_bytes(), first, "second projection is byte-identical")

    def test_empty_list_stays_empty_without_bracket_items(self) -> None:
        concept = self.write_existing_generated_page(())
        self.write_new_reusable_point()
        self.project_new_source()
        text = concept.read_text(encoding="utf-8")
        self.assertEqual(self.field_list(self.rendered_fields(concept), "related_concepts"), [])
        self.assertNotIn('  - "["', text)
        self.assertNotIn('  - "]"', text)
        self.assertNotIn("## 相關概念", text)

    def test_missing_related_concepts_key_has_no_links_or_error(self) -> None:
        concept = self.write_existing_generated_page(None)
        self.write_new_reusable_point()
        self.project_new_source()
        self.assertEqual(self.field_list(self.rendered_fields(concept), "related_concepts"), [])
        self.assertNotIn("## 相關概念", concept.read_text(encoding="utf-8"))

    def test_self_link_is_retained_exactly(self) -> None:
        links = (f"[[{self.CONCEPT}]]", self.LINKS[2])
        concept = self.write_existing_generated_page(links)
        self.write_new_reusable_point()
        self.project_new_source()
        self.assertEqual(set(self.field_list(self.rendered_fields(concept), "related_concepts")), set(links))

    def test_scoped_revision_keeps_link_to_active_staged_concept(self) -> None:
        a_fields, _ = self.stage_scoped_revision((f"[[{self.REVISION_B}]]",), keep_b=True)
        self.assertEqual(self.field_list(a_fields, "related_concepts"), [f"[[{self.REVISION_B}]]"])

    def test_scoped_revision_keeps_active_self_link(self) -> None:
        a_fields, _ = self.stage_scoped_revision((f"[[{self.REVISION_A}]]",), keep_b=True)
        self.assertEqual(self.field_list(a_fields, "related_concepts"), [f"[[{self.REVISION_A}]]"])

    def test_scoped_revision_removes_link_only_when_target_is_withdrawn(self) -> None:
        a_fields, b_fields = self.stage_scoped_revision((f"[[{self.REVISION_B}]]",), keep_b=False)
        self.assertEqual(self.field_list(a_fields, "related_concepts"), [])
        self.assertEqual(b_fields["concept_status"], "superseded")
        self.assertEqual(self.field_list(b_fields, "related_concepts"), [])

    def test_scoped_revision_keeps_link_to_unstaged_concept(self) -> None:
        a_fields, _ = self.stage_scoped_revision((f"[[{self.REVISION_C}]]",), keep_b=True)
        self.assertEqual(self.field_list(a_fields, "related_concepts"), [f"[[{self.REVISION_C}]]"])

    def test_handwritten_page_is_never_written(self) -> None:
        concept = self.vault / "concepts" / f"{self.CONCEPT}.md"
        original = f"---\ntype: concept\nconcept: {self.CONCEPT}\n---\n\n# 手寫頁\n"
        concept.write_text(original, encoding="utf-8")
        self.write_new_reusable_point()
        self.project_new_source()
        self.assertEqual(concept.read_text(encoding="utf-8"), original)

    def test_brand_new_page_keeps_empty_list_format(self) -> None:
        self.write_new_reusable_point()
        self.project_new_source()
        concept = self.vault / "concepts" / f"{self.CONCEPT}.md"
        self.assertEqual(self.field_list(self.rendered_fields(concept), "related_concepts"), [])
        self.assertNotIn("## 相關概念", concept.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
