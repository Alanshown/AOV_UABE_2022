# -*- coding: utf-8 -*-
"""Cross-bundle effect indexing, normalized scenes and ``.effect`` export.

The Unity player build stores prefab assets as top-level GameObjects referenced
by AssetBundle.m_newPathContainer.  This module keeps that serialized identity
separate from preview/runtime representations so a later Blender importer can
consume the package without depending on UnityPy internals.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import re
import struct
import tempfile
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from AssetbundleUtils.AnimationPipeline import (
    AnimationProjectIndex,
    AnimationTake,
    AsciiFbxWriter,
)


EFFECT_COMPONENT_TYPES = {
    "Animator", "Animation", "ParticleSystem", "ParticleSystemRenderer",
    "TrailRenderer", "LineRenderer", "MeshFilter", "MeshRenderer",
    "SkinnedMeshRenderer", "MonoBehaviour",
}

DEPENDENCY_TYPES = {
    "AnimationClip", "AnimatorController", "RuntimeAnimatorController",
    "Avatar", "Material", "Mesh", "MonoBehaviour", "MonoScript", "Shader",
    "Texture2D", "Sprite", "SpriteAtlas",
}


def asset_id(file_index: int, path_id: int) -> str:
    return f"f{int(file_index)}:p{int(path_id)}"


def _safe_name(value: str, fallback: str = "asset") -> str:
    value = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value)).strip("._")
    return value[:120] or fallback


def _json_value(value):
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        return _json_value(vars(value))
    return str(value)


def iter_pptrs(value, field_path: str = ""):
    """Yield every serialized PPtr, including tuple-based Unity maps."""
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value:
            if int(value.get("m_PathID", 0)):
                yield field_path, value
            return
        for key, item in value.items():
            path = f"{field_path}/{key}" if field_path else str(key)
            yield from iter_pptrs(item, path)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from iter_pptrs(item, f"{field_path}[{index}]")


def _raw_local_asset_pointers(
    index, file_index: int, source_obj, target_types: Iterable[str],
) -> List[dict]:
    """Recover proven local PPtrs when an AOV TypeTree is partially unreadable."""

    raw = bytes(source_obj.get_raw_data())
    endian = getattr(source_obj.reader, "endian", "<")
    allowed = set(target_types)
    matches = []
    for target in index.objects[int(file_index)].values():
        if target.type.name not in allowed:
            continue
        path_id = int(target.path_id)
        token = struct.pack(endian + "iq", 0, path_id)
        offset = raw.find(token)
        if offset >= 0:
            matches.append((
                offset,
                {"m_FileID": 0, "m_PathID": path_id},
            ))
    matches.sort(key=lambda item: item[0])
    return [pointer for _offset, pointer in matches]


@dataclass(frozen=True)
class EffectAssetKey:
    file_index: int
    path_id: int

    @property
    def id(self) -> str:
        return asset_id(self.file_index, self.path_id)


@dataclass
class EffectReference:
    source: str
    field: str
    target: str
    target_type: str


@dataclass
class EffectComponent:
    id: str
    type: str
    enabled: bool
    properties: dict


@dataclass
class EffectNode:
    id: str
    name: str
    full_path: str
    parent: Optional[str]
    active: bool
    local_position: Tuple[float, float, float]
    local_rotation: Tuple[float, float, float, float]
    local_scale: Tuple[float, float, float]
    components: List[EffectComponent] = field(default_factory=list)


@dataclass
class EffectAnimation:
    id: str
    name: str
    controller: str
    animator_node: str
    duration: float
    sample_rate: float
    binding_count: int


@dataclass
class EffectRoot:
    file_index: int
    game_object_id: int
    transform_id: int
    name: str
    node_count: int
    component_counts: Dict[str, int]
    score: int
    preload_index: int = 0
    preload_size: int = 0

    @property
    def id(self) -> str:
        return asset_id(self.file_index, self.game_object_id)


@dataclass
class EffectScene:
    schema_version: int
    name: str
    root: str
    source_bundles: List[dict]
    nodes: List[EffectNode]
    animations: List[EffectAnimation]
    assets: Dict[str, dict]
    references: List[EffectReference]
    duration: float

    def to_dict(self) -> dict:
        return _json_value(asdict(self))


class EffectProjectIndex:
    """Index prefab-like effect roots and all forward/reverse dependencies."""

    def __init__(
        self, paths: Sequence[str], environments: Optional[Sequence[object]] = None,
        project: Optional[AnimationProjectIndex] = None,
    ):
        self.project = project or AnimationProjectIndex(paths, environments)
        self.paths = self.project.paths
        self.objects = self.project.objects
        self.roots: List[EffectRoot] = []
        self.root_by_id: Dict[str, EffectRoot] = {}
        self.asset_roots: Dict[EffectAssetKey, set[str]] = defaultdict(set)
        self.references: Dict[str, List[EffectReference]] = defaultdict(list)
        self._root_transform_sets: Dict[str, set[int]] = {}
        self._build()

    def rebuild(self):
        """Rebuild effect roots/dependencies from the shared reference graph."""
        self.roots.clear()
        self.root_by_id.clear()
        self.asset_roots.clear()
        self.references.clear()
        self._root_transform_sets.clear()
        self._build()

    @classmethod
    def from_path(cls, path: str):
        from AssetbundleUtils.AnimationPipeline import discover_project_paths
        return cls(discover_project_paths(path))

    def object(self, file_index: int, path_id: int):
        return self.project.object(file_index, path_id)

    def tree(self, file_index: int, path_id: int) -> dict:
        return self.project.tree(file_index, path_id)

    def resolve_pptr(self, source_obj, source_file_index: int, pointer: dict):
        return self.project.resolve_pptr(source_obj, source_file_index, pointer)

    def object_name(self, file_index: int, obj) -> str:
        try:
            value = str(obj.peek_name(""))
            if value:
                return value
        except Exception:
            pass
        try:
            value = self.tree(file_index, int(obj.path_id)).get("m_Name")
            if value:
                return str(value)
        except Exception:
            pass
        return f"{obj.type.name}_{obj.path_id}"

    def _top_level_entries(self):
        yielded = set()
        for file_index, objects in enumerate(self.objects):
            for obj in objects.values():
                if obj.type.name != "AssetBundle":
                    continue
                try:
                    bundle = obj.read(False)
                except Exception:
                    continue
                containers = list(
                    getattr(bundle, "m_ContainerEntries", [])
                )
                for _route, info in containers:
                    pointer = getattr(info, "asset", None)
                    if pointer is None:
                        continue
                    target_file_index, target = self.resolve_pptr(
                        obj,
                        file_index,
                        {
                            "m_FileID": int(pointer.file_id),
                            "m_PathID": int(pointer.path_id),
                        },
                    )
                    if target is None or target.type.name != "GameObject":
                        continue
                    key = target_file_index, int(target.path_id)
                    if key in yielded:
                        continue
                    yielded.add(key)
                    yield (
                        target_file_index, int(target.path_id),
                        int(getattr(info, "preload_index", 0)),
                        int(getattr(info, "preload_size", 0)),
                    )
                if containers:
                    continue
                # Some AOV bundles intentionally keep a complete preload table
                # while omitting m_Container.  In that layout the authoritative
                # prefab/effect entry points are the parentless GameObjects
                # referenced by preload, not names inferred from the asset list.
                for preload_index, pointer in enumerate(
                    getattr(bundle, "m_PreloadTable", [])
                ):
                    target_file_index, target = self.resolve_pptr(
                        obj,
                        file_index,
                        {
                            "m_FileID": int(pointer.file_id),
                            "m_PathID": int(pointer.path_id),
                        },
                    )
                    if target is None or target.type.name != "GameObject":
                        continue
                    game_object_id = int(target.path_id)
                    transform_id = self.project.transform_by_game_object.get(
                        (target_file_index, game_object_id)
                    )
                    transform = self.project.transforms.get(
                        (target_file_index, transform_id)
                    )
                    if transform is None or int(transform.parent_id):
                        continue
                    key = target_file_index, game_object_id
                    if key in yielded:
                        continue
                    yielded.add(key)
                    yield (
                        target_file_index, game_object_id,
                        int(preload_index), 1,
                    )

    def _subtree_transform_ids(self, file_index: int, root_transform_id: int):
        result = []
        stack = [int(root_transform_id)]
        while stack:
            transform_id = stack.pop()
            if transform_id in result:
                continue
            record = self.project.transforms.get((file_index, transform_id))
            if record is None:
                continue
            result.append(transform_id)
            stack.extend(reversed(record.children))
        return result

    def _component_objects(self, file_index: int, transform_ids: Iterable[int]):
        for transform_id in transform_ids:
            record = self.project.transforms[file_index, int(transform_id)]
            game_object = self.project.game_objects.get(
                (file_index, record.game_object_id)
            )
            if game_object is None:
                continue
            for component in game_object.m_Components:
                obj = self.object(file_index, int(component.path_id))
                if obj is not None:
                    yield record, obj

    @staticmethod
    def _effect_score(counts: Counter) -> int:
        return (
            counts["ParticleSystem"] * 12
            + counts["TrailRenderer"] * 10
            + counts["LineRenderer"] * 10
            + counts["ParticleSystemRenderer"] * 4
            + counts["MeshRenderer"] * 2
            + counts["SkinnedMeshRenderer"] * 2
            + counts["Animator"] * 3
        )

    @staticmethod
    def _is_effect(counts: Counter) -> bool:
        if counts["ParticleSystem"] or counts["TrailRenderer"] or counts["LineRenderer"]:
            return True
        return bool(
            counts["Animator"]
            and (counts["MeshRenderer"] or counts["SkinnedMeshRenderer"])
        )

    def _build(self):
        seen_roots = set()
        for file_index, game_object_id, preload_index, preload_size in self._top_level_entries():
            root_transform_id = self.project.transform_by_game_object.get(
                (file_index, game_object_id)
            )
            if root_transform_id is None or (file_index, game_object_id) in seen_roots:
                continue
            seen_roots.add((file_index, game_object_id))
            transform_ids = self._subtree_transform_ids(file_index, root_transform_id)
            components = list(self._component_objects(file_index, transform_ids))
            counts = Counter(obj.type.name for _record, obj in components)
            if not self._is_effect(counts):
                continue
            root_record = self.project.transforms[file_index, root_transform_id]
            root = EffectRoot(
                file_index=file_index,
                game_object_id=game_object_id,
                transform_id=root_transform_id,
                name=root_record.name,
                node_count=len(transform_ids),
                component_counts=dict(counts),
                score=self._effect_score(counts),
                preload_index=preload_index,
                preload_size=preload_size,
            )
            self.roots.append(root)
            self.root_by_id[root.id] = root
            self._root_transform_sets[root.id] = set(transform_ids)
            self._index_root_dependencies(root, components)
        self.roots.sort(key=lambda item: (-item.score, -item.node_count, item.name.lower()))

    def _remember_asset(self, root: EffectRoot, file_index: int, obj):
        key = EffectAssetKey(file_index, int(obj.path_id))
        self.asset_roots[key].add(root.id)

    def _index_root_dependencies(self, root: EffectRoot, components):
        queue = deque()
        for record, obj in components:
            self._remember_asset(root, root.file_index, obj)
            queue.append((root.file_index, obj))
            self.asset_roots[
                EffectAssetKey(root.file_index, record.game_object_id)
            ].add(root.id)
            self.asset_roots[
                EffectAssetKey(root.file_index, record.path_id)
            ].add(root.id)
        visited = set()
        graph = self.project.reference_graph
        while queue:
            file_index, obj = queue.popleft()
            key = EffectAssetKey(file_index, int(obj.path_id))
            if key in visited:
                continue
            visited.add(key)
            for edge in graph.outgoing((file_index, int(obj.path_id))):
                if "m_GameObject" in edge.field:
                    continue
                target_file_index, target_path_id = edge.target
                target = self.object(target_file_index, target_path_id)
                if target is None:
                    continue
                target_key = EffectAssetKey(target_file_index, int(target.path_id))
                reference = EffectReference(
                    source=key.id, field=edge.field, target=target_key.id,
                    target_type=target.type.name,
                )
                if not any(
                    item.field == reference.field and item.target == reference.target
                    for item in self.references[key.id]
                ):
                    self.references[key.id].append(reference)
                if target.type.name in DEPENDENCY_TYPES:
                    self.asset_roots[target_key].add(root.id)
                    if target_key not in visited:
                        queue.append((target_file_index, target))

    def resolve_effects(self, file_index: int, path_id: int) -> List[EffectRoot]:
        ids = self.asset_roots.get(EffectAssetKey(file_index, int(path_id)), set())
        return sorted(
            (self.root_by_id[root_id] for root_id in ids),
            key=lambda item: (-item.score, item.name.lower()),
        )

    def root(self, root_id: str) -> EffectRoot:
        return self.root_by_id[root_id]

    def _clip_summary(self, file_index: int, obj) -> Tuple[float, float, int]:
        tree = self.tree(file_index, int(obj.path_id))
        record = next((
            item for item in self.project.animations
            if item.file_index == file_index and item.path_id == int(obj.path_id)
        ), None)
        if record is not None:
            return record.duration, record.sample_rate, record.binding_count
        return 0.0, float(tree.get("m_SampleRate", 30.0)), 0

    def build_scene(self, root_or_id) -> EffectScene:
        root = root_or_id if isinstance(root_or_id, EffectRoot) else self.root(root_or_id)
        transform_ids = self._root_transform_sets[root.id]
        nodes = []
        animations: List[EffectAnimation] = []
        scene_asset_keys = set()
        scene_references = []
        seen_clips = set()

        def remember_animation(
            clip_file_index, clip, controller_id, animator_node
        ):
            if clip is None or clip.type.name != "AnimationClip":
                return
            clip_key = EffectAssetKey(clip_file_index, int(clip.path_id))
            if clip_key in seen_clips:
                return
            seen_clips.add(clip_key)
            duration, sample_rate, binding_count = self._clip_summary(
                clip_file_index, clip
            )
            animations.append(EffectAnimation(
                id=clip_key.id,
                name=self.object_name(clip_file_index, clip),
                controller=controller_id,
                animator_node=animator_node,
                duration=duration,
                sample_rate=sample_rate,
                binding_count=binding_count,
            ))

        for transform_id in self._subtree_transform_ids(root.file_index, root.transform_id):
            if transform_id not in transform_ids:
                continue
            record = self.project.transforms[root.file_index, transform_id]
            game_object = self.project.game_objects[(root.file_index, record.game_object_id)]
            node_id = asset_id(root.file_index, record.game_object_id)
            parent = None
            if record.parent_id in transform_ids:
                parent_record = self.project.transforms[root.file_index, record.parent_id]
                parent = asset_id(root.file_index, parent_record.game_object_id)
            components = []
            for component_pointer in game_object.m_Components:
                component_obj = self.object(root.file_index, int(component_pointer.path_id))
                if component_obj is None:
                    continue
                component_id = asset_id(root.file_index, int(component_obj.path_id))
                scene_asset_keys.add(EffectAssetKey(root.file_index, int(component_obj.path_id)))
                try:
                    component_tree = self.tree(root.file_index, int(component_obj.path_id))
                except Exception:
                    component_tree = {}
                components.append(EffectComponent(
                    id=component_id,
                    type=component_obj.type.name,
                    enabled=bool(component_tree.get("m_Enabled", True)),
                    properties=_json_value(component_tree),
                ))
                if component_obj.type.name == "Animator":
                    controller_pointer = component_tree.get("m_Controller", {})
                    controller_file_index, controller = self.resolve_pptr(
                        component_obj, root.file_index, controller_pointer
                    )
                    if controller is not None:
                        controller_id = asset_id(
                            controller_file_index, int(controller.path_id)
                        )
                        try:
                            controller_tree = self.tree(
                                controller_file_index, int(controller.path_id)
                            )
                        except Exception:
                            controller_tree = {}
                        for clip_pointer in controller_tree.get("m_AnimationClips", []):
                            clip_file_index, clip = self.resolve_pptr(
                                controller, controller_file_index, clip_pointer
                            )
                            remember_animation(
                                clip_file_index, clip, controller_id, node_id
                            )
                elif component_obj.type.name == "Animation":
                    pointers = [component_tree.get("m_Animation", {})]
                    pointers.extend(component_tree.get("m_Animations", []))
                    for clip_pointer in pointers:
                        if not isinstance(clip_pointer, dict):
                            continue
                        clip_file_index, clip = self.resolve_pptr(
                            component_obj, root.file_index, clip_pointer
                        )
                        remember_animation(
                            clip_file_index, clip, component_id, node_id
                        )
                elif component_obj.type.name == "MonoBehaviour":
                    for edge in self.project.reference_graph.outgoing(
                        (root.file_index, int(component_obj.path_id))
                    ):
                        target_file_index, target_path_id = edge.target
                        target = self.object(target_file_index, target_path_id)
                        if target is None:
                            continue
                        if target.type.name == "AnimationClip":
                            remember_animation(
                                target_file_index, target, component_id, node_id
                            )
                        elif target.type.name in (
                            "AnimatorController", "RuntimeAnimatorController",
                        ):
                            try:
                                controller_tree = self.tree(
                                    target_file_index, target_path_id
                                )
                            except Exception:
                                continue
                            controller_id = asset_id(
                                target_file_index, target_path_id
                            )
                            for clip_pointer in controller_tree.get(
                                "m_AnimationClips", []
                            ):
                                clip_file_index, clip = self.resolve_pptr(
                                    target, target_file_index, clip_pointer
                                )
                                remember_animation(
                                    clip_file_index, clip, controller_id, node_id
                                )
            nodes.append(EffectNode(
                id=node_id,
                name=record.name,
                full_path=record.full_path,
                parent=parent,
                active=bool(getattr(game_object, "m_IsActive", True)),
                local_position=record.position,
                local_rotation=record.rotation,
                local_scale=record.scale,
                components=components,
            ))

        root_asset_ids = self.asset_roots
        for key, root_ids in root_asset_ids.items():
            if root.id in root_ids:
                scene_asset_keys.add(key)
        assets = {}
        for key in sorted(scene_asset_keys, key=lambda item: (item.file_index, item.path_id)):
            obj = self.object(key.file_index, key.path_id)
            if obj is None:
                continue
            assets[key.id] = {
                "id": key.id,
                "bundle": os.path.basename(self.paths[key.file_index]),
                "file_index": key.file_index,
                "path_id": key.path_id,
                "type": obj.type.name,
                "name": self.object_name(key.file_index, obj),
            }
            scene_references.extend(self.references.get(key.id, []))
        duration = max(
            [item.duration for item in animations]
            + [float(component.properties.get("lengthInSec", 0.0))
               for node in nodes for component in node.components
               if component.type == "ParticleSystem"]
            + [1.0]
        )
        return EffectScene(
            schema_version=2,
            name=root.name,
            root=root.id,
            source_bundles=[{
                "file_index": index,
                "name": os.path.basename(path),
                "path": path,
            } for index, path in enumerate(self.paths)],
            nodes=nodes,
            animations=animations,
            assets=assets,
            references=scene_references,
            duration=float(duration),
        )


def _write_json(path: str, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(_json_value(value), handle, ensure_ascii=False, indent=2)


def _asset_filename(asset: dict, extension: str) -> str:
    identity = asset["id"].replace(":", "_")
    return f"{_safe_name(asset['name'], asset['type'])}__{identity}.{extension}"


def _write_bytes(path: str, value: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(value)


def _package_file_info(root: str, relative: str) -> dict:
    path = os.path.join(root, *relative.replace("\\", "/").split("/"))
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return {
        "path": relative.replace("\\", "/"),
        "size": os.path.getsize(path),
        "sha256": digest.hexdigest(),
    }


def export_effect_package(
    index: EffectProjectIndex, root_or_id, output_path: str,
) -> dict:
    """Export a portable, self-describing effect archive.

    Geometry/hierarchy is stored in ``scene.fbx``. Unity-specific component
    payloads remain lossless JSON/raw data so a Blender 4.5+ importer can map
    them without requiring the original bundles.
    """
    root = root_or_id if isinstance(root_or_id, EffectRoot) else index.root(root_or_id)
    scene = index.build_scene(root)
    output_path = os.path.abspath(output_path)
    if not output_path.lower().endswith(".effect"):
        output_path += ".effect"
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    exported = Counter()
    file_map = {}
    asset_files = {}
    export_errors = []
    with tempfile.TemporaryDirectory(prefix="aov_effect_") as temp_dir:
        scene_path = os.path.join(temp_dir, "scene.fbx")
        model = index.project._make_model_candidate(root.file_index, root.transform_id)
        take = AnimationTake(
            name=scene.animations[0].name if scene.animations else scene.name,
            sample_rate=30.0, duration=scene.duration, tracks={},
            mapped_bindings=0, total_transform_bindings=0,
        )
        AsciiFbxWriter(
            index.project, model, take,
            include_model=True, include_attachments=True,
        ).write(scene_path)
        file_map["scene"] = "scene.fbx"

        for identity, asset in scene.assets.items():
            obj = index.object(asset["file_index"], asset["path_id"])
            if obj is None:
                export_errors.append({
                    "asset": identity,
                    "error": "Object was unavailable during package export",
                })
                continue
            asset_type = asset["type"]
            descriptor = {
                "id": identity,
                "type": asset_type,
                "name": asset["name"],
                "bundle": asset["bundle"],
                "primary": None,
                "raw": None,
                "metadata": None,
                "alternates": [],
                "errors": [],
            }
            # Every referenced asset receives its original serialized object and
            # TypeTree JSON. Optimized representations below are additional, not
            # replacements, so a future importer never has to consult the ABs.
            asset_directory = os.path.join("assets", _safe_name(asset_type, "Object"))
            raw_relative = os.path.join(
                asset_directory, _asset_filename(asset, "raw")
            ).replace("\\", "/")
            metadata_relative = os.path.join(
                asset_directory, _asset_filename(asset, "json")
            ).replace("\\", "/")
            try:
                _write_bytes(
                    os.path.join(temp_dir, *raw_relative.split("/")),
                    obj.get_raw_data(),
                )
                descriptor["raw"] = raw_relative
                descriptor["alternates"].append(raw_relative)
            except Exception as exc:
                descriptor["errors"].append(
                    f"raw: {type(exc).__name__}: {exc}"
                )
            try:
                tree = index.tree(asset["file_index"], asset["path_id"])
                asset_references = [
                    asdict(reference)
                    for reference in scene.references
                    if reference.source == identity
                ]
                _write_json(
                    os.path.join(temp_dir, *metadata_relative.split("/")),
                    {
                        "asset": asset,
                        "typetree": tree,
                        "references": asset_references,
                    },
                )
                descriptor["metadata"] = metadata_relative
                descriptor["alternates"].append(metadata_relative)
            except Exception as exc:
                descriptor["errors"].append(
                    f"metadata: {type(exc).__name__}: {exc}"
                )
            try:
                if asset_type == "Texture2D":
                    relative = os.path.join(
                        "textures", _asset_filename(asset, "png")
                    ).replace("\\", "/")
                    path = os.path.join(temp_dir, *relative.split("/"))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    obj.read(False).image.save(path, format="PNG")
                elif asset_type == "Material":
                    relative = os.path.join(
                        "materials", _asset_filename(asset, "json")
                    ).replace("\\", "/")
                    _write_json(
                        os.path.join(temp_dir, *relative.split("/")),
                        index.tree(asset["file_index"], asset["path_id"]),
                    )
                elif asset_type == "Shader":
                    relative = os.path.join(
                        "shaders", _asset_filename(asset, "shader")
                    ).replace("\\", "/")
                    path = os.path.join(temp_dir, *relative.split("/"))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    try:
                        shader = obj.read()
                        source = shader.export()
                        if isinstance(source, bytes):
                            source = source.decode("utf-8", errors="replace")
                        with open(
                            path, "w", encoding="utf-8", errors="replace"
                        ) as handle:
                            handle.write(str(source))
                    except Exception as shader_error:
                        relative = os.path.join(
                            "shaders", _asset_filename(asset, "shader.json")
                        ).replace("\\", "/")
                        path = os.path.join(temp_dir, *relative.split("/"))
                        _write_json(path, {
                            "decode_error": (
                                f"{type(shader_error).__name__}: {shader_error}"
                            ),
                            "asset": asset,
                            "raw_file": os.path.basename(path).replace(
                                ".shader.json", ".shader.bin"
                            ),
                        })
                        raw_path = path.replace(".shader.json", ".shader.bin")
                        with open(raw_path, "wb") as handle:
                            handle.write(obj.get_raw_data())
                elif asset_type == "AnimationClip":
                    relative = os.path.join(
                        "animations", _asset_filename(asset, "raw")
                    ).replace("\\", "/")
                    path = os.path.join(temp_dir, *relative.split("/"))
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "wb") as handle:
                        handle.write(obj.get_raw_data())
                    _write_json(
                        os.path.splitext(path)[0] + ".json",
                        index.tree(asset["file_index"], asset["path_id"]),
                    )
                elif asset_type == "ParticleSystem":
                    relative = os.path.join(
                        "particles", _asset_filename(asset, "json")
                    ).replace("\\", "/")
                    _write_json(
                        os.path.join(temp_dir, *relative.split("/")),
                        index.tree(asset["file_index"], asset["path_id"]),
                    )
                else:
                    relative = descriptor["metadata"] or descriptor["raw"]
            except Exception as exc:
                error = f"optimized: {type(exc).__name__}: {exc}"
                descriptor["errors"].append(error)
                relative = descriptor["metadata"] or descriptor["raw"]
            if relative:
                descriptor["primary"] = relative
                if relative not in descriptor["alternates"]:
                    descriptor["alternates"].insert(0, relative)
                file_map[identity] = relative
                exported[asset_type] += 1
            else:
                export_errors.append({
                    "asset": identity,
                    "error": "; ".join(descriptor["errors"]) or "No payload",
                })
            asset_files[identity] = descriptor

        manifest = scene.to_dict()
        manifest["files"] = file_map
        manifest["asset_files"] = asset_files
        manifest["dependency_graph"] = {
            identity: [
                {
                    "field": reference.field,
                    "target": reference.target,
                    "target_type": reference.target_type,
                }
                for reference in scene.references
                if reference.source == identity
            ]
            for identity in scene.assets
        }
        manifest["unresolved_dependencies"] = [
            {
                "source": reference.source,
                "field": reference.field,
                "target": reference.target,
                "target_type": reference.target_type,
            }
            for reference in scene.references
            if reference.target not in scene.assets
        ]
        manifest["export_summary"] = {
            "asset_count": len(scene.assets),
            "mapped_asset_count": len(file_map) - 1,
            "reference_count": len(scene.references),
            "errors": export_errors,
        }
        manifest["format"] = {
            "name": "AOV Effect Package",
            "extension": ".effect",
            "scene": "binary-fbx-7400",
            "target_blender": ">=4.5",
            "self_contained": True,
            "mapping": "asset_files + dependency_graph + references",
        }
        integrity = {}
        for directory, _folders, filenames in os.walk(temp_dir):
            for filename in filenames:
                path = os.path.join(directory, filename)
                relative = os.path.relpath(path, temp_dir).replace("\\", "/")
                integrity[relative] = _package_file_info(temp_dir, relative)
        manifest["integrity"] = integrity
        _write_json(os.path.join(temp_dir, "effect.json"), manifest)

        with zipfile.ZipFile(
            output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for directory, _folders, files in os.walk(temp_dir):
                for filename in files:
                    path = os.path.join(directory, filename)
                    archive.write(path, os.path.relpath(path, temp_dir).replace("\\", "/"))

    return {
        "path": output_path,
        "root": root.name,
        "nodes": len(scene.nodes),
        "animations": len(scene.animations),
        "duration": scene.duration,
        "exported": dict(exported),
        "assets": len(scene.assets),
        "references": len(scene.references),
    }


def inspect_effect_package(path: str) -> dict:
    with zipfile.ZipFile(path, "r") as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("effect.json").decode("utf-8"))
    return {
        "path": os.path.abspath(path),
        "entries": len(names),
        "files": names,
        "manifest": manifest,
    }


def export_effect_directory(
    index: EffectProjectIndex, root_or_id, selected_directory: str,
) -> dict:
    """Export a browsable ``effect_N`` folder plus its portable archive.

    The extracted directory and ``effect_N.effect`` are created from the exact
    same archive, so their manifests and payload hashes can never diverge.
    Existing exports are preserved by choosing the next available number.
    """
    selected_directory = os.path.abspath(selected_directory)
    os.makedirs(selected_directory, exist_ok=True)
    sequence = 0
    while True:
        folder_name = f"effect_{sequence}"
        output_directory = os.path.join(selected_directory, folder_name)
        if not os.path.exists(output_directory):
            break
        sequence += 1
    os.makedirs(output_directory)
    archive_path = os.path.join(output_directory, f"{folder_name}.effect")
    info = export_effect_package(index, root_or_id, archive_path)
    with zipfile.ZipFile(archive_path, "r") as archive:
        archive.extractall(output_directory)
    info.update({
        "path": output_directory,
        "directory": output_directory,
        "archive": archive_path,
        "folder_name": folder_name,
    })
    return info


def _curve_scalar(curve, default=0.0) -> float:
    if not isinstance(curve, dict):
        return float(default)
    try:
        return float(curve.get("scalar", default))
    except (TypeError, ValueError):
        return float(default)


def _color_tuple(value, default=(1.0, 1.0, 1.0, 1.0)):
    if not isinstance(value, dict):
        return tuple(float(item) for item in default)
    return tuple(float(value.get(key, default[index])) for index, key in enumerate(
        ("r", "g", "b", "a")
    ))


def _material_properties(index: EffectProjectIndex, source_obj, source_file_index, pointer):
    target_file_index, material = index.resolve_pptr(source_obj, source_file_index, pointer)
    if material is None or material.type.name != "Material":
        return None
    try:
        tree = index.tree(target_file_index, int(material.path_id))
    except Exception:
        tree = {}
    properties = tree.get("m_SavedProperties", {})
    colors = dict(properties.get("m_Colors", []))
    floats = dict(properties.get("m_Floats", []))
    texture_envs = dict(properties.get("m_TexEnvs", []))
    texture_name = next((
        name for name in ("_MainTex", "_BaseMap", "_BaseColorMap")
        if name in texture_envs
    ), next(iter(texture_envs), None))
    texture_obj = None
    texture_file_index = None
    texture_scale = (1.0, 1.0)
    texture_offset = (0.0, 0.0)
    if texture_name is not None:
        environment = texture_envs[texture_name]
        texture_file_index, texture_obj = index.resolve_pptr(
            material, target_file_index, environment.get("m_Texture", {})
        )
        scale = environment.get("m_Scale", {})
        offset = environment.get("m_Offset", {})
        texture_scale = (float(scale.get("x", 1.0)), float(scale.get("y", 1.0)))
        texture_offset = (float(offset.get("x", 0.0)), float(offset.get("y", 0.0)))
    color = next((
        _color_tuple(colors[name])
        for name in ("_TintColor", "_Color", "_MainTexColor", "_BaseColor")
        if name in colors
    ), (1.0, 1.0, 1.0, 1.0))
    return {
        "id": asset_id(target_file_index, int(material.path_id)),
        "file_index": target_file_index,
        "object": material,
        "tree": tree,
        "name": index.object_name(target_file_index, material),
        "texture_file_index": texture_file_index,
        "texture_object": texture_obj,
        "texture_scale": texture_scale,
        "texture_offset": texture_offset,
        "color": color,
        "floats": floats,
        "render_queue": int(tree.get("m_CustomRenderQueue", 3000)),
    }


def _mesh_geometry_with_uv(mesh):
    import numpy as np
    from AssetbundleUtils.AnimationPipeline import _preview_mesh_geometry
    vertices, indices = _preview_mesh_geometry(mesh)
    vertex_count = len(vertices)
    source_uv = np.asarray(getattr(mesh, "m_UV0", []), dtype=np.float32)
    if source_uv.size >= vertex_count * 2:
        uv = source_uv.reshape(vertex_count, -1)[:, :2].copy()
    else:
        uv = np.zeros((vertex_count, 2), dtype=np.float32)
    source_colors = np.asarray(getattr(mesh, "m_Colors", []), dtype=np.float32)
    if source_colors.size >= vertex_count * 4:
        colors = source_colors.reshape(vertex_count, -1)[:, :4].copy()
    else:
        colors = np.ones((vertex_count, 4), dtype=np.float32)
    return vertices, indices, uv, colors


def _build_texture_atlas(materials: Dict[str, dict], cell_size=256):
    from io import BytesIO
    import math
    from PIL import Image

    texture_images = {"__white__": Image.new("RGBA", (4, 4), (255, 255, 255, 255))}
    material_texture_keys = {}
    for material_id, material in materials.items():
        texture = material.get("texture_object")
        if texture is None:
            material_texture_keys[material_id] = "__white__"
            continue
        key = asset_id(material["texture_file_index"], int(texture.path_id))
        material_texture_keys[material_id] = key
        if key in texture_images:
            continue
        try:
            texture_images[key] = texture.read(False).image.convert("RGBA")
        except Exception:
            texture_images[key] = texture_images["__white__"].copy()

    keys = list(texture_images)
    columns = max(1, int(math.ceil(math.sqrt(len(keys)))))
    rows = int(math.ceil(len(keys) / columns))
    atlas = Image.new("RGBA", (columns * cell_size, rows * cell_size), (0, 0, 0, 0))
    rectangles = {}
    for index_value, key in enumerate(keys):
        image = texture_images[key].copy()
        image.thumbnail((cell_size - 4, cell_size - 4), Image.Resampling.LANCZOS)
        column, row = index_value % columns, index_value // columns
        x = column * cell_size + (cell_size - image.width) // 2
        y = row * cell_size + (cell_size - image.height) // 2
        atlas.alpha_composite(image, (x, y))
        rectangles[key] = (
            x / atlas.width,
            1.0 - (y + image.height) / atlas.height,
            (x + image.width) / atlas.width,
            1.0 - y / atlas.height,
        )
    buffer = BytesIO()
    atlas.save(buffer, format="PNG", optimize=False)
    return buffer.getvalue(), {
        material_id: rectangles[key]
        for material_id, key in material_texture_keys.items()
    }


def _map_uv(values, rectangle, scale=(1.0, 1.0), offset=(0.0, 0.0)):
    import numpy as np
    uv = np.asarray(values, dtype=np.float32).copy()
    uv[:, 0] = uv[:, 0] * float(scale[0]) + float(offset[0])
    uv[:, 1] = uv[:, 1] * float(scale[1]) + float(offset[1])
    uv -= np.floor(uv)
    left, bottom, right, top = rectangle
    uv[:, 0] = left + uv[:, 0] * (right - left)
    uv[:, 1] = bottom + uv[:, 1] * (top - bottom)
    return uv


def _particle_births(system_tree: dict, preview_duration: float, seed: int):
    import random
    randomizer = random.Random(int(seed) & 0xFFFFFFFF)
    duration = max(1e-3, float(system_tree.get("lengthInSec", 1.0)))
    delay = max(0.0, _curve_scalar(system_tree.get("startDelay"), 0.0))
    looping = bool(system_tree.get("looping", False))
    initial = system_tree.get("InitialModule", {})
    emission = system_tree.get("EmissionModule", {})
    lifetime = max(1e-3, _curve_scalar(initial.get("startLifetime"), 1.0))
    speed = _curve_scalar(initial.get("startSpeed"), 0.0)
    size = max(1e-3, _curve_scalar(initial.get("startSize"), 1.0))
    max_particles = max(1, min(160, int(initial.get("maxNumParticles", 64))))
    rate = max(0.0, _curve_scalar(emission.get("rateOverTime"), 0.0))
    cycles = max(1, int(preview_duration / duration) + 2) if looping else 1
    birth_times = []
    for cycle in range(cycles):
        origin = delay + cycle * duration
        if rate > 1e-5:
            count = min(max_particles, int(duration * rate) + 1)
            birth_times.extend(origin + index / rate for index in range(count))
        for burst in emission.get("m_Bursts", [])[:16]:
            count = max(0, int(round(_curve_scalar(burst.get("countCurve"), 0.0))))
            count = min(count, max_particles)
            burst_time = origin + float(burst.get("time", 0.0))
            birth_times.extend(burst_time for _ in range(count))
    if not birth_times and bool(system_tree.get("playOnAwake", True)):
        birth_times.append(delay)
    birth_times = sorted(time for time in birth_times if time <= preview_duration + lifetime)
    shape = system_tree.get("ShapeModule", {})
    shape_enabled = bool(shape.get("enabled", False))
    radius_value = shape.get("radius", {})
    radius = float(radius_value.get("value", 1.0)) if isinstance(radius_value, dict) else 1.0
    box = shape.get("m_Scale", {})
    particles = []
    for index_value, birth in enumerate(birth_times[:max_particles * max(1, cycles)]):
        if shape_enabled:
            shape_type = int(shape.get("type", 0))
            if shape_type in (5, 6, 7, 8, 13):
                position = (
                    (randomizer.random() - 0.5) * float(box.get("x", 1.0)),
                    (randomizer.random() - 0.5) * float(box.get("y", 1.0)),
                    (randomizer.random() - 0.5) * float(box.get("z", 1.0)),
                )
            else:
                theta = randomizer.random() * 6.283185307
                radial = radius * (randomizer.random() ** 0.5)
                position = (radial * __import__("math").cos(theta), 0.0,
                            radial * __import__("math").sin(theta))
        else:
            position = (0.0, 0.0, 0.0)
        direction = (
            (randomizer.random() - 0.5) * 0.35,
            (randomizer.random() - 0.5) * 0.35,
            1.0,
        )
        particles.append({
            "birth": float(birth), "lifetime": lifetime,
            "speed": speed, "size": size, "position": position,
            "direction": direction, "phase": randomizer.random() * 6.283185307,
            "index": index_value,
        })
    return particles


def _extract_effect_scalar_tracks(project, file_index, path_id, model):
    """Decode non-Transform clip bindings needed by the realtime effect view."""
    from AssetbundleUtils.AnimationPipeline import (
        StreamedClip, _clip_has_data, _decompress_self_bindings,
        _dense_sample_values,
    )

    tree = project.tree(file_index, path_id)
    use_self = bool(
        int(tree.get("m_SelfClipSize", 0)) and _clip_has_data(tree["m_SelfClip"])
    )
    clip = tree["m_SelfClip"] if use_self else tree["m_MuscleClip"]
    data = clip["m_Clip"]["data"]
    bindings = (
        _decompress_self_bindings(tree)
        if use_self else tree["m_ClipBindingConstant"]["genericBindings"]
    )
    hash_map = project.model_hash_map(model)
    root_path = project.transforms[model.file_index, model.transform_id].full_path
    relative_paths = defaultdict(list)
    for transform_id in model.transform_ids:
        full_path = project.transforms[model.file_index, transform_id].full_path
        relative_paths[full_path[len(root_path):].lstrip("/")].append(transform_id)
    ranges = []
    cursor = 0
    for binding in bindings:
        type_id = int(binding.get("typeID", binding.get("classID", 0)))
        attribute = int(binding.get("attribute", -1))
        is_transform = (
            type_id == 0xFFFFFFFF if use_self else type_id == 4
        )
        count = 4 if is_transform and attribute == 2 else (
            3 if is_transform and attribute in (1, 3, 4) else 1
        )
        candidates = (
            relative_paths.get(str(binding.get("path", "")), [])
            if use_self else hash_map.get(int(binding.get("path", 0)), [])
        )
        transform_id = candidates[0] if candidates else None
        ranges.append((cursor, cursor + count, transform_id, type_id, attribute,
                       int(binding.get("customType", 0)), is_transform))
        cursor += count
    result = defaultdict(list)

    def append_values(time_value, values):
        for start, end, transform_id, type_id, attribute, custom, is_transform in ranges:
            if is_transform or transform_id is None or end - start != 1 or start not in values:
                continue
            result[transform_id, type_id, attribute, custom].append(
                (float(time_value), float(values[start]))
            )

    stream = data["m_StreamedClip"]
    if stream["data"]:
        unpacker = object.__new__(StreamedClip)
        unpacker.data = stream["data"]
        unpacker.curveCount = int(stream["curveCount"])
        for frame in unpacker.ReadData()[1:-1]:
            append_values(float(frame.time), {
                int(key.index): float(key.value) for key in frame.keyList
            })
    dense = data["m_DenseClip"]
    dense_values = _dense_sample_values(dense)
    frame_count = int(dense["m_FrameCount"])
    curve_count = int(dense["m_CurveCount"])
    stream_count = int(stream["curveCount"])
    if frame_count and curve_count and len(dense_values) >= frame_count * curve_count:
        for frame_index in range(frame_count):
            offset = frame_index * curve_count
            append_values(
                float(dense["m_BeginTime"]) + frame_index / float(dense["m_SampleRate"]),
                {stream_count + i: float(dense_values[offset + i]) for i in range(curve_count)},
            )
    constant = data["m_ConstantClip"]["data"]
    if constant:
        base = stream_count + curve_count
        values = {base + i: float(value) for i, value in enumerate(constant)}
        append_values(0.0, values)
        append_values(max(0.0, float(clip["m_StopTime"]) - float(clip["m_StartTime"])), values)
    for keys in result.values():
        keys.sort(key=lambda item: item[0])
    return result


def _sample_scalar_keys(keys, time_value, fallback):
    if not keys:
        return float(fallback)
    if time_value <= keys[0][0]:
        return float(keys[0][1])
    if time_value >= keys[-1][0]:
        return float(keys[-1][1])
    for index_value in range(1, len(keys)):
        right_time, right_value = keys[index_value]
        if time_value <= right_time:
            left_time, left_value = keys[index_value - 1]
            span = right_time - left_time
            amount = 0.0 if abs(span) < 1e-8 else (time_value - left_time) / span
            return float(left_value + (right_value - left_value) * amount)
    return float(keys[-1][1])


def build_effect_preview_payload(
    index: EffectProjectIndex, root_or_id, max_frames: int = 150,
    frames_per_second: float = 30.0, animation_id: Optional[str] = None,
    cancel_check=None,
) -> dict:
    """Pre-sample one mapped composition, including its skinned character."""
    import math
    import numpy as np
    from AssetbundleUtils.AnimationPipeline import (
        _matrix_from_trs, _matrix_identity, _matrix_inverse, _matrix_multiply,
        _sample_quaternion, _sample_vector, _unity_matrix, unity_crc32,
    )

    root = root_or_id if isinstance(root_or_id, EffectRoot) else index.root(root_or_id)
    scene = index.build_scene(root)
    project = index.project
    model = project._make_model_candidate(root.file_index, root.transform_id)
    transform_order = index._subtree_transform_ids(root.file_index, root.transform_id)
    transform_set = set(transform_order)
    tracks = {}
    scalar_tracks = {}
    selected_animations = [
        animation for animation in scene.animations
        if animation_id is None or animation.id == str(animation_id)
    ]
    for animation in selected_animations:
        match = re.fullmatch(r"f(-?\d+):p(-?\d+)", animation.id)
        if match is None:
            continue
        try:
            take = project.extract_animation(
                int(match.group(1)), int(match.group(2)), model
            )
        except Exception:
            continue
        for transform_id, track in take.tracks.items():
            tracks[transform_id] = track
        try:
            scalar_tracks.update(_extract_effect_scalar_tracks(
                project, int(match.group(1)), int(match.group(2)), model
            ))
        except Exception:
            pass

    component_by_game_object = defaultdict(dict)
    initial_active = {}
    rest_globals = {}
    for transform_id in transform_order:
        record = project.transforms[root.file_index, transform_id]
        rest_local = _matrix_from_trs(
            record.position, record.rotation, record.scale
        )
        rest_globals[transform_id] = _matrix_multiply(
            rest_globals.get(record.parent_id, _matrix_identity()), rest_local
        )
        game_object = project.game_objects[(root.file_index, record.game_object_id)]
        initial_active[transform_id] = bool(getattr(game_object, "m_IsActive", True))
        for pointer in game_object.m_Components:
            component = index.object(root.file_index, int(pointer.path_id))
            if component is not None:
                component_by_game_object[record.game_object_id][component.type.name] = component

    materials = {}
    static_parts = []
    skinned_parts = []
    particle_parts = []
    for transform_id in transform_order:
        record = project.transforms[root.file_index, transform_id]
        components = component_by_game_object[record.game_object_id]
        renderer = components.get("MeshRenderer")
        mesh_filter = components.get("MeshFilter")
        if renderer is not None and mesh_filter is not None:
            renderer_tree = index.tree(root.file_index, int(renderer.path_id))
            filter_tree = index.tree(root.file_index, int(mesh_filter.path_id))
            _mesh_file_index, mesh_obj = index.resolve_pptr(
                mesh_filter, root.file_index, filter_tree.get("m_Mesh", {})
            )
            material = None
            pointers = renderer_tree.get("m_Materials", [])
            if pointers:
                material = _material_properties(
                    index, renderer, root.file_index, pointers[0]
                )
            if mesh_obj is not None:
                try:
                    geometry = _mesh_geometry_with_uv(mesh_obj.read())
                    material_id = material["id"] if material else "__default__"
                    if material:
                        materials[material_id] = material
                    static_parts.append({
                        "transform_id": transform_id, "geometry": geometry,
                        "material_id": material_id,
                        "renderer_type_id": int(renderer.type.value),
                    })
                except Exception:
                    pass

        skinned_renderer = components.get("SkinnedMeshRenderer")
        if skinned_renderer is not None:
            renderer_tree = index.tree(
                root.file_index, int(skinned_renderer.path_id)
            )
            _mesh_file_index, mesh_obj = index.resolve_pptr(
                skinned_renderer, root.file_index,
                renderer_tree.get("m_Mesh", {}),
            )
            pointers = renderer_tree.get("m_Materials", [])
            material = _material_properties(
                index, skinned_renderer, root.file_index, pointers[0]
            ) if pointers else None
            material_id = material["id"] if material else "__default__"
            if material:
                materials[material_id] = material
            if mesh_obj is not None:
                try:
                    mesh = mesh_obj.read()
                    vertices, indices, uv, colors = _mesh_geometry_with_uv(mesh)
                    vertex_count = len(vertices)
                    resolved_bones = []
                    for pointer in renderer_tree.get("m_Bones", []):
                        bone_file_index, bone_obj = index.resolve_pptr(
                            skinned_renderer, root.file_index, pointer
                        )
                        bone_id = None
                        if bone_obj is not None and bone_obj.type.name in (
                            "Transform", "RectTransform",
                        ):
                            if bone_file_index == root.file_index:
                                bone_id = int(bone_obj.path_id)
                            else:
                                external_record = project.transforms.get((
                                    bone_file_index, int(bone_obj.path_id)
                                ))
                                if external_record is not None:
                                    candidates = [
                                        transform_id for transform_id in transform_order
                                        if project.transforms[
                                            root.file_index, transform_id
                                        ].name == external_record.name
                                    ]
                                    if len(candidates) == 1:
                                        bone_id = candidates[0]
                        if bone_id is not None:
                            resolved_bones.append(bone_id)
                    skin = list(getattr(mesh, "m_Skin", []))
                    bind_poses = list(getattr(mesh, "m_BindPose", []))
                    if len(skin) != vertex_count or not resolved_bones:
                        bone_indices = np.zeros((vertex_count, 1), dtype=np.int32)
                        bone_weights = np.ones((vertex_count, 1), dtype=np.float64)
                        resolved_bones = [transform_id]
                        bind_matrices = [np.asarray(
                            _matrix_identity(), dtype=np.float64
                        )]
                    else:
                        influence_count = max(
                            1, max(len(item.weight) for item in skin)
                        )
                        bone_indices = np.zeros(
                            (vertex_count, influence_count), dtype=np.int32
                        )
                        bone_weights = np.zeros(
                            (vertex_count, influence_count), dtype=np.float64
                        )
                        for vertex_index, item in enumerate(skin):
                            for slot, (bone_index, weight) in enumerate(zip(
                                item.boneIndex, item.weight
                            )):
                                if slot >= influence_count:
                                    break
                                bone_indices[vertex_index, slot] = int(bone_index)
                                bone_weights[vertex_index, slot] = max(
                                    0.0, float(weight)
                                )
                        totals = bone_weights.sum(axis=1)
                        valid = totals > 1e-8
                        bone_weights[valid] /= totals[valid, None]
                        bind_matrices = []
                        mesh_world = rest_globals.get(
                            transform_id, _matrix_identity()
                        )
                        for bone_index, bone_transform_id in enumerate(
                            resolved_bones
                        ):
                            if bone_index < len(bind_poses):
                                bind_matrix = _unity_matrix(
                                    bind_poses[bone_index]
                                )
                            else:
                                bind_matrix = _matrix_multiply(
                                    _matrix_inverse(rest_globals.get(
                                        bone_transform_id, _matrix_identity()
                                    )),
                                    mesh_world,
                                )
                            bind_matrices.append(np.asarray(
                                bind_matrix, dtype=np.float64
                            ))
                    skinned_parts.append({
                        "transform_id": transform_id,
                        "renderer_type_id": int(skinned_renderer.type.value),
                        "vertices": vertices,
                        "indices": indices,
                        "uv": uv,
                        "colors": colors,
                        "material_id": material_id,
                        "bones": resolved_bones,
                        "bone_indices": bone_indices,
                        "bone_weights": bone_weights,
                        "bind_matrices": bind_matrices,
                    })
                except Exception:
                    pass

        particle = components.get("ParticleSystem")
        particle_renderer = components.get("ParticleSystemRenderer")
        if particle is None or particle_renderer is None:
            continue
        try:
            system_tree = index.tree(
                root.file_index, int(particle.path_id)
            )
        except Exception as exc:
            system_tree = {
                "__decode_issue__": str(exc),
                "m_Enabled": True,
            }
        try:
            renderer_tree = index.tree(
                root.file_index, int(particle_renderer.path_id)
            )
        except Exception as exc:
            renderer_tree = {
                "__decode_issue__": str(exc),
                "m_Enabled": True,
                "m_Materials": _raw_local_asset_pointers(
                    index,
                    root.file_index,
                    particle_renderer,
                    {"Material"},
                ),
            }
        pointers = renderer_tree.get("m_Materials", [])
        material = _material_properties(
            index, particle_renderer, root.file_index, pointers[0]
        ) if pointers else None
        material_id = material["id"] if material else "__default__"
        if material:
            materials[material_id] = material
        mesh_geometry = None
        if int(renderer_tree.get("m_RenderMode", 0)) == 4:
            _mesh_file_index, mesh_obj = index.resolve_pptr(
                particle_renderer, root.file_index, renderer_tree.get("m_Mesh", {})
            )
            if mesh_obj is not None:
                try:
                    mesh_geometry = _mesh_geometry_with_uv(mesh_obj.read())
                except Exception:
                    mesh_geometry = None
        particle_parts.append({
            "transform_id": transform_id,
            "system_id": int(particle.path_id),
            "system": system_tree,
            "renderer": renderer_tree,
            "material_id": material_id,
            "mesh_geometry": mesh_geometry,
            "renderer_type_id": int(particle_renderer.type.value),
        })

    if "__default__" not in materials:
        materials["__default__"] = {
            "id": "__default__", "texture_object": None,
            "texture_scale": (1.0, 1.0), "texture_offset": (0.0, 0.0),
            "color": (1.0, 1.0, 1.0, 1.0), "render_queue": 3000,
        }
    atlas_bytes, atlas_rectangles = _build_texture_atlas(materials)

    selected_duration = max(
        [float(animation.duration) for animation in selected_animations]
        + [float(scene.duration) if animation_id is None else 0.0]
    )
    duration = max(
        1.0 / frames_per_second, min(selected_duration or float(scene.duration), 10.0)
    )
    requested = max(2, int(round(duration * frames_per_second)) + 1)
    frame_count = min(max(2, int(max_frames)), requested)
    times = np.linspace(0.0, duration, frame_count)
    actual_fps = (frame_count - 1) / duration
    active_attribute = unity_crc32("m_IsActive")
    enabled_attribute = unity_crc32("m_Enabled")
    color_hashes = {
        unity_crc32(name) & 0x0FFFFFFF
        for name in ("_Color", "_TintColor", "_MainTexColor", "_BaseColor")
    }

    def activation_starts(transform_id):
        """Return times when the complete active hierarchy becomes enabled."""
        chain = []
        current = transform_id
        while (root.file_index, current) in project.transforms:
            chain.append(current)
            if current == root.transform_id:
                break
            current = project.transforms[root.file_index, current].parent_id
        event_times = {0.0}
        for current in chain:
            for key, keys in scalar_tracks.items():
                if (
                    key[0] == current and key[1] == 1
                    and key[2] == active_attribute and key[3] == 0
                ):
                    event_times.update(
                        max(0.0, min(duration, float(time_value)))
                        for time_value, _value in keys
                    )

        def active_at(time_value):
            for current in chain:
                fallback = 1.0 if initial_active.get(current, True) else 0.0
                keys = scalar_tracks.get(
                    (current, 1, active_attribute, 0), ()
                )
                if _sample_scalar_keys(keys, time_value, fallback) < 0.5:
                    return False
            return True

        starts = []
        previous = False
        for time_value in sorted(event_times):
            state = active_at(min(duration, time_value + 1e-6))
            if state and not previous:
                starts.append(float(time_value))
            previous = state
        return starts

    particle_birth_tables = {}
    for part in particle_parts:
        births = []
        for activation_time in activation_starts(part["transform_id"]):
            local_births = _particle_births(
                part["system"], max(0.0, duration - activation_time),
                part["system_id"] ^ int(round(activation_time * 1000.0)),
            )
            for particle in local_births:
                particle = dict(particle)
                particle["birth"] += activation_time
                births.append(particle)
        particle_birth_tables[part["system_id"]] = sorted(
            births, key=lambda particle: particle["birth"]
        )

    def sampled_scalar(transform_id, attribute, custom, time_value, fallback,
                       type_id=None):
        candidates = (
            ((transform_id, type_id, attribute, custom),)
            if type_id is not None else tuple(
                key for key in scalar_tracks
                if key[0] == transform_id and key[2] == attribute and key[3] == custom
            )
        )
        for key in candidates:
            if key in scalar_tracks:
                return _sample_scalar_keys(scalar_tracks[key], time_value, fallback)
        return float(fallback)

    def visible_at(transform_id, renderer_type_id, time_value):
        current = transform_id
        while (root.file_index, current) in project.transforms:
            active = sampled_scalar(
                current, active_attribute, 0, time_value,
                1.0 if initial_active.get(current, True) else 0.0,
            )
            if active < 0.5:
                return False
            current = project.transforms[root.file_index, current].parent_id
            if current == 0:
                break
        return sampled_scalar(
            transform_id, enabled_attribute, 0, time_value, 1.0,
            renderer_type_id,
        ) >= 0.5

    def animated_tint(transform_id, renderer_type_id, time_value, base):
        result = list(base)
        for key, keys in scalar_tracks.items():
            target, type_id, attribute, custom = key
            if target != transform_id or custom != 22 or type_id != renderer_type_id:
                continue
            component = (int(attribute) >> 28) - 4
            property_hash = int(attribute) & 0x0FFFFFFF
            if property_hash in color_hashes and 0 <= component < 4:
                result[component] = _sample_scalar_keys(keys, time_value, result[component])
        return tuple(result)

    frame_vertices = []
    frame_uvs = []
    frame_colors = []
    frame_indices = []
    vertex_counts = []
    index_counts = []
    particle_counts = []
    for time_value in times:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("preview_superseded")
        globals_at_time = {}
        for transform_id in transform_order:
            record = project.transforms[root.file_index, transform_id]
            track = tracks.get(transform_id)
            position = _sample_vector(
                track.translations if track else [], float(time_value), record.position
            )
            rotation = _sample_quaternion(
                track.rotations if track else [], float(time_value), record.rotation
            )
            scale = _sample_vector(
                track.scales if track else [], float(time_value), record.scale
            )
            local = _matrix_from_trs(position, rotation, scale)
            parent = globals_at_time.get(record.parent_id, _matrix_identity())
            globals_at_time[transform_id] = _matrix_multiply(parent, local)
        root_inverse = np.asarray(
            _matrix_inverse(globals_at_time[root.transform_id]), dtype=np.float64
        )
        vertices_chunks = []
        uv_chunks = []
        color_chunks = []
        index_chunks = []
        offset = 0

        def append_geometry(vertices, indices, uv, colors, material_id, world_matrix,
                            tint_override=None):
            nonlocal offset
            transformed = (root_inverse @ world_matrix @ vertices.T).T[:, :3]
            transformed[:, 0] *= -1.0
            material = materials[material_id]
            mapped_uv = _map_uv(
                uv, atlas_rectangles[material_id],
                material.get("texture_scale", (1.0, 1.0)),
                material.get("texture_offset", (0.0, 0.0)),
            )
            tint = np.asarray(
                tint_override if tint_override is not None
                else material.get("color", (1, 1, 1, 1)), dtype=np.float32
            )
            final_colors = np.clip(colors * tint[None, :], 0.0, 1.0)
            vertices_chunks.append(transformed.astype(np.float32, copy=False))
            uv_chunks.append(mapped_uv.astype(np.float32, copy=False))
            color_chunks.append(final_colors.astype(np.float32, copy=False))
            index_chunks.append(indices.astype(np.uint32, copy=False) + offset)
            offset += len(transformed)

        for part in static_parts:
            if not visible_at(
                part["transform_id"], part["renderer_type_id"], float(time_value)
            ):
                continue
            material = materials[part["material_id"]]
            tint = animated_tint(
                part["transform_id"], part["renderer_type_id"], float(time_value),
                material.get("color", (1, 1, 1, 1)),
            )
            append_geometry(
                *part["geometry"], part["material_id"],
                np.asarray(globals_at_time[part["transform_id"]], dtype=np.float64),
                tint,
            )

        for part in skinned_parts:
            if not visible_at(
                part["transform_id"], part["renderer_type_id"], float(time_value)
            ):
                continue
            source = part["vertices"]
            vertex_count = len(source)
            deformed = np.zeros((vertex_count, 4), dtype=np.float64)
            contributed = np.zeros(vertex_count, dtype=np.float64)
            for slot in range(part["bone_weights"].shape[1]):
                slot_weights = part["bone_weights"][:, slot]
                for bone_index in np.unique(part["bone_indices"][:, slot]):
                    bone_index = int(bone_index)
                    mask = (
                        (part["bone_indices"][:, slot] == bone_index)
                        & (slot_weights > 1e-8)
                    )
                    if (
                        not np.any(mask)
                        or bone_index < 0
                        or bone_index >= len(part["bones"])
                        or bone_index >= len(part["bind_matrices"])
                    ):
                        continue
                    bone_world = globals_at_time.get(part["bones"][bone_index])
                    if bone_world is None:
                        continue
                    skin_matrix = (
                        root_inverse @ np.asarray(bone_world, dtype=np.float64)
                        @ part["bind_matrices"][bone_index]
                    )
                    transformed = (skin_matrix @ source[mask].T).T
                    weights = slot_weights[mask, None]
                    deformed[mask] += transformed * weights
                    contributed[mask] += slot_weights[mask]
            missing = contributed <= 1e-8
            if np.any(missing):
                mesh_world = np.asarray(
                    globals_at_time.get(
                        part["transform_id"], _matrix_identity()
                    ), dtype=np.float64,
                )
                deformed[missing] = (
                    root_inverse @ mesh_world @ source[missing].T
                ).T
            material = materials[part["material_id"]]
            tint = animated_tint(
                part["transform_id"], part["renderer_type_id"],
                float(time_value), material.get("color", (1, 1, 1, 1)),
            )
            # Deformed vertices are already root-local; passing the root world
            # keeps append_geometry's common transform path at identity.
            append_geometry(
                deformed, part["indices"], part["uv"], part["colors"],
                part["material_id"],
                np.asarray(globals_at_time[root.transform_id], dtype=np.float64),
                tint,
            )

        visible_particles = 0
        for part in particle_parts:
            if not visible_at(
                part["transform_id"], part["renderer_type_id"], float(time_value)
            ):
                continue
            world = np.asarray(globals_at_time[part["transform_id"]], dtype=np.float64)
            initial = part["system"].get("InitialModule", {})
            start_color = _color_tuple(
                initial.get("startColor", {}).get("maxColor", {})
            )
            gravity = _curve_scalar(initial.get("gravityModifier"), 0.0) * -9.81
            for particle in particle_birth_tables[part["system_id"]]:
                age = float(time_value) - particle["birth"]
                if age < 0.0 or age > particle["lifetime"]:
                    continue
                visible_particles += 1
                position = np.asarray(particle["position"], dtype=np.float64)
                direction = np.asarray(particle["direction"], dtype=np.float64)
                position = position + direction * particle["speed"] * age
                position[1] += 0.5 * gravity * age * age
                fade = max(0.0, min(1.0, 1.0 - age / particle["lifetime"]))
                color = np.asarray(start_color, dtype=np.float32)
                color[3] *= fade
                material = materials[part["material_id"]]
                color *= np.asarray(animated_tint(
                    part["transform_id"], part["renderer_type_id"], float(time_value),
                    material.get("color", (1, 1, 1, 1)),
                ), dtype=np.float32)
                size = particle["size"] * max(0.05, fade)
                geometry = part["mesh_geometry"]
                if geometry is not None:
                    source, indices, uv, vertex_colors = geometry
                    local = np.asarray(source, dtype=np.float64).copy()
                    local[:, :3] *= size
                    local[:, :3] += position[None, :]
                    particle_colors = vertex_colors * color[None, :]
                    append_geometry(
                        local, indices, uv, particle_colors,
                        part["material_id"], world, (1.0, 1.0, 1.0, 1.0),
                    )
                else:
                    half = max(0.01, size * 0.5)
                    x, y, z = position
                    points = np.asarray((
                        (x-half, y-half, z, 1), (x+half, y-half, z, 1),
                        (x+half, y+half, z, 1), (x-half, y+half, z, 1),
                        (x, y-half, z-half, 1), (x, y-half, z+half, 1),
                        (x, y+half, z+half, 1), (x, y+half, z-half, 1),
                    ), dtype=np.float64)
                    indices = np.asarray((0, 2, 1, 0, 3, 2, 4, 6, 5, 4, 7, 6), dtype=np.uint32)
                    uv = np.asarray(((0,0),(1,0),(1,1),(0,1))*2, dtype=np.float32)
                    colors = np.tile(color[None, :], (8, 1))
                    append_geometry(
                        points, indices, uv, colors, part["material_id"], world,
                        (1.0, 1.0, 1.0, 1.0),
                    )

        if not vertices_chunks:
            vertices_chunks.append(np.zeros((3, 3), dtype=np.float32))
            uv_chunks.append(np.zeros((3, 2), dtype=np.float32))
            color_chunks.append(np.zeros((3, 4), dtype=np.float32))
            index_chunks.append(np.asarray((0, 1, 2), dtype=np.uint32))
        vertices = np.concatenate(vertices_chunks)
        uvs = np.concatenate(uv_chunks)
        colors = np.concatenate(color_chunks)
        indices = np.concatenate(index_chunks)
        frame_vertices.append(vertices.tobytes())
        frame_uvs.append(uvs.tobytes())
        frame_colors.append(colors.tobytes())
        frame_indices.append(indices.tobytes())
        vertex_counts.append(len(vertices))
        index_counts.append(len(indices))
        particle_counts.append(visible_particles)

    return {
        "version": 1,
        "kind": "effect",
        "frame_bytes": b"".join(frame_vertices),
        "uv_bytes": b"".join(frame_uvs),
        "color_bytes": b"".join(frame_colors),
        "index_bytes": b"".join(frame_indices),
        "frame_vertex_counts": tuple(vertex_counts),
        "frame_index_counts": tuple(index_counts),
        "frames_per_second": float(actual_fps),
        "duration": duration,
        "atlas_png": atlas_bytes,
        "metadata": {
            "name": scene.name,
            "root_id": root.id,
            "nodes": len(scene.nodes),
            "particle_systems": len(particle_parts),
            "static_meshes": len(static_parts),
            "skinned_meshes": len(skinned_parts),
            "animations": [item.name for item in selected_animations],
            "selected_animation_id": animation_id,
            "animated_properties": len(scalar_tracks),
            "materials": len(materials) - int("__default__" in materials),
            "textures": len(set(
                material.get("texture_object") for material in materials.values()
                if material.get("texture_object") is not None
            )),
            "particle_counts": tuple(particle_counts),
        },
    }
