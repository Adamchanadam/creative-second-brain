"""Which vault a workspace uses is decided in one place, ``workspace_paths``.

The installer lets the user name the vault; it is stored in the workspace's
``brain.json``.  ``tools/brain.py`` also exports it as
``CREATIVE_SECOND_BRAIN_VAULT``, but the commands a work order prints run the
runtime tools directly, with no such variable.  So no runtime tool may read the
variable itself: each asks ``workspace_paths.configured_vault_path(root)`` (or
the relative form), which reads ``brain.json``.

The few helpers that build paths without a ``root`` in scope follow the process
environment; so every tool that can be run directly calls
``workspace_paths.bind_process_vault(root)`` once, right after it reads its
arguments, which checks ``brain.json`` against the environment and then exports
the declared vault to this process and its child processes.  A tool may stop on
a refused binding, never carry on past it.

One entry is different: the read-only lookup (``brain_lookup``) is started from
anywhere to read one named workspace, possibly by a program that works on
another workspace and exported that one's vault.  It calls
``workspace_paths.bind_lookup_process_vault(root)``, which replaces an inherited
value by the named workspace's own ``brain.json``.  No other tool may call it.

Run offline with the acceptance Python, no arguments needed:

    python -B tests/test_vault_resolution_contract.py [--tools <runtime/workspace/tools>]
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VARIABLE = "CREATIVE_SECOND_BRAIN_VAULT"
# The resolver's private constant; no other tool may import or name it under any spelling.
CONSTANT_NAMES = ("ENV_VAULT", "_ENV_VAULT")
# Runtime tools that run directly but never touch a vault or a workspace's vault setting.
# Each reason is checked below: such a tool must not mention a vault or the resolver at all.
NO_BIND_ALLOWED = {
    "brain_update": "updates the workspace's program files and documents; it never reads or writes vault content, and a damaged brain.json must not block the update that repairs it",
    "global_entry": "writes the user-level registry under the home folder; it has no vault",
    "infographic_visual_qc": "inspects one PNG file; it has no workspace or vault",
}
# The only tool that may replace an inherited vault instead of refusing it, and why.
LOOKUP_BIND = "bind_lookup_process_vault"
LOOKUP_BIND_ALLOWED = {
    "brain_lookup": "read-only lookup of a named workspace, started from anywhere; it never writes a vault",
}
ROOT_VARIABLE = "CREATIVE_SECOND_BRAIN_ROOT"
# The modules whose vault default used to come from the variable; each must still import when
# the workspace's brain.json is unusable (the default is resolved when used, never at import).
FORMER_READERS = (
    "ask_second_brain", "firecrawl_obsidian_pipeline", "ingest_plan_contract", "ingest_rail", "knowledge_direction",
    "knowledge_nebula", "knowledge_material_selector", "obsidian_idea_retrieval", "obsidian_semantic_maintenance",
    "obsidian_semantic_map_flow", "obsidian_vault_adapter", "outcome_page_generator", "outcome_page_quality_audit",
    "source_ingest_jobs",
)


def docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of string constants that are documentation, which may name the variable."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def variable_mentions(source: str, name: str = "<source>") -> list[int]:
    """Lines of code that name the variable: ``os.environ.get``, ``os.environ[...]``, ``os.getenv``, aliases, and so on."""
    tree = ast.parse(source, name)
    docs = docstring_nodes(tree)
    return sorted({node.lineno for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and isinstance(node.value, str) and VARIABLE in node.value and id(node) not in docs})


def constant_mentions(source: str, name: str = "<source>") -> list[int]:
    """Lines that use the resolver's private constant: as a name, an attribute, or an import (aliased or not)."""
    tree = ast.parse(source, name)
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in CONSTANT_NAMES:
            found.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr in CONSTANT_NAMES:
            found.add(node.lineno)
        elif isinstance(node, ast.alias) and (node.name in CONSTANT_NAMES or node.asname in CONSTANT_NAMES):
            found.add(getattr(node, "lineno", 0))
    return sorted(found)


