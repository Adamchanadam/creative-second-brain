"""global_entry: install / status / remove user-level entries, always against a temporary --home."""
from __future__ import annotations

import json
import os
import re
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
TOOL = REPO_ROOT / "tools" / "global_entry.py"
SCRATCH = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
MARKER = "creative-second-brain global entry v1"
REGISTRY_REL = ".creative-second-brain/workspaces.json"


def run_tool(command: str, home: Path, workspace: Path | None = None, *extra: str) -> tuple[int, dict]:
    args = [sys.executable, "-B", str(TOOL), command, "--home", str(home), "--json"]
    if workspace:
        args += ["--workspace", str(workspace)]
    done = subprocess.run([*args, *extra], capture_output=True, text=True, encoding="utf-8", cwd=str(home.parent))
    return done.returncode, json.loads(done.stdout)


def make_workspace(root: Path, name: str, entries: list[str], claude_rules: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True)
    contract = [{"skill": skill, "purpose": f"{skill} 的用途", "key": skill[6:], "host_prompt": f"${skill}"} for skill in entries]
    (root / "brain.json").write_text(json.dumps({
        "display_name": name, "vault_name": "v", "vault": "v", "runtime_python": sys.executable, "entry_contract": contract,
    }, ensure_ascii=False), encoding="utf-8")
    for skill in entries:
        folder = root / ".agents" / "skills" / skill
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: {skill} 的工作區描述\n---\n\n正文\n", encoding="utf-8")
    for skill, description in (claude_rules or {}).items():
        folder = root / ".claude" / "skills" / skill
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: {description}\n---\n\nOPS 本地補充\n", encoding="utf-8")
    return root


def run_raw(command: str, home: Path, workspace: Path, *extra: str) -> tuple[int, str, str]:
    done = subprocess.run([sys.executable, "-B", str(TOOL), command, "--home", str(home), "--workspace", str(workspace), "--json", *extra],
                          capture_output=True, text=True, encoding="utf-8", cwd=str(home.parent))
    return done.returncode, done.stdout, done.stderr


def make_link(link: Path, target: Path) -> bool:
    """A junction (Windows) or symlink pointing at `target`; False when the platform or rights do not allow it."""
    try:
        if os.name == "nt":
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)  # bytes: mklink prints in the console code page
            return done.returncode == 0 and link.exists()
        os.symlink(target, link, target_is_directory=True)
        return True
    except OSError:
        return False


def drop_link(link: Path) -> None:
    try:
        os.rmdir(link)  # removes a junction / directory symlink without touching its target
    except OSError:
        try:
            os.unlink(link)
        except OSError:
            pass


def files_under(home: Path) -> dict[str, bytes]:
    return {str(p.relative_to(home)).replace("\\", "/"): p.read_bytes() for p in sorted(home.rglob("*")) if p.is_file()}


