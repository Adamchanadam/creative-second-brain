from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be",
}


def youtube_video_id(value: str) -> str | None:
    """Return a canonical YouTube video ID when *value* names one video."""
    parsed = urlsplit(str(value))
    host = (parsed.hostname or "").rstrip(".").lower()
    if host not in YOUTUBE_HOSTS:
        return None
    path_parts = [part for part in parsed.path.split("/") if part]
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if host.endswith("youtu.be"):
        candidate = path_parts[0] if path_parts else ""
    elif path_parts[:1] == ["watch"]:
        candidate = query.get("v", "")
    elif len(path_parts) >= 2 and path_parts[0] in {"shorts", "live", "embed", "v"}:
        candidate = path_parts[1]
    else:
        candidate = ""
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def is_youtube_url(value: str) -> bool:
    """Whether a normalized public URL points at a YouTube host."""
    return (urlsplit(str(value)).hostname or "").rstrip(".").lower() in YOUTUBE_HOSTS


TRACKING_QUERY_FIELDS = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "ref_src",
}
SENSITIVE_QUERY_FIELDS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
    "signature",
    "sig",
    "token",
    "x-amz-credential",
    "x-amz-signature",
    "x-goog-credential",
    "x-goog-signature",
}


def normalize_url(value: str) -> str:
    """Canonicalize a public HTTP(S) URL without retaining credentials or tracking data."""
    text = str(value).strip()
    if not text:
        return ""
    if text.startswith("//"):
        text = "https:" + text
    elif "://" not in text:
        text = "https://" + text

    parsed = urlsplit(text)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("intake URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("intake URL must not contain credentials")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("intake URL requires a hostname")
    hostname = hostname.rstrip(".").lower()
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("intake URL hostname is invalid") from exc

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("intake URL port is invalid") from exc
    include_port = port is not None and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443))
    display_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{display_host}:{port}" if include_port else display_host

    query_items: list[tuple[str, str]] = []
    for key, query_value in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in SENSITIVE_QUERY_FIELDS:
            raise ValueError(f"intake URL must not contain credential query field: {key}")
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_FIELDS:
            continue
        query_items.append((key, query_value))
    query = urlencode(sorted(query_items), doseq=True)

    path = parsed.path
    if path == "/":
        path = ""
    elif path:
        path = path.rstrip("/") or "/"
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    video_id = youtube_video_id(normalized)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return normalized
