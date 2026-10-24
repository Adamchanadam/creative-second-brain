from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Mapping


GENERIC_SUBMITTER_TERMS = ("使用者", "用戶")
PERSONAL_LAYER_HEADING = "## 個人化消化"
_FENCE_MARKER_PATTERN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
_LEVEL_TWO_HEADING_PATTERN = re.compile(r"^ {0,3}##(?:[ \t]+|$)")
_OWNER_ALIASES_CACHE: dict[Path, tuple[tuple[int, int] | None, tuple[str, ...]]] = {}


def workspace_root() -> Path:
    """Locate this portable workspace the same way the runtime entry does."""

    return Path(__file__).resolve().parents[1]


def owner_aliases(*, workspace_root_path: Path | None = None) -> tuple[str, ...]:
    """Load configured owner aliases once per marker revision."""

    marker = (workspace_root_path or workspace_root()).resolve() / "brain.json"
    try:
        stat = marker.stat()
        revision: tuple[int, int] | None = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        revision = None
    cached = _OWNER_ALIASES_CACHE.get(marker)
    if cached is not None and cached[0] == revision:
        return cached[1]
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        aliases: tuple[str, ...] = ()
    else:
        configured = data.get("owner_aliases") if isinstance(data, dict) else None
        values = configured if isinstance(configured, list) else []
        aliases = tuple(dict.fromkeys(
            value.strip() for value in values
            if isinstance(value, str) and value.strip()
        ))
    _OWNER_ALIASES_CACHE[marker] = (revision, aliases)
    return aliases


def _configured_name_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    if re.fullmatch(r"[A-Za-z]+(?:[ '\-][A-Za-z]+)*", alias):
        return (
            rf"(?<![A-Za-z0-9'\-]){escaped}(?![A-Za-z0-9'\-])"
            rf"(?!\s+[A-Z][A-Za-z'\-]*)"
        )
    return escaped


def _configured_name_patterns(*, workspace_root_path: Path | None = None) -> tuple[str, ...]:
    return tuple(
        _configured_name_pattern(alias)
        for alias in sorted(owner_aliases(workspace_root_path=workspace_root_path), key=len, reverse=True)
    )


def matches_submitter_identity(value: object, *, workspace_root_path: Path | None = None) -> bool:
    """Judge author, source-label and content-owner fields only."""

    text = str(value or "")
    name_patterns = _configured_name_patterns(workspace_root_path=workspace_root_path)
    if name_patterns and re.search("(?:" + "|".join(name_patterns) + ")", text):
        return True
    return any(
        text.startswith(term)
        or re.search(re.escape(term) + r"\s*(?:評註|提供)", text)
        for term in GENERIC_SUBMITTER_TERMS
    )


def markdown_fence_aware_lines(text: str) -> Iterator[tuple[int, str, bool]]:
    """Yield offsets, lines and fence state using Markdown closing-fence rules."""

    fence_character: str | None = None
    fence_length = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        fence = _FENCE_MARKER_PATTERN.match(content)
        fenced = fence_character is not None
        if fence_character is None:
            if fence:
                marker = fence.group(1)
                fence_character, fence_length = marker[0], len(marker)
                fenced = True
        elif (
            fence
            and fence.group(1)[0] == fence_character
            and len(fence.group(1)) >= fence_length
            and not content[fence.end():].strip()
        ):
            fence_character, fence_length = None, 0
        yield offset, content, fenced
        offset += len(line)


def _personal_layer_heading_pattern() -> re.Pattern[str]:
    return re.compile(
        r"^ {0,3}##[ \t]+(?:個人化消化|For [^\r\n:：]{1,40}[:：]個人化消化)[ \t]*$",
        re.MULTILINE,
    )


def personal_layer_heading_match(text: str) -> re.Match[str] | None:
    """Locate a personal-layer heading outside fenced Markdown code blocks."""

    pattern = _personal_layer_heading_pattern()
    for offset, content, fenced in markdown_fence_aware_lines(text):
        if fenced:
            continue
        match = pattern.match(text, offset, offset + len(content))
        if match is not None:
            return match
    return None


def next_level_two_heading_offset(text: str, *, after: int) -> int | None:
    """Find the next unfenced, Markdown level-two heading after an offset."""

    for offset, content, fenced in markdown_fence_aware_lines(text):
        if offset >= after and not fenced and _LEVEL_TWO_HEADING_PATTERN.match(content):
            return offset
    return None


def has_personal_layer(text: str) -> bool:
    """Detect only the documented personal-layer headings outside fences."""

    return personal_layer_heading_match(text) is not None