class GlobalEntryTests(unittest.TestCase):
    def setUp(self):
        self.base = SCRATCH / ("global-entry-test-" + uuid4().hex)
        self.home = self.base / "home"
        self.home.mkdir(parents=True)
        self.ws = make_workspace(self.base / "ws-one", "工作區甲", ["brain-think", "brain-url", "brain-nebula"])
        self.addCleanup(shutil.rmtree, self.base, True)

    def contract_names(self, workspace: Path) -> list[str]:
        config = json.loads((workspace / "brain.json").read_text(encoding="utf-8"))
        return [e["skill"] for e in config["entry_contract"] if e["skill"].startswith("brain-")]

    def expected_relatives(self, workspace: Path) -> set[str]:
        rels = {
            ".claude/skills/brain-ask/SKILL.md",
            ".agents/skills/brain-ask/SKILL.md",
            ".agents/skills/brain-ask/agents/openai.yaml",
            REGISTRY_REL,
        }
        rels |= {f".claude/skills/{n}/SKILL.md" for n in self.contract_names(workspace) if n != "brain-ask"}
        return rels

    def test_install_creates_all_targets_and_reads_back(self):
        code, result = run_tool("install", self.home, self.ws)
        self.assertEqual(code, 0)
        self.assertTrue(result["verified"])
        self.assertEqual({t["relative"] for t in result["targets"]}, self.expected_relatives(self.ws))
        self.assertEqual({t["action"] for t in result["targets"]}, {"create"})
        self.assertEqual(set(files_under(self.home)), self.expected_relatives(self.ws))
        registry = json.loads((self.home / REGISTRY_REL).read_text(encoding="utf-8"))
        self.assertEqual(registry["default"], "ws-one")  # the folder name, not display_name 工作區甲
        self.assertEqual(registry["workspaces"], [{"name": "ws-one", "root": str(self.ws.resolve()), "python": sys.executable}])

    def test_entry_names_follow_entry_contract_and_descriptions_are_reused(self):
        run_tool("install", self.home, self.ws)
        forwarders = {p.parent.name for p in (self.home / ".claude/skills").glob("*/SKILL.md")} - {"brain-ask"}
        self.assertEqual(forwarders, set(self.contract_names(self.ws)))
        text = (self.home / ".claude/skills/brain-url/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("brain-url 的工作區描述", text)
        claude_at = text.index("<root>/.claude/skills/brain-url/SKILL.md")
        agents_at = text.index("<root>/.agents/skills/brain-url/SKILL.md")
        self.assertLess(claude_at, agents_at)  # .claude is read first, .agents only when it does not exist
        self.assertIn("否則", text[claude_at:agents_at])

    def test_second_install_writes_nothing(self):
        run_tool("install", self.home, self.ws)
        before = files_under(self.home)
        code, result = run_tool("install", self.home, self.ws)
        self.assertEqual(code, 0)
        self.assertEqual(result["written"], 0)
        self.assertEqual({t["action"] for t in result["targets"]}, {"unchanged"})
        self.assertEqual(before, files_under(self.home))
        self.assertFalse((self.home / ".creative-second-brain/backups").exists())

    def test_existing_different_entry_is_backed_up_then_overwritten(self):
        target = self.home / ".claude/skills/brain-think/SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("舊的手寫轉介\n", encoding="utf-8")
        code, result = run_tool("install", self.home, self.ws)
        self.assertEqual(code, 0)
        actions = {t["relative"]: t["action"] for t in result["targets"]}
        self.assertEqual(actions[".claude/skills/brain-think/SKILL.md"], "overwrite")
        self.assertIn(MARKER, target.read_text(encoding="utf-8"))
        backup = Path(result["backup_dir"]) / ".claude/skills/brain-think/SKILL.md"
        self.assertEqual(backup.read_text(encoding="utf-8"), "舊的手寫轉介\n")
        self.assertTrue(Path(result["backup_dir"]).is_relative_to(self.home / ".creative-second-brain/backups"))

    def test_status_is_read_only_and_reports_each_entry(self):
        code, missing = run_tool("status", self.home, self.ws)
        self.assertEqual(code, 1)
        self.assertEqual({t["state"] for t in missing["targets"]}, {"missing"})
        self.assertEqual(files_under(self.home), {})
        run_tool("install", self.home, self.ws)
        before = files_under(self.home)
        code, ok = run_tool("status", self.home, self.ws)
        self.assertEqual(code, 0)
        self.assertTrue(ok["ok"])
        self.assertEqual({t["state"] for t in ok["targets"]}, {"ok"})
        self.assertEqual(ok["registry"]["workspaces"][0]["name"], "ws-one")
        self.assertTrue(ok["registry"]["workspaces"][0]["root_has_brain_json"])
        self.assertEqual(before, files_under(self.home))
        (self.home / ".claude/skills/brain-url/SKILL.md").write_text("被改過\n", encoding="utf-8")
        (self.home / ".claude/skills/brain-think/SKILL.md").write_text(f"<!-- {MARKER} -->\n舊版\n", encoding="utf-8")
        _, bad = run_tool("status", self.home, self.ws)
        states = {t["relative"]: t["state"] for t in bad["targets"]}
        self.assertEqual(states[".claude/skills/brain-url/SKILL.md"], "not_ours")
        self.assertEqual(states[".claude/skills/brain-think/SKILL.md"], "outdated")
        self.assertFalse(bad["ok"])

    def test_remove_deletes_only_marked_files_and_registry_item(self):
        run_tool("install", self.home, self.ws)
        unmarked = self.home / ".claude/skills/brain-url/SKILL.md"
        unmarked.write_text("用戶自己的版本\n", encoding="utf-8")
        outsider = self.home / ".claude/skills/other-skill/SKILL.md"
        outsider.parent.mkdir(parents=True)
        outsider.write_text("別人的技能\n", encoding="utf-8")
        code, result = run_tool("remove", self.home, self.ws)
        self.assertEqual(code, 0)
        actions = {t["relative"]: t["action"] for t in result["targets"]}
        self.assertEqual(actions[".claude/skills/brain-url/SKILL.md"], "kept_not_ours")
        self.assertEqual(actions[".claude/skills/brain-think/SKILL.md"], "removed")
        self.assertEqual(unmarked.read_text(encoding="utf-8"), "用戶自己的版本\n")
        self.assertEqual(outsider.read_text(encoding="utf-8"), "別人的技能\n")
        self.assertFalse((self.home / ".claude/skills/brain-ask").exists())
        self.assertFalse((self.home / ".agents/skills/brain-ask").exists())
        self.assertFalse((self.home / ".claude/skills/brain-think").exists())
        self.assertFalse((self.home / REGISTRY_REL).exists())
        self.assertEqual(set(files_under(self.home)), {".claude/skills/brain-url/SKILL.md", ".claude/skills/other-skill/SKILL.md"})

    def test_multiple_workspaces_share_one_registry(self):
        other = make_workspace(self.base / "ws-two", "工作區乙", ["brain-think", "brain-url", "brain-nebula"])
        run_tool("install", self.home, self.ws)
        code, result = run_tool("install", self.home, other)
        self.assertEqual(code, 0)
        registry = json.loads((self.home / REGISTRY_REL).read_text(encoding="utf-8"))
        self.assertEqual([w["name"] for w in registry["workspaces"]], ["ws-one", "ws-two"])
        self.assertEqual(registry["default"], "ws-one")
        actions = {t["relative"]: t["action"] for t in result["targets"]}
        self.assertEqual(actions[".claude/skills/brain-ask/SKILL.md"], "unchanged")
        self.assertEqual(actions[REGISTRY_REL], "overwrite")
        # removing one keeps the shared entries and the other registry item
        _, removed = run_tool("remove", self.home, self.ws)
        registry = json.loads((self.home / REGISTRY_REL).read_text(encoding="utf-8"))
        self.assertEqual([w["name"] for w in registry["workspaces"]], ["ws-two"])
        self.assertEqual(registry["default"], "ws-two")
        self.assertTrue((self.home / ".claude/skills/brain-ask/SKILL.md").exists())
        self.assertEqual(removed["other_workspaces_remaining"], ["ws-two"])
        code, status = run_tool("status", self.home, other)
        self.assertEqual(code, 0)

    def test_reinstall_same_workspace_updates_its_item_and_name_clash_is_refused(self):
        run_tool("install", self.home, self.ws, "--name", "別名")
        run_tool("install", self.home, self.ws, "--name", "新別名")
        registry = json.loads((self.home / REGISTRY_REL).read_text(encoding="utf-8"))
        self.assertEqual([w["name"] for w in registry["workspaces"]], ["新別名"])
        other = make_workspace(self.base / "ws-two", "工作區乙", ["brain-think"])
        done = subprocess.run([sys.executable, "-B", str(TOOL), "install", "--home", str(self.home), "--workspace", str(other),
                               "--name", "新別名", "--json"], capture_output=True, text=True, encoding="utf-8")
        self.assertNotEqual(done.returncode, 0)
        registry = json.loads((self.home / REGISTRY_REL).read_text(encoding="utf-8"))
        self.assertEqual(len(registry["workspaces"]), 1)

    def test_entry_content_has_no_absolute_paths_and_marker(self):
        run_tool("install", self.home, self.ws)
        drive_path = re.compile(r"[A-Za-z]:[\\/]")
        for relative, data in files_under(self.home).items():
            if relative == REGISTRY_REL:
                continue
            text = data.decode("utf-8")
            self.assertIn(MARKER, text, relative)
            self.assertNotIn(str(self.ws), text, relative)
            self.assertNotIn(str(self.home), text, relative)
            self.assertNotIn(self.ws.name, text, relative)
            self.assertIsNone(drive_path.search(text), relative)
            self.assertNotIn("\r", text)

    def test_brain_ask_entries_state_read_only_boundary(self):
        run_tool("install", self.home, self.ws)
        for relative in (".claude/skills/brain-ask/SKILL.md", ".agents/skills/brain-ask/SKILL.md"):
            text = (self.home / relative).read_text(encoding="utf-8")
            self.assertIn("brain_lookup.py", text)
            self.assertIn("answer_rules", text)
            self.assertIn("brain-think", text)
            self.assertIn("workspaces.json", text)
        yaml = (self.home / ".agents/skills/brain-ask/agents/openai.yaml").read_text(encoding="utf-8")
        self.assertIn("default_prompt:", yaml)
        self.assertIn(MARKER, yaml)

    def test_forwarder_reads_claude_rule_file_when_workspace_has_one_else_agents(self):
        ws = make_workspace(self.base / "ws-ops", "OPS", ["brain-tidy", "brain-url"], {"brain-tidy": "OPS 專用說明"})
        run_tool("install", self.home, ws)
        tidy = (self.home / ".claude/skills/brain-tidy/SKILL.md").read_text(encoding="utf-8")
        url = (self.home / ".claude/skills/brain-url/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("OPS 專用說明", tidy)  # description follows the same order as the rules file
        self.assertIn("brain-url 的工作區描述", url)  # no .claude file: description from .agents
        for text, name in ((tidy, "brain-tidy"), (url, "brain-url")):
            self.assertLess(text.index(f"<root>/.claude/skills/{name}/SKILL.md"), text.index(f"<root>/.agents/skills/{name}/SKILL.md"))
        # one fixed text for both workspaces: only the name differs
        self.assertEqual(tidy.replace("brain-tidy", "X").split("---\n", 2)[2], url.replace("brain-url", "X").split("---\n", 2)[2])

    def test_entry_texts_use_single_quoted_question_and_quoted_paths(self):
        run_tool("install", self.home, self.ws)
        ps_example = "& '<python>' '<root>\\tools\\brain_lookup.py' --question '<問題>' --json"
        bash_example = "'<python>' '<root>/tools/brain_lookup.py' --question '<問題>' --json"
        for relative in (".claude/skills/brain-ask/SKILL.md", ".agents/skills/brain-ask/SKILL.md"):
            text = (self.home / relative).read_text(encoding="utf-8")
            self.assertIn(ps_example, text, relative)
            self.assertIn(bash_example, text, relative)
            self.assertIn("`''`", text, relative)  # PowerShell: a single quote inside the question
            self.assertIn("`'\\''`", text, relative)  # Bash: a single quote inside the question
            self.assertNotIn('--question "', text, relative)
            self.assertNotIn('"<python>"', text, relative)
        for name in self.contract_names(self.ws):
            text = (self.home / f".claude/skills/{name}/SKILL.md").read_text(encoding="utf-8")
            self.assertNotIn('--question "', text)
            self.assertNotIn('"<python>"', text)

    def test_registry_name_defaults_to_folder_name_and_name_option_overrides(self):
        _, result = run_tool("install", self.home, self.ws)
        self.assertEqual(result["workspace"]["name"], "ws-one")
        _, result = run_tool("install", self.home, self.ws, "--name", "自訂名稱")
        self.assertEqual(result["workspace"]["name"], "自訂名稱")
        registry = json.loads((self.home / REGISTRY_REL).read_text(encoding="utf-8"))
        self.assertEqual([w["name"] for w in registry["workspaces"]], ["自訂名稱"])

    def test_same_name_from_a_different_root_is_refused_in_plain_words_and_writes_nothing(self):
        run_tool("install", self.home, self.ws, "--name", "共用名")
        other = make_workspace(self.base / "ws-two", "乙", ["brain-think"])
        before = files_under(self.home)
        code, stdout, stderr = run_raw("install", self.home, other, "--name", "共用名")
        self.assertNotEqual(code, 0)
        self.assertIn("已被另一個工作區使用", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(before, files_under(self.home))
        # the folder-name default clashes the same way
        run_tool("install", self.home, self.ws)  # registers the name ws-one
        clash = make_workspace(self.base / "clash" / "ws-one", "丙", ["brain-think"])
        before = files_under(self.home)
        code, _, stderr = run_raw("install", self.home, clash)
        self.assertNotEqual(code, 0)
        self.assertIn("--name", stderr)
        self.assertEqual(before, files_under(self.home))
        # renaming an already registered root onto another item's name is refused too
        run_tool("install", self.home, other)  # registers ws-two
        before = files_under(self.home)
        code, _, _ = run_raw("install", self.home, self.ws, "--name", "ws-two")
        self.assertNotEqual(code, 0)
        self.assertEqual(before, files_under(self.home))

    def test_remove_restores_the_pre_install_originals_bit_for_bit(self):
        originals = {
            ".claude/skills/brain-think/SKILL.md": b"user hand-written forwarder\r\n\xff\xfe raw bytes\n",
            ".agents/skills/brain-ask/SKILL.md": "其他工具的 brain-ask\n".encode("utf-8"),
        }
        for relative, data in originals.items():
            target = self.home / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        run_tool("install", self.home, self.ws)
        # a second install with changed content, even within the same second, must not replace the original backup
        rules = self.ws / ".agents/skills/brain-think/SKILL.md"
        rules.write_text("---\nname: brain-think\ndescription: 改過的描述\n---\n", encoding="utf-8")
        _, second = run_tool("install", self.home, self.ws)
        self.assertEqual({t["relative"]: t["action"] for t in second["targets"]}[".claude/skills/brain-think/SKILL.md"], "overwrite")
        code, result = run_tool("remove", self.home, self.ws)
        self.assertEqual(code, 0)
        actions = {t["relative"]: t["action"] for t in result["targets"]}
        self.assertEqual(actions[".claude/skills/brain-think/SKILL.md"], "restored")
        self.assertEqual(actions[".agents/skills/brain-ask/SKILL.md"], "restored")
        self.assertEqual(actions[".claude/skills/brain-ask/SKILL.md"], "removed")  # no original: deleted
        self.assertEqual(actions[".agents/skills/brain-ask/agents/openai.yaml"], "removed")
        for relative, data in originals.items():
            self.assertEqual((self.home / relative).read_bytes(), data, relative)
        self.assertEqual(len(result["restored_from"]), 2)
        for backup in result["restored_from"]:
            self.assertTrue(Path(backup).is_relative_to(self.home / ".creative-second-brain/backups"))
        self.assertTrue(result["backup_dir"].endswith("backups"))
        self.assertFalse((self.home / ".claude/skills/brain-url").exists())
        self.assertFalse((self.home / REGISTRY_REL).exists())
        left = set(files_under(self.home))
        self.assertEqual({r for r in left if not r.startswith(".creative-second-brain/backups/")}, set(originals))

    def test_remove_keeps_shared_entries_while_other_workspaces_remain_then_restores_original(self):
        original = self.home / ".claude/skills/brain-think/SKILL.md"
        original.parent.mkdir(parents=True)
        original.write_text("原件\n", encoding="utf-8")
        other = make_workspace(self.base / "ws-two", "乙", ["brain-think", "brain-url", "brain-nebula"])
        run_tool("install", self.home, self.ws)
        run_tool("install", self.home, other)
        _, result = run_tool("remove", self.home, self.ws)
        self.assertEqual({t["action"] for t in result["targets"]} - {"absent"}, {"kept_other_workspaces_registered"})
        self.assertIn(MARKER, original.read_text(encoding="utf-8"))  # still ours and shared: not restored yet
        _, result = run_tool("remove", self.home, other)
        self.assertEqual(original.read_text(encoding="utf-8"), "原件\n")

    def test_links_pointing_outside_home_are_refused_on_install(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("home 以外的檔\n", encoding="utf-8")
        link = self.home / ".claude/skills/brain-think"
        link.parent.mkdir(parents=True)
        if not make_link(link, outside):
            self.skipTest("cannot create a junction or symlink here (no rights or unsupported platform)")
        self.addCleanup(drop_link, link)
        before_outside = (outside / "SKILL.md").read_bytes()
        code, result = run_tool("install", self.home, self.ws)
        self.assertEqual(code, 1)
        self.assertFalse(result["verified"])
        self.assertEqual(result["written"], 0)
        self.assertTrue(any("brain-think" in path for path in result["refused"]))
        self.assertEqual((outside / "SKILL.md").read_bytes(), before_outside)
        self.assertEqual([p.name for p in outside.iterdir()], ["SKILL.md"])
        self.assertFalse((self.home / REGISTRY_REL).exists())  # nothing at all was written
        self.assertFalse((self.home / ".claude/skills/brain-ask").exists())

    def test_links_pointing_outside_home_are_refused_on_remove(self):
        run_tool("install", self.home, self.ws)
        link = self.home / ".claude/skills/brain-think"
        shutil.rmtree(link)
        outside = self.base / "outside"
        outside.mkdir()
        marked = f"<!-- {MARKER} -->\n外面的檔案\n"
        (outside / "SKILL.md").write_text(marked, encoding="utf-8")
        if not make_link(link, outside):
            self.skipTest("cannot create a junction or symlink here (no rights or unsupported platform)")
        self.addCleanup(drop_link, link)
        code, result = run_tool("remove", self.home, self.ws)
        self.assertEqual(code, 1)
        actions = {t["relative"]: t["action"] for t in result["targets"]}
        self.assertEqual(actions[".claude/skills/brain-think/SKILL.md"], "refused_outside_home")
        self.assertEqual((outside / "SKILL.md").read_text(encoding="utf-8"), marked)
        self.assertTrue(result["refused"])
        self.assertEqual(actions[".claude/skills/brain-ask/SKILL.md"], "removed")  # in-home targets are still handled

    def test_malformed_registry_stops_with_plain_words_and_is_never_overwritten(self):
        payloads = {
            "invalid json": "{oops",
            "top level is a list": json.dumps([{"name": "a", "root": "r"}]),
            "workspaces is not a list": json.dumps({"workspaces": {"a": 1}}),
            "row is a string": json.dumps({"default": "a", "workspaces": ["just-a-string"]}),
            "row missing root": json.dumps({"default": "a", "workspaces": [{"name": "a", "python": "p"}]}),
            "row missing name": json.dumps({"default": "a", "workspaces": [{"root": "r", "python": "p"}]}),
        }
        for label, payload in payloads.items():
            with self.subTest(label):
                shutil.rmtree(self.home)
                registry = self.home / REGISTRY_REL
                registry.parent.mkdir(parents=True)
                registry.write_text(payload, encoding="utf-8")
                before = files_under(self.home)
                for command in ("install", "status", "remove"):
                    code, stdout, stderr = run_raw(command, self.home, self.ws)
                    self.assertNotEqual(code, 0, command)
                    self.assertNotIn("Traceback", stderr, command)
                    if command == "status":
                        self.assertTrue(json.loads(stdout)["registry"]["state"].startswith("invalid"))
                    else:
                        self.assertIn("登記表有問題", stderr, command)
                    self.assertEqual(before, files_under(self.home), command)

    def test_registry_with_bom_is_read_and_rewritten_without_bom_keeping_user_fields(self):
        registry = self.home / REGISTRY_REL
        registry.parent.mkdir(parents=True)
        other_root = str(self.base / "nowhere")
        registry.write_bytes(b"\xef\xbb\xbf" + json.dumps({
            "schema": "creative_second_brain_workspaces_v1", "default": "乙", "note": "用戶備註",
            "workspaces": [{"name": "乙", "root": other_root, "python": "py", "comment": "保留"}],
        }, ensure_ascii=False).encode("utf-8"))
        code, status = run_tool("status", self.home, self.ws)
        self.assertEqual(status["registry"]["state"], "ok")
        code, _ = run_tool("install", self.home, self.ws)
        self.assertEqual(code, 0)
        raw = registry.read_bytes()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
        data = json.loads(raw.decode("utf-8"))
        self.assertEqual(data["note"], "用戶備註")
        self.assertEqual(data["default"], "乙")
        self.assertEqual([w["name"] for w in data["workspaces"]], ["乙", "ws-one"])
        self.assertEqual(data["workspaces"][0]["comment"], "保留")
        run_tool("remove", self.home, self.ws)
        data = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(data["note"], "用戶備註")
        self.assertEqual(data["workspaces"], [{"name": "乙", "root": other_root, "python": "py", "comment": "保留"}])

    def test_public_entry_contract_drives_the_names_and_never_forwards_brain_ask(self):
        # The package's own entry contract and generated entries (nothing hard-coded here) build a PUBLIC-shaped
        # workspace; install goes only to a temporary home.
        package = REPO_ROOT.parents[1]
        probe = (
            "import json, sys; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); import setup_workspace as s; "
            "print(json.dumps({'contract': s.entry_contract('public-portable'), "
            "'files': {k: v.decode('utf-8') for k, v in s.skill_entry_payloads('public-portable').items() if k.endswith('SKILL.md')}}, ensure_ascii=False))"
        )
        done = subprocess.run([sys.executable, "-B", "-c", probe, str(package / "scripts")], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(done.returncode, 0, done.stderr)
        built = json.loads(done.stdout)
        ws = self.base / "public-ws"
        ws.mkdir()
        (ws / "brain.json").write_text(json.dumps({"display_name": "公開", "vault": "v", "runtime_python": sys.executable,
                                                   "entry_contract": built["contract"]}, ensure_ascii=False), encoding="utf-8")
        for relative, text in built["files"].items():
            target = ws / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        self.assertIn(".agents/skills/brain-ask/SKILL.md", built["files"])
        code, result = run_tool("install", self.home, ws)
        self.assertEqual(code, 0)
        expected = {e["skill"] for e in built["contract"] if e["skill"].startswith("brain-")}
        self.assertNotIn("brain-ask", expected)  # the canonical contract lists no read-only entry
        installed = {p.parent.name for p in (self.home / ".claude/skills").glob("*/SKILL.md")}
        self.assertEqual(installed, expected | {"brain-ask"})
        # brain-ask is the fixed read-only entry, not a forwarder to the workspace's own brain-ask
        ask = (self.home / ".claude/skills/brain-ask/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("全域只讀快答入口", ask)
        self.assertNotIn("全域轉介入口", ask)
        self.assertNotIn(self.home, [Path.home()])

if __name__ == "__main__":
    unittest.main()
