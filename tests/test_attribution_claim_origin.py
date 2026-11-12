"""Claim attribution ("主張歸屬"): who made each knowledge point's claim.

Compiler spec section 7.2 "主張歸屬" is the only rule owner; these tests check that the program side follows it:
the marker `rail start` writes, the plan checks and their error codes, the projections (Knowledge Package, note,
registry, outcome page), the receipt count and the reader sentence.  Every case runs in an isolated temp root with
injected captures (no network, no production Vault or package).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime" / "workspace").is_dir():
    ROOT = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(ROOT))

from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import completion_revisions as revisions  # noqa: E402
from tools import ingest_plan_contract as contract  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402
from tools import obsidian_vault_adapter as adapter  # noqa: E402
from tools import outcome_page_generator as outcome  # noqa: E402
from tools import youtube_transcript_capture as yt  # noqa: E402

CACHE = Path(os.environ.get("BRAIN_TEST_TMPDIR") or tempfile.gettempdir())
VAULT = Path("knowledge-base-vault")
VIDEO_ID = "abcDEF12345"


def stamp(ms):
    hours, rest = divmod(ms, 3600000)
    minutes, rest = divmod(rest, 60000)
    seconds, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


def rolling_vtt(lines, step=2500):
    """YouTube-style rolling auto captions: each line shows in two or three cues."""
    out = ["WEBVTT", "Kind: captions", "Language: en", ""]
    now, previous = 0, None
    for line in lines:
        out += [f"{stamp(now)} --> {stamp(now + step)}", *([previous, line] if previous else [line]), ""]
        out += [f"{stamp(now + step)} --> {stamp(now + step + 10)}", line, ""]
        now += step + 10
        previous = line
    return "\n".join(out).encode()

SOURCE_AUTHOR = {"origin": "source_author", "cited_work": "", "relay": ""}
REASON = "文章全部是作者自己的經驗和判斷，沒有轉述別人的主張。"
NOT_FOUND = "搜尋後沒有找到名稱相同的書目頁，無法確定。"

# A book-review style source: other people's claims, named and unnamed, next to the author's own.
RELAY_TEXT = "\n\n".join([
    "這本書的作者Tiago Forte提出，筆記應該服務行動。",
    "書中把整理分成收集、整理、提煉和輸出四步。",
    "我自己試過之後，發現提煉這一步最容易被忽略。",
    "另一本書叫深度工作，說專注時間要先保護起來。",
])
# An original article: no claim-origin signal term anywhere.
ORIGINAL_TEXT = "\n\n".join([
    "我們把流程拆成三步，先收集資料。",
    "第二步是整理資料，把重複的項目合併。",
    "第三步是輸出結果，並且每週檢查一次。",
])
CAPTION_LINES = [
    "Today I want to talk about habits and how they shape a week.",
    "In this video I walk through the book Atomic Habits.",
    "The author says that small habits compound over time.",
    "I tried tracking my own habits for a month.",
    "My result was that a short list works best for me.",
    "Another study says that a written plan helps people follow through.",
    "So I keep one page and review it every Sunday.",
    "That is the whole routine I use now.",
]
# A Cantonese caption where the auto-captioner misheard a book title ("快師想" for 快思慢想).
CJK_CAPTION_LINES = [
    "今日想同大家傾下習慣點樣影響一個星期嘅安排。",
    "呢條片我會講下呢本書快師想，內容關於人點樣思考。",
    "作者話人有兩個思考系統，一個快一個慢。",
    "我自己試過記錄一個月嘅決定，發現慢思考好費力。",
    "我嘅結果係只要列出簡單清單就已經好有幫助。",
    "另外有本書叫原子習慣，話細小習慣會慢慢累積。",
    "另外有研究指出，寫低計劃會令人更加堅持去完成。",
    "所以我而家每個星期日都會檢視一頁紙嘅計劃。",
    "以上就係我而家每日使用嘅整套做法。",
]


def verification(name, quote=None, url="https://example.org/book"):
    """A verification record whose quote contains the verified name."""
    return {"url": url, "quote": quote or f"{name}（出版社書目頁的原文介紹）", "retrieved_at": "2026-11-07"}


def item(title, claim, block_ids, attribution=None):
    value = {"title": title, "claim": claim, "layer": "原文主張", "confidence": "高", "evidence_block_ids": list(block_ids),
             "reusable_concept": {"status": "not_reusable", "reason": "只適用於這個例子。"}}
    if attribution is not None:
        value["attribution"] = attribution
    return value


def named(position, relay="restates"):
    return {"origin": "cited_named", "cited_work": str(position), "relay": relay}


def codes(result):
    return sorted({rail.check_error_code(error) for error in result["errors"]})


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Job:
    """One started rail job: its package, the work order the agent reads and its source blocks."""

    def __init__(self, root, job_id, pointer):
        self.root, self.job_id, self.pointer = root, job_id, pointer
        _, self.package, self.kp_path = rail.paths(root, job_id)
        self.order = worker.read_json(root / pointer["work_order_file"])
        self.blocks = contract.source_block_catalog(self.package, version=self.order["source_catalog_version"])["blocks"]

    @property
    def bundle(self):
        return worker.read_json(self.package / "input_bundle.json")

    def block(self, needle):
        return next(block["id"] for block in self.blocks if needle in block["text"])

    def plan(self, knowledge, **sections):
        plan = {"schema_version": contract.COMPACT_VERSION, "source_catalog_version": self.order["source_catalog_version"],
                "input_fingerprint": self.order["input_fingerprint"],
                "summary": ["這份來源把作者自己的經驗和別人的主張放在一起說明。"],
                "knowledge": knowledge, "application": {"knowledge_use": "學習材料"}}
        plan.update(sections)
        return plan

    def check(self, plan, name="plan"):
        path = self.root / f"{self.job_id}-{name}.json"
        worker.write_json(path, plan)
        return rail.check_plan(self.root, self.job_id, path)

    def submit_and_complete(self, plan):
        path = self.root / f"{self.job_id}-submit.json"
        worker.write_json(path, plan)
        rail.measured(self.root, self.job_id, "submit_plan", rail.submit_plan, root=self.root, job_id=self.job_id, response_path=path)
        result = rail.advance(self.root, self.job_id, VAULT)
        assert result["status"] == "completed", result
        return result

    @property
    def receipt(self):
        return worker.read_json(self.package / "completion_receipt.json")

    @property
    def kp(self):
        return worker.read_json(self.kp_path)

    def note_paths(self):
        return [self.root / path for path in self.receipt["adapter"]["knowledge_points"]]

    def registry_rows(self):
        path = self.root / VAULT / "_registry" / "knowledge_points.jsonl"
        return [row for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if row["source_id"] == self.kp["source"]["source_id"]]

    def outcome_html(self):
        return (self.root / self.receipt["outcome"]["current_page"]).read_text(encoding="utf-8")

    def card_sentences(self):
        """The sentences the page shows under each knowledge-point card (the stored note body repeats them further down)."""
        return re.findall(r'<p class="small kp-attribution">([^<]*)</p>', self.outcome_html())


class RootCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        CACHE.mkdir(parents=True, exist_ok=True)
        cls.root = CACHE / f"attribution-{uuid.uuid4().hex[:10]}"
        cls.root.mkdir()
        # The outcome page is built by the generator found beside the root (as in the revision fixtures).
        (cls.root / "tools").mkdir()
        shutil.copy2(ROOT / "tools" / "outcome_page_generator.py", cls.root / "tools" / "outcome_page_generator.py")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.root, ignore_errors=True)

    def start_text(self, text, name, *, marked=True):
        source = self.root / f"{name}.md"
        # Each job needs its own bytes (one source identity has one owner); the closing note has no signal term.
        source.write_text(text + f"\n\n記錄編號 {uuid.uuid4().hex}。", encoding="utf-8")
        job_id = f"ATTR-{name.upper()}-{uuid.uuid4().hex[:6]}"
        args = SimpleNamespace(root=self.root, url=None, image_file=None, text_file=source, source_id=None, job_id=job_id,
                               title=name, slug=name, profile="article")
        if marked:
            pointer = rail.start(args)
        else:  # a job as issued before the marker existed
            with patch.object(worker, "accepted_attribution_fields", return_value={}):
                pointer = rail.start(args)
        return Job(self.root, job_id, pointer)

    def start_youtube(self, lines, name):
        vtt = rolling_vtt(lines)
        url = f"https://www.youtube.com/watch?v={VIDEO_ID}"
        capture = yt.YoutubeTranscriptCapture(
            normalized_url=url, video_id=VIDEO_ID, title=name, channel="Fixture", upload_date="20261008",
            selected_language="en", selected_track_kind="automatic", selected_track_group="en-orig", selected_track_item_index=0,
            selected_track_item_language="en", raw_vtt=vtt, raw_vtt_filename="source.en.vtt",
            info_json=json.dumps({"id": VIDEO_ID, "title": name, "language": "en",
                                  "automatic_captions": {"en-orig": [{"ext": "vtt", "url": "https://captions.example/track?lang=en"}]}}).encode(),
            canonical_body=yt.vtt_to_canonical_body(vtt, normalized_url=url, video_id=VIDEO_ID, language="en", track_kind="automatic"),
            yt_dlp_version="fixture")
        job_id = f"ATTR-YT-{uuid.uuid4().hex[:6]}"
        with patch.object(worker, "capture_youtube_transcript", return_value=capture):
            pointer = rail.start(SimpleNamespace(root=self.root, url=url, image_file=None, text_file=None, source_id=None,
                                                 job_id=job_id, title=None, slug=name, profile="article"))
        return Job(self.root, job_id, pointer)


def relay_plan(job, **sections):
    """Five points covering every origin; the source names two works."""
    first, second, third, fourth = (job.block(needle) for needle in ("作者Tiago Forte", "書中把整理", "我自己試過", "另一本書叫深度工作"))
    knowledge = [
        item("筆記服務行動", "作者Tiago Forte主張筆記應該服務行動，而不是只做收藏。", [first], named(1)),
        item("整理分四步", "書中把整理分成收集、整理、提煉和輸出四個步驟。", [second], {"origin": "cited_unnamed", "cited_work": "", "relay": "interprets"}),
        item("提煉最易被忽略", "作者自己試用後，覺得提煉這一步最容易被忽略。", [third], SOURCE_AUTHOR),
        item("專注時間要保護", "深度工作提醒要先把專注時間保護起來。", [fourth], named(2, "illustrates")),
        item("整理與提煉的分別", "整理和提煉是否同一步，來源沒有交代清楚。", [second, third], {"origin": "unclear", "cited_work": "", "relay": "extends"}),
    ]
    works = [{"label": "Tiago Forte", "evidence_block_ids": [first]}, {"label": "深度工作", "evidence_block_ids": [fourth]}]
    plan = job.plan(knowledge, source_metadata={"cited_works": works})
    plan.update(sections)
    return plan


class MarkerAndWorkOrderTests(RootCase):
    def test_new_text_job_carries_the_marker_and_asks_for_attribution(self):
        job = self.start_text(RELAY_TEXT, "marker")
        self.assertEqual(job.bundle["attribution_contract"], "claim_origin_v1")
        self.assertIn("attribution_contract", worker.read_json(worker.job_file(self.root, job.job_id))["rail_capture"])
        draft = worker.read_json(self.root / job.pointer["draft_file"])
        self.assertEqual(draft["knowledge"][0]["attribution"], {"origin": "", "cited_work": "", "relay": ""})
        self.assertIn("attribution", job.order["response_schema"]["properties"]["knowledge"]["items"]["required"])
        self.assertIn("source_metadata.cited_works", job.order["instructions"])
        self.assertIn("attribution_decision", job.order["instructions"])

    def test_a_job_started_without_the_marker_asks_for_nothing_new(self):
        job = self.start_text(RELAY_TEXT, "legacy-order", marked=False)
        self.assertNotIn("attribution_contract", job.bundle)
        draft = worker.read_json(self.root / job.pointer["draft_file"])
        self.assertNotIn("attribution", draft["knowledge"][0])
        self.assertNotIn("attribution", job.order["response_schema"]["properties"]["knowledge"]["items"]["required"])
        self.assertNotIn("attribution_decision", job.order["instructions"])

    def test_the_blank_template_is_refused_not_accepted(self):
        job = self.start_text(RELAY_TEXT, "blank")
        plan = job.plan([item("筆記服務行動", "作者主張筆記應該服務行動。", [job.block("作者Tiago Forte")], {"origin": "", "cited_work": "", "relay": ""})])
        result = job.check(plan)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(codes(result), ["attribution_missing"])

    def test_new_image_job_carries_the_marker(self):
        from PIL import Image
        image = self.root / "marker.png"
        Image.new("RGB", (32, 32), "white").save(image)
        job_id = "ATTR-IMG-" + uuid.uuid4().hex[:6]
        pointer = rail.start(SimpleNamespace(root=self.root, url=None, image_file=image, text_file=None, source_id=None,
                                             job_id=job_id, title="marker image", slug="marker-image", profile="article"))
        self.assertEqual(pointer["action"], "review_original_image")
        _, package, _ = rail.paths(self.root, job_id)
        self.assertEqual(worker.read_json(package / "input_bundle.json")["attribution_contract"], "claim_origin_v1")

    def test_the_marker_does_not_change_the_fingerprint_of_an_issued_job(self):
        job = self.start_text(RELAY_TEXT, "issued", marked=False)
        issued = job.order["input_fingerprint"]
        # Starting a new marked job for other text, then asking again, must leave the issued job's order alone.
        self.start_text(ORIGINAL_TEXT, "later")
        self.assertEqual(rail.work_order(self.root, job.job_id)["draft_file"], job.pointer["draft_file"])
        self.assertEqual(contract.input_fingerprint(job.package, job.order["source_catalog_version"]), issued)


class CheckPlanTests(RootCase):
    def test_a_correct_relaying_plan_passes_and_compiles_the_label(self):
        job = self.start_text(RELAY_TEXT, "correct")
        plan = relay_plan(job)
        result = job.check(plan)
        self.assertEqual(result["status"], "passed", result)
        kp = contract.build_kp(plan, job.package, root=self.root)
        self.assertEqual(kp["knowledge"][0]["attribution"], {"origin": "cited_named", "cited_work": "1", "relay": "restates", "label": "Tiago Forte"})
        self.assertEqual(kp["knowledge"][3]["attribution"]["label"], "深度工作")
        self.assertNotIn("label", kp["knowledge"][1]["attribution"])
        self.assertEqual(kp["source"]["cited_works"][1]["label"], "深度工作")

    def test_label_must_be_in_its_evidence_text(self):  # b
        job = self.start_text(RELAY_TEXT, "label")
        plan = relay_plan(job)
        plan["source_metadata"]["cited_works"][0]["label"] = "Tiago Forté Jr"
        result = job.check(plan)
        self.assertEqual(codes(result), ["cited_work_label_not_in_evidence"])
        # NFKC and whitespace do not matter: full-width letters and spacing still find the name.
        plan["source_metadata"]["cited_works"][0]["label"] = "Ｔｉａｇｏ　Ｆｏｒｔｅ"
        self.assertEqual(job.check(plan)["status"], "passed")

    def test_cited_work_structure_errors_each_have_their_own_code(self):  # c
        job = self.start_text(RELAY_TEXT, "structure")
        base = relay_plan(job)

        unreferenced = copy.deepcopy(base)
        unreferenced["source_metadata"]["cited_works"].append({"label": "提煉", "evidence_block_ids": [job.block("我自己試過")]})
        self.assertEqual(codes(job.check(unreferenced, "unreferenced")), ["cited_work_unreferenced"])

        invalid = copy.deepcopy(base)
        invalid["knowledge"][0]["attribution"]["cited_work"] = "9"
        self.assertEqual(codes(job.check(invalid, "invalid")), ["attribution_cited_work_invalid", "cited_work_unreferenced"])

        stray = copy.deepcopy(base)
        stray["knowledge"][2]["attribution"]["cited_work"] = "1"
        self.assertEqual(codes(job.check(stray, "stray")), ["attribution_cited_work_invalid"])

        missing = copy.deepcopy(base)
        missing["knowledge"][3]["attribution"]["cited_work"] = ""
        self.assertEqual(codes(job.check(missing, "missing")), ["attribution_cited_work_missing", "cited_work_unreferenced"])

        relay = copy.deepcopy(base)
        relay["knowledge"][1]["attribution"]["relay"] = ""
        self.assertEqual(codes(job.check(relay, "relay")), ["attribution_relay_missing"])

    def test_decision_rules(self):  # d
        job = self.start_text(RELAY_TEXT, "decision")
        ids = [job.block(needle) for needle in ("作者Tiago Forte", "書中把整理", "我自己試過")]
        every_own = [item(f"我的經驗{index}", f"作者自己的經驗和判斷第{index}點。", [block_id], SOURCE_AUTHOR) for index, block_id in enumerate(ids, 1)]
        missing = job.check(job.plan(copy.deepcopy(every_own)))
        self.assertEqual(codes(missing), ["attribution_decision_missing"])
        thin = job.check(job.plan(copy.deepcopy(every_own), attribution_decision={"decision": "all_source_author", "reason": "都是我"}))
        self.assertEqual(codes(thin), ["attribution_decision_reason_thin"])
        ok = job.check(job.plan(copy.deepcopy(every_own), attribution_decision={"decision": "all_source_author", "reason": REASON}))
        self.assertEqual(ok["status"], "passed", ok)
        # A decision beside a point that is not source_author is refused.
        mixed = copy.deepcopy(every_own)
        mixed[0]["attribution"] = {"origin": "cited_unnamed", "cited_work": "", "relay": "restates"}
        self.assertEqual(codes(job.check(job.plan(mixed, attribution_decision={"decision": "all_source_author", "reason": REASON}), "mixed")),
                         ["attribution_decision_not_applicable"])

    def test_decision_without_any_signal_is_not_applicable(self):  # d
        job = self.start_text(ORIGINAL_TEXT, "nosignal")
        ids = [job.block(needle) for needle in ("先收集", "整理資料", "輸出結果")]
        points = [item(f"步驟{index}", f"流程的第{index}步由作者自己設計。", [block_id], SOURCE_AUTHOR) for index, block_id in enumerate(ids, 1)]
        result = job.check(job.plan(points, attribution_decision={"decision": "all_source_author", "reason": REASON}))
        self.assertEqual(codes(result), ["attribution_decision_not_applicable"])

    def test_every_error_is_listed_at_once(self):  # i
        job = self.start_text(RELAY_TEXT, "all")
        plan = relay_plan(job)
        plan["source_metadata"]["cited_works"][0]["label"] = "不存在的名字"
        plan["source_metadata"]["cited_works"].append({"label": "提煉", "evidence_block_ids": [job.block("我自己試過")]})
        plan["knowledge"][1]["attribution"]["relay"] = ""
        plan["knowledge"][2]["attribution"] = {}
        plan["knowledge"][3]["attribution"]["cited_work"] = ""
        plan["knowledge"][4]["attribution"]["cited_work"] = "7"
        result = job.check(plan)
        self.assertEqual(codes(result), [
            "attribution_cited_work_invalid", "attribution_cited_work_missing", "attribution_missing", "attribution_relay_missing",
            "cited_work_label_not_in_evidence", "cited_work_unreferenced"])
        self.assertGreaterEqual(result["error_count"], 7)

    def test_signal_terms_have_one_definition_and_match_by_word(self):
        terms = contract.ATTRIBUTION_SIGNAL_TERMS
        for term in ("作者", "《", "研究指出", "引用", "author", "according to", "researchers"):
            self.assertIn(term, terms)
        count = contract.attribution_signal_block_count
        self.assertEqual(count([{"text": "As the Author wrote, according  to the paper."}]), 1)
        self.assertEqual(count([{"text": "A bookkeeping and authority note about studio work."}]), 0)
        self.assertEqual(count([{"text": "我們把流程拆成三步。"}, {"text": "教授提出了方法。"}]), 1)


class OriginalArticleTests(RootCase):
    def test_original_article_passes_without_a_decision_and_without_a_sentence(self):  # e
        job = self.start_text(ORIGINAL_TEXT, "original")
        ids = [job.block(needle) for needle in ("先收集", "整理資料", "輸出結果")]
        points = [item(f"步驟{index}", f"流程的第{index}步由作者自己設計。", [block_id], SOURCE_AUTHOR) for index, block_id in enumerate(ids, 1)]
        plan = job.plan(points)
        self.assertEqual(job.check(plan)["status"], "passed")
        self.assertEqual(contract.attribution_signal_block_count(job.blocks), 0)
        job.submit_and_complete(plan)
        self.assertEqual(job.receipt["attribution_effect"], {
            "contract": "claim_origin_v1", "signal_block_count": 0, "cited_work_count": 0, "verified_count": 0, "unverified_count": 0,
            "origin_counts": {"source_author": 3, "cited_named": 0, "cited_unnamed": 0, "unclear": 0}, "decision": ""})
        for note in job.note_paths():
            self.assertNotIn("主張來源", note.read_text(encoding="utf-8"))
        self.assertNotIn("主張來源", job.outcome_html())


class LegacyCompatibilityTests(RootCase):
    def test_an_unmarked_job_needs_no_attribution(self):  # f
        job = self.start_text(RELAY_TEXT, "legacy", marked=False)
        plan = job.plan([item("筆記服務行動", "作者Tiago Forte主張筆記應該服務行動。", [job.block("作者Tiago Forte")]),
                         item("提煉最易被忽略", "作者自己試用後，覺得提煉最容易被忽略。", [job.block("我自己試過")])])
        self.assertEqual(job.check(plan)["status"], "passed")
        self.assertNotIn("attribution", contract.build_kp(plan, job.package, root=self.root)["knowledge"][0])
        self.assertEqual(codes(job.check(job.plan(plan["knowledge"], attribution_decision={"decision": "all_source_author", "reason": REASON}), "d")),
                         ["attribution_decision_not_applicable"])

    def test_one_attribution_in_an_unmarked_plan_turns_the_whole_set_on(self):  # f
        job = self.start_text(RELAY_TEXT, "optin", marked=False)
        points = [item("筆記服務行動", "作者Tiago Forte主張筆記應該服務行動。", [job.block("作者Tiago Forte")], SOURCE_AUTHOR),
                  item("提煉最易被忽略", "作者自己試用後，覺得提煉最容易被忽略。", [job.block("我自己試過")]),
                  item("整理分四步", "書中把整理分成收集、整理、提煉和輸出四個步驟。", [job.block("書中把整理")])]
        result = job.check(job.plan(points))
        self.assertEqual(codes(result), ["attribution_missing"])
        self.assertEqual(result["error_count"], 2)
        points[1]["attribution"] = SOURCE_AUTHOR
        points[2]["attribution"] = SOURCE_AUTHOR
        self.assertEqual(codes(job.check(job.plan(points), "all")), ["attribution_decision_missing"])

    def test_old_projections_are_byte_identical_without_attribution(self):  # old data unchanged
        # The digests were taken from the code before this change (git HEAD) for the same inputs.
        point = adapter.KnowledgePoint("KP-KS-VS-0001-001", "筆記應該服務行動。", "原文主張", "高", "筆記應該服務行動。", "筆記服務行動",
                                       "reusable", "行動導向筆記", "可跨來源重用。")
        relation = adapter.KnowledgeRelation("KP-KS-VS-0001-001", "KP-KS-VS-0001-002", "supports", "證據", "中")
        note = adapter.render_kp_note(point, Path("KS-VS-0001_fixture.md"), "", [relation])
        self.assertEqual(sha(note), "7a374a4b3bd82492fe4db2189f82ae18738ceeaa26c4fb59eb002c4f7dd1bbb8")
        self.assertEqual(adapter.render_kp_note(point, Path("KS-VS-0001_fixture.md"), "", [relation], transcript_archive=True), note)
        cards = outcome.knowledge_point_cards([{"kp_id": "KP-1", "knowledge_point": "筆記應該服務行動。", "knowledge_type": "原文主張",
                                                "confidence": "高", "source_anchor": "這本書提到筆記應該服務行動。"}])
        self.assertEqual(sha(cards), "c536ffdb69491eb9aa9d4a5158f73a3cc0bc10768942547b46e4a131fc3aced1")
        self.assertEqual(adapter.knowledge_point_attribution(point), {})


class ProjectionTests(RootCase):
    """g: the correct relaying plan reaches the Knowledge Package, the notes, the registry, the page and the receipt."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job = cls.start_text(cls, RELAY_TEXT, "projection")
        cls.job.submit_and_complete(relay_plan(cls.job))

    def test_knowledge_package_carries_every_attribution(self):
        origins = [point["attribution"]["origin"] for point in self.job.kp["knowledge"]]
        self.assertEqual(origins, ["cited_named", "cited_unnamed", "source_author", "cited_named", "unclear"])
        self.assertEqual(self.job.kp["knowledge"][0]["attribution"]["label"], "Tiago Forte")

    def test_notes_carry_frontmatter_and_the_reader_sentence(self):
        notes = [path.read_text(encoding="utf-8") for path in self.job.note_paths()]
        fields = [adapter.frontmatter_fields_from_text(text) for text in notes]
        self.assertEqual([field["attribution_origin"] for field in fields],
                         ["cited_named", "cited_unnamed", "source_author", "cited_named", "unclear"])
        self.assertEqual([field["attribution_label"] for field in fields], ["Tiago Forte", "", "", "深度工作", ""])
        self.assertEqual([field["attribution_relay"] for field in fields], ["restates", "interprets", "", "illustrates", "extends"])
        self.assertIn("主張來源：Tiago Forte｜這段內容：照述", notes[0])
        self.assertIn("主張來源：來源提及但未點名的作品或人物｜這段內容：解讀及串連", notes[1])
        self.assertNotIn("主張來源", notes[2])
        self.assertIn("主張來源：深度工作｜這段內容：用例子說明", notes[3])
        self.assertIn("主張來源：來源未分清是誰的主張｜這段內容：延伸或回應", notes[4])
        self.assertNotIn("未核實", "".join(notes))

    def test_registry_rows_carry_the_same_object(self):
        rows = sorted(self.job.registry_rows(), key=lambda row: row["kp_id"])
        self.assertEqual([row["attribution"] for row in rows], [point["attribution"] for point in self.job.kp["knowledge"]])

    def test_outcome_page_shows_the_sentence_under_each_relayed_point(self):
        self.assertEqual(self.job.card_sentences(), [
            "主張來源：Tiago Forte｜這段內容：照述", "主張來源：來源提及但未點名的作品或人物｜這段內容：解讀及串連",
            "主張來源：深度工作｜這段內容：用例子說明", "主張來源：來源未分清是誰的主張｜這段內容：延伸或回應"])
        html = self.job.outcome_html()
        for internal in ("cited_named", "cited_unnamed", "source_author", "restates", "interprets", "illustrates"):
            self.assertNotIn(internal, html)

    def test_receipt_counts_the_attribution(self):
        self.assertEqual(self.job.receipt["attribution_effect"], {
            "contract": "claim_origin_v1", "signal_block_count": 3, "cited_work_count": 2, "verified_count": 0, "unverified_count": 2,
            "origin_counts": {"source_author": 1, "cited_named": 2, "cited_unnamed": 1, "unclear": 1}, "decision": ""})


