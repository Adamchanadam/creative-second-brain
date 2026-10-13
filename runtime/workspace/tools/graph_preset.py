"""One generic Obsidian graph preset for the portable second brain.

The preset is deliberately data-free.  It gives a new vault the same visual
language as the operational graph while leaving an existing user graph file
untouched.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json


GRAPH_PRESET: dict[str, object] = {
    "collapse-filter": False,
    "search": "path:concepts",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": True,
    # A new portable vault needs to show its first valid concepts even before
    # enough evidence-backed relations exist to make a connected component.
    "showOrphans": True,
    "collapse-color-groups": True,
    "colorGroups": [
        {"query": "path:concepts [graph_group:hub]", "color": {"a": 1, "rgb": 15453067}},
        {"query": "path:concepts [graph_group:tension]", "color": {"a": 1, "rgb": 13666160}},
        {"query": "path:concepts [graph_group:taste_signal]", "color": {"a": 1, "rgb": 11833005}},
        {"query": "path:concepts [graph_group:application_scene]", "color": {"a": 1, "rgb": 10731148}},
    ],
    "collapse-display": False,
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "collapse-forces": False,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 300,
    "scale": 1.4769860980685305,
    "close": False,
}

# Camera position is personal interaction state, not a condition for the
# portable visual preset to be useful after the user has panned or zoomed.
REQUIRED_PRESET_KEYS = tuple(key for key in GRAPH_PRESET if key not in {"scale", "close"})


def default_graph_settings() -> dict[str, object]:
    return deepcopy(GRAPH_PRESET)


def graph_settings_status(vault: Path) -> dict[str, object]:
    graph_file = vault / ".obsidian" / "graph.json"
    expected = {key: GRAPH_PRESET[key] for key in REQUIRED_PRESET_KEYS}
    if not graph_file.exists():
        return {"exists": False, "ready": False, "expected": expected, "actual": {}, "issues": ["missing_graph_json"]}
    try:
        settings = json.loads(graph_file.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        return {"exists": True, "ready": False, "expected": expected, "actual": {}, "issues": ["invalid_graph_json"]}
    actual = {key: settings.get(key) for key in expected}
    issues = [key for key, value in expected.items() if actual.get(key) != value]
    return {"exists": True, "ready": not issues, "expected": expected, "actual": actual, "issues": issues}


def ensure_initial_graph_preset(vault: Path) -> str:
    """Create the portable default once; never replace user graph settings."""
    graph_file = vault / ".obsidian" / "graph.json"
    if graph_file.exists():
        return "preserved_existing"
    graph_file.parent.mkdir(parents=True, exist_ok=True)
    graph_file.write_text(json.dumps(default_graph_settings(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "installed_default"
