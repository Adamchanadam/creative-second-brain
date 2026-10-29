"""Background incubation jobs behind the nebula's 星之搖籃 panel.

The local nebula server owns one worker thread.  A job prepares a fixed material packet, lets a restricted,
already-logged-in AI process write an answer, checks it mechanically, has a fresh process review it
independently (at most three rounds) and, only when the existing formal validators accept it, saves it with
``thinking_archive.archive_answer``.  Nothing here has a web or API-key path.
"""
from __future__ import annotations

import ctypes
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import threading
import time
from datetime import datetime

try:
    from . import ask_second_brain as ask
    from . import thinking_archive
except ImportError:
    import ask_second_brain as ask
    import thinking_archive

JOB_ID = re.compile(r"CJ-\d{8}-\d{4}\Z")
OPS_ROOT = Path(__file__).resolve().parents[1]
SPEC_DOC = OPS_ROOT / "docs" / "Obsidian語義索引與關聯圖落地方案.md"
SPEC_HEADINGS = ("#### 取證、推理與交付", "#### `thinking_v2` 資料契約")
THOUGHT_ANALYSIS = Path(__file__).resolve().with_name("thought_analysis.py")
SETTINGS_SCHEMA = "knowledge_cradle_settings_v1"
DEFAULT_SETTINGS = {"schema": SETTINGS_SCHEMA, "runner": None, "claude_command": None, "codex_command": None,
                    "timeouts": {"write": 900, "other": 600}, "max_review_rounds": 3, "max_mechanical_fixes": 2}
QUOTE = re.compile(r"「([^「」]+)」")
MAX_ACTIVE_JOBS = 10
STALE_WAIT_SECONDS = 15.0  # how long a recorded child from a previous server run gets to disappear after it was told to end
SUSPENDED_WAITS = (15.0, 5.0)  # how long a never-started (suspended) AI process gets to exit after each of its two termination attempts
EXPECTED_TOOLS = ["Edit", "Glob", "Grep", "Read", "Write"]
SECRET_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"}
RESTART_CODES = {"review_not_converged", "mechanical_unfixed", "review_invalid", "questions_invalid"}
STEP_SECONDS = {"prepare": 5, "write": 240, "check": 5, "review": 150, "revise": 150, "archive": 30, "questions": 180}
STEP_NAMES = {"write": "寫答案", "fix": "修正引文", "review": "覆核", "revise": "修訂", "questions": "擬問題", "qfix": "擬問題"}
PUBLIC_STEP = {"prepare": "prepare", "write": "write", "check": "check", "review": "review", "revise": "revise",
               "archive": "archive", "refresh": "refresh", "questions": "questions", "qcheck": "questions"}

# The prompts below are the proven artefact of the incubation experiment: copied character for character.
COMMON = """你在背景替「知識搖籃」工作，沒有人可以回答你的問題。工作資料夾就是目前資料夾，你只可以讀寫這個資料夾。你不能執行程式；你完成後，伺服器會用同一套規則核對。

寫作規則（伺服器會逐條核對）：
- 「」只用於逐字引用 packet.json evidence 的 text 原文（英文原文可照抄英文）。所有放在「」內的文字，都必須能在某一份 evidence text 中原樣找到。翻譯、轉述、你自己的概念名稱，一律不可放在「」內；需要標示時改用『』或不加引號。
- premises 的 quote 必須逐字取自該 evidence 的 text。
- 保留來源原本的確定程度：來源說「可能」，就不能寫成「會」、「必然」或「必須」；你自己的推論要寫明是推論，並寫出成立條件；不要把相關寫成因果。
- 引用只用 packet evidence 的 id（例如 L1）；不上網：不要寫 web_sources 或 web_reason。全部用繁體中文。
- 時效：你不能上網核對最新資料。把依賴的前提分成「不受時間影響」與「有時效」兩類；有時效的結論要寫明「未上網核對，以筆記內容為準」，不可寫成現況。
"""
WRITE_TASK = COMMON + """
輸入：packet.json（問題 question、證據原文 evidence[].id / path / text、已成立的跨來源張力 established_tensions）、spec.md（答案規格，thinking_v2 資料契約）、thought_analysis.py（程式怎樣核對 analysis，只供對照格式）、tension_check.py（程式怎樣核對 tension_responses）。

任務：按 spec.md 的 thinking_v2 資料契約，深入回答 packet.json 的 question，寫 answer.json：
{"session_id": "<packet.json 的 session_id>", "analysis": {thinking_v2 內容}, "tension_responses": [{"relation_id": "...", "response": "..."}]}
- packet 有 established_tensions 時，每個 relation_id 要一項實質回應；沒有便寫空陣列。
- 寫完後，用嚴格覆核者的標準逐項自查：每個「」是否逐字、每步推論有沒有跳步、有沒有把可能寫成必然或因果、有沒有把來源的貢獻寫成來源原話；再對照 thought_analysis.py 的 validate_analysis 及 tension_check.py 修正格式。

最後寫 receipt.json：{"status":"completed","outputs":["answer.json"],"note":"一句說明"}；無法完成時 status 寫 "failed" 並在 note 寫原因。
只寫 answer.json 和 receipt.json。
"""
FIX_TASK = COMMON + """
輸入：packet.json、answer.json（你上一版答案）、mechanical_errors.txt（伺服器核對發現的問題）、spec.md、thought_analysis.py、tension_check.py。

任務：只修正 mechanical_errors.txt 列出的問題，其餘內容不要改。不逐字的「」引文：改成 evidence text 中真正的原文，或拿走「」改為轉述。直接覆寫 answer.json，session_id 不變。

最後寫 receipt.json：{"status":"completed","outputs":["answer.json"],"note":"一句說明改了甚麼"}；無法完成時 status 寫 "failed" 並寫原因。
只寫 answer.json 和 receipt.json。
"""
REVISE_TASK = COMMON + """
輸入：packet.json、answer.json（上一版答案）、review.json（獨立覆核意見）、spec.md、thought_analysis.py、tension_check.py。

任務：按 review.json 中 verdict 為 revise 的意見修訂 answer.json，並考慮 accept 項目提出的反對意見是否需要小幅補充。
- 只修改需要修改的地方；session_id 不變。
- 若某項洞見無法在證據內站得住，可刪去或降格（kind 改為 hypothesis 並在 assumptions 寫明）。
- 新加或改寫的文字同樣要守上面的寫作規則：以往修訂時最常見的新錯誤，是改寫時加入了不逐字的「」引文，或把條件句改成了必然句。
- 修訂後用嚴格覆核者的標準重新自查全文，再對照 thought_analysis.py 及 tension_check.py 修正格式。直接覆寫 answer.json。

最後寫 receipt.json：{"status":"completed","outputs":["answer.json"],"note":"一句說明改了甚麼"}；無法完成時 status 寫 "failed" 並寫原因。
只寫 answer.json 和 receipt.json。
"""
REVIEW_TASK = """你是獨立覆核者，在背景替「知識搖籃」工作，沒有人可以回答你的問題。工作資料夾就是目前資料夾，你只可以讀寫這個資料夾。你沒有參與寫 answer.json。

輸入：packet.json（問題及全部證據原文 evidence[].id / text）、answer.json（另一個 AI 寫的答案，thinking_v2）、target.txt（程式回傳的覆核指紋 review_target_sha256）、spec.md（答案及覆核規格）、thought_analysis.py（程式怎樣核對覆核 validate_review，只供對照格式）。{previous}

任務：逐一實讀證據原文，覆核 answer.json 的每一個 insight，寫 review.json：
{{"target_sha256": "<target.txt 內容>", "reviewer": "headless-independent-reviewer", "method": "independent",
 "items": [{{"id": "<insight id>", "increment_check": "...", "grounding_check": "...", "strongest_objection": "...", "adjudication": "...", "verdict": "accept 或 revise", "visual_check": "（insight 有 map_labels 時必填）"}}]}}
- 每個 insight 一項，理由要具體。
- 判 revise 的標準：問題會令讀者相信證據不支持的事，或推論不成立。包括：引文不逐字、把轉述當原話、推論跳步、把可能寫成必然或確定因果、張冠李戴、把有時效而未經核對的說法寫成現況。
- 只屬措辭偏好、可再補充的角度、或不影響結論的小瑕疵，判 accept，並把建議寫在 adjudication。
- answer 的 insights 為空時，改填 no_increment_check。全部用繁體中文。

最後寫 receipt.json：{{"status":"completed","outputs":["review.json"],"note":"一句說明（列出 revise 的 insight，如有）"}}；無法完成時 status 寫 "failed" 並寫原因。
只寫 review.json 和 receipt.json，不要寫其他檔案，也不要修改 answer.json。
"""
PREVIOUS = """
另有 previous_review.json：上一輪另一位覆核者的意見；答案已按它修訂。先逐項確認上一輪 revise 的問題是否已真正解決，再用同一標準檢查修訂後全文（包括新加的文字）。上一輪 accept 而修訂後未有改動的部分，除非發現明顯錯誤，否則不要另找新問題。"""
QUESTIONS_TASK = """你在背景替「知識搖籃」工作，沒有人可以回答你的問題。工作資料夾就是目前資料夾，你只可以讀寫這個資料夾。你不能執行程式；你完成後，伺服器會檢查格式。

輸入：packet.json（暫定問題 question、證據原文 evidence[].id / path / text、已成立的跨來源張力 established_tensions）、ways.json（五種思考方式，每種有 angle 及 prompt）。{direction}

任務：先讀完 packet.json 全部 evidence 及 established_tensions，暫時不要作答。按 ways.json 的次序，每種方式各擬一條問題，寫 questions.json：
{"session_id": "<packet.json 的 session_id>", "questions": [{"angle": "<ways.json 該項的 angle>", "question": "一句問題", "why": "一句為甚麼值得問", "notes": ["L1", "L2"]}], "surprising": <最出人意表那條的位置，0 至 4>}
- 每條問題只寫一行、不超過 120 字，要具體針對這幾份筆記的內容，不要泛泛而問；why 不超過 150 字；notes 只列 packet evidence 的 id，寫出問題所依據的筆記。
- {focus}
- 問題要能只靠這些筆記深入思考，不要求上網或最新資料。全部用繁體中文。

最後寫 receipt.json：{"status":"completed","outputs":["questions.json"],"note":"一句說明"}；無法完成時 status 寫 "failed" 並在 note 寫原因。
只寫 questions.json 和 receipt.json。
"""
QUESTIONS_FIX_TASK = """你在背景替「知識搖籃」工作，沒有人可以回答你的問題。工作資料夾就是目前資料夾，你只可以讀寫這個資料夾。你不能執行程式。

輸入：packet.json、ways.json、questions.json（你上一版）、format_errors.txt（伺服器檢查發現的問題）。

任務：只修正 format_errors.txt 列出的問題，其餘內容不要改。直接覆寫 questions.json，session_id 不變。

最後寫 receipt.json：{"status":"completed","outputs":["questions.json"],"note":"一句說明改了甚麼"}；無法完成時 status 寫 "failed" 並寫原因。
只寫 questions.json 和 receipt.json。
"""

