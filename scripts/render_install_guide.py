"""Build the novice guide from the single installation prompt and command registry."""
from __future__ import annotations
import argparse
import html
import json
import os
from pathlib import Path
from urllib.parse import quote
from package_paths import assert_plain_path

ROOT = Path(__file__).resolve().parents[1]


def render(root: Path, output: Path) -> None:
    template = (root / "assets/install-guide-template.html").read_text(encoding="utf-8")
    prompt = (root / "install-prompt.txt").read_text(encoding="utf-8").strip()
    registry = json.loads((root / "spec/command-registry.json").read_text(encoding="utf-8"))
    version = json.loads((root / "profiles" / "profiles.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not version:
        raise ValueError("product version is missing")
    rows = []
    skills = [(key, "brain-" + key) for key, definition in registry["commands"].items()
              if isinstance(definition, dict) and isinstance(definition.get("menu_order"), int) and not definition.get("alias_for")]
    if not skills:
        raise ValueError("command registry has no daily entries")
    for key, name in sorted(skills, key=lambda item: registry["commands"][item[0]]["menu_order"]):
        definition = registry["commands"][key]
        # The shared registry deliberately preserves the same `$brain-*`
        # invocation that OPS exposes.  The installer verifies host discovery
        # before it tells a user the entry is ready.
        invocation = definition["host_prompt"]
        title = html.escape(definition["home_title"])
        rows.append('<details class="prompt"><summary>' + title + '</summary><div class="prompt-body"><p>'
                    + html.escape(definition["purpose"]) + '</p><textarea id="daily-' + key
                    + '" name="daily-' + key + '" class="daily-command" readonly aria-label="' + title + '">'
                    + html.escape(invocation) + '</textarea><button class="copy" type="button">複製指令</button>'
                    + '<span class="status-line" role="status" aria-live="polite"></span></div></details>')
    for token, value in {"@@INSTALL_PROMPT@@": html.escape(prompt), "@@COMMANDS@@": "\n".join(rows),
                         "@@VERSION@@": html.escape(version), "@@COMMAND_COUNT@@": str(len(skills))}.items():
        if template.count(token) != 1:
            raise ValueError("guide template slot is missing or duplicated: " + token)
        template = template.replace(token, value)
    # Use the same offline material and passage layer as workspace readers.
    import importlib.util
    shell_path = root / "runtime/workspace/tools/reader_shell.py"
    spec = importlib.util.spec_from_file_location("install_reader_shell", shell_path)
    shell = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shell)
    template = shell.apply_visual(template, 'home')
    assert_plain_path(output)
    stardust = root / "runtime/workspace/assets/brand/gold-silver-stardust.png"
    if not stardust.is_file():
        raise ValueError("install guide stardust asset is missing")
    relative_stardust = quote(os.path.relpath(stardust, output.parent).replace("\\", "/"), safe="/")
    asset_style = '<style id="brain-reader-assets">:root{--brain-stardust:url("' + relative_stardust + '");}</style>'
    if "</head>" not in template:
        raise ValueError("install guide has no document head")
    template = template.replace("</head>", asset_style + "</head>", 1)
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(template)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    render(ROOT, args.output)
