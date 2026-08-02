"""Lossless AssetBundle project export, validation, and reconstruction."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import json
import os
import re
import shutil
import struct
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

from PIL import Image

from AssetbundleUtils import UnityPy_AOV
from AssetbundleUtils.TextureImport import (
    optimize_texture_runtime_storage,
    replace_texture_image,
    texture_runtime_metadata,
    validate_texture_roundtrip,
)
from AssetbundleUtils.UnityPy_AOV.enums import TextureFormat
from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType
from AssetbundleUtils.UnityPy_AOV.classes.AssetBundle import AssetInfo
from AssetbundleUtils.UnityPy_AOV.classes.PPtr import PPtr
from AssetbundleUtils.UnityPy_AOV.files.SerializedFile import SerializedType
from AssetbundleUtils.UnityPy_AOV.helpers.ResourceReader import get_resource_data
from AssetbundleUtils.UnityPy_AOV.streams import (
    EndianBinaryReader,
    EndianBinaryWriter,
)


SCHEMA_V1 = "aov-uabe.bundle-project/v1"
SCHEMA = "aov-uabe.bundle-project/v2"
ASSET_INDEX_SCHEMA = "aov-uabe.asset-index/v1"
ASSET_INDEX_JSON = "asset_index.json"
ASSET_INDEX_CSV = "asset_index.csv"


TYPE_DIRECTORIES = {
    "Texture2D": "texture",
    "Sprite": "sprite",
    "SpriteAtlas": "spriteatlas",
    "Mesh": "mesh",
    "GameObject": "object",
    "AssetBundle": "assetbundle",
    "TextAsset": "text",
    "Material": "material",
    "Shader": "shader",
    "AnimationClip": "animation",
    "AudioClip": "audio",
    "MonoBehaviour": "monobehaviour",
    "ParticleSystem": "particlesystem",
    "ParticleSystemRenderer": "particlesystemrenderer",
    "RectTransform": "recttransform",
    "Transform": "transform",
    "SortingGroup": "sortinggroup",
    "MeshRenderer": "meshrenderer",
    "MeshFilter": "meshfilter",
    "SpriteRenderer": "spriterenderer",
    "CanvasRenderer": "canvasrenderer",
}
DIRECTORY_TYPES = {value: key for key, value in TYPE_DIRECTORIES.items()}


def _asset_file_stem(name: str, path_id: int, type_name: str) -> str:
    return (
        f"{_safe_filename(name, type_name)}_{int(path_id)}"
    )


def _parse_asset_file_stem(stem: str) -> Tuple[str, int]:
    """Parse ``<asset name>_<PathID>`` at the final underscore only."""

    match = re.fullmatch(r"(.+)_(-?\d+)", str(stem))
    if not match:
        raise ValueError(
            f"Asset file '{stem}' must end in _<numeric PathID>"
        )
    name = match.group(1)
    path_id = int(match.group(2))
    if path_id == 0:
        raise ValueError(f"Asset file '{stem}' uses reserved PathID 0")
    return name, path_id


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _shallow_clone(value):
    """Clone UnityPy records without invoking proxy ``__getattr__`` hooks."""

    clone = object.__new__(value.__class__)
    clone.__dict__.update(value.__dict__)
    return clone


def _safe_filename(value: str, fallback: str = "asset") -> str:
    visible = []
    for character in str(value or ""):
        codepoint = ord(character)
        if (
            unicodedata.category(character) in ("Cc", "Cf", "Co", "Cs")
            or codepoint == 0
        ):
            visible.append(f"U{codepoint:04X}")
        else:
            visible.append(character)
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", "".join(visible))
    cleaned = cleaned.strip(" .")[:100]
    return cleaned or fallback


def _node_bytes(node) -> bytes:
    if hasattr(node, "bytes"):
        return bytes(node.bytes)
    return bytes(node.save())


def _object_name(obj) -> str:
    try:
        name = obj.peek_name(None)
        if name is not None:
            return str(name)
    except Exception:
        pass
    try:
        data = obj.read(False)
        return str(getattr(data, "m_Name", "") or "")
    except Exception:
        return ""


def _write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _write_json(path: str, value) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _index_record(item: dict) -> dict:
    stream = item.get("stream") or {}
    return {
        "type": item["type"],
        "name": item.get("name", ""),
        "path_id": int(item["path_id"]),
        "file_stem": item.get(
            "file_stem",
            _asset_file_stem(
                item.get("name", ""), item["path_id"], item["type"]
            ),
        ),
        "raw": item["raw"],
        "editable": item.get("editable", ""),
        "source_node": item.get("source_node", ""),
        "bytes": int(item.get("bytes", 0)),
        "sha256": item.get("sha256", ""),
        "stream_payload": stream.get("payload", ""),
        "stream_bytes": int(stream.get("size", 0)),
        "stream_sha256": stream.get("payload_sha256", ""),
        "stream_path": stream.get("path", stream.get("original_path", "")),
    }


def _write_index_files(
    json_path: str,
    csv_path: str,
    records: List[dict],
    *,
    bundle_name: str,
) -> None:
    ordered = sorted(
        (_index_record(item) for item in records),
        key=lambda item: (
            item["type"].casefold(),
            item["name"].casefold(),
            item["path_id"],
        ),
    )
    _write_json(
        json_path,
        {
            "$schema": ASSET_INDEX_SCHEMA,
            "bundle": bundle_name,
            "assets": ordered,
        },
    )
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    fields = [
        "type", "name", "path_id", "file_stem", "raw", "editable",
        "source_node", "bytes", "sha256", "stream_payload",
        "stream_bytes", "stream_sha256", "stream_path",
    ]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(ordered)


def _write_asset_indexes(project_dir: str, manifest: dict) -> None:
    """Write one global record table and one table in every type directory."""

    assets = list(manifest.get("assets", []))
    bundle_name = manifest.get("source", {}).get("file_name", "")
    _write_index_files(
        os.path.join(project_dir, ASSET_INDEX_JSON),
        os.path.join(project_dir, ASSET_INDEX_CSV),
        assets,
        bundle_name=bundle_name,
    )
    assets_root = os.path.join(project_dir, "assets")
    by_directory = {}
    for item in assets:
        raw_parts = item["raw"].replace("\\", "/").split("/")
        directory = raw_parts[1] if len(raw_parts) > 2 else TYPE_DIRECTORIES.get(
            item["type"], _safe_filename(item["type"].lower(), "unknown")
        )
        by_directory.setdefault(directory, []).append(item)
    existing = (
        [
            name for name in os.listdir(assets_root)
            if os.path.isdir(os.path.join(assets_root, name))
        ]
        if os.path.isdir(assets_root)
        else []
    )
    for directory in sorted(set(existing).union(by_directory)):
        type_dir = os.path.join(assets_root, directory)
        os.makedirs(type_dir, exist_ok=True)
        _write_index_files(
            os.path.join(type_dir, ASSET_INDEX_JSON),
            os.path.join(type_dir, ASSET_INDEX_CSV),
            by_directory.get(directory, []),
            bundle_name=bundle_name,
        )


def _relative(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _available_project_directory(output_root: str, source_path: str) -> str:
    stem = _safe_filename(os.path.splitext(os.path.basename(source_path))[0], "bundle")
    candidate = os.path.join(os.path.abspath(output_root), stem)
    if not os.path.exists(candidate):
        return candidate
    suffix = 1
    while os.path.exists(f"{candidate}_{suffix}"):
        suffix += 1
    return f"{candidate}_{suffix}"


def _recommended_packer(bundle) -> str:
    storage = getattr(bundle, "special_storage_format", None)
    flags = int(getattr(bundle, "dataflags", 0))
    if storage == "aov-sm4-blockinfo-at-end-lzma" or (
        storage and flags == 0x6C1
    ):
        return "aov-fingerprint-1"
    if storage == "aov-sm4-blockinfo-prefix-lz4hc" or (
        storage and flags == 0x643
    ):
        return "aov-fingerprint-2"
    if storage == "aov-sm4-blockinfo-prefix-lzma" or (
        storage and flags == 0x641
    ):
        return "aov-fingerprint-3"
    if storage is None:
        return "original"
    return "lz4"


def _pointer(pointer) -> Dict[str, int]:
    return {
        "file_id": int(pointer.file_id),
        "path_id": int(pointer.path_id),
    }


def validate_sprite_atlas_relationships(environment) -> Dict[str, object]:
    objects = list(environment.objects)
    local_ids = {int(obj.path_id) for obj in objects}
    bundles = [
        (obj, obj.read(False))
        for obj in objects
        if obj.type.name == "AssetBundle"
    ]
    atlases = [
        (obj, obj.read(False))
        for obj in objects
        if obj.type.name == "SpriteAtlas"
    ]
    preload = {
        (int(pointer.file_id), int(pointer.path_id))
        for _reader, bundle in bundles
        for pointer in bundle.m_PreloadTable
    }
    issues = []
    relationships = []

    for atlas_reader, atlas in atlases:
        packed = [_pointer(pointer) for pointer in atlas.m_PackedSprites]
        render_texture_ids = sorted(
            {
                int(data.texture.path_id)
                for data in atlas.m_RenderDataMap.values()
                if int(data.texture.file_id) == 0 and int(data.texture.path_id)
            }
        )
        for texture_path_id in render_texture_ids:
            if texture_path_id not in local_ids:
                issues.append(
                    {
                        "kind": "missing_spriteatlas_texture",
                        "atlas_path_id": int(atlas_reader.path_id),
                        "path_id": texture_path_id,
                    }
                )
        relationships.append(
            {
                "kind": "sprite_atlas",
                "atlas_path_id": int(atlas_reader.path_id),
                "name": atlas.m_Name,
                "packed_sprites": packed,
                "packed_names": list(atlas.m_PackedSpriteNamesToIndex),
                "render_data_count": len(atlas.m_RenderDataMap),
                "texture_path_ids": render_texture_ids,
            }
        )
        if len(packed) != len(atlas.m_PackedSpriteNamesToIndex):
            issues.append(
                {
                    "kind": "atlas_name_count",
                    "atlas_path_id": int(atlas_reader.path_id),
                    "packed": len(packed),
                    "names": len(atlas.m_PackedSpriteNamesToIndex),
                }
            )
        if len(packed) != len(atlas.m_RenderDataMap):
            issues.append(
                {
                    "kind": "atlas_render_data_count",
                    "atlas_path_id": int(atlas_reader.path_id),
                    "packed": len(packed),
                    "render_data": len(atlas.m_RenderDataMap),
                }
            )
        for pointer in packed:
            key = (pointer["file_id"], pointer["path_id"])
            if pointer["file_id"] == 0 and pointer["path_id"] not in local_ids:
                issues.append(
                    {
                        "kind": "missing_local_sprite",
                        "atlas_path_id": int(atlas_reader.path_id),
                        **pointer,
                    }
                )
            if bundles and key not in preload:
                issues.append(
                    {
                        "kind": "missing_assetbundle_preload",
                        "atlas_path_id": int(atlas_reader.path_id),
                        **pointer,
                    }
                )

    sprite_count = 0
    for reader in objects:
        if reader.type.name != "Sprite":
            continue
        sprite_count += 1
        try:
            sprite = reader.read(False)
        except Exception as exc:
            issues.append(
                {
                    "kind": "sprite_parse_error",
                    "path_id": int(reader.path_id),
                    "error": str(exc),
                }
            )
            continue
        atlas_pointer = _pointer(sprite.m_SpriteAtlas)
        render_key_exists = False
        if atlas_pointer["file_id"] == 0 and atlas_pointer["path_id"]:
            for atlas_reader, atlas in atlases:
                if int(atlas_reader.path_id) == atlas_pointer["path_id"]:
                    render_key_exists = sprite.m_RenderDataKey in atlas.m_RenderDataMap
                    break
            if not render_key_exists:
                issues.append(
                    {
                        "kind": "missing_sprite_render_data",
                        "path_id": int(reader.path_id),
                        "name": sprite.m_Name,
                        "atlas_path_id": atlas_pointer["path_id"],
                    }
                )

    return {
        "assetbundle_count": len(bundles),
        "spriteatlas_count": len(atlases),
        "sprite_count": sprite_count,
        "relationships": relationships,
        "issues": issues,
    }


def repair_sprite_atlas_preloads(environment) -> List[int]:
    """Add only demonstrably missing local SpriteAtlas entries to preload tables."""

    objects = list(environment.objects)
    bundles = [
        obj.read(False) for obj in objects if obj.type.name == "AssetBundle"
    ]
    if not bundles:
        return []
    packed = []
    for reader in objects:
        if reader.type.name == "SpriteAtlas":
            packed.extend(reader.read(False).m_PackedSprites)
    added = []
    for bundle in bundles:
        existing = {
            (int(pointer.file_id), int(pointer.path_id))
            for pointer in bundle.m_PreloadTable
        }
        changed = False
        for pointer in packed:
            key = (int(pointer.file_id), int(pointer.path_id))
            if key not in existing:
                bundle.m_PreloadTable.append(pointer)
                existing.add(key)
                added.append(int(pointer.path_id))
                changed = True
        if changed:
            bundle.save()
    return added


def _asset_relationship(obj) -> Optional[dict]:
    if obj.type.name not in {"Texture2D", "Sprite", "AssetBundle"}:
        return None
    try:
        data = obj.read(False)
    except Exception:
        return None
    if obj.type.name == "Texture2D":
        stream = getattr(data, "m_StreamData", None)
        return {
            "kind": "texture_stream",
            "path_id": int(obj.path_id),
            "width": int(data.m_Width),
            "height": int(data.m_Height),
            "format": data.m_TextureFormat.name,
            "mip_count": int(getattr(data, "m_MipCount", 1)),
            "complete_image_size": int(data.m_CompleteImageSize),
            "stream": {
                "path": str(getattr(stream, "path", "")),
                "offset": int(getattr(stream, "offset", 0)),
                "size": int(getattr(stream, "size", 0)),
            },
        }
    if obj.type.name == "Sprite":
        key = getattr(data, "m_RenderDataKey", (b"", 0))
        return {
            "kind": "sprite",
            "path_id": int(obj.path_id),
            "atlas": _pointer(data.m_SpriteAtlas),
            "render_data_key": [bytes(key[0]).hex(), int(key[1])],
            "atlas_tags": list(getattr(data, "m_AtlasTags", [])),
        }
    if obj.type.name == "AssetBundle":
        return {
            "kind": "assetbundle_catalog",
            "path_id": int(obj.path_id),
            "preload": [_pointer(pointer) for pointer in data.m_PreloadTable],
            "container": [
                {
                    "key": key,
                    "preload_index": int(value.preload_index),
                    "preload_size": int(value.preload_size),
                    "asset": _pointer(value.asset),
                }
                for key, value in data.m_ContainerEntries
            ],
            "dependencies": list(data.m_Dependencies),
        }
    return None


def _stream_descriptor(obj, parsed=None) -> Optional[dict]:
    """Return the external resource slice owned by a Unity object."""

    type_name = obj.type.name
    if type_name not in {"Texture2D", "Mesh", "AudioClip", "VideoClip"}:
        return None
    data = parsed if parsed is not None else obj.read(False)
    if type_name in {"Texture2D", "Mesh"}:
        stream = getattr(data, "m_StreamData", None)
        if stream is None:
            return None
        path = str(getattr(stream, "path", "") or "")
        offset = int(getattr(stream, "offset", 0))
        size = int(getattr(stream, "size", 0))
        kind = "streaming_info"
    elif type_name == "AudioClip":
        path = str(getattr(data, "m_Source", "") or "")
        offset = int(getattr(data, "m_Offset", 0))
        size = int(getattr(data, "m_Size", 0))
        kind = "audio"
    else:
        path = str(getattr(data, "source", "") or "")
        offset = int(getattr(data, "offset", 0))
        size = int(getattr(data, "size", 0))
        kind = "video"
    if not path or size <= 0:
        return None
    return {
        "kind": kind,
        "path": path,
        "offset": offset,
        "size": size,
    }


def _export_stream_payload(
    obj,
    parsed,
    asset_dir: str,
    base: str,
    project_dir: str,
) -> Optional[dict]:
    descriptor = _stream_descriptor(obj, parsed)
    if descriptor is None:
        return None
    payload = bytes(
        get_resource_data(
            descriptor["path"],
            obj.assets_file,
            descriptor["offset"],
            descriptor["size"],
        )
    )
    if len(payload) != descriptor["size"]:
        raise ValueError(
            f"{obj.type.name} {obj.path_id} external resource is truncated: "
            f"expected {descriptor['size']} bytes, got {len(payload)}"
        )
    payload_path = os.path.join(asset_dir, f"{base}.resS")
    _write_bytes(payload_path, payload)
    return {
        "kind": descriptor["kind"],
        "original_path": descriptor["path"],
        "path": descriptor["path"],
        "offset": descriptor["offset"],
        "size": len(payload),
        "payload": _relative(payload_path, project_dir),
        "payload_sha256": _sha256(payload),
    }


def _export_decoded_asset(obj, data, asset_dir: str, base: str) -> Dict[str, object]:
    exported = {}
    try:
        if obj.type.name in ("Texture2D", "Sprite"):
            path = os.path.join(asset_dir, f"{base}.png")
            data.image.save(path)
            raw = open(path, "rb").read()
            exported["editable"] = os.path.basename(path)
            exported["editable_sha256"] = _sha256(raw)
        elif obj.type.name == "Mesh":
            path = os.path.join(asset_dir, f"{base}.obj")
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(data.export())
            exported["editable"] = os.path.basename(path)
            exported["editable_sha256"] = _sha256(open(path, "rb").read())
        elif obj.type.name == "TextAsset":
            script = getattr(data, "script", getattr(data, "m_Script", b""))
            if isinstance(script, str):
                script = script.encode("utf-8")
            path = os.path.join(asset_dir, f"{base}.bin")
            _write_bytes(path, bytes(script))
            exported["editable"] = os.path.basename(path)
            exported["editable_sha256"] = _sha256(bytes(script))
        elif obj.type.name in {
            "MonoBehaviour", "MonoScript", "AssetBundle", "Material",
            "Shader", "GameObject",
        }:
            tree = obj.read_typetree()
            path = os.path.join(asset_dir, f"{base}.json")
            _write_json(path, tree)
            raw = open(path, "rb").read()
            exported["editable"] = os.path.basename(path)
            exported["editable_sha256"] = _sha256(raw)
    except Exception as exc:
        exported["editable_error"] = str(exc)
    return exported


def export_bundle_project(
    source_path: str,
    output_root: str,
    *,
    export_decoded: bool = True,
) -> Dict[str, object]:
    """Export every object raw plus decoded supported assets and relationship JSON."""

    source_path = os.path.abspath(source_path)
    project_dir = _available_project_directory(output_root, source_path)
    os.makedirs(project_dir, exist_ok=False)
    backup_dir = os.path.join(project_dir, "backup")
    internal_dir = os.path.join(project_dir, "internal")
    assets_root = os.path.join(project_dir, "assets")
    os.makedirs(backup_dir)
    os.makedirs(internal_dir)
    os.makedirs(assets_root)

    backup_path = os.path.join(backup_dir, os.path.basename(source_path))
    shutil.copy2(source_path, backup_path)
    environment = UnityPy_AOV.load(source_path)
    bundle = environment.file

    nodes = []
    for name, node in bundle.files.items():
        data = _node_bytes(node)
        node_path = os.path.join(internal_dir, _safe_filename(name, "node"))
        _write_bytes(node_path, data)
        kind = (
            "serialized"
            if hasattr(node, "objects")
            else "resource"
        )
        nodes.append(
            {
                "name": name,
                "kind": kind,
                "flags": int(getattr(node, "flags", 0)),
                "bytes": len(data),
                "sha256": _sha256(data),
                "path": _relative(node_path, project_dir),
            }
        )

    assets = []
    relationships = []
    for obj in environment.objects:
        type_name = obj.type.name
        type_dir_name = TYPE_DIRECTORIES.get(
            type_name, _safe_filename(type_name.lower(), "unknown")
        )
        asset_dir = os.path.join(assets_root, type_dir_name)
        os.makedirs(asset_dir, exist_ok=True)
        name = _object_name(obj)
        base = _asset_file_stem(name, int(obj.path_id), type_name)
        raw_data = bytes(obj.get_raw_data())
        raw_path = os.path.join(asset_dir, f"{base}.raw")
        _write_bytes(raw_path, raw_data)
        item = {
            "path_id": int(obj.path_id),
            "type": type_name,
            "name": name,
            "file_stem": base,
            "source_node": str(getattr(obj.assets_file, "name", "") or ""),
            "bytes": len(raw_data),
            "sha256": _sha256(raw_data),
            "raw": _relative(raw_path, project_dir),
        }
        relation = _asset_relationship(obj)
        if relation is not None:
            relationships.append(relation)
        try:
            parsed = obj.read(False)
            stream = _export_stream_payload(
                obj, parsed, asset_dir, base, project_dir
            )
            if stream is not None:
                item["stream"] = stream
            if export_decoded:
                decoded = _export_decoded_asset(obj, parsed, asset_dir, base)
                if "editable" in decoded:
                    decoded["editable"] = (
                        f"assets/{type_dir_name}/{decoded['editable']}"
                    )
                item.update(decoded)
        except Exception as exc:
            item["parse_error"] = str(exc)
        assets.append(item)

    atlas_validation = validate_sprite_atlas_relationships(environment)
    manifest = {
        "$schema": SCHEMA,
        "source": {
            "path": source_path,
            "file_name": os.path.basename(source_path),
            "bytes": os.path.getsize(source_path),
            "sha256": _sha256(open(source_path, "rb").read()),
            "backup": _relative(backup_path, project_dir),
            # Kept as a compatibility alias for older GUI builds and scripts.
            "template": _relative(backup_path, project_dir),
        },
        "unityfs": {
            "signature": str(bundle.signature),
            "version": int(bundle.version),
            "engine": str(bundle.version_engine),
            "player": str(bundle.version_player),
            "flags": int(bundle.dataflags),
            "special_storage": bundle.special_storage_format,
            "recommended_packer": _recommended_packer(bundle),
        },
        "inventory": dict(
            sorted(Counter(obj.type.name for obj in environment.objects).items())
        ),
        "nodes": nodes,
        # Immutable inventory of the AB copied to backup/. Directory rebuilds
        # always diff against this list, even after the live indexes are
        # refreshed to include newly added objects.
        "backup_assets": [dict(item) for item in assets],
        "assets": assets,
        "relationships": relationships,
        "sprite_atlas_validation": atlas_validation,
    }
    manifest_path = os.path.join(project_dir, "bundle_manifest.json")
    _write_json(manifest_path, manifest)
    _write_asset_indexes(project_dir, manifest)
    return {
        "path": project_dir,
        "manifest": manifest_path,
        "index": os.path.join(project_dir, ASSET_INDEX_JSON),
        "backup": backup_path,
        "assets": len(assets),
        "types": len(manifest["inventory"]),
        "nodes": len(nodes),
        "relationship_issues": len(atlas_validation["issues"]),
    }


def stage_cross_bundle_assets(
    project_dir: str,
    selections: Dict[str, Iterable[int]],
    *,
    remove_path_ids: Iterable[int] = (),
) -> dict:
    """Stage proven cross-bundle objects into an exported project.

    External PPtrs are rebased automatically: selected donor objects become
    local references and external CAB references are mapped by exact CAB path.
    Missing serialized type hashes are carried in the project manifest.
    """

    project_dir = os.path.abspath(project_dir)
    manifest = _load_manifest(project_dir)
    backup_path = os.path.join(
        project_dir, *manifest["source"]["backup"].split("/")
    )
    target_environment = UnityPy_AOV.load(backup_path)
    target_assets_file = next(iter(target_environment.assets), None)
    if target_assets_file is None:
        raise ValueError("Target bundle has no serialized CAB")
    target_external_ids = {
        str(external.path): index
        for index, external in enumerate(
            target_assets_file.externals, start=1
        )
    }
    selected_ids = {
        int(path_id)
        for path_ids in selections.values()
        for path_id in path_ids
    }
    existing_ids = {int(obj.path_id) for obj in target_environment.objects}
    collisions = sorted(selected_ids & existing_ids)
    if collisions:
        raise ValueError(
            "Cross-bundle import PathIDs already exist in the target: "
            + ", ".join(str(value) for value in collisions[:16])
        )

    removed = []
    remove_ids = {int(value) for value in remove_path_ids}
    for item in manifest.get("assets", []):
        if int(item["path_id"]) not in remove_ids:
            continue
        for field in ("raw", "editable"):
            relative = item.get(field)
            if relative:
                path = os.path.join(project_dir, *relative.split("/"))
                if os.path.isfile(path):
                    os.remove(path)
        stream = item.get("stream") or {}
        if stream.get("payload"):
            path = os.path.join(
                project_dir, *stream["payload"].split("/")
            )
            if os.path.isfile(path):
                os.remove(path)
        removed.append(
            {
                "type": item["type"],
                "path_id": int(item["path_id"]),
                "name": item.get("name", ""),
            }
        )

    imported = []
    imported_types = list(manifest.get("imported_types", []))
    target_type_keys = {
        (
            int(item.class_id),
            bytes(getattr(item, "old_type_hash", b"")).hex(),
        )
        for item in target_assets_file.types
    }
    known_imported_type_keys = {
        (int(item["class_id"]), str(item["old_type_hash"]).lower())
        for item in imported_types
    }
    import_sources = []
    for source_path, requested_ids in selections.items():
        source_path = os.path.abspath(source_path)
        environment = UnityPy_AOV.load(source_path)
        requested = {int(value) for value in requested_ids}
        source_objects = {
            int(obj.path_id): obj for obj in environment.objects
        }
        missing = sorted(requested - set(source_objects))
        if missing:
            raise ValueError(
                f"Donor {source_path} is missing PathIDs {missing[:16]}"
            )
        pointer_mappings = {}
        for bundle_obj in environment.objects:
            if bundle_obj.type.name != "AssetBundle":
                continue
            donor_bundle = bundle_obj.read(False)
            for pointer in donor_bundle.m_PreloadTable:
                file_id = int(pointer.file_id)
                path_id = int(pointer.path_id)
                if file_id <= 0:
                    continue
                if path_id in selected_ids:
                    target_file_id = 0
                else:
                    external = pointer.assets_file.externals[file_id - 1]
                    target_file_id = target_external_ids.get(
                        str(external.path)
                    )
                    if target_file_id is None:
                        continue
                pointer_mappings[(file_id, path_id)] = (
                    target_file_id, path_id
                )
        for path_id in sorted(requested):
            obj = source_objects[path_id]
            if obj.type.name == "AssetBundle":
                raise ValueError(
                    "A donor AssetBundle catalog cannot be nested as an "
                    "ordinary object; select its referenced assets instead"
                )
            raw = bytes(obj.get_raw_data())
            endian = obj.reader.endian
            for (old_file_id, old_path_id), (
                new_file_id, new_path_id
            ) in pointer_mappings.items():
                old = struct.pack(
                    endian + "iq", old_file_id, old_path_id
                )
                if old not in raw:
                    continue
                new = struct.pack(
                    endian + "iq", new_file_id, new_path_id
                )
                raw = raw.replace(old, new)
            type_name = obj.type.name
            name = _object_name(obj)
            serialized = obj.serialized_type
            serialized_type_hash = bytes(
                getattr(serialized, "old_type_hash", b"")
            ).hex()
            type_directory = TYPE_DIRECTORIES.get(
                type_name,
                _safe_filename(type_name.lower(), "unknown"),
            )
            asset_dir = os.path.join(
                project_dir, "assets", type_directory
            )
            os.makedirs(asset_dir, exist_ok=True)
            stem = _asset_file_stem(name, path_id, type_name)
            raw_path = os.path.join(asset_dir, f"{stem}.raw")
            if os.path.exists(raw_path):
                raise ValueError(f"Import destination already exists: {raw_path}")
            _write_bytes(raw_path, raw)
            record = {
                "type": type_name,
                "path_id": path_id,
                "name": name,
                "raw": _relative(raw_path, project_dir),
                "source_bundle": source_path,
                "serialized_type_hash": serialized_type_hash,
                "rebased_external_pointers": sum(
                    raw.count(
                        struct.pack(endian + "iq", target_id, pointer_id)
                    )
                    for target_id, pointer_id
                    in pointer_mappings.values()
                ),
            }
            manifest_item = {
                "type": type_name,
                "path_id": path_id,
                "name": name,
                "file_stem": stem,
                "source_node": target_assets_file.name,
                "bytes": len(raw),
                "sha256": _sha256(raw),
                "raw": record["raw"],
                "serialized_type_hash": serialized_type_hash,
            }
            parsed = None
            try:
                parsed = obj.read(False)
            except Exception:
                pass
            descriptor = (
                _stream_descriptor(obj, parsed)
                if parsed is not None
                else None
            )
            if descriptor is not None:
                payload = bytes(
                    get_resource_data(
                        descriptor["path"],
                        obj.assets_file,
                        descriptor["offset"],
                        descriptor["size"],
                    )
                )
                payload_path = os.path.join(
                    asset_dir, f"{stem}.resS"
                )
                _write_bytes(payload_path, payload)
                record["stream_payload"] = _relative(
                    payload_path, project_dir
                )
                record["stream_sha256"] = _sha256(payload)
                manifest_item["stream"] = {
                    "kind": descriptor["kind"],
                    "original_path": descriptor["path"],
                    "path": descriptor["path"],
                    "offset": descriptor["offset"],
                    "size": len(payload),
                    "payload": record["stream_payload"],
                    "payload_sha256": record["stream_sha256"],
                }
            imported.append(record)
            manifest.setdefault("assets", []).append(manifest_item)

            class_id = int(obj.class_id)
            serialized_type_key = (class_id, serialized_type_hash.lower())
            if (
                serialized_type_key not in target_type_keys
                and serialized_type_key not in known_imported_type_keys
            ):
                descriptor_type = {
                    "target_node": target_assets_file.name,
                    "class_id": class_id,
                    "is_stripped_type": bool(
                        getattr(serialized, "is_stripped_type", False)
                    ),
                    "script_type_index": int(
                        getattr(serialized, "script_type_index", -1)
                    ),
                    "old_type_hash": bytes(
                        serialized.old_type_hash
                    ).hex(),
                }
                if hasattr(serialized, "script_id"):
                    descriptor_type["script_id"] = bytes(
                        serialized.script_id
                    ).hex()
                imported_types.append(descriptor_type)
                known_imported_type_keys.add(serialized_type_key)
        import_sources.append(
            {
                "path": source_path,
                "sha256": _sha256(open(source_path, "rb").read()),
                "assets": len(requested),
            }
        )

    manifest["imported_types"] = imported_types
    manifest.setdefault("import_sources", []).extend(import_sources)
    _write_json(
        os.path.join(project_dir, "bundle_manifest.json"), manifest
    )
    report = {
        "project": project_dir,
        "removed": removed,
        "imported": imported,
        "imported_types": imported_types,
        "sources": import_sources,
    }
    report_path = os.path.join(
        project_dir, "cross_bundle_import_report.json"
    )
    if os.path.isfile(report_path):
        with open(report_path, "r", encoding="utf-8-sig") as handle:
            previous = json.load(handle)
        report["removed"] = previous.get("removed", []) + report["removed"]
        report["imported"] = previous.get("imported", []) + report["imported"]
        report["sources"] = previous.get("sources", []) + report["sources"]
    _write_json(report_path, report)
    return report


def _load_manifest(project_dir: str) -> dict:
    path = os.path.join(os.path.abspath(project_dir), "bundle_manifest.json")
    with open(path, "r", encoding="utf-8-sig") as handle:
        manifest = json.load(handle)
    schema = manifest.get("$schema")
    if schema not in {SCHEMA, SCHEMA_V1}:
        raise ValueError(
            f"Unsupported bundle project schema; expected {SCHEMA} "
            f"or legacy {SCHEMA_V1}"
        )
    source = manifest.setdefault("source", {})
    if "backup" not in source and "template" in source:
        source["backup"] = source["template"]
    return manifest


def _restore_resource_nodes(bundle, project_dir: str, manifest: dict) -> None:
    for node_info in manifest["nodes"]:
        if node_info["kind"] != "resource":
            continue
        path = os.path.join(project_dir, *node_info["path"].split("/"))
        data = open(path, "rb").read()
        reader = EndianBinaryReader(data)
        reader.flags = int(node_info.get("flags", 0))
        reader.name = node_info["name"]
        bundle.files[node_info["name"]] = reader


def _install_imported_types(environment, manifest: dict) -> None:
    """Install serialized type hashes carried by an automatic cross-AB import."""

    descriptors = manifest.get("imported_types", [])
    if not descriptors:
        return
    assets_files = {
        str(getattr(item, "name", "") or ""): item
        for item in environment.assets
    }
    for descriptor in descriptors:
        source_node = descriptor.get("target_node", "")
        assets_file = assets_files.get(source_node)
        if assets_file is None:
            assets_file = next(iter(environment.assets), None)
        if assets_file is None:
            raise ValueError("Bundle has no serialized file for imported types")
        class_id = int(descriptor["class_id"])
        old_type_hash = bytes.fromhex(descriptor["old_type_hash"])
        if any(
            int(item.class_id) == class_id
            and bytes(getattr(item, "old_type_hash", b"")) == old_type_hash
            for item in assets_file.types
        ):
            continue
        serialized = object.__new__(SerializedType)
        serialized.class_id = class_id
        serialized.is_stripped_type = bool(
            descriptor.get("is_stripped_type", False)
        )
        serialized.script_type_index = int(
            descriptor.get("script_type_index", -1)
        )
        serialized.old_type_hash = old_type_hash
        if descriptor.get("script_id"):
            serialized.script_id = bytes.fromhex(descriptor["script_id"])
        assets_file.types.append(serialized)
        assets_file.mark_changed()


def _apply_legacy_project_objects(environment, project_dir: str, manifest: dict):
    lookup = {
        (int(obj.path_id), obj.type.name): obj for obj in environment.objects
    }
    expected = {
        (int(item["path_id"]), item["type"]) for item in manifest["assets"]
    }
    if set(lookup) != expected:
        missing = sorted(expected.difference(lookup))
        extra = sorted(set(lookup).difference(expected))
        raise ValueError(
            f"Template inventory differs from manifest; missing={missing[:8]}, "
            f"extra={extra[:8]}"
        )

    edited_images = []
    edited_typetrees = []
    for item in manifest["assets"]:
        key = (int(item["path_id"]), item["type"])
        obj = lookup[key]
        raw_path = os.path.join(project_dir, *item["raw"].split("/"))
        raw = open(raw_path, "rb").read()
        obj.set_raw_data(raw)

        editable = item.get("editable")
        if (
            editable
            and item["type"] == "Texture2D"
            and editable.lower().endswith(".png")
        ):
            editable_path = os.path.join(project_dir, *editable.split("/"))
            current_hash = _sha256(open(editable_path, "rb").read())
            if current_hash != item.get("editable_sha256"):
                edited_images.append((obj, editable_path))
        elif editable and editable.lower().endswith(".json"):
            editable_path = os.path.join(project_dir, *editable.split("/"))
            current_hash = _sha256(open(editable_path, "rb").read())
            if current_hash != item.get("editable_sha256"):
                edited_typetrees.append((obj, editable_path))

    for obj, editable_path in edited_images:
        with Image.open(editable_path) as source:
            image = source.convert("RGBA").copy()
        texture = obj.read(False)
        replace_texture_image(texture, image)
        texture.save()
    for obj, editable_path in edited_typetrees:
        with open(editable_path, "r", encoding="utf-8-sig") as handle:
            tree = json.load(handle)
        obj.save_typetree(tree)
    return {
        "textures": len(edited_images),
        "typetrees": len(edited_typetrees),
        "added": [],
        "deleted": [],
        "renamed": [],
        "modified_raw": [],
        "assets": list(manifest["assets"]),
        "nulled_references": [],
    }


def _candidate_names_for_id(directory: str, path_id: int) -> List[str]:
    names = set()
    for entry in os.scandir(directory):
        if not entry.is_file():
            continue
        if entry.name in {ASSET_INDEX_JSON, ASSET_INDEX_CSV}:
            continue
        stem = os.path.splitext(entry.name)[0]
        try:
            name, candidate_id = _parse_asset_file_stem(stem)
        except ValueError:
            continue
        if candidate_id == int(path_id):
            names.add(name)
    return sorted(names, key=str.casefold)


def _pick_editable(
    directory: str,
    path_id: int,
    chosen_name: str,
    original: Optional[dict],
    project_dir: str,
) -> Optional[str]:
    candidates = []
    for entry in os.scandir(directory):
        if not entry.is_file() or entry.name in {
            ASSET_INDEX_JSON, ASSET_INDEX_CSV
        }:
            continue
        if entry.name.lower().endswith((".raw", ".ress")):
            continue
        stem = os.path.splitext(entry.name)[0]
        try:
            name, candidate_id = _parse_asset_file_stem(stem)
        except ValueError:
            continue
        if candidate_id == int(path_id):
            candidates.append((name, entry.path))
    if not candidates:
        return None
    exact = [
        path for name, path in candidates if name.casefold() == chosen_name.casefold()
    ]
    if len(exact) == 1:
        return _relative(exact[0], project_dir)
    if original and original.get("editable"):
        original_extension = os.path.splitext(original["editable"])[1].casefold()
        same_extension = [
            path for _name, path in candidates
            if os.path.splitext(path)[1].casefold() == original_extension
        ]
        if len(same_extension) == 1:
            return _relative(same_extension[0], project_dir)
    if len(candidates) == 1:
        return _relative(candidates[0][1], project_dir)
    raise ValueError(
        f"PathID {path_id} has multiple ambiguous editable files: "
        + ", ".join(os.path.basename(path) for _name, path in candidates)
    )


def _pick_stream_payload(
    directory: str,
    path_id: int,
    chosen_name: str,
    project_dir: str,
) -> Optional[str]:
    candidates = []
    for entry in os.scandir(directory):
        if not entry.is_file() or not entry.name.lower().endswith(".ress"):
            continue
        stem = os.path.splitext(entry.name)[0]
        try:
            name, candidate_id = _parse_asset_file_stem(stem)
        except ValueError:
            continue
        if candidate_id == int(path_id):
            candidates.append((name, entry.path))
    if not candidates:
        return None
    exact = [
        path for name, path in candidates
        if name.casefold() == chosen_name.casefold()
    ]
    if len(exact) == 1:
        return _relative(exact[0], project_dir)
    if len(candidates) == 1:
        return _relative(candidates[0][1], project_dir)
    raise ValueError(
        f"PathID {path_id} has multiple ambiguous .resS payloads: "
        + ", ".join(os.path.basename(path) for _name, path in candidates)
    )


def _scan_project_assets(project_dir: str, manifest: dict) -> dict:
    """Treat the exported ``*.raw`` files as the editable object inventory."""

    assets_root = os.path.join(project_dir, "assets")
    if not os.path.isdir(assets_root):
        raise ValueError("Bundle project is missing its assets directory")
    current_records = {
        (item["type"], int(item["path_id"])): item
        for item in manifest.get("assets", [])
    }
    backup_records = {
        (item["type"], int(item["path_id"])): item
        for item in manifest.get(
            "backup_assets", manifest.get("assets", [])
        )
    }
    scanned = {}
    ids = {}
    for directory_entry in sorted(
        os.scandir(assets_root), key=lambda entry: entry.name.casefold()
    ):
        if not directory_entry.is_dir():
            continue
        directory_name = directory_entry.name
        type_name = DIRECTORY_TYPES.get(directory_name)
        known = {
            item["type"]
            for item in manifest.get("assets", [])
            if item["raw"].replace("\\", "/").startswith(
                f"assets/{directory_name}/"
            )
        }
        if len(known) == 1:
            manifest_type = next(iter(known))
            if type_name is None or type_name not in known:
                type_name = manifest_type
        if type_name is None:
            raise ValueError(
                f"Cannot infer Unity type for assets/{directory_name}"
            )
        for root, _dirs, files in os.walk(directory_entry.path):
            companions = {}
            for companion_name in files:
                if companion_name in {ASSET_INDEX_JSON, ASSET_INDEX_CSV}:
                    continue
                companion_stem, companion_extension = os.path.splitext(
                    companion_name
                )
                try:
                    companion_asset_name, companion_path_id = (
                        _parse_asset_file_stem(companion_stem)
                    )
                except ValueError:
                    continue
                companions.setdefault(companion_path_id, []).append(
                    (
                        companion_asset_name,
                        os.path.join(root, companion_name),
                        companion_extension.casefold(),
                    )
                )
            for file_name in sorted(files, key=str.casefold):
                if not file_name.lower().endswith(".raw"):
                    continue
                stem = os.path.splitext(file_name)[0]
                parsed_name, path_id = _parse_asset_file_stem(stem)
                key = (type_name, path_id)
                if key in scanned:
                    raise ValueError(
                        f"Duplicate {type_name} PathID {path_id}: "
                        f"{scanned[key]['raw']} and "
                        f"{_relative(os.path.join(root, file_name), project_dir)}"
                    )
                if path_id in ids and ids[path_id] != type_name:
                    raise ValueError(
                        f"PathID {path_id} is used by both {ids[path_id]} "
                        f"and {type_name}"
                    )
                ids[path_id] = type_name
                source = current_records.get(key) or backup_records.get(key)
                backup_source = backup_records.get(key)
                original_export_name = None
                if source:
                    source_stem = source.get("file_stem")
                    if source_stem:
                        try:
                            original_export_name, _ = _parse_asset_file_stem(
                                source_stem
                            )
                        except ValueError:
                            original_export_name = None
                    if original_export_name is None:
                        original_export_name = _safe_filename(
                            source.get("name", ""), type_name
                        )
                names = sorted(
                    {
                        candidate_name
                        for candidate_name, _path, _extension
                        in companions.get(path_id, [])
                    },
                    key=str.casefold,
                )
                changed_names = [
                    name for name in names
                    if original_export_name is None
                    or name.casefold() != original_export_name.casefold()
                ]
                if (
                    source
                    and parsed_name.casefold() == original_export_name.casefold()
                    and len(changed_names) == 1
                ):
                    chosen_export_name = changed_names[0]
                elif (
                    source
                    and parsed_name.casefold() == original_export_name.casefold()
                    and len(changed_names) > 1
                ):
                    raise ValueError(
                        f"PathID {path_id} has multiple rename candidates: "
                        + ", ".join(changed_names)
                    )
                else:
                    chosen_export_name = parsed_name
                if (
                    source
                    and chosen_export_name.casefold()
                    == original_export_name.casefold()
                ):
                    chosen_name = source.get("name", chosen_export_name)
                else:
                    chosen_name = chosen_export_name
                raw_path = os.path.join(root, file_name)
                raw_data = open(raw_path, "rb").read()
                item = dict(source or {})
                item.update(
                    {
                        "path_id": path_id,
                        "type": type_name,
                        "name": chosen_name,
                        "file_stem": _asset_file_stem(
                            chosen_name, path_id, type_name
                        ),
                        "bytes": len(raw_data),
                        "sha256": _sha256(raw_data),
                        "raw": _relative(raw_path, project_dir),
                        "source_node": (
                            (
                                backup_source or source or {}
                            ).get("source_node", "")
                        ),
                    }
                )
                editable_candidates = [
                    (candidate_name, candidate_path)
                    for candidate_name, candidate_path, extension
                    in companions.get(path_id, [])
                    if extension not in {".raw", ".ress"}
                ]
                editable = None
                exact_editable = [
                    candidate_path
                    for candidate_name, candidate_path
                    in editable_candidates
                    if candidate_name.casefold()
                    == chosen_export_name.casefold()
                ]
                if len(exact_editable) == 1:
                    editable = _relative(
                        exact_editable[0], project_dir
                    )
                elif source and source.get("editable"):
                    original_extension = os.path.splitext(
                        source["editable"]
                    )[1].casefold()
                    same_extension = [
                        candidate_path
                        for _candidate_name, candidate_path
                        in editable_candidates
                        if os.path.splitext(candidate_path)[1].casefold()
                        == original_extension
                    ]
                    if len(same_extension) == 1:
                        editable = _relative(
                            same_extension[0], project_dir
                        )
                if editable is None and len(editable_candidates) == 1:
                    editable = _relative(
                        editable_candidates[0][1], project_dir
                    )
                if editable is None and len(editable_candidates) > 1:
                    raise ValueError(
                        f"PathID {path_id} has multiple ambiguous editable "
                        "files: "
                        + ", ".join(
                            os.path.basename(candidate_path)
                            for _candidate_name, candidate_path
                            in editable_candidates
                        )
                    )
                if editable:
                    item["editable"] = editable
                    item["_baseline_editable_sha256"] = (
                        source.get("editable_sha256") if source else None
                    )
                else:
                    item.pop("editable", None)
                    item.pop("editable_sha256", None)
                payload_candidates = [
                    (candidate_name, candidate_path)
                    for candidate_name, candidate_path, extension
                    in companions.get(path_id, [])
                    if extension == ".ress"
                ]
                exact_payloads = [
                    candidate_path
                    for candidate_name, candidate_path
                    in payload_candidates
                    if candidate_name.casefold()
                    == chosen_export_name.casefold()
                ]
                payload = None
                if len(exact_payloads) == 1:
                    payload = _relative(exact_payloads[0], project_dir)
                elif len(payload_candidates) == 1:
                    payload = _relative(
                        payload_candidates[0][1], project_dir
                    )
                elif len(payload_candidates) > 1:
                    raise ValueError(
                        f"PathID {path_id} has multiple ambiguous .resS "
                        "payloads: "
                        + ", ".join(
                            os.path.basename(candidate_path)
                            for _candidate_name, candidate_path
                            in payload_candidates
                        )
                    )
                if payload:
                    payload_path = os.path.join(
                        project_dir, *payload.split("/")
                    )
                    payload_data = open(payload_path, "rb").read()
                    stream = dict(item.get("stream") or {})
                    stream.update(
                        {
                            "payload": payload,
                            "size": len(payload_data),
                            "payload_sha256": _sha256(payload_data),
                        }
                    )
                    item["stream"] = stream
                else:
                    item.pop("stream", None)
                scanned[key] = item

    original_keys = set(backup_records)
    scanned_keys = set(scanned)
    unmatched_original = set(original_keys - scanned_keys)
    unmatched_scanned = set(scanned_keys - original_keys)
    path_id_changes = []
    claimed_new = set()
    for old_key in sorted(unmatched_original):
        before = backup_records[old_key]
        candidates = [
            new_key for new_key in unmatched_scanned - claimed_new
            if new_key[0] == old_key[0]
            and scanned[new_key].get("sha256") == before.get("sha256")
            and scanned[new_key].get("name", "")
            == before.get("name", "")
        ]
        if len(candidates) != 1:
            candidates = [
                new_key for new_key in unmatched_scanned - claimed_new
                if new_key[0] == old_key[0]
                and scanned[new_key].get("name", "")
                == before.get("name", "")
            ]
        if len(candidates) != 1:
            continue
        new_key = candidates[0]
        after = scanned[new_key]
        after["source_node"] = before.get("source_node", "")
        path_id_changes.append(
            {
                "type": after["type"],
                "from_path_id": int(before["path_id"]),
                "to_path_id": int(after["path_id"]),
                "from_name": before.get("name", ""),
                "to_name": after.get("name", ""),
                "source_node": before.get("source_node", ""),
            }
        )
        claimed_new.add(new_key)
    changed_old_keys = {
        (item["type"], int(item["from_path_id"]))
        for item in path_id_changes
    }
    changed_new_keys = {
        (item["type"], int(item["to_path_id"]))
        for item in path_id_changes
    }
    deleted = [
        {
            "type": backup_records[key]["type"],
            "path_id": int(backup_records[key]["path_id"]),
            "name": backup_records[key].get("name", ""),
            "source_node": backup_records[key].get("source_node", ""),
        }
        for key in sorted((original_keys - scanned_keys) - changed_old_keys)
    ]
    added = [
        {
            "type": scanned[key]["type"],
            "path_id": int(scanned[key]["path_id"]),
            "name": scanned[key].get("name", ""),
            "source_node": scanned[key].get("source_node", ""),
        }
        for key in sorted((scanned_keys - original_keys) - changed_new_keys)
    ]
    renamed = []
    modified_raw = []
    for key in sorted(original_keys & scanned_keys):
        before = backup_records[key]
        after = scanned[key]
        if before.get("name", "") != after.get("name", ""):
            renamed.append(
                {
                    "type": after["type"],
                    "path_id": int(after["path_id"]),
                    "from": before.get("name", ""),
                    "to": after.get("name", ""),
                }
            )
        if before.get("sha256") != after.get("sha256"):
            modified_raw.append(
                {
                    "type": after["type"],
                    "path_id": int(after["path_id"]),
                    "name": after.get("name", ""),
                }
            )
    return {
        "items": list(scanned.values()),
        "by_key": scanned,
        "original": backup_records,
        "current_records": current_records,
        "added": added,
        "deleted": deleted,
        "renamed": renamed,
        "modified_raw": modified_raw,
        "path_id_changes": path_id_changes,
    }


def _assets_file_for_new_object(environment, item: dict):
    assets_files = list(environment.assets)
    source_node = item.get("source_node", "")
    if source_node:
        for assets_file in assets_files:
            if str(getattr(assets_file, "name", "")) == source_node:
                return assets_file
    for assets_file in assets_files:
        if any(
            obj.type.name == item["type"]
            for obj in assets_file.objects.values()
        ):
            return assets_file
    class_id = int(ClassIDType[item["type"]])
    for assets_file in assets_files:
        if any(int(serialized.class_id) == class_id for serialized in assets_file.types):
            return assets_file
    raise ValueError(
        f"Backup bundle has no serialized type definition for {item['type']}; "
        "a new raw object of that type cannot be created safely"
    )


def _add_raw_object(environment, item: dict, raw: bytes):
    assets_file = _assets_file_for_new_object(environment, item)
    path_id = int(item["path_id"])
    if path_id in assets_file.objects:
        raise ValueError(
            f"Cannot add {item['type']} {path_id}; that PathID already exists"
        )
    serialized_type_hash = item.get("serialized_type_hash")
    imported_type_id = None
    if serialized_type_hash:
        expected_hash = bytes.fromhex(serialized_type_hash)
        imported_type_id = next(
            (
                index for index, serialized in enumerate(assets_file.types)
                if int(serialized.class_id) == int(ClassIDType[item["type"]])
                and bytes(getattr(serialized, "old_type_hash", b""))
                == expected_hash
            ),
            None,
        )
        if imported_type_id is None:
            raise ValueError(
                f"Serialized type hash {serialized_type_hash} for "
                f"{item['type']} was not installed in the target CAB"
            )
    prototype = next(
        (
            obj for obj in assets_file.objects.values()
            if obj.type.name == item["type"]
        ),
        None,
    )
    class_id = int(ClassIDType[item["type"]])
    if prototype is not None and imported_type_id is None:
        reader = _shallow_clone(prototype)
    else:
        if not assets_file.objects:
            raise ValueError("Cannot create an object in an empty serialized file")
        reader = _shallow_clone(next(iter(assets_file.objects.values())))
        type_id = imported_type_id
        if type_id is None:
            type_id = next(
                (
                    index for index, serialized in enumerate(assets_file.types)
                    if int(serialized.class_id) == class_id
                ),
                None,
            )
        if type_id is None:
            raise ValueError(
                f"Serialized type {item['type']} is unavailable in the backup"
            )
        reader.type_id = type_id
        reader.serialized_type = assets_file.types[type_id]
        reader.class_id = class_id
        reader.type = ClassIDType(class_id)
    reader.assets_file = assets_file
    reader.reader = assets_file.reader
    reader.path_id = path_id
    reader.byte_start = 0
    reader.byte_size = len(raw)
    reader.data = bytes(raw)
    reader._read_until = 0
    reader._in_object_reader = False
    reader._object_read_depth = 0
    reader.version = assets_file.version
    reader.version2 = assets_file.header.version
    reader.platform = assets_file.target_platform
    reader.build_type = assets_file.build_type
    assets_file.objects[path_id] = reader
    assets_file.mark_changed()
    item["source_node"] = str(getattr(assets_file, "name", "") or "")
    return reader


def _rename_object(obj, name: str) -> bool:
    current = _object_name(obj)
    if current == name:
        return False
    try:
        tree = obj.read_typetree()
        if isinstance(tree, dict) and "m_Name" in tree:
            tree["m_Name"] = name
            obj.save_typetree(tree)
        else:
            raise ValueError("object typetree has no m_Name")
    except Exception:
        parsed = obj.read(False)
        if not hasattr(parsed, "m_Name"):
            raise ValueError(
                f"{obj.type.name} PathID {obj.path_id} cannot be renamed"
            )
        parsed.m_Name = name
        parsed.save()
    verified = _object_name(obj)
    if verified != name:
        raise RuntimeError(
            f"Rename verification failed for {obj.type.name} "
            f"{obj.path_id}: expected {name!r}, got {verified!r}"
        )
    return True


def _make_pointer(
    assets_file, path_id: int, prototype=None, *, file_id: int = 0
):
    pointer = (
        _shallow_clone(prototype)
        if prototype is not None
        else PPtr.__new__(PPtr)
    )
    pointer._version = assets_file.header.version
    pointer.index = -2
    pointer.file_id = int(file_id)
    pointer.path_id = int(path_id)
    pointer.assets_file = assets_file
    pointer._obj = None
    return pointer


def _file_id_for_target(owner_assets_file, target_assets_file) -> int:
    if owner_assets_file is target_assets_file:
        return 0
    target_names = {
        str(getattr(target_assets_file, "name", "") or "").casefold(),
        os.path.basename(
            str(getattr(target_assets_file, "name", "") or "")
        ).casefold(),
    }
    for index, external in enumerate(owner_assets_file.externals, start=1):
        external_names = {
            str(getattr(external, "name", "") or "").casefold(),
            os.path.basename(
                str(getattr(external, "path", "") or "")
            ).casefold(),
        }
        if target_names.intersection(external_names):
            return index
    raise ValueError(
        f"{getattr(owner_assets_file, 'name', 'serialized file')} has no "
        f"external mapping to {getattr(target_assets_file, 'name', 'target')}"
    )


def _renamed_catalog_key(key: str, old_name: str, new_name: str) -> str:
    if key == old_name:
        return new_name
    directory, file_name = os.path.split(key.replace("\\", "/"))
    stem, extension = os.path.splitext(file_name)
    if stem.casefold() != old_name.casefold():
        return key
    updated = f"{new_name}{extension}"
    return f"{directory}/{updated}" if directory else updated


def _sync_assetbundle_catalogs(
    environment,
    deleted_ids: set,
    added: List[dict],
    renamed: List[dict],
    compatibility: Optional[dict] = None,
) -> dict:
    existing_ids = {int(obj.path_id) for obj in environment.objects}
    renamed_by_id = {
        int(item["path_id"]): (item["from"], item["to"])
        for item in renamed
    }
    preloads_added = []
    entries_added = []
    compatibility_updates = []
    objects_by_id = {
        int(obj.path_id): obj for obj in environment.objects
    }
    for reader in environment.objects:
        if reader.type.name != "AssetBundle":
            continue
        bundle = reader.read(False)
        old_preloads = list(bundle.m_PreloadTable)
        keep = [
            not (
                int(pointer.file_id) == 0
                and (
                    int(pointer.path_id) in deleted_ids
                    or (
                        int(pointer.path_id)
                        and int(pointer.path_id) not in existing_ids
                    )
                )
            )
            for pointer in old_preloads
        ]
        prefix = [0]
        for retained in keep:
            prefix.append(prefix[-1] + int(retained))
        bundle.m_PreloadTable = [
            pointer for pointer, retained in zip(old_preloads, keep) if retained
        ]
        def update_asset_info(info) -> None:
            start = max(
                0, min(len(old_preloads), int(info.preload_index))
            )
            end = max(
                start,
                min(
                    len(old_preloads),
                    start + int(info.preload_size),
                ),
            )
            info.preload_index = prefix[start]
            info.preload_size = prefix[end] - prefix[start]

        updated_entries = []
        for key, info in list(bundle.m_ContainerEntries):
            asset_id = int(info.asset.path_id)
            if int(info.asset.file_id) == 0 and asset_id in deleted_ids:
                continue
            update_asset_info(info)
            if asset_id in renamed_by_id:
                before, after = renamed_by_id[asset_id]
                key = _renamed_catalog_key(key, before, after)
            updated_entries.append((key, info))
        update_asset_info(bundle.m_MainAsset)
        for _class_id, info in bundle.m_ClassCompatibility:
            update_asset_info(info)
        bundle.m_ContainerEntries = updated_entries
        bundle.m_Container = dict(updated_entries)
        present = {
            (int(pointer.file_id), int(pointer.path_id))
            for pointer in bundle.m_PreloadTable
        }
        pointer_prototype = (
            bundle.m_PreloadTable[0] if bundle.m_PreloadTable else None
        )
        if compatibility:
            root_path_id = int(
                compatibility["class_root_path_id"]
            )
            class_info = next(
                (
                    info
                    for _class_id, info in bundle.m_ClassCompatibility
                    if int(info.asset.file_id) == 0
                    and int(info.asset.path_id) == root_path_id
                ),
                None,
            )
            if class_info is None:
                raise ValueError(
                    "AssetBundle class compatibility root is missing: "
                    f"{root_path_id}"
                )
            start = int(class_info.preload_index)
            end = start + int(class_info.preload_size)
            dependency_block = list(bundle.m_PreloadTable[start:end])
            appended_external = []
            appended_local = []
            ordered_pointer_info = compatibility.get(
                "preload_pointers"
            )
            if ordered_pointer_info is not None:
                configured_pairs = [
                    (
                        int(pointer_info["file_id"]),
                        int(pointer_info["path_id"]),
                    )
                    for pointer_info in ordered_pointer_info
                ]
                dependency_pairs = [
                    (int(pointer.file_id), int(pointer.path_id))
                    for pointer in dependency_block
                ]
                reused_existing_block = (
                    len(dependency_pairs) >= len(configured_pairs)
                    and dependency_pairs[-len(configured_pairs):]
                    == configured_pairs
                    if configured_pairs
                    else True
                )
                if not reused_existing_block:
                    for file_id, path_id in configured_pairs:
                        if file_id == 0:
                            target = objects_by_id.get(path_id)
                            if target is None:
                                raise ValueError(
                                    "Compatibility preload references a "
                                    f"missing local object: {path_id}"
                                )
                            actual_file_id = _file_id_for_target(
                                reader.assets_file, target.assets_file
                            )
                            if actual_file_id != 0:
                                raise ValueError(
                                    "Ordered compatibility preload expected "
                                    f"local PathID {path_id}, but it belongs "
                                    f"to serialized file ID {actual_file_id}"
                                )
                            appended_local.append(path_id)
                            preloads_added.append(path_id)
                        else:
                            if not 1 <= file_id <= len(
                                reader.assets_file.externals
                            ):
                                raise ValueError(
                                    "Compatibility preload references "
                                    f"missing external file ID {file_id}"
                                )
                            appended_external.append(
                                {
                                    "file_id": file_id,
                                    "path_id": path_id,
                                }
                            )
                        dependency_block.append(
                            _make_pointer(
                                reader.assets_file,
                                path_id,
                                pointer_prototype,
                                file_id=file_id,
                            )
                        )
            else:
                block_pairs = {
                    (int(pointer.file_id), int(pointer.path_id))
                    for pointer in dependency_block
                }
                for pointer_info in compatibility.get(
                    "external_pointers", []
                ):
                    pair = (
                        int(pointer_info["file_id"]),
                        int(pointer_info["path_id"]),
                    )
                    if pair in block_pairs:
                        continue
                    dependency_block.append(
                        _make_pointer(
                            reader.assets_file,
                            pair[1],
                            pointer_prototype,
                            file_id=pair[0],
                        )
                    )
                    block_pairs.add(pair)
                    appended_external.append(
                        {"file_id": pair[0], "path_id": pair[1]}
                    )
                configured_ids = compatibility.get(
                    "local_path_ids",
                    [
                        int(item["path_id"])
                        for item in added
                        if item["type"] != "AssetBundle"
                    ],
                )
                for path_id in configured_ids:
                    path_id = int(path_id)
                    target = objects_by_id.get(path_id)
                    if target is None:
                        raise ValueError(
                            "Compatibility preload references a missing "
                            f"local object: {path_id}"
                        )
                    file_id = _file_id_for_target(
                        reader.assets_file, target.assets_file
                    )
                    pair = (file_id, path_id)
                    if pair in block_pairs:
                        continue
                    dependency_block.append(
                        _make_pointer(
                            reader.assets_file,
                            path_id,
                            pointer_prototype,
                            file_id=file_id,
                        )
                    )
                    block_pairs.add(pair)
                    appended_local.append(path_id)
                    preloads_added.append(path_id)
                reused_existing_block = not (
                    appended_external or appended_local
                )
            if not reused_existing_block:
                class_info.preload_index = len(bundle.m_PreloadTable)
                class_info.preload_size = len(dependency_block)
                bundle.m_PreloadTable.extend(dependency_block)
            compatibility_updates.append(
                {
                    "class_root_path_id": root_path_id,
                    "preload_index": int(class_info.preload_index),
                    "preload_size": int(class_info.preload_size),
                    "base_dependencies": (
                        len(dependency_block)
                        - len(appended_external)
                        - len(appended_local)
                    ),
                    "external_pointers": appended_external,
                    "local_path_ids": appended_local,
                    "reused_existing_block": reused_existing_block,
                }
            )
        else:
            for item in added:
                if item["type"] == "AssetBundle":
                    continue
                path_id = int(item["path_id"])
                target = objects_by_id[path_id]
                file_id = _file_id_for_target(
                    reader.assets_file, target.assets_file
                )
                if (file_id, path_id) in present:
                    continue
                pointer = _make_pointer(
                    reader.assets_file,
                    path_id,
                    pointer_prototype,
                    file_id=file_id,
                )
                preload_index = len(bundle.m_PreloadTable)
                bundle.m_PreloadTable.append(pointer)
                present.add((file_id, path_id))
                preloads_added.append(path_id)
                if updated_entries:
                    key = item["name"]
                    used = {
                        entry_key for entry_key, _info in updated_entries
                    }
                    if key in used:
                        key = f"{key}_{path_id}"
                    info = AssetInfo.__new__(AssetInfo)
                    info.preload_index = preload_index
                    info.preload_size = 1
                    info.asset = _make_pointer(
                        reader.assets_file,
                        path_id,
                        pointer_prototype,
                        file_id=file_id,
                    )
                    updated_entries.append((key, info))
                    entries_added.append(path_id)
        bundle.m_ContainerEntries = updated_entries
        bundle.m_Container = dict(updated_entries)
        bundle.save()
    return {
        "preloads_added": sorted(set(preloads_added)),
        "container_entries_added": sorted(set(entries_added)),
        "compatibility_updates": compatibility_updates,
    }


def _sprite_details(reader) -> Optional[dict]:
    if reader.type.name != "Sprite":
        return None
    sprite = reader.read(False)
    key = getattr(sprite, "m_RenderDataKey", (b"", 0))
    return {
        "path_id": int(reader.path_id),
        "name": str(sprite.m_Name),
        "atlas_id": int(sprite.m_SpriteAtlas.path_id),
        "atlas_file_id": int(sprite.m_SpriteAtlas.file_id),
        "render_key": key,
    }


def _sync_sprite_atlases(
    environment,
    deleted_sprites: List[dict],
    renamed: List[dict],
    added: List[dict],
) -> dict:
    deleted_by_atlas = {}
    for detail in deleted_sprites:
        if detail["atlas_file_id"] == 0 and detail["atlas_id"]:
            deleted_by_atlas.setdefault(detail["atlas_id"], []).append(detail)
    renamed_by_id = {
        int(item["path_id"]): item["to"]
        for item in renamed
        if item["type"] == "Sprite"
    }
    added_ids = {
        int(item["path_id"]) for item in added if item["type"] == "Sprite"
    }
    removed = []
    renamed_names = []
    for reader in environment.objects:
        if reader.type.name != "SpriteAtlas":
            continue
        atlas = reader.read(False)
        atlas_id = int(reader.path_id)
        packed = list(atlas.m_PackedSprites)
        names = list(atlas.m_PackedSpriteNamesToIndex)
        if len(packed) != len(names):
            raise ValueError(
                f"SpriteAtlas {atlas_id} has mismatched pointer/name arrays"
            )
        kept_pointers = []
        kept_names = []
        removed_keys = []
        deleted_ids = {
            int(item["path_id"])
            for item in deleted_by_atlas.get(atlas_id, [])
        }
        for pointer, name in zip(packed, names):
            path_id = int(pointer.path_id)
            if int(pointer.file_id) == 0 and path_id in deleted_ids:
                detail = next(
                    item for item in deleted_by_atlas[atlas_id]
                    if int(item["path_id"]) == path_id
                )
                removed_keys.append(detail["render_key"])
                removed.append(path_id)
                continue
            if path_id in renamed_by_id:
                name = renamed_by_id[path_id]
                renamed_names.append(path_id)
            kept_pointers.append(pointer)
            kept_names.append(name)
        atlas.m_PackedSprites = kept_pointers
        atlas.m_PackedSpriteNamesToIndex = kept_names
        surviving_keys = set()
        for sprite_reader in environment.objects:
            if sprite_reader.type.name != "Sprite":
                continue
            detail = _sprite_details(sprite_reader)
            if (
                detail["atlas_file_id"] == 0
                and detail["atlas_id"] == atlas_id
            ):
                surviving_keys.add(detail["render_key"])
                if (
                    detail["path_id"] in added_ids
                    and not any(
                        int(pointer.file_id) == 0
                        and int(pointer.path_id) == detail["path_id"]
                        for pointer in atlas.m_PackedSprites
                    )
                ):
                    raise ValueError(
                        f"New packed Sprite {detail['name']} "
                        f"({detail['path_id']}) has no independent "
                        "SpriteAtlas render-data entry. Add a matching edited "
                        "SpriteAtlas raw object instead of guessing the mapping."
                    )
        for render_key in removed_keys:
            if render_key not in surviving_keys:
                atlas.m_RenderDataMap.pop(render_key, None)
        if deleted_ids or renamed_by_id:
            atlas.save()
    return {
        "removed_sprite_path_ids": sorted(set(removed)),
        "renamed_sprite_path_ids": sorted(set(renamed_names)),
    }


def _null_deleted_references(environment, deleted_ids: set) -> List[dict]:
    """Clear remaining local PPtrs to deleted objects without inventing targets."""

    changed = []

    def visit(value, locations, location):
        if isinstance(value, dict):
            if "m_FileID" in value and "m_PathID" in value:
                if (
                    int(value["m_FileID"]) == 0
                    and int(value["m_PathID"]) in deleted_ids
                ):
                    locations.append(location)
                    value["m_PathID"] = 0
                    return
            for key, child in value.items():
                visit(child, locations, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, locations, f"{location}[{index}]")

    for obj in environment.objects:
        if obj.type.name in {"AssetBundle", "SpriteAtlas"}:
            continue
        raw = bytes(obj.get_raw_data())
        endian = obj.reader.endian
        if not any(
            struct.pack(endian + "q", int(path_id)) in raw
            for path_id in deleted_ids
        ):
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        locations = []
        visit(tree, locations, obj.type.name)
        if locations:
            obj.save_typetree(tree)
            changed.append(
                {
                    "type": obj.type.name,
                    "path_id": int(obj.path_id),
                    "locations": locations,
                }
            )
    return changed


def _remap_path_id_references(
    environment,
    path_id_changes: List[dict],
) -> List[dict]:
    """Redirect every readable PPtr after a user changes a file PathID."""

    remap = {
        int(item["from_path_id"]): int(item["to_path_id"])
        for item in path_id_changes
    }
    if not remap:
        return []
    changed = []

    def remap_pointer(pointer) -> bool:
        path_id = int(getattr(pointer, "path_id", 0))
        if path_id not in remap:
            return False
        pointer.path_id = remap[path_id]
        pointer._obj = None
        pointer.index = -2
        return True

    def visit(value, locations, location):
        if isinstance(value, dict):
            if "m_FileID" in value and "m_PathID" in value:
                path_id = int(value["m_PathID"])
                if path_id in remap:
                    value["m_PathID"] = remap[path_id]
                    locations.append(location)
                    return
            for key, child in value.items():
                visit(child, locations, f"{location}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, locations, f"{location}[{index}]")

    for obj in environment.objects:
        locations = []
        if obj.type.name == "AssetBundle":
            bundle = obj.read(False)
            for index, pointer in enumerate(bundle.m_PreloadTable):
                if remap_pointer(pointer):
                    locations.append(f"m_PreloadTable[{index}]")
            for index, (_key, info) in enumerate(bundle.m_ContainerEntries):
                if remap_pointer(info.asset):
                    locations.append(f"m_Container[{index}].asset")
            if locations:
                bundle.save()
        elif obj.type.name == "SpriteAtlas":
            atlas = obj.read(False)
            for index, pointer in enumerate(atlas.m_PackedSprites):
                if remap_pointer(pointer):
                    locations.append(f"m_PackedSprites[{index}]")
            for render_key, render_data in atlas.m_RenderDataMap.items():
                if remap_pointer(render_data.texture):
                    locations.append(
                        f"m_RenderDataMap[{render_key!r}].texture"
                    )
                if remap_pointer(render_data.alphaTexture):
                    locations.append(
                        f"m_RenderDataMap[{render_key!r}].alphaTexture"
                    )
            if locations:
                atlas.save()
        else:
            try:
                tree = obj.read_typetree()
            except Exception:
                continue
            visit(tree, locations, obj.type.name)
            if locations:
                obj.save_typetree(tree)
        if locations:
            changed.append(
                {
                    "type": obj.type.name,
                    "path_id": int(obj.path_id),
                    "locations": locations,
                }
            )
    return changed


def _validate_local_references(
    environment,
    object_path_ids: Optional[set] = None,
) -> List[dict]:
    """Report local PPtrs whose target is absent from that serialized file."""

    issues = []

    def visit(value, local_ids, owner, location):
        if isinstance(value, dict):
            if "m_FileID" in value and "m_PathID" in value:
                file_id = int(value["m_FileID"])
                path_id = int(value["m_PathID"])
                if file_id == 0 and path_id and path_id not in local_ids:
                    issues.append(
                        {
                            "owner_type": owner.type.name,
                            "owner_path_id": int(owner.path_id),
                            "location": location,
                            "missing_path_id": path_id,
                        }
                    )
                    return
            for key, child in value.items():
                visit(
                    child, local_ids, owner, f"{location}.{key}"
                )
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(
                    child, local_ids, owner, f"{location}[{index}]"
                )

    for obj in environment.objects:
        if (
            object_path_ids is not None
            and int(obj.path_id) not in object_path_ids
            and obj.type.name not in {"AssetBundle", "SpriteAtlas"}
        ):
            continue
        local_ids = {
            int(item.path_id) for item in obj.assets_file.objects.values()
        }
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        visit(tree, local_ids, obj, obj.type.name)
    return issues


def _apply_dynamic_project_objects(
    environment, project_dir: str, manifest: dict
):
    scan = _scan_project_assets(project_dir, manifest)
    lookup = {
        (obj.type.name, int(obj.path_id)): obj for obj in environment.objects
    }
    changed_by_new = {
        (item["type"], int(item["to_path_id"])): item
        for item in scan["path_id_changes"]
    }
    for change in scan["path_id_changes"]:
        old_key = (change["type"], int(change["from_path_id"]))
        new_key = (change["type"], int(change["to_path_id"]))
        obj = lookup.get(old_key)
        if obj is None:
            raise ValueError(
                f"Backup is missing {change['type']} "
                f"{change['from_path_id']} for PathID migration"
            )
        if int(change["to_path_id"]) in obj.assets_file.objects:
            raise ValueError(
                f"Cannot migrate {change['type']} {change['from_path_id']} "
                f"to occupied PathID {change['to_path_id']}"
            )
        del obj.assets_file.objects[int(change["from_path_id"])]
        obj.path_id = int(change["to_path_id"])
        obj.assets_file.objects[int(change["to_path_id"])] = obj
        obj.assets_file.mark_changed()
        lookup.pop(old_key)
        lookup[new_key] = obj
    deleted_ids = {int(item["path_id"]) for item in scan["deleted"]}
    if any(item["type"] == "AssetBundle" for item in scan["deleted"]):
        raise ValueError(
            "The AssetBundle catalog object cannot be deleted from a "
            "rebuildable project"
        )
    deleted_sprites = []
    for item in scan["deleted"]:
        obj = lookup.get((item["type"], int(item["path_id"])))
        if obj is None:
            continue
        detail = _sprite_details(obj)
        if detail:
            deleted_sprites.append(detail)
        del obj.assets_file.objects[int(obj.path_id)]
        obj.assets_file.mark_changed()

    current = {}
    original_keys = set(scan["original"])
    for item in scan["items"]:
        key = (item["type"], int(item["path_id"]))
        raw_path = os.path.join(project_dir, *item["raw"].split("/"))
        raw = open(raw_path, "rb").read()
        if key in changed_by_new:
            obj = lookup[key]
            obj.set_raw_data(raw)
        elif key in original_keys:
            obj = lookup.get(key)
            if obj is None:
                raise ValueError(
                    f"Backup is missing {item['type']} {item['path_id']}"
                )
            obj.set_raw_data(raw)
        else:
            obj = _add_raw_object(environment, item, raw)
            for added_item in scan["added"]:
                if (
                    added_item["type"] == item["type"]
                    and int(added_item["path_id"])
                    == int(item["path_id"])
                ):
                    added_item["source_node"] = item.get(
                        "source_node", ""
                    )
                    break
        current[key] = obj
    compatibility = manifest.get("runtime_compatibility")
    if compatibility:
        preferred_ids = [
            int(value)
            for value in compatibility.get("local_path_ids", [])
        ]
        preferred = set(preferred_ids)
        for assets_file in environment.assets:
            existing = list(assets_file.objects.items())
            original = [
                (path_id, obj)
                for path_id, obj in existing
                if int(path_id) not in preferred
            ]
            by_id = {
                int(path_id): (path_id, obj)
                for path_id, obj in existing
                if int(path_id) in preferred
            }
            ordered = original + [
                by_id[path_id]
                for path_id in preferred_ids
                if path_id in by_id
            ]
            if len(ordered) != len(existing):
                raise ValueError(
                    "Runtime-compatible object ordering lost one or more "
                    "serialized objects"
                )
            assets_file.objects = dict(ordered)
            assets_file.mark_changed()
    # All new ObjectReaders must exist before a class parser follows local
    # PPtrs while checking or changing names.
    for item in scan["items"]:
        key = (item["type"], int(item["path_id"]))
        try:
            _rename_object(current[key], item["name"])
        except Exception as exc:
            raise ValueError(
                f"Failed to apply name for {item['type']} "
                f"{item['path_id']} ({item['name']!r})"
            ) from exc

    edited_images = []
    edited_typetrees = []
    for item in scan["items"]:
        editable = item.get("editable")
        if not editable:
            continue
        editable_path = os.path.join(project_dir, *editable.split("/"))
        current_hash = _sha256(open(editable_path, "rb").read())
        baseline_hash = item.get("_baseline_editable_sha256")
        if current_hash == baseline_hash:
            continue
        obj = current[(item["type"], int(item["path_id"]))]
        if item["type"] == "Texture2D" and editable.lower().endswith(".png"):
            edited_images.append((obj, editable_path, item))
        elif editable.lower().endswith(".json"):
            edited_typetrees.append((obj, editable_path))

    for obj, editable_path, item in edited_images:
        with Image.open(editable_path) as source:
            image = source.convert("RGBA").copy()
        texture = obj.read(False)
        replace_texture_image(texture, image)
        texture.save()
        descriptor = _stream_descriptor(obj)
        stream = item.get("stream")
        if descriptor is not None:
            if not stream or not stream.get("payload"):
                raise ValueError(
                    f"Texture2D {obj.path_id} is externally streamed but "
                    "its per-asset .resS file is missing"
                )
            payload = bytes(
                get_resource_data(
                    descriptor["path"],
                    obj.assets_file,
                    descriptor["offset"],
                    descriptor["size"],
                )
            )
            payload_path = os.path.join(
                project_dir, *stream["payload"].split("/")
            )
            _write_bytes(payload_path, payload)
            stream.update(
                {
                    **descriptor,
                    "payload_sha256": _sha256(payload),
                }
            )
    for obj, editable_path in edited_typetrees:
        with open(editable_path, "r", encoding="utf-8-sig") as handle:
            tree = json.load(handle)
        desired_name = next(
            item["name"] for item in scan["items"]
            if item["type"] == obj.type.name
            and int(item["path_id"]) == int(obj.path_id)
        )
        if isinstance(tree, dict) and "m_Name" in tree:
            tree["m_Name"] = desired_name
        obj.save_typetree(tree)

    atlas_sync = _sync_sprite_atlases(
        environment, deleted_sprites, scan["renamed"], scan["added"]
    )
    remapped = _remap_path_id_references(
        environment, scan["path_id_changes"]
    )
    nulled = _null_deleted_references(environment, deleted_ids)
    catalog_sync = _sync_assetbundle_catalogs(
        environment,
        deleted_ids,
        scan["added"],
        scan["renamed"],
        compatibility=manifest.get("runtime_compatibility"),
    )
    return {
        "textures": len(edited_images),
        "typetrees": len(edited_typetrees),
        "added": scan["added"],
        "deleted": scan["deleted"],
        "renamed": scan["renamed"],
        "modified_raw": scan["modified_raw"],
        "path_id_changes": scan["path_id_changes"],
        "remapped_references": remapped,
        "assets": scan["items"],
        "nulled_references": nulled,
        "atlas_sync": atlas_sync,
        "catalog_sync": catalog_sync,
    }


def _apply_project_objects(environment, project_dir: str, manifest: dict):
    if manifest.get("$schema") == SCHEMA_V1:
        return _apply_legacy_project_objects(
            environment, project_dir, manifest
        )
    return _apply_dynamic_project_objects(
        environment, project_dir, manifest
    )


def _aligned_string_bytes(value: str, endian: str) -> bytes:
    encoded = str(value).encode("utf-8", "surrogateescape")
    raw = struct.pack(endian + "i", len(encoded)) + encoded
    return raw + b"\0" * ((4 - len(raw) % 4) % 4)


def _stream_descriptor_bytes(
    obj,
    descriptor: dict,
) -> bytes:
    endian = obj.reader.endian
    path = _aligned_string_bytes(descriptor["path"], endian)
    offset = int(descriptor["offset"])
    size = int(descriptor["size"])
    if descriptor["kind"] == "streaming_info":
        offset_format = "Q" if tuple(obj.version) >= (2020,) else "I"
        return (
            struct.pack(endian + offset_format + "I", offset, size)
            + path
        )
    if descriptor["kind"] == "audio":
        return path + struct.pack(endian + "Qq", offset, size)
    if descriptor["kind"] == "video":
        return path + struct.pack(endian + "QQ", offset, size)
    raise ValueError(f"Unsupported stream descriptor kind: {descriptor['kind']}")


def _patch_stream_descriptor(
    obj,
    old: dict,
    new: dict,
) -> None:
    """Patch only the stream locator inside an object's serialized bytes."""

    raw = bytes(obj.get_raw_data())
    old_bytes = _stream_descriptor_bytes(obj, old)
    new_bytes = _stream_descriptor_bytes(obj, new)
    count = raw.count(old_bytes)
    if count != 1:
        raise ValueError(
            f"Cannot safely update {obj.type.name} {obj.path_id} stream "
            f"locator: serialized descriptor matched {count} times"
        )
    obj.set_raw_data(raw.replace(old_bytes, new_bytes, 1))