WAYS = {"think": [("新應用", "這些筆記的做法，用在哪一個場景會長出甚麼新做法？"), ("新情景", "哪一個你未想過的場合，最有用或最容易出事？"), ("能力轉化", "要把這些筆記由知道變成做到，第一個練習是甚麼？"), ("被忽略的洞見", "這些筆記共同默認的前提若不成立，會看見甚麼新機會？"), ("上升一層", "這些筆記背後同一條原理，還能解釋甚麼？")],
        "gap": [("碰撞", "把其中幾點合起來，可以做出甚麼單靠一方做不到的事？"), ("遷移", "把其中一點的做法搬到另一點，會打開甚麼新可能？"), ("張力突破", "其中互相限制或此消彼長的兩點，有沒有兩者兼得的做法？"), ("上升一層", "這幾點之間的關係，背後共同的規律還能解釋甚麼？"), ("被忽略的前提", "這幾點共同默認的前提若不成立，會看見甚麼新機會？")]}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def squash(text):
    return re.sub(r"\s+", " ", text).strip()


def mechanical(packet, answer):
    """Server-side check before any review: the existing validator plus a verbatim test for every 「」 quote."""
    errors, sha = [], None
    if not isinstance(answer, dict):
        return ["answer.json 必須是一個 JSON 物件"], None
    try:
        sha = ask.review_answer(packet, answer, None)["review_target_sha256"]
    except (ValueError, KeyError, TypeError) as error:
        errors.append(f"程式核對失敗：{error}")
    corpus = [squash(item["text"]) for item in packet["evidence"]]
    bad = sorted({quote for text in strings([answer.get("analysis"), answer.get("tension_responses")])
                  for quote in QUOTE.findall(text) if not any(squash(quote) in source for source in corpus)})
    errors += [f"「{quote}」不在任何 evidence text 中原樣出現" for quote in bad]
    return errors, sha


def check_questions(packet, way, value):
    """Format check of a questions.json; returns the list of problems (empty when valid)."""
    angles = [angle for angle, _ in WAYS[way]]
    ids = {item["id"] for item in packet["evidence"]}
    if not isinstance(value, dict):
        return ["questions.json 必須是一個 JSON 物件"]
    errors, items = [], value.get("questions")
    if value.get("session_id") != packet["session_id"]:
        errors.append("session_id 要與 packet.json 相同")
    surprising = value.get("surprising")
    if type(surprising) is not int or not 0 <= surprising <= 4:
        errors.append("surprising 要是 0 至 4 的整數")
    if not isinstance(items, list) or len(items) != 5:
        return errors + ["questions 要正好有 5 項"]
    seen = set()
    for number, item in enumerate(items):
        label = f"questions[{number}]"
        if not isinstance(item, dict):
            errors.append(label + " 要是物件")
            continue
        if item.get("angle") != angles[number]:
            errors.append(f"{label}.angle 要是「{angles[number]}」")
        question, why, notes = item.get("question"), item.get("why"), item.get("notes")
        if not isinstance(question, str) or not 1 <= len(question) <= 120 or re.search(r"[\r\n]", question):
            errors.append(label + ".question 要是 1 至 120 字的一行文字")
        elif question in seen:
            errors.append(label + ".question 與另一條問題重複")
        else:
            seen.add(question)
        if not isinstance(why, str) or not 1 <= len(why) <= 150:
            errors.append(label + ".why 要是 1 至 150 字的文字")
        if (not isinstance(notes, list) or not notes or len(set(map(str, notes))) != len(notes)
                or not all(isinstance(x, str) and x in ids for x in notes)):
            errors.append(label + ".notes 要是不重複、且存在於 packet evidence 的 id 清單")
    return errors


def spec_text():
    """spec.md = the two sections of the semantic-index document, each from its heading up to the next '#' line."""
    try:
        lines = re.findall(r"[^\n]*\n|[^\n]+", SPEC_DOC.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        raise JobFailure("spec_missing", "找不到答案規格，未能開始孕育。") from None
    parts = []
    for heading in SPEC_HEADINGS:
        start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
        if start is None:
            raise JobFailure("spec_missing", "找不到答案規格，未能開始孕育。")
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
        parts.append("".join(lines[start:end]))
    return "\n".join(parts)


def now_text():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def plain(path):
    """Refuse a link or junction (same test as the nebula's constellation writer)."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("Link/junction paths are not supported")


class CradleError(Exception):
    """A request the HTTP layer answers with this status and sentence."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


class JobFailure(Exception):
    """A step that ends the job as ``failed`` with a plain-language error."""

    def __init__(self, code, message, retryable=True, actions=None, detail=None):
        super().__init__(message)
        self.error = {"code": code, "message": message, "retryable": retryable,
                      "actions": ["retry"] if actions is None and retryable else list(actions or []), "detail": detail}


def unsafe_failure(detail=None):
    return JobFailure("unsafe_runner", "背景 AI 的安全設定與預期不符，已停止，沒有使用它的結果。", False, [], detail)


def stale_failure():
    return JobFailure("stale_process", "上次的背景 AI 仍未結束，未能安全接續。請稍後再試。", True, ["retry"])


class Stopped(Exception):
    """The job was cancelled while a step was running; nothing more is written for it."""


class Interrupted(Exception):
    """The service is retiring: the job goes back to the queue at its unfinished step."""


# ---- Windows process helpers (ctypes); the Job Object is what lets the server end exactly the tree it started ----
_WIN = None


def _win():
    global _WIN
    if _WIN is None:
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes, api.CreateJobObjectW.restype = [ctypes.c_void_p, ctypes.c_wchar_p], ctypes.c_void_p
        api.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        api.SetInformationJobObject.restype = wintypes.BOOL
        api.OpenProcess.argtypes, api.OpenProcess.restype = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], ctypes.c_void_p
        api.AssignProcessToJobObject.argtypes, api.AssignProcessToJobObject.restype = [ctypes.c_void_p, ctypes.c_void_p], wintypes.BOOL
        api.TerminateJobObject.argtypes, api.TerminateJobObject.restype = [ctypes.c_void_p, wintypes.UINT], wintypes.BOOL
        api.CloseHandle.argtypes, api.CloseHandle.restype = [ctypes.c_void_p], wintypes.BOOL
        api.WaitForSingleObject.argtypes, api.WaitForSingleObject.restype = [ctypes.c_void_p, wintypes.DWORD], wintypes.DWORD
        api.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
        api.QueryFullProcessImageNameW.restype = wintypes.BOOL

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]
        api.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(FileTime)] * 4
        api.GetProcessTimes.restype = wintypes.BOOL

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        _WIN = (api, Extended, FileTime)
    return _WIN


class JobObject:
    """A Windows Job Object that kills every process in it when terminated or when its last handle closes."""

    def __init__(self, handle):
        self.handle = handle

    @classmethod
    def attach(cls, process):
        """Put the process (a process handle; it must still be suspended) in a new kill-on-close job; None when Windows refuses."""
        api, extended, _ = _win()
        handle = api.CreateJobObjectW(None, None)
        if not handle:
            return None
        info = extended()
        info.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = bool(api.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info))
                  and api.AssignProcessToJobObject(handle, process))
        if not ok:
            api.CloseHandle(handle)
            return None
        return cls(handle)

    def terminate(self):
        if self.handle:
            _win()[0].TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            _win()[0].CloseHandle(self.handle)
            self.handle = None


def resume_process(handle):
    """Let a process that was created suspended start running (NtResumeProcess); True only when Windows reports success."""
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes, ntdll.NtResumeProcess.restype = [ctypes.c_void_p], ctypes.c_long
    return ntdll.NtResumeProcess(handle) == 0


def process_image_name(pid):
    """Lower-case executable file name of a running pid, or None when it cannot be read."""
    if os.name == "nt":
        api = _win()[0]
        handle = api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            from ctypes import wintypes
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            return Path(buffer.value).name.lower() if api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else None
        finally:
            api.CloseHandle(handle)
    try:
        return Path(os.readlink(f"/proc/{pid}/exe")).name.lower()
    except OSError:
        return None


def creation_time_of_handle(handle):
    """Creation time (Windows FILETIME as an int) of the process behind an open handle, or None when it cannot be read."""
    api, _, filetime = _win()
    created, other = filetime(), [filetime(), filetime(), filetime()]
    if not api.GetProcessTimes(handle, ctypes.byref(created), *map(ctypes.byref, other)):
        return None
    return (created.high << 32) | created.low


def process_created(pid):
    """When a running pid was created (an int that is the same for the whole life of that process), or None when it cannot be read.

    A pid number alone does not name a process (Windows reuses it); pid and this value together do."""
    if os.name == "nt":
        api = _win()[0]
        handle = api.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            return creation_time_of_handle(handle)
        finally:
            api.CloseHandle(handle)
    try:  # field 22 of /proc/<pid>/stat; the command name (field 2) may contain spaces, so count from the last ")"
        return int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace").rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def process_identity(pid, created):
    """Whether a recorded (pid, creation time) still names a running process: "gone", "same" or "unknown".

    "gone": no such process, or its creation time is readable and differs (the pid now belongs to another program).
    "same": readable and equal.  "unknown": anything else (nothing recorded, unreadable, any error): never act on it."""
    if type(pid) is not int or pid <= 0:
        return "gone"
    try:
        if not thinking_archive._pid_alive(pid):
            return "gone"
        if type(created) is not int:
            return "unknown"
        now = process_created(pid)
    except Exception:  # an unreadable answer is never a reason to forget or to end a process
        return "unknown"
    if now is None:
        return "unknown"
    return "same" if now == created else "gone"


def end_tree_by_pid(pid):
    """End a process tree known only by pid (a run whose Job Object could not be made, and stale pids); True when the command worked.

    Callers that must be sure the process is gone check for it afterwards: a refused command (e.g. "Access denied") is not an exit."""
    if os.name == "nt":
        try:
            done = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        except (OSError, subprocess.SubprocessError):
            return False
        return done.returncode == 0
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        return False
    return True


