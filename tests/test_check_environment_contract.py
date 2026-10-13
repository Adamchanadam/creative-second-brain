"""Focused regressions for dynamic direct Python requirement validation."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_environment_contract", ROOT / "scripts" / "check_environment.py")
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(checker)


def runtime_with(lines: list[str]) -> Path:
    runtime = Path(tempfile.mkdtemp(prefix="check-environment-contract-"))
    (runtime / "tools").mkdir()
    (runtime / "requirements.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return runtime


def result_for(lines: list[str], versions: dict[str, str], *, missing: str | None = None,
               timezone_error: Exception | None = None) -> dict:
    runtime = runtime_with(lines)
    fake_ytdlp = SimpleNamespace(version=SimpleNamespace(__version__=versions.get("yt-dlp", "")))

    def version(name: str) -> str:
        if name == missing:
            raise checker.importlib.metadata.PackageNotFoundError(name)
        return versions[name]

    def import_module(name: str):
        return fake_ytdlp if name == "yt_dlp" else object()

    def run(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=versions["yt-dlp"] + "\n", stderr="")

    with (mock.patch.object(checker.sys, "prefix", "dedicated-test-venv"),
          mock.patch.object(checker.importlib.metadata, "version", side_effect=version),
          mock.patch.object(checker.importlib, "import_module", side_effect=import_module),
          mock.patch.object(checker.subprocess, "run", side_effect=run),
          mock.patch.object(checker, "ZoneInfo", side_effect=timezone_error or (lambda _name: object()))):
        return checker.check(runtime)


def main() -> None:
    upgraded = {"Pillow": "13.2.1", "beautifulsoup4": "5.0.0", "yt-dlp": "2027.1.2", "tzdata": "2026.5"}
    locks = [f"Pillow=={upgraded['Pillow']}", f"beautifulsoup4=={upgraded['beautifulsoup4']}",
             f"yt-dlp[default]=={upgraded['yt-dlp']}", f"tzdata=={upgraded['tzdata']}"]

    matching = result_for(locks, upgraded)
    assert matching["status"] == "ready"
    assert matching["python_dependencies"]["direct_locked_requirements"] == ["Pillow", "beautifulsoup4", "tzdata", "yt-dlp"]
    assert matching["installation"]["overall"] == "basic_limited"
    assert matching["installation"]["dependency_complete"] == "needs_browser"

    old_environment = dict(upgraded, Pillow="12.9.9")
    stale = result_for(locks, old_environment)
    assert stale["status"] == "blocked"
    assert any("Pillow: requires 13.2.1, found 12.9.9" == error for error in stale["errors"])

    missing_extra = result_for([locks[0], locks[1], "yt-dlp==2027.1.2", locks[3]], upgraded)
    assert missing_extra["status"] == "blocked"
    assert "yt-dlp: requires [default]" in missing_extra["errors"]

    malformed = result_for(["Pillow>=13.2.1", locks[1], locks[2], locks[3]], upgraded)
    assert malformed["status"] == "blocked"
    assert any("requires an exact direct lock" in error for error in malformed["errors"])

    unavailable = result_for(locks, upgraded, missing="beautifulsoup4")
    assert unavailable["status"] == "blocked"
    assert any(error.startswith("beautifulsoup4: unavailable") for error in unavailable["errors"])
    missing_timezone = result_for(locks, upgraded, missing="tzdata")
    assert missing_timezone["status"] == "blocked"
    assert any(error.startswith("tzdata: unavailable") for error in missing_timezone["errors"])
    unusable_timezone = result_for(locks, upgraded, timezone_error=RuntimeError("missing IANA data"))
    assert unusable_timezone["status"] == "blocked"
    assert any(error.startswith("tzdata: IANA timezone data unavailable") for error in unusable_timezone["errors"])
    print(json.dumps({"status": "passed", "coverage": [
        "candidate requirement version accepted without a duplicated source pin",
        "stale environment rejected", "yt-dlp default extra required",
        "malformed requirement fails closed", "missing package or IANA timezone data blocked",
        "missing browser and ImageGen do not claim complete installation"
    ]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
