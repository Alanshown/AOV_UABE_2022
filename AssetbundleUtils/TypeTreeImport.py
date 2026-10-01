"""Editable, lossless JSON dumps, separate from the truncated display dump."""

from __future__ import annotations

import base64
import hashlib
import json
import math


SCHEMA = "aov-uabe.typetree-dump/v1"


def _encode(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {"$bytes"}:
            return base64.b64decode(value["$bytes"], validate=True)
        if set(value) == {"$tuple"}:
            return tuple(_decode(item) for item in value["$tuple"])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def clone_typetree(value):
    """Detach zero-copy byte views without pickling their backing readers."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, dict):
        return {key: clone_typetree(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(clone_typetree(item) for item in value)
    if isinstance(value, list):
        return [clone_typetree(item) for item in value]
    return value


def _type_signature(obj):
    nodes = obj.get_typetree_nodes()
    signature = [(node.m_Type, node.m_Name, node.m_Level, node.m_MetaFlag)
                 for node in nodes]
    return hashlib.sha256(json.dumps(signature).encode("utf-8")).hexdigest()


def _validate_shape(original, replacement, location="tree"):
    if isinstance(original, dict):
        if not isinstance(replacement, dict) or original.keys() != replacement.keys():
            raise ValueError(f"{location}: TypeTree fields must match the target")
        for key, value in original.items():
            _validate_shape(value, replacement[key], f"{location}.{key}")
    elif isinstance(original, (tuple, list)):
        if not isinstance(replacement, type(original)):
            raise ValueError(f"{location}: expected {type(original).__name__}")
        if isinstance(original, tuple):
            if len(original) != len(replacement):
                raise ValueError(f"{location}: tuple length changed")
            for index, (old, new) in enumerate(zip(original, replacement)):
                _validate_shape(old, new, f"{location}[{index}]")
        elif original:
            for index, item in enumerate(replacement):
                _validate_shape(original[0], item, f"{location}[{index}]")
    elif isinstance(original, (bytes, bytearray, memoryview)):
        if not isinstance(replacement, bytes):
            raise ValueError(f"{location}: expected a byte payload")
    elif isinstance(original, float):
        if isinstance(replacement, bool) or not isinstance(replacement, (int, float)) or not math.isfinite(replacement):
            raise ValueError(f"{location}: expected a finite number")
    elif type(original) is not type(replacement):
        raise ValueError(f"{location}: expected {type(original).__name__}")


def export_typetree_dump(obj, path):
    document = {
        "$schema": SCHEMA,
        "target": {"path_id": int(obj.path_id), "type": obj.type.name,
                   "unity_version": list(obj.version), "typetree_sha256": _type_signature(obj)},
        "tree": _encode(obj.read_typetree()),
    }
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def import_typetree_dump(obj, path):
    with open(path, encoding="utf-8-sig") as handle:
        document = json.load(handle)
    if document.get("$schema") != SCHEMA:
        raise ValueError("Import requires a complete editable JSON Dump, not a display/hex dump")
    target = document.get("target", {})
    if (target.get("path_id") != int(obj.path_id) or target.get("type") != obj.type.name
            or target.get("unity_version") != list(obj.version)
            or target.get("typetree_sha256") != _type_signature(obj)):
        raise ValueError("Dump target PathID/type/Unity version/TypeTree differs from the selected asset")
    tree = _decode(document["tree"])
    original_tree = obj.read_typetree()
    _validate_shape(original_tree, tree)
    validate_changed_references(obj, original_tree, tree)
    original = obj.get_raw_data()
    try:
        obj.save_typetree(tree)
        obj.read_typetree()
        if obj.type.name == "Mesh":
            from AssetbundleUtils.MeshImport import validate_mesh_payload
            validate_mesh_payload(obj)
    except Exception:
        obj.set_raw_data(original)
        raise
    return obj


def _references(value):
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value:
            yield int(value["m_FileID"]), int(value["m_PathID"])
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _references(item)


def validate_changed_references(obj, original, replacement):
    """Preserve existing dependencies; reject new dangling local/FileID pointers."""
    old = set(_references(original))
    for file_id, path_id in set(_references(replacement)) - old:
        if path_id == 0:
            continue
        if file_id == 0 and path_id not in obj.assets_file.objects:
            raise ValueError(f"Dump references missing local PathID {path_id}")
        if file_id < 0 or file_id > len(obj.assets_file.externals):
            raise ValueError(f"Dump references invalid external FileID {file_id}")