def open_process_for_end(pid):
    """A process handle that can be queried and waited on (Windows), or None when it cannot be opened.

    Windows never gives a pid to another process while any handle to the process is open: the caller keeps this handle for as long as it
    acts on the pid, so the pid can only mean the process whose creation time was read through the same handle."""
    return _win()[0].OpenProcess(0x100000 | 0x1000, False, pid) or None  # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION


def wait_process_exit(handle, seconds):
    """True when the process behind a handle has exited within the time (Windows)."""
    return _win()[0].WaitForSingleObject(handle, max(0, int(seconds * 1000))) == 0  # WAIT_OBJECT_0


def close_process_handle(handle):
    _win()[0].CloseHandle(handle)


def end_recorded_tree(pid, created, wait_seconds):
    """End the process tree recorded as (pid, creation time) only if it is still that very process; True when it is gone afterwards.

    "Gone" is also the answer for a pid that now belongs to another program (never touched).  False: it could not be ended in time, or its
    identity cannot be read ("unknown": never ended).  On Windows one handle is opened, the creation time is checked through it, and it
    stays open while the tree is ended and awaited, so the end command can only reach the verified process (or nothing)."""
    if os.name == "nt":
        if type(pid) is not int or pid <= 0:
            return True
        try:
            handle = open_process_for_end(pid)
        except Exception:
            return False
        if not handle:
            return process_identity(pid, created) == "gone"  # not openable: gone, or unknown; never an end command
        try:
            if wait_process_exit(handle, 0):
                return True  # already exited (a handle held elsewhere keeps the pid openable): nothing to end
            if type(created) is not int:
                return False
            now = creation_time_of_handle(handle)
            if now is None:
                return False
            if now != created:
                return True  # the pid belongs to another program now
            end_tree_by_pid(pid)  # the handle is still open: this pid is the verified process or nothing
            return wait_process_exit(handle, wait_seconds)
        except Exception:
            return False
        finally:
            close_process_handle(handle)
    state = process_identity(pid, created)
    if state != "same":
        return state == "gone"
    end_tree_by_pid(pid)
    deadline = time.monotonic() + wait_seconds
    while True:
        state = process_identity(pid, created)
        if state != "same":
            return state == "gone"
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