def _canonical_resource_name(assets_file, bundle) -> str:
    preferred = f"{assets_file.name}.resS"
    if preferred in bundle.files:
        return preferred
    casefolded = {
        name.casefold(): name for name in bundle.files
    }
    if preferred.casefold() in casefolded:
        return casefolded[preferred.casefold()]
    return preferred


def _rebuild_stream_resources(
    environment,
    project_dir: str,
    manifest: dict,
    assets: List[dict],
) -> dict:
    """Repack per-asset sidecars and bind every stream to the current CAB."""

    bundle = environment.file
    objects = {
        (obj.type.name, int(obj.path_id)): obj
        for obj in environment.objects
    }
    managed_nodes = {
        item.get("source_node", "")
        for item in manifest.get(
            "backup_assets", manifest.get("assets", [])
        )
        if item.get("stream") and item.get("source_node")
    }
    groups = {}
    active = []
    for item in assets:
        key = (item["type"], int(item["path_id"]))
        obj = objects.get(key)
        if obj is None:
            raise ValueError(
                f"Cannot bind stream for missing {item['type']} {item['path_id']}"
            )
        descriptor = _stream_descriptor(obj)
        stream = item.get("stream")
        if descriptor is None:
            if stream:
                raise ValueError(
                    f"{item['type']} {item['path_id']} has a .resS sidecar "
                    "but its raw object does not contain an external stream"
                )
            continue
        if not stream or not stream.get("payload"):
            raise ValueError(
                f"{item['type']} {item['path_id']} references "
                f"{descriptor['path']} but its per-asset .resS file is missing"
            )
        payload_path = os.path.join(
            project_dir, *stream["payload"].split("/")
        )
        if not os.path.isfile(payload_path):
            raise ValueError(
                f"External payload is missing for {item['type']} "
                f"{item['path_id']}: {payload_path}"
            )
        payload = open(payload_path, "rb").read()
        expected_sha = stream.get("payload_sha256")
        if expected_sha and _sha256(payload) != expected_sha:
            # A changed sidecar is an intentional asset edit. The current
            # content becomes the new source of truth and is re-indexed.
            stream["payload_sha256"] = _sha256(payload)
        stream["size"] = len(payload)
        assets_file = obj.assets_file
        source_node = str(getattr(assets_file, "name", "") or "")
        item["source_node"] = source_node
        managed_nodes.add(source_node)
        groups.setdefault(source_node, []).append(
            (item, obj, descriptor, payload)
        )
        active.append(key)

    rebuilt = []
    object_order = {
        (obj.type.name, int(obj.path_id)): index
        for index, obj in enumerate(environment.objects)
    }
    for assets_file in environment.assets:
        source_node = str(getattr(assets_file, "name", "") or "")
        if source_node not in managed_nodes:
            continue
        resource_name = _canonical_resource_name(assets_file, bundle)
        previous = bundle.files.get(resource_name)
        resource_data = bytearray(
            _node_bytes(previous) if previous is not None else b""
        )
        resource_endian = getattr(previous, "endian", ">")
        resource_flags = int(getattr(previous, "flags", 0))
        bound = []
        preserved = []
        overwritten = []
        appended = []
        for item, obj, old, payload in sorted(
            groups.get(source_node, []),
            key=lambda value: object_order.get(
                (
                    value[0]["type"],
                    int(value[0]["path_id"]),
                ),
                1 << 62,
            ),
        ):
            old_offset = int(old["offset"])
            old_size = int(old["size"])
            old_end = old_offset + old_size
            same_resource = (
                os.path.basename(str(old["path"])).casefold()
                == resource_name.casefold()
            )
            fits_existing = (
                same_resource
                and old_offset >= 0
                and old_end <= len(resource_data)
            )
            existing_matches = (
                fits_existing
                and old_size == len(payload)
                and _sha256(bytes(resource_data[old_offset:old_end]))
                == _sha256(payload)
            )
            if existing_matches:
                new = dict(old)
                preserved.append(int(item["path_id"]))
            elif fits_existing and old_size == len(payload):
                resource_data[old_offset:old_end] = payload
                new = dict(old)
                overwritten.append(int(item["path_id"]))
            else:
                padding = (-len(resource_data)) % 16
                if padding:
                    resource_data.extend(b"\0" * padding)
                offset = len(resource_data)
                resource_data.extend(payload)
                new = {
                    "kind": old["kind"],
                    "path": f"archive:/{source_node}/{resource_name}",
                    "offset": offset,
                    "size": len(payload),
                }
                _patch_stream_descriptor(obj, old, new)
                appended.append(int(item["path_id"]))
            item["stream"].update(
                {
                    "kind": old["kind"],
                    "path": new["path"],
                    "offset": int(new["offset"]),
                    "size": len(payload),
                    "payload_sha256": _sha256(payload),
                }
            )
            bound.append(
                {
                    "type": item["type"],
                    "path_id": int(item["path_id"]),
                    "offset": int(new["offset"]),
                    "size": len(payload),
                }
            )
        replacement = EndianBinaryReader(
            bytes(resource_data), endian=resource_endian
        )
        replacement.flags = resource_flags
        replacement.name = resource_name
        bundle.files[resource_name] = replacement
        rebuilt.append(
            {
                "source_node": source_node,
                "resource_node": resource_name,
                "bytes": len(resource_data),
                "assets": bound,
                "preserved_path_ids": preserved,
                "overwritten_path_ids": overwritten,
                "appended_path_ids": appended,
            }
        )

    issues = _validate_stream_bindings(environment, assets)
    if issues:
        raise ValueError(
            "External resource validation failed before save: "
            + json.dumps(issues[:8], ensure_ascii=False)
        )
    return {
        "active_stream_assets": len(active),
        "resources": rebuilt,
        "issues": [],
    }


