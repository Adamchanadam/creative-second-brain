"""The reasoning validator refuses a new answer whose value opens with a fixed label wording.

The reading template already writes 材料原已說明／新增判斷／原先／現在 in front of baseline, delta and implication.
A new answer that opens one of those fields with the same label would show it twice, so only new answers are refused;
saved records are never re-judged.  Synthetic cases only."""
from __future__ import annotations

import copy
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cradle_fixture as fixtures  # noqa: E402
import knowledge_cradle as cradle  # noqa: E402
import thought_analysis as thought  # noqa: E402

FIELDS = (("baseline",), ("delta",), ("implication", "before"), ("implication", "after"))


def put(insight: dict, path: tuple, value: str) -> None:
    target = insight
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value


class LabelPrefixTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
        scratch.mkdir(parents=True, exist_ok=True)
        self.dir = Path(tempfile.mkdtemp(dir=scratch))
        self.addCleanup(shutil.rmtree, self.dir, True)
        packet, answer, _ = fixtures.fixture(self.dir)
        self.analysis = answer["analysis"]
        self.lookup = {e["id"]: e for e in packet["evidence"]}

    def check(self, path, value, new_answer=True):
        analysis = copy.deepcopy(self.analysis)
        put(analysis["insights"][0], path, value)
        return thought.validate_analysis(analysis, self.lookup, new_answer=new_answer)

    def test_the_plain_fixture_passes(self):
        thought.validate_analysis(copy.deepcopy(self.analysis), self.lookup, new_answer=True)

    def test_fixed_label_wordings_are_refused_in_a_new_answer(self):
        labels = ("書摘已經說明：", "原文已經說明：", "材料原已說明：", "原已說明：", "已說明：", "新增判斷：", "新增推導：", "新暴露的問題：", "原文已說：",
                  "書摘已指出：", "文章已提到：", "已說明:", "已說明︰", "已說明﹕", "已說明∶", "書摘已經說明 ：", "  新增判斷：", "　新增判斷：",
                  "**材料原已說明**：", "**材料原已說明：**", "【新增判斷】：", "【新增判斷：】")
        for path in (("baseline",), ("delta",), ("implication", "before"), ("implication", "after")):
            for label in labels:
                with self.subTest(field=".".join(path), label=label):
                    with self.assertRaises(ValueError) as caught:
                        self.check(path, label + "巡園兼有觀察功能，兩份材料各自沒有說破。")
                    self.assertIn("I1." + ".".join(path), str(caught.exception))
                    self.assertIn("不要加標籤前綴", str(caught.exception))

    def test_implication_also_knows_the_two_template_labels(self):
        for path in (("implication", "before"), ("implication", "after")):
            for label in ("原先：", "現在：", "**原先**：", "現在 :"):
                with self.subTest(field=".".join(path), label=label):
                    with self.assertRaises(ValueError) as caught:
                        self.check(path, label + "只看分數。")
                    self.assertIn("I1.implication." + path[1], str(caught.exception))
        for path in (("baseline",), ("delta",)):
            for label in ("原先：", "現在：", "現在:"):
                with self.subTest(field=".".join(path), label=label):
                    self.check(path, label + "巡園兼有觀察功能，兩份材料各自沒有說破。")

    def test_ordinary_sentences_pass_even_when_they_use_the_same_words(self):
        values = ("這只能說明：巡園減少與病蟲提早出現同時發生。", "兩份材料都沒有說明：誰來承接巡園的觀察。", "很難判斷：病蟲提早出現是因為巡園減少還是天氣。",
                  "仍無法判斷：自動化是否移除了偵測渠道。", "書摘只給了價值判斷：壞消息比好消息有用。", "2024 年的調查說明：農戶減少巡園。",
                  "原文沒有說明：巡園同時是觀察渠道。", "原文未說明：誰來補回觀察。", "這個說明很重要：它改變了誰來承接觀察。",
                  "研究說明了三件事，巡園兼有觀察功能。", "材料已經說明了灌溉耗時，但沒有說明觀察。", "判斷標準：A 與 B 是否同時出現。",
                  "材料說明巡園減少，另一份說明病蟲發現的時機。", "要檢查灌溉自動化有沒有同時移除偵測渠道。", "原文提到的說明：巡園減少。",
                  "Note: the two notes disagree on timing.")
        for path in (("baseline",), ("delta",), ("implication", "before"), ("implication", "after")):
            for value in values:
                with self.subTest(field=".".join(path), value=value):
                    self.check(path, value)

    def test_every_labelled_field_is_listed_in_one_error(self):
        analysis = copy.deepcopy(self.analysis)
        second = copy.deepcopy(analysis["insights"][0])
        second["id"] = "I2"
        analysis["insights"].append(second)
        first = analysis["insights"][0]
        first["baseline"] = "原文已說明：" + first["baseline"]
        first["implication"]["before"] = "原先：" + first["implication"]["before"]
        second["baseline"] = "新增判斷：" + second["baseline"]
        with self.assertRaises(ValueError) as caught:
            thought.validate_analysis(analysis, self.lookup, new_answer=True)
        message = str(caught.exception)
        self.assertIn("這些欄位以標籤開頭：I1.baseline, I1.implication.before, I2.baseline；", message)
        self.assertIn("直接寫內容，不要加標籤前綴（閱讀模板會自動加）", message)

    def test_the_label_rule_is_off_unless_a_new_answer_is_submitted(self):
        self.check(("baseline",), "書摘已經說明：" + self.analysis["insights"][0]["baseline"], new_answer=False)
        analysis = copy.deepcopy(self.analysis)
        analysis["insights"][0]["delta"] = "新增判斷：" + analysis["insights"][0]["delta"]
        thought.validate_analysis(analysis, self.lookup)


class CradleFailureMessageTests(unittest.TestCase):
    def test_mechanical_failure_names_quotes_only_for_quote_problems(self):
        quote = "「巡園」不在任何 evidence text 中原樣出現"
        self.assertIn("引文與原文不符", cradle.mechanical_failure_message([quote]))
        self.assertIn("引文與原文不符", cradle.mechanical_failure_message(["程式核對失敗：premise quote must match the current evidence", quote]))
        other = "程式核對失敗：這些欄位以標籤開頭：I1.baseline；直接寫內容，不要加標籤前綴（閱讀模板會自動加）"
        for errors in ([other], [other, quote]):
            message = cradle.mechanical_failure_message(errors)
            self.assertNotIn("引文", message)
            self.assertIn("答案未通過格式核對", message)


if __name__ == "__main__":
    unittest.main()