class AiProcess:
    """One headless AI process and everything it starts; ``kill`` ends exactly that tree and confirms the exit.

    On Windows the process is created suspended, put into a kill-on-close Job Object and only then resumed, so it can never start a
    descendant outside the job.  If it cannot be contained or resumed it has run no code: it is terminated and the run is unsafe."""

    def __init__(self, argv, cwd, env, stdout, stderr):
        self.lock = threading.Lock()
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4}  # 0x4 = CREATE_SUSPENDED
                   if os.name == "nt" else {"start_new_session": True})
        self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **options)
        self.pid = self.proc.pid
        try:  # with the pid this names the process for good (Windows reuses pid numbers); None when it cannot be read
            self.created = creation_time_of_handle(int(self.proc._handle)) if os.name == "nt" else process_created(self.pid)
        except Exception:
            self.created = None
        self.job = None
        self.unsafe = False  # True: no reliable control of the process tree, so the process was ended at once and must not be used
        if os.name == "nt":
            try:
                handle = int(self.proc._handle)
                self.job = JobObject.attach(handle)
                contained = self.job is not None and bool(resume_process(handle))
            except (OSError, AttributeError, ValueError, TypeError):
                contained = False
            if not contained:
                self._end_suspended()

    def _end_suspended(self):
        """The process never started running, so there is no tree to find: terminate it (and its job) and wait for the exit."""
        self.unsafe = True
        for attempt, wait in enumerate(SUSPENDED_WAITS):
            try:
                if self.job is not None:
                    self.job.terminate()
                self.proc.kill()
            except OSError:
                pass
            try:
                self.proc.wait(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                pass
        if self.job is not None:
            self.job.close()
            self.job = None

    def poll(self):
        return self.proc.poll()

    def kill(self, wait=15.0):
        """End the whole tree and wait (bounded) until the child has exited; True when it has."""
        with self.lock:
            running = self.proc.poll() is None
            if self.job is not None:
                self.job.terminate()
            elif running and os.name == "nt":
                self.proc.kill()  # no job: the process was never contained, so it is only ever the suspended parent
            if os.name != "nt":
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except OSError:
                    pass
            try:
                self.proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                return False
            return True

    def finish(self):
        """After a normal exit: leave nothing of the tree behind and release the Job Object."""
        with self.lock:
            if self.job is not None:
                self.job.terminate()
                self.job.close()
                self.job = None
            elif os.name != "nt":
                try:
                    os.killpg(self.pid, signal.SIGKILL)
                except OSError:
                    pass


def read_events(path):
    """The few facts needed from a stream-json transcript; unparsable lines are ignored."""
    found = {"init": None, "rate": None, "result": None}
    try:
        text = Path(path).read_bytes().decode("utf-8", "replace")
    except OSError:
        return found
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            found["init"] = event
        elif event.get("type") == "rate_limit_event" and isinstance(event.get("rate_limit_info"), dict):
            found["rate"] = event["rate_limit_info"]
        elif event.get("type") == "result":
            found["result"] = event
    return found


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def unsafe_runner(init, exit_code):
    """Fail closed: a runner that is not exactly the restricted, account-login, tool-limited one is never trusted."""
    if init is None:
        return exit_code == 0
    return (not isinstance(init.get("tools"), list) or sorted(map(str, init["tools"])) != EXPECTED_TOOLS
            or bool(init.get("mcp_servers")) or init.get("apiKeySource") != "none")


def classify(code, timed_out, events, stderr_text, receipt, required, run_dir, timeout_seconds, step):
    """The failure of one finished AI run in the brief's fixed order, or None when its output may be used."""
    result, rate, init = events["result"] or {}, events["rate"] or {}, events["init"]
    if timed_out:
        minutes = max(1, round(timeout_seconds / 60))
        return JobFailure("timeout", f"{STEP_NAMES[step]}超過 {minutes} 分鐘仍未完成，已停止。", True, ["retry"])
    if unsafe_runner(init, code):
        return unsafe_failure()
    note = receipt.get("note") if isinstance(receipt, dict) and isinstance(receipt.get("note"), str) and receipt["note"].strip() else None
    if code != 0 or result.get("is_error"):
        text = (str(result.get("result") or "") + "\n" + stderr_text).casefold()
        status = result.get("api_error_status")
        if status in (401, 403) or any(word in text for word in ("not logged in", "/login", "invalid api key", "authentication", "oauth")):
            return JobFailure("not_logged_in", "Claude 未登入，請先打開 Claude 登入一次。", True, ["retry"])
        if rate.get("status") == "rejected" or status == 429 or any(word in text for word in ("usage limit", "limit reached", "rate limit")):
            resets = rate.get("resetsAt")
            if isinstance(resets, (int, float)) and not isinstance(resets, bool):
                hours = max(1, math.ceil((resets - time.time()) / 3600))
                message = f"已到用量上限，約 {hours} 小時後可再試。"
            else:
                message = "已到用量上限，請稍後再試。"
            return JobFailure("usage_limit", message, True, ["retry_later"])
        return JobFailure("agent_failed", "AI 回報未能完成：" + note if note else f"背景 AI 未能完成這一步（結束碼 {code}）。",
                          True, ["retry", "edit_question"], detail=f"exit={code} subtype={result.get('subtype')}")
    listed = receipt.get("outputs") if isinstance(receipt, dict) else None
    ok = (isinstance(receipt, dict) and receipt.get("status") == "completed"
          and isinstance(listed, list) and all(isinstance(name, str) and name and "/" not in name and "\\" not in name
                                               and (run_dir / name).is_file() for name in listed)
          and all((run_dir / name).is_file() for name in required))
    if not ok:
        return JobFailure("agent_failed", "AI 回報未能完成：" + (note or "沒有收據"), True, ["retry", "edit_question"])
    return None


def runner_env():
    return {key: value for key, value in os.environ.items() if key not in SECRET_ENV}


def claude_command(settings):
    if settings.get("claude_command"):
        return list(settings["claude_command"])
    found = shutil.which("claude") or shutil.which("claude.cmd")
    if not found and (Path.home() / ".local" / "bin" / "claude.exe").is_file():
        found = str(Path.home() / ".local" / "bin" / "claude.exe")
    return [found] if found else None


def codex_command(settings):
    if settings.get("codex_command"):
        return list(settings["codex_command"])
    if os.name == "nt":
        wrapper = shutil.which("codex.cmd") or shutil.which("codex")
        if wrapper:
            matches = sorted(Path(wrapper).parent.glob(
                "node_modules/@openai/codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"))
            if matches:
                return [str(matches[0])]
        found = shutil.which("codex.exe")
    else:
        found = shutil.which("codex")
    return [found] if found else None


def claude_login(done):
    try:
        value = json.loads(done.stdout)
    except (ValueError, TypeError):
        return "none"
    if not isinstance(value, dict) or done.returncode != 0 or value.get("loggedIn") is not True:
        return "none"
    method = value.get("authMethod")
    return ("account" if method == "claude.ai" else "api_key" if method in {"api_key", "api_key_helper"}
            else "other" if method in {"oauth_token", "third_party"} else "none")


def codex_login(done):
    text = (done.stdout + "\n" + done.stderr).casefold()
    # Conflicting output must never promote an API key to an account login.
    return "api_key" if "api key" in text else "account" if done.returncode == 0 and "chatgpt" in text else "none"


CODEX_SANDBOX = ['windows.sandbox="mxc"', 'default_permissions="cradle"',
                 'permissions.cradle.filesystem={":minimal"="read", ":workspace_roots"={"."="write"}}',
                 'permissions.cradle.network.enabled=false']
CODEX_CONFIG = CODEX_SANDBOX + ['approval_policy="never"', 'web_search="disabled"', 'project_doc_max_bytes=0',
                               'history.persistence="none"', 'forced_login_method="chatgpt"']
CODEX_DISABLED = ["apps", "plugins", "hooks", "memories", "multi_agent", "browser_use", "computer_use",
                  "image_generation", "view_image"]
CODEX_ITEMS = {"agent_message", "reasoning", "command_execution", "file_change", "todo_list"}


def claude_run(run):
    flags = ["--restricted", "--strict-mcp-config", "--tools", "Read,Write,Edit,Glob,Grep", "--permission-mode", "acceptEdits",
             "--permission-prompts", "none", "--no-session-persistence", "--output-format", "stream-json", "--verbose"]
    return ["-p", "讀 task.md 並完整照做。"] + flags, flags


def codex_run(run):
    flags = ["exec", "-C", str(run), "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules", "--json"]
    flags += [part for value in CODEX_CONFIG for part in ("-c", value)]
    flags += [part for value in CODEX_DISABLED for part in ("--disable", value)]
    return flags + ["讀 task.md 並完整照做。"], flags


def read_codex_events(path):
    found = {"thread_started": False, "tools": [], "tokens": None, "failure": None}
    try:
        text = Path(path).read_bytes().decode("utf-8", "replace")
    except OSError:
        return found
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if not isinstance(kind, str):
            continue
        if kind == "thread.started":
            found["thread_started"] = True
        elif kind in {"item.started", "item.completed"}:
            item = event.get("item")
            name = item.get("type") if isinstance(item, dict) else None
            if name not in found["tools"]:
                found["tools"].append(name)
        elif kind == "turn.completed":
            found["tokens"] = event.get("usage") if isinstance(event.get("usage"), dict) else None
        elif kind in {"turn.failed", "error"}:
            error = event.get("error", event.get("message", ""))
            found["failure"] = str(error.get("message", "") if isinstance(error, dict) else error)[:300]
    return found


def unsafe_codex(events, code):
    return (code == 0 and not events["thread_started"]
            or any(not isinstance(item, str) or item not in CODEX_ITEMS for item in events["tools"]))


def classify_codex(code, timed_out, events, stderr_text, receipt, required, run_dir, timeout_seconds, step):
    if timed_out:
        return classify(code, True, {"init": None, "result": None, "rate": None}, "", None, required, run_dir, timeout_seconds, step)
    if unsafe_codex(events, code):
        return unsafe_failure()
    if code != 0 or events["failure"] is not None:
        text = (str(events["failure"] or "") + "\n" + stderr_text).casefold()
        if any(word in text for word in ("401", "unauthorized", "not logged in", "login")):
            return JobFailure("not_logged_in", "Codex 未登入，請先打開 Codex 登入一次。", True, ["retry"])
        if any(word in text for word in ("usage limit", "rate limit", "429", "quota")):
            return JobFailure("usage_limit", "已到用量上限，請稍後再試。", True, ["retry_later"])
        code = code or 1
    # Use precisely the same receipt validation and generic failure wording as Claude.
    return classify(code, False, {"init": {"tools": EXPECTED_TOOLS, "mcp_servers": [], "apiKeySource": "none"},
                                 "result": {}, "rate": {}}, "", receipt, required, run_dir, timeout_seconds, step)


RUNNERS = {
    "claude": {"label": "Claude Code", "resolve": claude_command, "login_args": ["auth", "status"], "detect": claude_login,
               "build": claude_run, "read": read_events, "safe": lambda events, code: not unsafe_runner(events["init"], code),
               "classify": classify, "preflight": None, "task_replace": None,
               "missing": "這部電腦找不到 Claude Code。"},
    "codex": {"label": "Codex", "resolve": codex_command, "login_args": ["login", "status"], "detect": codex_login,
              "build": codex_run, "read": read_codex_events, "safe": lambda events, code: not unsafe_codex(events, code),
              "classify": classify_codex, "preflight": lambda store, prefix, **kw: store._preflight(prefix, **kw),
              "task_replace": ("你不能執行程式", "你只可以用指令讀寫這個資料夾內的檔案，不要執行 python 或其他程式"),
              "missing": "這部電腦找不到 Codex。"},
}


class Cradle:
    """Job store, request validation and the single worker thread of one nebula server."""

    def __init__(self, server, reader_stamp=None):
        self.server = server
        self.root, self.vault = Path(server.root), Path(server.vault)
        self.reader_stamp = reader_stamp
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.jobs: dict[str, dict] = {}
        self.sequence = 0
        self.closing = False
        self.thread = None
        self.proc = None
        self.proc_job = None
        self.admitted = set()  # ids of jobs that hold the archive write admission (they can no longer be cancelled)
        self.detection_lock = threading.Lock()  # detection and every sandbox probe share this single-flight gate
        self.runner_state = {"checked_at": None, "runners": []}
        self.detection_started = False
        self.detection_thread = None
        self.detection_epoch = 0
        self.probe_proc = None
        # Construction only reads an existing runtime folder; it never creates one.
        self._scan()

    # ---------------------------------------------------------------- storage
    @property
    def base(self):
        return self.root / ".reader-runtime" / "cradle"

    def _chain(self, *parts):
        """Every folder from .reader-runtime down to the target is checked for links before it is touched."""
        path = self.root / ".reader-runtime"
        plain(path)
        for part in ("cradle", *parts):
            path = path / part
            plain(path)
        return path

    def _job_dir(self, job_id, create=False):
        path = self._chain("jobs", job_id)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_json(self, path, value):
        path = Path(path)
        self._chain(*path.relative_to(self.base).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name("." + path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(20):  # Windows can refuse the rename while a reader has the old file open for an instant
            try:
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def _save_locked(self, job):
        self._write_json(self._job_dir(job["job_id"]) / "job.json", job)

    def _update(self, job, fields, force=False):
        """The only way the worker changes a job; refuses once the job was cancelled or left the running state."""
        with self.lock:
            if not force and (job.get("cancel_requested") or job.get("status") != "running"):
                raise Stopped()
            job.update(fields)
            self._save_locked(job)

    def _scan(self):
        folder = self.base / "jobs"
        try:
            self._chain("jobs")
            if not folder.is_dir():
                return
            names = sorted(path.name for path in folder.iterdir() if JOB_ID.fullmatch(path.name))
        except (ValueError, OSError):
            return
        for name in names:
            try:
                job = read_json(self._job_dir(name) / "job.json")
            except (ValueError, OSError):
                continue  # an unreadable job is skipped, never deleted
            if not isinstance(job, dict) or job.get("job_id") != name or job.get("kind") not in {"think", "gap", "questions"}:
                continue
            self.jobs[name] = job
            self.sequence = max(self.sequence, int(job.get("queue_seq") or 0))
        for job in self.jobs.values():
            if job.get("status") == "running":
                if self._reap_stale(job):
                    job.update(status="queued", pid=None, pid_created=None, step=None, interrupted=True, cancel_requested=False)
                    try:
                        self._save_locked(job)
                    except (ValueError, OSError):
                        pass
                else:  # the old AI is still running (or cannot be shown to be gone): never start a second one beside it
                    job["interrupted"] = True
                    try:
                        self._end(job, "failed", stale_failure().error)  # the recorded pid stays while the process is not "gone"
                    except (ValueError, OSError):
                        pass
            elif job.get("pid"):  # an earlier run's process that was never confirmed gone: check it again now
                if self._reap_stale(job):
                    job.update(pid=None, pid_created=None)
                    self._safe_save(job)
        if any(job.get("status") == "queued" for job in self.jobs.values()):
            self._ensure_worker()

    def _identity(self, job):
        """"gone" / "same" / "unknown" for the process recorded on a job (pid + creation time); never looks at settings or program names."""
        return process_identity(job.get("pid"), job.get("pid_created"))

    def _forget_pid(self, job):
        """Clear a job's recorded pid only when that process is "gone" (callers hold the lock); "same" and "unknown" stay recorded."""
        if job.get("pid") and self._identity(job) == "gone":
            job["pid"] = job["pid_created"] = None

    def _track(self, job, ap):
        """Record a process of ours whose exit could not be confirmed, so every later check and restart still knows it."""
        self._update(job, {"pid": ap.pid, "pid_created": ap.created}, force=True)

    def _reap_stale(self, job):
        """True when the process recorded on the job can no longer be running (it is "gone").

        Only a "same" process (same pid and creation time) is ended, and its exit awaited (bounded); False means it could not be
        ended, or its identity cannot be read ("unknown": never ended), so no new run may start.  Another program is never touched."""
        return end_recorded_tree(job.get("pid"), job.get("pid_created"), STALE_WAIT_SECONDS)

    # --------------------------------------------------------------- settings
    def _ensure_settings(self):
        path = self._chain("settings.json")
        if not path.exists():
            self._write_json(path, DEFAULT_SETTINGS)

    def _settings(self):
        failure = JobFailure("settings_invalid", "搖籃設定檔格式不正確，沒有開始孕育。")
        path = self._chain("settings.json")
        if not path.exists():
            return json.loads(json.dumps(DEFAULT_SETTINGS))
        try:
            value = read_json(path)
        except (ValueError, OSError):
            raise failure from None
        whole = lambda x, low: type(x) is int and x >= low
        timeouts = value.get("timeouts") if isinstance(value, dict) else None
        valid_command = lambda x: x is None or (isinstance(x, list) and bool(x) and all(isinstance(s, str) and bool(s) for s in x))
        if (not isinstance(value, dict) or value.get("schema") != SETTINGS_SCHEMA or "runner" not in value
                or not (value.get("runner") is None or isinstance(value.get("runner"), str) and value["runner"] in RUNNERS)
                or not all(valid_command(value.get(key)) for key in ("claude_command", "codex_command"))
                or not isinstance(timeouts, dict) or not whole(timeouts.get("write"), 1) or not whole(timeouts.get("other"), 1)
                or not whole(value.get("max_review_rounds"), 1) or not whole(value.get("max_mechanical_fixes"), 0)):
            raise failure
        return value

    def _command(self, settings, name=None):
        runner = RUNNERS[name or settings["runner"]]
        command = runner["resolve"](settings)
        if not command:
            raise JobFailure("tool_missing", runner["missing"], True, ["copy"])
        return command

    def _call(self, prefix, args, deadline):
        ap = None
        try:
            with self.lock:
                self._check_stopped(None)
                started = time.monotonic()
                remaining = min(8.0, deadline - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(prefix, 0)
                ap = AiProcess(prefix + args, self.root, runner_env(), subprocess.PIPE, subprocess.PIPE)
                self.probe_proc = ap  # register before close() can take the lock
            timed_out, _, stdout = self._wait_ai(ap, job=None, timeout=remaining, started=started, capture=True)
            self._check_stopped(None)
            if timed_out:
                raise subprocess.TimeoutExpired(prefix, remaining)
            _, stderr = ap.proc.communicate(timeout=1)
            return subprocess.CompletedProcess(prefix + args, ap.poll(), stdout.decode("utf-8", "replace"),
                                               (stderr or b"").decode("utf-8", "replace"))
        finally:
            if ap is not None:
                ap.finish()
                with self.lock:
                    if self.probe_proc is ap:
                        self.probe_proc = None

    def _runner_snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.runner_state))

    def detect(self, name=None):
        """On-demand detection; overlapping requests wait for and reuse the one in flight, including a forced refresh."""
        with self.lock:
            if self.closing:
                return self._runner_snapshot()
            epoch = self.detection_epoch
        if not self.detection_lock.acquire(blocking=False):
            with self.detection_lock:
                with self.lock:
                    if self.detection_epoch != epoch:
                        return self._runner_snapshot()
            return self.detect(name)  # the operation we waited for was only a per-run probe, not a detection
        try:
            if self.closing:
                return self._runner_snapshot()
            deadline = time.monotonic() + 30
            try:
                settings = self._settings()
            except JobFailure:
                settings = DEFAULT_SETTINGS
            results = {row["name"]: row for row in self._runner_snapshot()["runners"]}
            for key in ([name] if name is not None else RUNNERS):
                runner, label = RUNNERS[key], RUNNERS[key]["label"]
                row = {"name": key, "label": label, "installed": False, "version": None, "login": "none",
                       "locked": False, "ready": False, "reason": runner["missing"]}
                prefix = runner["resolve"](settings)
                if prefix:
                    row["installed"] = True
                    try:
                        version = self._call(prefix, ["--version"], deadline)
                        row["version"] = next((line.strip()[:80] for line in version.stdout.splitlines() if line.strip()), None)
                        row["login"] = runner["detect"](self._call(prefix, runner["login_args"], deadline))
                        self._check_stopped(None)
                        row["locked"] = (True if runner["preflight"] is None else
                                         runner["preflight"](self, prefix, timeout=min(8.0, max(0, deadline - time.monotonic()))))
                        row["ready"] = row["login"] == "account" and row["locked"]
                        if row["login"] == "none":
                            row["reason"] = f"{label} 未登入，請先打開 {label} 登入一次。"
                        elif row["login"] == "api_key":
                            row["reason"] = f"{label} 只用 API key 登入，背景孕育只接受帳戶登入。"
                        elif row["login"] == "other":
                            row["reason"] = f"{label} 的登入方式不是個人帳戶，背景孕育暫不接受。"
                        elif not row["locked"]:
                            row["reason"] = ("未在這個系統核實 Codex 的鎖緊，暫不可用。" if os.name != "nt" else
                                             "Codex 的鎖緊測試未通過（讀得到資料夾外的檔案或連得上網絡），暫不可用。")
                        else:
                            row["reason"] = None
                    except (subprocess.TimeoutExpired, TimeoutError):
                        row.update(ready=False, reason=f"{label} 沒有及時回應，請稍後再按「偵測」。")
                    except OSError:
                        row.update(installed=False, ready=False, reason=runner["missing"])
                    except Interrupted:
                        return self._runner_snapshot()
                    except (ValueError, JobFailure):
                        row.update(locked=False, ready=False, reason="Codex 的鎖緊測試未通過（讀得到資料夾外的檔案或連得上網絡），暫不可用。")
                results[key] = row
            with self.lock:
                if self.closing:
                    return self._runner_snapshot()
                self.runner_state = {"checked_at": now_text(), "runners": [results[key] for key in RUNNERS if key in results]}
                self.detection_epoch += 1
            return self._runner_snapshot()
        finally:
            self.detection_lock.release()

    def runners(self, refresh=False):
        if refresh or self._runner_snapshot()["checked_at"] is None:
            self.detect()
        try:
            name = self._settings()["runner"]
        except JobFailure:
            name = None
        return {"runner": name, **self._runner_snapshot()}

    def set_runner(self, body):
        if (not isinstance(body, dict) or set(body) != {"runner"}
                or not (body["runner"] is None or isinstance(body["runner"], str) and body["runner"] in RUNNERS)):
            raise CradleError(400, "AI 設置的要求格式不正確。")
        invalid = CradleError(409, "搖籃設定檔格式不正確，沒有更改。")
        try:
            self._settings()
        except (JobFailure, ValueError, OSError):
            raise invalid from None
        name = body["runner"]
        if name is not None:
            state = self.detect(name)
            row = next((item for item in state["runners"] if item["name"] == name), None)
            if row is None:  # the concurrent detection was for a different single runner
                state = self.detect(name)
                row = next(item for item in state["runners"] if item["name"] == name)
            if not row["ready"]:
                raise CradleError(409, row["reason"])
        with self.lock:
            try:
                value = self._settings()  # re-read after detection; preserve every other field, including unknown fields
            except (JobFailure, ValueError, OSError):
                raise invalid from None
            value["runner"] = name
            self._write_json(self._chain("settings.json"), value)
        while not self.closing and set(RUNNERS) - {row["name"] for row in self._runner_snapshot()["runners"]}:
            self.detect()
        return {"runner": name, **self._runner_snapshot()}

    def _start_detection(self):
        with self.lock:
            if self.detection_started or self.closing:
                return
            self.detection_started = True
            if self.runner_state["checked_at"] is not None:
                return
            self.detection_thread = threading.Thread(target=self.detect, name="cradle-runner-detection", daemon=True)
            self.detection_thread.start()

    def _preflight(self, prefix, timeout=30, job=None):
        """Caller holds detection_lock. Probe folders are checked and made only inside that gate; no transcript is stored."""
        self._check_stopped(job)
        if os.name != "nt":
            return False
        if timeout <= 0:
            raise subprocess.TimeoutExpired(prefix, timeout)
        probe, work = self._chain("probe"), self._chain("probe", "work")
        probe.mkdir(parents=True, exist_ok=True)
        outside = self._chain("probe", "outside.txt")
        outside.write_text("server-owned sandbox probe", encoding="utf-8")
        if work.exists():
            for path in work.rglob("*"):
                self._chain("probe", "work", *path.relative_to(work).parts)
            shutil.rmtree(work)
        work.mkdir()
        script = ("$ErrorActionPreference='Stop'; $read='denied'; $connect='denied'; $write='failed'; "
                  "try { $null=Get-Content -LiteralPath '..\\outside.txt' -Raw; $read='allowed' } catch {}; "
                  "$client=New-Object System.Net.Sockets.TcpClient; "
                  "try { $pending=$client.ConnectAsync('127.0.0.1'," + str(self.server.server_port) + "); "
                  "if ($pending.Wait(3000) -and $client.Connected) { $connect='allowed' } } catch {} finally { $client.Dispose() }; "
                  "try { Set-Content -LiteralPath 'ok.txt' -Value 'ok'; $write='ok' } catch {}; "
                  "Write-Output ('read='+$read); Write-Output ('connect='+$connect); Write-Output ('write='+$write)")
        argv = prefix + ["sandbox"] + [part for value in CODEX_SANDBOX for part in ("-c", value)]
        argv += ["-P", "cradle", "-C", str(work), "--", "powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script]
        ap = None
        try:
            with self.lock:
                self._check_stopped(job)
                ap = AiProcess(argv, work, runner_env(), subprocess.PIPE, subprocess.PIPE)
                if job is None:
                    self.probe_proc = ap
                else:
                    self.proc, self.proc_job = ap, job["job_id"]
            timed_out, _, stdout = self._wait_ai(ap, job, timeout, time.monotonic(), capture=True)
            if timed_out:
                raise subprocess.TimeoutExpired(prefix, timeout)
            self._check_stopped(job)
            lines = stdout.decode("utf-8", "replace").splitlines()
            checks = {key: [line for line in lines if line.startswith(key + "=")] for key in ("read", "connect", "write")}
            ok = self._chain("probe", "work", "ok.txt")
            return (ap.poll() == 0 and checks == {"read": ["read=denied"], "connect": ["connect=denied"], "write": ["write=ok"]}
                    and ok.is_file())
        finally:
            if ap is not None:
                ap.finish()
                with self.lock:
                    if self.proc is ap:
                        self.proc, self.proc_job = None, None
                    if self.probe_proc is ap:
                        self.probe_proc = None
                    if job is not None:
                        self._forget_pid(job)
                        self._safe_save(job)
            ok = self._chain("probe", "work", "ok.txt")
            if ok.is_file():
                ok.unlink()

    def _check_stopped(self, job):
        if job is not None and (job.get("cancel_requested") or job.get("status") != "running"):
            raise Stopped()
        if self.closing:
            raise Interrupted()

    def _wait_ai(self, ap, job, timeout, started, capture=False):
        """Shared containment, tracking, timeout and stop handling for AI runs and zero-token probes."""
        if ap.unsafe:
            if job is not None and ap.poll() is None:
                self._track(job, ap)
            raise unsafe_failure("process tree control unavailable" + ("" if ap.poll() is not None else "; exit not confirmed"))
        with self.lock:
            if job is None:
                self.probe_proc = ap
            else:
                self.proc, self.proc_job = ap, job["job_id"]
            abort = self.closing or job is not None and (job.get("cancel_requested") or job.get("status") != "running")
        if abort:
            if not ap.kill() and job is not None:
                self._track(job, ap)
        elif job is not None:
            try:
                self._update(job, {"pid": ap.pid, "pid_created": ap.created})
            except Stopped:
                if not ap.kill():
                    self._track(job, ap)
        timed_out, unconfirmed, stdout = False, False, b""
        while ap.poll() is None:
            if time.monotonic() >= started + timeout:
                timed_out, unconfirmed = True, not ap.kill()
                break
            try:
                if capture:
                    stdout, _ = ap.proc.communicate(timeout=0.1)
                else:
                    ap.proc.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
        ap.finish()
        if capture and not unconfirmed:
            stdout, _ = ap.proc.communicate(timeout=1)
        return timed_out, unconfirmed, stdout or b""

    # ---------------------------------------------------------------- queries
    def busy(self):
        with self.lock:
            return sum(job["status"] in {"queued", "running"} for job in self.jobs.values())

    def _position(self, job):
        """How many queued or running jobs are ahead of a queued job in the real queue order (the order ``_take`` uses); None when it is not queued."""
        if job["status"] != "queued":
            return None
        key = lambda item: (item.get("queue_seq", 0), item["job_id"])
        return sum(other["status"] == "running" or other["status"] == "queued" and key(other) < key(job) for other in self.jobs.values())

    def _view(self, job, summary=False):
        out = {"job_id": job["job_id"], "kind": job["kind"]}
        if job["kind"] == "questions":
            out["way"] = job.get("way")
        question = job.get("question") or ""
        out.update(status=job["status"], position=self._position(job), step=job.get("step") if job["status"] == "running" else None, round=job.get("round", 0),
                   attempt=job.get("attempt", 1), eta_seconds=self._eta(job),
                   question=question[:80] + "…" if summary and len(question) > 80 else question)
        if not summary:
            out.update(notes=list(job.get("notes") or []), basket=list(job.get("basket") or []))
        out.update(created_at=job.get("created_at"), started_at=job.get("started_at"), ended_at=job.get("ended_at"),
                   error=json.loads(json.dumps(job.get("error"))), result=json.loads(json.dumps(job.get("result"))))
        if not summary:
            out["questions"] = json.loads(json.dumps(job.get("questions")))
        out.update(surprising=job.get("surprising"), picked=job.get("picked"), dismissed=bool(job.get("dismissed")),
                   runner=job.get("runner") or "claude")
        return out

    def _eta(self, job):
        if job["status"] not in {"queued", "running"}:
            return None
        ahead = job.get("next") or "prepare"
        if job["kind"] == "questions":
            chain = {"prepare": ["prepare", "questions"], "questions": ["questions"], "qcheck": ["check"]}.get(ahead, ["questions"])
        else:
            chain = {"prepare": ["prepare", "write", "check", "review", "archive"], "write": ["write", "check", "review", "archive"],
                     "check": ["check", "review", "archive"], "review": ["review", "archive"],
                     "revise": ["revise", "check", "review", "archive"], "archive": ["archive"], "refresh": ["archive"]}.get(ahead, ["archive"])
        total = sum(STEP_SECONDS[name] for name in chain)
        if job["status"] == "running" and job.get("step_started"):
            elapsed = max(0.0, time.time() - job["step_started"])
            total += max(30, STEP_SECONDS[chain[0]] - elapsed) - STEP_SECONDS[chain[0]]
        return int(round(total))

    def status(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return self._view(job) if job else None

    def summaries(self):
        self._start_detection()
        try:
            name = self._settings()["runner"]
        except JobFailure:
            name = None
        with self.lock:
            row = next((item for item in self.runner_state["runners"] if item["name"] == name), None)
            ordered = sorted(self.jobs.values(), key=lambda job: job["job_id"], reverse=True)
            return {"jobs": [self._view(job, True) for job in ordered[:20]],
                    "active": sum(job["status"] in {"queued", "running"} for job in ordered),
                    "awaiting_pick": sum(job["kind"] == "questions" and job["status"] == "done" and not job.get("picked")
                                         and not job.get("dismissed") for job in ordered),
                    "runner": {"name": name, "label": RUNNERS[name]["label"] if name in RUNNERS else None,
                               "ready": False if name is None else row["ready"] if row else None}}

    def draft(self, job_id):
        """The latest written answer rendered as the reader would show it, with the latest review's verdicts; None when there is none."""
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job["kind"] == "questions" or not job.get("answer_path"):
                return None
            folder, answer_path, review_path = self._job_dir(job_id), job["answer_path"], job.get("review_path")
        try:
            packet, answer = read_json(folder / "packet.json"), read_json(folder / answer_path)
            review = read_json(folder / review_path) if review_path and (folder / review_path).is_file() else {}
            rows = [{key: item.get(key) for key in ("id", "verdict", "adjudication", "strongest_objection")}
                    for item in (review.get("items") or []) if isinstance(item, dict)] if isinstance(review, dict) else []
            return {"markdown": ask.render(packet, answer), "review": rows}
        except (ValueError, OSError, KeyError, TypeError, AttributeError):
            return None

    # --------------------------------------------------------------- requests
    def validate_request(self, body):
        """The cleaned job description of a POST body; CradleError(400) when anything is off (the input is never echoed)."""
        try:
            return self._validated(body)
        except (TypeError, KeyError, AttributeError):
            raise CradleError(400, "孕育要求格式不正確，沒有開始孕育。") from None

    def _validated(self, body):
        bad = CradleError(400, "孕育要求格式不正確，沒有開始孕育。")
        allowed = {"kind", "way", "notes", "question", "basket", "encounter", "from_questions"}
        if not isinstance(body, dict) or set(body) - allowed:
            raise bad
        kind, way = body.get("kind"), body.get("way")
        if kind not in {"think", "gap", "questions"} or (kind == "questions") != (way is not None) or (way is not None and way not in {"think", "gap"}):
            raise bad
        notes = body.get("notes")
        if not isinstance(notes, list) or not 1 <= len(notes) <= 6 or not all(isinstance(x, str) for x in notes) or len(set(notes)) != len(notes):
            raise bad
        try:
            for note in notes:
                thinking_archive.checked_note(self.vault, note)
        except (ValueError, OSError):
            raise bad from None
        question = body.get("question", "")
        if not isinstance(question, str):
            raise bad
        question = re.sub(r"[\r\n]+", " ", question).strip()
        if len(question) > 500 or (kind != "questions" and not question):
            raise bad
        basket = body.get("basket", [])
        if not isinstance(basket, list) or len(basket) > 6 or not all(isinstance(x, str) and len(x) <= 160 for x in basket):
            raise bad
        encounter = body.get("encounter")
        if encounter is not None:
            try:
                if not isinstance(encounter, dict) or len(json.dumps(encounter, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > 2048:
                    raise bad
            except (TypeError, ValueError):
                raise bad from None
        source = body.get("from_questions")
        if source is not None and (kind == "questions" or not isinstance(source, str) or not JOB_ID.fullmatch(source)):
            raise bad
        return {"kind": kind, "way": way, "notes": notes, "question": question, "basket": basket, "encounter": encounter, "from_questions": source}

    def create(self, body):
        """Validate, store and queue one job; returns ``{"job_id", "position"}``."""
        data = self.validate_request(body)
        with self.lock:
            if self.closing or self.server.retiring:
                raise CradleError(409, "星雲服務正在更新，請稍後再試。")
            if self.busy() >= MAX_ACTIVE_JOBS:
                raise CradleError(409, "排隊的孕育太多，請等現有的完成。")
            source = None
            if data["from_questions"] is not None:
                source = self.jobs.get(data["from_questions"])
                if (not source or source["kind"] != "questions" or source["status"] != "done" or source.get("dismissed") or source.get("picked")
                        or source.get("way") != data["kind"] or source.get("notes") != data["notes"]
                        or data["question"] not in [item.get("question") for item in source.get("questions") or []]):
                    raise CradleError(400, "孕育要求格式不正確，沒有開始孕育。")
            self._ensure_settings()
            try:
                runner = self._settings()["runner"]
            except JobFailure:
                runner = "claude"  # retain the existing invalid-settings job's retry behaviour after the file is repaired
            else:
                if runner is None:
                    raise CradleError(409, "還未設定背景 AI，請先在 AI 設置揀一個；也可以改用「複製指令」。")
            jobs_dir = self._chain("jobs")
            jobs_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now().strftime("%Y%m%d")
            numbers = [int(name[-4:]) for name in self.jobs if name[3:11] == day]
            numbers += [int(path.name[-4:]) for path in jobs_dir.iterdir() if JOB_ID.fullmatch(path.name) and path.name[3:11] == day]
            job_id = f"CJ-{day}-{max(numbers, default=0) + 1:04d}"
            self.sequence += 1
            job = {"job_id": job_id, "kind": data["kind"], "way": data["way"], "status": "queued", "step": None, "next": "prepare",
                   "round": 0, "rounds_done": 0, "attempt": 1, "question": data["question"], "notes": data["notes"],
                   "basket": data["basket"], "encounter": data["encounter"], "from_questions": data["from_questions"],
                   "created_at": now_text(), "started_at": None, "ended_at": None, "error": None, "result": None,
                   "questions": None, "surprising": None, "picked": None, "dismissed": False, "runner": runner,
                   "queue_seq": self.sequence, "run_no": 0, "fixes": 0, "rerun": False, "pid": None, "pid_created": None, "interrupted": False,
                   "cancel_requested": False, "steps": [], "verdicts": [], "answer_path": None, "review_path": None,
                   "pending_review_path": None, "previous_review_path": None, "target_sha": None}
            position = self.busy()
            self._job_dir(job_id).mkdir(exist_ok=False)
            self._save_locked(job)
            if source is not None:
                source["picked"] = job_id
                self._save_locked(source)
            self.jobs[job_id] = job
            self._ensure_worker()
            self.cond.notify_all()
            return {"job_id": job_id, "position": position}

    def retry(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            error = job.get("error") if job else None
            if not job or not (job["status"] == "cancelled" or (job["status"] == "failed" and isinstance(error, dict) and error.get("retryable"))):
                raise CradleError(409, "這個孕育工作不能重試。")
            if self.closing or self.server.retiring:
                raise CradleError(409, "星雲服務正在更新，請稍後再試。")
            code = error.get("code") if isinstance(error, dict) else None
            fields = {}
            if code in RESTART_CODES:
                fields.update(attempt=job["attempt"] + 1, next="questions" if job["kind"] == "questions" else "write", round=0, rounds_done=0,
                              fixes=0, rerun=False, verdicts=[], answer_path=None, review_path=None, pending_review_path=None,
                              previous_review_path=None, target_sha=None)
            elif code == "archive_failed":
                fields["next"] = "archive"
            elif code == "nebula_not_updated":
                fields["next"] = "refresh"
            self.sequence += 1
            job.update(fields, status="queued", error=None, ended_at=None, started_at=None, step=None, cancel_requested=False,
                       interrupted=False, queue_seq=self.sequence)
            self._save_locked(job)
            self._ensure_worker()
            self.cond.notify_all()
            return self._view(job)

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise CradleError(404, "沒有此孕育工作。")
            if job["status"] == "done" and job["kind"] == "questions":
                job["dismissed"] = True
                self._save_locked(job)
                return self._view(job)
            if job["status"] == "queued":
                self._end(job, "cancelled")  # a pid that is not "gone" stays recorded: an old AI that was never confirmed gone
                return self._view(job)
            if job["status"] != "running":
                raise CradleError(409, "這個孕育工作現在不能取消。")
            if job["job_id"] in self.admitted:  # it holds the write admission and is saving: it finishes first
                raise CradleError(409, "正在存成新星，請稍候，完成後才能停止。")
            job["cancel_requested"] = True  # from here the worker can no longer change this job
            self._save_locked(job)
            proc = self.proc if self.proc_job == job_id else None
        if proc is not None:
            proc.kill()  # confirmed exit before the job is marked
        with self.lock:
            self._end(job, "cancelled")
            return self._view(job)

    # ----------------------------------------------------------------- worker
    def _ensure_worker(self):
        with self.lock:
            if self.closing or (self.thread and self.thread.is_alive()):
                return
            self.thread = threading.Thread(target=self._worker, name="knowledge-cradle", daemon=True)
            self.thread.start()

    def close(self):
        """Stop admitting, end the running AI tree and wait for the worker (an archive in progress finishes first)."""
        with self.lock:
            self.closing = True
            proc = self.proc
            probe = self.probe_proc
            self.cond.notify_all()
        if proc is not None:
            proc.kill()
        if probe is not None:
            probe.kill()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(300)
        if self.detection_thread is not None and self.detection_thread is not threading.current_thread():
            self.detection_thread.join(30)

    def _take(self):
        with self.cond:
            while not self.closing:
                queued = [job for job in self.jobs.values() if job["status"] == "queued"]
                if queued:
                    job = min(queued, key=lambda item: (item.get("queue_seq", 0), item["job_id"]))
                    job.update(status="running", started_at=job.get("started_at") or now_text(), cancel_requested=False)
                    self._save_locked(job)
                    return job
                self.cond.wait(1.0)
        return None

    def _worker(self):
        while True:
            try:
                job = self._take()
            except (ValueError, OSError):
                time.sleep(1.0)
                continue
            if job is None:
                return
            try:
                self._process(job)
            except Stopped:
                pass
            except Interrupted:
                with self.lock:
                    if job["status"] == "running":
                        self._forget_pid(job)  # an AI that could not be confirmed gone keeps its pid for the next start
                        job.update(status="queued", interrupted=True, step=None)
                        self._safe_save(job)
            except Exception as error:  # a defect here must end this one job, not the worker
                try:
                    self._fail(job, JobFailure("internal_error", "孕育過程發生內部錯誤，已停止。", True, ["retry"], f"{type(error).__name__}: {str(error)[:120]}"))
                except Stopped:
                    pass
            finally:
                self.server.last_active = time.monotonic()

    def _safe_save(self, job):
        try:
            self._save_locked(job)
        except (ValueError, OSError):
            pass

    def _end(self, job, status, error=None):
        """Final (or cancelled) state of a job plus its receipt; callers hold the lock."""
        self._forget_pid(job)  # a pid whose process is not "gone" stays recorded: every later retry checks it again
        job.update(status=status, ended_at=now_text(), step=None, cancel_requested=False, error=error)
        self._save_locked(job)
        self._write_receipt(job)

    def _fail(self, job, failure):
        with self.lock:
            if job.get("cancel_requested") or job.get("status") != "running":
                raise Stopped()
            self._end(job, "failed", failure.error)

    def _write_receipt(self, job):
        try:
            minutes = round((datetime.fromisoformat(job["ended_at"]) - datetime.fromisoformat(job.get("started_at") or job["created_at"])).total_seconds() / 60, 1)
        except (TypeError, ValueError):
            minutes = None
        version = next((row["version"] for row in self.runner_state["runners"] if row["name"] == job.get("runner")), None)
        self._write_json(self._job_dir(job["job_id"]) / "receipt.json", {
            "job_id": job["job_id"], "kind": job["kind"], "status": job["status"], "question": job["question"], "notes": job["notes"],
            "runner": job.get("runner"), "runner_version": version,
            "started_at": job.get("started_at"), "ended_at": job.get("ended_at"), "minutes": minutes,
            "steps": list(job.get("steps") or []), "review": {"rounds": len(job.get("verdicts") or []), "verdicts": list(job.get("verdicts") or [])},
            "result": job.get("result"), "error": job.get("error")})

    def _process(self, job):
        try:
            if job.get("pid"):  # a retried job whose old AI could not be ended: check again before anything starts
                if not self._reap_stale(job):
                    raise stale_failure()
                self._update(job, {"pid": None, "pid_created": None}, force=True)  # confirmed gone: forgotten even if the job was cancelled meanwhile
            settings = self._settings()
            if job.get("runner") not in RUNNERS:
                raise JobFailure("runner_not_ready", "還未設定可用的背景 AI，請到 AI 設置揀一個；也可改用「複製指令」。", False, ["copy"])
            while job["next"] != "done":
                if self.closing:
                    raise Interrupted()
                step = job["next"]
                self._update(job, {"step": PUBLIC_STEP[step], "step_started": time.time()})
                try:
                    getattr(self, "_step_" + step)(job, settings)
                finally:
                    with self.lock:
                        self.admitted.discard(job["job_id"])
            with self.lock:
                if job.get("cancel_requested") or job.get("status") != "running":
                    raise Stopped()
                self._end(job, "done")
        except JobFailure as failure:
            self._fail(job, failure)

    # ------------------------------------------------------------------ steps
    def _packet(self, job):
        return read_json(self._job_dir(job["job_id"]) / "packet.json")

    def _common_files(self, job):
        return {"packet.json": self._job_dir(job["job_id"]) / "packet.json", "spec.md": spec_text(),
                "thought_analysis.py": THOUGHT_ANALYSIS, "tension_check.py": inspect.getsource(ask.check_tension_responses)}

    def _step_prepare(self, job, settings):
        packet_path = self._job_dir(job["job_id"]) / "packet.json"
        blocked = JobFailure("prepare_blocked", "這幾份筆記未能完整放入材料包，未能開始孕育。請減少星的數目或改揀其他星。", False, ["edit_question"])
        if not packet_path.is_file():  # fixed once: a resumed job never prepares again
            question = job["question"] if job["kind"] != "questions" else (job["question"] or "為以上筆記擬題")
            try:
                packet = ask.prepare(question, self.vault, notes=job["notes"], selected_only=True, topic_ids=[], expansions=[],
                                     expansion_decision={"mode": "not_applicable_selected_only"})
            except Exception as error:
                blocked.error["detail"] = f"{type(error).__name__}: {str(error)[:120]}"
                raise blocked from None
            if packet.get("status") != "context_ready" or packet.get("deferred_required_paths"):
                raise blocked
            self._write_json(packet_path, packet)
        self._update(job, {"next": "questions" if job["kind"] == "questions" else "write"})

    def _step_write(self, job, settings):
        run = self._run(job, "write", WRITE_TASK, self._common_files(job), "write", ["answer.json"], settings)
        self._update(job, {"answer_path": self._rel(job, run / "answer.json"), "fixes": 0, "next": "check"})

    def _step_check(self, job, settings):
        folder = self._job_dir(job["job_id"])
        packet = self._packet(job)
        try:
            answer = read_json(folder / job["answer_path"])
        except (ValueError, OSError):
            answer = None
        errors, sha = mechanical(packet, answer)
        if not errors:
            self._update(job, {"target_sha": sha, "round": job["rounds_done"] + 1, "rerun": False, "next": "review"})
            return
        if job["fixes"] >= settings["max_mechanical_fixes"]:
            raise JobFailure("mechanical_unfixed", "答案的引文與原文不符，修正兩次仍未通過。", True, ["retry"], "; ".join(errors)[:200])
        (folder / "mechanical_errors.txt").write_text("\n".join(errors), encoding="utf-8")
        files = self._common_files(job)
        files.update({"answer.json": folder / job["answer_path"], "mechanical_errors.txt": folder / "mechanical_errors.txt"})
        run = self._run(job, "fix", FIX_TASK, files, "other", ["answer.json"], settings)
        self._update(job, {"answer_path": self._rel(job, run / "answer.json"), "fixes": job["fixes"] + 1})

    def _step_review(self, job, settings):
        folder = self._job_dir(job["job_id"])
        packet, number = self._packet(job), job["round"]
        files = {"packet.json": folder / "packet.json", "answer.json": folder / job["answer_path"], "target.txt": job["target_sha"] or "",
                 "spec.md": spec_text(), "thought_analysis.py": THOUGHT_ANALYSIS}
        if number > 1:
            files["previous_review.json"] = folder / job["previous_review_path"]
        run = self._run(job, "review", REVIEW_TASK.format(previous=PREVIOUS if number > 1 else ""), files, "other", ["review.json"], settings)
        review_path = self._rel(job, run / "review.json")
        try:
            review = read_json(run / "review.json")
        except (ValueError, OSError):
            review = None
        listed = review.get("items") if isinstance(review, dict) else None
        items = listed if isinstance(listed, list) and all(isinstance(item, dict) for item in listed) else []  # anything else is a format problem
        verdicts = (list(job["verdicts"]) + [[item.get("verdict") for item in items]]) if items else list(job["verdicts"])
        try:
            accepted = ask.review_answer(packet, read_json(folder / job["answer_path"]), review)["status"] == "complete"
        except Exception:  # a malformed AI file is a format problem for the rerun rule, never an internal error
            accepted = False
        if accepted:
            self._update(job, {"review_path": review_path, "verdicts": verdicts, "next": "archive"})
            return
        revise = next((item for item in items if item.get("verdict") == "revise"), None)
        if revise is None:  # a format problem, not an objection: one more review in the same round
            if job["rerun"]:
                raise JobFailure("review_invalid", "覆核結果格式不正確，未能完成覆核。", True, ["retry"])
            self._update(job, {"review_path": review_path, "verdicts": verdicts, "rerun": True})
            return
        if number >= settings["max_review_rounds"]:
            reason = str(revise.get("adjudication") or revise.get("strongest_objection") or "")[:120]
            self._update(job, {"review_path": review_path, "verdicts": verdicts})
            raise JobFailure("review_not_converged", "覆核者仍認為有推論站不住：" + reason, True, ["retry", "edit_question", "draft"])
        self._update(job, {"review_path": review_path, "verdicts": verdicts, "pending_review_path": review_path, "rounds_done": number, "next": "revise"})

    def _step_revise(self, job, settings):
        folder = self._job_dir(job["job_id"])
        files = self._common_files(job)
        files.update({"answer.json": folder / job["answer_path"], "review.json": folder / job["pending_review_path"]})
        run = self._run(job, "revise", REVISE_TASK, files, "write", ["answer.json"], settings)
        self._update(job, {"answer_path": self._rel(job, run / "answer.json"), "fixes": 0, "previous_review_path": job["pending_review_path"],
                           "pending_review_path": None, "next": "check"})

    @staticmethod
    def _ways(job):
        return json.dumps({"way": job["way"], "angles": [{"angle": angle, "prompt": prompt} for angle, prompt in WAYS[job["way"]]]},
                          ensure_ascii=False, indent=1)

    def _step_questions(self, job, settings):
        folder = self._job_dir(job["job_id"])
        direction = bool(job["question"])
        focus = ("每條問題可以針對其中一份或幾份筆記。" if job["way"] == "think"
                 else "五條問題都只針對筆記之間的「關係」（例如互相限制、可以搬用、共同前提），不是單一筆記本身的內容。")
        task = QUESTIONS_TASK.replace("{direction}", "另有 direction.txt：使用者想問的方向；五條問題都要圍繞這個方向。" if direction else "").replace("{focus}", focus)
        files = {"packet.json": folder / "packet.json", "ways.json": self._ways(job)}
        if direction:
            files["direction.txt"] = job["question"]
        run = self._run(job, "questions", task, files, "other", ["questions.json"], settings)
        self._update(job, {"answer_path": self._rel(job, run / "questions.json"), "fixes": 0, "next": "qcheck"})

    def _step_qcheck(self, job, settings):
        folder = self._job_dir(job["job_id"])
        packet = self._packet(job)
        try:
            value = read_json(folder / job["answer_path"])
        except (ValueError, OSError):
            value = None
        errors = check_questions(packet, job["way"], value)
        if not errors:
            self._update(job, {"questions": [{key: item[key] for key in ("angle", "question", "why", "notes")} for item in value["questions"]],
                               "surprising": value["surprising"], "next": "done"})
            return
        if job["fixes"] >= settings["max_mechanical_fixes"]:
            raise JobFailure("questions_invalid", "AI 擬的問題格式不正確，未能列出問題。", True, ["retry"], "; ".join(errors)[:200])
        (folder / "format_errors.txt").write_text("\n".join(errors), encoding="utf-8")
        files = {"packet.json": folder / "packet.json", "ways.json": self._ways(job),
                 "questions.json": folder / job["answer_path"], "format_errors.txt": folder / "format_errors.txt"}
        run = self._run(job, "qfix", QUESTIONS_FIX_TASK, files, "other", ["questions.json"], settings)
        self._update(job, {"answer_path": self._rel(job, run / "questions.json"), "fixes": job["fixes"] + 1})

    def _step_archive(self, job, settings):
        folder = self._job_dir(job["job_id"])
        packet = self._packet(job)
        answer, review = read_json(folder / job["answer_path"]), read_json(folder / job["review_path"])
        self._admit(job)
        try:
            try:
                receipt = thinking_archive.archive_answer(self.vault, packet, answer, review)
            except Exception as error:
                reason = f"{type(error).__name__}: {str(error)}"[:100]
                raise JobFailure("archive_failed", f"答案已通過覆核，但存檔失敗：{reason}。草稿已保留。", True, ["retry"], reason) from None
            nebula = receipt.get("nebula") or {}
            key = packet["session_id"]
            result = {"record": f"thinking/{key}.md", "star_id": key, "href": f"thinking/{key}.html", "saved": True,
                      "in_nebula": nebula.get("status") == "updated"}
            if result["in_nebula"] and self.reader_stamp is not None:
                try:  # the archive already refreshed the readers and the nebula: the watcher must not rebuild the same input again
                    self.server.applied_reader_input = self.reader_stamp(self.root)
                except (ValueError, OSError, KeyError, TypeError):
                    pass
            self._update(job, {"result": result, "next": "done" if result["in_nebula"] else "refresh"})
        finally:
            self.server.end_write()
        if not result["in_nebula"]:
            raise self._not_in_nebula(nebula)  # the record is saved; a retry only refreshes the nebula

    def _not_in_nebula(self, nebula):
        status = nebula.get("status")
        reason = nebula.get("reason") if status == "paused" else nebula.get("error") if status == "refresh_failed" else "星雲尚未安裝"
        return JobFailure("nebula_not_updated", f"思考紀錄已存好，但星雲暫時未能顯示新星：{reason or '原因不明'}。", True, ["refresh"], status)

    def _step_refresh(self, job, settings):
        self._admit(job)
        try:
            try:
                self.server.refresh_projections()
            except Exception as error:
                guard = getattr(error, "guard", None)  # ProjectionPaused: the release gate has not registered the code on disk
                if isinstance(guard, dict):
                    raise self._not_in_nebula({"status": "paused", "reason": guard.get("reason") or guard.get("notice")}) from None
                raise self._not_in_nebula({"status": "refresh_failed", "error": f"{type(error).__name__}: {str(error)}"[:100]}) from None
            result = dict(job["result"] or {})
            result["in_nebula"] = True
            self._update(job, {"result": result, "next": "done"})
        finally:
            self.server.end_write()

    def _admit(self, job):
        """Wait for the same write admission the refresh job uses; a retiring service leaves the job resumable."""
        while True:
            with self.lock:  # cancel and admission decide under this one lock: a job is either cancelled or admitted, never both
                if job.get("cancel_requested") or job.get("status") != "running":
                    raise Stopped()
                if self.closing or self.server.retiring:
                    raise Interrupted()
                if self.server.begin_write():
                    self.admitted.add(job["job_id"])
                    return
            time.sleep(0.2)

    # ------------------------------------------------------------- one AI run
    def _rel(self, job, path):
        return Path(path).relative_to(self._job_dir(job["job_id"])).as_posix()

    def _run(self, job, step, task, files, timeout_key, required, settings):
        """Run the restricted AI once in a fresh folder; returns that folder or raises the classified failure."""
        runner = RUNNERS[job["runner"]]
        prefix = self._command(settings, job["runner"])
        if runner["preflight"] is not None:
            with self.detection_lock:
                self._check_stopped(job)
                try:
                    login = runner["detect"](self._call(prefix, runner["login_args"], time.monotonic() + 8))
                except OSError:
                    raise JobFailure("tool_missing", runner["missing"], True, ["copy"]) from None
                except subprocess.TimeoutExpired:
                    raise JobFailure("not_logged_in", "Codex 未登入，請先打開 Codex 登入一次。", True, ["retry"]) from None
                if login == "api_key":
                    raise unsafe_failure()
                if login != "account":
                    raise JobFailure("not_logged_in", "Codex 未登入，請先打開 Codex 登入一次。", True, ["retry"])
                try:
                    locked = runner["preflight"](self, prefix, job=job)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    locked = False
                if not locked:
                    raise unsafe_failure()
                self._check_stopped(job)
        jobdir = self._job_dir(job["job_id"])
        runs = jobdir / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        number = job["run_no"] + 1
        while any(runs.glob(f"{number:02d}-*")):
            number += 1
        label = f"{number:02d}-{step}"
        self._update(job, {"run_no": number})
        run = runs / label
        run.mkdir()
        for name, source in files.items():
            if isinstance(source, Path):
                shutil.copyfile(source, run / name)
            else:
                (run / name).write_text(source, encoding="utf-8")
        if runner["task_replace"] is not None:
            task = task.replace(*runner["task_replace"])
        (run / "task.md").write_text(task, encoding="utf-8")
        arguments, flags = runner["build"](run)
        argv = prefix + arguments
        env = runner_env()
        events, stderr_file = runs / f"{label}.events.jsonl", runs / f"{label}.stderr.txt"
        timeout = settings["timeouts"][timeout_key]
        entry = {"run": label, "step": step, "round": job.get("round", 0), "attempt": job.get("attempt", 1), "started_at": now_text(),
                 "minutes": None, "exit": None, "timed_out": False, "argv0": prefix[0], "flags": flags, "tools": None, "mcp_servers": None,
                 "apiKeySource": None, "model": None, "cost_usd": None, "num_turns": None, "denials": None, "rate_limit_status": None,
                 "resets_at": None, "agent_receipt": None, "note": None, "error_code": None, "tokens": None}
        started = time.monotonic()
        ap = None
        try:
            try:
                with events.open("wb") as out, stderr_file.open("wb") as err:
                    ap = AiProcess(argv, run, env, out, err)
            except OSError as error:
                raise JobFailure("tool_missing", runner["missing"], True, ["copy"], f"{type(error).__name__}"[:60]) from None
            timed_out, unconfirmed, _ = self._wait_ai(ap, job, timeout, started)
            entry.update(exit=ap.poll(), timed_out=timed_out, minutes=round((time.monotonic() - started) / 60, 2))
            if job.get("cancel_requested") or job.get("status") != "running":
                entry["error_code"] = "cancelled"
                raise Stopped()
            if self.closing:
                entry["error_code"] = "interrupted"
                raise Interrupted()
            found = runner["read"](events)
            init, result, rate = found.get("init") or {}, found.get("result") or {}, found.get("rate") or {}
            receipt = None
            if runner["safe"](found, entry["exit"]):  # the safety gate comes first: an unsafe run's files are never opened
                try:
                    receipt = read_json(run / "receipt.json")
                except (ValueError, OSError):
                    receipt = None
            try:
                stderr_text = stderr_file.read_bytes()[:65536].decode("utf-8", "replace")
            except OSError:
                stderr_text = ""
            entry.update(tools=found.get("tools", init.get("tools")), tokens=found.get("tokens"),
                         mcp_servers=init.get("mcp_servers"), apiKeySource=init.get("apiKeySource"),
                         model=init.get("model"), cost_usd=result.get("total_cost_usd"), num_turns=result.get("num_turns"),
                         denials=len(result.get("permission_denials") or []), rate_limit_status=rate.get("status"),
                         resets_at=rate.get("resetsAt"), agent_receipt=receipt.get("status") if isinstance(receipt, dict) else None,
                         note=str(receipt.get("note"))[:200] if isinstance(receipt, dict) and receipt.get("note") is not None else None)
            failure = runner["classify"](entry["exit"], timed_out, found, stderr_text, receipt, required, run, timeout, step)
            if failure is not None:
                entry["error_code"] = failure.error["code"]
                if unconfirmed:
                    failure.error["detail"] = "process exit not confirmed"
                raise failure
            return run
        except JobFailure as failure:
            entry["error_code"] = entry["error_code"] or failure.error["code"]
            raise
        finally:
            if ap is not None:
                ap.finish()
            with self.lock:
                if self.proc is ap:
                    self.proc, self.proc_job = None, None
            entry["minutes"] = entry["minutes"] if entry["minutes"] is not None else round((time.monotonic() - started) / 60, 2)
            self._log(job, entry, ap)

    def _log(self, job, entry, ap=None):
        """One line per AI run, plus the step summary the job receipt lists.  Never the environment, token or note contents."""
        try:
            with self.lock:
                path = self._job_dir(job["job_id"]) / "log.jsonl"
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                job["steps"] = list(job.get("steps") or []) + [{"step": entry["step"], "run": entry["run"], "exit": entry["exit"],
                                                               "agent_receipt": entry["agent_receipt"],
                                                               "minutes": entry["minutes"], "cost_usd": entry["cost_usd"],
                                                               "tokens": entry["tokens"]}]
                if ap is not None and job.get("pid") == ap.pid:
                    if ap.poll() is not None:  # the run's own process: forgotten only once its exit is confirmed
                        job["pid"] = job["pid_created"] = None
                else:
                    self._forget_pid(job)
                self._save_locked(job)
        except (ValueError, OSError):
            pass
