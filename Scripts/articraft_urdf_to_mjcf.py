#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse


def _clean_name(value: str | None, fallback: str) -> str:
    raw = (value or fallback).strip() or fallback
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw)


def _unique_name(base_name: str, used_names: defaultdict[str, int]) -> str:
    used_names[base_name] += 1
    if used_names[base_name] == 1:
        return base_name
    return f"{base_name}_{used_names[base_name]}"


def _format_float(value: float) -> str:
    text = f"{value:.12g}"
    return "0" if text == "-0" else text


def _format_vector(values: list[float]) -> str:
    return " ".join(_format_float(value) for value in values)


def _parse_vector(value: str | None, *, default: tuple[float, ...]) -> list[float]:
    if not value:
        return list(default)
    parsed = [float(part) for part in value.split()]
    if len(parsed) != len(default):
        raise ValueError(f"Expected {len(default)} values, got {len(parsed)} in {value!r}")
    return parsed


def _origin_attributes(element: ET.Element | None) -> dict[str, str]:
    if element is None:
        return {}
    attrs: dict[str, str] = {}
    xyz = _parse_vector(element.attrib.get("xyz"), default=(0.0, 0.0, 0.0))
    rpy = _parse_vector(element.attrib.get("rpy"), default=(0.0, 0.0, 0.0))
    if any(value != 0 for value in xyz):
        attrs["pos"] = _format_vector(xyz)
    if any(value != 0 for value in rpy):
        attrs["euler"] = _format_vector(rpy)
    return attrs


def _material_colors(root: ET.Element) -> dict[str, str]:
    colors: dict[str, str] = {}
    for material in root.findall("./material"):
        name = material.attrib.get("name")
        color = material.find("./color")
        rgba = color.attrib.get("rgba") if color is not None else None
        if name and rgba:
            colors[name] = rgba
    for visual in root.findall(".//visual"):
        material = visual.find("./material")
        if material is None:
            continue
        name = material.attrib.get("name")
        color = material.find("./color")
        rgba = color.attrib.get("rgba") if color is not None else None
        if name and rgba:
            colors.setdefault(name, rgba)
    return colors


def _normalize_mesh_scale(value: str | None) -> str | None:
    if not value:
        return None
    return _format_vector(_parse_vector(value, default=(1.0, 1.0, 1.0)))


def _resolve_mesh_path(filename: str, urdf_dir: Path) -> Path:
    parsed = urlparse(filename)
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path))
    elif parsed.scheme == "package":
        package_path = unquote(parsed.path).lstrip("/")
        candidate = urdf_dir / package_path
    elif parsed.scheme:
        raise ValueError(f"Unsupported URDF mesh URI scheme: {parsed.scheme}")
    else:
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = urdf_dir / candidate

    if candidate.exists():
        return candidate

    # Some exporters keep only the mesh basename in package:// URIs. Fall back to
    # the URDF materialization directory so Articraft records remain importable.
    matches = list(urdf_dir.rglob(Path(filename).name))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"URDF mesh file not found: {filename} (looked under {urdf_dir})")


def _relative_xml_path(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _register_mesh_asset(
    mesh: ET.Element,
    *,
    asset: ET.Element,
    urdf_dir: Path,
    output_dir: Path,
    asset_dir: Path,
    mesh_assets: dict[tuple[Path, str | None], str],
    copied_mesh_names: defaultdict[str, int],
    used_mesh_names: defaultdict[str, int],
) -> str:
    filename = mesh.attrib.get("filename")
    if not filename:
        raise ValueError("URDF <mesh> is missing filename")

    source_path = _resolve_mesh_path(filename, urdf_dir).resolve()
    scale = _normalize_mesh_scale(mesh.attrib.get("scale"))
    key = (source_path, scale)
    if key in mesh_assets:
        return mesh_assets[key]

    clean_mesh_name = _unique_name(_clean_name(Path(filename).stem, "mesh"), used_mesh_names)
    clean_file_stem = _unique_name(_clean_name(source_path.stem, "mesh"), copied_mesh_names)
    destination = asset_dir / f"{clean_file_stem}{source_path.suffix}"
    asset_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)

    attrs = {
        "name": clean_mesh_name,
        "file": _relative_xml_path(destination, output_dir),
    }
    if scale is not None:
        attrs["scale"] = scale
    ET.SubElement(asset, "mesh", attrs)
    mesh_assets[key] = clean_mesh_name
    return clean_mesh_name


