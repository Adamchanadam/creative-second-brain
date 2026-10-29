"""Deterministic stand-in for the restricted headless Claude Code runner.  It never contacts a model.

The cradle tests point ``claude_command`` at this script.  It ignores the command-line flags (it only records them), works out
which prompt it was given from ``task.md`` and writes realistic outputs built from ``packet.json``.  Behaviour comes from the
environment: ``CRADLE_FAKE_SCENARIO`` and ``CRADLE_FAKE_TRACE`` (a folder outside the run folder that receives one JSON line
per invocation).  Scenarios: success, revise-then-accept, never-accept, bad-quote-then-fixed, unfixable-quote, receipt-failed,
no-receipt, hang, orphan (leaves a sleeping grandchild behind, then finishes normally), spawn-first (its very first action is a start line and a sleeping grandchild, then it finishes normally), slow, not-logged-in, usage-limit, usage-limit-no-reset, unsafe-tools, unsafe-key, no-init, review-bad-format,
review-bad-once, questions-bad, questions-bad-then-fixed.  Anything else behaves like success.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

cwd = Path.cwd()
scenario = os.environ.get("CRADLE_FAKE_SCENARIO", "success")
trace_dir = os.environ.get("CRADLE_FAKE_TRACE")


def step_for(task):
    for text, name in (("每種方式各擬一條問題", "questions"), ("只修正 format_errors.txt", "qfix"),
                       ("你是獨立覆核者", "review"), ("只修正 mechanical_errors.txt", "fix"), ("修訂 answer.json", "revise")):
        if text in task:
            return name
    return "write"


def trace(**extra):
    if not trace_dir:
        return
    folder = Path(trace_dir)
    folder.mkdir(parents=True, exist_ok=True)
    line = {"step": step, "scenario": scenario, "argv": sys.argv[1:], "cwd": str(cwd), "files": sorted(p.name for p in cwd.iterdir()),
            "api_key": "ANTHROPIC_API_KEY" in os.environ, "pid": os.getpid(), "time": time.time(), **extra}
    with (folder / "trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")


def earlier(name):
    """How many invocations of this step were traced before this one."""
    path = Path(trace_dir or ".") / "trace.jsonl"
    if not trace_dir or not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if json.loads(line).get("step") == name)




def verbatim(text):
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return lines[-1]


def build_answer(packet):
    first, second = packet["evidence"][0], packet["evidence"][1]
    q1, q2 = verbatim(first["text"]), verbatim(second["text"])
    wrap = "「" + q1 + "」" if not ({"「", "」"} & set(q1)) else q1
    insight = {
        "id": "I1", "title": "合成推論", "kind": "hypothesis",
        "claim": "兩份材料可能共同提示需要檢查替代條件。",
        "baseline": "兩份材料各自描述一項觀察。",
        "delta": "把兩項觀察並讀，才可提出需要驗證的條件。",
        "premises": [{"id": first["id"], "quote": q1, "contribution": "第一份材料提供一項觀察：" + wrap + "。"},
                     {"id": second["id"], "quote": q2, "contribution": "第二份材料提供另一項觀察。"}],
        "derivation": ["兩份材料描述不同觀察。", "若它們涉及同一情境，應檢查兩者的關係；這是推論，不是來源原話。"],
        "mechanism": "關係可能由共同條件造成，尚待驗證。",
        "assumptions": "兩份材料可比較，沒有遺漏決定因素。",
        "alternative": "兩項觀察也可能無關。",
        "boundary": "資料只足以提出假說，不足以確認因果。",
        "discriminator": {"observe": "比較同一情境的兩項觀察與替代因素。", "supports": "若關係在控制條件後仍存在，假說較值得保留。",
                          "undermines": "若關係消失，放棄此假說。"},
        "implication": {"before": "分別閱讀兩份材料。", "after": "同時檢查兩份材料的共同條件。"},
        "adjacent": {"question": "哪些條件值得先驗證？", "from_frame": "分別觀察", "to_frame": "共同條件",
                     "bridge": "兩項觀察可形成可反駁問題。", "return_value": "讓下一步驗證更具體。"}}
    answer = {"session_id": packet["session_id"],
              "analysis": {"schema": "thinking_v2", "thesis": "合成測試答案：以兩份材料檢查一個有條件的推論。", "insights": [insight]},
              "tension_responses": [{"relation_id": t["relation_id"], "response": "合成回應：兩邊來源都已讀過，結論只作假說。"}
                                    for t in packet.get("established_tensions") or []]}
    return answer


def write_json(name, value):
    (cwd / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def write_outputs(step, packet, scenario="success", previous_count=0):
    """Shared deterministic step artefacts for both fake CLIs."""
    if step in {"write", "fix", "revise"}:
        answer = build_answer(packet)
        if step == "revise":
            answer["analysis"]["thesis"] += "（已按覆核意見修訂）"
        bad = scenario == "unfixable-quote" or (scenario == "bad-quote-then-fixed" and step == "write")
        if bad:
            answer["analysis"]["thesis"] += "「不存在於任何證據的引文」"
        write_json("answer.json", answer)
        outputs = ["answer.json"]
    elif step == "review":
        answer = json.loads((cwd / "answer.json").read_text(encoding="utf-8"))
        target = (cwd / "target.txt").read_text(encoding="utf-8")
        first_round = not (cwd / "previous_review.json").exists()
        verdict = "revise" if scenario == "never-accept" or (scenario == "revise-then-accept" and first_round) else "accept"
        items = [{"id": item["id"], "increment_check": "合成覆核：增量成立。", "grounding_check": "引文來自材料。",
                  "strongest_objection": "資料有限，只是假說。", "adjudication": "已標明限制" if verdict == "accept" else "推論跳過了替代條件，要補寫。",
                  "verdict": verdict} for item in answer["analysis"]["insights"]]
        value = {"target_sha256": target, "reviewer": "fake independent reviewer", "method": "independent", "items": items}
        if scenario == "review-bad-format" or (scenario == "review-bad-once" and previous_count == 0):
            value = {"target_sha256": "wrong", "reviewer": "fake independent reviewer", "method": "independent", "items": items}
        write_json("review.json", value)
        outputs = ["review.json"]
    else:
        ways = json.loads((cwd / "ways.json").read_text(encoding="utf-8"))
        value = {"session_id": packet["session_id"], "surprising": 2,
                 "questions": [{"angle": way["angle"], "question": f"{way['angle']}：這些筆記可以怎樣形成一條可驗證的問題（{number + 1}）？",
                                "why": "合成測試問題。", "notes": [packet["evidence"][0]["id"]]} for number, way in enumerate(ways["angles"])]}
        if scenario == "questions-bad" or (scenario == "questions-bad-then-fixed" and step == "questions"):
            value["questions"] = value["questions"][:4]
        write_json("questions.json", value)
        outputs = ["questions.json"]

    if scenario != "no-receipt":
        failed = scenario == "receipt-failed"
        write_json("receipt.json", {"status": "failed" if failed else "completed", "outputs": [] if failed else outputs,
                                    "note": "合成失敗原因" if failed else "fake completed"})


def main():
    global step
    if "--version" in sys.argv[1:]:
        print("Claude Code 2.0-fake")
        return
    if sys.argv[1:] == ["auth", "status"]:
        method = os.environ.get("CRADLE_FAKE_AUTH", "claude.ai")
        print(json.dumps({"loggedIn": method != "none", "authMethod": method,
                          "email": "cradle-dummy@example.invalid"}))
        sys.exit(1 if method == "none" else 0)
    task = (cwd / "task.md").read_text(encoding="utf-8")

    step = step_for(task)

    if scenario == "spawn-first":  # the very first action: say so, then start a descendant (a run that is not contained before it executes leaks it)
        trace(event="start")
        first_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        trace(grandchild=first_child.pid)

    previous_count = earlier(step)


    if scenario in {"hang", "orphan"}:
        time.sleep(float(os.environ.get("CRADLE_FAKE_SPAWN_DELAY", "0")))  # lets a test end this process before it has started anything
        child =subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        trace(grandchild=child.pid)
        if scenario == "hang":
            time.sleep(120)
    trace()
    if scenario == "slow":
        time.sleep(float(os.environ.get("CRADLE_FAKE_DELAY", "3")))


    packet = json.loads((cwd / "packet.json").read_text(encoding="utf-8"))
    write_outputs(step, packet, scenario, previous_count)
    tools = ["Edit", "Glob", "Grep", "Read", "Write"] + (["Bash"] if scenario == "unsafe-tools" else [])
    if scenario != "no-init":
        print(json.dumps({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": [],
                          "apiKeySource": "ANTHROPIC_API_KEY" if scenario == "unsafe-key" else "none", "model": "fake-model"}), flush=True)
    rejected = scenario in {"usage-limit", "usage-limit-no-reset"}
    info = {"status": "rejected" if rejected else "allowed"}
    if scenario != "usage-limit-no-reset":
        info["resetsAt"] = int(time.time() + 2.2 * 3600)
    print(json.dumps({"type": "rate_limit_event", "rate_limit_info": info}), flush=True)
    logged_out = scenario == "not-logged-in"
    error = logged_out or rejected
    print(json.dumps({"type": "result", "subtype": "error_during_execution" if error else "success", "is_error": error,
                      "total_cost_usd": 0.0123, "num_turns": 3, "permission_denials": [],
                      "api_error_status": 401 if logged_out else 429 if rejected else None,
                      "result": "Not logged in · Please run /login" if logged_out else "usage limit reached" if rejected else "done"}), flush=True)
    sys.exit(1 if error else 0)


if __name__ == "__main__":
    main()