def _validate_stream_bindings(
    environment,
    assets: Optional[List[dict]] = None,
) -> List[dict]:
    expected = {
        (item["type"], int(item["path_id"])): item
        for item in (assets or [])
    }
    issues = []
    for obj in environment.objects:
        descriptor = _stream_descriptor(obj)
        if descriptor is None:
            continue
        basename = os.path.basename(descriptor["path"])
        resource = environment.file.files.get(basename)
        if resource is None:
            issues.append(
                {
                    "type": obj.type.name,
                    "path_id": int(obj.path_id),
                    "kind": "missing_resource_node",
                    "path": descriptor["path"],
                }
            )
            continue
        end = descriptor["offset"] + descriptor["size"]
        if descriptor["offset"] < 0 or end > int(resource.Length):
            issues.append(
                {
                    "type": obj.type.name,
                    "path_id": int(obj.path_id),
                    "kind": "resource_slice_out_of_bounds",
                    "offset": descriptor["offset"],
                    "size": descriptor["size"],
                    "resource_bytes": int(resource.Length),
                }
            )
            continue
        item = expected.get((obj.type.name, int(obj.path_id)))
        if item and item.get("stream", {}).get("payload_sha256"):
            payload = _node_bytes(resource)[
                descriptor["offset"]:
                descriptor["offset"] + descriptor["size"]
            ]
            if _sha256(payload) != item["stream"]["payload_sha256"]:
                issues.append(
                    {
                        "type": obj.type.name,
                        "path_id": int(obj.path_id),
                        "kind": "resource_payload_hash_mismatch",
                    }
                )
        source_node = str(getattr(obj.assets_file, "name", "") or "")
        expected_path = f"archive:/{source_node}/{basename}"
        if descriptor["path"] != expected_path:
            issues.append(
                {
                    "type": obj.type.name,
                    "path_id": int(obj.path_id),
                    "kind": "noncanonical_cab_path",
                    "path": descriptor["path"],
                    "expected": expected_path,
                }
            )
    return issues


