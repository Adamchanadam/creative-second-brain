"""brain_lookup: read-only lookup with absolute paths, excerpts, attribution and answer rules."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if (REPO_ROOT / "runtime" / "workspace").is_dir():
    REPO_ROOT = REPO_ROOT / "runtime" / "workspace"
LOOKUP = REPO_ROOT / "tools" / "brain_lookup.py"
SCRATCH = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())

ORIGINAL_MARK = "判斷與驗證仍要由人負責，這句是原文保存區內的逐字內容。"
SOURCE_NOTE = f"""---
type: "source"
source_id: "KS-TEST-0200"
source_retention_mode: "full_body_required"
title: "AI 工作的瓶頸遷移"
source_url: "https://example.com/ai-bottleneck"
source_type: "測試"
capture_date: "2026-07-06"
knowledge_use:
  - "判斷材料"
summary: "這條來源說明 AI 令產出變快後，瓶頸會移到判斷、驗證與責任。"
---

## TL;DR

AI 解掉產出速度後，真正要讀的是判斷與驗證責任。

## 正文保存區

### Canonical article body
```markdown
AI 令產出變快，瓶頸遷移到判斷。{ORIGINAL_MARK}
```
"""
APP_NOTE = """---
source_id: "KS-TEST-0200"
adoption_reason: "這能幫使用者判斷 AI 使用後的新瓶頸。"
---

## 讀取層成果頁內容

### 跟使用者有何關係

這條內容提醒使用者，AI 產出變快後，真正要處理的是判斷、驗證與交付責任。
"""
KP_PLAIN = """---
kp_id: "KP-TEST-0200-001"
knowledge_point: "AI 把瓶頸移到判斷與驗證。"
definition: "AI 把瓶頸移到判斷與驗證。"
source_anchor: "瓶頸遷移"
---

AI 令產出變快，但判斷與驗證仍要由人負責。
"""
KP_ATTRIBUTED = """---
kp_id: "KP-TEST-0200-002"
knowledge_point: "交付責任不能由模型代替。"
definition: "交付責任不能由模型代替。"
source_anchor: "責任"
attribution_origin: "cited_named"
attribution_label: "某位被引用的作者"
attribution_relay: "restates"
---

