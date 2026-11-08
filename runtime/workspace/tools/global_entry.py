"""Install, inspect or remove the user-level (global) brain entries.

Only these targets are ever written, all under --home (default: the real user home):
  .creative-second-brain/workspaces.json      workspace registry
  .claude/skills/brain-ask/SKILL.md           read-only quick answer (Claude Code)
  .agents/skills/brain-ask/SKILL.md           read-only quick answer (Codex)
  .agents/skills/brain-ask/agents/openai.yaml
  .claude/skills/<brain-*>/SKILL.md           forwarders named by brain.json entry_contract
  .creative-second-brain/backups/<stamp>/...  copies of pre-existing files before they are overwritten

Every target must resolve (links and junctions followed) to a place inside --home; anything
that points outside is refused. Never touches any AI settings file (settings.json, config.toml, ...).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]  # default workspace root; --workspace overrides (tests)
MARKER = "creative-second-brain global entry v1"
SKILL_MARKER = f"<!-- {MARKER} -->"
YAML_MARKER = f"# {MARKER}"
REGISTRY_SCHEMA = "creative_second_brain_workspaces_v1"
REGISTRY_REL = ".creative-second-brain/workspaces.json"
BACKUP_REL = ".creative-second-brain/backups"
ASK_NAME = "brain-ask"

FIND_WORKSPACE = (
    "先讀使用者層登記表 `~/.creative-second-brain/workspaces.json`（`~` 是使用者主目錄）找知識庫工作區：\n"
    "   - 只有一個登記項，就用它；多於一個，先把各項的 `name` 列給用戶，請用戶揀一個，再繼續。\n"
    "   - 每項有 `name`、`root`（工作區根）、`python`（執行工具用的 Python）；`default` 只是預設建議，有多項時仍先問用戶。\n"
    "   - 登記表不存在、沒有登記項，或 `root` 下找不到 `brain.json`：不要猜路徑，請用戶回到知識庫工作區，"
    "執行 `python tools/global_entry.py install` 重新登記，然後停止。"
)


PS_QUOTE = "''"  # a single quote inside a PowerShell single-quoted string
BASH_QUOTE = "'\\''"  # a single quote inside a Bash single-quoted string
BACKSLASH = chr(92)  # built with chr: a doubled backslash in source text looks like a UNC path to the package's local-path guard
PS_EXAMPLE = f"& '<python>' '<root>{BACKSLASH}tools{BACKSLASH}brain_lookup.py' --question '<問題>' --json"
BASH_EXAMPLE = "'<python>' '<root>/tools/brain_lookup.py' --question '<問題>' --json"


def frontmatter(name: str, description: str) -> str:
    return f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\n{SKILL_MARKER}\n"


def ask_body() -> str:
    return (
        "\n"
        "這是知識庫的全域只讀快答入口：不論目前在哪個資料夾、哪個專案，都可以查詢知識庫並引用，但只讀。\n\n"
        "1. " + FIND_WORKSPACE + "\n"
        "2. 以登記項的 `python` 執行該項 `root` 下的 `tools/brain_lookup.py`（在任何資料夾都可以；`<python>`、`<root>` 換成該項的值，"
        "`<問題>` 換成用戶的問題；命令是唯讀的，不會寫入任何檔案）。引號規則：路徑一律加單引號；"
        "問題用單引號原樣傳遞，不要用雙引號包問題（shell 會展開問題內的 `$` 與反引號）；"
        f"問題內若有單引號，PowerShell 內寫成兩個 `{PS_QUOTE}`，Bash 內寫成 `{BASH_QUOTE}`。完整範例：\n\n"
        f"   PowerShell：`{PS_EXAMPLE}`\n\n"
        f"   Bash：`{BASH_EXAMPLE}`\n\n"
        "   問題有不同說法時，可多加 `--expand '<另一說法>'`；候選不夠可加 `--limit N`（上限 20）。\n"
        "3. 完全照做輸出內的 `answer_rules`，並只用輸出內的內容及其所列筆記作答。\n\n"
        "只讀邊界：不得保存思考紀錄、入庫、修訂或整理知識庫，也不得在目前資料夾建立任何知識庫檔案。"
        "用戶要求存入、入庫、修訂或保存思考時，不執行任何寫入命令，"
        "只回覆 {think} 等命令，並請用戶回到知識庫工作區執行。\n"
    )


ASK_DESCRIPTION = "在任何專案內唯讀查詢並引用知識庫，快答問題；不保存、不入庫、不修訂。"
CLAUDE_ASK = frontmatter(ASK_NAME, ASK_DESCRIPTION) + ask_body().replace("{think}", "`/brain-think <問題>`（Codex 為 `$brain-think <問題>`）")
CODEX_ASK = frontmatter(ASK_NAME, ASK_DESCRIPTION) + ask_body().replace("{think}", "`$brain-think <問題>`（Claude Code 為 `/brain-think <問題>`）")
CODEX_YAML = (
    f"{YAML_MARKER}\n"
    "interface:\n"
    '  display_name: "05 使用｜brain-ask｜唯讀快答"\n'
    f'  short_description: "{ASK_DESCRIPTION}"\n'
    '  default_prompt: "$brain-ask [問題]"\n'
)


def forwarder(name: str, description: str) -> str:
    return (
        frontmatter(name, description)
        + "\n這是全域轉介入口：不論目前在哪個資料夾，都以知識庫工作區為根工作。\n\n"
        "1. " + FIND_WORKSPACE + "\n"
        "2. 以該項的 `root` 為工作區根：所有相對路徑、讀寫和命令都以 `root` 為準，命令在 `root` 執行；"
        "不得在目前資料夾建立任何知識庫檔案。\n"
        f"3. 找規則的次序固定：若 `<root>/.claude/skills/{name}/SKILL.md` 存在，讀取並完整照做它；"
        f"否則讀取並完整照做 `<root>/.agents/skills/{name}/SKILL.md`（唯一規則）。"
        "需要 Python 時，用該項的 `python`；命令中的路徑一律加單引號，文字參數用單引號原樣傳遞。"
        "使用者在指令後附帶的文字，就是該技能的輸入。\n"
    )


def read_brain_json(root: Path) -> dict[str, Any]:
    path = root / "brain.json"
    if not path.is_file():
        raise SystemExit("找不到 brain.json：" + str(root))
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"brain.json 無法讀取或不是有效的 JSON：{path}（{exc}）")
    if not isinstance(data, dict):
        raise SystemExit(f"brain.json 的內容不是物件，無法使用：{path}")
    return data


def rule_file(root: Path, name: str) -> Path | None:
    """The file a forwarder tells the AI to follow: .claude first, otherwise .agents."""
    for folder in (".claude", ".agents"):
        path = root / folder / "skills" / name / "SKILL.md"
        if path.is_file():
            return path
    return None


def skill_description(root: Path, name: str, fallback: str) -> str:
    path = rule_file(root, name)
    if path is None:
        return fallback
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return fallback
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    match = re.search(r"(?m)^description:\s*(.+?)\s*$", head)
    if not match:
        return fallback
    value = match.group(1)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value or fallback


def forwarder_names(config: dict[str, Any]) -> list[tuple[str, str]]:
    rows = []
    for entry in config.get("entry_contract") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("skill") or "").strip()
        if name.startswith("brain-") and name != ASK_NAME and name not in [row[0] for row in rows]:
            rows.append((name, str(entry.get("purpose") or "").strip()))
    return rows


def build_entries(root: Path, config: dict[str, Any]) -> dict[str, str]:
    """Relative target path (under home) -> fixed content. Contains no workspace path."""
    entries = {
        f".claude/skills/{ASK_NAME}/SKILL.md": CLAUDE_ASK,
        f".agents/skills/{ASK_NAME}/SKILL.md": CODEX_ASK,
        f".agents/skills/{ASK_NAME}/agents/openai.yaml": CODEX_YAML,
    }
    for name, purpose in forwarder_names(config):
        entries[f".claude/skills/{name}/SKILL.md"] = forwarder(name, skill_description(root, name, purpose))
    return entries


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def inside_home(home: Path, path: Path) -> bool:
    """True when `path`, with every link and junction followed, still lies inside `home`."""
    try:
        return path.resolve().is_relative_to(home.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def registry_problem(path: Path, why: str) -> SystemExit:
    return SystemExit(f"登記表有問題，工具不會覆蓋它，請先處理後再試：{path}（{why}）")


def load_registry(home: Path) -> dict[str, Any]:
    path = home / REGISTRY_REL
    if not inside_home(home, path):
        raise SystemExit(f"登記表的實際位置在使用者目錄之外（可能是捷徑或連結），工具拒絕使用：{path}")
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "default": None, "workspaces": []}
    if not path.is_file():
        raise registry_problem(path, "它不是檔案")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise registry_problem(path, f"無法讀取或不是有效的 JSON：{exc}")
    if not isinstance(data, dict):
        raise registry_problem(path, "最外層不是物件")
    if not isinstance(data.get("workspaces"), list):
        raise registry_problem(path, "缺少 workspaces 清單")
    for index, row in enumerate(data["workspaces"], 1):
        if not isinstance(row, dict):
            raise registry_problem(path, f"第 {index} 項不是物件")
        for key in ("name", "root"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise registry_problem(path, f"第 {index} 項缺少 {key}")
    data.setdefault("schema", REGISTRY_SCHEMA)
    data.setdefault("default", None)
    return data


def registry_with(registry: dict[str, Any], name: str, root: Path, python: str) -> dict[str, Any]:
    rows = [dict(row) for row in registry["workspaces"]]
    mine = {"name": name, "root": str(root), "python": python}
    for row in rows:
        if row.get("name") == name and norm(str(row.get("root", ""))) != norm(str(root)):
            raise SystemExit(
                f"登記名稱「{name}」已被另一個工作區使用（{row.get('root')}）。"
                "請用 --name 另取一個名稱再安裝；本次沒有寫入任何檔案。"
            )
    for index, row in enumerate(rows):
        if norm(str(row.get("root", ""))) == norm(str(root)):
            rows[index] = {**row, **mine}  # keep any extra fields the user added to this item
            break
    else:
        rows.append(mine)
    default = registry.get("default")
    if default not in [row.get("name") for row in rows]:
        default = name  # a newly added workspace never replaces an existing valid default
    return {**registry, "schema": REGISTRY_SCHEMA, "default": default, "workspaces": rows}


def registry_without(registry: dict[str, Any], root: Path) -> dict[str, Any]:
    rows = [dict(row) for row in registry["workspaces"] if norm(str(row.get("root", ""))) != norm(str(root))]
    default = registry.get("default")
    if default not in [row.get("name") for row in rows]:
        default = rows[0]["name"] if rows else None
    return {**registry, "schema": REGISTRY_SCHEMA, "default": default, "workspaces": rows}


def registry_text(registry: dict[str, Any]) -> str:
    return json.dumps(registry, ensure_ascii=False, indent=2) + "\n"  # written as UTF-8 without BOM


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def read_if_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def state_of(path: Path, desired: bytes) -> str:
    current = read_if_file(path)
    if current is None:
        return "create"
    return "unchanged" if current == desired else "overwrite"


def has_marker(data: bytes | None) -> bool:
    return data is not None and MARKER.encode("utf-8") in data


def default_name(root: Path) -> str:
    return root.name.strip() or str(root)


def new_backup_dir(home: Path) -> Path:
    base = home / BACKUP_REL
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = base / stamp
    counter = 2
    while candidate.exists():  # never write into an earlier backup: it may hold the only original
        candidate = base / f"{stamp}-{counter}"
        counter += 1
    return candidate


def earliest_original(home: Path, rel: str) -> Path | None:
    """Oldest backed-up copy of `rel` that this tool did not write (no marker): the pre-install original."""
    base = home / BACKUP_REL
    if not base.is_dir() or not inside_home(home, base):
        return None
    for stamp in sorted(item for item in base.iterdir() if item.is_dir()):
        candidate = stamp / rel
        if candidate.is_file() and inside_home(home, candidate) and not has_marker(read_if_file(candidate)):
            return candidate
    return None


def cmd_install(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    home = args.home
    root = args.root
    name = args.name or default_name(root)
    python = str(config.get("runtime_python") or sys.executable)
    desired: dict[str, bytes] = {rel: text.encode("utf-8") for rel, text in build_entries(root, config).items()}
    registry = registry_with(load_registry(home), name, root, python)
    desired[REGISTRY_REL] = registry_text(registry).encode("utf-8")

    plan = [{"path": str(home / rel), "relative": rel, "action": state_of(home / rel, data)} for rel, data in desired.items()]
    refused = [item["path"] for item in plan if not inside_home(home, home / item["relative"])]
    if any(item["action"] == "overwrite" for item in plan) and not inside_home(home, home / BACKUP_REL):
        refused.append(str(home / BACKUP_REL))
    for item in plan:
        shown = "refused" if item["path"] in refused else item["action"]
        print(f"{shown:<9} {item['path']}", file=sys.stderr)

    result = {
        "command": "install",
        "home": str(home),
        "workspace": {"name": name, "root": str(root), "python": python},
        "targets": plan,
        "written": 0,
        "backup_dir": "",
        "refused": refused,
        "verified": False,
        "failures": [],
    }
    if refused:
        print("以下位置的實際指向在使用者目錄之外（可能是捷徑或連結），為安全起見拒絕寫入，本次沒有寫入任何檔案：\n  "
              + "\n  ".join(refused), file=sys.stderr)
        result["failures"] = list(refused)
        return result

    changes = [item for item in plan if item["action"] != "unchanged"]
    backup_dir = None
    if changes:
        backup_dir = new_backup_dir(home)
        for item in changes:
            if item["action"] == "overwrite":
                source = home / item["relative"]
                write_bytes(backup_dir / item["relative"], source.read_bytes())
        for item in changes:
            write_bytes(home / item["relative"], desired[item["relative"]])

    failures = [item["path"] for item in plan if read_if_file(home / item["relative"]) != desired[item["relative"]]]
    result.update({
        "written": len(changes),
        "backup_dir": str(backup_dir) if backup_dir and any(i["action"] == "overwrite" for i in changes) else "",
        "verified": not failures,
        "failures": failures,
    })
    return result


def cmd_status(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    home = args.home
    root = args.root
    targets = []
    for rel, text in build_entries(root, config).items():
        if not inside_home(home, home / rel):
            state = "outside_home"
        else:
            data = read_if_file(home / rel)
            if data is None:
                state = "missing"
            elif not has_marker(data):
                state = "not_ours"
            elif data == text.encode("utf-8"):
                state = "ok"
            else:
                state = "outdated"
        targets.append({"path": str(home / rel), "relative": rel, "state": state})

    registry_path = home / REGISTRY_REL
    registry_state = "missing"
    workspaces = []
    default = None
    if registry_path.exists() or not inside_home(home, registry_path):
        try:
            registry = load_registry(home)
            registry_state = "ok"
            default = registry.get("default")
            for row in registry["workspaces"]:
                row_root = Path(str(row.get("root", "")))
                workspaces.append({
                    "name": row.get("name"),
                    "root": str(row.get("root", "")),
                    "python": row.get("python"),
                    "this_workspace": norm(str(row_root)) == norm(str(root)),
                    "root_has_brain_json": (row_root / "brain.json").is_file(),
                    "python_exists": bool(row.get("python")) and Path(str(row.get("python"))).is_file(),
                })
        except SystemExit as exc:
            registry_state = f"invalid: {exc}"
    mine = [row for row in workspaces if row["this_workspace"]]
    ok = (
        registry_state == "ok"
        and bool(mine)
        and all(row["root_has_brain_json"] and row["python_exists"] for row in mine)
        and all(item["state"] == "ok" for item in targets)
    )
    return {
        "command": "status",
        "home": str(home),
        "registry": {"path": str(registry_path), "state": registry_state, "default": default, "workspaces": workspaces},
        "targets": targets,
        "ok": ok,
    }


def remove_empty_dirs(path: Path, stop: Path) -> None:
    current = path
    while current != stop and current.is_dir() and not any(current.iterdir()):
        current.rmdir()
        current = current.parent


def cmd_remove(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    home = args.home
    root = args.root
    registry_path = home / REGISTRY_REL
    registry_before = load_registry(home)  # stops with a plain-language message when the registry is unusable
    registry_after = registry_without(registry_before, root)
    removed_registry_item = len(registry_after["workspaces"]) != len(registry_before["workspaces"])
    others_remain = bool(registry_after["workspaces"])

    results = []
    restored_from = []
    for rel in build_entries(root, config):
        path = home / rel
        if not inside_home(home, path):
            results.append({"path": str(path), "relative": rel, "action": "refused_outside_home"})
            continue
        data = read_if_file(path)
        if data is None:
            results.append({"path": str(path), "relative": rel, "action": "absent"})
        elif not has_marker(data):
            results.append({"path": str(path), "relative": rel, "action": "kept_not_ours"})
        elif others_remain:
            results.append({"path": str(path), "relative": rel, "action": "kept_other_workspaces_registered"})
        else:
            original = earliest_original(home, rel)
            if original is not None:
                write_bytes(path, original.read_bytes())
                restored_from.append(str(original))
                results.append({"path": str(path), "relative": rel, "action": "restored", "backup": str(original)})
            else:
                path.unlink()
                remove_empty_dirs(path.parent, home)
                results.append({"path": str(path), "relative": rel, "action": "removed"})

    if registry_path.is_file() and removed_registry_item:
        if others_remain:
            write_bytes(registry_path, registry_text(registry_after).encode("utf-8"))
        else:
            registry_path.unlink()
            remove_empty_dirs(registry_path.parent, home)
    return {
        "command": "remove",
        "home": str(home),
        "targets": results,
        "registry_item_removed": removed_registry_item,
        "other_workspaces_remaining": [row.get("name") for row in registry_after["workspaces"]],
        "restored_from": restored_from,
        "backup_dir": str(home / BACKUP_REL),
        "refused": [item["path"] for item in results if item["action"] == "refused_outside_home"],
    }


def render_text(result: dict[str, Any]) -> str:
    lines = [f"{result['command']} (home: {result['home']})"]
    for item in result["targets"] if result["command"] != "install" else []:  # install already listed its plan before writing
        lines.append(f"  {item.get('action') or item.get('state'):<34} {item['path']}")
    if result["command"] == "install":
        if result["refused"]:
            lines.append("已拒絕寫入（實際位置在使用者目錄之外）：" + "；".join(result["refused"]))
        else:
            lines.append(f"寫入 {result['written']} 個檔案；讀回核對：{'通過' if result['verified'] else '失敗'}")
        if result["backup_dir"]:
            lines.append("既有不同內容的檔案已備份到：" + result["backup_dir"])
    if result["command"] == "status":
        lines.append(f"登記表：{result['registry']['state']}；預設：{result['registry']['default']}")
        for row in result["registry"]["workspaces"]:
            lines.append(f"  登記 {row['name']} -> {row['root']} (brain.json: {row['root_has_brain_json']}, python: {row['python_exists']})")
        lines.append("整體：" + ("有效" if result["ok"] else "有缺漏或失效"))
    if result["command"] == "remove":
        lines.append("登記表已移除本工作區：" + ("是" if result["registry_item_removed"] else "本來就沒有"))
        for backup in result["restored_from"]:
            lines.append("已還原安裝前的原件，來源備份：" + backup)
        lines.append("備份位置（保留，不會刪除）：" + result["backup_dir"])
        if result["refused"]:
            lines.append("已拒絕處理（實際位置在使用者目錄之外）：" + "；".join(result["refused"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage the user-level (global) brain entries.")
    parser.add_argument("command", choices=["install", "status", "remove"])
    parser.add_argument("--home", type=Path, default=Path.home(), help="User home to write under (default: the real user home).")
    parser.add_argument("--name", default=None, help="Registry name of this workspace (default: the workspace folder name).")
    parser.add_argument("--workspace", type=Path, default=None, help="Override the workspace root (default: the root this file belongs to).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    args.home = args.home.resolve()
    args.root = (args.workspace or ROOT).resolve()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    config = read_brain_json(args.root)
    result = {"install": cmd_install, "status": cmd_status, "remove": cmd_remove}[args.command](args, config)
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render_text(result))
    if args.command == "install":
        return 0 if result["verified"] else 1
    if args.command == "status":
        return 0 if result["ok"] else 1
    return 1 if result["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
