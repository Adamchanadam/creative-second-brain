"""Create a new, profile-selected workspace without rewriting user data."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from package_paths import assert_plain_path
from render_workspace_home import render as render_home
from check_environment import check as check_environment

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(RUNTIME / "tools"))
from graph_preset import ensure_initial_graph_preset

PROFILES = json.loads((ROOT / "profiles" / "profiles.json").read_text(encoding="utf-8"))
INVENTORY = json.loads((ROOT / "spec" / "package-inventory.json").read_text(encoding="utf-8"))

_EXPECTED_MANAGED_ENTRIES = frozenset({
    ("guide", "brain-guide"), ("url", "brain-url"), ("text", "brain-text"), ("image", "brain-image"),
    ("think", "brain-think"), ("ask", "brain-ask"), ("create", "brain-create"), ("grow", "brain-grow"),
    ("research", "brain-research"), ("resume", "brain-resume"), ("check", "brain-check"), ("fix", "brain-fix"),
    ("infographic", "brain-infographic"),
    ("tidy", "brain-tidy"), ("nebula", "brain-nebula"), ("update", "brain-update"),
})
_EXPECTED_CANONICAL_ENTRIES = _EXPECTED_MANAGED_ENTRIES - {("ask", "brain-ask")}


TASK_PROGRESS_CARD_RULE = (
    "如本次是多步、較長、除錯、根因修復或驗收，先依 `AGENTS.md` 指定的唯一任務現況位置建立或接續「🧭 任務進度卡」；未指定時才用 `outputs/task-progress/current.md`。卡只列目標、目前階段（已完成／總階段）、已完成成果、正在做甚麼、下一步及真的受阻原因。除錯、根因修復或驗收另列根因／假說、唯一改動、驗收結果及失敗原因、修復輪次與停止線。開始、每完成一階段、真正受阻或改路線，以及約二十分鐘未有可驗證成果時更新同一張卡；回覆用階段軌道或狀態標記顯示同一現況。卡不是完成證據；每輪修復必須是新證據 → 一項改動 → 可推翻驗收，同一假說兩次失敗時停止盲目重跑並改回根因分析。"
)


def managed_entries() -> list[tuple[str, str]]:
    contract = INVENTORY.get("daily_entry_contract") if isinstance(INVENTORY, dict) else None
    entries = contract.get("managed_entries") if isinstance(contract, dict) else None
    if not isinstance(entries, list) or len(entries) != 16:
        raise ValueError("package ownership inventory has no sixteen-entry managed contract")
    result = []
    for item in entries:
        if (not isinstance(item, dict) or not isinstance(item.get("command"), str) or not isinstance(item.get("skill"), str)
                or item.get("role") not in {"canonical", "compatibility_alias"}):
            raise ValueError("package ownership inventory daily entry is invalid")
        result.append((item["command"], item["skill"]))
    if len({skill for _, skill in result}) != len(result) or len({command for command, _ in result}) != len(result):
        raise ValueError("package ownership inventory has duplicate daily entries")
    roles = {item["command"]: item["role"] for item in entries}
    if set(result) != _EXPECTED_MANAGED_ENTRIES or roles != {
        command: "compatibility_alias" if command == "ask" else "canonical"
        for command, _ in _EXPECTED_MANAGED_ENTRIES
    }:
        raise ValueError("package ownership inventory has an unexpected managed entry")
    return result


def canonical_entries() -> list[tuple[str, str]]:
    contract = INVENTORY["daily_entry_contract"]
    entries = [(item["command"], item["skill"]) for item in contract["managed_entries"] if item.get("role") == "canonical"]
    if len(entries) != len(_EXPECTED_CANONICAL_ENTRIES) or set(entries) != _EXPECTED_CANONICAL_ENTRIES:
        raise ValueError("package canonical entry inventory is invalid")
    registry = json.loads((ROOT / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    try:
        ordered = sorted(entries, key=lambda entry: registry[entry[0]]["menu_order"])
    except (KeyError, TypeError):
        raise ValueError("package canonical entry menu metadata is invalid") from None
    if any(not isinstance(registry[key].get("menu_order"), int) for key, _ in ordered):
        raise ValueError("package canonical entry menu metadata is invalid")
    return ordered


def plain_child(root: Path, value: str) -> Path:
    raw = value.replace("\\", "/"); rel = Path(raw)
    windows_rel = PureWindowsPath(raw)
    if (not raw.strip() or raw.startswith("/") or rel.is_absolute() or windows_rel.is_absolute()
            or windows_rel.drive or ".." in rel.parts or rel == Path(".")):
        raise ValueError("vault must be a named relative path inside the workspace")
    if rel.parts[0].casefold() in {"tools", "docs", "assets", "inputs", "outputs", ".git", ".agents", "brain.json", "agents.md", "index.html", "requirements.txt", ".package-init.json"}:
        raise ValueError("vault overlaps a package runtime path")
    target = root / rel
    # Check the lexical path before resolving it: resolving first would hide a
    # junction in the configured vault root and could redirect the renderer.
    assert_plain_path(target)
    resolved = target.resolve()
    if not resolved.is_relative_to(root.resolve()): raise ValueError("vault path escapes workspace")
    return target


def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def workspace_config_bytes(config: dict) -> bytes:
    """Return the byte-stable v2 workspace configuration encoding.

    This is shared with ``manage_workspace`` because its managed ownership
    checks use the configuration's on-disk bytes.
    """
    return (json.dumps(config, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def management_binding() -> dict[str, str]:
    return {
        "package_version": str(PROFILES["version"]),
        "profile_build_sha256": sha(ROOT / "profile-build.json"),
        "inventory_sha256": sha(ROOT / "spec" / "package-inventory.json"),
        "runtime_provenance_sha256": sha(ROOT / "runtime" / "snapshot-provenance.json"),
    }


def entry_contract(profile: str) -> list[dict[str, str]]:
    if profile != "public-portable":
        raise ValueError("unsupported portable profile")
    registry = json.loads((ROOT / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    mapping = canonical_entries()
    return [{"key": key, "skill": skill, "home_title": str(registry[key]["home_title"]),
             "purpose": str(registry[key]["purpose"]), "host_prompt": str(registry[key]["host_prompt"])}
            for key, skill in mapping]


def skill_entry_payloads(profile: str) -> dict[str, bytes]:
    """Return the complete generated public entry set without writing it.

    Both first-time setup and managed adoption use this one renderer.  Keeping
    it pure lets an adoption plan show the exact managed hashes without
    creating a temporary skill tree or changing an existing workspace.
    """
    registry = json.loads((ROOT / "spec" / "command-registry.json").read_text(encoding="utf-8"))["commands"]
    skill_names = dict(managed_entries())
    reader_opening_instruction = (
        "只可執行 `{command}` 開啟本機閱讀服務；命令回傳 `status=ready` 及 {url_requirement} 的精確 URL 才算服務已確認，然後立即提供 URL。"
        "提供 URL 後才可使用宿主已有且已證明能連到該 URL 的瀏覽器或顯示工具；正常開啟不自動抓 DOM、截圖或驗收 UI。"
        "不可直接執行 `knowledge_nebula_server.py`、自行猜 port，或以瀏覽器存在與否代替服務確認。"
        "若下一次實際使用時 URL 無法連線，先以最小範圍分清產品缺陷、AI 操作錯誤，或該 AI 執行環境不能在命令結束後保留服務；後兩者只修正操作，不得改產品或寫死 OS、路徑、執行器假設。"
    )
    specialized = {
        "url": "可保存完整文章、論文或提交即已授權的 YouTube 字幕；摘要、影片描述、登入牆摘要或只讀頁面不當全文。正常 knowledge_only 工作單會提供完整來源 block、compact schema 及指紋；分析必填摘要、知識點和證據 block ID，來源 metadata／relations／application 在來源有實質依據時一併填入。程式保留全文、逐字證據、欄位、讀回和 completion receipt；資訊圖只在使用者之後明示時補做。若要查看結果，從 completion receipt 讀取當前成果頁；宿主有安全本機開檔工具時才開啟它，再用 `tools/brain.py home` 讀回首頁／成果總覽；沒有開檔工具便提供可點擊連結，不得假稱已打開。",
        "text": "保存完整文字時，先按正式 rail start／work_order 取得完整來源 block 和 compact schema。每個知識點必須有真實 evidence_block_ids；來源自身有實質用途、採納、關係、複利或品味判斷時，使用可選 application／relations／metadata 欄位表達，沒有既有脈絡便如實 needs_context。程式保留全文、逐字證據、既有 Obsidian 欄位、讀回和 completion receipt；資訊圖不阻礙文字成果，只在正式入庫後按明示要求補做。",
        "image": "保留原圖，分開可讀文字與看不清內容；不把猜測寫成來源事實。正常 knowledge_only 可在同一份回應提交原圖解讀和以 IMG-* 錨點支持的 compact semantic plan；解讀已接納而語義被拒時，只按下一份工作單補語義，不重讀原圖。這是原圖入庫，不是畫圖或補圖。",
        "infographic": "只為指定 source_id 的已入庫知識製作可選資訊圖；文字內容已完成，不會因未製圖而受阻。先用 `tools/brain.py rail add-infographic --source-id <SOURCE_ID>` 讀取同一來源的工作單；已有合格圖只交付現有成果，不自動畫。只有使用者明示重製才加 `--rebuild` 並沿用原有 revision 計畫；沒有 source ID 時只列可選來源，不掃描全庫。原圖入庫不等於已製資訊圖。",
        "think": "先讀證據再形成判斷；若 prepare 回 `needs_review` 或 `deferred_required_paths` 非空，代表原文與知識點包未齊，必須收窄問題或明示選定一個來源後重新 prepare，不可用部分材料作答。深入分析只有經過來源綁定的 answer、review 與讀回後才會保存思考紀錄；簡短事實回答不會自動建立紀錄。",
        "grow": "先評估來源是否適合補充知識庫；適合才交由正式入庫流程，不自動保存探索結果。",
        "create": "先選取庫內材料再創作；每個候選先讀其 `must_read`（來源筆記、應用筆記及已列衍生應用，來源筆記已載知識點草案），再按需要讀 `knowledge_point_paths` 列出的知識點；材料未齊時先收窄至一個完整來源包，不可把片段知識點當原文。新作品不自動回寫為知識來源。",
        "tidy": "先提出可核對的整理建議；只有使用者指定時才改閱讀頁或目錄。",
        "guide": "若使用者只問目前版本，讀取 `brain.json` 的 `package_version` 後直接回答「目前使用：創造型第二大腦 v…」，不要啟動服務或改檔。要求首頁、成果總覽或常用操作時，" + reader_opening_instruction.format(command="tools/brain.py open-home", url_requirement="以 `index.html#prompts` 結尾") + "沒有可用工具或瀏覽器開啟失敗時，URL 仍屬已交付，並如實說明 UI 未確認；只有使用者明確要求 UI 驗收或除錯時，才進入瀏覽器流程。不要把 `tools/brain.py home` 的唯讀診斷當作開啟路由；只有使用者明確要求更新說明或重建首頁時才執行 `tools/brain.py guide`。",
        "nebula": reader_opening_instruction.format(command="tools/brain.py nebula", url_requirement="正確的知識星雲") + "沒有可用工具或瀏覽器開啟失敗時，URL 仍屬已交付，並如實說明 UI 未確認。這不會重新入庫、重做來源成果頁或生成圖片；宿主未列出 slash command 時可讀本檔後執行，但不得把檔案存在誤報為宿主已辨識。",
        "check": "唯讀核對指定成果；不修復、重建或把部分證據補成完成。",
        "resume": "保留同一工作身份與已合格成果；先讀 context，不猜舊聊天內容。已入庫而尚未製圖的來源可接續同一來源補圖，不重開入庫。",
        "fix": "一般修復先用工作編號執行 `tools/brain.py rail inspect --job-id <JOB_ID>`，再只按工作單列出的正式修復路徑處理；例：`$brain-fix INGEST-123 的閱讀頁打不開，請修復` 不得轉為補圖。補製資訊圖一律使用 `brain-infographic`，不作全庫整理或產品重設計。",
        "research": "先讀 docs/研究主題操作.md，再列出或選定主題；PUBLIC 只可按使用者直接要求建立或接續 manual_research，不能建立排程。",
        "update": "只可用 `tools/brain_update.py` 的正式更新流程。使用者要求 `brain-update` 時，先以 `--workspace <workspace> --check` 讀回候選與計畫；狀態是 `checked` 才以同一工作區執行 `--apply`。只接受正式 GitHub `Adamchanadam/creative-second-brain` 發布來源，不用 npm；衝突、未完成交易或 recovery 一律保留現場並回報，不能自動接受 resolution。更新只處理受管 runtime 與入口，絕不觸碰私人 vault、個人設定或既有全域舊 skill。",
    }
    entries = {}
    for key, name in skill_names.items():
        definition = registry[key]
        entry = definition["entry"]
        if key == "ask":
            entries[name] = (
                "---\nname: brain-ask\ndescription: 舊名相容入口；請改用 brain-think。\n---\n\n"
                "# brain-ask（舊名相容）\n\n"
                "這是舊名稱的相容入口，不是另一套行為規則。先讀 `../brain-think/SKILL.md`，再使用同一條正式工具路由："
                "`<workspace>/tools/brain.py think prepare --question <問題>`。新對話與首頁一律使用 `brain-think`；不得因這個別名跳過來源、answer、review、thinking archive 或讀回。\n"
            )
            continue
        route_command = entry.removeprefix("<workspace>/")
        preflight = (
            "1. 先讀工作區的 `AGENTS.md` 與 `brain.json`。只問版本時直接讀設定；要求首頁時按正式開啟路由，勿讀取 `context`。只有使用者明確要求更新說明或重建首頁時，才執行重建路由。\n"
            if key == "guide" else
            "1. 先讀工作區的 `AGENTS.md` 與 `brain.json`。此入口必須先以 `tools/brain_update.py --workspace <workspace> --check` 讀回計畫；舊版 workspace 不可先要求 `doctor` 或 `context`，以免舊 runtime 阻塞正式升級。\n"
            if key == "update" else
            "1. 直接以 `brain.json` 的 `runtime_python` 執行本入口的 "
            f"`{route_command}`。`AGENTS.md` 已由宿主載入，不再重讀；不先跑 doctor、context、測試，不讀工程規格；執行規則只見 Compiler 規格 §7.1–7.2，所需資料都在工作單。\n"
            if key in {"url", "text", "image"} else
            f"1. 直接以 `brain.json` 的 `runtime_python` 執行 `{route_command}` 讀取工作單；`AGENTS.md` 已由宿主載入，不再重讀；不先跑 doctor、context 或測試。按工作單讀完必讀規格、填草稿、跑 `check-infographic-plan` 至通過再 submit。\n"
            if key == "infographic" else
            "1. 先讀 `docs/研究主題操作.md`，再用 `brain.json` 的 `runtime_python` 執行唯讀的 `tools/brain.py research list` 或 `tools/brain.py research-program next --topic-id <topic-id>`（零寫入）取得下一步命令；不先跑 doctor、context。只操作這個工作區。\n"
            if key == "research" else
            "1. 先讀工作區的 `AGENTS.md` 與 `brain.json`，再用設定中的 Python 執行 `tools/brain.py doctor` 和 `tools/brain.py context`。只操作這個工作區。\n"
        )
        obsidian_boundary = (
            "若使用者要求以 Obsidian CLI 讀取、搜尋或開啟這個工作區的筆記，只可使用 "
            "`tools/brain.py obsidian --cli <已核對的 Obsidian CLI 完整路徑> --vault-id <明確 vault ID> "
            "<check|read|search|open>`。它每次先核對 `vault info=path` 是否正是 brain.json 的 vault；"
            "只支援唯讀 read/search/check 與 requested-only 的 open，不可直接呼叫 Obsidian、使用 vault 名稱、依賴 active vault/file，或傳入任意 command ID。Graph 保留原生 UI 手動入口，未由此工具驗證。\n"
        )
        slash_note = "若宿主沒有把這個技能列為 slash command，直接讀本檔後按上述流程執行；不要把檔案存在誤報為宿主已辨識。\n"
        completion_report = "完成即回報 completion receipt 的 `execution`（總秒數、目標、狀態）及成果頁位置。"
        header = f"---\nname: {name}\ndescription: {definition['purpose']}\n---\n\n# {name}\n\n"
        if key in {"url", "text", "image"}:
            work_order_step = (
                "2. 只按回傳的工作單指標做：依序讀完閱讀檔、填草稿、跑 check 至通過、submit；未完成便以同一 job 繼續（`rail advance --job-id <id>` 或工作單所列命令）。"
                + ("原圖入庫則按工作單的原圖解讀要求提交，不用閱讀檔。" if key == "image" else "")
                + "\n"
            )
            entries[name] = (
                header
                + preflight
                + work_order_step
                + f"3. {specialized[key]}\n"
                + "4. 正常單篇入庫不建立任務進度卡、不寫交接或日誌；只有真正受阻（擷取失敗、權限、runtime 錯誤）才停止並回報具體阻塞。" + completion_report + "\n"
                + "5. " + obsidian_boundary
                + "6. " + slash_note
            )
        else:
            progress_step = (
                "5. 正常單次補圖不建立任務進度卡、不寫交接或日誌；只有真正受阻才停止並回報具體阻塞。" + completion_report + "\n"
                if key == "infographic" else f"5. {TASK_PROGRESS_CARD_RULE}\n"
            )
            entries[name] = (
                header
                + preflight
                + f"2. 本入口的正式工具路由是：`{entry}`。先讀相應的 runtime 規格與工作單；不要自行猜參數、schema 或完成狀態。\n"
                + f"3. {specialized[key]}\n"
                + "4. " + obsidian_boundary
                + progress_step
                + "6. " + slash_note
            )
    payloads: dict[str, bytes] = {}
    for name, content in entries.items():
        definition = registry[next(key for key, skill_name in skill_names.items() if skill_name == name)]
        path = Path(".agents") / "skills" / name / "SKILL.md"
        payloads[path.as_posix()] = content.encode("utf-8")
        menu_order = definition.get("menu_order")
        menu_group = definition.get("menu_group")
        menu_label = definition.get("menu_label")
        if (not isinstance(menu_order, int) or not 1 <= menu_order <= 99
                or not isinstance(menu_group, str) or not menu_group.strip()
                or not isinstance(menu_label, str) or not menu_label.strip()):
            raise ValueError(f"daily entry menu metadata is invalid: {key}")
        display_title = f"{menu_order:02d} {menu_group}｜/{name}｜{menu_label}"
        metadata = path.parent / "agents" / "openai.yaml"
        payloads[metadata.as_posix()] = (
            "interface:\n"
            + "  display_name: " + json.dumps(display_title, ensure_ascii=False) + "\n"
            + "  short_description: " + json.dumps(definition["purpose"], ensure_ascii=False) + "\n"
            + "  default_prompt: " + json.dumps(definition["host_prompt"], ensure_ascii=False) + "\n"
        ).encode("utf-8")
    return payloads


def write_skill_entries(workspace: Path, profile: str) -> dict[str, str]:
    """Write the complete public entry set into one new workspace."""
    written = {}
    for relative, payload in skill_entry_payloads(profile).items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        written[path.relative_to(workspace).as_posix()] = sha(path)
    return written


def write_empty_reader_pages(vault: Path) -> None:
    pages = {"index.html":"成果總覽", "knowledge.html":"知識成果", "thinking.html":"思考紀錄"}
    target = vault / "outcome-pages"; target.mkdir(parents=True, exist_ok=True)
    for name, title in pages.items():
        (target / name).write_text("<!doctype html><meta charset=\"utf-8\"><title>" + title + "</title><h1>" + title + "</h1><p>尚無成果；先保存完整文字或提出問題。</p>", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILES["profiles"]))
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--vault")
    parser.add_argument("--display-name")
    parser.add_argument("--legacy-preview", type=Path, help="Read an old brain.json without changing it")
    parser.add_argument("--set-display-name", help="Update workspace display metadata and home only")
    parser.add_argument("--refresh-home", action="store_true", help="Rebuild this workspace's daily home from the installed package")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        if args.refresh_home:
            if not args.workspace or args.profile or args.vault or args.display_name or args.legacy_preview or args.set_display_name:
                raise ValueError("refresh-home requires only --workspace")
            workspace = args.workspace.absolute(); assert_plain_path(workspace)
            marker = workspace / "brain.json"; home = workspace / "index.html"
            assert_plain_path(marker); assert_plain_path(home)
            current = json.loads(marker.read_text(encoding="utf-8"))
            if current.get("package_schema") != "creative_second_brain_portable_v1":
                raise ValueError("refresh-home only supports this package's workspace")
            plain_child(workspace, current["vault"])
            if args.dry_run:
                print(json.dumps({"status":"home_refresh_planned", "writes":0,
                                  "historical_notes_rewritten":False}, ensure_ascii=False, indent=2)); return 0
            render_home(runtime=workspace, workspace=workspace, vault_rel=current["vault"],
                        display_name=current["display_name"], package_version=current["package_version"])
            print(json.dumps({"status":"home_refreshed", "writes":1, "historical_notes_rewritten":False}, ensure_ascii=False, indent=2)); return 0
        if args.set_display_name:
            if not args.workspace or args.profile or args.vault or args.display_name or args.legacy_preview:
                raise ValueError("set-display-name requires only --workspace and --set-display-name")
            workspace = args.workspace.absolute(); assert_plain_path(workspace)
            marker = workspace / "brain.json"
            home = workspace / "index.html"
            # Both paths are read or overwritten below.  Validate their actual
            # entries before reading config or changing either file.
            assert_plain_path(marker); assert_plain_path(home)
            current = json.loads(marker.read_text(encoding="utf-8"))
            if current.get("package_schema") != "creative_second_brain_portable_v1":
                raise ValueError("display-name update only supports this package's workspace")
            display_name = args.set_display_name.strip()
            if not display_name: raise ValueError("display name is required")
            # Validate the configured vault lexically before the existing
            # renderer receives it; do not let Path.resolve hide a junction.
            plain_child(workspace, current["vault"])
            if args.dry_run:
                print(json.dumps({"status":"display_name_planned", "writes":0,
                                  "historical_notes_rewritten":False}, ensure_ascii=False, indent=2)); return 0
            current["display_name"] = display_name
            marker.write_bytes(workspace_config_bytes(current))
            render_home(runtime=workspace, workspace=workspace, vault_rel=current["vault"],
                        display_name=current["display_name"], package_version=current["package_version"])
            print(json.dumps({"status":"display_name_updated", "writes":2,
                              "historical_notes_rewritten":False}, ensure_ascii=False, indent=2)); return 0
        if args.legacy_preview:
            legacy = json.loads(args.legacy_preview.read_text(encoding="utf-8"))
            if legacy.get("schema") != "creative_second_brain_workspace_v1" or legacy.get("version") != "1.4.5":
                raise ValueError("legacy preview accepts only a brain.json v1.4.5 fixture/workspace")
            print(json.dumps({"status":"legacy_preview", "version":legacy["version"], "vault":legacy.get("vault"),
                              "migration":"not performed", "writes":0}, ensure_ascii=False, indent=2)); return 0
        if not all((args.profile, args.workspace, args.vault, args.display_name)):
            raise ValueError("provide --profile, --workspace, --vault and --display-name for a new workspace")
        if not args.display_name.strip(): raise ValueError("display name is required")
        if not RUNTIME.is_dir(): raise ValueError("package runtime core is missing")
        workspace = args.workspace.absolute(); assert_plain_path(workspace)
        if workspace == Path(workspace.anchor) or workspace == Path.home().resolve() or ROOT in workspace.parents or workspace in ROOT.parents:
            raise ValueError("choose a new data folder outside the package and home roots")
        vault = plain_child(workspace, args.vault)
        if (workspace / "brain.json").exists() or (workspace / ".package-init.json").exists():
            raise ValueError("existing or interrupted workspace is preserved; no migration is performed")
        if workspace.exists() and any(workspace.iterdir()):
            raise ValueError("workspace must be new or empty; existing user data is preserved")
        files = [p for p in RUNTIME.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        if not files: raise ValueError("package runtime core is empty")
        plan = {"status": "planned" if args.dry_run else "initialized", "package_version": PROFILES["version"],
                "profile": args.profile, "display_name": args.display_name, "vault": vault.relative_to(workspace).as_posix(),
                "runtime_file_count": len(files), "graph_preset": "new vault receives the portable visual preset; existing settings are preserved",
                "writes": 0 if args.dry_run else len(files) + 13}
        if args.dry_run:
            print(json.dumps(plan, ensure_ascii=False, indent=2)); return 0
        environment = check_environment()
        if environment["status"] != "ready":
            raise ValueError("Python dependencies are not ready: " + "; ".join(environment["errors"]))
        plan["browser_capture"] = environment.get("browser_capture", {})
        if not workspace.exists(): workspace.mkdir(parents=True, exist_ok=False)
        (workspace / ".package-init.json").write_text(json.dumps({"state":"initializing"}), encoding="utf-8")
        runtime_files = {}
        for source in files:
            relative = source.relative_to(RUNTIME); destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle: handle.write(source.read_bytes())
            runtime_files[relative.as_posix()] = sha(destination)
        brain = ROOT / "scripts" / "brain.py"; target_brain = workspace / "tools" / "brain.py"
        with target_brain.open("xb") as handle: handle.write(brain.read_bytes())
        runtime_files["tools/brain.py"] = sha(target_brain)
        for folder in ("_registry", "sources", "applications", "knowledge-points", "concepts", "maps", "assets", "thinking", ".obsidian"):
            (vault / folder).mkdir(parents=True, exist_ok=True)
        ensure_initial_graph_preset(vault)
        write_empty_reader_pages(vault)
        # Input/output folders are workspace-owned handoff surfaces, not vault
        # content. They make the documented text command usable without asking a
        # new user to invent a missing directory.
        for folder in ("inputs", "outputs"):
            (workspace / folder).mkdir(exist_ok=True)
        for name in ("sources.jsonl", "knowledge_points.jsonl", "relations.jsonl"):
            (vault / "_registry" / name).write_text("", encoding="utf-8")
        entry_files = write_skill_entries(workspace, args.profile)
        startup = workspace / "AGENTS.md"
        startup.write_text("# 此工作區的第二大腦\n\n先讀 brain.json，核對本工作區的 vault、display_name 和 runtime_python。日常保存網址／文字／原圖時，直接用該 Python 執行相應 `tools/brain.py rail start` 並讀回 work_order；正常工作只讀工作單提供的 schema 和完整來源／原圖 block，不先跑 doctor、context 或重讀工程規格。接續既有未完工作才用 context；路徑失效時先核實本機環境，不猜或改用另一專案。日常技能在 .agents/skills/brain-*，包括保存網址／文字／圖片、提問、探索、創作、整理、首頁、知識星雲、核對、接續、修復、研究及安全更新。多步、較長、除錯、根因修復或驗收工作只有一張目前任務進度卡；本 workspace 的預設位置是 outputs/task-progress/current.md，細節按被呼叫的 brain-* skill。不得把本地 doctor 通過當作 Obsidian、宿主辨識或圖片能力已驗收。PUBLIC 沒有背景研究排程，只可由使用者直接啟動研究。\n", encoding="utf-8")
        entry_files["AGENTS.md"] = sha(startup)
        # AGENTS.md is a local orientation note. The manager owns only the
        # public runtime and the generated daily entries, so it never
        # overwrites a workspace's operator note during adoption or update.
        managed_files = {key: value for key, value in {**runtime_files, **entry_files}.items() if key != "AGENTS.md"}
        config = {"schema":"creative_second_brain_workspace_v3", "package_schema":"creative_second_brain_portable_v1",
                  "package_version":PROFILES["version"], "profile":args.profile, "display_name":args.display_name,
                  "vault_name":vault.name, "vault":vault.relative_to(workspace).as_posix(), "created_at":datetime.now(timezone.utc).isoformat(),
                  "runtime_python":sys.executable, "runtime_files":runtime_files, "entry_files":entry_files, "managed_files":managed_files, "owner_aliases":[],
                  "entry_contract":entry_contract(args.profile), "management":{"schema":"creative_second_brain_workspace_manager_v2", "control_directory":".brain-management", "package_binding":management_binding(), "private_exclusions": sorted(["knowledge-base-vault/", "inputs/", "outputs/", ".obsidian/", "connections/", "jobs/", "registry/", "receipts/", vault.relative_to(workspace).as_posix().rstrip("/") + "/", vault.relative_to(workspace).as_posix().rstrip("/") + "/.obsidian/"])}, "legacy_compatibility":"preview_only"}
        (workspace / "brain.json").write_bytes(workspace_config_bytes(config))
        render_home(runtime=workspace, workspace=workspace, vault_rel=config["vault"], display_name=config["display_name"],
                    package_version=config["package_version"])
        # First installation only: create the advertised graph and real empty catalogues.
        # This runs after the new workspace marker and empty registries exist.
        from knowledge_nebula import build as build_nebula
        from outcome_catalog import write_catalog_pages
        build_nebula(workspace)
        write_catalog_pages([], vault/'outcome-pages', 20, vault=vault)
        (workspace / ".package-init.json").unlink()
        print(json.dumps(plan, ensure_ascii=False, indent=2)); return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status":"blocked", "reason":str(error)}, ensure_ascii=False)); return 2


if __name__ == "__main__":
    raise SystemExit(main())