def _geometry_attributes(
    geometry: ET.Element,
    *,
    asset: ET.Element,
    urdf_dir: Path,
    output_dir: Path,
    asset_dir: Path,
    mesh_assets: dict[tuple[Path, str | None], str],
    copied_mesh_names: defaultdict[str, int],
    used_mesh_names: defaultdict[str, int],
) -> dict[str, str] | None:
    box = geometry.find("./box")
    if box is not None:
        size = _parse_vector(box.attrib.get("size"), default=(0.0, 0.0, 0.0))
        return {"type": "box", "size": _format_vector([value / 2 for value in size])}

    cylinder = geometry.find("./cylinder")
    if cylinder is not None:
        radius = float(cylinder.attrib["radius"])
        length = float(cylinder.attrib["length"])
        return {"type": "cylinder", "size": _format_vector([radius, length / 2])}

    sphere = geometry.find("./sphere")
    if sphere is not None:
        return {"type": "sphere", "size": _format_float(float(sphere.attrib["radius"]))}

    mesh = geometry.find("./mesh")
    if mesh is not None:
        mesh_name = _register_mesh_asset(
            mesh,
            asset=asset,
            urdf_dir=urdf_dir,
            output_dir=output_dir,
            asset_dir=asset_dir,
            mesh_assets=mesh_assets,
            copied_mesh_names=copied_mesh_names,
            used_mesh_names=used_mesh_names,
        )
        return {"type": "mesh", "mesh": mesh_name}

    return None


def _add_material_asset(asset: ET.Element, name: str, rgba: str) -> None:
    ET.SubElement(asset, "material", {"name": _clean_name(name, name), "rgba": rgba})


def _add_geom(
    parent: ET.Element,
    urdf_geom_holder: ET.Element,
    fallback_name: str,
    used_geom_names: defaultdict[str, int],
    *,
    asset: ET.Element,
    urdf_dir: Path,
    output_dir: Path,
    asset_dir: Path,
    mesh_assets: dict[tuple[Path, str | None], str],
    copied_mesh_names: defaultdict[str, int],
    used_mesh_names: defaultdict[str, int],
) -> None:
    geometry = urdf_geom_holder.find("./geometry")
    if geometry is None:
        return
    geom_attrs = _geometry_attributes(
        geometry,
        asset=asset,
        urdf_dir=urdf_dir,
        output_dir=output_dir,
        asset_dir=asset_dir,
        mesh_assets=mesh_assets,
        copied_mesh_names=copied_mesh_names,
        used_mesh_names=used_mesh_names,
    )
    if geom_attrs is None:
        return

    clean_name = _clean_name(urdf_geom_holder.attrib.get("name"), fallback_name)
    attrs = {"name": _unique_name(clean_name, used_geom_names)}
    attrs.update(_origin_attributes(urdf_geom_holder.find("./origin")))
    attrs.update(geom_attrs)

    material = urdf_geom_holder.find("./material")
    material_name = material.attrib.get("name") if material is not None else None
    if material_name:
        attrs["material"] = _clean_name(material_name, material_name)

    ET.SubElement(parent, "geom", attrs)


def _link_geometry_elements(link: ET.Element, geometry_source: str) -> list[ET.Element]:
    if geometry_source == "visual":
        visuals = link.findall("./visual")
        return visuals if visuals else link.findall("./collision")
    if geometry_source == "collision":
        collisions = link.findall("./collision")
        return collisions if collisions else link.findall("./visual")
    return [*link.findall("./visual"), *link.findall("./collision")]