def scan_runtime_tools(tools: Path) -> dict[str, list[int]]:
    offenders: dict[str, list[int]] = {}
    for path in sorted(tools.glob("*.py")):
        if path.name == "workspace_paths.py":
            continue
        source = path.read_text(encoding="utf-8-sig")
        lines = sorted(set(variable_mentions(source, path.name)) | set(constant_mentions(source, path.name)))
        if lines:
            offenders[path.name] = lines
    return offenders


def main_guard(tree: ast.Module) -> ast.If | None:
    """The module-level ``if __name__ == "__main__":`` block, whichever quotes or operand order it uses."""
    for node in tree.body:
        test = node.test if isinstance(node, ast.If) else None
        if isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq):
            sides = (test.left, test.comparators[0])
            names_module = any(isinstance(side, ast.Name) and side.id == "__name__" for side in sides)
            names_main = any(isinstance(side, ast.Constant) and side.value == "__main__" for side in sides)
            if names_module and names_main:
                return node
    return None


BIND_NAMES = ("bind_process_vault", LOOKUP_BIND)


def is_bind_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Name) and node.func.id in BIND_NAMES)
        or (isinstance(node.func, ast.Attribute) and node.func.attr in BIND_NAMES))


def lookup_bind_mentions(source: str, name: str = "<source>") -> list[int]:
    """Lines that use the lookup-only binding: as a name, an attribute, or an import (aliased or not)."""
    tree = ast.parse(source, name)
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == LOOKUP_BIND:
            found.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == LOOKUP_BIND:
            found.add(node.lineno)
        elif isinstance(node, ast.alias) and LOOKUP_BIND in (node.name, node.asname):
            found.add(getattr(node, "lineno", 0))
    return sorted(found)


# `try ... except*` (ast.TryStar, Python 3.11+) can swallow a refused binding exactly like `try ... except`.
TRY_NODES = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)


def handler_leaves(handler: ast.ExceptHandler) -> bool:
    """True when the handler ends by leaving the entry: ``raise``, ``return``, ``parser.error(...)`` or ``sys.exit(...)``."""
    last = handler.body[-1]
    if isinstance(last, (ast.Raise, ast.Return)):
        return True
    call = last.value if isinstance(last, ast.Expr) and isinstance(last.value, ast.Call) else None
    func = call.func if call is not None else None
    return (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
            and (func.value.id, func.attr) in {("parser", "error"), ("sys", "exit")})


def swallowed_bind_lines(body: list[ast.stmt]) -> list[int]:
    """Lines of ``except`` handlers that could carry on after a refused binding.

    Every handler of every ``try`` around an unconditional bind call must leave the entry.  ``except ...: pass``,
    a handler that only prints or logs, and one that falls through to the code after the ``try`` are all refused:
    the tool would go on with a vault that was never checked.
    """
    lines: list[int] = []
    for statement in body:
        if isinstance(statement, TRY_NODES) and unconditional_bind_lines(statement.body):
            lines.extend(handler.lineno for handler in statement.handlers if not handler_leaves(handler))
            lines.extend(swallowed_bind_lines(statement.body))
    return sorted(lines)


def unconditional_bind_lines(body: list[ast.stmt]) -> list[int]:
    """Lines of bind calls that always run: plain statements of the body, or of a ``try`` body, never inside a branch or loop."""
    lines: list[int] = []
    for statement in body:
        if isinstance(statement, TRY_NODES):
            lines.extend(unconditional_bind_lines(statement.body))
        elif isinstance(statement, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.FunctionDef,
                                    ast.AsyncFunctionDef, ast.ClassDef, ast.Match)):
            continue
        else:
            lines.extend(node.lineno for node in ast.walk(statement) if is_bind_call(node))
    return sorted(lines)