# A provenance statement is about the source itself, so its subject opens a
# sentence, clause or text block ("本文由使用者提供"); a subject glued to a
# preceding word, such as "一手來源" or "正式來源", is ordinary reader text.
# Whitespace counts as a boundary because HTML block breaks become spaces.
_CLAUSE_START_PATTERN = r"(?:^|(?<=[。！？!?；;，,|>\s]))\s*"
_PROVENANCE_SUBJECT_PATTERN = (
    _CLAUSE_START_PATTERN + r"(?:來源|本文|這份(?:文字|內容|資料)|此資料|資料|原文|內容|文字)"
)
_ENGLISH_PROVENANCE_SUBJECT_PATTERN = (
    _CLAUSE_START_PATTERN + r"(?:this\s+|the\s+)?\b(?:source|text|content|summary)\b"
)
_STATIC_READER_SURFACE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "external_verification_processing_status",
        re.compile(
            r"(?:未|沒有|未有).{0,8}(?:另行|外部|獨立)?(?:核對|校對|查證).{0,28}"
            r"(?:原始(?:影片|文章|發布|來源)|原文|發布者|身份|產品現況|官方)",
            re.IGNORECASE,
        ),
    ),
    (
        "source_boundary_unit_metadata",
        re.compile(
            r"\b(?:source_provenance|source_boundary|source_identity|source_input_kind|"
            r"canonical_body_sha256|source_content_sha256)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "infographic_internal_validation_metadata",
        re.compile(
            r"(?:"
            r"PNG\s*(?:生成方式|語義驗收|SHA-256|尺寸)"
            r"|圖文鎖定稿(?:校對)?"
            r"|語氣口徑檢查"
            r"|圖面邊界"
            r"|圖示語義清單"
            r"|重複文字\s*/\s*數字檢查"
            r"|正文敘事充足性檢查"
            r"|visual\s+QC"
            r"|自動視覺安全檢查"
            r"|工作\s*job"
            r"|鎖定提示"
            r"|候選\s*PNG"
            r"|語義驗收\s*:\s*(?:pending|待|未讀原文)"
            r")",
            re.IGNORECASE,
        ),
    ),
)


def reader_surface_internal_message_patterns(
    *,
    workspace_root_path: Path | None = None,
) -> tuple[tuple[str, re.Pattern[str]], ...]:
    """Return every reader-surface privacy rule for this workspace.

    Submitter identities are deliberately resolved from workspace settings at
    call time, so no consumer can use a name-free subset of the rules.
    """

    configured_names = _configured_name_patterns(workspace_root_path=workspace_root_path)
    identity_pattern = "|".join(("使用者", "用戶", *configured_names))
    english_identity_pattern = "|".join((
        r"(?<![A-Za-z0-9'\-])user(?![A-Za-z0-9'\-])",
        *configured_names,
    ))
    return (
        (
            "source_provenance_processing_status",
            re.compile(
                _PROVENANCE_SUBJECT_PATTERN
                + r"\s*(?:為|由|是|經)?\s*(?:" + identity_pattern + r")\s*"
                + r"(?:提供|貼上|輸入|整理|提交|摘要)"
                + r"|" + _ENGLISH_PROVENANCE_SUBJECT_PATTERN + r"[^。！？.!?\r\n]{0,48}?"
                + r"\b(?:submitted|provided|pasted)\s+by\s+(?:the\s+)?(?:"
                + english_identity_pattern + r")",
                re.IGNORECASE,
            ),
        ),
        (
            "source_verification_processing_status",
            re.compile(
                _PROVENANCE_SUBJECT_PATTERN + r"[^。！？.!?\r\n]{0,48}?"
                + r"(?:未|尚未|沒有|未有)(?:經)?(?:另行|外部|獨立)*(?:核對|校對|查證|核實)"
                + r"(?=$|[\s,，;；:：.。!?！？])"
                + r"|" + _ENGLISH_PROVENANCE_SUBJECT_PATTERN + r"[^。！？.!?\r\n]{0,48}?"
                + r"\bnot\s+(?:independently\s+|externally\s+)?(?:verified|checked)\b"
                + r"(?=$|[\s,，;；:：.。!?！？])",
                re.IGNORECASE,
            ),
        ),
        *_STATIC_READER_SURFACE_PATTERNS,
    )


FOOTER_INTERNAL_STATUS_PATTERN = re.compile(
    r"(?:原始(?:發布頁|來源|文章|影片)|發布者|作者|來源).{0,32}"
    r"(?:未提供|待核實|未核實|未外部核對)"
    r"|(?:未提供|待核實|未核實|未外部核對).{0,32}"
    r"(?:原始(?:發布頁|來源|文章|影片)|發布者|作者|來源)",
    re.IGNORECASE,
)
READER_SOURCE_VALUE_STATUS_PATTERN = re.compile(
    r"(?:未完整提供|未提供|未核實|待核實)",
    re.IGNORECASE,
)
READER_SOURCE_DERIVATIVE_LABEL_PATTERN = re.compile(
    r"(?:演講|文章|文字|內容|資料)\s*整理(?:$|[｜|；;，,。])",
    re.IGNORECASE,
)
READER_SOURCE_IDENTITY_PATTERN = re.compile(
    r"\buser_provided_(?:text|image):[0-9a-f]{64}\b",
    re.IGNORECASE,
)
READER_SOURCE_OUTPUT_PATH_PATTERN = re.compile(r"outputs[\\/]", re.IGNORECASE)


