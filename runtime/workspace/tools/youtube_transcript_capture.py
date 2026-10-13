"""Capture one already-published YouTube caption track with yt-dlp only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:
    from tools.source_identity import normalize_url, youtube_video_id
except ModuleNotFoundError:
    from source_identity import normalize_url, youtube_video_id


ATTESTATION = "submitted_url_owned_or_author_authorized_v1"
CAPTURE_POLICY = "yt_dlp_existing_caption_only_v1"
_VTT_TIMING = re.compile(r"^(?P<start>(?:(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}))\s+-->\s+(?P<end>(?:(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}))(?:\s+.*)?$")
_WEBVTT_HEADER = re.compile(r"^WEBVTT(?:[ \t].*)?$")


class YoutubeTranscriptError(ValueError):
    """The requested caption source is missing, incomplete, or unsafe."""


class YoutubeCaptionsUnavailable(YoutubeTranscriptError):
    """The video did not yield an existing readable VTT caption track."""


@dataclass(frozen=True)
class YoutubeTranscriptCapture:
    normalized_url: str
    video_id: str
    title: str
    channel: str
    upload_date: str
    selected_language: str
    selected_track_kind: str
    raw_vtt: bytes
    raw_vtt_filename: str
    info_json: bytes
    canonical_body: str
    yt_dlp_version: str


def _yt_dlp_version() -> str:
    try:
        import yt_dlp  # type: ignore
    except ImportError as exc:
        raise YoutubeTranscriptError("yt-dlp is not installed; install runtime requirements before YouTube intake") from exc
    return str(getattr(yt_dlp, "version", None) and yt_dlp.version.__version__ or "unknown")


def _language_from_filename(path: Path) -> str:
    name = path.name
    return name[len("source."):-len(".vtt")] if name.startswith("source.") and name.endswith(".vtt") else ""


def _caption_table(info: dict[str, Any], key: str) -> dict[str, Any]:
    value = info.get(key)
    return value if isinstance(value, dict) else {}


def _listed_track(table: dict[str, Any], language: str) -> bool:
    return isinstance(table.get(language), list) and any(isinstance(item, dict) for item in table[language])


def _listed_source_track(table: dict[str, Any], language: str) -> bool:
    entries = table.get(language)
    if not isinstance(entries, list):
        return False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str):
            return True
        try:
            if not parse_qs(urlsplit(url).query).get("tlang"):
                return True
        except ValueError:
            continue
    return False


def _language_candidates(language: str) -> tuple[str, ...]:
    """Keep the declared source language before its explicit base fallback."""
    base = language.split("-", 1)[0]
    return (language,) if base == language else (language, base)


def _source_track(info: dict[str, Any]) -> tuple[str, str]:
    """Select one declared source-language track; never bulk-download translations."""
    manual, automatic = _caption_table(info, "subtitles"), _caption_table(info, "automatic_captions")
    original = info.get("original_language")
    source_language = original if isinstance(original, str) and original else info.get("language")
    if not isinstance(source_language, str) or not source_language:
        raise YoutubeCaptionsUnavailable("yt-dlp metadata has no declared source language")
    for language in _language_candidates(source_language):
        if _listed_source_track(manual, language):
            return language, "manual"
    for language in _language_candidates(source_language):
        if _listed_source_track(automatic, language):
            return language, "automatic"
    raise YoutubeCaptionsUnavailable("yt-dlp found no existing caption track for the video's declared source language")


def _track_kind(language: str, info: dict[str, Any]) -> str:
    """Resolve one selected track, preferring a manual track when both exist."""
    manual, automatic = _caption_table(info, "subtitles"), _caption_table(info, "automatic_captions")
    for key in _language_candidates(language):
        if _listed_track(manual, key):
            return "manual"
        if _listed_track(automatic, key):
            return "automatic"
    raise YoutubeTranscriptError("yt-dlp caption metadata has no track for the selected VTT language")


def _read_info(work: Path) -> tuple[Path, bytes, dict[str, Any]]:
    info_paths = list(work.glob("source.info.json"))
    if len(info_paths) != 1:
        raise YoutubeTranscriptError("yt-dlp did not return one caption metadata record")
    info_json = info_paths[0].read_bytes()
    try:
        info = json.loads(info_json)
    except json.JSONDecodeError as exc:
        raise YoutubeTranscriptError("yt-dlp caption metadata is invalid") from exc
    if not isinstance(info, dict):
        raise YoutubeTranscriptError("yt-dlp caption metadata must be an object")
    return info_paths[0], info_json, info


def _selected_vtt(work: Path, language: str, track_kind: str, info: dict[str, Any]) -> Path:
    paths = [path for path in work.glob("source.*.vtt") if path.is_file() and path.stat().st_size > 0]
    selected = [path for path in paths if _language_from_filename(path) == language]
    if len(selected) != 1:
        raise YoutubeCaptionsUnavailable("yt-dlp did not return exactly one selected source-language VTT track")
    if _track_kind(language, info) != track_kind:
        raise YoutubeTranscriptError("yt-dlp caption metadata changed the selected track kind during retrieval")
    return selected[0]


def _run(command: list[str], *, work: Path, timeout_seconds: int) -> None:
    try:
        completed = subprocess.run(command, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise YoutubeTranscriptError("yt-dlp caption retrieval timed out") from exc
    except OSError as exc:
        raise YoutubeTranscriptError(f"yt-dlp could not start: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise YoutubeCaptionsUnavailable("yt-dlp could not obtain an existing public caption track")


def vtt_to_canonical_body(raw_vtt: bytes, *, normalized_url: str, video_id: str, language: str, track_kind: str) -> str:
    try:
        text = raw_vtt.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise YoutubeTranscriptError("caption VTT is not UTF-8") from exc
    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    # YouTube VTT may put a whitespace-only line between a timing line and its
    # visible payload. Keep actual cue separators and cue identifiers intact.
    lines = [
        line for index, line in enumerate(raw_lines)
        if not (
            line != ""
            and not line.strip()
            and index > 0
            and index + 1 < len(raw_lines)
            and _VTT_TIMING.fullmatch(raw_lines[index - 1].strip())
            and raw_lines[index + 1].strip()
            and raw_lines[index + 1].strip() not in {"STYLE", "REGION", "NOTE"}
            and not raw_lines[index + 1].strip().startswith("NOTE ")
            and _VTT_TIMING.fullmatch(raw_lines[index + 1].strip()) is None
            and not (index + 2 < len(raw_lines) and _VTT_TIMING.fullmatch(raw_lines[index + 2].strip()))
        )
    ]
    if not lines or not _WEBVTT_HEADER.fullmatch(lines[0]):
        raise YoutubeTranscriptError("caption VTT is missing a valid WEBVTT header")
    index = 1
    while index < len(lines) and lines[index].strip():
        if "-->" in lines[index]:
            raise YoutubeTranscriptError("caption VTT has an invalid timing line before cues")
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    cues: list[tuple[str, str, list[str]]] = []
    while index < len(lines):
        block: list[str] = []
        while index < len(lines) and lines[index].strip():
            block.append(lines[index]); index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        if not block:
            continue
        first = block[0].strip()
        if first in {"STYLE", "REGION"} or first == "NOTE" or first.startswith("NOTE "):
            continue
        timing_index = 0 if _VTT_TIMING.fullmatch(first) else 1
        if timing_index >= len(block):
            raise YoutubeTranscriptError("caption VTT cue lacks a valid timing line")
        if timing_index == 1 and "-->" in first:
            raise YoutubeTranscriptError("caption VTT has an invalid timing line")
        timing_line = block[timing_index].strip()
        timing = _VTT_TIMING.fullmatch(timing_line)
        if timing is None:
            if "-->" in timing_line:
                raise YoutubeTranscriptError("caption VTT has an invalid timing line")
            raise YoutubeTranscriptError("caption VTT cue identifier is not followed by a valid timing line")
        payload = block[timing_index + 1:]
        if any("-->" in line for line in payload):
            raise YoutubeTranscriptError("caption VTT has an invalid timing line inside cue text")
        rendered_lines = [re.sub(r"<[^>]+>", "", line).strip() for line in payload]
        rendered_lines = [line for line in rendered_lines if line]
        if not rendered_lines:
            # YouTube emits a short empty timed cue to clear an on-screen line.
            # It is not source text, while a wholly empty VTT still fails below.
            continue
        cues.append((timing.group("start"), timing.group("end"), rendered_lines))
    rendered = [f"[{start} --> {end}] {' '.join(lines).strip()}" for start, end, lines in cues]
    if not rendered:
        raise YoutubeCaptionsUnavailable("youtube captions contain no readable transcript cues")
    return "\n".join(["# YouTube transcript", f"Video URL: {normalized_url}", f"Video ID: {video_id}", f"Caption language: {language}", f"Caption track: {track_kind}", "", *rendered])


def capture_youtube_transcript(url: str, *, temporary_root: Path, timeout_seconds: int = 60) -> YoutubeTranscriptCapture:
    normalized_url = normalize_url(url)
    video_id = youtube_video_id(normalized_url)
    if not video_id:
        raise YoutubeTranscriptError("YouTube URL must identify one video")
    version = _yt_dlp_version()
    temporary_root.mkdir(parents=True, exist_ok=True)
    work = temporary_root / f"youtube-caption-{uuid.uuid4().hex}"
    work.mkdir(parents=False, exist_ok=False)
    try:
        common = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--skip-download", "--write-info-json", "--no-write-thumbnail", "--no-write-description", "--no-warnings", "--output", str(work / "source.%(ext)s"), normalized_url]
        _run(common, work=work, timeout_seconds=timeout_seconds)
        _, _, metadata = _read_info(work)
        language, track_kind = _source_track(metadata)
        command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--skip-download"]
        command.append("--write-subs" if track_kind == "manual" else "--write-auto-subs")
        command.extend(["--sub-format", "vtt", "--sub-langs", language, "--write-info-json", "--no-write-thumbnail", "--no-write-description", "--no-warnings", "--output", str(work / "source.%(ext)s"), normalized_url])
        _run(command, work=work, timeout_seconds=timeout_seconds)
        info_path, info_json, info = _read_info(work)
        if _source_track(info) != (language, track_kind):
            raise YoutubeTranscriptError("yt-dlp caption metadata changed the declared source track during retrieval")
        selected = _selected_vtt(work, language, track_kind, info)
        raw_vtt = selected.read_bytes()
        canonical_body = vtt_to_canonical_body(raw_vtt, normalized_url=normalized_url, video_id=video_id, language=language, track_kind=track_kind)
        resolved_id = str(info.get("id") or "")
        if resolved_id != video_id:
            raise YoutubeTranscriptError("yt-dlp metadata video identity differs from the submitted URL")
        allowed = {info_path.name, selected.name}
        if [path.name for path in work.iterdir() if path.name not in allowed]:
            raise YoutubeTranscriptError("yt-dlp wrote an unexpected non-caption artifact")
        return YoutubeTranscriptCapture(normalized_url=normalized_url, video_id=video_id, title=str(info.get("title") or "YouTube video"), channel=str(info.get("channel") or info.get("uploader") or ""), upload_date=str(info.get("upload_date") or ""), selected_language=language, selected_track_kind=track_kind, raw_vtt=raw_vtt, raw_vtt_filename=selected.name, info_json=info_json, canonical_body=canonical_body, yt_dlp_version=version)
    finally:
        shutil.rmtree(work, ignore_errors=True)