def entry_binding_issues(source: str, name: str = "<source>") -> tuple[bool, list[str]]:
    """(has a __main__ guard, problems).  The entry point is the module function the guard calls, whatever its name."""
    tree = ast.parse(source, name)
    guard = main_guard(tree)
    if guard is None:
        return False, []
    functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    called: list[str] = []
    for node in ast.walk(guard):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in functions and node.func.id not in called:
            called.append(node.func.id)
    entries = [(entry_name, functions[entry_name].body, functions[entry_name]) for entry_name in called] or [("<__main__ block>", guard.body, guard)]
    for entry_name, body, node in entries:
        binds = unconditional_bind_lines(body)
        if not binds:
            continue
        early = sorted(inner.lineno for inner in ast.walk(node)
                       if isinstance(inner, ast.Attribute) and inner.attr == "vault" and inner.lineno < binds[0])
        if early:
            return True, [f"{entry_name} reads .vault (line {early[0]}) before bind_process_vault (line {binds[0]})"]
        swallowed = swallowed_bind_lines(body)
        if swallowed:
            return True, [f"{entry_name} can carry on after a refused bind_process_vault: the except at line {swallowed[0]} "
                          "does not end in raise, return, parser.error or sys.exit"]
        return True, []
    return True, [f"{' / '.join(entry_name for entry_name, _, _ in entries)} never calls bind_process_vault unconditionally"]


