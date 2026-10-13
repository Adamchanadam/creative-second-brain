"""Focused source contract for Nebula motion preference precedence."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "runtime" / "workspace" / "assets" / "knowledge-nebula" / "template.html"


def run_model(system_reduced: bool, saved: bool) -> dict:
    source = TEMPLATE.read_text(encoding="utf-8")
    state = re.search(r"const state=\{[^\n]+\};", source)
    apply = re.search(r"function applyReducedPreference\(value\)\{[^\n]+\}", source)
    assert state and apply
    program = (
        "const systemReduced=" + str(system_reduced).lower() + ";\n"
        + state.group(0) + "\n" + apply.group(0)
        + f"\napplyReducedPreference({str(saved).lower()});"
        + "\nconsole.log(JSON.stringify({reduced:state.reduced,preference:state.reducedPreference}));"
    )
    result = subprocess.run(["node", "-e", program], text=True, encoding="utf-8", errors="replace", capture_output=True, check=True)
    return json.loads(result.stdout)


def main() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    assert 'reduced:systemReduced,reducedPreference:false' in source
    assert run_model(False, False) == {"reduced": False, "preference": False}
    assert run_model(False, True) == {"reduced": True, "preference": True}
    assert run_model(True, False) == {"reduced": True, "preference": False}
    assert "applyReducedPreference(saved.reduced_motion)" in source
    assert "catch{applyReducedPreference(false);syncNotice.textContent='星雲偏好未能讀取；目前只依系統減少動態設定。';}" in source
    assert "if(systemReduced){button.disabled=true;button.textContent='系統已減少動態'" in source
    assert "$('#motion').onclick=()=>{if(systemReduced)return;applyReducedPreference(!state.reducedPreference);" in source
    restore = source[source.index("function restore()"):source.index("let memoryTimer")]
    assert "state.reduced=" not in restore and "state.reducedPreference=" not in restore
    print(json.dumps({"status": "passed", "coverage": [
        "normal saved reduced false enables motion", "saved reduced true restores reduced mode",
        "system reduced motion remains authoritative", "preference API failure falls back only to system setting",
        "system-controlled button is explicit and disabled", "manual toggle persists only when actionable",
        "session reload never overwrites persisted motion preference"
    ]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
