"""Read-only checks of the actual interpreter and locked imports used by this skill."""
from __future__ import annotations
import importlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import re
from pathlib import Path
from zoneinfo import ZoneInfo


DIRECT_REQUIREMENTS = {
    "Pillow": {"module": "PIL.Image", "extras": frozenset()},
    "beautifulsoup4": {"module": "bs4", "extras": frozenset()},
    "yt-dlp": {"module": "yt_dlp", "extras": frozenset({"default"})},
    "tzdata": {"module": "tzdata", "extras": frozenset()},
}
LOCKED_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[(?P<extras>[A-Za-z0-9._-]+(?:,[A-Za-z0-9._-]+)*)\])?==(?P<version>[A-Za-z0-9][A-Za-z0-9._+!-]*)$"
)

def read_direct_requirements(requirements: Path) -> tuple[dict[str, str], list[str]]:
    """Read the portable direct pins without duplicating their versions."""
    errors: list[str] = []
    locked: dict[str, str] = {}
    try:
        lines = requirements.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return locked, [f"requirements.txt: unavailable ({type(error).__name__})"]
    for number, raw in enumerate(lines, start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LOCKED_REQUIREMENT.fullmatch(line)
        if not match:
            errors.append(f"requirements.txt:{number}: requires an exact direct lock")
            continue
        distribution = match["name"]
        expected = DIRECT_REQUIREMENTS.get(distribution)
        if expected is None:
            errors.append(f"requirements.txt:{number}: unsupported direct requirement {distribution}")
            continue
        extras = frozenset(filter(None, (match["extras"] or "").split(",")))
        if extras != expected["extras"]:
            wanted = "[default]" if distribution == "yt-dlp" else "no extras"
            errors.append(f"{distribution}: requires {wanted}")
            continue
        if distribution in locked:
            errors.append(f"{distribution}: duplicate locked requirement")
            continue
        locked[distribution] = match["version"]
    missing = set(DIRECT_REQUIREMENTS) - set(locked)
    if missing:
        errors.append("requirements.txt must contain exactly Pillow, beautifulsoup4, yt-dlp[default] and tzdata")
    return locked, errors


def check(workspace: Path | None = None) -> dict:
    """Report basic Python readiness separately from the complete local setup."""
    errors = []
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        errors.append("a dedicated virtual environment is required")
    if sys.version_info < (3, 11):
        errors.append("Python 3.11 or newer is required")
    runtime = workspace.absolute() if workspace else Path(__file__).resolve().parents[1] / "runtime/workspace"
    requirements = runtime / "requirements.txt"
    modules = {name: definition["module"] for name, definition in DIRECT_REQUIREMENTS.items()}
    checked = {}
    locked, requirement_errors = read_direct_requirements(requirements)
    errors.extend(requirement_errors)
    for distribution, expected in locked.items():
        try:
            actual = importlib.metadata.version(distribution)
            module = importlib.import_module(modules[distribution])
            checked[distribution] = actual
            if actual != expected:
                errors.append(f"{distribution}: requires {expected}, found {actual}")
            if distribution == "yt-dlp":
                cli = subprocess.run([sys.executable, "-m", "yt_dlp", "--version"], text=True,
                                     encoding="utf-8", errors="replace", capture_output=True, timeout=15)
                module_version = str(module.version.__version__)
                if cli.returncode != 0 or cli.stdout.strip() != module_version:
                    errors.append("yt-dlp: CLI version readback failed")
        except (ImportError, OSError, KeyError) as error:
            errors.append(f"{distribution}: unavailable ({type(error).__name__})")
        except subprocess.SubprocessError as error:
            errors.append(f"{distribution}: CLI unavailable ({type(error).__name__})")
    if "tzdata" in locked:
        try:
            ZoneInfo("Europe/London")
        except Exception as error:
            errors.append(f"tzdata: IANA timezone data unavailable ({type(error).__name__})")
    browser_tools = runtime / "tools"
    node = shutil.which("node")
    browser_ready = False
    browser_check_error = ""
    capture_tool = browser_tools / "case_reproduction_capture.js"
    if node and (browser_tools / "node_modules" / "playwright" / "package.json").is_file() and capture_tool.is_file():
        try:
            probe = subprocess.run([node, str(capture_tool), "--check"], cwd=browser_tools, text=True,
                                   encoding="utf-8", errors="replace", capture_output=True, timeout=30)
            browser_ready = probe.returncode == 0
            if not browser_ready:
                browser_check_error = (probe.stderr or probe.stdout).strip()[:300]
        except (OSError, subprocess.SubprocessError) as error:
            browser_check_error = type(error).__name__
    browser_capture = {
        "status": "ready" if browser_ready else "needs_install",
        "node_found": bool(node),
        "package": "tools/package.json",
        **({"check_error": browser_check_error} if browser_check_error else {}),
        "boundary": "Complete setup requires the workspace-local lockfile Playwright and an actual Chromium launch/close; global modules and host browsers do not count.",
    }
    basic_status = "ready" if not errors else "blocked"
    dependency_complete = "ready" if basic_status == "ready" and browser_ready else ("blocked" if basic_status == "blocked" else "needs_browser")
    return {"status": basic_status, "python": sys.executable,
            "python_version": sys.version.split()[0], "packages": checked, "errors": errors,
            "python_dependencies": {"status": basic_status, "direct_locked_requirements": sorted(locked)},
            "browser_capture": browser_capture,
            "installation": {
                "basic": basic_status,
                "dependency_complete": dependency_complete,
                "image_generation": "requires_current_host_schema",
                "overall": "blocked" if basic_status == "blocked" else "basic_limited",
                "node_contract": "At installation time, use the current official Playwright system requirements intersected with this package-lock engine; this package does not hardcode a Node minimum.",
            },
            "boundary": "Basic readiness is only the locked Python requirements, including tzdata for IANA timezones. Complete local setup additionally needs locked Playwright plus Chromium; host recognition, Obsidian and ImageGen remain separate evidence."}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, help="installed workspace runtime to inspect")
    args = parser.parse_args()
    result = check(args.workspace)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "ready" else 2)
