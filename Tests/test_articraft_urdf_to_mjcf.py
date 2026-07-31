from __future__ import annotations

import importlib.util
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import mujoco


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PLUGIN_ROOT / "Scripts" / "articraft_urdf_to_mjcf.py"


def _load_converter_module():
    spec = importlib.util.spec_from_file_location("articraft_urdf_to_mjcf", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_convert_articraft_urdf_writes_valid_mjcf_with_unique_names(tmp_path: Path) -> None:
    urdf_path = tmp_path / "sample.urdf"
    mjcf_path = tmp_path / "sample.xml"
    urdf_path.write_text(
        """\
<robot name="sample_microwave">
  <link name="cabinet">
    <visual name="cap">
      <geometry><box size="0.5 0.4 0.3" /></geometry>
      <material name="shell"><color rgba="0.8 0.8 0.76 1" /></material>
    </visual>
  </link>
  <link name="button_a">
    <visual name="cap"><geometry><box size="0.04 0.01 0.02" /></geometry></visual>
  </link>
  <link name="button_b">
    <visual name="cap"><geometry><box size="0.04 0.01 0.02" /></geometry></visual>
  </link>
  <joint name="button_press" type="prismatic">
    <parent link="cabinet" />
    <child link="button_a" />
    <axis xyz="0 1 0" />
    <limit lower="0" upper="0.003" effort="1" velocity="1" />
  </joint>
  <joint name="button_press" type="prismatic">
    <parent link="cabinet" />
    <child link="button_b" />
    <axis xyz="0 1 0" />
    <limit lower="0" upper="0.003" effort="1" velocity="1" />
  </joint>
</robot>
""",
        encoding="utf-8",
    )
    converter = _load_converter_module()

    converter.convert_urdf_to_mjcf(urdf_path, mjcf_path, model_name="sample_microwave")

    root = ET.parse(mjcf_path).getroot()
    assert root.find("./compiler").attrib["angle"] == "radian"
    for tag in ("geom", "joint", "body", "material"):
        names = [node.attrib["name"] for node in root.findall(f".//{tag}") if "name" in node.attrib]
        duplicates = [name for name, count in Counter(names).items() if count > 1]
        assert duplicates == []

    model = mujoco.MjModel.from_xml_path(str(mjcf_path))
    assert model.ngeom == 3
    assert model.njnt == 2


def test_cli_can_write_to_output_dir_using_robot_name(tmp_path: Path) -> None:
    urdf_path = tmp_path / "model.urdf"
    output_dir = tmp_path / "mjcf"
    urdf_path.write_text(
        """\
<robot name="Tiny Oven">
  <link name="base">
    <visual><geometry><box size="0.3 0.2 0.2" /></geometry></visual>
  </link>
</robot>
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(urdf_path),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Wrote MJCF to" in result.stdout
    mjcf_path = output_dir / "Tiny_Oven.xml"
    assert mjcf_path.exists()
    mujoco.MjModel.from_xml_path(str(mjcf_path))


def test_convert_urdf_copies_mesh_assets_and_declares_them(tmp_path: Path) -> None:
    mesh_dir = tmp_path / "assets" / "meshes"
    mesh_dir.mkdir(parents=True)
    mesh_path = mesh_dir / "lamp_shade.obj"
    mesh_path.write_text(
        """\
v 0 0 0
v 0.1 0 0
v 0 0.1 0
v 0 0 0.1
f 1 2 3
f 1 2 4
f 1 3 4
f 2 3 4
""",
        encoding="utf-8",
    )
    urdf_path = tmp_path / "model.urdf"
    mjcf_path = tmp_path / "out" / "desk_lamp.xml"
    urdf_path.write_text(
        """\
<robot name="desk_lamp">
  <link name="shade">
    <visual name="shade_shell">
      <geometry><mesh filename="assets/meshes/lamp_shade.obj" /></geometry>
    </visual>
    <visual name="shade_bulb">
      <geometry><mesh filename="assets/meshes/lamp_shade.obj" /></geometry>
    </visual>
  </link>
</robot>
""",
        encoding="utf-8",
    )
    converter = _load_converter_module()

    converter.convert_urdf_to_mjcf(urdf_path, mjcf_path)

    copied_mesh = mjcf_path.parent / "desk_lamp_assets" / "lamp_shade.obj"
    assert copied_mesh.exists()
    root = ET.parse(mjcf_path).getroot()
    mesh_assets = root.findall("./asset/mesh")
    assert len(mesh_assets) == 1
    assert mesh_assets[0].attrib == {
        "name": "lamp_shade",
        "file": "desk_lamp_assets/lamp_shade.obj",
    }
    assert [geom.attrib["mesh"] for geom in root.findall(".//geom")] == [
        "lamp_shade",
        "lamp_shade",
    ]
    model = mujoco.MjModel.from_xml_path(str(mjcf_path))
    assert model.nmesh == 1
    assert model.ngeom == 2