def _canonicalize_project_files(
    project_dir: str, assets: List[dict]
) -> List[dict]:
    """Make every active file follow ``<name>_<PathID>`` after a rebuild."""

    canonical = []
    for source_item in assets:
        item = {
            key: value for key, value in source_item.items()
            if not key.startswith("_")
        }
        desired_stem = _asset_file_stem(
            item.get("name", ""), item["path_id"], item["type"]
        )
        raw_path = os.path.join(project_dir, *item["raw"].split("/"))
        desired_raw = os.path.join(
            os.path.dirname(raw_path), f"{desired_stem}.raw"
        )
        if os.path.normcase(raw_path) != os.path.normcase(desired_raw):
            if os.path.exists(desired_raw):
                raise ValueError(
                    f"Cannot canonicalize {raw_path}; {desired_raw} exists"
                )
            os.replace(raw_path, desired_raw)
            raw_path = desired_raw
        item["raw"] = _relative(raw_path, project_dir)
        item["file_stem"] = desired_stem
        raw = open(raw_path, "rb").read()
        item["bytes"] = len(raw)
        item["sha256"] = _sha256(raw)

        editable = item.get("editable")
        if editable:
            editable_path = os.path.join(
                project_dir, *editable.split("/")
            )
            extension = os.path.splitext(editable_path)[1]
            desired_editable = os.path.join(
                os.path.dirname(editable_path), f"{desired_stem}{extension}"
            )
            if (
                os.path.normcase(editable_path)
                != os.path.normcase(desired_editable)
            ):
                if os.path.exists(desired_editable):
                    raise ValueError(
                        f"Cannot canonicalize {editable_path}; "
                        f"{desired_editable} exists"
                    )
                os.replace(editable_path, desired_editable)
                editable_path = desired_editable
            item["editable"] = _relative(editable_path, project_dir)
            item["editable_sha256"] = _sha256(
                open(editable_path, "rb").read()
            )
        stream = item.get("stream")
        if stream and stream.get("payload"):
            payload_path = os.path.join(
                project_dir, *stream["payload"].split("/")
            )
            desired_payload = os.path.join(
                os.path.dirname(payload_path), f"{desired_stem}.resS"
            )
            if (
                os.path.normcase(payload_path)
                != os.path.normcase(desired_payload)
            ):
                if os.path.exists(desired_payload):
                    raise ValueError(
                        f"Cannot canonicalize {payload_path}; "
                        f"{desired_payload} exists"
                    )
                os.replace(payload_path, desired_payload)
                payload_path = desired_payload
            payload = open(payload_path, "rb").read()
            stream["payload"] = _relative(payload_path, project_dir)
            stream["size"] = len(payload)
            stream["payload_sha256"] = _sha256(payload)
        canonical.append(item)
    return canonical


