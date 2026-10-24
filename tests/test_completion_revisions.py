"""Completion revisions preserve immutable inputs and reject later drift."""
from __future__ import annotations

import copy
import json
import os
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
sys.path.insert(0, str(ROOT / "tools"))
import completion_revisions as revisions
from tools import compiler_worker_v2 as worker
from tools import ingest_rail as rail
from tools import ingest_plan_contract as contract
from tools import obsidian_vault_adapter as adapter
from tools import workspace_paths
from tools import youtube_transcript_capture as yt


class SyntheticDirectory:
    """A no-cleanup temporary root that keeps inherited Windows sandbox ACLs."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.path: Path | None = None

    def __enter__(self) -> str:
        base = Path(os.environ.get("BRAIN_TEST_TMPDIR") or os.environ.get("TEMP") or ".")
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{self.prefix[:1]}{uuid.uuid4().hex[:4]}"
        self.path.mkdir()
        return str(self.path)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        return False


class CompletionRevisions(unittest.TestCase):
    def test_retention_mode_selects_accepted_original_and_rejects_unknown_mode(self) -> None:
        with SyntheticDirectory("completion") as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            canonical = package / "canonical_source.md"
            canonical.write_text("archive body\n", encoding="utf-8")
            source_note = root / "source.md"
            source_note.write_text(
                "## 正文保存區\n\n### Canonical article body\n```markdown\naccepted source body\n```\n",
                encoding="utf-8",
            )
            (package / "input_bundle.json").write_text(
                json.dumps({"source_retention_mode": "full_body_required"}), encoding="utf-8",
            )
            self.assertEqual(
                revisions.original_body_for_current_source_review(
                    root, package, {"retention_mode": "summarized_allowed"}, "source.md",
                ),
                "accepted source body",
            )
            (package / "input_bundle.json").write_text(
                json.dumps({"source_retention_mode": "summarized_allowed"}), encoding="utf-8",
            )
            self.assertEqual(
                revisions.original_body_for_current_source_review(root, package, {}, "source.md"),
                "archive body",
            )
            (package / "input_bundle.json").write_text(
                json.dumps({"source_retention_mode": "unrecognized_mode"}), encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unsupported source retention mode"):
                revisions.original_body_for_current_source_review(root, package, {}, "source.md")

    def test_staged_knowledge_package_is_checked_before_any_live_write(self) -> None:
        with SyntheticDirectory("completion") as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            kp_path = package / "knowledge_package.json"
            accepted = {"source": {"source_identity": "accepted", "retention_mode": "summarized_allowed"}}
            kp_path.write_text(json.dumps(accepted), encoding="utf-8")
            (package / "canonical_source.md").write_text("accepted original\n", encoding="utf-8")
            source = root / "source.md"
            source.write_text("unchanged live source", encoding="utf-8")
            application = root / "application.md"
            application.write_text("unchanged live application", encoding="utf-8")
            review = {
                "schema_version": "current_source_review_v1",
                "scope": "current_content_not_historical_reconstruction",
                "source_identity": "accepted", "status": "accepted", "reviewer": "reviewer",
                "historical_limitations": "Current content was reviewed.",
                "unresolved_current_findings": [],
                "reviewed_files": [{"path": "application.md", "sha256": revisions.sha(application.read_bytes())}],
            }
            review["payload_sha256"] = revisions.digest(review)
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            record = {
                "current_source_review": {"path": "review.json", "sha256": revisions.sha(review_path.read_bytes())},
            }
            completion = {
                "knowledge_package_path": "package/knowledge_package.json", "source_identity": "accepted",
                "adapter": {"source_note": "source.md"},
            }
            changes = [{"current_path": "application.md", "sha256": revisions.sha(application.read_bytes())}]
            before = {path: path.read_bytes() for path in (kp_path, source, application, review_path)}
            staged = {"source": {"source_identity": "replaced", "retention_mode": "summarized_allowed"}}
            with self.assertRaisesRegex(ValueError, "current source identity changed"):
                revisions.current_source_review(
                    root, record, completion, changes,
                    virtual={"package/knowledge_package.json": json.dumps(staged).encode("utf-8")},
                )
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_accept_protection_and_drift(self) -> None:
        with SyntheticDirectory("completion") as tmp:
            root = Path(tmp)
            package = root / "package"
            package.mkdir()
            kp = package / "knowledge_package.json"
            kp.write_text(json.dumps({"source": {"retention_mode": "summarized_allowed"}}), encoding="utf-8")
            source_slice = package / "slice.md"
            source_slice.write_text("old slice", encoding="utf-8")
            original = package / "canonical_source.md"
            original.write_bytes(b"Immutable original\nSecond line\n")
            source = root / "source.md"
            source.write_text("Original source summary", encoding="utf-8")
            note = root / "application.md"
            note.write_text("old interpretation", encoding="utf-8")
            receipt = {
                "source_id": "TEST",
                "source_identity": "identity",
                "knowledge_package_path": "package/knowledge_package.json",
                "knowledge_package_sha256": revisions.sha(kp.read_bytes()),
                "source_slice_path": "package/slice.md",
                "source_slice_sha256": revisions.sha(source_slice.read_bytes()),
                "adapter": {"source_note": "source.md", "application_note": "application.md", "knowledge_points": []},
                "artifact_manifest": [
                    {"path": "source.md", "sha256": revisions.sha(source.read_bytes())},
                    {"path": "application.md", "sha256": revisions.sha(note.read_bytes())},
                    {"path": "package/canonical_source.md", "sha256": revisions.sha(original.read_bytes())},
                ],
            }
            receipt["payload_sha256"] = revisions.digest(receipt)
            receipt_path = package / "completion_receipt.json"
            receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
            original_receipt = receipt_path.read_bytes()
            proof = root / "review.txt"
            proof.write_text("Reviewed interpretation against the immutable original.", encoding="utf-8")
            note.write_text("New interpretation\nWith explicit scope.\n", encoding="utf-8")
            record = {
                "schema_version": revisions.SCHEMA,
                "completion_payload_sha256": receipt["payload_sha256"],
                "source_id": "TEST",
                "source_identity": "identity",
                "authorization": "user explicitly authorized this revision",
                "review_summary": "source-grounded scope correction",
                "previous_pointer_sha256": "",
                "protected_inputs": [{"path": "package/canonical_source.md", "sha256": revisions.sha(original.read_bytes())}],
                "changes": [{
                    "original_path": "application.md",
                    "original_sha256": receipt["artifact_manifest"][1]["sha256"],
                    "current_path": "application.md",
                    "sha256": revisions.sha(note.read_bytes()),
                    "reason": "correct interpretation",
                    "evidence": [{"path": "review.txt", "sha256": revisions.sha(proof.read_bytes()), "supports": "source comparison"}],
                }],
            }

            def signed(value: dict) -> dict:
                value["payload_sha256"] = revisions.digest(value)
                return value

            for forbidden in ("package/canonical_source.md", "package/knowledge_package.json", "package/slice.md"):
                bad = copy.deepcopy(record)
                bad["changes"][0].update(
                    original_path=forbidden,
                    original_sha256=revisions.original_files(receipt)[forbidden],
                    current_path=forbidden,
                    sha256=revisions.sha((root / forbidden).read_bytes()),
                )
                with self.assertRaises(ValueError):
                    revisions.validate(root, signed(bad), receipt)
            original_lf = original.read_bytes()
            original.write_bytes(original_lf.replace(b"\n", b"\r\n"))
            revisions.validate(root, signed(record), receipt)
            original.write_bytes(original_lf + b"changed")
            with self.assertRaises(ValueError):
                revisions.validate(root, signed(record), receipt)
            original.write_bytes(original_lf.replace(b"\n", b"\r\n"))
            revisions.accept(root, package, receipt, record)
            self.assertEqual(receipt_path.read_bytes(), original_receipt)
            self.assertTrue(revisions.read_current(root, package, receipt))
            proof.write_text("changed evidence", encoding="utf-8")
            with self.assertRaises(ValueError):
                revisions.read_current(root, package, receipt)
            self.assertEqual(receipt_path.read_bytes(), original_receipt)


class ContentRevisionApplyPreflightTests(unittest.TestCase):
    """Exercise completed-source revisions through their formal transaction path."""

    TEXT_BODY = "完整原文必須在修訂前後保持可核對，並讓來源判斷可追溯。"
    IMAGE_BODY = "完整圖片分析要保留原圖、轉錄及可追溯應用判斷。"

    def setUp(self) -> None:
        with SyntheticDirectory("content-revision") as root:
            self.root = Path(root)

    def workspace(self, name: str) -> Path:
        root = self.root / name
        root.mkdir()
        (root / "tools").mkdir()
        shutil.copy2(ROOT / "tools" / "outcome_page_generator.py", root / "tools" / "outcome_page_generator.py")
        return root

    @staticmethod
    def vault_for(root: Path) -> Path:
        return workspace_paths.vault_path(root)

    @staticmethod
    def accepted_review(prepared: dict, path: Path) -> dict:
        review = copy.deepcopy(prepared["review_template"])
        review.update(status="accepted", reviewer="SYNTHETIC-TEST-ONLY")
        review["payload_sha256"] = revisions.digest(review)
        worker.write_json(path, review)
        return review

    @staticmethod
    def target_bytes(root: Path, item: dict) -> bytes:
        path = root / item["path"]
        return path.read_bytes() if path.exists() else b""

    @staticmethod
    def transaction_bytes(path: Path) -> dict[str, bytes]:
        return {
            item.relative_to(path).as_posix(): item.read_bytes()
            for item in path.rglob("*") if item.is_file()
        }

    @staticmethod
    def tamper_canonical_body(text: str, suffix: str) -> str:
        body = adapter.extract_canonical_body_markdown(text)
        if not body:
            raise AssertionError("fixture source note lacks a canonical body")
        start = text.index(body, text.index("### Canonical article body"))
        return text[:start] + body + suffix + text[start + len(body):]

    @staticmethod
    def without_canonical_body(text: str) -> str:
        body = adapter.extract_canonical_body_markdown(text)
        if not body:
            raise AssertionError("fixture source note lacks a canonical body")
        start = text.index(body, text.index("### Canonical article body"))
        return text[:start] + text[start + len(body):]

    def compact_plan(self, package: Path, *, claim: str, summary: str) -> dict:
        version = contract.CURRENT_SOURCE_CATALOG_VERSION
        return {
            "schema_version": contract.COMPACT_VERSION,
            "input_fingerprint": contract.input_fingerprint(package, version),
            "source_catalog_version": version,
            "summary": [summary],
            "knowledge": [{
                "title": "可追溯修訂",
                "claim": claim,
                "layer": "原文主張",
                "confidence": "高",
                "evidence_block_ids": ["SRC-001"],
                "reusable_concept": {"status": "not_reusable", "reason": "此合成測試只驗證修訂交易。"},
            }],
            "application": {"knowledge_use": "學習材料"},
        }

    def complete_text_source(self) -> tuple[Path, Path, Path]:
        root = self.workspace("text")
        source = root / "source.md"
        source.write_text(self.TEXT_BODY, encoding="utf-8")
        job_id = "T"
        rail.start(SimpleNamespace(
            root=root, url=None, image_file=None, text_file=source,
            source_id="KS-VS-1001", job_id=job_id, title="合成文字來源",
            slug="t", profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
        ))
        _, package, kp_path = rail.paths(root, job_id)
        response_path = root / "text-plan.json"
        worker.write_json(response_path, self.compact_plan(
            package, claim=self.TEXT_BODY, summary="此合成文字來源只驗證內容修訂的正式交易。",
        ))
        rail.measured(root, job_id, "submit_plan", rail.submit_plan,
                      root=root, job_id=job_id, response_path=response_path)
        result = rail.advance(root, job_id, self.vault_for(root))
        self.assertEqual(result["status"], "completed", result)
        return root, package, kp_path

    def prepare_text_revision(self) -> tuple[Path, Path, Path, dict, Path, Path]:
        root, package, kp_path = self.complete_text_source()
        plan = self.compact_plan(
            package, claim=self.TEXT_BODY, summary="此修訂只改正已保存的應用判斷。",
        )
        plan["application"] = {
            "knowledge_use": "學習材料",
            "adoption_reason": "修訂應用判斷，不改變原文或來源身分。",
        }
        plan_path = root / "text-revision-plan.json"
        worker.write_json(plan_path, plan)
        prepared = revisions.prepare_content_revision(
            root, kp_path, plan_path,
            authorization="Explicit synthetic text content-revision authorization.",
            reason="Correct only the saved application judgement for this completed text source.",
        )
        review_path = root / "text-accepted-review.json"
        self.accepted_review(prepared, review_path)
        return root, package, kp_path, prepared, root / prepared["revision_file"], review_path

    def current_review_record(self, root: Path, completion: dict) -> dict:
        review = {
            "schema_version": "current_source_review_v1",
            "scope": "current_content_not_historical_reconstruction",
            "source_identity": completion["source_identity"],
            "status": "accepted",
            "reviewer": "SYNTHETIC-TEST-ONLY",
            "historical_limitations": ["Synthetic direct current-source review only."],
            "unresolved_current_findings": [],
            "reviewed_files": [],
        }
        review["payload_sha256"] = revisions.digest(review)
        path = root / "direct-current-source-review.json"
        worker.write_json(path, review)
        return {"current_source_review": {
            "path": path.relative_to(root).as_posix(), "sha256": revisions.sha(path.read_bytes()),
        }}

    def prepare_image_revision(self) -> tuple[Path, Path, Path, dict, Path, Path]:
        from PIL import Image

        root = self.workspace("image")
        image = root / "image-source.png"
        Image.new("RGB", (320, 240), "white").save(image)
        job_id = "I"
        order = rail.start(SimpleNamespace(
            root=root, url=None, image_file=image, text_file=None,
            source_id="KS-VS-1002", job_id=job_id, title="合成原圖契約",
            slug="i", profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
        ))
        response = order["response_template"]
        analysis = response["image_analysis"]
        analysis.update(
            analysis_status="complete", checker_status="passed",
            checker_evidence="Synthetic fixture only; not actual image reading.",
            visual_type="document_scan", title="合成原圖契約", summary=self.IMAGE_BODY,
        )
        analysis["anchors"][0].update(kind="text", evidence=self.IMAGE_BODY)
        analysis["transcription"][0].update(text=self.IMAGE_BODY, confidence="high")
        analysis["source_claims"][0].update(claim=self.IMAGE_BODY, verification_status="image_visible")
        analysis["coverage"][0].update(disposition="knowledge_point", reason="只驗證圖片來源的修訂交易。")
        response_path = root / "image-analysis-response.json"
        worker.write_json(response_path, response)
        next_order = rail.submit_image_analysis(root, job_id, response_path)
        semantic = worker.read_json(root / next_order["draft_file"])
        semantic.update(
            summary=["合成圖片來源的應用判斷修訂。"],
            knowledge=[{
                "title": "合成原圖證據", "claim": self.IMAGE_BODY,
                "layer": "原文主張", "confidence": "高", "evidence_block_ids": ["IMG-001"],
                "reusable_concept": {"status": "not_reusable", "reason": "此測試只驗證內容修訂。"},
            }],
            application={"knowledge_use": "學習材料"},
        )
        semantic_path = root / "image-semantic-response.json"
        worker.write_json(semantic_path, semantic)
        rail.submit_plan(root, job_id, semantic_path)
        _, package, kp_path = rail.paths(root, job_id)
        completed = rail.advance(root, job_id, self.vault_for(root))
        self.assertEqual(completed["status"], "completed", completed)
        plan = copy.deepcopy(semantic)
        plan["input_fingerprint"] = contract.input_fingerprint(package, plan.get("source_catalog_version"))
        plan["application"] = {
            "knowledge_use": "學習材料",
            "adoption_reason": "修訂應用判斷，不改動圖片來源或其分析。",
        }
        plan_path = root / "image-application-revision.json"
        worker.write_json(plan_path, plan)
        prepared = revisions.prepare_content_revision(
            root, kp_path, plan_path,
            authorization="Explicit synthetic image content-revision authorization.",
            reason="Correct only the saved application judgement for this completed image source.",
        )
        review_path = root / "image-accepted-review.json"
        self.accepted_review(prepared, review_path)
        return root, package, kp_path, prepared, root / prepared["revision_file"], review_path

    def prepare_transcript_archive_revision(self) -> tuple[Path, Path, dict, Path, Path]:
        root = self.workspace("transcript")
        job_id = "Y"
        url, video_id = "https://www.youtube.com/watch?v=abcDEF12345", "abcDEF12345"
        captions = [
            "Today we build a small tool that checks every invoice.",
            "It reads the file and then compares the totals.",
            "If the totals differ it writes a short note.",
            "The note names the supplier and the amount.",
            "Nothing is sent until a person approves it.",
            "That approval step is the most important part.",
            "A second check runs again each morning.",
            "It keeps a log so errors are easy to trace.",
            "Small tools like this save a lot of time.",
            "Start with one supplier and grow from there.",
        ]
        vtt = ("WEBVTT\n\n" + "".join(
            f"00:00:{index * 3:02d}.000 --> 00:00:{(index + 1) * 3:02d}.000\n{line}\n\n"
            for index, line in enumerate(captions)
        )).encode("utf-8")
        capture = yt.YoutubeTranscriptCapture(
            normalized_url=url, video_id=video_id, title="Invoice tool", channel="Fixture", upload_date="20261008",
            selected_language="en", selected_track_kind="automatic", selected_track_group="en-orig",
            selected_track_item_index=0, selected_track_item_language="en", raw_vtt=vtt,
            raw_vtt_filename="source.en.vtt",
            info_json=json.dumps({"id": video_id, "title": "Invoice tool", "language": "en",
                                  "automatic_captions": {"en-orig": [{"ext": "vtt", "url": "https://captions.example/track?lang=en"}]}},
                                 ensure_ascii=False).encode("utf-8"),
            canonical_body=yt.vtt_to_canonical_body(vtt, normalized_url=url, video_id=video_id,
                                                     language="en", track_kind="automatic"),
            yt_dlp_version="fixture",
        )
        with patch.object(rail.worker, "capture_youtube_transcript", return_value=capture):
            pointer = rail.start(SimpleNamespace(
                root=root, url=url, image_file=None, text_file=None,
                source_id="KS-VS-1003", job_id=job_id, title=None,
                slug="y", profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
            ))
        self.assertEqual(pointer["status"], "needs_input", pointer)
        _, package, kp_path = rail.paths(root, job_id)
        order = worker.read_json(root / pointer["work_order_file"])
        claim = "Nothing is sent until a person approves it."
        block = next(block for block in contract.source_block_catalog(
            package, version=order["source_catalog_version"])["blocks"] if claim in block["text"])
        plan = {
            "schema_version": contract.COMPACT_VERSION,
            "input_fingerprint": order["input_fingerprint"],
            "source_catalog_version": order["source_catalog_version"],
            "summary": ["此測試只檢查字幕存檔模式的內容修訂。"],
            "knowledge": [{
                "title": "發送前的人手批准", "claim": claim,
                "layer": "原文方法", "confidence": "高", "evidence_block_ids": [block["id"]],
                "reusable_concept": {"status": "not_reusable", "reason": "此測試只驗證字幕存檔的內容修訂。"},
            }],
            "application": {"knowledge_use": "學習材料"},
        }
        plan_path = root / "transcript-plan.json"
        worker.write_json(plan_path, plan)
        rail.measured(root, job_id, "submit_plan", rail.submit_plan,
                      root=root, job_id=job_id, response_path=plan_path)
        completed = rail.advance(root, job_id, self.vault_for(root))
        self.assertEqual(completed["status"], "completed", completed)
        plan["knowledge"][0].update(title="修訂後的人手批准", reusable_concept={
            "status": "reusable", "name": "人手批准閘門", "reason": "此測試只驗證字幕存檔的內容修訂。",
        })
        revision_plan = root / "transcript-revision-plan.json"
        worker.write_json(revision_plan, plan)
        prepared = revisions.prepare_content_revision(
            root, kp_path, revision_plan,
            authorization="Explicit synthetic transcript content-revision authorization.",
            reason="Correct only the saved application judgement for this completed transcript source.",
        )
        review_path = root / "transcript-accepted-review.json"
        self.accepted_review(prepared, review_path)
        return root, package, prepared, root / prepared["revision_file"], review_path

    def test_image_content_revision_validates_then_completes(self) -> None:
        root, package, kp_path, prepared, journal_path, review_path = self.prepare_image_revision()
        before = {item["path"]: self.target_bytes(root, item) for item in prepared["changes"]}
        transaction_before = self.transaction_bytes(journal_path.parent)
        dry = revisions.apply_content_revision(root, journal_path, review_path, dry_run=True)
        self.assertEqual((dry["status"], dry["write_performed"]), ("reviewed_ready", False))
        self.assertEqual(before, {item["path"]: self.target_bytes(root, item) for item in prepared["changes"]})
        self.assertEqual(transaction_before, self.transaction_bytes(journal_path.parent))
        self.assertEqual(revisions.apply_content_revision(root, journal_path, review_path)["status"], "completed")
        completion = worker.read_json(package / "completion_receipt.json")
        record = revisions.read_current(root, package, completion, include_record=True)
        self.assertEqual(record["source_identity"], completion["source_identity"])
        self.assertEqual(worker.completed_receipt_readback(root, package, kp_path, worker.read_json(kp_path))["source_id"], "KS-VS-1002")

    def test_transcript_archive_content_revision_validates_then_completes(self) -> None:
        root, package, prepared, journal_path, review_path = self.prepare_transcript_archive_revision()
        self.assertEqual(revisions.apply_content_revision(root, journal_path, review_path)["status"], "completed")
        completion = worker.read_json(package / "completion_receipt.json")
        self.assertTrue(revisions.read_current(root, package, completion, include_record=True))

    def test_full_body_required_source_note_without_body_is_blocked(self) -> None:
        root, package, _ = self.complete_text_source()
        completion = worker.read_json(package / "completion_receipt.json")
        note_path = root / completion["adapter"]["source_note"]
        note_path.write_text(self.without_canonical_body(note_path.read_text(encoding="utf-8-sig")), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current source review changed or lost original body"):
            revisions.current_source_review(root, self.current_review_record(root, completion), completion, [])

    def test_unknown_source_retention_mode_is_blocked(self) -> None:
        root, package, _ = self.complete_text_source()
        completion = worker.read_json(package / "completion_receipt.json")
        bundle_path = package / "input_bundle.json"
        bundle = worker.read_json(bundle_path)
        bundle["source_retention_mode"] = "unrecognized_retention_mode"
        worker.write_json(bundle_path, bundle)
        with self.assertRaisesRegex(ValueError, "unsupported source retention mode: unrecognized_retention_mode"):
            revisions.current_source_review(root, self.current_review_record(root, completion), completion, [])

    def test_text_canonical_body_tamper_remains_blocked(self) -> None:
        root, package, _, _, journal_path, review_path = self.prepare_text_revision()
        self.assertEqual(revisions.apply_content_revision(root, journal_path, review_path)["status"], "completed")
        completion = worker.read_json(package / "completion_receipt.json")
        source = root / completion["adapter"]["source_note"]
        source.write_text(self.tamper_canonical_body(source.read_text(encoding="utf-8-sig"), "\n篡改原文。"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current source review changed or lost original body"):
            revisions.read_current(root, package, completion)

    def test_preflight_source_candidate_failure_writes_nothing_for_dry_run_and_apply(self) -> None:
        root, _, _, _, journal_path, review_path = self.prepare_text_revision()
        journal = worker.read_json(journal_path)
        completion = worker.read_json(root / journal["completion_path"])
        source_item = next(item for item in journal["items"] if item["path"] == completion["adapter"]["source_note"])
        candidate = root / source_item["candidate_path"]
        candidate.write_text(self.tamper_canonical_body(candidate.read_text(encoding="utf-8-sig"), "\n篡改候選原文。"), encoding="utf-8")
        source_item["after_sha256"] = revisions.sha(candidate.read_bytes())
        review = worker.read_json(review_path)
        for item in review["reviewed_files"]:
            if item["path"] == source_item["path"]:
                item["sha256"] = source_item["after_sha256"]
        review["payload_sha256"] = revisions.digest(review)
        worker.write_json(review_path, review)
        revisions._save_json(journal_path, journal)
        targets_before = {item["path"]: self.target_bytes(root, item) for item in journal["items"]}
        journal_before = journal_path.read_bytes()
        transaction_before = self.transaction_bytes(journal_path.parent)
        transaction_review = revisions._review_file_path(journal_path.parent, review)
        for dry_run in (True, False):
            with self.assertRaisesRegex(ValueError, "current source review changed or lost original body"):
                revisions.apply_content_revision(root, journal_path, review_path, dry_run=dry_run)
            self.assertEqual(targets_before, {item["path"]: self.target_bytes(root, item) for item in journal["items"]})
            self.assertEqual(journal_path.read_bytes(), journal_before)
            self.assertEqual(transaction_before, self.transaction_bytes(journal_path.parent))
            self.assertFalse(transaction_review.exists())

    def test_kp_only_bad_candidate_is_blocked_before_any_write(self) -> None:
        root, _, _, _, journal_path, review_path = self.prepare_text_revision()
        journal = worker.read_json(journal_path)
        kp_item = next(item for item in journal["items"] if item["path"] == journal["kp_path"])
        candidate = root / kp_item["candidate_path"]
        kp = worker.read_json(candidate)
        original_sha256 = kp["source"]["canonical_body_sha256"]
        kp["source"]["canonical_body_sha256"] = ("0" if original_sha256[0] != "0" else "1") * 64
        worker.write_json(candidate, kp)
        kp_item["after_sha256"] = revisions.sha(candidate.read_bytes())
        review = worker.read_json(review_path)
        for item in review["reviewed_files"]:
            if item["path"] == kp_item["path"]:
                item["sha256"] = kp_item["after_sha256"]
        review["payload_sha256"] = revisions.digest(review)
        worker.write_json(review_path, review)
        revisions._save_json(journal_path, journal)
        targets_before = {item["path"]: self.target_bytes(root, item) for item in journal["items"]}
        journal_before = journal_path.read_bytes()
        transaction_before = self.transaction_bytes(journal_path.parent)
        transaction_review = revisions._review_file_path(journal_path.parent, review)
        for dry_run in (True, False):
            with self.assertRaisesRegex(ValueError, "current source review lacks intact accepted input"):
                revisions.apply_content_revision(root, journal_path, review_path, dry_run=dry_run)
            self.assertEqual(targets_before, {item["path"]: self.target_bytes(root, item) for item in journal["items"]})
            self.assertEqual(journal_path.read_bytes(), journal_before)
            self.assertEqual(transaction_before, self.transaction_bytes(journal_path.parent))
            self.assertFalse(transaction_review.exists())

    def test_applying_transaction_at_before_bytes_resumes_with_existing_review(self) -> None:
        root, package, _, _, journal_path, review_path = self.prepare_text_revision()
        journal = worker.read_json(journal_path)
        review = worker.read_json(review_path)
        transaction_review = revisions._review_file_path(journal_path.parent, review)
        transaction_review.write_bytes(revisions._json_bytes(review))
        journal.update(state="applying", review_sha256=revisions.sha(transaction_review.read_bytes()))
        revisions._save_json(journal_path, journal)
        self.assertTrue(all(revisions.sha(self.target_bytes(root, item)) == item["before_sha256"] for item in journal["items"]))
        targets_before = {item["path"]: self.target_bytes(root, item) for item in journal["items"]}
        transaction_before = self.transaction_bytes(journal_path.parent)
        dry = revisions.apply_content_revision(root, journal_path, review_path, dry_run=True)
        self.assertEqual((dry["status"], dry["write_performed"]), ("reviewed_ready", False))
        self.assertEqual(targets_before, {item["path"]: self.target_bytes(root, item) for item in journal["items"]})
        self.assertEqual(transaction_before, self.transaction_bytes(journal_path.parent))
        self.assertEqual(revisions.apply_content_revision(root, journal_path, review_path)["status"], "completed")
        completion = worker.read_json(package / "completion_receipt.json")
        self.assertTrue(revisions.read_current(root, package, completion, include_record=True))


if __name__ == "__main__":
    unittest.main()
