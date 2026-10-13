"""Regression coverage for the 1.9.11 portable alignment boundary."""
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if (ROOT / "runtime/workspace").is_dir():
    ROOT = ROOT / "runtime/workspace"

import sys
sys.path.insert(0, str(ROOT))
from tools import compiler_worker_v2 as worker
from tools import ingest_rail as rail


class Alignment1911Tests(unittest.TestCase):
    def test_visual_repair_archives_original_before_output_replacement(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR")) as folder:
            package = Path(folder)
            original = package / "image.png"
            original.write_bytes(b"first image")
            first = {
                "attempt_number": 1,
                "attempt_id": "IMGATT-1",
                "png_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
                "failure_code": "reverse_review_failed",
                "status": "blocking_failure",
            }
            kp = {"source": {"source_id": "KS-VS-0001"}}
            page = {"page_id": "IG-001", "locked_prompt_sha256": "locked"}
            authorization = {
                "schema_version": "explicit_visual_repair_v1",
                "authority": "current_user_message",
                "source_id": "KS-VS-0001",
                "page_id": "IG-001",
                "first_attempt_id": "IMGATT-1",
                "reference_png_sha256": first["png_sha256"],
                "locked_prompt_sha256": "locked",
                "scope": "remove_excess_icons_preserve_all_text",
                "additional_attempts": 1,
                "user_instruction": "Remove the extra icon only.",
                "remove_icon_description": "the extra icon",
                "reference_asset": "image.png",
            }
            preserved, archive = worker.preserve_visual_repair_reference(
                package, original, first, authorization
            )
            self.assertEqual(archive.name, "image.attempt-1-reference.png")
            original.write_bytes(b"edited image")
            self.assertEqual(
                worker.validate_visual_repair_authorization(package, kp, page, first, preserved),
                archive,
            )

    def test_direct_capture_mode_is_frozen_on_the_accepted_rail_job(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("BRAIN_TEST_TMPDIR")) as folder:
            root = Path(folder)
            source = root / "source.md"
            source.write_text("完整正文。", encoding="utf-8")
            args = SimpleNamespace(
                root=root, url=None, image_file=None, text_file=source,
                source_id="KS-VS-0001", job_id="RAIL-1911", title="測試",
                slug="alignment", profile="article", case_evidence_file=None,
                capture_mode=worker.URL_CAPTURE_DIRECT_HTML_ONLY,
            )
            identity = "user_provided_text:" + worker.sha256_bytes(source.read_bytes())
            job = worker.ensure_accepted_job(
                root=root, source_id=args.source_id, job_id=args.job_id,
                source_input_kind="user_provided_text", source_identity=identity,
            )
            job["rail_capture"] = {
                "title": args.title, "slug": args.slug, "profile": args.profile,
                "capture_mode": worker.URL_CAPTURE_DIRECT_HTML_ONLY,
                "case_evidence_file": "",
            }
            worker.write_v2_job(root, job)
            job = worker.read_json(worker.job_file(root, "RAIL-1911"))
            self.assertEqual(
                job["rail_capture"]["capture_mode"], worker.URL_CAPTURE_DIRECT_HTML_ONLY
            )
            args.capture_mode = worker.URL_CAPTURE_API_FIRST
            with self.assertRaises(worker.CompilerWorkerV2Error):
                rail.start(args)


if __name__ == "__main__":
    unittest.main()