class DecisionProjectionTests(RootCase):
    def test_the_all_source_author_decision_reaches_the_receipt(self):
        job = self.start_text(RELAY_TEXT, "decided")
        ids = [job.block(needle) for needle in ("作者Tiago Forte", "我自己試過")]
        points = [item(f"我的經驗{index}", f"作者自己的經驗和判斷第{index}點。", [block_id], SOURCE_AUTHOR) for index, block_id in enumerate(ids, 1)]
        job.submit_and_complete(job.plan(points, attribution_decision={"decision": "all_source_author", "reason": REASON}))
        effect = job.receipt["attribution_effect"]
        self.assertEqual((effect["decision"], effect["signal_block_count"], effect["cited_work_count"]), ("all_source_author", 3, 0))
        self.assertEqual(effect["origin_counts"]["source_author"], 2)
        self.assertEqual(job.kp["attribution_decision"]["decision"], "all_source_author")


class TranscriptArchiveTests(RootCase):
    def test_caption_source_marks_the_named_work_as_unverified(self):  # g
        job = self.start_youtube(CAPTION_LINES, "captions")
        self.assertEqual(job.bundle["source_retention_mode"], yt.TRANSCRIPT_ARCHIVE_MODE)
        book, author, study, own = (job.block(needle) for needle in ("Atomic Habits", "The author says", "Another study", "my own habits"))
        plan = job.plan(
            [item("Small habits compound", "The book argues that small habits compound over time.", [author],
                  named(1)),
             item("A written plan helps", "A study is said to show that a written plan helps people follow through.", [study],
                  {"origin": "cited_unnamed", "cited_work": "", "relay": "restates"}),
             item("A short list works", "The speaker found a short list works best.", [own], SOURCE_AUTHOR)],
            source_metadata={"cited_works": [{"label": "Atomic Habits", "evidence_block_ids": [book], "unverified_reason": NOT_FOUND}]})
        self.assertEqual(job.check(plan)["status"], "passed", job.check(plan))
        job.submit_and_complete(plan)
        notes = [path.read_text(encoding="utf-8") for path in job.note_paths()]
        self.assertIn("主張來源：Atomic Habits（照字幕所聞，未核實）｜這段內容：照述", notes[0])
        self.assertIn("主張來源：來源提及但未點名的作品或人物｜這段內容：照述", notes[1])
        self.assertNotIn("未核實", notes[1])
        self.assertEqual(adapter.frontmatter_fields_from_text(notes[0])["attribution_label"], "Atomic Habits")
        self.assertEqual(job.card_sentences(), ["主張來源：Atomic Habits（照字幕所聞，未核實）｜這段內容：照述",
                                                "主張來源：來源提及但未點名的作品或人物｜這段內容：照述"])
        self.assertEqual(job.receipt["attribution_effect"]["origin_counts"]["cited_named"], 1)
        self.assertGreaterEqual(job.receipt["attribution_effect"]["signal_block_count"], 2)


