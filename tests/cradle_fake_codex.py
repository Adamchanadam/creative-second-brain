"""Zero-model Codex CLI double. Step artefacts are shared with cradle_fake_claude."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import cradle_fake_claude as shared

USAGE = {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0,
         "output_tokens": 25, "reasoning_output_tokens": 5}
SECRET_ENV = {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "CODEX_API_KEY"}


def main():
    args = sys.argv[1:]
    scenario = os.environ.get("CRADLE_FAKE_CODEX", "success")
    command = args[0]
    cwd = Path(args[args.index("-C") + 1]) if "-C" in args else Path.cwd()

    def trace(**extra):
        folder = os.environ.get("CRADLE_FAKE_TRACE")
        if folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
            row = {"runner": "codex", "step": command, "scenario": scenario, "argv": args, "cwd": str(cwd),
                   "secret_env": sorted(SECRET_ENV.intersection(os.environ)), "pid": os.getpid(), "time": time.time(), **extra}
            with (Path(folder) / "codex-trace.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    trace()
    if command == "--version":
        if scenario == "version-hang":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            trace(grandchild=child.pid)
            time.sleep(120)
        if scenario == "version-slow":
            time.sleep(float(os.environ.get("CRADLE_FAKE_CODEX_DELAY", "1")))
        print("codex-cli 0.163.0-fake")
        return
    if args == ["login", "status"]:
        print("Not logged in" if scenario == "login-none" else
              "Logged in using an API key" if scenario == "login-apikey" else "Logged in using ChatGPT")
        sys.exit(1 if scenario == "login-none" else 0)
    if command == "sandbox":
        if scenario == "no-backend":
            print("Restricted read-only access requires the elevated Windows sandbox backend", file=sys.stderr)
            sys.exit(1)
        if scenario == "probe-hang":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            trace(grandchild=child.pid)
            time.sleep(120)
        (cwd / "ok.txt").write_text("ok", encoding="utf-8")
        print("sandbox warning: fake backend")
        print("read=allowed\nconnect=allowed\nwrite=ok" if scenario == "leaky" else "read=denied\nconnect=denied\nwrite=ok")
        return
    if command != "exec":
        sys.exit(2)
    task = (cwd / "task.md").read_text(encoding="utf-8")
    if scenario == "exec-hang":
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        trace(grandchild=child.pid)
        time.sleep(120)
    shared.cwd = cwd
    shared.write_outputs(shared.step_for(task), json.loads((cwd / "packet.json").read_text(encoding="utf-8")))

    def event(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)

    if scenario != "exec-no-thread":
        event({"type": "thread.started", "thread_id": "fake-thread"})
    event({"type": "turn.started"})
    item = {"id": "item_0", "type": "command_execution", "command": "Get-Content -LiteralPath task.md",
            "aggregated_output": "", "exit_code": None, "status": "in_progress"}
    event({"type": "item.started", "item": item})
    event({"type": "item.completed", "item": {**item, "exit_code": 0, "status": "completed"}})
    if scenario == "exec-mcp":
        event({"type": "item.completed", "item": {"id": "item_1", "type": "mcp_tool_call", "server": "unsafe"}})
    event({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "fake completed"}})
    if scenario in {"exec-401", "exec-limit"}:
        event({"type": "turn.failed", "error": {"message": "401 unauthorized" if scenario == "exec-401" else "usage limit"}})
        sys.exit(1)
    event({"type": "turn.completed", "usage": USAGE})


if __name__ == "__main__":
    main()
