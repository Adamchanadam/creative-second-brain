"""AI setup link coverage for the portable home renderer."""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT / "tools"))
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import setup_workspace
import workspace_home_renderer as home


class HomeAiSetupTests(unittest.TestCase):
    def test_optional_link_is_unique_and_removing_it_restores_identical_page(self):
        with tempfile.TemporaryDirectory(prefix="home-ai-") as folder:
            workspace = Path(folder)
            runtime = workspace / "runtime"
            (runtime / "tools").mkdir(parents=True)
            shutil.copyfile(ROOT / "tools" / "daily_home.py", runtime / "tools" / "daily_home.py")
            shutil.copytree(ROOT / "assets" / "guide", runtime / "assets" / "guide")
            args = dict(
                runtime=runtime,
                workspace=workspace,
                vault_rel="My notes & ideas",
                display_name="測試",
                package_version="1.0.0",
                entries=setup_workspace.entry_contract("public-portable"),
            )
            absent = home.document(**args)
            self.assertNotIn('class="nebula-ai"', absent)
            (runtime / "tools" / "knowledge_cradle.py").write_text("# fixture", encoding="utf-8")
            present = home.document(**args)
            href = re.search(r'<a class="nebula-cta" href="([^"]+)"', absent).group(1)
            link = '<a class="nebula-ai" href="' + href + '#ai-setup">AI 設置</a>'
            self.assertEqual(present.count('class="nebula-ai"'), 1)
            self.assertIn(link, present)
            self.assertRegex(present, r'<a class="nebula-cta"[^>]*>.*?</a>' + re.escape(link))
            self.assertEqual(present.replace(link + home.NEBULA_AI_STYLE, "", 1), absent)

    def test_missing_cta_fails_closed_when_cradle_exists(self):
        fake = type(
            "Daily",
            (),
            {
                "render": lambda **kwargs: '<h1>home</h1><section id="prompts"></section>',
                "think_alias_markup": lambda *args: "",
            },
        )
        with patch.object(home, "_daily_home", return_value=fake), tempfile.TemporaryDirectory(prefix="home-ai-") as folder:
            runtime = Path(folder) / "runtime"
            (runtime / "tools").mkdir(parents=True)
            (runtime / "tools" / "knowledge_cradle.py").write_text("# fixture", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "daily-home renderer shape changed"):
                home.document(
                    runtime=runtime,
                    workspace=Path(folder),
                    vault_rel="vault",
                    display_name="test",
                    package_version="1.0.0",
                    entries=setup_workspace.entry_contract("public-portable"),
                )


if __name__ == "__main__":
    unittest.main()