class ContentRevisionTests(RootCase):
    """h: a completed source from before the marker gains attribution through a content revision."""

    def test_revision_adds_attribution_to_every_projection(self):
        job = self.start_text(RELAY_TEXT, "revision", marked=False)
        first, second = job.block("作者Tiago Forte"), job.block("我自己試過")
        original = job.plan([item("筆記服務行動", "作者Tiago Forte主張筆記應該服務行動。", [first]),
                             item("提煉最易被忽略", "作者自己試用後，覺得提煉最容易被忽略。", [second])])
        job.submit_and_complete(original)
        self.assertEqual(job.receipt["attribution_effect"], {"status": "not_applicable"})
        self.assertNotIn("attribution", job.kp["knowledge"][0])
        self.assertNotIn("attribution", job.registry_rows()[0])

        revised = copy.deepcopy(original)
        revised["input_fingerprint"] = contract.input_fingerprint(job.package, revised["source_catalog_version"])
        revised["knowledge"][0]["attribution"] = named(1)
        revised["knowledge"][1]["attribution"] = copy.deepcopy(SOURCE_AUTHOR)
        revised["source_metadata"] = {"cited_works": [{"label": "Tiago Forte", "evidence_block_ids": [first]}]}
        plan_path = self.root / "revision-plan.json"
        worker.write_json(plan_path, revised)
        prepared = revisions.prepare_content_revision(
            self.root, job.kp_path, plan_path,
            authorization="Explicit synthetic content-revision authorization.",
            reason="Add the claim origin to this completed source only.")
        review = copy.deepcopy(prepared["review_template"])
        review.update(status="accepted", reviewer="ISOLATED-SYNTHETIC-TEST-ONLY")
        review["payload_sha256"] = revisions.digest(review)
        review_path = self.root / "revision-review.json"
        worker.write_json(review_path, review)
        self.assertEqual(revisions.apply_content_revision(self.root, self.root / prepared["revision_file"], review_path)["status"], "completed")

        kp = job.kp
        self.assertEqual(kp["knowledge"][0]["attribution"], {"origin": "cited_named", "cited_work": "1", "relay": "restates", "label": "Tiago Forte"})
        self.assertEqual(kp["source"]["cited_works"][0]["label"], "Tiago Forte")
        notes = [path.read_text(encoding="utf-8") for path in job.note_paths()]
        self.assertIn("主張來源：Tiago Forte｜這段內容：照述", notes[0])
        self.assertNotIn("主張來源", notes[1])
        fields = adapter.frontmatter_fields_from_text(notes[0])
        self.assertEqual((fields["attribution_origin"], fields["attribution_label"], fields["attribution_relay"]), ("cited_named", "Tiago Forte", "restates"))
        rows = sorted(job.registry_rows(), key=lambda row: row["kp_id"])
        self.assertEqual([row["attribution"]["origin"] for row in rows], ["cited_named", "source_author"])
        self.assertEqual(rows[0]["attribution"]["label"], "Tiago Forte")
        self.assertEqual(job.card_sentences(), ["主張來源：Tiago Forte｜這段內容：照述"])