def _synchronize_project_files(
    project_dir: str,
    manifest: dict,
    verified,
    assets: List[dict],
) -> None:
    """Persist rebuilt raw locators and resource nodes back into the project."""

    objects = {
        (obj.type.name, int(obj.path_id)): obj
        for obj in verified.objects
    }
    for item in assets:
        obj = objects[(item["type"], int(item["path_id"]))]
        raw_path = os.path.join(project_dir, *item["raw"].split("/"))
        _write_bytes(raw_path, bytes(obj.get_raw_data()))
        descriptor = _stream_descriptor(obj)
        if descriptor is not None and item.get("stream"):
            item["stream"].update(descriptor)

    nodes_by_name = {
        node["name"]: node for node in manifest.get("nodes", [])
    }
    for name, node in verified.file.files.items():
        node_info = nodes_by_name.get(name)
        if node_info is None and name.lower().endswith((".ress", ".resource")):
            node_path = os.path.join(
                project_dir, "internal", _safe_filename(name, "resource")
            )
            node_info = {
                "name": name,
                "kind": "resource",
                "flags": int(getattr(node, "flags", 0)),
                "path": _relative(node_path, project_dir),
            }
            manifest.setdefault("nodes", []).append(node_info)
            nodes_by_name[name] = node_info
        if not node_info or node_info.get("kind") != "resource":
            continue
        data = _node_bytes(node)
        node_path = os.path.join(
            project_dir, *node_info["path"].split("/")
        )
        _write_bytes(node_path, data)
        node_info["bytes"] = len(data)
        node_info["sha256"] = _sha256(data)
        node_info["flags"] = int(getattr(node, "flags", 0))


