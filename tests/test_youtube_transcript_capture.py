"""Offline counterexamples for selecting one declared-source YouTube caption (verified VTT item contract)."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(WORKSPACE))

from tools import compiler_worker_v2 as worker  # noqa: E402
from tools import ingest_rail as rail  # noqa: E402
from tools import youtube_transcript_capture as transcript  # noqa: E402


URL = "https://youtu.be/0zM3nApSvMg?utm_source=fixture"
CANONICAL_URL = "https://www.youtube.com/watch?v=0zM3nApSvMg"
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nFirst caption line.\n\n00:00:02.000 --> 00:00:04.000\nSecond caption line.\n"
def caption_item(language: str, *, translated_to: str | None = None, ext: str = "vtt") -> dict[str, str]:
    query = f"lang={language}"
    if translated_to:
        query += f"&tlang={translated_to}"
    return {"ext": ext, "url": f"https://captions.example/track?{query}"}


INFO = {
    "id": "0zM3nApSvMg",
    "title": "Caption fixture",
    "channel": "Fixture channel",
    "upload_date": "20261008",
    "language": "en",
    "subtitles": {"en": [caption_item("en")]},
}


def fixture_capture() -> transcript.YoutubeTranscriptCapture:
    canonical = transcript.vtt_to_canonical_body(
        VTT, normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", language="en", track_kind="manual",
    )
    return transcript.YoutubeTranscriptCapture(
        normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", title="Caption fixture", channel="Fixture channel",
        upload_date="20261008", selected_language="en", selected_track_kind="manual",
        selected_track_group="en", selected_track_item_index=0, selected_track_item_language="en", raw_vtt=VTT,
        raw_vtt_filename="source.en.vtt", info_json=json.dumps(INFO).encode("utf-8"), canonical_body=canonical,
        yt_dlp_version="fixture",
    )


def test_caption_command_is_media_free() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-caption-"))
    try:
        def fake_run(command, *, cwd, **kwargs):
            target = Path(cwd)
            (target / "source.info.json").write_text(json.dumps(INFO), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_download(info, track, *, destination, timeout_seconds):
            assert transcript._selected_item(info, track) == caption_item("en")
            destination.write_bytes(VTT)

        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run) as run, patch.object(transcript, "_download_verified_vtt", side_effect=fake_download) as download:
            captured = transcript.capture_youtube_transcript(URL, temporary_root=root)
        assert run.call_count == 1 and download.call_count == 1
        metadata_command = run.call_args.args[0]
        assert "--skip-download" in metadata_command and "--write-info-json" in metadata_command
        assert "--write-subs" not in metadata_command and "--write-auto-subs" not in metadata_command
        assert "--sub-langs" not in metadata_command and "--load-info-json" not in metadata_command
        assert not {"--extract-audio", "--format", "--write-thumbnail", "--write-description"} & set(metadata_command)
        assert captured.normalized_url == CANONICAL_URL
        assert captured.selected_track_kind == "manual"
        assert "[00:00:02.000 --> 00:00:04.000] Second caption line." in captured.canonical_body
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mixed_automatic_group_pins_original_item_for_download() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-mixed-download-"))
    mixed = {**INFO, "language": "en-US", "subtitles": {}, "automatic_captions": {
        "en": [caption_item("en"), caption_item("uk", translated_to="en")],
    }}
    try:
        def fake_run(command, *, cwd, **kwargs):
            target = Path(cwd)
            (target / "source.info.json").write_text(json.dumps(mixed), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def fake_download(info, track, *, destination, timeout_seconds):
            assert (track.group_key, track.item_index, track.item_language, track.track_kind) == ("en", 0, "en", "automatic")
            assert transcript._selected_item(info, track) == caption_item("en")
            destination.write_bytes(VTT.replace(b"First", b"Original"))

        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run) as run, patch.object(transcript, "_download_verified_vtt", side_effect=fake_download) as download:
            captured = transcript.capture_youtube_transcript(URL, temporary_root=root)
        assert run.call_count == 1 and download.call_count == 1
        assert "--load-info-json" not in run.call_args.args[0]
        assert captured.selected_track_kind == "automatic"
        assert (captured.selected_track_group, captured.selected_track_item_index, captured.selected_track_item_language) == ("en", 0, "en")
        assert "Original caption line." in captured.canonical_body
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_verified_vtt_download_cannot_reextract_video() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-direct-vtt-"))
    item = caption_item("en")
    info = {**INFO, "subtitles": {"en": [item]}}
    track = transcript._source_track(info)
    calls: list[tuple[str, str]] = []
    unexpected: list[str] = []

    class Request:
        def __init__(self, url, *, headers):
            self.url, self.headers = url, headers

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return VTT

    class YoutubeDL:
        def __init__(self, options):
            assert options["socket_timeout"] == 7

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def urlopen(self, request):
            calls.append(("urlopen", request.url))
            return Response()

        def extract_info(self, *_args, **_kwargs):
            unexpected.append("extract_info")
            raise AssertionError("fresh extraction is prohibited")

        def download(self, *_args, **_kwargs):
            unexpected.append("download")
            raise AssertionError("fresh video download is prohibited")

    fake_ytdlp, fake_networking = ModuleType("yt_dlp"), ModuleType("yt_dlp.networking")
    fake_ytdlp.YoutubeDL, fake_networking.Request = YoutubeDL, Request
    destination = root / "source.en.vtt"
    try:
        with patch.dict(sys.modules, {"yt_dlp": fake_ytdlp, "yt_dlp.networking": fake_networking}):
            transcript._download_verified_vtt(info, track, destination=destination, timeout_seconds=7)
        assert destination.read_bytes() == VTT
        assert calls == [("urlopen", item["url"])] and unexpected == []

        class FailingYoutubeDL(YoutubeDL):
            def urlopen(self, request):
                calls.append(("urlopen", request.url))
                raise RuntimeError("failed https://signed.example/vtt?sig=private C:\\private")

        fake_ytdlp.YoutubeDL = FailingYoutubeDL
        failed_destination = root / "failed.vtt"
        with patch.dict(sys.modules, {"yt_dlp": fake_ytdlp, "yt_dlp.networking": fake_networking}):
            try:
                transcript._download_verified_vtt(info, track, destination=failed_destination, timeout_seconds=7)
            except transcript.YoutubeTranscriptError as exc:
                assert exc.diagnostic()["stage"] == "verified_vtt"
                assert "private" not in str(exc) and "sig=private" not in str(exc)
            else:
                raise AssertionError("verified VTT failure was accepted")
        assert not failed_destination.exists()
        assert calls == [("urlopen", item["url"]), ("urlopen", item["url"])] and unexpected == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_empty_clear_cue_is_not_source_text() -> None:
    body = transcript.vtt_to_canonical_body(
        b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n \nVisible text.\n\n00:00:01.000 --> 00:00:01.010 align:start position:0%\n \n",
        normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", language="en", track_kind="automatic",
    )
    assert "Visible text." in body and body.count("[") == 1
    note = transcript.vtt_to_canonical_body(
        b"WEBVTT\n\n00:00:00.000 --> 00:00:00.010\n\nNOTE editor-only\nDO NOT INCLUDE AS SOURCE\n\n00:00:01.000 --> 00:00:02.000\nVisible after note.\n",
        normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", language="en", track_kind="automatic",
    )
    assert "Visible after note." in note and "DO NOT INCLUDE AS SOURCE" not in note


def expect_transcript_error(callable_) -> None:
    try:
        callable_()
    except transcript.YoutubeTranscriptError:
        return
    raise AssertionError("invalid transcript input was accepted")


def test_strict_vtt_blocks_and_short_timestamps() -> None:
    valid = (
        b"WEBVTT Caption fixture\nHeader: allowed\n\n"
        b"NOTE this whole block is ignored\nnot source text\n\n"
        b"cue-one\n00:00.000 --> 00:01.000\nFirst cue.\n\n"
        b"STYLE cue-two\n00:01.000 --> 00:02.000\nReserved-looking cue remains source text.\n\n"
        b"STYLE\n::cue { color: lime; }\n\n"
        b"00:02.000 --> 00:03.000\nSecond cue.\n"
    )
    body = transcript.vtt_to_canonical_body(valid, normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", language="en", track_kind="manual")
    assert "[00:00.000 --> 00:01.000] First cue." in body
    assert "[00:01.000 --> 00:02.000] Reserved-looking cue remains source text." in body
    assert "[00:02.000 --> 00:03.000] Second cue." in body
    assert "cue-one" not in body and "not source text" not in body and "color: lime" not in body
    for invalid in (
        b"not-webvtt\n\n00:00.000 --> 00:01.000\ntext\n",
        b"WEBVTT\n\n00:00.000 --> 00:01.00\ntext\n",
        b"WEBVTT\n\n00:00.000 --> 00:01.000\ntext\n00:01.000 --> 00:02.000\n",
    ):
        expect_transcript_error(lambda invalid=invalid: transcript.vtt_to_canonical_body(invalid, normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", language="en", track_kind="manual"))


def test_youtube_time_anchors_are_not_article_coverage_sections() -> None:
    body = fixture_capture().canonical_body
    assert worker.is_youtube_caption_transcript(body) is True
    assert worker.extracted_required_units(body) == []
    assert worker.extracted_required_sections(body) == []


def test_metadata_identity_and_track_kind_are_strict() -> None:
    automatic_original = {"language": "en-US", "automatic_captions": {
        "en": [caption_item("en"), caption_item("uk", translated_to="en")],
        "en-orig": [caption_item("en")],
    }}
    selected = transcript._source_track(automatic_original)
    assert (selected.group_key, selected.item_index, selected.item_language, selected.track_kind) == ("en-orig", 0, "en", "automatic")
    assert transcript._track_kind("en-orig", automatic_original) == "automatic"

    # When no dynamic *-orig item exists, choose the one non-translated VTT item,
    # not the translated item that happens to occur last in the same group.
    mixed_group = {"language": "en-US", "automatic_captions": {"en": [caption_item("en"), caption_item("uk", translated_to="en")]}}
    selected = transcript._source_track(mixed_group)
    assert (selected.group_key, selected.item_index, selected.item_language, selected.track_kind) == ("en", 0, "en", "automatic")

    manual_first = {"language": "en", "subtitles": {"en": [caption_item("en")]}, "automatic_captions": {"en-orig": [caption_item("en")]}}
    assert transcript._source_track(manual_first).track_kind == "manual"

    # Script variants are independent: no Traditional/Simplified interchange.
    traditional = {"language": "zh-Hant", "automatic_captions": {"zh-Hant": [caption_item("zh-Hant")], "zh-Hans": [caption_item("zh-Hans")]}}
    simplified = {"language": "zh-Hans", "automatic_captions": {"zh-Hant": [caption_item("zh-Hant")], "zh-Hans": [caption_item("zh-Hans")]}}
    assert transcript._source_track(traditional).group_key == "zh-Hant"
    assert transcript._source_track(simplified).group_key == "zh-Hans"
    expect_transcript_error(lambda: transcript._source_track({"original_language": "ja", "language": "en", "automatic_captions": {"en": [caption_item("ja", translated_to="en")]}}))
    expect_transcript_error(lambda: transcript._source_track({"language": "en", "subtitles": {"en": [{"ext": "vtt", "lang": "en", "url": "https://captions.example/track"}]}}))
    expect_transcript_error(lambda: transcript._track_kind("zz", {"subtitles": {"en": [caption_item("en")]}}))
    root = Path(tempfile.mkdtemp(prefix="youtube-metadata-"))
    try:
        def fake_run(command, *, cwd, **kwargs):
            target = Path(cwd)
            (target / "source.info.json").write_text(json.dumps({**INFO, "id": ""}), encoding="utf-8")
            (target / "source.en.vtt").write_bytes(VTT)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run) as run:
            expect_transcript_error(lambda: transcript.capture_youtube_transcript(URL, temporary_root=root))
        assert run.call_count == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_verified_original_vtt_stops_before_download_and_writer() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-no-original-"))
    translated_only = {**INFO, "subtitles": {}, "automatic_captions": {"en": [caption_item("uk", translated_to="en")]}}
    try:
        def fake_run(command, *, cwd, **kwargs):
            assert "--load-info-json" not in command
            (Path(cwd) / "source.info.json").write_text(json.dumps(translated_only), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run) as run:
            expect_transcript_error(lambda: transcript.capture_youtube_transcript(URL, temporary_root=root))
        assert run.call_count == 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_caption_failure_diagnostic_is_bounded_and_safe() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-diagnostic-"))
    try:
        with patch.object(transcript.subprocess, "run", return_value=SimpleNamespace(returncode=17, stdout="", stderr="failed https://signed.example/vtt?sig=private C:\\Users\\fixture\\private")):
            try:
                transcript._run(["yt-dlp"], work=root, timeout_seconds=1, stage="verified_vtt")
            except transcript.YoutubeTranscriptError as exc:
                assert exc.diagnostic() == {"stage": "verified_vtt", "exit_code": 17, "summary": exc.safe_summary}
                assert not isinstance(exc, transcript.YoutubeCaptionsUnavailable)
                assert "sig=private" not in str(exc) and "private" not in str(exc)
            else:
                raise AssertionError("nonzero yt-dlp exit was accepted")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_capture_diagnostic_survives_compiler_and_existing_job_event() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-capture-diagnostic-"))
    diagnostic = {"stage": "verified_vtt", "exit_code": 17, "summary": "<redacted-url> failed"}
    source_error = transcript.YoutubeTranscriptError(
        "yt-dlp verified_vtt failed (exit 17; <redacted-url> failed)",
        stage=diagnostic["stage"], exit_code=diagnostic["exit_code"], safe_summary=diagnostic["summary"],
    )
    args = SimpleNamespace(
        root=root, url=URL, source_id="KS-VS-YT-DIAGNOSTIC", job_id="YT-DIAGNOSTIC",
        title=None, slug=None, profile="article", asset_mode=worker.ASSET_MODE_KNOWLEDGE,
        capture_mode=worker.URL_CAPTURE_API_FIRST, case_evidence_file=None, youtube_capture=None,
        timeout_seconds=1,
    )
    try:
        with patch.object(worker, "capture_youtube_transcript", side_effect=source_error):
            try:
                worker.command_accept_url(args)
            except worker.CompilerWorkerV2Error as exc:
                assert exc.diagnostic == diagnostic
                compiler_error = exc
            else:
                raise AssertionError("YouTube capture error was accepted")
        worker.write_v2_job(root, {"job_id": args.job_id, "rail_events": []})
        try:
            rail.measured(root, args.job_id, "capture", lambda: (_ for _ in ()).throw(compiler_error))
        except worker.CompilerWorkerV2Error:
            pass
        else:
            raise AssertionError("capture failure was accepted")
        saved = worker.read_json(worker.job_file(root, args.job_id))
        event = saved["rail_events"][-1]
        assert event["status"] == "failed" and event["capture_diagnostic"] == diagnostic
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_metadata_json_must_be_an_object() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-metadata-array-"))
    try:
        def fake_run(command, *, cwd, **kwargs):
            target = Path(cwd)
            (target / "source.info.json").write_text("[]", encoding="utf-8")
            (target / "source.en.vtt").write_bytes(VTT)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run):
            expect_transcript_error(lambda: transcript.capture_youtube_transcript(URL, temporary_root=root))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_no_caption_stops_before_compiler_job() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-no-caption-"))
    try:
        args = SimpleNamespace(root=root, url=URL, image_file=None, text_file=None, source_id=None, job_id=None, title=None, slug=None, profile="article")
        with patch.object(worker, "capture_youtube_transcript", side_effect=worker.YoutubeCaptionsUnavailable("youtube captions unavailable")):
            result = rail.start(args)
        assert result["status"] == "blocked"
        assert result["stage"] == "youtube_caption_preflight"
        assert result["writer_started"] is False and result["compiler_job_created"] is False
        assert not (root / "outputs" / "compiler_worker_v2" / "_jobs").exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_caption_capture_uses_existing_url_pipeline_and_replays_vtt() -> None:
    root = Path(tempfile.mkdtemp(prefix="youtube-accepted-"))
    try:
        args = SimpleNamespace(root=root, url=URL, image_file=None, text_file=None, source_id="KS-VS-YOUTUBE", job_id="YT-FIXTURE", title=None, slug="caption-fixture", profile="article")
        with patch.object(worker, "capture_youtube_transcript", return_value=fixture_capture()), patch.object(worker, "capture_url_api_first") as article_capture:
            order = rail.start(args)
        assert order["action"] == "prepare_semantic_plan"
        article_capture.assert_not_called()
        _, package, _ = rail.paths(root, "YT-FIXTURE")
        bundle = worker.read_json(package / "input_bundle.json")
        assert bundle["source_input_kind"] == "url"
        assert bundle["normalized_url"] == CANONICAL_URL
        assert bundle["acquisition_policy"] == transcript.CAPTURE_POLICY
        assert bundle["submitted_url_attestation"] == transcript.ATTESTATION
        assert (bundle["youtube_caption_group_key"], bundle["youtube_caption_item_index"], bundle["youtube_caption_item_language"], bundle["youtube_caption_non_translated"]) == ("en", 0, "en", True)
        assert (package / "source_capture_youtube.vtt").read_bytes() == VTT
        assert (package / "source_capture_youtube.info.json").is_file()
        # A new knowledge_only YouTube caption job keeps its transcript in the
        # package archive; the program fixes that mode at acceptance.
        assert bundle["source_retention_mode"] == transcript.TRANSCRIPT_ARCHIVE_MODE
        kp = {"source": {
            "source_input_kind": "url", "source_identity": bundle["source_identity"],
            "retention_mode": bundle["source_retention_mode"], "canonical_body_sha256": bundle["canonical_body_sha256"],
        }}
        assert worker.accepted_body_integrity_issues(kp, package) == []
        kp["source"]["retention_mode"] = "full_body_required"
        assert any("transcript archive" in issue[1] for issue in worker.accepted_body_integrity_issues(kp, package))
        kp["source"]["retention_mode"] = bundle["source_retention_mode"]
        with patch.object(worker, "capture_youtube_transcript", side_effect=AssertionError("same source must reuse its accepted package")):
            assert rail.start(args)["job_id"] == "YT-FIXTURE"
        (package / "source_capture_youtube.vtt").write_bytes(VTT.replace(b"Second", b"Changed"))
        assert any("VTT is missing or changed" in issue[1] for issue in worker.accepted_body_integrity_issues(kp, package))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    test_caption_command_is_media_free()
    test_mixed_automatic_group_pins_original_item_for_download()
    test_verified_vtt_download_cannot_reextract_video()
    test_empty_clear_cue_is_not_source_text()
    test_strict_vtt_blocks_and_short_timestamps()
    test_youtube_time_anchors_are_not_article_coverage_sections()
    test_metadata_identity_and_track_kind_are_strict()
    test_metadata_json_must_be_an_object()
    test_no_verified_original_vtt_stops_before_download_and_writer()
    test_caption_failure_diagnostic_is_bounded_and_safe()
    test_capture_diagnostic_survives_compiler_and_existing_job_event()
    test_no_caption_stops_before_compiler_job()
    test_caption_capture_uses_existing_url_pipeline_and_replays_vtt()
    print(json.dumps({"status": "passed", "coverage": ["metadata-only yt-dlp command", "mixed automatic group translation-last exact-item download", "no fresh extraction or video download fallback", "en and region-base fallback", "zh-Hant and zh-Hans isolation", "manual before automatic", "missing original stops before download/writer", "safe failure diagnostic persisted in existing job event", "same URL pipeline with VTT replay and reuse"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