class ContentRevisionPartialTests(RootCase):
    def test_revision_with_a_partial_attribution_is_checked_in_full(self):
        job = self.start_text(RELAY_TEXT, "revision-partial", marked=False)
        original = job.plan([item("筆記服務行動", "作者Tiago Forte主張筆記應該服務行動。", [job.block("作者Tiago Forte")]),
                             item("提煉最易被忽略", "作者自己試用後，覺得提煉最容易被忽略。", [job.block("我自己試過")])])
        job.submit_and_complete(original)
        revised = copy.deepcopy(original)
        revised["input_fingerprint"] = contract.input_fingerprint(job.package, revised["source_catalog_version"])
        revised["knowledge"][0]["attribution"] = copy.deepcopy(SOURCE_AUTHOR)
        plan_path = self.root / "revision-partial-plan.json"
        worker.write_json(plan_path, revised)
        with self.assertRaisesRegex(ValueError, "attribution_missing: knowledge\\[1\\]"):
            revisions.prepare_content_revision(self.root, job.kp_path, plan_path, authorization="Explicit synthetic authorization.",
                                               reason="Add the claim origin to this completed source only.")


def relay_text_points(job):
    """Two points that both interpret an unnamed work (the same reader sentence twice) and one of the author's own."""
    unnamed = {"origin": "cited_unnamed", "cited_work": "", "relay": "interprets"}
    return [item("整理分四步", "書中把整理分成收集、整理、提煉和輸出四個步驟。", [job.block("書中把整理")], dict(unnamed)),
            item("專注時間要保護", "另一本書提醒要先把專注時間保護起來。", [job.block("另一本書叫深度工作")], dict(unnamed)),
            item("提煉最易被忽略", "作者自己試用後，覺得提煉這一步最容易被忽略。", [job.block("我自己試過")], SOURCE_AUTHOR)]


class RepeatedCaptionSentenceTests(RootCase):
    """P1-1: two points may cite the same work and relay; the identical program sentence is not repeated content."""

    def test_two_points_of_one_book_in_a_caption_archive_complete(self):
        job = self.start_youtube(CAPTION_LINES, "repeat-captions")
        book, author, own = (job.block(needle) for needle in ("Atomic Habits", "The author says", "my own habits"))
        plan = job.plan(
            [item("Small habits compound", "The book argues that small habits compound over time.", [author], named(1)),
             item("The book is a walkthrough", "The video walks through the book chapter by chapter.", [book], named(1)),
             item("A short list works", "The speaker found a short list works best.", [own], SOURCE_AUTHOR)],
            source_metadata={"cited_works": [{"label": "Atomic Habits", "evidence_block_ids": [book], "unverified_reason": NOT_FOUND}]})
        self.assertEqual(job.check(plan)["status"], "passed")
        job.submit_and_complete(plan)
        sentence = "主張來源：Atomic Habits（照字幕所聞，未核實）｜這段內容：照述"
        self.assertEqual(job.card_sentences(), [sentence, sentence])
        self.assertGreaterEqual(len(sentence), 30)
        self.assertEqual(job.receipt["reader_quality_audit"]["status"], "ready")
        self.assertEqual(job.receipt["attribution_effect"]["origin_counts"]["cited_named"], 2)

class RepeatedUnnamedSentenceTests(RootCase):
    """P1-1: the same for two unnamed interpretations in a text source (one job per root keeps the older-knowledge step out of it)."""

    def test_two_unnamed_interpretations_in_a_text_source_complete(self):
        job = self.start_text(RELAY_TEXT, "repeat-unnamed")
        plan = job.plan(relay_text_points(job))
        self.assertEqual(job.check(plan)["status"], "passed")
        job.submit_and_complete(plan)
        sentence = "主張來源：來源提及但未點名的作品或人物｜這段內容：解讀及串連"
        self.assertEqual(job.card_sentences(), [sentence, sentence])
        self.assertEqual(job.receipt["reader_quality_audit"]["status"], "ready")
        self.assertEqual(job.receipt["attribution_effect"]["origin_counts"]["cited_unnamed"], 2)

    def test_only_the_attribution_paragraph_is_exempt_from_the_repetition_check(self):
        from tools import outcome_page_quality_audit as audit
        line = '<p class="small kp-attribution">主張來源：來源提及但未點名的作品或人物｜這段內容：解讀及串連</p>'
        self.assertEqual(audit.content_repetition_issues(f"<main>{line}{line}</main>"), [])
        other = "<p>這是一句夠長的重複內容，讀者在同一頁看見兩次就是重複內容，審核必須照舊把它攔下來。</p>"
        self.assertEqual(len(audit.content_repetition_issues(f"<main>{other}{other}</main>")), 1)
        copied = line.replace("small kp-attribution", "small")
        self.assertEqual(len(audit.content_repetition_issues(f"<main>{copied}{copied}</main>")), 1)