AI 模型可以協助，但不能替人承擔最終交付。
"""


def write_workspace(root: Path) -> Path:
    vault = root / "vault"
    registry = vault / "_registry"
    source = vault / "sources" / "2026" / "07" / "KS-TEST-0200_ai_bottleneck.md"
    app = vault / "applications" / "2026" / "07" / "APP-KS-TEST-0200_ai_bottleneck.md"
    kp_dir = vault / "knowledge-points" / "by-source" / "KS-TEST-0200"
    for folder in (registry, source.parent, app.parent, kp_dir, vault / "outcome-pages"):
        folder.mkdir(parents=True, exist_ok=True)
    (root / "brain.json").write_text(json.dumps({"vault": "vault", "runtime_python": sys.executable}), encoding="utf-8")
    rows = [
        {"source_id": "KS-TEST-0200", "kp_id": f"KP-TEST-0200-00{n}", "kp_uid": f"KS-TEST-0200:KP-TEST-0200-00{n}",
         "kp_note_path": f"knowledge-points/by-source/KS-TEST-0200/KP-TEST-0200-00{n}.md",
         "knowledge_point": text, "status": "linked"}
        for n, text in ((1, "AI 把瓶頸移到判斷與驗證。"), (2, "交付責任不能由模型代替。"))
    ]
    (registry / "sources.jsonl").write_text(json.dumps({
        "source_id": "KS-TEST-0200", "source_note_path": "sources/2026/07/KS-TEST-0200_ai_bottleneck.md",
        "source_url": "https://example.com/ai-bottleneck", "status": "linked", "updated": "2026-07-06",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (registry / "knowledge_points.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    (registry / "relations.jsonl").write_text("", encoding="utf-8")
    source.write_text(SOURCE_NOTE, encoding="utf-8")
    app.write_text(APP_NOTE, encoding="utf-8")
    (kp_dir / "KP-TEST-0200-001.md").write_text(KP_PLAIN, encoding="utf-8")
    (kp_dir / "KP-TEST-0200-002.md").write_text(KP_ATTRIBUTED, encoding="utf-8")
    (vault / "outcome-pages" / "KS-TEST-0200_outcome.html").write_text("<html></html>", encoding="utf-8")
    return root


def tree_hash(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def run_lookup(*args: str, cwd: Path, bytecode: bool = False, script: Path | None = None) -> tuple[int, dict]:
    done = run_raw(*args, cwd=cwd, bytecode=bytecode, script=script)
    return done.returncode, json.loads(done.stdout)


def run_raw(*args: str, cwd: Path, bytecode: bool = False, script: Path | None = None) -> subprocess.CompletedProcess:
    """`bytecode=True` runs without -B and without PYTHONDONTWRITEBYTECODE, so any cache write would really happen."""
    env = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
    command = [sys.executable, *([] if bytecode else ["-B"]), str(script or LOOKUP), *args]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", env=env)


def files_with_stat(root: Path) -> dict[str, tuple[int, int]]:
    return {str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns) for path in sorted(root.rglob("*")) if path.is_file()}


class BrainLookupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = SCRATCH / ("brain-lookup-test-" + uuid4().hex)
        cls.workspace = write_workspace(cls.base / "workspace")
        cls.elsewhere = cls.base / "unrelated-project"
        cls.elsewhere.mkdir(parents=True)
        # An installed workspace owns its own copy of tools/; the default workspace is where that copy sits.
        cls.installed = write_workspace(cls.base / "installed")
        for folder in ("tools", "docs", "assets"):
            shutil.copytree(REPO_ROOT / folder, cls.installed / folder, ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
        cls.installed_lookup = cls.installed / "tools" / "brain_lookup.py"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def lookup(self, question="AI 瓶頸 判斷 驗證", *extra):
        return run_lookup("--workspace", str(self.workspace), "--question", question, "--json", *extra, cwd=self.elsewhere)

    def test_returns_absolute_existing_paths_and_excerpts(self):
        code, result = self.lookup()
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(Path(result["workspace_root"]), self.workspace.resolve())
        self.assertEqual(result["question"], "AI 瓶頸 判斷 驗證")
        candidate = result["candidates"][0]
        self.assertEqual(candidate["source_id"], "KS-TEST-0200")
        self.assertEqual(candidate["title"], "AI 工作的瓶頸遷移")
        for key in ("source_note_path", "application_note_path", "outcome_page_path"):
            path = Path(candidate[key])
            self.assertTrue(path.is_absolute(), key)
            self.assertTrue(path.exists(), key)
        self.assertTrue(candidate["outcome_page_url"].startswith("file:///"))
        self.assertIn(ORIGINAL_MARK, candidate["original_excerpt"])
        self.assertTrue(candidate["knowledge_points"])
        for point in candidate["knowledge_points"]:
            self.assertTrue(Path(point["path"]).is_absolute() and Path(point["path"]).exists())
            self.assertLessEqual(len(point["text"]), 602)
        self.assertTrue(candidate["evidence_snippets"])

    def test_answer_rules_cover_all_five_specified_rules(self):
        _, result = self.lookup()
        rules = result["answer_rules"]

        def rule_with(*needles: str) -> str:
            found = [rule for rule in rules if all(needle in rule for needle in needles)]
            self.assertTrue(found, f"no answer rule contains {needles}")
            return found[0]

        rule_with("只用本結果內的內容", "所列筆記")                      # 1. answer only from the returned content and its notes
        rule_with("引用：來源編號 · 筆記路徑 · 成果頁", "絕對路徑")        # 2. citation line at the end
        rule_with("材料不足", "no_material", "如實說明")                  # 3. insufficient material is stated honestly
        rule_with("唯讀", "不得寫入", "不得保存思考紀錄或入庫")            # 4. never write the knowledge base
        write_rule = rule_with("存入", "入庫", "修訂", "保存思考", "不執行任何寫入命令")  # 5. write requests: reply with the command only
        self.assertIn("`$brain-think <問題>`", write_rule)
        self.assertIn("`/brain-think`", write_rule)
        self.assertIn("請用戶回到知識庫工作區執行", write_rule)
        self.assertTrue(result["read_only"])

    def test_claim_attribution_sentence_only_when_present(self):
        _, result = self.lookup()
        points = {p["kp_id"]: p for p in result["candidates"][0]["knowledge_points"]}
        self.assertNotIn("attribution", points["KP-TEST-0200-001"])
        self.assertIn("主張來源：某位被引用的作者", points["KP-TEST-0200-002"]["attribution"])
        self.assertIn("照述", points["KP-TEST-0200-002"]["attribution"])

    def test_zero_writes_to_workspace_vault_and_code_folder(self):
        before = tree_hash(self.base)
        tools_before = files_with_stat(REPO_ROOT / "tools")
        for question in ("AI 瓶頸 判斷 驗證", "完全不存在的主題zzqq"):
            self.lookup(question)
            # without -B (the way a global entry may call it) the tool must still leave no cache file in tools/
            run_lookup("--workspace", str(self.workspace), "--question", question, "--json", cwd=self.elsewhere, bytecode=True)
        self.assertEqual(before, tree_hash(self.base))
        self.assertEqual(tools_before, files_with_stat(REPO_ROOT / "tools"))  # no new or changed file, __pycache__ included

    def test_no_material_returns_status_and_rules_without_error(self):
        code, result = self.lookup("完全不存在的主題zzqq")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "no_material")
        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(result["answer_rules"])

    def test_limit_and_expand_are_accepted(self):
        code, result = self.lookup("交付", "--limit", "1", "--expand", "責任")
        self.assertEqual(code, 0)
        self.assertLessEqual(result["candidate_count"], 1)
        self.assertNotIn("limit_note", result)

    def test_limit_above_twenty_is_counted_as_twenty_and_says_so(self):
        code, result = self.lookup("AI 瓶頸 判斷 驗證", "--limit", "500")
        self.assertEqual(code, 0)
        self.assertEqual(result["limit_applied"], 20)
        self.assertIn("500", result["limit_note"])
        self.assertIn("20", result["limit_note"])
        self.assertLessEqual(result["candidate_count"], 20)
        _, exact = self.lookup("AI 瓶頸 判斷 驗證", "--limit", "20")
        self.assertNotIn("limit_note", exact)
        # the installed workspace too (default workspace, called from an unrelated folder)
        code, real = run_lookup("--question", "判斷", "--limit", "500", "--json", cwd=self.elsewhere, script=self.installed_lookup)
        self.assertEqual(code, 0)
        self.assertEqual(real["limit_applied"], 20)
        self.assertLessEqual(real["candidate_count"], 20)

    def test_invalid_limit_or_missing_question_is_a_usage_error(self):
        for args in (["--question", "任何", "--limit", "0", "--json"], ["--limit", "3", "--json"]):
            done = run_raw("--workspace", str(self.workspace), *args, cwd=self.elsewhere)
            self.assertEqual(done.returncode, 2, args)
            self.assertNotIn("Traceback", done.stderr)

    def test_question_with_quotes_and_shell_characters_is_returned_unchanged(self):
        question = "他說 \"AI\" 會取代 $HOME 與 `反引號` 及 'it''s' ; & 判斷"
        code, result = self.lookup(question)
        self.assertEqual(code, 0)
        self.assertEqual(result["question"], question)

    def broken_workspace(self, name: str, config: str | None, registry_line: str | None = None, make_vault: bool = True) -> Path:
        root = self.base / name
        root.mkdir(parents=True)
        if config is not None:
            (root / "brain.json").write_text(config, encoding="utf-8")
        if make_vault:
            (root / "v").mkdir()
        if registry_line is not None:
            (root / "v" / "_registry").mkdir()
            for registry in ("sources.jsonl", "knowledge_points.jsonl", "relations.jsonl"):
                (root / "v" / "_registry" / registry).write_text(registry_line, encoding="utf-8")
        return root

    def test_unreadable_config_or_vault_or_registry_is_error_not_no_material(self):
        cases = {
            "brain.json missing": self.broken_workspace("c1", None),
            "brain.json is a list": self.broken_workspace("c2", "[]"),
            "brain.json is not json": self.broken_workspace("c3", "{bad"),
            "brain.json without vault": self.broken_workspace("c4", "{}"),
            "vault folder missing": self.broken_workspace("c5", '{"vault": "v"}', make_vault=False),
            "registry files missing": self.broken_workspace("c6", '{"vault": "v"}'),
            "registry has a broken line": self.broken_workspace("c7", '{"vault": "v"}', "{not json\n"),
        }
        for label, root in cases.items():
            with self.subTest(label):
                done = run_raw("--workspace", str(root), "--question", "判斷 驗證", "--json", cwd=self.elsewhere)
                self.assertEqual(done.returncode, 1)
                self.assertNotIn("Traceback", done.stderr)
                result = json.loads(done.stdout)
                self.assertEqual(result["status"], "error")
                self.assertNotEqual(result["status"], "no_material")
                self.assertTrue(result["message"])
                self.assertEqual(result["candidate_count"], 0)
                self.assertTrue(result["answer_rules"])
        self.assertIn("讀取失敗", json.loads(run_raw("--workspace", str(cases["registry files missing"]), "--question", "判斷", "--json", cwd=self.elsewhere).stdout)["message"])

    def test_empty_question_is_no_material_not_an_error(self):
        code, result = self.lookup("   ")
        self.assertEqual(code, 0)
        self.assertEqual(result["status"], "no_material")

    def test_default_workspace_is_the_file_location_from_any_cwd(self):
        code, result = run_lookup("--question", "AI 瓶頸 判斷 驗證", "--limit", "1", "--json", cwd=self.elsewhere, script=self.installed_lookup)
        self.assertEqual(code, 0)
        self.assertIn(result["status"], ("ready", "no_material"))
        self.assertEqual(Path(result["workspace_root"]), self.installed.resolve())

    def test_missing_workspace_config_is_a_clear_error_with_rules(self):
        code, result = run_lookup("--workspace", str(self.elsewhere), "--question", "任何", "--json", cwd=self.elsewhere)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["answer_rules"])


VAULT_VARIABLE = "CREATIVE_SECOND_BRAIN_VAULT"
ROOT_VARIABLE = "CREATIVE_SECOND_BRAIN_ROOT"
# Runs the lookup entry in this process, then reports what the process ended up bound to.
BINDING_PROBE = """
import contextlib, io, json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tools import brain_lookup
from tools.knowledge_material_selector import SelectionIndex
out = io.StringIO()
with contextlib.redirect_stdout(out):
    code = brain_lookup.main(["--workspace", sys.argv[2], "--question", sys.argv[3], "--json"])
