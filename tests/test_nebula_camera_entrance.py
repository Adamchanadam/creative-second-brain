"""Focused contract for the camera-only nebula entrance."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "runtime" / "workspace" / "assets" / "knowledge-nebula" / "template.html"


def main() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    assert "CAMERA_TOUR_MS=4000" in source
    assert "CAMERA_TOUR_NEAR=MAX_DISTANCE/6.6" in source
    assert "CAMERA_TOUR_FINAL=MAX_DISTANCE/2" in source
    assert "finish.yaw-Math.PI" in source
    assert "const available=nodes.length?nodes:allNodes" in source
    assert "const center=[0,1,2].map" in source
    assert "state.yaw=state.tyaw=" in source
    assert "state.distance=state.tdistance=" in source
    assert "state.focus=state.target=" in source
    assert "if(!automatic&&!hasSavedReadingPosition(restored))startCameraTour();" in source
    assert "if(!state.drag&&!state.cameraTour)" in source
    assert "function select(id,record=true){stopCameraTour();" in source
    assert "function zoom(d){stopCameraTour();" in source
    assert "canvas.addEventListener('pointerdown',e=>{stopCameraTour();" in source
    assert "function advanceCameraTour(now){if(state.reduced){stopCameraTour();return;}" in source
    assert "if(state.reduced){stopCameraTour();state.events=[];" in source
    assert "if(!state.gpu||state.reduced||!state.legacyEntranceEnabled||state.selected||state.cameraTour)return;" in source
    for forbidden in ("entranceWarp", "entranceFlare", "entranceHalo", "entrance-skip", "entrance-toggle"):
        assert forbidden not in source
    print("camera entrance: 4s / 180 degree / 6.6x to 2x / real-node route / cancel and skip contracts passed")


if __name__ == "__main__":
    main()