def _refresh_project_manifest(
    project_dir: str,
    manifest: dict,
    verified,
    edited: dict,
) -> None:
    _synchronize_project_files(
        project_dir, manifest, verified, edited["assets"]
    )
    assets = _canonicalize_project_files(
        project_dir, edited["assets"]
    )
    manifest["$schema"] = SCHEMA
    manifest["assets"] = assets
    manifest["inventory"] = dict(
        sorted(Counter(obj.type.name for obj in verified.objects).items())
    )
    manifest["relationships"] = [
        relation for relation in (
            _asset_relationship(obj) for obj in verified.objects
        )
        if relation is not None
    ]
    validation = validate_sprite_atlas_relationships(verified)
    manifest["sprite_atlas_validation"] = validation
    history = manifest.setdefault("change_history", [])
    history.append(
        {
            "added": edited["added"],
            "deleted": edited["deleted"],
            "renamed": edited["renamed"],
            "modified_raw": edited["modified_raw"],
            "path_id_changes": edited.get("path_id_changes", []),
            "remapped_references": edited.get(
                "remapped_references", []
            ),
            "nulled_references": edited.get("nulled_references", []),
            "atlas_sync": edited.get("atlas_sync", {}),
            "catalog_sync": edited.get("catalog_sync", {}),
            "stream_sync": edited.get("stream_sync", {}),
        }
    )
    _write_json(
        os.path.join(project_dir, "bundle_manifest.json"), manifest
    )
    _write_asset_indexes(project_dir, manifest)


