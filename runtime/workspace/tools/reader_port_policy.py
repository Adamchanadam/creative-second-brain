"""Deterministic, bounded loopback ports shared by the reader client and server."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import time
from typing import Callable


LOWEST_PORT = 49152
HIGHEST_PORT = 59161
PRIMARY_COUNT = 10
FALLBACK_COUNT = 6


def candidate_ports(identity: str) -> tuple[int, ...]:
    """Keep the historic contiguous range, then try a few stable spread ports."""
    if not isinstance(identity, str) or len(identity) != 20 or any(char not in "0123456789abcdef" for char in identity):
        raise ValueError("reader identity is invalid")
    primary = LOWEST_PORT + int(identity[:6], 16) % 10000
    ports = list(range(primary, primary + PRIMARY_COUNT))
    digest = hashlib.sha256((identity + ":reader-port-fallback-v1").encode("ascii")).digest()
    span = HIGHEST_PORT - LOWEST_PORT + 1
    for index in range(FALLBACK_COUNT):
        port = LOWEST_PORT + int.from_bytes(digest[index * 2:index * 2 + 2], "big") % span
        if port not in ports:
            ports.append(port)
    return tuple(ports)


def find_reader_state(identity: str, probe: Callable[[str, float], dict | None], *,
                      timeout: float, deadline: float | None = None) -> tuple[str | None, dict | None]:
    """Probe all deterministic ports concurrently without weakening identity checks.

    ``probe`` performs one bounded loopback request and returns its decoded
    status, if any.  This owner validates the expected reader identity and
    keeps candidate order deterministic even when a later fallback responds
    first.  A dead or filtered port therefore consumes at most one timeout for
    a whole sweep, not one timeout per candidate.
    """
    if timeout <= 0:
        raise ValueError("reader probe timeout is invalid")
    ports = candidate_ports(identity)
    remaining = None if deadline is None else deadline - time.monotonic()
    if remaining is not None and remaining <= 0:
        return None, None
    per_probe_timeout = timeout if remaining is None else min(timeout, remaining)
    executor = ThreadPoolExecutor(max_workers=len(ports), thread_name_prefix="reader-probe")
    pending = {
        executor.submit(probe, f"http://127.0.0.1:{port}", per_probe_timeout): index
        for index, port in enumerate(ports)
    }
    states: dict[int, dict | None] = {}
    try:
        while pending:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return None, None
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                return None, None
            for future in done:
                index = pending.pop(future)
                value = future.result()
                states[index] = (value if isinstance(value, dict)
                                 and value.get("app") == "knowledge-nebula"
                                 and value.get("identity") == identity else None)
            matches = [index for index, value in states.items() if value is not None]
            if matches:
                selected = min(matches)
                if all(index not in pending.values() for index in range(selected)):
                    return f"http://127.0.0.1:{ports[selected]}", states[selected]
        return None, None
    finally:
        for future in pending:
            future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
