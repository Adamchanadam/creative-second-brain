"""Synthetic thinking-answer fixture for the 星之搖籃 tests (ported from the OPS reasoning counterexamples). Synthetic sources, never real findings."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT / "tools"))
import ask_second_brain as ask
import thought_analysis as thought


def fixture(root, vault_rel="vault"):
    """A packet, an accepted answer and its review over two synthetic source notes inside root/<vault_rel>.

    vault_rel may name a subfolder (for example "notes/vault"); the caller writes the same value to brain.json."""
    vault = root / vault_rel
    (vault / "sources").mkdir(parents=True)
    bodies = ["# 合成園圃紀錄\n## 觀察\n自動灌溉後，巡園次數由每日一次改為每周一次。",
              "# 合成病蟲紀錄\n## 觀察\n最早的病蟲跡象通常在人工巡園時發現。"]
    evidence = []
    for n, body in enumerate(bodies, 1):
        path = f"sources/SRC-{n}.md"
        (vault / path).write_text(body, encoding="utf-8")
        evidence.append({"id":f"L{n}", "path":path, "text":body, "sha256":ask.digest(body)})
    packet = {"schema_version":ask.SCHEMA, "session_id":"ASK-" + uuid4().hex, "question":"自動灌溉後，園圃管理要重新想甚麼？（合成示例）", "vault":str(vault), "evidence":evidence}
    insight = {"id":"I1", "title":"省下的巡園也曾是觀察入口", "kind":"hypothesis",
       "claim":"自動灌溉可能連帶降低病蟲的早期可見度，效果取決於巡園是否兼有觀察功能。",
       "baseline":"材料分別描述巡園減少及病蟲發現的時機。",
       "delta":"要檢查灌溉自動化有沒有同時移除偵測渠道，不能只比較灌溉耗時。",
       "premises":[{"id":"L1","quote":"巡園次數由每日一次改為每周一次","contribution":"灌溉改變了接觸植物的頻率。"},
                   {"id":"L2","quote":"病蟲跡象通常在人工巡園時發現","contribution":"同一活動兼有偵測功能，減少巡園可能改變另一種結果。"}],
       "derivation":["灌溉與觀察可能共用一次到場，原本的工序包含兩種功能。", "若只把灌溉替代而沒有補回觀察，病蟲被注意到的時間可能延後；兩份記錄尚未證明延後已發生。"],
       "mechanism":"因素是接觸頻率、訊號可見度及補充檢查。灌溉耗時減少，不代表觀察需求消失；假如沒有其他渠道，較疏的接觸可能延長發現時間。",
       "assumptions":"兩份記錄適用於同一園圃；沒有其他人、感測器或例行巡查補回觀察。",
       "alternative":"灌溉更穩定可能減少植株壓力，令病蟲事件本來就減少；另一位園丁也可能已承接巡查。",
       "boundary":"若巡園原先不負責觀察，或已有同等的監測，此推理不能用來反對自動灌溉。",
       "discriminator":{"observe":"在不減少既有安全巡查的前提下，比對灌溉前後的到場、病蟲首次可見及首次記錄日期，並記錄其他照顧者。", "supports":"若可见跡象至發現的延遲增加且没有替代觀察，應為省下的工序補一個觀察入口。", "undermines":"若發現時間不變或更早，且觀察已有承接，保留自動化；改看病蟲發生率等其他因素。"},
       "implication":{"before":"是否能省去澆水時間？", "after":"澆水兼帶的觀察由誰、在何時承接？"},
       "adjacent":{"question":"哪些看似低效的活動，其實兼任另一種感知功能？", "from_frame":"單一步驟的效率", "to_frame":"整個系統的可見度", "bridge":"巡園同時提供動作與訊號，拆開兩種功能才能看清替代條件。", "return_value":"回到園圃，能把灌溉自動化與觀察安排分開設計，而非一概保留或取消巡園。"}}
    answer = {"session_id":packet["session_id"], "analysis":{"schema":thought.SCHEMA,"thesis":"合成示例用來展示：省下一項工作時，先找出它同時帶來的訊號。這不是園藝研究結論。", "insights":[insight]}}
    review = {"target_sha256":thought.review_target(packet, answer), "reviewer":"synthetic mechanical fixture, not an actual semantic reviewer", "method":"author_second_pass", "items":[{"id":"I1", "increment_check":"合成測試資料：連接灌溉頻率與觀察功能。", "grounding_check":"引用匹配合成來源，因果只標假說。", "strongest_objection":"可能有其他觀察渠道。", "adjudication":"測試把此反例納入界線及觀察條件。", "verdict":"accept"}]}
    return packet, answer, review