def check_entry_binding(tools: Path) -> dict[str, object]:
    guarded: list[str] = []
    failures: dict[str, list[str]] = {}
    for path in sorted(tools.glob("*.py")):
        source = path.read_text(encoding="utf-8-sig")
        has_guard, problems = entry_binding_issues(source, path.name)
        if not has_guard:
            continue
        guarded.append(path.stem)
        if path.stem in NO_BIND_ALLOWED:
            # The reason must stay true: no vault, no resolver.
            assert "vault" not in source.lower() and "workspace_paths" not in source, (path.name, NO_BIND_ALLOWED[path.stem])
            continue
        if problems:
            failures[path.name] = problems
    assert not failures, f"every directly runnable tool must call workspace_paths.bind_process_vault(root) right after its arguments: {failures}"
    # Replacing an inherited vault instead of refusing it is for the read-only lookup alone.
    misuse = {path.name: lines for path in sorted(tools.glob("*.py")) if path.name != "workspace_paths.py" and path.stem not in LOOKUP_BIND_ALLOWED
              for lines in [lookup_bind_mentions(path.read_text(encoding="utf-8-sig"), path.name)] if lines}
    assert not misuse, f"only {sorted(LOOKUP_BIND_ALLOWED)} may call workspace_paths.{LOOKUP_BIND}; a tool that writes must refuse a contradicting vault: {misuse}"
    for stem in LOOKUP_BIND_ALLOWED:
        assert lookup_bind_mentions((tools / f"{stem}.py").read_text(encoding="utf-8-sig")), f"allow-list names a tool that does not use {LOOKUP_BIND}: {stem}"
    stale = sorted(set(NO_BIND_ALLOWED) - set(guarded))
    assert not stale, f"allow-list names tools that have no __main__ guard: {stale}"
    # The guard must not be vacuous.
    guard = 'if __name__ == "__main__":\n    raise SystemExit(main())\n'
    bind = "    bind_process_vault(args.root)\n"
    good = "from workspace_paths import bind_process_vault\ndef main():\n    args = parse()\n" + bind + "    return args.vault\n" + guard
    assert entry_binding_issues(good) == (True, [])
    assert entry_binding_issues(good.replace('"__main__"', "'__main__'")) == (True, [])
    assert entry_binding_issues(good.replace("main()", "run()").replace("def main", "def run")) == (True, [])
    assert entry_binding_issues(good.replace(bind, ""))[1], "missing bind must fail"
    assert entry_binding_issues(good.replace(bind, "    if args.root:\n    " + bind))[1], "conditional bind must fail"
    assert entry_binding_issues(good.replace(bind, "    x = args.vault\n" + bind).replace("return args.vault", "return x"))[1], "bind after .vault must fail"
    assert entry_binding_issues(good.replace(bind, "    try:\n    " + bind + "    except ValueError:\n        return 2\n")) == (True, [])
    assert entry_binding_issues("def main():\n    return 0\n") == (False, [])
    # A refused binding must stop the tool: a handler that swallows it, or lets the code after it run, fails.
    tried = lambda handler: good.replace(bind, "    try:\n    " + bind + handler)
    for handler in ("    except ValueError:\n        return 2\n", "    except ValueError as exc:\n        raise SystemExit(str(exc)) from exc\n",
                    "    except ValueError as exc:\n        parser.error(str(exc))\n", "    except ValueError as exc:\n        print(exc)\n        sys.exit(2)\n",
                    "    except KeyError:\n        return 3\n    except ValueError:\n        raise\n"):
        assert entry_binding_issues(tried(handler)) == (True, []), handler
    for handler in ("    except ValueError:\n        pass\n", "    except Exception:\n        pass\n", "    except:\n        pass\n",
                    "    except ValueError as exc:\n        print(exc)\n", "    except ValueError as exc:\n        log.error(str(exc))\n",
                    "    except ValueError as exc:\n        result = {'status': 'error'}\n", "    except ValueError:\n        ...\n",
                    "    except KeyError:\n        return 3\n    except ValueError:\n        pass\n",
                    "    except ValueError:\n        if strict:\n            raise\n", "    except* ValueError:\n        pass\n"):
        problems = entry_binding_issues(tried(handler))[1]
        assert problems and "carry on" in problems[0], ("a swallowed bind error must fail", handler, problems)
    nested = good.replace(bind, "    try:\n        try:\n        " + bind + "        except ValueError as exc:\n            raise Stop(str(exc)) from exc\n    except Stop:\n        OUTER\n")
    assert entry_binding_issues(nested.replace("OUTER", "return 1")) == (True, [])
    assert "carry on" in entry_binding_issues(nested.replace("OUTER", "pass"))[1][0], "an outer handler that swallows the stop must fail"
    assert entry_binding_issues(good.replace(bind, "    with contextlib.suppress(ValueError):\n    " + bind))[1], "a suppressed bind must fail"
    assert entry_binding_issues(good.replace("bind_process_vault", LOOKUP_BIND)) == (True, [])
    for snippet in (f"from workspace_paths import {LOOKUP_BIND}\n", f"from workspace_paths import {LOOKUP_BIND} as bind\n",
                    f"import workspace_paths as w\nw.{LOOKUP_BIND}(root)\n", f"x = {LOOKUP_BIND}\n"):
        assert lookup_bind_mentions(snippet), snippet
    assert not lookup_bind_mentions(f'''"""Mentions {LOOKUP_BIND} in prose."""\nfrom workspace_paths import bind_process_vault\n''')
    for snippet in ("from workspace_paths import _ENV_VAULT\n", "from workspace_paths import _ENV_VAULT as name\n",
                    "import workspace_paths as w\nx = w._ENV_VAULT\n", "x = ENV_VAULT\n", "import workspace_paths as w\nx = w.ENV_VAULT\n"):
        assert constant_mentions(snippet), snippet
    assert not constant_mentions('"""Mentions ENV_VAULT in prose."""\n# _ENV_VAULT in a comment\n')
    return {"tools_with_main_guard": len(guarded), "bound": len(guarded) - len(NO_BIND_ALLOWED), "allowed_without_bind": sorted(NO_BIND_ALLOWED)}


@contextlib.contextmanager
def environment(**values: str | None):
    saved = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def expect_error(paths, action, *needles: str) -> str:
    try:
        action()
    except paths.WorkspacePathError as exc:
        message = str(exc)
        for needle in needles:
            assert needle in message, (needle, message)
        return message
    raise AssertionError("expected WorkspacePathError")