class FullFormatPlanTests(RootCase):
    """P1-2: attribution is handed in through a compact plan only (spec 7.2 "主張歸屬" item 1)."""

    def full_plan(self, job, plan=None):
        return contract.materialize_compact_plan(plan or relay_plan(job), job.package)

    def assert_refused(self, job, plan, name, *carried):
        result = job.check(plan, name)
        self.assertEqual(result["status"], "failed", result)
        self.assertEqual(codes(result), ["semantic_plan_format"], result["errors"])
        for token in carried:
            self.assertIn(token, "\n".join(result["errors"]))
        path = self.root / f"{job.job_id}-{name}-submit.json"
        worker.write_json(path, plan)
        with self.assertRaisesRegex(ValueError, "semantic_plan_format"):
            rail.submit_plan(self.root, job.job_id, path)
        state = worker.read_json(worker.job_file(self.root, job.job_id))
        self.assertFalse(state.get("execution_contract"))
        self.assertFalse((job.package / "completion_receipt.json").exists())

    def test_a_marked_job_refuses_a_full_format_plan_with_or_without_attribution(self):
        job = self.start_text(RELAY_TEXT, "full-marked")
        full = self.full_plan(job)
        self.assertEqual(full["schema_version"], contract.VERSION)
        self.assertIn("attribution", full["knowledge"][0])
        self.assert_refused(job, full, "carries", "knowledge[0].attribution", "source_metadata.cited_works")
        stripped = copy.deepcopy(full)
        for point in stripped["knowledge"]:
            point.pop("attribution", None)
        stripped["source_metadata"].pop("cited_works", None)
        self.assert_refused(job, stripped, "stripped")
        blank = copy.deepcopy(stripped)
        blank["knowledge"][0]["attribution"] = {"origin": "", "cited_work": "", "relay": ""}
        self.assert_refused(job, blank, "blank")
        # The compact plan the same job accepts is still the way in.
        self.assertEqual(job.check(relay_plan(job), "compact")["status"], "passed")

    def test_an_unmarked_job_refuses_attribution_in_a_full_format_plan(self):
        job = self.start_text(RELAY_TEXT, "full-unmarked", marked=False)
        plain = job.plan([item("筆記服務行動", "作者Tiago Forte主張筆記應該服務行動。", [job.block("作者Tiago Forte")]),
                          item("提煉最易被忽略", "作者自己試用後，覺得提煉最容易被忽略。", [job.block("我自己試過")])])
        full = self.full_plan(job, plain)
        self.assertEqual(job.check(full, "plain")["status"], "passed")  # an old-style full plan without attribution is unchanged
        self.assertEqual(job.check(plain, "compact")["status"], "passed")

        with_attribution = copy.deepcopy(full)
        with_attribution["knowledge"][0]["attribution"] = copy.deepcopy(SOURCE_AUTHOR)
        self.assert_refused(job, with_attribution, "attribution", "knowledge[0].attribution")

        with_decision = copy.deepcopy(full)
        with_decision["attribution_decision"] = {"decision": "all_source_author", "reason": REASON}
        self.assert_refused(job, with_decision, "decision", "attribution_decision")

        with_works = copy.deepcopy(full)
        with_works["source_metadata"]["cited_works"] = [{"label": "Tiago Forte", "evidence_block_ids": [job.block("作者Tiago Forte")]}]
        self.assert_refused(job, with_works, "works", "source_metadata.cited_works")

    def test_case_mode_and_unmarked_bundles_are_not_touched(self):
        plain_plan = {"knowledge": [{"title": "a", "claim": "b"}], "source_metadata": {"title": "t"}}
        self.assertEqual(contract.non_compact_attribution_issues(plain_plan, {"asset_mode": "case_evidence"}), [])
        self.assertEqual(contract.non_compact_attribution_issues(plain_plan, {"asset_mode": "knowledge_only"}), [])
        marked = {"asset_mode": "knowledge_only", "attribution_contract": "claim_origin_v1"}
        self.assertEqual(len(contract.non_compact_attribution_issues(plain_plan, marked)), 1)
        # The marker only means something for knowledge_only; a stray one does not block a case job.
        self.assertEqual(contract.non_compact_attribution_issues(plain_plan, {**marked, "asset_mode": "case_evidence"}), [])


# Helpers for a marked job that also offers older knowledge (a trimmed copy of the prior-knowledge fixtures).
BODY1 = (
    "# 證據可追溯性\n\n"
    "引用必須能回到原文位置，讀者才能核對。\n\n"
    "摘要只能整理原文，不能取代原文。\n\n"
    "保留上下文可以避免斷章取義。\n"
)
SENT1 = "引用必須能回到原文位置，讀者才能核對。"
SENT2 = "摘要只能整理原文，不能取代原文。"
SENT3 = "保留上下文可以避免斷章取義。"

BODY2 = (
    "# 影片字幕引用需要時間戳\n\n"
    "字幕引用必須附上時間戳，讀者才能回到影片的原始位置。\n\n"
    "## 證據可追溯性：引用必須能回到原文位置\n\n"
    "沒有時間戳的摘要無法取代影片原文。\n\n"
    "ZEBRAFISH-BODY-ONLY 只出現在正文，不是標題。\n"
)
NEW1 = "字幕引用必須附上時間戳，讀者才能回到影片的原始位置。"
NEW2 = "沒有時間戳的摘要無法取代影片原文。"
CONCEPT_NAME = "證據可追溯性"
SOURCE1_TITLE = "原則彙編"  # not the concept name, so a loose point's source title shares no words with the concept name
TARGET1 = "KS-VS-0001:KP-KS-VS-0001-001"
TARGET2 = "KS-VS-0001:KP-KS-VS-0001-002"
INSIGHT = "時間戳把原文位置的要求延伸到影音來源，沒有時間戳便無從核對。"
DISTINCT_REASON = "字幕引用是影音來源的做法，與一般的原文位置要求是兩個獨立概念。"
NOT_REUSABLE = {"status": "not_reusable", "reason": "Specific to this example."}


def prior_start_text(root: Path, source_id: str, job_id: str, title: str, body: str) -> dict:
    path = root / f"{job_id}.md"
    path.write_text(body, encoding="utf-8")
    return rail.start(SimpleNamespace(root=root, url=None, image_file=None, text_file=path, source_id=source_id,
                                      job_id=job_id, title=title, slug="fixture", profile="article"))


def prior_block_id(root: Path, job_id: str, sentence: str) -> str:
    job, package, _ = rail.paths(root, job_id)
    version = job["analysis_contract_version"]
    return next(row["id"] for row in contract.source_block_catalog(package, version=version)["blocks"] if sentence in row["text"])


def prior_base_plan(root: Path, job_id: str, items: list[dict]) -> dict:
    job, package, _ = rail.paths(root, job_id)
    version = job["analysis_contract_version"]
    return {"schema_version": contract.COMPACT_VERSION, "source_catalog_version": version,
            "input_fingerprint": contract.input_fingerprint(package, version),
            "summary": ["Fixture summary for the prior knowledge tests."],
            "knowledge": [
                {"title": item["title"], "claim": item["claim"], "layer": "原文主張", "confidence": "高",
                 "evidence_block_ids": [prior_block_id(root, job_id, item["sentence"])],
                 "reusable_concept": item["concept"]}
                for item in items],
            **({"application": {"knowledge_use": "學習材料"}}
               if version == contract.SOURCE_CATALOG_VERSION_V4 else {})}


def prior_reusable(name: str, **extra) -> dict:
    return {"status": "reusable", "name": name, "reason": "The principle holds beyond this source.", **extra}


def prior_write_json_file(root: Path, name: str, payload: dict) -> Path:
    path = root / name
    worker.write_json(path, payload)
    return path


def prior_complete_job(root: Path, job_id: str) -> dict:
    result = rail.advance(root, job_id, VAULT)
    assert result["status"] == "completed", result
    return result


def prior_start_second_source(root: Path, job_id="PK-TWO", source_id="KS-VS-0002", body=BODY2) -> dict:
    return prior_start_text(root, source_id, job_id, "證據可追溯性在影片字幕上的做法", body)


def prior_linked_plan(root: Path, job_id="PK-TWO", concept=None) -> dict:
    """A plan that decides the older concept the candidates offer (one link, the other point not linked)."""
    concept = concept or prior_reusable("字幕引用時間戳")
    same = concept.get("name") == CONCEPT_NAME
    plan = prior_base_plan(root, job_id, [
        {"title": "字幕引用附時間戳", "claim": NEW1, "sentence": NEW1, "concept": concept}])
    plan["prior_knowledge"] = {
        "decision": "linked",
        "reason": "The caption rule extends the older traceability principle.",
        "cross_links": [{"source_index": "1", "target_kp_uid": TARGET1, "relation_type": "extends", "insight": INSIGHT,
                         "evidence_block_ids": [prior_block_id(root, job_id, NEW1)],
                         "concept_relation": "same_concept" if same else "distinct",
                         **({} if same else {"concept_relation_reason": DISTINCT_REASON})}],
        "not_linked": [{"target_kp_uid": TARGET2, "reason": "This source does not say anything about summaries of its own."}],
    }
    return plan


def prior_check(root: Path, plan: dict, job_id="PK-TWO", name="candidate.json") -> dict:
    return rail.check_plan(root, job_id, prior_write_json_file(root, name, plan))

plk = SimpleNamespace(
    BODY1=BODY1,
    SENT1=SENT1,
    SENT2=SENT2,
    SENT3=SENT3,
    CONCEPT_NAME=CONCEPT_NAME,
    SOURCE1_TITLE=SOURCE1_TITLE,
    TARGET1=TARGET1,
    NOT_REUSABLE=NOT_REUSABLE,
    start_text=prior_start_text,
    block_id=prior_block_id,
    base_plan=prior_base_plan,
    reusable=prior_reusable,
    write_json_file=prior_write_json_file,
    complete_job=prior_complete_job,
    start_second_source=prior_start_second_source,
    linked_plan=prior_linked_plan,
    check=prior_check,
)