def _atlas_texture_ids(environment) -> set:
    texture_ids = set()
    for obj in environment.objects:
        if obj.type.name != "SpriteAtlas":
            continue
        atlas = obj.read(False)
        for data in atlas.m_RenderDataMap.values():
            if int(data.texture.file_id) == 0 and int(data.texture.path_id):
                texture_ids.add(int(data.texture.path_id))
    return texture_ids


def optimize_sprite_atlas_textures(
    environment, *, require_existing_resource: bool = False
) -> List[dict]:
    optimized = []
    if require_existing_resource and not any(
        name.lower().endswith((".ress", ".resource"))
        for name in environment.file.files
    ):
        return optimized
    atlas_texture_ids = _atlas_texture_ids(environment)
    for obj in environment.objects:
        if obj.type.name != "Texture2D" or int(obj.path_id) not in atlas_texture_ids:
            continue
        texture = obj.read(False)
        if texture.m_TextureFormat != TextureFormat.RGBA32:
            continue
        if int(texture.m_Width) % 4 or int(texture.m_Height) % 4:
            continue
        result = optimize_texture_runtime_storage(
            texture, TextureFormat.ETC2_RGBA8, externalize=True
        )
        texture.save()
        optimized.append({"path_id": int(obj.path_id), **result})
    return optimized


