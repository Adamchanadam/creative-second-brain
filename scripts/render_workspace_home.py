"""Render the existing portable daily-home template with the chosen display name."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime" / "workspace" / "tools"))
from workspace_home_renderer import render as render_portable_home


def public_entries() -> list[dict[str, str]]:
    registry = json.loads((ROOT / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    required = ("url", "text", "image", "think", "grow", "create", "tidy", "guide", "nebula", "check", "resume", "fix", "research", "update")
    if any(name not in registry for name in required): raise ValueError("public command registry is incomplete")
    skills = {"think": "brain-think", "url": "brain-url", "text": "brain-text", "image": "brain-image",
              "grow": "brain-grow", "create": "brain-create", "research": "brain-research", "tidy": "brain-tidy",
              "guide": "brain-guide", "nebula": "brain-nebula", "check": "brain-check", "resume": "brain-resume",
               "fix": "brain-fix", "update": "brain-update"}
    rows: list[dict[str, str]] = []
    for name, skill in sorted(skills.items(), key=lambda item: registry[item[0]]["menu_order"]):
        definition = registry[name]
        rows.append({"key": name, "skill": skill, "home_title": str(definition["home_title"]),
                     "purpose": str(definition["purpose"]), "host_prompt": str(definition["host_prompt"])})
    return rows


def render(*, runtime: Path, workspace: Path, vault_rel: str, display_name: str, package_version: str) -> Path:
    return render_portable_home(runtime=runtime, workspace=workspace, vault_rel=vault_rel,
                                display_name=display_name, package_version=package_version,
                                entries=public_entries())
