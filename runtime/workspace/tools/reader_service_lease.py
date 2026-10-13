"""Small cross-process leases for one local reader per canonical workspace."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path


class LeaseUnavailable(RuntimeError):
    """Another process owns this workspace-scoped reader lease."""


def _lock_path(root: Path, kind: str) -> Path:
    if kind not in {"startup", "service"}:
        raise ValueError("reader lease kind is invalid")
    try:
        canonical = Path(root).resolve(strict=True)
    except OSError as error:
        raise ValueError("reader workspace is unreadable") from error
    if not canonical.is_dir():
        raise ValueError("reader workspace is not a directory")
    folder = canonical / ".reader-runtime"
    try:
        folder.mkdir(exist_ok=True)
        resolved = folder.resolve(strict=True)
        resolved.relative_to(canonical)
    except (OSError, ValueError) as error:
        raise ValueError("reader lease path is unsafe") from error
    path = resolved / (kind + ".lock")
    try:
        if path.exists():
            path.resolve(strict=True).relative_to(canonical)
            if path.is_symlink():
                raise ValueError("reader lease path is unsafe")
    except (OSError, ValueError) as error:
        raise ValueError("reader lease path is unsafe") from error
    return path


class ReaderLease:
    """An OS-held byte lock.  A crash releases it without PID cleanup."""

    def __init__(self, root: Path, kind: str):
        self.path = _lock_path(root, kind)
        self.handle = None

    def acquire(self, *, blocking: bool = False) -> bool:
        if self.handle is not None:
            raise ValueError("reader lease is already held by this object")
        try:
            handle = self.path.open("a+b")
            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except (OSError, BlockingIOError):
            try:
                handle.close()
            except UnboundLocalError:
                pass
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        handle, self.handle = self.handle, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *_unused) -> None:
        self.release()


@contextmanager
def reader_lease(root: Path, kind: str, *, blocking: bool = False):
    lease = ReaderLease(root, kind)
    if not lease.acquire(blocking=blocking):
        raise LeaseUnavailable("reader lease is already held")
    try:
        yield lease
    finally:
        lease.release()
