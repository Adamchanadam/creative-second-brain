"""Offline counterexamples for selecting one declared-source YouTube caption."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT / "runtime" / "workspace"
sys.path.insert(0, str(WORKSPACE))

from tools import youtube_transcript_capture as transcript  # noqa: E402
from tools import compiler_worker_v2 as worker  # noqa: E402


URL = "https://youtu.be/0zM3nApSvMg?utm_source=fixture"
CANONICAL_URL = "https://www.youtube.com/watch?v=0zM3nApSvMg"
VTT = b"WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nFirst caption line.\n"
MANUAL = {"id": "0zM3nApSvMg", "language": "en", "subtitles": {"en": [{}]}}
AUTOMATIC = {"id": "0zM3nApSvMg", "language": "en", "subtitles": {}, "automatic_captions": {"en": [{}], "fr": [{}]}}


def capture_with(info: dict[str, object]) -> tuple[transcript.YoutubeTranscriptCapture, list[list[str]]]:
    temporary = tempfile.TemporaryDirectory(prefix="youtube-public-test-")
    root = Path(temporary.name)
    calls: list[list[str]] = []
    try:
        def fake_run(command, *, cwd, **kwargs):
            calls.append(command)
            target = Path(cwd)
            (target / "source.info.json").write_text(json.dumps(info), encoding="utf-8")
            if "--sub-langs" in command:
                language = command[command.index("--sub-langs") + 1]
                (target / f"source.{language}.vtt").write_bytes(VTT)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run):
            return transcript.capture_youtube_transcript(URL, temporary_root=root), calls
    finally:
        temporary.cleanup()


def test_declared_source_selection() -> None:
    assert transcript._source_track({"language": "en-US", "subtitles": {"en": [{}]}}) == ("en", "manual")
    assert transcript._source_track(AUTOMATIC) == ("en", "automatic")
    try:
        transcript._source_track({"language": "en", "automatic_captions": {"fr": [{}]}})
    except transcript.YoutubeCaptionsUnavailable:
        pass
    else:
        raise AssertionError("a translated-only track was accepted as the source transcript")
    try:
        transcript._source_track({"original_language": "ja", "language": "en", "automatic_captions": {"en": [{"url": "https://example.invalid/?lang=ja&tlang=en"}]}})
    except transcript.YoutubeCaptionsUnavailable:
        pass
    else:
        raise AssertionError("a translated display-language track was accepted when the declared original language had no track")
    try:
        transcript._source_track({"language": "en", "automatic_captions": {"en": [{"url": "https://example.invalid/?lang=ja&tlang=en"}]}})
    except transcript.YoutubeCaptionsUnavailable:
        pass
    else:
        raise AssertionError("a track explicitly marked as translated was accepted")


def test_capture_never_bulk_downloads_translations() -> None:
    captured, calls = capture_with(AUTOMATIC)
    assert len(calls) == 2
    metadata, caption = calls
    assert "--write-subs" not in metadata and "--write-auto-subs" not in metadata
    assert "--sub-langs" not in metadata
    assert "--write-auto-subs" in caption and "--write-subs" not in caption
    assert caption[caption.index("--sub-langs") + 1] == "en"
    assert "all" not in caption
    assert "--skip-download" in caption
    assert not {"--extract-audio", "--format", "--write-thumbnail", "--write-description"} & set(caption)
    assert captured.normalized_url == CANONICAL_URL
    assert captured.selected_language == "en" and captured.selected_track_kind == "automatic"


def test_manual_wins_for_the_declared_source_language() -> None:
    both = {**MANUAL, "automatic_captions": {"en": [{}]}}
    captured, calls = capture_with(both)
    assert "--write-subs" in calls[1] and "--write-auto-subs" not in calls[1]
    assert captured.selected_track_kind == "manual"


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


def test_time_anchors_are_not_article_coverage_sections() -> None:
    body = transcript.vtt_to_canonical_body(
        VTT, normalized_url=CANONICAL_URL, video_id="0zM3nApSvMg", language="en", track_kind="automatic",
    )
    assert worker.is_youtube_caption_transcript(body) is True
    assert worker.extracted_required_units(body) == []
    assert worker.extracted_required_sections(body) == []


def test_retrieval_rejects_changed_source_track() -> None:
    temporary = tempfile.TemporaryDirectory(prefix="youtube-public-drift-")
    root = Path(temporary.name)
    calls: list[list[str]] = []
    before = AUTOMATIC
    after = {"id": "0zM3nApSvMg", "language": "fr", "subtitles": {}, "automatic_captions": {"en": [{}]}}
    try:
        def fake_run(command, *, cwd, **kwargs):
            calls.append(command)
            target = Path(cwd)
            (target / "source.info.json").write_text(json.dumps(before if len(calls) == 1 else after), encoding="utf-8")
            if "--sub-langs" in command:
                (target / "source.en.vtt").write_bytes(VTT)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with patch.object(transcript, "_yt_dlp_version", return_value="fixture"), patch.object(transcript.subprocess, "run", side_effect=fake_run):
            try:
                transcript.capture_youtube_transcript(URL, temporary_root=root)
            except transcript.YoutubeTranscriptError:
                pass
            else:
                raise AssertionError("metadata source-language drift was accepted")
    finally:
        temporary.cleanup()


def main() -> int:
    test_declared_source_selection()
    test_capture_never_bulk_downloads_translations()
    test_manual_wins_for_the_declared_source_language()
    test_empty_clear_cue_is_not_source_text()
    test_time_anchors_are_not_article_coverage_sections()
    test_retrieval_rejects_changed_source_track()
    print(json.dumps({"status": "passed", "coverage": ["source-language selection", "manual priority", "automatic-caption fallback", "translation bulk-download refusal", "caption-only command"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

