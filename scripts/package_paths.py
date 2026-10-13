"""Path guards shared by candidate setup and rendering entry points."""
from __future__ import annotations

import stat
from pathlib import Path


def assert_plain_path(path: Path) -> None:
    """Reject a target itself or any existing ancestor that is a link/junction."""
    for node in (path, *path.parents):
        try:
            meta = node.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(meta.st_mode) or bool(getattr(meta, "st_file_attributes", 0) & 0x400):
            raise ValueError("workspace path cannot contain a link or junction")