def reader_source_value_has_submitter_role(
    value: str,
    *,
    workspace_root_path: Path | None = None,
) -> bool:
    """Reject a submitter role while leaving a real author's bare name intact."""

    identity_patterns = (
        *(re.escape(term) for term in GENERIC_SUBMITTER_TERMS),
        *_configured_name_patterns(workspace_root_path=workspace_root_path),
    )
    return bool(re.search(
        r"(?:" + "|".join(identity_patterns) + r")\s*(?:提供|貼上|輸入|整理|提交)",
        value,
        re.IGNORECASE,
    ))


def reader_surface_internal_message_errors(
    visible_items: list[Any],
    *,
    context: str = "reader_surface",
    workspace_root_path: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    for index, raw_item in enumerate(visible_items, start=1):
        text = str(raw_item or "").strip()
        if not text:
            continue
        for code, pattern in reader_surface_internal_message_patterns(
            workspace_root_path=workspace_root_path
        ):
            if pattern.search(text):
                errors.append(
                    f"{context}[{index}] exposes internal processing message "
                    f"({code}): {text}"
                )
                break
    return errors


def has_reader_surface_internal_message(
    text: str,
    *,
    workspace_root_path: Path | None = None,
) -> bool:
    return bool(
        reader_surface_internal_message_errors(
            [text],
            context="reader_surface",
            workspace_root_path=workspace_root_path,
        )
    )


def footer_internal_status_errors(text: str, *, context: str = "footer") -> list[str]:
    """Reject capture or verification status only when it is used as a public footer."""

    value = str(text or "").strip()
    if value and FOOTER_INTERNAL_STATUS_PATTERN.search(value):
        return [
            f"{context} exposes internal processing message "
            f"(source_availability_or_verification_status): {value}"
        ]
    return []


def reader_safe_source_value(
    value: object,
    *,
    fallback: str = "",
    workspace_root_path: Path | None = None,
) -> str:
    """Return one reader-safe source value, never capture/review status text."""

    label = str(value or "").strip()
    if (
        not label
        or reader_surface_internal_message_errors(
            [label],
            context="footer_source_label",
            workspace_root_path=workspace_root_path,
        )
        or footer_internal_status_errors(label, context="footer_source_label")
        or reader_source_value_has_submitter_role(label, workspace_root_path=workspace_root_path)
        or "按" in label and re.search(r"按[^。！？.!?\r\n]{0,80}?指示", label)
        or READER_SOURCE_VALUE_STATUS_PATTERN.search(label)
        or READER_SOURCE_DERIVATIVE_LABEL_PATTERN.search(label)
        or re.fullmatch(r"[0-9a-f]{64}", label, re.IGNORECASE)
        or READER_SOURCE_IDENTITY_PATTERN.search(label)
        or READER_SOURCE_OUTPUT_PATH_PATTERN.search(label)
    ):
        return fallback
    return label


def public_footer_source_label(source_label: str, *, fallback: str = "來源資料") -> str:
    """Keep provenance-processing state out of the public image surface."""

    return reader_safe_source_value(source_label, fallback=fallback)


def reader_source_attribution_label(
    source: Mapping[str, object],
    *,
    fallback: str = "來源資料",
) -> str:
    """Select the one concise attribution suitable for reader-facing surfaces.

    ``source_input_kind`` identifies delivery and storage, not authorship.  A
    source label may contain necessary audit caveats that are unsafe to show on
    a reader surface; in that case this deliberately falls through to a
    separately declared original author or publication instead of exposing the
    delivery kind as the article's source.
    """

    for field in (
        "footer_source_label",
        "source_label",
        "attribution_label",
        "_original_author",
        "author",
        "_original_publication",
        "publication",
    ):
        label = reader_safe_source_value(source.get(field), fallback="")
        if label:
            return label
    return fallback


def reader_source_attribution_details(
    source: Mapping[str, object],
) -> tuple[str, str, str]:
    """Return safe publication, author and date values for a detailed citation."""

    def first_safe(*fields: str) -> str:
        for field in fields:
            value = reader_safe_source_value(source.get(field), fallback="")
            if value:
                return value
        return ""

    return (
        first_safe("_original_publication", "publication"),
        first_safe("_original_author", "author"),
        first_safe("_original_date", "original_date"),
    )
