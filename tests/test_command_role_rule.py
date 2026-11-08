"""The inventory role rule (scripts/command_rules.command_role) must stay compatible with installed updaters."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.dont_write_bytecode = True
import ast  # noqa: E402
from command_rules import COMMAND_ROLES, command_role, entry_kind, is_menu_command  # noqa: E402


def frozen_installed_updater_roles(registry: dict) -> dict:
    """The formula used by installed 1.9.22 to 1.9.25 updaters to verify a new package.

    Their ``tools/verify_package.py`` reads only ``alias_for`` and an integer
    ``menu_order``.  A new package is rejected unless its inventory roles equal
    this result, so this formula must never change.
    """
    return {key: "compatibility_alias" if d.get("alias_for") else "canonical"
            for key, d in registry.items() if isinstance(d.get("menu_order"), int)}


# Names that the OLD modules (1.9.22 to 1.9.25) of an installed workspace export.  An old
# updater runs the workspace's own tools/verify_package.py first, so its old
# ``package_paths`` / ``package_inventory`` (and, on the archive route, ``verify_package`` /
# ``delivery_contract``) are already in ``sys.modules`` when it loads this package's scripts.
# A name imported from them that is not in these sets raises ImportError mid-upgrade.
OLD_PACKAGE_INVENTORY_NAMES = frozenset({
    "HOME_ALIAS_RELATIVE", "INVENTORY_SCHEMA", "RUNTIME_PREFIX", "compiled_home_alias_metadata", "digest",
    "is_runtime_member_allowed", "listed_runtime_files", "load_inventory", "runtime_allowlist",
    "validate_compiled_home_alias_metadata", "validate_provenance",
})
OLD_PACKAGE_PATHS_NAMES = frozenset({"assert_plain_path"})
OLD_MODULE_NAMES = {"package_inventory": OLD_PACKAGE_INVENTORY_NAMES, "package_paths": OLD_PACKAGE_PATHS_NAMES}
UPDATER_LOADED_SCRIPTS = ("manage_workspace.py", "setup_workspace.py", "render_install_guide.py",
                          "render_workspace_home.py", "verify_package.py")


def load_registry() -> dict:
    return json.loads((ROOT / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]


class CommandRoleRuleTests(unittest.TestCase):
    def test_role_is_alias_or_canonical_only(self) -> None:
        self.assertEqual(COMMAND_ROLES, frozenset({"canonical", "compatibility_alias"}))
        self.assertEqual(command_role({"alias_for": "think"}), "compatibility_alias")
        self.assertEqual(command_role({"menu_order": 1}), "canonical")
        # An explicit role or entry_kind never changes the role.
        self.assertEqual(command_role({"role": "read_only_query"}), "canonical")
        self.assertEqual(command_role({"entry_kind": "read_only_query", "menu_order": 5}), "canonical")

    def test_malformed_entries_are_refused(self) -> None:
        for bad in ("ask", None, []):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    command_role(bad)
                with self.assertRaises(ValueError):
                    entry_kind(bad)
                with self.assertRaises(ValueError):
                    is_menu_command(bad)

    def test_entry_kind_default_and_invalid_values(self) -> None:
        self.assertEqual(entry_kind({}), "route")
        self.assertEqual(entry_kind({"entry_kind": "route"}), "route")
        self.assertEqual(entry_kind({"entry_kind": "read_only_query"}), "read_only_query")
        for bad in ("mystery", "", None, 1, "ask"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                entry_kind({"entry_kind": bad})

    def test_menu_membership_excludes_read_only_query_and_aliases(self) -> None:
        self.assertTrue(is_menu_command({"menu_order": 1}))
        self.assertFalse(is_menu_command({"menu_order": 1, "entry_kind": "read_only_query"}))
        self.assertFalse(is_menu_command({"menu_order": 1, "alias_for": "think"}))
        registry = load_registry()
        menu = sorted((name for name, entry in registry.items()
                       if isinstance(entry.get("menu_order"), int) and is_menu_command(entry)),
                      key=lambda name: registry[name]["menu_order"])
        self.assertEqual(len(menu), 15)
        self.assertNotIn("ask", menu)
        self.assertEqual(entry_kind(registry["ask"]), "read_only_query")
        self.assertNotIn("alias_for", registry["ask"])
        self.assertNotIn("role", registry["ask"])
        self.assertIsInstance(registry["ask"]["menu_order"], int)
        self.assertEqual([name for name, entry in registry.items() if entry_kind(entry) != "route"], ["ask"])

    def test_inventory_roles_equal_the_frozen_installed_updater_formula(self) -> None:
        registry = load_registry()
        inventory = json.loads((ROOT / "spec" / "package-inventory.json").read_text(encoding="utf-8"))
        rows = inventory["daily_entry_contract"]["managed_entries"]
        actual = {row["command"]: row["role"] for row in rows}
        expected = frozen_installed_updater_roles(registry)
        self.assertEqual(len(actual), len(rows))
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, {name: command_role(entry) for name, entry in registry.items()
                                  if isinstance(entry.get("menu_order"), int)})
        self.assertEqual(len(rows), 16)
        self.assertTrue(set(actual.values()) <= COMMAND_ROLES)
        for row in rows:
            self.assertNotIn("canonical_command", row)

    def test_scripts_import_only_names_old_modules_already_export(self) -> None:
        found = 0
        for name in UPDATER_LOADED_SCRIPTS:
            tree = ast.parse((ROOT / "scripts" / name).read_text(encoding="utf-8"), filename=name)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name, OLD_MODULE_NAMES, f"{name}: use 'from ... import <old name>' only")
                if isinstance(node, ast.ImportFrom) and node.module in OLD_MODULE_NAMES:
                    for alias in node.names:
                        found += 1
                        self.assertIn(alias.name, OLD_MODULE_NAMES[node.module],
                                      f"{name}: {node.module}.{alias.name} does not exist in installed 1.9.22 to 1.9.25 workspaces")
        self.assertGreater(found, 0)

    def test_verify_package_import_surface_does_not_widen(self) -> None:
        tree = ast.parse((ROOT / "scripts" / "verify_package.py").read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.add(node.module)
        self.assertNotIn("command_rules", modules)
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    and node.module == "package_inventory" for alias in node.names}
        self.assertEqual(imported, {"load_inventory", "validate_provenance"})


if __name__ == "__main__":
    unittest.main()
