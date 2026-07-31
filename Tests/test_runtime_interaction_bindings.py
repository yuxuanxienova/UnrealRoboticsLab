from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"Could not find end of function {signature}")


def test_pie_camera_uses_right_drag_for_rotation_and_middle_drag_for_pan() -> None:
    source = (
        PROJECT_ROOT
        / "Source"
        / "URLabHost"
        / "Camera"
        / "URH_PieCameraControlSubsystem.cpp"
    ).read_text(encoding="utf-8")
    body = _function_body(source, "void UURH_PieCameraControlSubsystem::ApplyPossessCameraInput")

    rotation_condition = re.search(
        r"if\s*\(\s*PC->IsInputKeyDown\(EKeys::(?P<button>\w+MouseButton)\).*?Rot\.Yaw",
        body,
        flags=re.S,
    )
    assert rotation_condition is not None
    assert rotation_condition.group("button") == "RightMouseButton"
    assert "EKeys::MiddleMouseButton" in body


def test_mujoco_perturbation_left_drag_selects_and_translates_without_ctrl() -> None:
    source = (
        PLUGIN_ROOT
        / "Source"
        / "URLab"
        / "Private"
        / "MuJoCo"
        / "Input"
        / "MjInputHandler.cpp"
    ).read_text(encoding="utf-8")
    body = _function_body(source, "void UMjInputHandler::ProcessPerturbation")

    assert "bPlainLmbHeld" in body
    plain_left_press = re.search(
        r"if\s*\(\s*bPlainLmbHeld\s*&&\s*!bPrevPlainLmbHeld.*?HandleSelect.*?StartTranslate",
        body,
        flags=re.S,
    )
    assert plain_left_press is not None
    assert "bCtrlRmbHeld && !bPrevCtrlRmbHeld" not in body
