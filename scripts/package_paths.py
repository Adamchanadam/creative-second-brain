"""Path guards shared by candidate setup and rendering entry points."""
from __future__ import annotations

import os
import stat
from pathlib import Path

# Only name-surrogate reparse points (symlinks, junctions and any third-party
# redirect: Windows tag bit 0x20000000) count as links. Cloud-sync placeholders
# (OneDrive, Dropbox, Google Drive, iCloud) lack that bit and keep their own path.
_NAME_SURROGATE = 0x20000000


def is_link(meta: os.stat_result) -> bool:
    """True for a symlink or a junction (lstat result), never for a cloud placeholder."""
    if stat.S_ISLNK(meta.st_mode):
        return True
    return bool(getattr(meta, "st_file_attributes", 0) & 0x400) and bool(getattr(meta, "st_reparse_tag", 0) & _NAME_SURROGATE)


def assert_plain_path(path: Path) -> None:
    """Reject a target itself or any existing ancestor that is a link/junction, naming it."""
    for node in (path, *path.parents):
        try:
            meta = node.lstat()
        except FileNotFoundError:
            continue
        if is_link(meta):
            raise ValueError(f"workspace path cannot contain a link or junction: {node}")