class PriorKnowledgeWithAttributionTests(RootCase):
    """A marked job may carry old-knowledge cross links and attribution in the same compact plan."""

    def test_cross_links_and_attribution_complete_together(self):
        # Source one through the real rail, as a marked job (its text has a signal word, so it hands in the decision).
        plk.start_text(self.root, "KS-VS-0001", "PK-ONE", plk.SOURCE1_TITLE, plk.BODY1)
        first = plk.base_plan(self.root, "PK-ONE", [
            {"title": "引用回到原文", "claim": plk.SENT1, "sentence": plk.SENT1, "concept": plk.reusable(plk.CONCEPT_NAME)},
            {"title": "摘要不取代原文", "claim": plk.SENT2, "sentence": plk.SENT2, "concept": plk.reusable(plk.CONCEPT_NAME)},
            {"title": "保留上下文", "claim": plk.SENT3, "sentence": plk.SENT3, "concept": dict(plk.NOT_REUSABLE)}])
        for point in first["knowledge"]:
            point["attribution"] = copy.deepcopy(SOURCE_AUTHOR)
        first["attribution_decision"] = {"decision": "all_source_author", "reason": REASON}
        self.assertEqual(plk.check(self.root, first, "PK-ONE", "check-one.json")["status"], "passed")
        rail.submit_plan(self.root, "PK-ONE", plk.write_json_file(self.root, "plan-one.json", first))
        plk.complete_job(self.root, "PK-ONE")

        plk.start_second_source(self.root)
        plan = plk.linked_plan(self.root)
        plan["knowledge"][0]["attribution"] = {"origin": "cited_unnamed", "cited_work": "", "relay": "extends"}
        result = plk.check(self.root, plan)
        self.assertEqual((result["status"], result["errors"]), ("passed", []))
        rail.submit_plan(self.root, "PK-TWO", plk.write_json_file(self.root, "plan-two.json", plan))
        plk.complete_job(self.root, "PK-TWO")

        _, package, kp_path = rail.paths(self.root, "PK-TWO")
        kp = worker.read_json(kp_path)
        self.assertEqual(kp["knowledge"][0]["attribution"]["origin"], "cited_unnamed")
        self.assertEqual([link["target_kp_uid"] for link in kp["prior_knowledge"]["cross_links"]], [plk.TARGET1])
        receipt = worker.read_json(package / "completion_receipt.json")
        self.assertEqual(receipt["attribution_effect"]["origin_counts"]["cited_unnamed"], 1)
        self.assertEqual(receipt["prior_knowledge_effect"]["status"], "candidates_reviewed")
        self.assertEqual(receipt["reader_quality_audit"]["status"], "ready")
        note = (self.root / receipt["adapter"]["knowledge_points"][0]).read_text(encoding="utf-8")
        self.assertIn("主張來源：來源提及但未點名的作品或人物｜這段內容：延伸或回應", note)
        self.assertEqual(adapter.frontmatter_fields_from_text(note)["attribution_relay"], "extends")


class ContentRevisionOfAttributedSourceTests(RootCase):
    """A source that already has attribution is revised again; a cited work leaves the list."""

    def revise(self, job, revised, name):
        revised["input_fingerprint"] = contract.input_fingerprint(job.package, revised["source_catalog_version"])
        plan_path = self.root / f"{name}-plan.json"
        worker.write_json(plan_path, revised)
        prepared = revisions.prepare_content_revision(
            self.root, job.kp_path, plan_path, authorization="Explicit synthetic content-revision authorization.",
            reason="Change the claim origins of this attributed source only.")
        review = copy.deepcopy(prepared["review_template"])
        review.update(status="accepted", reviewer="ISOLATED-SYNTHETIC-TEST-ONLY")
        review["payload_sha256"] = revisions.digest(review)
        review_path = self.root / f"{name}-review.json"
        worker.write_json(review_path, review)
        self.assertEqual(revisions.apply_content_revision(self.root, self.root / prepared["revision_file"], review_path)["status"], "completed")

    def test_removing_a_cited_work_updates_every_projection(self):
        job = self.start_text(RELAY_TEXT, "revise-attributed")
        original = relay_plan(job)
        job.submit_and_complete(original)
        before = [path.read_text(encoding="utf-8") for path in job.note_paths()]
        self.assertIn("主張來源：深度工作｜這段內容：用例子說明", before[3])

        revised = copy.deepcopy(original)
        revised["knowledge"][3]["attribution"] = {"origin": "cited_unnamed", "cited_work": "", "relay": "illustrates"}
        revised["source_metadata"]["cited_works"] = revised["source_metadata"]["cited_works"][:1]
        self.revise(job, revised, "revise-attributed")

        kp = job.kp
        self.assertEqual([work["label"] for work in kp["source"]["cited_works"]], ["Tiago Forte"])
        self.assertEqual(kp["knowledge"][3]["attribution"], {"origin": "cited_unnamed", "cited_work": "", "relay": "illustrates"})
        self.assertEqual(kp["knowledge"][0]["attribution"]["label"], "Tiago Forte")
        notes = [path.read_text(encoding="utf-8") for path in job.note_paths()]
        self.assertIn("主張來源：來源提及但未點名的作品或人物｜這段內容：用例子說明", notes[3])
        self.assertNotIn("深度工作｜", notes[3])
        fields = adapter.frontmatter_fields_from_text(notes[3])
        self.assertEqual((fields["attribution_origin"], fields["attribution_label"], fields["attribution_relay"]), ("cited_unnamed", "", "illustrates"))
        self.assertEqual(adapter.frontmatter_fields_from_text(notes[0])["attribution_label"], "Tiago Forte")
        rows = sorted(job.registry_rows(), key=lambda row: row["kp_id"])
        self.assertEqual(rows[3]["attribution"], {"origin": "cited_unnamed", "cited_work": "", "relay": "illustrates"})
        self.assertNotIn("深度工作", json.dumps([row["attribution"] for row in rows], ensure_ascii=False))
        self.assertEqual(job.card_sentences(), [
            "主張來源：Tiago Forte｜這段內容：照述", "主張來源：來源提及但未點名的作品或人物｜這段內容：解讀及串連",
            "主張來源：來源提及但未點名的作品或人物｜這段內容：用例子說明", "主張來源：來源未分清是誰的主張｜這段內容：延伸或回應"])

class ContentRevisionVerifiedNameTests(RootCase):
    """Name verification through a content revision of an attributed source (its own root: one source of this text per root)."""

    revise = ContentRevisionOfAttributedSourceTests.revise

    def test_a_revision_writes_the_verification_record_and_refuses_a_bad_one(self):
        job = self.start_text(RELAY_TEXT, "revise-verified")
        original = relay_plan(job)
        job.submit_and_complete(original)

        bad = copy.deepcopy(original)
        bad["input_fingerprint"] = contract.input_fingerprint(job.package, bad["source_catalog_version"])
        bad["source_metadata"]["cited_works"][1].update(verified_name="《深度工作》", verification=verification("《深度工作》", url="http://example.org/deep-work"))
        bad_path = self.root / "revise-verified-bad.json"
        worker.write_json(bad_path, bad)
        with self.assertRaisesRegex(ValueError, "cited_work_verification_invalid"):
            revisions.prepare_content_revision(self.root, job.kp_path, bad_path, authorization="Explicit synthetic authorization.",
                                               reason="Add a verified name to this attributed source only.")

        revised = copy.deepcopy(original)
        record = verification("《深度工作》", "《深度工作》（Deep Work）是 Cal Newport 於2016年出版的書。")
        revised["source_metadata"]["cited_works"][1].update(verified_name="《深度工作》", verification=record)
        self.revise(job, revised, "revise-verified")

        kp = job.kp
        self.assertEqual(kp["source"]["cited_works"][1]["verification"], record)
        self.assertEqual(kp["source"]["cited_works"][1]["verified_name"], "《深度工作》")
        self.assertEqual(kp["knowledge"][3]["attribution"]["verified_name"], "《深度工作》")
        self.assertNotIn("verified_name", kp["knowledge"][0]["attribution"])
        notes = [path.read_text(encoding="utf-8") for path in job.note_paths()]
        self.assertIn("主張來源：《深度工作》（原文作「深度工作」）｜這段內容：用例子說明", notes[3])
        self.assertEqual(adapter.frontmatter_fields_from_text(notes[3])["attribution_verified_name"], "《深度工作》")
        self.assertIn("主張來源：Tiago Forte｜這段內容：照述", notes[0])
        rows = sorted(job.registry_rows(), key=lambda row: row["kp_id"])
        self.assertEqual(rows[3]["attribution"]["verified_name"], "《深度工作》")
        self.assertIn("主張來源：《深度工作》（原文作「深度工作」）｜這段內容：用例子說明", job.card_sentences())


