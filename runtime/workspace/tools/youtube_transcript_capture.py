"""Capture one already-published YouTube caption track with yt-dlp only."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:
    from tools.source_identity import normalize_url, youtube_video_id
except ModuleNotFoundError:
    from source_identity import normalize_url, youtube_video_id


ATTESTATION = "submitted_url_owned_or_author_authorized_v1"
CAPTURE_POLICY = "yt_dlp_existing_caption_only_v1"
SELECTION_POLICY = "declared_source_language_manual_before_automatic_original_item_v3"
_VTT_TIMING = re.compile(r"^(?P<start>(?:(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}))\s+-->\s+(?P<end>(?:(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}))(?:\s+.*)?$")
_WEBVTT_HEADER = re.compile(r"^WEBVTT(?:[ \t].*)?$")
# Retention mode for a YouTube caption job: the complete transcript lives only
# in the package archive (canonical_source.md + the original VTT); the Vault
# keeps program-built, de-duplicated short quotations with time links.
TRANSCRIPT_ARCHIVE_MODE = "transcript_archive_required"


class YoutubeTranscriptError(ValueError):
    """The requested caption source is missing, incomplete, or unsafe."""

    def __init__(self, message: str, *, stage: str | None = None, exit_code: int | None = None, safe_summary: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.exit_code = exit_code
        self.safe_summary = safe_summary

    def diagnostic(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "exit_code": self.exit_code,
            "summary": self.safe_summary,
        }


class YoutubeCaptionsUnavailable(YoutubeTranscriptError):
    """The video did not yield an existing readable VTT caption track."""


@dataclass(frozen=True)
class YoutubeCaptionTrack:
    """One verified, non-translated VTT item from the raw yt-dlp snapshot."""

    declared_language: str
    group_key: str
    item_index: int
    item_language: str
    track_kind: str


@dataclass(frozen=True)
class YoutubeTranscriptCapture:
    normalized_url: str
    video_id: str
    title: str
    channel: str
    upload_date: str
    selected_language: str
    selected_track_kind: str
    selected_track_group: str
    selected_track_item_index: int
    selected_track_item_language: str
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


def _language_candidates(language: str) -> tuple[str, ...]:
    """Keep the declared source language before its explicit base fallback."""
    base = language.split("-", 1)[0]
    return (language,) if base == language else (language, base)


def _declared_source_language(info: dict[str, Any]) -> str:
    original = info.get("original_language")
    source_language = original if isinstance(original, str) and original else info.get("language")
    if not isinstance(source_language, str) or not source_language:
        raise YoutubeCaptionsUnavailable("yt-dlp metadata has no declared source language")
    return source_language


def _item_language_evidence(entry: dict[str, Any]) -> tuple[str, bool] | None:
    """Return declared item language and whether it is translated, without exposing its URL."""
    url = entry.get("url")
    if not isinstance(url, str) or not url:
        return None
    try:
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
    except ValueError:
        return None
    query_languages = [value for value in query.get("lang", []) if value]
    if len(query_languages) != 1:
        return None
    item_languages = [value for value in (entry.get("lang"), entry.get("language")) if isinstance(value, str) and value]
    if item_languages and any(value != query_languages[0] for value in item_languages):
        return None
    translated = bool(entry.get("tlang")) or any(value for value in query.get("tlang", []))
    return query_languages[0], translated


def _track_for_group(
    table: dict[str, Any], *, declared_language: str, group_key: str, track_kind: str,
) -> YoutubeCaptionTrack | None:
    entries = table.get(group_key)
    if not isinstance(entries, list):
        return None
    valid: list[YoutubeCaptionTrack] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or str(entry.get("ext") or "").lower() != "vtt":
            continue
        evidence = _item_language_evidence(entry)
        if evidence is None:
            continue
        item_language, translated = evidence
        if translated or item_language not in _language_candidates(declared_language):
            continue
        valid.append(YoutubeCaptionTrack(
            declared_language=declared_language,
            group_key=group_key,
            item_index=index,
            item_language=item_language,
            track_kind=track_kind,
        ))
    if len(valid) > 1:
        raise YoutubeCaptionsUnavailable("yt-dlp found multiple indistinguishable original-language VTT items")
    return valid[0] if valid else None


def _source_track(info: dict[str, Any]) -> YoutubeCaptionTrack:
    """Select one concrete source-language VTT item; never pass a mixed group to yt-dlp."""
    declared_language = _declared_source_language(info)
    manual, automatic = _caption_table(info, "subtitles"), _caption_table(info, "automatic_captions")
    candidates = _language_candidates(declared_language)
    for language in candidates:
        selected = _track_for_group(manual, declared_language=declared_language, group_key=language, track_kind="manual")
        if selected:
            return selected
    for language in candidates:
        selected = _track_for_group(automatic, declared_language=declared_language, group_key=f"{language}-orig", track_kind="automatic")
        if selected:
            return selected
    for language in candidates:
        selected = _track_for_group(automatic, declared_language=declared_language, group_key=language, track_kind="automatic")
        if selected:
            return selected
    raise YoutubeCaptionsUnavailable("yt-dlp found no verified original-language VTT item")


def selected_caption_track(info: dict[str, Any]) -> YoutubeCaptionTrack:
    """Public replay helper for the exact non-translated VTT selection contract."""
    return _source_track(info)


def _track_kind(language: str, info: dict[str, Any]) -> str:
    """Compatibility helper that resolves the same concrete item as capture."""
    selected = _source_track(info)
    if language not in {selected.group_key, selected.item_language}:
        raise YoutubeTranscriptError("yt-dlp caption metadata has no selected VTT item for the requested language")
    return selected.track_kind


def _selected_item(info: dict[str, Any], track: YoutubeCaptionTrack) -> dict[str, Any]:
    table = _caption_table(info, "subtitles" if track.track_kind == "manual" else "automatic_captions")
    entries = table.get(track.group_key)
    if not isinstance(entries, list) or not 0 <= track.item_index < len(entries) or not isinstance(entries[track.item_index], dict):
        raise YoutubeTranscriptError("yt-dlp metadata changed the selected VTT item")
    item = entries[track.item_index]
    evidence = _item_language_evidence(item)
    if (
        str(item.get("ext") or "").lower() != "vtt"
        or evidence != (track.item_language, False)
        or track.item_language not in _language_candidates(track.declared_language)
    ):
        raise YoutubeTranscriptError("yt-dlp metadata changed the verified VTT item evidence")
    return item


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


def _selected_vtt(work: Path, track: YoutubeCaptionTrack, info: dict[str, Any]) -> Path:
    paths = [path for path in work.glob("source.*.vtt") if path.is_file() and path.stat().st_size > 0]
    selected = [path for path in paths if _language_from_filename(path) == track.group_key]
    if len(selected) != 1:
        raise YoutubeCaptionsUnavailable("yt-dlp did not return exactly one verified VTT item")
    if _source_track(info) != track:
        raise YoutubeTranscriptError("yt-dlp caption metadata changed the selected VTT item during retrieval")
    return selected[0]


def _safe_summary(value: str) -> str:
    """Keep a small diagnostic without leaking signed URLs, queries, or local paths."""
    summary = re.sub(r"https?://[^\s'\"]+", "<redacted-url>", value)
    summary = re.sub(r"(?i)((?:token|sig|signature|expire|key|auth|cookie)=)[^\s&]+", r"\1<redacted>", summary)
    summary = re.sub(r"(?i)[a-z]:\\[^\s'\"]*", "<local-path>", summary)
    summary = re.sub(r"(?i)(?:[a-z]:\\|/)(?:[^\s'\"]+[/\\])+[^\s'\"]*", "<local-path>", summary)
    summary = " ".join(summary.split())
    return summary[:240] or "yt-dlp returned no safe diagnostic"


def _verified_subtitle_headers(info: dict[str, Any], item: dict[str, Any]) -> dict[str, str]:
    """Use only non-secret headers already attached to the verified VTT item."""
    raw_headers = item.get("http_headers")
    if not isinstance(raw_headers, dict):
        raw_headers = info.get("http_headers")
    if not isinstance(raw_headers, dict):
        return {}
    return {
        str(name): str(value)
        for name, value in raw_headers.items()
        if isinstance(name, str)
        and isinstance(value, str)
        and name.lower() not in {"authorization", "cookie", "proxy-authorization"}
    }


def _download_verified_vtt(
    info: dict[str, Any], track: YoutubeCaptionTrack, *, destination: Path, timeout_seconds: int,
) -> None:
    """Fetch exactly the verified VTT URL without letting yt-dlp re-extract the video."""
    item = _selected_item(info, track)
    url = item.get("url")
    if not isinstance(url, str) or not url or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", track.group_key):
        raise YoutubeTranscriptError(
            "yt-dlp metadata cannot safely identify the verified VTT item",
            stage="verified_vtt",
            safe_summary="missing verified VTT item evidence",
        )
    try:
        import yt_dlp  # type: ignore
        from yt_dlp.networking import Request  # type: ignore
        options = {"quiet": True, "no_warnings": True, "socket_timeout": timeout_seconds}
        with yt_dlp.YoutubeDL(options) as downloader:
            with downloader.urlopen(Request(url, headers=_verified_subtitle_headers(info, item))) as response:
                raw_vtt = response.read()
    except YoutubeTranscriptError:
        raise
    except Exception as exc:
        detail = _safe_summary(str(exc))
        raise YoutubeTranscriptError(
            f"yt-dlp verified_vtt failed ({detail})",
            stage="verified_vtt",
            safe_summary=detail,
        ) from exc
    if not isinstance(raw_vtt, bytes) or not raw_vtt:
        raise YoutubeTranscriptError(
            "yt-dlp verified_vtt returned an empty response",
            stage="verified_vtt",
            safe_summary="empty verified VTT response",
        )
    destination.write_bytes(raw_vtt)


def _run(command: list[str], *, work: Path, timeout_seconds: int, stage: str) -> None:
    try:
        completed = subprocess.run(command, cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        raise YoutubeTranscriptError(f"yt-dlp {stage} timed out", stage=stage, safe_summary="timeout") from exc
    except OSError as exc:
        raise YoutubeTranscriptError(f"yt-dlp {stage} could not start: {type(exc).__name__}", stage=stage, safe_summary=type(exc).__name__) from exc
    if completed.returncode != 0:
        detail = _safe_summary((completed.stderr or completed.stdout or ""))
        raise YoutubeTranscriptError(
            f"yt-dlp {stage} failed (exit {completed.returncode}; {detail})",
            stage=stage,
            exit_code=completed.returncode,
            safe_summary=detail,
        )


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
        _run(common, work=work, timeout_seconds=timeout_seconds, stage="metadata")
        info_path, info_json, metadata = _read_info(work)
        resolved_id = str(metadata.get("id") or "")
        if resolved_id != video_id:
            raise YoutubeTranscriptError("yt-dlp metadata video identity differs from the submitted URL")
        track = _source_track(metadata)
        _download_verified_vtt(metadata, track, destination=work / f"source.{track.group_key}.vtt", timeout_seconds=timeout_seconds)
        selected = _selected_vtt(work, track, metadata)
        raw_vtt = selected.read_bytes()
        canonical_body = vtt_to_canonical_body(raw_vtt, normalized_url=normalized_url, video_id=video_id, language=track.item_language, track_kind=track.track_kind)
        allowed = {info_path.name, selected.name}
        if [path.name for path in work.iterdir() if path.name not in allowed]:
            raise YoutubeTranscriptError("yt-dlp wrote an unexpected non-caption artifact")
        return YoutubeTranscriptCapture(normalized_url=normalized_url, video_id=video_id, title=str(metadata.get("title") or "YouTube video"), channel=str(metadata.get("channel") or metadata.get("uploader") or ""), upload_date=str(metadata.get("upload_date") or ""), selected_language=track.item_language, selected_track_kind=track.track_kind, selected_track_group=track.group_key, selected_track_item_index=track.item_index, selected_track_item_language=track.item_language, raw_vtt=raw_vtt, raw_vtt_filename=selected.name, info_json=info_json, canonical_body=canonical_body, yt_dlp_version=version)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# Caption reading layer.  The canonical transcript is the retained archive; the
# functions below derive from it, deterministically and without writing, the
# de-duplicated "reading version" used for (a) the agent's work order, and (b)
# the short quotations that the Vault keeps.  Nothing here is stored as source.
# ---------------------------------------------------------------------------

_YOUTUBE_TRANSCRIPT_HEADER = "# YouTube transcript"
_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA = ("Video URL: https://www.youtube.com/watch?v=", "Video ID: ", "Caption language: ", "Caption track: ")


def is_youtube_caption_transcript(canonical_body: str) -> bool:
    """Recognise the accepted VTT-derived body without treating cues as article headings."""
    lines = [line.strip() for line in canonical_body.splitlines() if line.strip()]
    return (
        len(lines) > len(_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA)
        and lines[0] == _YOUTUBE_TRANSCRIPT_HEADER
        and all(lines[index + 1].startswith(prefix) for index, prefix in enumerate(_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA))
        and any(re.match(r"^\[[^\]]+\]\s+\S", line) for line in lines[1 + len(_YOUTUBE_TRANSCRIPT_REQUIRED_METADATA):])
    )


CAPTION_CHUNK_MIN_CHARS = 80
CAPTION_CHUNK_MAX_CHARS = 240
# A cue line as written by vtt_to_canonical_body: "[start --> end] text".
_CANONICAL_CUE = re.compile(r"^\[(?P<start>[^\]\s]+) --> (?P<end>[^\]\s]+)\] (?P<text>\S.*)$")
_SENTENCE_END = re.compile(r"[.!?。！？][\"'”’）)\]」』]*$")
# A cue timing line must never reach a Vault note when the archive holds the transcript.
CUE_TIMING_LINE = re.compile(r"^\[(?:\d{2,}:)?\d{2}:\d{2}\.\d{3} --> ", re.M)  # hh:mm:ss.mmm, plus the short mm:ss.mmm form
_EVIDENCE_LINK = re.compile(r"\s+—\s+https://youtu\.be/[A-Za-z0-9_-]{11}\?t=\d+\s*$")


def has_cue_timing_line(text: str) -> bool:
    return bool(CUE_TIMING_LINE.search(text))


def youtube_time_link(video_id: str, seconds: int) -> str:
    return f"https://youtu.be/{video_id}?t={int(seconds)}"


def strip_caption_evidence_link(anchor: str) -> str:
    """Remove the one program-added trailing time link from rendered evidence."""
    return _EVIDENCE_LINK.sub("", anchor)


def _cue_seconds(stamp: str) -> int:
    parts = stamp.split(".", 1)[0].split(":")
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _contained_on_word_boundaries(needle: str, haystack: str) -> bool:
    return bool(needle) and re.search(r"(?<!\S)" + re.escape(needle) + r"(?!\S)", haystack) is not None


def _rolling_overlap(previous: str, current: str, tail: str) -> int:
    """Longest word-bounded suffix of ``previous`` that starts ``current``.

    The overlap is only removed when the text already emitted ends with it, so
    every cue stays one contiguous run inside the de-duplicated output.
    """
    longest = min(len(previous), len(current))
    for size in range(longest, 0, -1):
        if current[:size] != previous[-size:]:
            continue
        if size < len(current) and not (current[size] == " " and size + 1 < len(current) and not current[size + 1].isspace()):
            continue
        if size < len(previous) and not previous[-size - 1].isspace():
            continue
        if tail.endswith(current[:size]) and (len(tail) == size or tail[-size - 1].isspace()):
            return size
    return 0


def caption_reading_chunks(canonical: str) -> list[dict[str, Any]]:
    """Return de-duplicated, sentence-sized reading chunks of a canonical caption body.

    YouTube automatic captions roll: each line is repeated in two or three
    consecutive cues.  A cue wholly contained in the previous cue is skipped;
    otherwise only the part that the previous cue did not already show is kept.
    Manual captions have no overlap and pass through unchanged.  Offsets are
    Unicode positions in ``canonical`` (LF newlines, no final LF); they span
    from the first to the last cue line that contributed new text.
    """
    fragments: list[dict[str, Any]] = []
    previous = ""
    tail = ""
    for line in re.finditer(r"[^\n]+(?:\n|$)", canonical):
        raw = line.group().rstrip("\n")
        cue = _CANONICAL_CUE.match(raw.rstrip())
        if cue is None:
            continue
        text = cue.group("text")
        begin, end = line.start(), line.start() + len(raw.rstrip())
        keep = text
        if previous and (text == previous or _contained_on_word_boundaries(text, previous)):
            keep = ""
        elif previous:
            overlap = _rolling_overlap(previous, text, tail)
            keep = text[overlap:].lstrip() if overlap else text
        previous = text
        if not keep:
            continue
        tail = (tail + " " + keep)[-2000:] if tail else keep
        fragments.append({"text": keep, "start": begin, "end": end, "seconds": _cue_seconds(cue.group("start"))})

    chunks: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []

    def close() -> None:
        if not group:
            return
        seconds = group[0]["seconds"]
        body = " ".join(item["text"] for item in group)
        begin, end = group[0]["start"], group[-1]["end"]
        chunks.append({
            "id": "SPAN-%07d-%07d" % (begin, end),
            "text": "[%02d:%02d:%02d] %s" % (seconds // 3600, seconds // 60 % 60, seconds % 60, body),
            "start_seconds": seconds,
            "start": begin,
            "end": end,
        })
        group.clear()

    for item in fragments:
        current = " ".join(part["text"] for part in group)
        if group and len(current) + 1 + len(item["text"]) > CAPTION_CHUNK_MAX_CHARS:
            close()
            current = ""
        group.append(item)
        current = (current + " " + item["text"]).strip()
        if len(current) >= CAPTION_CHUNK_MIN_CHARS and _SENTENCE_END.search(item["text"]):
            close()
    close()
    return chunks


@lru_cache(maxsize=4)
def _caption_chunk_texts(canonical: str) -> frozenset[str] | None:
    if not is_youtube_caption_transcript(canonical):
        return None
    return frozenset(re.sub(r"\s+", " ", chunk["text"]).strip() for chunk in caption_reading_chunks(canonical))


def caption_fragments_grounded(canonical: str, fragments: list[str]) -> bool | None:
    """Check rendered evidence fragments against the reading version of an archive.

    ``None`` means the body is not a YouTube caption transcript (the caller
    applies its ordinary quotation rule).  A fragment is grounded only when it
    is exactly one chunk the program derives from the archive.
    """
    known = _caption_chunk_texts(canonical)
    if known is None:
        return None
    normalized = [re.sub(r"\s+", " ", strip_caption_evidence_link(fragment)).strip() for fragment in fragments]
    return bool(normalized) and all(fragment in known for fragment in normalized)
