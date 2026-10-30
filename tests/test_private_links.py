"""A user's own link or junction inside private content must not block an update.

Reproduces the 1.9.23 field report "workspace path cannot contain a link or junction":
the update plan hashes private scopes to prove they are untouched, and used to stop at
the first link anywhere inside them without naming it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from package_paths import assert_plain_path, is_link  # noqa: E402
from manage_workspace import tree  # noqa: E402


def make_link(link: Path, target: Path) -> str:
    """Junction on Windows (no admin needed), symlink elsewhere; returns the kind made."""
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
        return "junction"
    link.symlink_to(target, target_is_directory=True)
    return "symlink"


def main() -> None:
    work = Path(tempfile.mkdtemp(prefix="csb-links-"))
    try:
        vault, outside, other = work / "ws" / "My Notes", work / "outside", work / "other"
        (vault / "notes").mkdir(parents=True); outside.mkdir(); other.mkdir()
        (vault / "notes" / "a.md").write_text("私人筆記", encoding="utf-8")
        (outside / "big.bin").write_bytes(b"x" * 1024)
        kind = make_link(vault / "附件連結", outside)

        assert is_link((vault / "附件連結").lstat()), kind
        assert not is_link(vault.lstat())

        first = tree(vault)                       # must not raise
        assert first["exists"] and first["files"] == 1, first   # the link's target is not walked or counted

        (outside / "big.bin").write_bytes(b"y" * 2048)            # data behind the link is not part of the proof
        assert tree(vault)["sha256"] == first["sha256"]

        (vault / "notes" / "a.md").write_text("改過", encoding="utf-8")   # real private content still is
        changed = tree(vault)
        assert changed["sha256"] != first["sha256"]

        os.rmdir(vault / "附件連結") if os.name == "nt" else (vault / "附件連結").unlink()
        make_link(vault / "附件連結", other)                         # re-pointing the link is a recorded change
        assert tree(vault)["sha256"] != changed["sha256"]

        # A path that goes through a link is still refused, and the message names it.
        try:
            assert_plain_path(vault / "附件連結" / "x.md")
        except ValueError as error:
            assert "附件連結" in str(error), error
        else:
            raise AssertionError("a path through a link must be refused")
        print(f"private link test passed ({kind})")
    finally:
        link = work / "ws" / "My Notes" / "附件連結"
        if link.exists() or os.path.lexists(link):
            os.rmdir(link) if os.name == "nt" else link.unlink()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
