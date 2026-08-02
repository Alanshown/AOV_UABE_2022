"""Editable plaintext representation of Unity's AssetBundle catalog object."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from typing import Dict, Iterable, Optional, Tuple


SCHEMA = "aov-uabe.assetbundle-metadata/v1"
REQUIRED_FIELDS = {
    "m_Name", "m_PreloadTable", "m_Container", "m_MainAsset",
    "m_AssetBundleName", "m_Dependencies",
}


def _walk_pptrs(value, location="tree") -> Iterable[Tuple[str, dict]]:
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value:
            yield location, value
        for key, child in value.items():
            yield from _walk_pptrs(child, f"{location}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from _walk_pptrs(child, f"{location}[{index}]")


def _summary(tree: dict) -> dict:
    references = list(_walk_pptrs(tree))
    return {
        "name": str(tree.get("m_Name", "")),
        "asset_bundle_name": str(tree.get("m_AssetBundleName", "")),
        "preload_entries": len(tree.get("m_PreloadTable", [])),
        "container_entries": len(tree.get("m_Container", [])),
        "dependencies": list(tree.get("m_Dependencies", [])),
        "pptr_references": len(references),
    }


def _json_value(value):
    """Normalize typetree tuples to their lossless JSON array representation."""
    if isinstance(value, dict):
        return {key: _json_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(child) for child in value]
    return value


def export_assetbundle_metadata(obj, bundle_path: str, output_path: str) -> dict:
    if obj.type.name != "AssetBundle":
        raise ValueError("Selected object is not Unity AssetBundle metadata")
    tree = obj.read_typetree()
    document = {
        "$schema": SCHEMA,
        "target": {
            "bundle_file": os.path.basename(bundle_path),
            "path_id": int(obj.path_id),
            "unity_type": obj.type.name,
        },
        "summary": _summary(tree),
        "tree": tree,
    }
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return {
        "path": os.path.abspath(output_path),
        **document["summary"],
    }


def _validate_tree_shape(tree: dict) -> None:
    if not isinstance(tree, dict):
        raise ValueError("Metadata JSON 'tree' must be an object")
    missing = sorted(REQUIRED_FIELDS.difference(tree))
    if missing:
        raise ValueError(f"Metadata JSON is missing required fields: {', '.join(missing)}")
    if not isinstance(tree["m_PreloadTable"], list):
        raise ValueError("m_PreloadTable must be an array")
    if not isinstance(tree["m_Container"], list):
        raise ValueError("m_Container must be an array")
    if not isinstance(tree["m_Dependencies"], list):
        raise ValueError("m_Dependencies must be an array")


def _validate_references(obj, file_index: int, tree: dict, project=None) -> dict:
    local_objects = {
        int(item.path_id) for item in obj.assets_file.objects.values()
    }
    externals = list(getattr(obj.assets_file, "externals", []))
    checked = 0
    unresolved_external = []
    for location, pointer in _walk_pptrs(tree):
        checked += 1
        file_id = int(pointer["m_FileID"])
        path_id = int(pointer["m_PathID"])
        if file_id < 0:
            raise ValueError(f"{location}: m_FileID cannot be negative")
        if path_id == 0:
            continue
        if file_id == 0:
            if path_id not in local_objects:
                raise ValueError(f"{location}: local PathID {path_id} does not exist")
            continue
        if file_id > len(externals):
            raise ValueError(
                f"{location}: FileID {file_id} exceeds {len(externals)} external files"
            )
        if project is None:
            continue
        target_index, target_obj = project.resolve_pptr(
            obj, int(file_index), pointer
        )
        if target_index is None:
            unresolved_external.append(str(externals[file_id - 1].name))
        elif target_obj is None:
            raise ValueError(
                f"{location}: external PathID {path_id} is absent from "
                f"{externals[file_id - 1].name}"
            )
    return {
        "references_checked": checked,
        "unresolved_external_files": sorted(set(unresolved_external)),
    }


def import_assetbundle_metadata(
    obj, file_index: int, input_path: str, project=None
) -> dict:
    if obj.type.name != "AssetBundle":
        raise ValueError("Selected object is not Unity AssetBundle metadata")
    with open(input_path, "r", encoding="utf-8-sig") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("$schema") != SCHEMA:
        raise ValueError(f"Unsupported metadata schema; expected {SCHEMA}")
    target = document.get("target", {})
    if target.get("unity_type") != "AssetBundle":
        raise ValueError("Metadata JSON target type is not AssetBundle")
    if int(target.get("path_id", obj.path_id)) != int(obj.path_id):
        raise ValueError(
            f"Metadata targets PathID {target.get('path_id')}, selected object is {obj.path_id}"
        )
    tree = deepcopy(document.get("tree"))
    _validate_tree_shape(tree)
    reference_info = _validate_references(obj, file_index, tree, project)
    original_path_id = int(obj.path_id)
    serialized = obj.save_typetree(tree)
    if int(obj.path_id) != original_path_id:
        raise RuntimeError("AssetBundle metadata PathID changed during serialization")
    verified = obj.read_typetree()
    _validate_tree_shape(verified)
    if _json_value(verified) != _json_value(tree):
        raise RuntimeError("AssetBundle metadata typetree reload differs from JSON")
    return {
        "path_id": original_path_id,
        "serialized_bytes": len(serialized),
        **_summary(verified),
        **reference_info,
    }