def _joint_type(urdf_type: str) -> str | None:
    return {
        "continuous": "hinge",
        "revolute": "hinge",
        "prismatic": "slide",
        "fixed": None,
    }.get(urdf_type)


def _add_joint(body: ET.Element, joint: ET.Element, used_joint_names: defaultdict[str, int]) -> None:
    mjcf_type = _joint_type(joint.attrib.get("type", "fixed"))
    if mjcf_type is None:
        return

    attrs = {
        "name": _unique_name(_clean_name(joint.attrib.get("name"), "joint"), used_joint_names),
        "type": mjcf_type,
    }
    axis = joint.find("./axis")
    attrs["axis"] = axis.attrib.get("xyz", "1 0 0") if axis is not None else "1 0 0"

    limit = joint.find("./limit")
    if limit is not None and "lower" in limit.attrib and "upper" in limit.attrib:
        attrs["range"] = f"{limit.attrib['lower']} {limit.attrib['upper']}"

    ET.SubElement(body, "joint", attrs)


def _child_body_attributes(link_name: str, joint: ET.Element | None) -> dict[str, str]:
    attrs = {"name": _clean_name(link_name, link_name)}
    if joint is not None:
        attrs.update(_origin_attributes(joint.find("./origin")))
    return attrs


def _build_body_tree(
    parent_xml: ET.Element,
    link_name: str,
    *,
    links: dict[str, ET.Element],
    children_by_parent: dict[str, list[ET.Element]],
    geometry_source: str,
    used_geom_names: defaultdict[str, int],
    used_joint_names: defaultdict[str, int],
    asset: ET.Element,
    urdf_dir: Path,
    output_dir: Path,
    asset_dir: Path,
    mesh_assets: dict[tuple[Path, str | None], str],
    copied_mesh_names: defaultdict[str, int],
    used_mesh_names: defaultdict[str, int],
    inbound_joint: ET.Element | None = None,
) -> None:
    body = ET.SubElement(parent_xml, "body", _child_body_attributes(link_name, inbound_joint))
    if inbound_joint is not None:
        _add_joint(body, inbound_joint, used_joint_names)

    link = links[link_name]
    for index, holder in enumerate(_link_geometry_elements(link, geometry_source)):
        _add_geom(
            body,
            holder,
            f"{link_name}_geom_{index}",
            used_geom_names,
            asset=asset,
            urdf_dir=urdf_dir,
            output_dir=output_dir,
            asset_dir=asset_dir,
            mesh_assets=mesh_assets,
            copied_mesh_names=copied_mesh_names,
            used_mesh_names=used_mesh_names,
        )

    for joint in children_by_parent.get(link_name, []):
        child = joint.find("./child")
        if child is None or "link" not in child.attrib:
            continue
        _build_body_tree(
            body,
            child.attrib["link"],
            links=links,
            children_by_parent=children_by_parent,
            geometry_source=geometry_source,
            used_geom_names=used_geom_names,
            used_joint_names=used_joint_names,
            asset=asset,
            urdf_dir=urdf_dir,
            output_dir=output_dir,
            asset_dir=asset_dir,
            mesh_assets=mesh_assets,
            copied_mesh_names=copied_mesh_names,
            used_mesh_names=used_mesh_names,
            inbound_joint=joint,
        )


def _find_root_links(links: dict[str, ET.Element], joints: list[ET.Element]) -> list[str]:
    children = {
        child.attrib["link"]
        for joint in joints
        for child in [joint.find("./child")]
        if child is not None and "link" in child.attrib
    }
    roots = [name for name in links if name not in children]
    return roots or list(links)


def _assert_unique_names(root: ET.Element) -> None:
    for tag in ("body", "geom", "joint", "material", "mesh"):
        seen: set[str] = set()
        duplicates: list[str] = []
        for element in root.findall(f".//{tag}"):
            name = element.attrib.get("name")
            if not name:
                continue
            if name in seen:
                duplicates.append(name)
            seen.add(name)
        if duplicates:
            joined = ", ".join(sorted(set(duplicates)))
            raise ValueError(f"Duplicate MJCF {tag} name(s): {joined}")


