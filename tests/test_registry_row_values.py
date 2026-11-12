"""One rule turns a registry JSON row into program values: ``obsidian_vault_adapter.registry_row_values``.

Registry writers rewrite whole files, so a reader that flattens every value with ``str()`` silently turns another
source's nested object (a point's attribution) into Python repr text on disk (2026-11-10, KS-VS-0103).  These tests
lock the rule, the three readers that must use it, and a static guard against a new reader that coerces on its own.
Everything runs on temporary files; no real vault is read or written.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE = ROOT / "runtime" / "workspace" if (ROOT / "runtime" / "workspace" / "tools").is_dir() else ROOT
TOOLS = CODE / "tools"
sys.path.insert(0, str(CODE))

from tools import completion_revisions as revisions  # noqa: E402
from tools import obsidian_vault_adapter as adapter  # noqa: E402

SHARED = ("obsidian_vault_adapter.py", "registry_row_values")
# Every reader of a registry row; each must call the shared function.
READERS = (("obsidian_vault_adapter.py", "read_jsonl_rows"), ("obsidian_vault_adapter.py", "read_source_registry_rows"),
           ("completion_revisions.py", "_rendered_registry_rows"))
# The same comprehension shape used on something that is not a registry row: (how many, why).  A further one in the
# same function is not covered.
NOT_A_REGISTRY_ROW = {
    ("youtube_transcript_capture.py", "_verified_subtitle_headers"):
        (1, "HTTP headers of a verified subtitle item: both sides are already filtered to text, nothing is written back"),
    ("completion_revisions.py", "register_kp_notes"):
        (2, "the caller's per-note reason and support sentences (note path -> text); no registry file is read"),
}
ATTRIBUTION = {"cited_work": "1", "label": "心態", "origin": "cited_named", "relay": "restates", "verified_name": "致富心態"}


def _is_items_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "items"


def _strs_a_name(node: ast.AST, names: set[str]) -> bool:
    return any(isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) and inner.func.id == "str"
               and len(inner.args) == 1 and isinstance(inner.args[0], ast.Name) and inner.args[0].id in names
               for inner in ast.walk(node))


def row_coercions(source: str, name: str = "<source>") -> list[tuple[str, int]]:
    """(enclosing function, line) of every mapping built by ``str()``-ing the values of ``<mapping>.items()``.

    Covers ``{k: str(v) for k, v in x.items()}``, a conditional around the ``str(v)``, any names for the pair, and
    ``dict((k, str(v)) for k, v in x.items())`` / the list form.
    """
    tree = ast.parse(source, name)
    owner: dict[int, str] = {}
    for function in ast.walk(tree):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(function):
                owner.setdefault(id(inner), function.name)  # outermost function wins: ast.walk is breadth-first
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.DictComp):
            value = node.value
        elif isinstance(node, (ast.GeneratorExp, ast.ListComp)) and isinstance(node.elt, (ast.Tuple, ast.List)) and len(node.elt.elts) == 2:
            value = node.elt.elts[1]
        else:
            continue
        for generator in node.generators:
            target = generator.target  # `for k, v in` and `for [k, v] in` unpack the same pair
            if (_is_items_call(generator.iter) and isinstance(target, (ast.Tuple, ast.List)) and len(target.elts) == 2
                    and isinstance(target.elts[1], ast.Name)):
                if _strs_a_name(value, {target.elts[1].id}):
                    found.append((owner.get(id(node), "<module>"), node.lineno))
    return found


def calls_shared(source: str, function: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function:
            return any(isinstance(inner, ast.Call) and (
                (isinstance(inner.func, ast.Name) and inner.func.id == SHARED[1])
                or (isinstance(inner.func, ast.Attribute) and inner.func.attr == SHARED[1])) for inner in ast.walk(node))
    return False


class RegistryRowRuleTests(unittest.TestCase):
    def test_nested_values_are_kept_and_scalars_keep_their_old_text_form(self):
        item = {"kp_uid": "KS-1:KP-1", "attribution": ATTRIBUTION, "empty": {}, "tags": ["a", 1], "none": None, "count": 3,
                "ratio": 1.5, "flag": True, "off": False, "text": "原文", 7: "numeric key"}
        row = adapter.registry_row_values(item)
        self.assertEqual(row, {"kp_uid": "KS-1:KP-1", "attribution": ATTRIBUTION, "empty": {}, "tags": ["a", 1], "none": "None",
                               "count": "3", "ratio": "1.5", "flag": "True", "off": "False", "text": "原文", "7": "numeric key"})
        self.assertIs(row["attribution"], item["attribution"])
        # A row without the nested key stays without it; callers' defaults still apply.
        self.assertEqual(adapter.registry_row_values({"status": "linked"}).get("attribution", "absent"), "absent")
        # Every scalar is exactly what str() gave before the rule existed.
        for value in (None, 0, 3, -1, 1.5, True, False, "", "linked"):
            self.assertEqual(adapter.registry_row_values({"k": value}), {"k": str(value)})


class RegistryReaderTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
        scratch.mkdir(parents=True, exist_ok=True)
        self.vault = scratch / f"registry-row-values-{uuid.uuid4().hex}" / "vault"
        self.addCleanup(shutil.rmtree, self.vault.parent, True)
        self.rows = [{"source_id": "KS-VS-0001", "source_identity": "url:one", "kp_uid": "KS-VS-0001:KP-001", "relation_id": "R-1",
                      "attribution": ATTRIBUTION, "aliases": ["甲", "乙"], "count": 3, "missing": None},
                     {"source_id": "KS-VS-0002", "source_identity": "url:two", "kp_uid": "KS-VS-0002:KP-001", "relation_id": "R-2"}]
        self.text = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in self.rows)

    def check(self, first: dict, second: dict):
        self.assertEqual(first["attribution"], ATTRIBUTION)
        self.assertEqual(first["aliases"], ["甲", "乙"])
        self.assertEqual((first["count"], first["missing"]), ("3", "None"))
        self.assertNotIn("attribution", second)

    def test_the_source_registry_reader_keeps_a_nested_value(self):
        path = adapter.source_registry_file(self.vault)
        path.parent.mkdir(parents=True)
        path.write_text(self.text, encoding="utf-8")
        rows = adapter.read_source_registry_rows(self.vault)
        self.assertEqual([line for line, _ in rows], [1, 2])
        self.check(rows[0][1], rows[1][1])
        # Written back in the on-disk form (sorted keys, no ASCII escaping) the nested value is still an object.
        again = json.loads(json.dumps(rows[0][1], ensure_ascii=False, sort_keys=True))
        self.assertEqual(again["attribution"], ATTRIBUTION)

    def test_the_jsonl_reader_keeps_a_nested_value(self):
        path = adapter.knowledge_point_registry_file(self.vault)
        path.parent.mkdir(parents=True)
        path.write_text(self.text, encoding="utf-8")
        rows = adapter.read_jsonl_rows(path)
        self.check(rows[0][1], rows[1][1])

    def test_the_candidate_render_reader_keeps_a_nested_value_like_the_live_reader(self):
        data = self.text.encode("utf-8")
        rows = revisions._rendered_registry_rows(data, "kp_uid")
        self.assertEqual(list(rows), ["KS-VS-0001:KP-001", "KS-VS-0002:KP-001"])
        self.check(rows["KS-VS-0001:KP-001"], rows["KS-VS-0002:KP-001"])
        path = adapter.knowledge_point_registry_file(self.vault)
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        self.assertEqual(rows, adapter.read_knowledge_point_registry(self.vault))
        with self.assertRaises(ValueError):
            revisions._rendered_registry_rows(data + data, "kp_uid")  # duplicate key: still refused


class RegistryRowGuardTests(unittest.TestCase):
    def test_no_tool_coerces_a_row_outside_the_shared_function(self):
        offenders, allowed = [], {}
        for path in sorted(TOOLS.glob("*.py")):
            for function, line in row_coercions(path.read_text(encoding="utf-8-sig"), path.name):
                if (path.name, function) == SHARED:
                    continue
                if (path.name, function) in NOT_A_REGISTRY_ROW:
                    allowed[(path.name, function)] = allowed.get((path.name, function), 0) + 1
                    continue
                offenders.append(f"{path.name}:{line} in {function}")
        self.assertEqual(offenders, [], "a registry row must be read through obsidian_vault_adapter.registry_row_values; "
                                        "a str() over .items() flattens nested values: " + "; ".join(offenders))
        self.assertEqual(allowed, {key: count for key, (count, _) in NOT_A_REGISTRY_ROW.items()},
                         "the allow-list no longer matches the code it excuses")

    def test_every_registry_reader_uses_the_shared_function(self):
        for name, function in READERS:
            self.assertTrue(calls_shared((TOOLS / name).read_text(encoding="utf-8-sig"), function), (name, function))
        shared = (TOOLS / SHARED[0]).read_text(encoding="utf-8-sig")
        self.assertEqual([function for function, _ in row_coercions(shared) if function == SHARED[1]], [SHARED[1]],
                         "the guard must see the shared function's own comprehension (else it is blind)")

    def test_the_guard_is_not_vacuous(self):
        bad = (
            "def read(item):\n    return {str(key): str(value) for key, value in item.items()}\n",
            "def read(row):\n    return {str(name): str(item) for name, item in row.items()}\n",
            "def read(row):\n    return {k: v if isinstance(v, (dict, list)) else str(v) for k, v in row.items()}\n",
            "def read(row):\n    return dict((k, str(v)) for k, v in row.items())\n",
            "def read(row):\n    return dict([(k, str(v)) for k, v in row.items()])\n",
            "def read(rows):\n    return [{k: str(v) for k, v in json.loads(line).items()} for line in rows]\n",
            "ROWS = {k: str(v).strip() for k, v in CONFIG.items()}\n",
            "def read(row):\n    return {str(k): str(v) for [k, v] in row.items()}\n",
            "def read(row):\n    return dict([k, str(v)] for k, v in row.items())\n",
        )
        for sample in bad:
            self.assertTrue(row_coercions(sample), sample)
        good = (
            "def read(item):\n    return registry_row_values(item)\n",
            "def read(item):\n    return {str(key): value for key, value in item.items()}\n",
            "def read(item):\n    return {key: len(value) for key, value in item.items()}\n",
            "def read(items):\n    return {str(value) for value in items}\n",
            "def read(item):\n    return {str(key): other for key, value in item.items()}\n",
        )
        for sample in good:
            self.assertEqual(row_coercions(sample), [], sample)
        self.assertEqual(row_coercions("def outer():\n    def inner(r):\n        return {k: str(v) for k, v in r.items()}\n    return inner\n"),
                         [("outer", 3)])
        self.assertTrue(calls_shared("def f(x):\n    return registry_row_values(x)\n", "f"))
        self.assertTrue(calls_shared("def f(x):\n    return adapter.registry_row_values(x)\n", "f"))
        self.assertFalse(calls_shared("def f(x):\n    return {str(k): str(v) for k, v in x.items()}\n", "f"))
        self.assertFalse(calls_shared("def g(x):\n    return registry_row_values(x)\n", "f"))


if __name__ == "__main__":
    unittest.main()