def check_static_guard(tools: Path) -> dict[str, object]:
    offenders = scan_runtime_tools(tools)
    assert not offenders, f"runtime tools must not read {VARIABLE}; use workspace_paths.configured_vault_path: {offenders}"
    # The guard must not be vacuous: every way of reading the variable is caught.
    for snippet in (
        f'import os\nx = os.environ.get("{VARIABLE}", "v")\n',
        f'import os\nx = os.environ["{VARIABLE}"]\n',
        f'import os\nx = os.getenv("{VARIABLE}")\n',
        f'from os import environ\nx = environ.get("{VARIABLE}")\n',
        f'import os\nNAME = "{VARIABLE}"\nx = os.environ.get(NAME)\n',
    ):
        assert variable_mentions(snippet), snippet
    assert not variable_mentions(f'"""Doc that names {VARIABLE}."""\ndef f():\n    """Also {VARIABLE}."""\n')
    return {"tools_scanned": len(list(tools.glob("*.py"))) - 1}


def load_paths(tools: Path):
    sys.path.insert(0, str(tools))
    try:
        return importlib.import_module("workspace_paths")
    finally:
        sys.path.remove(str(tools))


def child_value() -> str:
    """The variable as a child process of this one sees it (no explicit env: it inherits ours)."""
    code = "import os, sys; sys.stdout.write(os.environ.get(sys.argv[1], '<unset>'))"
    result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code, VARIABLE], text=True, encoding="utf-8",
                            capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def check_bind_process_vault(tools: Path, scratch: Path) -> dict[str, object]:
    paths = load_paths(tools)
    declared = "私人/創作庫"
    custom, other, bare, broken = (scratch / name for name in ("bind-custom", "bind-other", "bind-bare", "bind-broken"))
    for folder, vault in ((custom, declared), (other, "Other Notes"), (broken, None)):
        folder.mkdir()
        (folder / "brain.json").write_text("{not json" if vault is None else json.dumps({"vault": vault}, ensure_ascii=False), encoding="utf-8")
    bare.mkdir()
    with environment(**{VARIABLE: None}):
        assert child_value() == "<unset>"
        # An installed root: the declared vault is exported, and a child process sees it.
        assert paths.bind_process_vault(custom) == Path(declared)
        assert os.environ[VARIABLE] == declared and child_value() == declared
        # The helpers that have no root in scope now agree with brain.json.
        assert paths.vault_relative_posix() == declared and paths.vault_relative_path() == Path(declared)
        # Binding again, and an environment written differently but naming the same vault, are fine.
        assert paths.bind_process_vault(custom) == Path(declared)
        os.environ[VARIABLE] = declared + "/"
        assert paths.bind_process_vault(custom) == Path(declared) and os.environ[VARIABLE] == declared
        # Known side effect: one process cannot serve two installed roots with different vaults.
        expect_error(paths, lambda: paths.bind_process_vault(other), VARIABLE, "Other Notes")
        assert child_value() == declared, "a refused bind must leave the environment unchanged"
    # A root without brain.json never touches the environment.
    for before in (None, "Env Notes"):
        with environment(**{VARIABLE: before}):
            assert paths.bind_process_vault(bare) == Path(before or "knowledge-base-vault")
            assert os.environ.get(VARIABLE) == before and child_value() == (before or "<unset>")
    # A conflicting environment is refused before anything is exported; so is an unusable brain.json.
    with environment(**{VARIABLE: "Elsewhere"}):
        expect_error(paths, lambda: paths.bind_process_vault(custom), VARIABLE, declared)
        assert os.environ[VARIABLE] == "Elsewhere" and child_value() == "Elsewhere"
    with environment(**{VARIABLE: None}):
        expect_error(paths, lambda: paths.bind_process_vault(broken))
        assert VARIABLE not in os.environ and child_value() == "<unset>"
    # The read-only lookup binding: an inherited vault and root (another workspace's) are replaced by the named root's own.
    with environment(**{VARIABLE: "Other Notes", ROOT_VARIABLE: str(other)}):
        assert paths.bind_lookup_process_vault(custom) == Path(declared)
        assert os.environ[VARIABLE] == declared and child_value() == declared
        assert Path(os.environ[ROOT_VARIABLE]) == custom.resolve()
        # ... and every other tool's rule is untouched: this process now refuses a second installed root.
        expect_error(paths, lambda: paths.bind_process_vault(other), VARIABLE, "Other Notes")
    for inherited in ("../outside", "/absolute", "Other Notes"):
        with environment(**{VARIABLE: inherited, ROOT_VARIABLE: None}):
            assert paths.bind_lookup_process_vault(custom) == Path(declared) and os.environ[VARIABLE] == declared
    # An unusable brain.json is refused before anything is replaced; a root without brain.json is no lookup target.
    with environment(**{VARIABLE: "Elsewhere", ROOT_VARIABLE: str(other)}):
        expect_error(paths, lambda: paths.bind_lookup_process_vault(broken))
        assert os.environ[VARIABLE] == "Elsewhere" and os.environ[ROOT_VARIABLE] == str(other)
        assert paths.bind_lookup_process_vault(bare) == Path("Elsewhere")
        assert os.environ[VARIABLE] == "Elsewhere" and os.environ[ROOT_VARIABLE] == str(other)
    return {"cases": ["installed root exported to child process", "refused bind changes nothing", "no brain.json leaves environment alone",
                      "conflicting environment refused", "second installed root with another vault refused",
                      "read-only lookup replaces an inherited vault and root", "lookup binding leaves the writers' rule intact"]}