def infer_model_name(urdf_path: str | Path, model_name: str | None = None) -> str:
    root = ET.parse(urdf_path).getroot()
    if root.tag != "robot":
        raise ValueError(f"Expected URDF <robot>, got <{root.tag}>")
    return _clean_name(model_name or root.attrib.get("name"), Path(urdf_path).stem)


def convert_urdf_to_mjcf(
    urdf_path: str | Path,
    output_path: str | Path,
    *,
    model_name: str | None = None,
    geometry_source: str = "visual",
) -> Path:
    urdf_path = Path(urdf_path)
    output_path = Path(output_path)
    root = ET.parse(urdf_path).getroot()
    if root.tag != "robot":
        raise ValueError(f"Expected URDF <robot>, got <{root.tag}>")
    if geometry_source not in {"visual", "collision", "both"}:
        raise ValueError("geometry_source must be one of: visual, collision, both")

    model = ET.Element(
        "mujoco",
        {"model": _clean_name(model_name or root.attrib.get("name"), urdf_path.stem)},
    )
    ET.SubElement(model, "compiler", {"angle": "radian", "autolimits": "true"})
    ET.SubElement(model, "option", {"timestep": "0.002"})

    asset = ET.SubElement(model, "asset")
    for name, rgba in sorted(_material_colors(root).items()):
        _add_material_asset(asset, name, rgba)

    links = {
        link.attrib["name"]: link
        for link in root.findall("./link")
        if "name" in link.attrib
    }
    joints = root.findall("./joint")
    children_by_parent: dict[str, list[ET.Element]] = defaultdict(list)
    for joint in joints:
        parent = joint.find("./parent")
        if parent is not None and "link" in parent.attrib:
            children_by_parent[parent.attrib["link"]].append(joint)

    worldbody = ET.SubElement(model, "worldbody")
    used_geom_names: defaultdict[str, int] = defaultdict(int)
    used_joint_names: defaultdict[str, int] = defaultdict(int)
    used_mesh_names: defaultdict[str, int] = defaultdict(int)
    copied_mesh_names: defaultdict[str, int] = defaultdict(int)
    mesh_assets: dict[tuple[Path, str | None], str] = {}
    asset_dir = output_path.parent / f"{output_path.stem}_assets"
    for root_link in _find_root_links(links, joints):
        _build_body_tree(
            worldbody,
            root_link,
            links=links,
            children_by_parent=children_by_parent,
            geometry_source=geometry_source,
            used_geom_names=used_geom_names,
            used_joint_names=used_joint_names,
            asset=asset,
            urdf_dir=urdf_path.parent,
            output_dir=output_path.parent,
            asset_dir=asset_dir,
            mesh_assets=mesh_assets,
            copied_mesh_names=copied_mesh_names,
            used_mesh_names=used_mesh_names,
        )

    _assert_unique_names(model)
    ET.indent(model, space="  ")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(model).write(output_path, encoding="utf-8", xml_declaration=True)
    return output_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert an Articraft-style URDF to URLab-friendly MJCF."
    )
    parser.add_argument("urdf", type=Path, help="Input URDF path")
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--output", "-o", type=Path, help="Output MJCF XML path")
    output_group.add_argument("--output-dir", type=Path, help="Directory for <model-name>.xml")
    parser.add_argument("--model-name", help="Override MJCF model name")
    parser.add_argument(
        "--geometry-source",
        choices=("visual", "collision", "both"),
        default="visual",
        help="Which URDF geometry to export. Default: visual, falling back to collision per link.",
    )
    args = parser.parse_args(argv)

    if args.output is not None:
        output_path = args.output
    else:
        output_path = args.output_dir / f"{infer_model_name(args.urdf, args.model_name)}.xml"

    output = convert_urdf_to_mjcf(
        args.urdf,
        output_path,
        model_name=args.model_name,
        geometry_source=args.geometry_source,
    )
    print(f"Wrote MJCF to {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