def rebuild_bundle_project(
    project_dir: str,
    output_path: str,
    *,
    packer: str = "auto",
    optimize_atlas_textures: bool = False,
) -> Dict[str, object]:
    """Rebuild a bundle and preserve source texture formats by default.

    AssetBundle LZ4/LZMA compression is lossless.  Atlas transcoding is kept as
    an explicit opt-in for research workflows because RGBA32 -> ETC2 reduces
    file size by introducing visible color and alpha-edge loss.
    """

    project_dir = os.path.abspath(project_dir)
    manifest = _load_manifest(project_dir)
    backup = os.path.join(
        project_dir, *manifest["source"]["backup"].split("/")
    )
    if not os.path.isfile(backup):
        raise ValueError(
            f"Bundle project backup is missing: {backup}"
        )
    environment = UnityPy_AOV.load(backup)
    bundle = environment.file
    _restore_resource_nodes(bundle, project_dir, manifest)
    _install_imported_types(environment, manifest)
    edited = _apply_project_objects(
        environment, project_dir, manifest
    )
    stream_sync = (
        _rebuild_stream_resources(
            environment, project_dir, manifest, edited["assets"]
        )
        if manifest.get("$schema") != SCHEMA_V1
        else {"active_stream_assets": 0, "resources": [], "issues": []}
    )
    edited["stream_sync"] = stream_sync
    reference_scope = {
        int(item["path_id"])
        for key in ("added", "renamed", "modified_raw")
        for item in edited.get(key, [])
        if "path_id" in item
    }
    reference_scope.update(
        int(item["to_path_id"])
        for item in edited.get("path_id_changes", [])
    )
    reference_scope.update(
        int(item["path_id"])
        for key in ("nulled_references", "remapped_references")
        for item in edited.get(key, [])
    )
    added_preloads = repair_sprite_atlas_preloads(environment)
    optimized = (
        optimize_sprite_atlas_textures(environment)
        if optimize_atlas_textures
        else []
    )
    validation = validate_sprite_atlas_relationships(environment)
    if validation["issues"]:
        raise ValueError(
            "Bundle relationship validation failed before save: "
            + json.dumps(validation["issues"][:8], ensure_ascii=False)
        )
    reference_issues = _validate_local_references(
        environment, reference_scope
    )
    if reference_issues:
        raise ValueError(
            "Local object references are unresolved before save: "
            + json.dumps(reference_issues[:8], ensure_ascii=False)
        )
    texture_expectations = {}
    for obj in environment.objects:
        if obj.type.name != "Texture2D":
            continue
        texture = obj.read(False)
        texture_expectations[int(obj.path_id)] = {
            "metadata": texture_runtime_metadata(texture),
            "image_sha256": _sha256(bytes(texture.image_data)),
        }

    selected_packer = (
        manifest["unityfs"]["recommended_packer"]
        if packer == "auto"
        else packer
    )
    data = bundle.save(selected_packer)
    output_path = os.path.abspath(output_path)
    _write_bytes(output_path, data)

    verified = UnityPy_AOV.load(output_path)
    expected_inventory = {
        (int(item["path_id"]), item["type"]) for item in edited["assets"]
    }
    actual_inventory = {
        (int(obj.path_id), obj.type.name) for obj in verified.objects
    }
    if actual_inventory != expected_inventory:
        raise ValueError("PathID/type inventory changed after project rebuild")
    verified_objects = {
        int(obj.path_id): obj for obj in verified.objects
    }
    for path_id, expectation in texture_expectations.items():
        obj = verified_objects.get(path_id)
        if obj is None or obj.type.name != "Texture2D":
            raise ValueError(
                f"Texture2D {path_id} was lost after project rebuild"
            )
        texture = obj.read(False)
        validate_texture_roundtrip(texture, expectation["metadata"])
        if _sha256(bytes(texture.image_data)) != expectation["image_sha256"]:
            raise ValueError(
                f"Texture2D {path_id} encoded pixels changed during "
                "lossless project rebuild"
            )
    verified_validation = validate_sprite_atlas_relationships(verified)
    if verified_validation["issues"]:
        raise ValueError(
            "Bundle relationship validation failed after reload: "
            + json.dumps(verified_validation["issues"][:8], ensure_ascii=False)
        )
    verified_reference_issues = _validate_local_references(
        verified, reference_scope
    )
    if verified_reference_issues:
        raise ValueError(
            "Local object references are unresolved after reload: "
            + json.dumps(
                verified_reference_issues[:8], ensure_ascii=False
            )
        )
    verified_stream_issues = (
        _validate_stream_bindings(verified, edited["assets"])
        if manifest.get("$schema") != SCHEMA_V1
        else []
    )
    if verified_stream_issues:
        raise ValueError(
            "External resource validation failed after reload: "
            + json.dumps(
                verified_stream_issues[:8], ensure_ascii=False
            )
        )
    if manifest.get("$schema") != SCHEMA_V1:
        _refresh_project_manifest(
            project_dir, manifest, verified, edited
        )
    return {
        "path": output_path,
        "bytes": len(data),
        "packer": selected_packer,
        "assets": len(actual_inventory),
        "edited_textures": edited["textures"],
        "edited_typetrees": edited["typetrees"],
        "added_assets": edited["added"],
        "deleted_assets": edited["deleted"],
        "renamed_assets": edited["renamed"],
        "modified_raw_assets": edited["modified_raw"],
        "path_id_changes": edited.get("path_id_changes", []),
        "remapped_references": edited.get("remapped_references", []),
        "nulled_references": edited.get("nulled_references", []),
        "catalog_sync": edited.get("catalog_sync", {}),
        "atlas_sync": edited.get("atlas_sync", {}),
        "stream_sync": stream_sync,
        "optimized_textures": optimized,
        "texture_quality_preserved": True,
        "added_preload_path_ids": sorted(set(added_preloads)),
        "relationship_issues": 0,
        "reference_issues": 0,
    }