def check_rule(tools: Path, scratch: Path) -> dict[str, object]:
    paths = load_paths(tools)
    for name in ("configured_vault_relative_path", "configured_vault_relative_posix", "configured_vault_path"):
        # The workspace is always named by the caller; there is no "the workspace these tools live in".
        assert inspect.signature(getattr(paths, name)).parameters["root"].default is inspect.Parameter.empty, name
        try:
            getattr(paths, name)()
        except TypeError:
            pass
        else:
            raise AssertionError(f"{name}() without a root must not exist")
    assert not hasattr(paths, "ENV_VAULT") and not hasattr(paths, "tool_workspace_root")
    installed = scratch / "installed"
    installed.mkdir()
    (installed / "brain.json").write_text(json.dumps({"vault": "My Notes"}), encoding="utf-8")
    bare = scratch / "bare"
    bare.mkdir()

    # 1. brain.json, no variable: the declared vault.
    with environment(**{VARIABLE: None}):
        assert paths.configured_vault_path(installed) == (installed / "My Notes").resolve()
        assert paths.configured_vault_relative_path(installed) == Path("My Notes")
        assert paths.configured_vault_relative_posix(installed) == "My Notes"
        assert paths.vault_path(installed) == (installed / "My Notes").resolve()
        assert paths.managed_vault_path(installed) == (installed / "My Notes").resolve()
    # 2. brain.json and a variable that names the same vault: accepted.
    for same in ("My Notes", "My Notes/", "./My Notes", "My Notes\\"):
        with environment(**{VARIABLE: same}):
            assert paths.configured_vault_path(installed) == (installed / "My Notes").resolve(), same
    # 3. brain.json and a variable that names another vault: refused, naming both values; never a silent pick.
    for other in ("knowledge-base-vault", "Other Notes", "My Notes/sub", "../outside", "/absolute"):
        with environment(**{VARIABLE: other}):
            expect_error(paths, lambda: paths.configured_vault_path(installed), VARIABLE, "My Notes")
            expect_error(paths, lambda: paths.configured_vault_relative_path(installed), VARIABLE)
            expect_error(paths, lambda: paths.vault_path(installed), VARIABLE)
    # 4. no brain.json: exactly the old behaviour, the variable else the default name.
    with environment(**{VARIABLE: None}):
        assert paths.configured_vault_path(bare) == (bare / "knowledge-base-vault").resolve()
        assert paths.configured_vault_relative_path(bare) == Path("knowledge-base-vault")
        assert paths.vault_path(bare) == (bare / "knowledge-base-vault").resolve()
    with environment(**{VARIABLE: "Env Notes"}):
        assert paths.configured_vault_path(bare) == (bare / "Env Notes").resolve()
        assert paths.vault_path(bare) == (bare / "Env Notes").resolve()
        assert paths.configured_vault_relative_posix(bare) == "Env Notes"
    with environment(**{VARIABLE: "/absolute"}):
        expect_error(paths, lambda: paths.configured_vault_path(bare), "named relative path")
    # 5. an unreadable or malformed brain.json is an error, never a fallback to the default.
    broken = scratch / "broken"
    broken.mkdir()
    for content in ("{not json", json.dumps({"no_vault": True}), json.dumps({"vault": 7}), json.dumps(["list"]),
                    json.dumps({"vault": "../escape"}), json.dumps({"vault": ""})):
        (broken / "brain.json").write_text(content, encoding="utf-8")
        for variable in (None, "knowledge-base-vault"):
            with environment(**{VARIABLE: variable}):
                expect_error(paths, lambda: paths.configured_vault_path(broken))
                expect_error(paths, lambda: paths.configured_vault_relative_path(broken))
    (broken / "brain.json").unlink()
    (broken / "brain.json").mkdir()  # present but not readable as a file
    with environment(**{VARIABLE: None}):
        expect_error(paths, lambda: paths.configured_vault_path(broken))
    # 6. an explicit value is still just validated; the variable never overrides it.
    with environment(**{VARIABLE: "Other Notes"}):
        assert paths.vault_path(installed, "My Notes") == (installed / "My Notes").resolve()
        assert paths.vault_relative_path("Explicit/Name") == Path("Explicit/Name")
    # 6b. without a root nothing knows about brain.json: the environment, else the default name.
    with environment(**{VARIABLE: None}):
        assert paths.vault_relative_path() == Path("knowledge-base-vault") and paths.vault_relative_posix() == "knowledge-base-vault"
    with environment(**{VARIABLE: "Env Notes"}):
        assert paths.vault_relative_path() == Path("Env Notes") and paths.vault_relative_posix() == "Env Notes"
    with environment(**{VARIABLE: "/absolute"}):
        expect_error(paths, lambda: paths.vault_relative_path(), "named relative path")
    # 7. the explicit-vault binding of a writing tool still compares with brain.json.
    with environment(**{VARIABLE: None}):
        assert paths.bound_vault_path(installed, installed / "My Notes") == (installed / "My Notes").resolve()
        expect_error(paths, lambda: paths.bound_vault_path(installed, installed / "knowledge-base-vault"), "does not match")
    return {"cases": ["brain.json only", "brain.json and same variable", "brain.json and different variable refused",
                      "no brain.json (variable, default)", "malformed brain.json refused", "explicit value", "bound vault"]}