def cjk_caption_plan(job, works=None, **sections):
    """A Cantonese caption plan: the misheard book title (快師想) and a second book, plus an unnamed study and the speaker's own point."""
    second, third, own = (job.block(needle) for needle in ("作者話人", "原子習慣", "有研究指出"))
    first = job.block("快師想")
    knowledge = [
        item("思考分快慢", "書中把人的思考分成快與慢兩個系統。", [second], named(1)),
        item("細小習慣累積", "另一本書說細小的習慣會慢慢累積成大改變。", [third], named(2)),
        item("寫下計劃更堅持", "有研究指出，寫下計劃會令人更堅持完成。", [own], {"origin": "cited_unnamed", "cited_work": "", "relay": "restates"}),
        item("慢思考很費力", "說話的人記錄一個月的決定，發現慢思考很費力。", [job.block("我自己試過")], SOURCE_AUTHOR),
    ]
    works = works if works is not None else [
        {"label": "快師想", "evidence_block_ids": [first], "verified_name": "《快思慢想》",
         "verification": verification("《快思慢想》", "《快思慢想》（Thinking, Fast and Slow）是康納曼的著作。")},
        {"label": "原子習慣", "evidence_block_ids": [third], "unverified_reason": NOT_FOUND},
    ]
    return job.plan(knowledge, source_metadata={"cited_works": works}, **sections)


class CaptionVerificationCheckTests(RootCase):
    """Name verification (spec 7.2 item 2a): a caption source must verify each cited work or say why not; one job, checks only."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job = cls.start_youtube(cls, CJK_CAPTION_LINES, "cjk-captions")

    def works(self, plan):
        return plan["source_metadata"]["cited_works"]

    def test_a_verified_name_and_an_unverified_reason_both_pass(self):
        result = self.job.check(cjk_caption_plan(self.job))
        self.assertEqual(result["status"], "passed", result)

    def test_a_caption_work_with_neither_verification_nor_reason_is_stopped(self):
        plan = cjk_caption_plan(self.job)
        for work in self.works(plan):
            for key in ("verified_name", "verification", "unverified_reason"):
                work.pop(key, None)
        result = self.job.check(plan, "missing")
        self.assertEqual(codes(result), ["cited_work_verification_missing"])
        self.assertEqual(result["error_count"], 2)  # both works, listed together

    def test_each_invalid_verification_has_its_code(self):
        cases = {
            "name not in quote": lambda work: work["verification"].update(quote="《思考，快與慢》是同一本書的另一個譯名。"),
            "http url": lambda work: work["verification"].update(url="http://example.org/book"),
            "not a url": lambda work: work["verification"].update(url="example.org/book"),
            "no retrieved_at": lambda work: work["verification"].pop("retrieved_at"),
            "blank quote": lambda work: work["verification"].update(quote=" "),
            "both verification and reason": lambda work: work.update(unverified_reason=NOT_FOUND),
            "name without verification": lambda work: work.pop("verification"),
            "verification without name": lambda work: work.pop("verified_name"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                plan = cjk_caption_plan(self.job)
                change(self.works(plan)[0])
                self.assertEqual(codes(self.job.check(plan, "invalid")), ["cited_work_verification_invalid"])

    def test_the_name_is_found_in_the_quote_after_nfkc_and_without_spaces(self):
        plan = cjk_caption_plan(self.job)
        self.works(plan)[0].update(verified_name="Ｔｈｉｎｋｉｎｇ，　Ｆａｓｔ", verification=verification("x", "…Thinking,Fast and Slow…"))
        self.assertEqual(self.job.check(plan, "nfkc")["status"], "passed")

    def test_a_name_with_a_line_break_or_control_character_is_stopped(self):
        # the name is saved on one frontmatter line; a break would cut it short or end the frontmatter early
        for name in ("快思\n慢想", "快思\r慢想", "快思 慢想", "快思 慢想", "快思\x85慢想", "快思​慢想",
                     "快思慢想\n---\nevil: 1"):
            with self.subTest(name=name):
                plan = cjk_caption_plan(self.job)
                self.works(plan)[0].update(verified_name=name, verification=verification(name, "《快思慢想》（Thinking, Fast and Slow）是康納曼的著作。\n" + name))
                result = self.job.check(plan, "control-name")
                self.assertEqual(codes(result), ["cited_work_verification_invalid"])
                self.assertIn("verified_name must not contain a line break", json.dumps(result, ensure_ascii=False))

    def test_a_label_with_a_line_break_is_stopped(self):
        plan = cjk_caption_plan(self.job)
        self.works(plan)[0]["label"] = "快師\n想"  # still found in the caption once whitespace is ignored
        result = self.job.check(plan, "control-label")
        self.assertEqual(codes(result), ["cited_work_verification_invalid"])
        self.assertIn("label must not contain a line break", json.dumps(result, ensure_ascii=False))
        plan = cjk_caption_plan(self.job)
        self.works(plan)[1]["label"] = "原子​習慣"  # an unverified work with a zero-width label is stopped too (and is no longer the caption's own words)
        self.assertEqual(codes(self.job.check(plan, "control-label-2")), ["cited_work_label_not_in_evidence", "cited_work_verification_invalid"])

    def test_an_unparseable_url_is_one_listed_error_not_a_crash(self):
        plan = cjk_caption_plan(self.job)
        works = self.works(plan)
        works[0]["verification"]["url"] = "https://[::1"
        works[1]["unverified_reason"] = "查不到"
        plan["knowledge"][3]["attribution"] = {}
        result = self.job.check(plan, "bad-url")
        self.assertEqual(codes(result), ["attribution_missing", "cited_work_unverified_reason_thin", "cited_work_verification_invalid"])
        self.assertIn("verification.url must be an https page", json.dumps(result, ensure_ascii=False))

    def test_an_ordinary_name_with_spaces_fullwidth_and_punctuation_passes(self):
        for name in ("Thinking, Fast and Slow", "快思慢想　Thinking，Fast and Slow！", "《快思慢想》（第２版）: 康納曼 & 友人 #1"):
            with self.subTest(name=name):
                plan = cjk_caption_plan(self.job)
                self.works(plan)[0].update(verified_name=name, verification=verification(name, f"…{name}…是康納曼的著作。"))
                self.assertEqual(self.job.check(plan, "plain-name")["status"], "passed")

    def test_a_thin_reason_is_stopped(self):
        for reason in ("查不到", "。。。。。。。。", "   "):
            with self.subTest(reason):
                plan = cjk_caption_plan(self.job)
                self.works(plan)[1]["unverified_reason"] = reason
                wanted = ["cited_work_verification_missing"] if not reason.strip() else ["cited_work_unverified_reason_thin"]
                self.assertEqual(codes(self.job.check(plan, "thin")), wanted)

    def test_the_label_must_still_be_the_captions_own_words(self):
        plan = cjk_caption_plan(self.job)
        self.works(plan)[0]["label"] = "快思慢想"  # the correct name is not what the caption says
        self.assertEqual(codes(self.job.check(plan, "label")), ["cited_work_label_not_in_evidence"])

    def test_every_verification_error_is_listed_with_the_other_attribution_errors(self):
        plan = cjk_caption_plan(self.job)
        works = self.works(plan)
        works[0]["verification"]["url"] = "http://example.org/book"
        works[1]["unverified_reason"] = "查不到"
        works.append({"label": "習慣", "evidence_block_ids": [self.job.block("原子習慣")]})
        plan["knowledge"][3]["attribution"] = {}
        result = self.job.check(plan, "all")
        self.assertEqual(codes(result), ["attribution_missing", "cited_work_unreferenced", "cited_work_unverified_reason_thin",
                                         "cited_work_verification_invalid", "cited_work_verification_missing"])
        self.assertGreaterEqual(result["error_count"], 5)

    def test_error_codes_are_the_ones_the_spec_names(self):
        source = (ROOT / "tools" / "ingest_plan_contract.py").read_text(encoding="utf-8")
        spec = (ROOT / "docs" / "Compiler Worker與Knowledge Package設計及驗收規格.md").read_text(encoding="utf-8")
        for code in ("cited_work_verification_missing", "cited_work_verification_invalid", "cited_work_unverified_reason_thin"):
            self.assertIn(f'f"{code}:', source)
            self.assertIn(f"`{code}`", spec)

    def test_the_work_order_asks_a_caption_job_to_verify(self):
        self.assertIn("verified_name", self.job.order["instructions"])
        self.assertIn("unverified_reason", self.job.order["instructions"])


class CaptionVerifiedProjectionTests(RootCase):
    """A verified caption name reaches the Knowledge Package, notes, registry, page and receipt; an unverified one keeps its suffix."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job = cls.start_youtube(cls, CJK_CAPTION_LINES, "cjk-projection")
        cls.job.submit_and_complete(cjk_caption_plan(cls.job))

    def test_knowledge_package_keeps_the_label_and_the_verification_record(self):
        kp = self.job.kp
        self.assertEqual(kp["knowledge"][0]["attribution"],
                         {"origin": "cited_named", "cited_work": "1", "relay": "restates", "label": "快師想", "verified_name": "《快思慢想》"})
        self.assertNotIn("verified_name", kp["knowledge"][1]["attribution"])
        works = kp["source"]["cited_works"]
        self.assertEqual(works[0]["label"], "快師想")
        self.assertEqual(works[0]["verification"], {"url": "https://example.org/book", "retrieved_at": "2026-11-07",
                                                    "quote": "《快思慢想》（Thinking, Fast and Slow）是康納曼的著作。"})
        self.assertEqual(works[1]["unverified_reason"], NOT_FOUND)

    def test_notes_show_the_verified_name_and_the_caption_wording(self):
        notes = [path.read_text(encoding="utf-8") for path in self.job.note_paths()]
        fields = [adapter.frontmatter_fields_from_text(text) for text in notes]
        self.assertEqual(fields[0]["attribution_verified_name"], "《快思慢想》")
        self.assertEqual(fields[0]["attribution_label"], "快師想")
        self.assertNotIn("attribution_verified_name", fields[1])
        self.assertIn("主張來源：《快思慢想》（字幕作「快師想」）｜這段內容：照述", notes[0])
        self.assertNotIn("未核實", notes[0])
        self.assertIn("主張來源：原子習慣（照字幕所聞，未核實）｜這段內容：照述", notes[1])
        self.assertNotIn("attribution_verified_name", fields[3])

    def test_registry_rows_carry_the_verified_name(self):
        rows = sorted(self.job.registry_rows(), key=lambda row: row["kp_id"])
        self.assertEqual([row["attribution"] for row in rows], [point["attribution"] for point in self.job.kp["knowledge"]])
        self.assertEqual(rows[0]["attribution"]["verified_name"], "《快思慢想》")

    def test_the_page_shows_the_same_sentences(self):
        self.assertEqual(self.job.card_sentences(), [
            "主張來源：《快思慢想》（字幕作「快師想」）｜這段內容：照述", "主張來源：原子習慣（照字幕所聞，未核實）｜這段內容：照述",
            "主張來源：來源提及但未點名的作品或人物｜這段內容：照述"])

    def test_the_receipt_counts_verified_and_unverified_works(self):
        effect = self.job.receipt["attribution_effect"]
        self.assertEqual((effect["cited_work_count"], effect["verified_count"], effect["unverified_count"]), (2, 1, 1))
        self.assertEqual(self.job.receipt["reader_quality_audit"]["status"], "ready")