result = json.loads(out.getvalue())
selector_root = str(SelectionIndex(Path(result["vault"])).root) if result.get("vault") else ""
print(json.dumps({"code": code, "result": result, "selector_root": selector_root,
                  "vault_variable": os.environ.get(sys.argv[4]), "root_variable": os.environ.get(sys.argv[5])}, ensure_ascii=False))
"""


class CrossWorkspaceLookupTests(unittest.TestCase):
    """A lookup started by a program that works on another workspace (a background AI started from workspace A carries A's
    vault and root in its environment) must read the workspace it was asked about, by that workspace's own brain.json."""

    @classmethod
    def setUpClass(cls):
        cls.base = SCRATCH / ("brain-lookup-cross-" + uuid4().hex)
        cls.asked = write_workspace(cls.base / "asked")
        cls.launcher = cls.base / "launcher"                      # workspace A: another vault name
        (cls.launcher / "Vault A").mkdir(parents=True)
        (cls.launcher / "brain.json").write_text(json.dumps({"vault": "Vault A"}), encoding="utf-8")
        cls.twin = write_workspace(cls.base / "twin")             # workspace A with the same vault name as the asked one
        cls.elsewhere = cls.base / "unrelated-project"
        cls.elsewhere.mkdir(parents=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def run_with(self, launcher: Path, vault_name: str, *command: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if not key.startswith("CREATIVE_SECOND_BRAIN")}
        env.update({VAULT_VARIABLE: vault_name, ROOT_VARIABLE: str(launcher)})
        return subprocess.run([sys.executable, "-B", *command], cwd=self.elsewhere, capture_output=True, text=True, encoding="utf-8", env=env)

    def assert_reads_only_the_asked_workspace(self, result: dict, text: str, launcher: Path):
        self.assertEqual(result["status"], "ready", result.get("message"))
        self.assertEqual(Path(result["workspace_root"]), self.asked.resolve())
        self.assertEqual(Path(result["vault"]), (self.asked / "vault").resolve())
        self.assertGreater(result["candidate_count"], 0)
        for candidate in result["candidates"]:
            for key in ("source_note_path", "application_note_path", "outcome_page_path"):
                if candidate.get(key):
                    self.assertTrue(Path(candidate[key]).is_relative_to(self.asked.resolve()), (key, candidate[key]))
        self.assertNotIn(json.dumps(str(launcher.resolve()), ensure_ascii=False)[1:-1], text)

    def test_a_vault_inherited_from_another_workspace_is_not_used_and_does_not_block_the_lookup(self):
        before = tree_hash(self.asked)
        done = self.run_with(self.launcher, "Vault A", str(LOOKUP), "--workspace", str(self.asked), "--question", "AI 瓶頸 判斷 驗證", "--json")
        self.assertEqual(done.returncode, 0, done.stdout[-600:] + done.stderr[-600:])
        self.assert_reads_only_the_asked_workspace(json.loads(done.stdout), done.stdout, self.launcher)
        self.assertEqual(tree_hash(self.asked), before)

    def test_a_root_inherited_from_another_workspace_is_not_used_even_when_the_vault_names_match(self):
        done = self.run_with(self.twin, "vault", "-c", BINDING_PROBE, str(REPO_ROOT), str(self.asked), "AI 瓶頸 判斷 驗證", VAULT_VARIABLE, ROOT_VARIABLE)
        self.assertEqual(done.returncode, 0, done.stderr[-1200:])
        probe = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(probe["code"], 0)
        self.assert_reads_only_the_asked_workspace(probe["result"], json.dumps(probe["result"], ensure_ascii=False), self.twin)
        # What the lookup process and the selector it calls were bound to: the asked workspace, not the launcher's.
        self.assertEqual(Path(probe["selector_root"]), self.asked.resolve())
        self.assertEqual(Path(probe["root_variable"]), self.asked.resolve())
        self.assertEqual(probe["vault_variable"], "vault")

    def test_a_broken_setting_of_the_asked_workspace_is_still_an_error(self):
        broken = self.base / "broken"
        broken.mkdir(exist_ok=True)
        (broken / "brain.json").write_text("{bad", encoding="utf-8")
        done = self.run_with(self.launcher, "Vault A", str(LOOKUP), "--workspace", str(broken), "--question", "判斷", "--json")
        self.assertEqual(done.returncode, 1)
        self.assertNotIn("Traceback", done.stderr)
        self.assertEqual(json.loads(done.stdout)["status"], "error")


if __name__ == "__main__":
    unittest.main()