def check_import_safety(tools: Path, scratch: Path) -> dict[str, object]:
    """A tool's vault default is resolved when used: importing must not fail on an unusable brain.json or variable."""
    workspace = scratch / "import-safety"
    shutil.copytree(tools.parent, workspace, ignore=shutil.ignore_patterns("__pycache__", "assets"))
    shutil_tools = workspace / "tools"
    (workspace / "brain.json").write_text("{not json", encoding="utf-8")
    env = dict(os.environ)
    env[VARIABLE] = "A Different Vault"
    names = [name for name in FORMER_READERS if (shutil_tools / f"{name}.py").is_file()]
    code = "import importlib,sys\nfor n in sys.argv[1:]:\n    importlib.import_module(n)\nprint('imported', len(sys.argv) - 1)\n"
    result = subprocess.run([sys.executable, "-B", "-c", code, *names], cwd=shutil_tools, env={**env, "PYTHONPATH": str(shutil_tools)},
                            text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return {"modules_imported_with_unusable_brain_json": len(names)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, default=ROOT / "runtime" / "workspace" / "tools")
    args = parser.parse_args()
    tools = args.tools.resolve()
    assert (tools / "workspace_paths.py").is_file(), tools
    with tempfile.TemporaryDirectory(prefix="vault-rule-", dir=os.environ.get("BRAIN_TEST_TMPDIR") or None) as scratch_name:
        scratch = Path(scratch_name)
        report = {"status": "passed", "static_guard": check_static_guard(tools), "entry_binding": check_entry_binding(tools),
                  "rule": check_rule(tools, scratch), "bind_process_vault": check_bind_process_vault(tools, scratch),
                  "import_safety": check_import_safety(tools, scratch)}
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