def verified_text_plan(job, **changes):
    """The relay plan with both cited works verified (the first name equals the source's wording, the second differs from it)."""
    plan = relay_plan(job)
    works = plan["source_metadata"]["cited_works"]
    works[0].update(verified_name="Tiago Forte", verification=verification("Tiago Forte", "Tiago Forte is the author of Building a Second Brain."))
    works[1].update(verified_name="《深度工作》", verification=verification("《深度工作》", "《深度工作》（Deep Work）是 Cal Newport 於2016年出版的書。"))
    for index, change in changes.items():
        change(works[int(index)])
    return plan


class TextVerificationTests(RootCase):
    """A text source may verify a name, but need not; a wrong record is stopped the same way (checks only)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job = cls.start_text(cls, RELAY_TEXT, "text-verified")

    def plan(self, **changes):
        return verified_text_plan(self.job, **changes)

    def test_a_text_source_needs_no_verification(self):
        self.assertEqual(self.job.check(relay_plan(self.job), "none")["status"], "passed")

    def test_a_wrong_record_is_stopped_even_though_it_is_optional(self):
        plan = self.plan(**{"1": lambda work: work["verification"].update(url="http://example.org/deep-work")})
        self.assertEqual(codes(self.job.check(plan, "http")), ["cited_work_verification_invalid"])
        plan = self.plan(**{"0": lambda work: work.update(verified_name="Tiago Forté Jr")})
        self.assertEqual(codes(self.job.check(plan, "name")), ["cited_work_verification_invalid"])
        plan = self.plan(**{"0": lambda work: work.update(unverified_reason="查不到")})
        self.assertEqual(codes(self.job.check(plan, "both")), ["cited_work_verification_invalid"])
        plan = relay_plan(self.job)
        plan["source_metadata"]["cited_works"][0]["unverified_reason"] = "無"
        self.assertEqual(codes(self.job.check(plan, "thin")), ["cited_work_unverified_reason_thin"])

    def test_a_correct_record_passes(self):
        self.assertEqual(self.job.check(self.plan(), "ok")["status"], "passed")


class TextVerifiedProjectionTests(RootCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job = cls.start_text(cls, RELAY_TEXT, "text-projection")
        cls.job.submit_and_complete(verified_text_plan(cls.job))

    def test_a_verified_name_reaches_every_projection_and_the_source_wording_is_kept(self):
        kp = self.job.kp
        self.assertEqual(kp["knowledge"][3]["attribution"]["label"], "深度工作")
        self.assertEqual(kp["knowledge"][3]["attribution"]["verified_name"], "《深度工作》")
        self.assertEqual(kp["source"]["cited_works"][1]["verification"]["retrieved_at"], "2026-11-07")
        notes = [path.read_text(encoding="utf-8") for path in self.job.note_paths()]
        self.assertIn("主張來源：Tiago Forte｜這段內容：照述", notes[0])  # same as the source's wording: nothing added
        self.assertNotIn("原文作", notes[0])
        self.assertIn("主張來源：《深度工作》（原文作「深度工作」）｜這段內容：用例子說明", notes[3])
        self.assertEqual(adapter.frontmatter_fields_from_text(notes[3])["attribution_verified_name"], "《深度工作》")
        rows = sorted(self.job.registry_rows(), key=lambda row: row["kp_id"])
        self.assertEqual(rows[3]["attribution"]["verified_name"], "《深度工作》")
        self.assertIn("主張來源：《深度工作》（原文作「深度工作」）｜這段內容：用例子說明", self.job.card_sentences())
        effect = self.job.receipt["attribution_effect"]
        self.assertEqual((effect["cited_work_count"], effect["verified_count"], effect["unverified_count"]), (2, 2, 0))


class ReaderLineTests(unittest.TestCase):
    def test_reader_sentence_for_every_verification_state(self):
        line = adapter.attribution_reader_line
        self.assertEqual(line("cited_named", "快師想", "restates", transcript_archive=True, verified_name="《快思慢想》"),
                         "主張來源：《快思慢想》（字幕作「快師想」）｜這段內容：照述")
        self.assertEqual(line("cited_named", "快師想", "restates", verified_name="《快思慢想》"),
                         "主張來源：《快思慢想》（原文作「快師想」）｜這段內容：照述")
        self.assertEqual(line("cited_named", "Atomic Habits", "extends", transcript_archive=True, verified_name="Atomic Habits"),
                         "主張來源：Atomic Habits｜這段內容：延伸或回應")
        self.assertEqual(line("cited_named", "Atomic Habits", "extends", transcript_archive=True, verified_name="Atomic　Habits"),
                         "主張來源：Atomic　Habits｜這段內容：延伸或回應")
        self.assertEqual(line("cited_named", "快師想", "restates", transcript_archive=True),
                         "主張來源：快師想（照字幕所聞，未核實）｜這段內容：照述")
        self.assertEqual(line("cited_named", "", "restates", verified_name="《快思慢想》"), "")
        self.assertEqual(line("cited_unnamed", "", "restates", transcript_archive=True, verified_name="x"),
                         "主張來源：來源提及但未點名的作品或人物｜這段內容：照述")
        self.assertEqual(line("source_author", "", "", verified_name="x"), "")

    def test_a_point_without_a_verified_name_projects_exactly_as_before(self):
        point = adapter.KnowledgePoint("KP-X-001", "筆記應該服務行動。", "原文主張", "高", "筆記應該服務行動。", "筆記服務行動", "reusable", "行動導向筆記",
                                       "可跨來源重用。", "", "cited_named", "1", "Tiago Forte", "restates")
        self.assertEqual(adapter.knowledge_point_attribution(point), {"origin": "cited_named", "cited_work": "1", "relay": "restates", "label": "Tiago Forte"})
        note = adapter.render_kp_note(point, Path("KS-VS-0001_fixture.md"), "", [])
        self.assertNotIn("attribution_verified_name", note)
        self.assertIn("主張來源：Tiago Forte｜這段內容：照述", note)

    def test_the_cited_work_schema_accepts_the_record_and_nothing_else(self):
        item_schema = contract.cited_works_schema()["items"]
        self.assertEqual(item_schema["required"], ["label", "evidence_block_ids"])
        self.assertEqual(set(item_schema["properties"]), {"label", "evidence_block_ids", "verified_name", "verification", "unverified_reason"})
        self.assertEqual(set(item_schema["properties"]["verification"]["properties"]), {"url", "quote", "retrieved_at"})
        self.assertFalse(item_schema["additionalProperties"])


class RegistryRoundTripTests(unittest.TestCase):
    """A later registry rewrite (next ingest, duplicate review, source removal) must keep other sources'
    attribution objects intact instead of turning them into Python-repr strings."""

    def setUp(self):
        CACHE.mkdir(parents=True, exist_ok=True)
        self.vault = CACHE / f"registry-roundtrip-{uuid.uuid4().hex}"
        self.addCleanup(shutil.rmtree, self.vault, True)
        attribution = {"cited_work": "1", "label": "心態", "origin": "cited_named", "relay": "restates", "verified_name": "致富心態"}
        rows = [{"kp_uid": "KS-VS-0001:KP-001", "kp_id": "KP-001", "attribution": attribution, "count": 3,
                 "tags": ["a", "b"], "empty": None},
                {"kp_uid": "KS-VS-0002:KP-001", "kp_id": "KP-001"}]
        path = adapter.knowledge_point_registry_file(self.vault)
        path.parent.mkdir(parents=True)
        path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")
        self.attribution = attribution

    def test_rewriting_the_registry_keeps_other_sources_attribution_objects(self):
        existing = adapter.read_knowledge_point_registry(self.vault)
        self.assertEqual(existing["KS-VS-0001:KP-001"]["attribution"], self.attribution)
        self.assertEqual(existing["KS-VS-0001:KP-001"]["tags"], ["a", "b"])
        self.assertEqual(existing["KS-VS-0001:KP-001"]["count"], "3")  # scalars keep their old text form
        self.assertEqual(existing["KS-VS-0001:KP-001"]["empty"], "None")
        self.assertEqual(existing["KS-VS-0002:KP-001"]["kp_id"], "KP-001")
        adapter.write_knowledge_point_registry_rows(self.vault, existing)
        lines = adapter.knowledge_point_registry_file(self.vault).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])["attribution"], self.attribution)
        self.assertEqual(json.loads(lines[0])["tags"], ["a", "b"])
        self.assertNotIn("attribution", json.loads(lines[1]))


if __name__ == "__main__":
    unittest.main()
