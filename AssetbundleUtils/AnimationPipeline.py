# -*- coding: utf-8 -*-
"""Project-level Unity animation, skeleton, skin and FBX pipeline.

The Arena of Valor bundles used by this project split controllers, models,
clips and materials across sibling bundles.  This module deliberately indexes
the whole selected folder and uses Unity's embedded type trees for the modern
2022 layouts instead of the older hand-written class layouts.
"""

from __future__ import annotations

from copy import deepcopy
from ctypes import c_uint32
from dataclasses import dataclass, field
import math
import os
import zlib
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from AssetbundleUtils import UnityPy_AOV
from AssetbundleUtils.FbxBinary import write_binary_fbx
from AssetbundleUtils.UnityPy_AOV.classes.AnimationClip import StreamedClip
from AssetbundleUtils.UnityPy_AOV.helpers import TypeTreeHelper
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryWriter


BUNDLE_SUFFIXES = (".assetbundle", ".bundle", ".ab")
FBX_TICKS = 46_186_158_000


def is_bundle_path(path: str) -> bool:
    name = os.path.basename(path).lower()
    extension = os.path.splitext(name)[1]
    return name.endswith(BUNDLE_SUFFIXES) or (
        not extension and "assetbundle" in name
    )


def discover_project_paths(path: str) -> List[str]:
    if os.path.isfile(path):
        folder = os.path.dirname(os.path.abspath(path))
    else:
        folder = os.path.abspath(path)
    return sorted(
        (
            item.path
            for item in os.scandir(folder)
            if (
                item.is_file() and is_bundle_path(item.path)
                and not item.name.lower().startswith("_animation_import_roundtrip")
            )
        ),
        key=lambda value: os.path.basename(value).lower(),
    )


def unity_crc32(value: str) -> int:
    return zlib.crc32(value.encode("utf-8")) & 0xFFFFFFFF


def _iter_pptrs(value, path=""):
    """Yield every serialized PPtr without depending on effect modules."""
    if isinstance(value, dict):
        if "m_FileID" in value and "m_PathID" in value:
            yield path, value
            return
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _iter_pptrs(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_pptrs(child, f"{path}[{index}]")


def _vec3(value) -> Tuple[float, float, float]:
    if isinstance(value, dict):
        return tuple(float(value.get(axis, value.get(axis.upper()))) for axis in "xyz")
    return tuple(
        float(getattr(value, axis) if hasattr(value, axis) else getattr(value, axis.upper()))
        for axis in "xyz"
    )


def _quat(value) -> Tuple[float, float, float, float]:
    if isinstance(value, dict):
        return tuple(float(value.get(axis, value.get(axis.upper()))) for axis in "xyzw")
    return tuple(
        float(getattr(value, axis) if hasattr(value, axis) else getattr(value, axis.upper()))
        for axis in "xyzw"
    )


def quaternion_normalize(value: Sequence[float]) -> Tuple[float, float, float, float]:
    length = math.sqrt(sum(float(item) * float(item) for item in value))
    if length <= 1e-12:
        return 0.0, 0.0, 0.0, 1.0
    return tuple(float(item) / length for item in value)  # type: ignore[return-value]


def quaternion_to_euler_degrees(value: Sequence[float]) -> Tuple[float, float, float]:
    """Convert a quaternion to XYZ Euler degrees for FBX curves."""
    x, y, z, w = quaternion_normalize(value)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    rx = math.atan2(sinr_cosp, cosr_cosp)
    sinp = 2.0 * (w * y - z * x)
    ry = math.copysign(math.pi / 2.0, sinp) if abs(sinp) >= 1.0 else math.asin(sinp)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    rz = math.atan2(siny_cosp, cosy_cosp)
    return tuple(math.degrees(item) for item in (rx, ry, rz))


def euler_degrees_to_quaternion(value: Sequence[float]) -> Tuple[float, float, float, float]:
    x, y, z = (math.radians(float(item)) * 0.5 for item in value)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    return quaternion_normalize((
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    ))


def _clip_has_data(clip: dict) -> bool:
    data = clip["m_Clip"]["data"]
    return bool(
        data["m_StreamedClip"]["data"]
        or data["m_DenseClip"]["m_SampleArray"]
        or data["m_DenseClip"].get("m_SampleOptArray", [])
        or data["m_ConstantClip"]["data"]
    )


def _decompress_self_bindings(tree: dict) -> List[dict]:
    compressed = tree["m_CompressedBindings"]
    strings = compressed["strTable"]
    paths = []
    current = []
    for index in compressed["pathPartIndices"]:
        if int(index) == -1:
            paths.append("/".join(current))
            current = []
        else:
            current.append(str(strings[int(index)]))
    bindings = compressed["bindings"]
    if len(paths) != len(bindings):
        raise ValueError(
            f"Compressed binding path count mismatch: {len(paths)} != {len(bindings)}"
        )
    return [dict(binding, path=path) for path, binding in zip(paths, bindings)]


def _dense_sample_values(dense: dict) -> List[float]:
    direct = dense.get("m_SampleArray", [])
    if direct:
        return [float(value) for value in direct]
    packed = dense.get("m_SampleOptArray", [])
    infos = dense.get("m_SampleCurveInfoArray", [])
    if not packed or not infos:
        return []
    frame_count = int(dense["m_FrameCount"])
    curve_count = int(dense["m_CurveCount"])
    # Unity writes two different optimized layouts into m_SampleOptArray.
    # Older clips retain one UInt16 per scalar curve.  Newer AOV Unity 2022
    # clips group Vector3/quaternion curves and bit-pack every frame; the high
    # byte of m_CurveType is the quantization width and the low byte is the
    # value layout (1 = smallest-three quaternion, 2 = Vector3, 3 = scalar).
    scalar_word_layout = (
        len(packed) == frame_count * curve_count
        and all((int(info["m_CurveType"]) >> 8) == 0 for info in infos)
    )
    if scalar_word_layout:
        result = []
        cursor = 0
        for _frame in range(frame_count):
            for info in infos:
                layout_type = int(info["m_CurveType"]) & 0xFF
                component_count = 4 if layout_type == 1 else 3 if layout_type == 2 else 1
                for _component in range(component_count):
                    result.append(
                        float(int(packed[cursor]) & 0xFFFF)
                        * float(info["m_CurveScale"])
                        + float(info["m_CurveMinimum"])
                    )
                    cursor += 1
        if cursor != len(packed) or len(result) != frame_count * curve_count:
            raise ValueError(
                f"Dense scalar-word sample mismatch: {cursor}/{len(packed)}, "
                f"{len(result)}/{frame_count * curve_count}"
            )
        return result

    class _BitReader:
        def __init__(self, words):
            self.words = [int(value) & 0xFFFF for value in words]
            self.offset = 0

        def read(self, count: int) -> int:
            if count <= 0:
                raise ValueError(f"Invalid optimized dense bit width: {count}")
            if self.offset + count > len(self.words) * 16:
                raise ValueError("Dense optimized sample array ended unexpectedly")
            value = 0
            for bit in range(count):
                word = self.words[self.offset >> 4]
                value |= ((word >> (self.offset & 15)) & 1) << bit
                self.offset += 1
            return value

        def align_word(self):
            self.offset = (self.offset + 15) & ~15

    result = []
    reader = _BitReader(packed)
    for _frame in range(frame_count):
        frame_start = len(result)
        for info in infos:
            curve_type = int(info["m_CurveType"])
            bit_width = curve_type >> 8
            layout_type = curve_type & 0xFF
            minimum = float(info["m_CurveMinimum"])
            scale = float(info["m_CurveScale"])
            if layout_type == 1:
                # Unity stores the three smallest quaternion components,
                # followed by two bits for the omitted component and one sign
                # bit.  q and -q encode the same rotation, but retaining the
                # sign also preserves curve continuity before interpolation.
                components = [reader.read(bit_width) * scale + minimum for _ in range(3)]
                metadata = reader.read(3)
                omitted = metadata & 0x3
                if omitted > 3:
                    raise ValueError(f"Invalid optimized quaternion metadata: {metadata}")
                missing = math.sqrt(max(0.0, 1.0 - sum(value * value for value in components)))
                if metadata & 0x4:
                    missing = -missing
                quaternion = list(components)
                quaternion.insert(omitted, missing)
                result.extend(quaternion)
            elif layout_type == 2:
                result.extend(
                    reader.read(bit_width) * scale + minimum for _ in range(3)
                )
            elif layout_type == 3:
                result.append(reader.read(bit_width) * scale + minimum)
            else:
                raise ValueError(
                    f"Unsupported optimized dense curve layout: 0x{curve_type:04x}"
                )
        if len(result) - frame_start != curve_count:
            raise ValueError(
                f"Dense optimized frame width mismatch: "
                f"{len(result) - frame_start}/{curve_count}"
            )
        reader.align_word()
    consumed_words = reader.offset // 16
    if consumed_words != len(packed) or len(result) != frame_count * curve_count:
        raise ValueError(
            f"Dense optimized sample mismatch: {consumed_words}/{len(packed)}, "
            f"{len(result)}/{frame_count * curve_count}"
        )
    return result


def _matrix_identity() -> List[List[float]]:
    return [[1.0 if row == column else 0.0 for column in range(4)] for row in range(4)]


def _matrix_multiply(left, right) -> List[List[float]]:
    return [[
        sum(float(left[row][k]) * float(right[k][column]) for k in range(4))
        for column in range(4)
    ] for row in range(4)]


def _matrix_inverse(value) -> List[List[float]]:
    augmented = [
        [float(item) for item in value[row]] + _matrix_identity()[row]
        for row in range(4)
    ]
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= 1e-12:
            raise ValueError("Non-invertible transform matrix in skeleton")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [item / divisor for item in augmented[column]]
        for row in range(4):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                current - factor * source
                for current, source in zip(augmented[row], augmented[column])
            ]
    return [row[4:] for row in augmented]


def _matrix_from_trs(position, rotation, scale) -> List[List[float]]:
    x, y, z, w = quaternion_normalize(rotation)
    sx, sy, sz = scale
    matrix = [
        [(1 - 2 * (y * y + z * z)) * sx, (2 * (x * y - z * w)) * sy,
         (2 * (x * z + y * w)) * sz, float(position[0])],
        [(2 * (x * y + z * w)) * sx, (1 - 2 * (x * x + z * z)) * sy,
         (2 * (y * z - x * w)) * sz, float(position[1])],
        [(2 * (x * z - y * w)) * sx, (2 * (y * z + x * w)) * sy,
         (1 - 2 * (x * x + y * y)) * sz, float(position[2])],
        [0.0, 0.0, 0.0, 1.0],
    ]
    return matrix


def _matrix_to_fbx_array(value) -> Tuple[float, ...]:
    """FBX stores Matrix values column-major while our math is row-major."""
    return tuple(float(value[row][column]) for column in range(4) for row in range(4))


@dataclass
class TransformRecord:
    file_index: int
    path_id: int
    game_object_id: int
    name: str
    parent_id: int
    children: List[int]
    position: Tuple[float, float, float]
    rotation: Tuple[float, float, float, float]
    scale: Tuple[float, float, float]
    full_path: str = ""


@dataclass
class MeshSequenceRecord:
    """A field-compatible view of the game's MeshSequence MonoBehaviour."""

    component_id: int
    script_name: str
    mesh_pointers: List[dict]
    delays: List[float]
    material_pointers: List[dict]
    loop: bool
    default_delay: float
    submesh: int


@dataclass
class RigidAttachment:
    """A non-skinned MeshFilter/MeshRenderer pair under a model hierarchy."""

    file_index: int
    game_object_id: int
    transform_id: int
    name: str
    full_path: str
    mount_transform_id: int
    mount_name: str
    mesh_filter_id: int
    mesh_renderer_id: int
    mesh_pointer: dict
    mesh_name: str
    material_pointers: List[dict]
    material_names: List[str]
    enabled: bool
    kind: str
    sequence: Optional[MeshSequenceRecord] = None

    @property
    def sequence_frame_count(self) -> int:
        return len(self.sequence.mesh_pointers) if self.sequence is not None else 0


@dataclass
class ModelCandidate:
    file_index: int
    game_object_id: int
    transform_id: int
    name: str
    transform_ids: List[int]
    skinned_renderer_ids: List[int]
    mesh_renderer_ids: List[int]
    rigid_attachments: List[RigidAttachment] = field(default_factory=list)

    @property
    def sequence_attachment_count(self) -> int:
        return sum(item.sequence is not None for item in self.rigid_attachments)

    @property
    def label(self) -> str:
        return (
            f"{self.name} · {len(self.transform_ids)} nodes · "
            f"{len(self.skinned_renderer_ids)} skinned mesh(es)"
        )


@dataclass
class AnimationRecord:
    file_index: int
    path_id: int
    name: str
    sample_rate: float
    duration: float
    binding_count: int
    has_curves: bool


@dataclass
class AnimationTrack:
    transform_id: int
    path: str
    translations: List[Tuple[float, Tuple[float, float, float]]] = field(default_factory=list)
    rotations: List[Tuple[float, Tuple[float, float, float, float]]] = field(default_factory=list)
    scales: List[Tuple[float, Tuple[float, float, float]]] = field(default_factory=list)


@dataclass
class AnimationTake:
    name: str
    sample_rate: float
    duration: float
    tracks: Dict[int, AnimationTrack]
    mapped_bindings: int
    total_transform_bindings: int


class AnimationProjectIndex:
    """Cross-bundle index for one selected skin/project folder."""

    def __init__(self, paths: Sequence[str], environments: Optional[Sequence[object]] = None):
        self.paths = [os.path.abspath(path) for path in paths]
        self.environments = list(environments) if environments is not None else [
            UnityPy_AOV.load(path) for path in self.paths
        ]
        self.objects: List[Dict[int, object]] = [
            {int(obj.path_id): obj for obj in env.objects} for env in self.environments
        ]
        self.serialized_file_indexes: Dict[str, int] = {}
        for file_index, env in enumerate(self.environments):
            for obj in env.objects:
                name = getattr(obj.assets_file, "name", "")
                if name:
                    self.serialized_file_indexes[str(name).lower()] = file_index
        self.type_trees: Dict[Tuple[int, int], dict] = {}
        self.transforms: Dict[Tuple[int, int], TransformRecord] = {}
        self.transform_by_game_object: Dict[Tuple[int, int], int] = {}
        self.game_objects: Dict[Tuple[int, int], object] = {}
        self.models: List[ModelCandidate] = []
        self.animations: List[AnimationRecord] = []
        self.animation_model_matches: Dict[Tuple[int, int], Optional[ModelCandidate]] = {}
        self.animation_model_links = defaultdict(dict)
        self.decode_issues = []
        self.reference_graph = None
        self._build()
        from AssetbundleUtils.ReferenceGraph import (
            CrossBundleReferenceGraph, REFERENCE_SOURCE_TYPES,
        )
        self.reference_graph = CrossBundleReferenceGraph(self)
        self.reference_graph.expand_types(REFERENCE_SOURCE_TYPES)
        # Finish the graph for every remaining asset type.  Most are leaf
        # resources (Mesh/Texture/Shader), so this pass is inexpensive but
        # guarantees that reverse-reference queries are complete.
        self.reference_graph.expand_all()
        self._build_explicit_animation_links()

    @classmethod
    def from_path(cls, path: str):
        return cls(discover_project_paths(path))

    def object(self, file_index: int, path_id: int):
        return self.objects[file_index].get(int(path_id))

    def resolve_pptr(self, source_obj, source_file_index: int, pointer: dict):
        """Resolve a typetree PPtr across sibling bundles by CAB external name."""
        path_id = int(pointer.get("m_PathID", 0))
        file_id = int(pointer.get("m_FileID", 0))
        if path_id == 0:
            return None, None
        if file_id == 0:
            return source_file_index, self.object(source_file_index, path_id)
        externals = getattr(source_obj.assets_file, "externals", [])
        if file_id - 1 >= len(externals):
            return None, None
        external_name = str(externals[file_id - 1].name).lower()
        target_index = self.serialized_file_indexes.get(external_name)
        if target_index is None:
            return None, None
        return target_index, self.object(target_index, path_id)

    def tree(self, file_index: int, path_id: int) -> dict:
        key = int(file_index), int(path_id)
        if key not in self.type_trees:
            obj = self.object(*key)
            if obj is None:
                raise KeyError(f"Missing object {key}")
            try:
                self.type_trees[key] = obj.read_typetree()
            except Exception as exc:
                raise RuntimeError(
                    "Failed to decode "
                    f"{obj.type.name} PathID {int(obj.path_id)} in "
                    f"{self.paths[int(file_index)]}: {exc}"
                ) from exc
        return self.type_trees[key]

    def refresh_serialized_objects(self, keys: Sequence[Tuple[int, int]]) -> int:
        """Refresh graph edges and authoritative bindings after replacement.

        Import operations preserve object identity and therefore do not rebuild
        the catalog. They can, however, change PPtrs. Clearing the typetree cache
        before refreshing the graph prevents stale cross-bundle relationships.
        """
        normalized = list(dict.fromkeys(
            (int(key[0]), int(key[1])) for key in keys
        ))
        for key in normalized:
            self.type_trees.pop(key, None)
        if self.reference_graph is not None:
            self.reference_graph.refresh(normalized)
        self.animation_model_matches.clear()
        self.animation_model_links.clear()
        self._build_explicit_animation_links()
        return len(normalized)

    def _build(self):
        for file_index, obj_map in enumerate(self.objects):
            local_names: Dict[int, str] = {}
            local_transforms: Dict[int, object] = {}
            for path_id, obj in obj_map.items():
                if obj.type.name == "GameObject":
                    try:
                        game_object = obj.read()
                        self.game_objects[(file_index, path_id)] = game_object
                        local_names[path_id] = str(game_object.name)
                    except Exception:
                        pass
                elif obj.type.name == "Transform":
                    try:
                        local_transforms[path_id] = obj.read()
                    except Exception:
                        pass

            for path_id, transform in local_transforms.items():
                game_object_id = int(transform.m_GameObject.path_id)
                self.transforms[(file_index, path_id)] = TransformRecord(
                    file_index=file_index,
                    path_id=path_id,
                    game_object_id=game_object_id,
                    name=local_names.get(game_object_id, f"Transform_{path_id}"),
                    parent_id=int(transform.m_Father.path_id),
                    children=[int(child.path_id) for child in transform.m_Children],
                    position=_vec3(transform.m_LocalPosition),
                    rotation=_quat(transform.m_LocalRotation),
                    scale=_vec3(transform.m_LocalScale),
                )
                self.transform_by_game_object[(file_index, game_object_id)] = path_id

            cache: Dict[int, str] = {}
            def full_path(transform_id: int, visiting=None) -> str:
                if transform_id in cache:
                    return cache[transform_id]
                visiting = set() if visiting is None else visiting
                record = self.transforms[(file_index, transform_id)]
                if transform_id in visiting:
                    return record.name
                visiting.add(transform_id)
                parent_key = file_index, record.parent_id
                value = (
                    f"{full_path(record.parent_id, visiting)}/{record.name}"
                    if parent_key in self.transforms else record.name
                )
                cache[transform_id] = value
                return value

            for path_id in local_transforms:
                self.transforms[(file_index, path_id)].full_path = full_path(path_id)

            for path_id, transform in local_transforms.items():
                if (file_index, int(transform.m_Father.path_id)) not in self.transforms:
                    self.models.append(self._make_model_candidate(file_index, path_id))

            for path_id, obj in obj_map.items():
                if obj.type.name != "AnimationClip":
                    continue
                try:
                    tree = self.tree(file_index, path_id)
                    clip = (
                        tree["m_SelfClip"]
                        if int(tree.get("m_SelfClipSize", 0)) and _clip_has_data(tree["m_SelfClip"])
                        else tree["m_MuscleClip"]
                    )
                    data = clip["m_Clip"]["data"]
                    bindings = (
                        tree["m_CompressedBindings"]["bindings"]
                        if clip is tree["m_SelfClip"]
                        else tree["m_ClipBindingConstant"]["genericBindings"]
                    )
                    has_curves = bool(
                        data["m_StreamedClip"]["data"]
                        or data["m_DenseClip"]["m_SampleArray"]
                        or data["m_DenseClip"].get("m_SampleOptArray", [])
                        or data["m_ConstantClip"]["data"]
                    )
                    self.animations.append(AnimationRecord(
                        file_index, path_id, str(tree["m_Name"]),
                        float(tree["m_SampleRate"]),
                        max(0.0, float(clip["m_StopTime"]) - float(clip["m_StartTime"])),
                        len(bindings), has_curves,
                    ))
                except Exception:
                    pass

        self.models.sort(key=lambda item: (
            -len(item.skinned_renderer_ids), -len(item.transform_ids), item.name.lower(),
        ))
        self.animations.sort(key=lambda item: item.name.lower())

    def _models_containing_game_object(
        self, file_index: int, game_object_id: int
    ) -> List[ModelCandidate]:
        transform_id = self.transform_by_game_object.get(
            (int(file_index), int(game_object_id))
        )
        if transform_id is None:
            return []
        return [
            model for model in self.models
            if model.file_index == int(file_index)
            and transform_id in model.transform_ids
        ]

    def _register_animation_model_link(
        self, clip_key, model: ModelCandidate, priority: int, evidence: str,
        anchor_transform_id: Optional[int] = None,
    ):
        key = int(clip_key[0]), int(clip_key[1])
        model_key = int(model.file_index), int(model.game_object_id)
        entry = self.animation_model_links[key].get(model_key)
        if entry is None:
            self.animation_model_links[key][model_key] = {
                "model": model,
                "priority": int(priority),
                "evidence": {str(evidence)},
                "anchors": set(),
            }
            if anchor_transform_id is not None:
                self.animation_model_links[key][model_key]["anchors"].add(
                    int(anchor_transform_id)
                )
            return
        entry["priority"] = max(int(entry["priority"]), int(priority))
        entry["evidence"].add(str(evidence))
        if anchor_transform_id is not None:
            entry["anchors"].add(int(anchor_transform_id))

    def _controller_clip_keys(self, file_index: int, controller) -> List[Tuple[int, int]]:
        if self.reference_graph is not None:
            return [
                edge.target for edge in self.reference_graph.outgoing((
                    int(file_index), int(controller.path_id)
                ))
                if edge.target_type == "AnimationClip"
                and "m_AnimationClips" in edge.field
            ]
        try:
            tree = self.tree(file_index, int(controller.path_id))
        except Exception:
            return []
        result = []
        for pointer in tree.get("m_AnimationClips", []):
            target_file_index, target = self.resolve_pptr(
                controller, file_index, pointer
            )
            if target is not None and target.type.name == "AnimationClip":
                result.append((target_file_index, int(target.path_id)))
        return result

    def _component_owner(self, file_index: int, component):
        if self.reference_graph is not None:
            game_object_edge = next((
                edge for edge in self.reference_graph.outgoing((
                    int(file_index), int(component.path_id)
                ))
                if edge.target_type == "GameObject"
                and edge.field.endswith("m_GameObject")
            ), None)
            if game_object_edge is not None:
                return game_object_edge.target
        try:
            tree = self.tree(file_index, int(component.path_id))
        except Exception:
            return []
        game_object_file_index, game_object = self.resolve_pptr(
            component, file_index, tree.get("m_GameObject", {})
        )
        if game_object is None or game_object.type.name != "GameObject":
            return None
        return game_object_file_index, int(game_object.path_id)

    def _component_models(self, file_index: int, component) -> List[ModelCandidate]:
        owner = self._component_owner(file_index, component)
        return self._models_containing_game_object(*owner) if owner else []

    def _build_explicit_animation_links(self):
        """Index authoritative component/controller/script relationships.

        AOV folders frequently contain several visually similar LOD/presentation
        roots.  Curve-name scoring alone can select the wrong one, so explicit
        serialized references are recorded before any compatibility fallback.
        """
        for file_index, objects in enumerate(self.objects):
            for component in objects.values():
                component_type = component.type.name
                if component_type not in ("Animator", "Animation", "MonoBehaviour"):
                    continue
                models = self._component_models(file_index, component)
                if not models:
                    continue
                owner = self._component_owner(file_index, component)
                try:
                    tree = self.tree(file_index, int(component.path_id))
                except Exception:
                    continue
                links = []
                graph_edges = self.reference_graph.outgoing((
                    file_index, int(component.path_id)
                )) if self.reference_graph is not None else []
                if component_type == "Animator":
                    controller_edge = next((
                        edge for edge in graph_edges
                        if edge.target_type in (
                            "AnimatorController", "RuntimeAnimatorController",
                        ) and edge.field.endswith("m_Controller")
                    ), None)
                    target_file_index, controller = (
                        (controller_edge.target[0], self.object(*controller_edge.target))
                        if controller_edge is not None else (None, None)
                    )
                    if controller is not None and controller.type.name in (
                        "AnimatorController", "RuntimeAnimatorController",
                    ):
                        links.extend((
                            clip_key, 400,
                            f"Animator[{component.path_id}].m_Controller[{controller.path_id}]",
                        ) for clip_key in self._controller_clip_keys(
                            target_file_index, controller
                        ))
                elif component_type == "Animation":
                    for edge in graph_edges:
                        if edge.target_type == "AnimationClip" and (
                            "m_Animation" in edge.field
                            or "m_Animations" in edge.field
                        ):
                            links.append((
                                edge.target, 380,
                                f"Animation[{component.path_id}].{edge.field}",
                            ))
                else:
                    for edge in graph_edges:
                        field_path = edge.field
                        if field_path.endswith("m_GameObject") or field_path.endswith("m_Script"):
                            continue
                        target_file_index, target_path_id = edge.target
                        target = self.object(target_file_index, target_path_id)
                        if target is None:
                            continue
                        if target.type.name == "AnimationClip":
                            links.append((
                                edge.target, 320,
                                f"MonoBehaviour[{component.path_id}].{field_path}",
                            ))
                        elif target.type.name in (
                            "AnimatorController", "RuntimeAnimatorController",
                        ):
                            links.extend((
                                clip_key, 300,
                                f"MonoBehaviour[{component.path_id}].{field_path}",
                            ) for clip_key in self._controller_clip_keys(
                                target_file_index, target
                            ))
                for clip_key, priority, evidence in links:
                    for model in models:
                        anchor_transform_id = None
                        if owner and owner[0] == model.file_index:
                            candidate = self.transform_by_game_object.get(owner)
                            if candidate in model.transform_ids:
                                anchor_transform_id = candidate
                        self._register_animation_model_link(
                            clip_key, model, priority, evidence,
                            anchor_transform_id=anchor_transform_id,
                        )

    def explicit_models_for_animation(
        self, file_index: int, path_id: int, require_mesh: bool = True
    ) -> List[dict]:
        entries = list(self.animation_model_links.get(
            (int(file_index), int(path_id)), {}
        ).values())
        if require_mesh:
            entries = [
                entry for entry in entries
                if entry["model"].skinned_renderer_ids
            ]
        return sorted(entries, key=lambda entry: (
            -int(entry["priority"]),
            -len(entry["model"].skinned_renderer_ids),
            -len(entry["model"].rigid_attachments),
            -len(entry["model"].transform_ids),
            entry["model"].name.lower(),
        ))

    def _make_model_candidate(self, file_index: int, root_id: int) -> ModelCandidate:
        transform_ids = []
        stack = [root_id]
        while stack:
            path_id = stack.pop()
            key = file_index, path_id
            if key not in self.transforms or path_id in transform_ids:
                continue
            transform_ids.append(path_id)
            stack.extend(reversed(self.transforms[key].children))

        skinned = []
        regular = []
        attachments = []
        for transform_id in transform_ids:
            record = self.transforms[file_index, transform_id]
            game_object = self.game_objects.get((file_index, record.game_object_id))
            if game_object is None:
                continue
            by_type = defaultdict(list)
            for component in game_object.m_Components:
                by_type[component.type.name].append(int(component.path_id))
            skinned.extend(by_type.get("SkinnedMeshRenderer", []))

            mesh_filter_id = next(iter(by_type.get("MeshFilter", [])), 0)
            mesh_renderer_id = next(iter(by_type.get("MeshRenderer", [])), 0)
            if not mesh_renderer_id:
                continue
            try:
                renderer_tree = self.tree(file_index, mesh_renderer_id)
                filter_tree = (
                    self.tree(file_index, mesh_filter_id)
                    if mesh_filter_id else {}
                )
            except Exception as exc:
                self.decode_issues.append({
                    "file_index": int(file_index),
                    "game_object_path_id": int(record.game_object_id),
                    "mesh_renderer_path_id": int(mesh_renderer_id),
                    "mesh_filter_path_id": int(mesh_filter_id),
                    "error": str(exc),
                })
                continue
            regular.append(mesh_renderer_id)
            mesh_pointer = dict(
                filter_tree.get("m_Mesh") or renderer_tree.get("m_Mesh") or {}
            )
            sequence = self._mesh_sequence_for_components(
                file_index, by_type.get("MonoBehaviour", []),
                mesh_filter_id, mesh_renderer_id,
            )
            if int(mesh_pointer.get("m_PathID", 0)) == 0 and sequence is None:
                continue

            kind = self._attachment_kind(record.full_path)
            mount_id = self._attachment_mount(file_index, record, root_id, kind)
            mount_name = self.transforms[file_index, mount_id].name
            material_pointers = [
                dict(pointer) for pointer in renderer_tree.get("m_Materials", [])
                if isinstance(pointer, dict) and int(pointer.get("m_PathID", 0))
            ]
            source_component = mesh_filter_id or mesh_renderer_id
            attachments.append(RigidAttachment(
                file_index=file_index,
                game_object_id=record.game_object_id,
                transform_id=transform_id,
                name=record.name,
                full_path=record.full_path,
                mount_transform_id=mount_id,
                mount_name=mount_name,
                mesh_filter_id=mesh_filter_id,
                mesh_renderer_id=mesh_renderer_id,
                mesh_pointer=mesh_pointer,
                mesh_name=self._pointer_name(file_index, source_component, mesh_pointer),
                material_pointers=material_pointers,
                material_names=[
                    self._pointer_name(file_index, mesh_renderer_id, pointer)
                    for pointer in material_pointers
                ],
                enabled=bool(renderer_tree.get("m_Enabled", True)),
                kind=kind,
                sequence=sequence,
            ))
        root = self.transforms[file_index, root_id]
        return ModelCandidate(
            file_index=file_index,
            game_object_id=root.game_object_id,
            transform_id=root_id,
            name=root.name,
            transform_ids=transform_ids,
            skinned_renderer_ids=skinned,
            mesh_renderer_ids=regular,
            rigid_attachments=attachments,
        )

    @staticmethod
    def _attachment_kind(full_path: str) -> str:
        value = full_path.lower()
        categories = (
            ("weapon", ("weapon", "sword", "blade", "prop1", "prop2", "hand_r")),
            ("wing", ("wing", "cape", "tail")),
            ("head", ("head", "hair", "hat", "helmet")),
            ("effect", ("effect", "ef_", "fx_")),
        )
        for kind, tokens in categories:
            if any(token in value for token in tokens):
                return kind
        return "attachment"

    def _attachment_mount(
        self, file_index: int, record: TransformRecord, root_id: int, kind: str
    ) -> int:
        """Find the semantic socket while preserving the exact Transform chain."""
        fallback = (
            record.parent_id
            if (file_index, record.parent_id) in self.transforms else root_id
        )
        tokens = {
            "weapon": ("prop", "hand", "socket", "mount"),
            "wing": ("wing", "spine", "chest", "back"),
            "head": ("head", "neck"),
            "effect": ("prop", "hand", "head", "spine", "root"),
            "attachment": ("prop", "socket", "mount"),
        }.get(kind, ())
        current_id = fallback
        visited = set()
        while (file_index, current_id) in self.transforms and current_id not in visited:
            visited.add(current_id)
            current = self.transforms[file_index, current_id]
            if any(token in current.name.lower() for token in tokens):
                return current_id
            if current_id == root_id:
                break
            current_id = current.parent_id
        return fallback

    def _pointer_name(
        self, source_file_index: int, source_component_id: int, pointer: dict
    ) -> str:
        if not pointer or not source_component_id:
            return ""
        source = self.object(source_file_index, source_component_id)
        if source is None:
            return ""
        _target_index, target = self.resolve_pptr(source, source_file_index, pointer)
        if target is None:
            return ""
        try:
            return str(target.peek_name(f"{target.type.name}_{target.path_id}"))
        except Exception:
            try:
                return str(target.read().name)
            except Exception:
                return f"{target.type.name}_{target.path_id}"

    def _mesh_sequence_for_components(
        self, file_index: int, mono_ids: Sequence[int],
        mesh_filter_id: int, mesh_renderer_id: int,
    ) -> Optional[MeshSequenceRecord]:
        for component_id in mono_ids:
            try:
                tree = self.tree(file_index, component_id)
            except Exception:
                continue
            mesh_pointers = tree.get("meshes")
            if not isinstance(mesh_pointers, list) or not mesh_pointers:
                continue
            filter_ref = int(tree.get("meshFilter", {}).get("m_PathID", 0))
            renderer_ref = int(tree.get("meshRenderer", {}).get("m_PathID", 0))
            if filter_ref and mesh_filter_id and filter_ref != mesh_filter_id:
                continue
            if renderer_ref and renderer_ref != mesh_renderer_id:
                continue
            script_name = self._pointer_name(
                file_index, component_id, tree.get("m_Script", {})
            ) or "MeshSequence"
            return MeshSequenceRecord(
                component_id=component_id,
                script_name=script_name,
                mesh_pointers=[
                    dict(pointer) for pointer in mesh_pointers
                    if isinstance(pointer, dict)
                ],
                delays=[float(value) for value in tree.get("delays", [])],
                material_pointers=[
                    dict(pointer) for pointer in tree.get("materials", [])
                    if isinstance(pointer, dict)
                ],
                loop=bool(tree.get("loop", True)),
                default_delay=max(0.0, float(tree.get("delay", 0.0))),
                submesh=int(tree.get("submesh", 0)),
            )
        return None

    def find_model(self, file_index: int, game_object_id: int) -> Optional[ModelCandidate]:
        for model in self.models:
            if model.file_index == file_index and model.game_object_id == int(game_object_id):
                return model
        transform_id = next((
            record.path_id for (index, _path_id), record in self.transforms.items()
            if index == file_index and record.game_object_id == int(game_object_id)
        ), None)
        if transform_id is not None:
            containing = [
                model for model in self.models
                if model.file_index == file_index and transform_id in model.transform_ids
            ]
            if containing:
                return min(containing, key=lambda item: len(item.transform_ids))
        return None

    def best_models(self, require_mesh: bool = False) -> List[ModelCandidate]:
        if not require_mesh:
            return list(self.models)
        return [model for model in self.models if model.skinned_renderer_ids]

    def best_model_for_animation(
        self, file_index: int, path_id: int
    ) -> Optional[ModelCandidate]:
        """Choose an explicitly linked hierarchy before curve compatibility."""
        key = int(file_index), int(path_id)
        if key in self.animation_model_matches:
            return self.animation_model_matches[key]
        explicit = self.explicit_models_for_animation(*key, require_mesh=True)
        candidates = (
            [entry["model"] for entry in explicit]
            if explicit else self.best_models(require_mesh=True)
        )
        explicit_by_model = {
            (entry["model"].file_index, entry["model"].game_object_id): entry
            for entry in explicit
        }
        ranked = []
        saw_transform_bindings = False
        for model in candidates:
            try:
                take = self.extract_animation(file_index, path_id, model)
            except Exception:
                continue
            saw_transform_bindings = (
                saw_transform_bindings or take.total_transform_bindings > 0
            )
            entry = explicit_by_model.get((model.file_index, model.game_object_id))
            if take.mapped_bindings <= 0 and take.total_transform_bindings > 0:
                continue
            total = max(1, take.total_transform_bindings)
            ratio = take.mapped_bindings / total
            bundle_name = os.path.basename(self.paths[model.file_index]).lower()
            assembled_bundle = int("_raw_" not in bundle_name)
            ranked.append((
                int(entry["priority"]) if entry else 0,
                ratio,
                take.mapped_bindings,
                len(model.skinned_renderer_ids),
                len(model.rigid_attachments),
                assembled_bundle,
                -abs(len(model.transform_ids) - take.mapped_bindings // 3),
                model,
            ))
        if not ranked and explicit and not saw_transform_bindings:
            # A controller can legitimately drive only GameObject/material
            # properties.  The hierarchy link is still authoritative even
            # though there are no Transform curves to score.
            result = explicit[0]["model"]
            self.animation_model_matches[key] = result
            return result
        result = max(ranked, key=lambda item: item[:-1])[-1] if ranked else None
        self.animation_model_matches[key] = result
        return result

    def model_hash_map(self, model: ModelCandidate) -> Dict[int, List[int]]:
        result: Dict[int, List[int]] = defaultdict(list)
        root_path = self.transforms[model.file_index, model.transform_id].full_path
        for transform_id in model.transform_ids:
            full = self.transforms[model.file_index, transform_id].full_path
            relative = full[len(root_path):].lstrip("/")
            candidates = [full]
            if relative:
                candidates.append(relative)
            current = full
            while "/" in current:
                current = current.split("/", 1)[1]
                candidates.append(current)
            if transform_id == model.transform_id:
                result[0].append(transform_id)
            for candidate in candidates:
                result[unity_crc32(candidate)].append(transform_id)
        return result

    def model_path_map(
        self, model: ModelCandidate, clip_key: Tuple[int, int]
    ) -> Dict[str, List[int]]:
        """Map self-clip paths relative to their serialized component roots.

        Unity Animation and Animator bindings are relative to the GameObject
        carrying the component, which is often several levels below the top
        prefab root.  Treating them as model-root-relative caused valid AOV
        effect animations to map to zero bones in nested hierarchies.
        """
        result: Dict[str, List[int]] = defaultdict(list)
        model_key = int(model.file_index), int(model.game_object_id)
        link = self.animation_model_links.get(tuple(map(int, clip_key)), {}).get(model_key)
        anchors = list(link.get("anchors", ())) if link else []
        if model.transform_id not in anchors:
            anchors.append(model.transform_id)
        model_paths = {
            transform_id: self.transforms[model.file_index, transform_id].full_path
            for transform_id in model.transform_ids
        }
        for anchor_id in anchors:
            anchor_path = model_paths.get(anchor_id)
            if anchor_path is None:
                continue
            prefix = anchor_path + "/"
            for transform_id, full_path in model_paths.items():
                if transform_id == anchor_id:
                    relative = ""
                elif full_path.startswith(prefix):
                    relative = full_path[len(prefix):]
                else:
                    continue
                if transform_id not in result[relative]:
                    result[relative].append(transform_id)
        # Compatibility fallback for components whose owner PPtr is absent:
        # match only complete suffix paths, never partial names.
        root_path = model_paths[model.transform_id]
        for transform_id, full_path in model_paths.items():
            relative = full_path[len(root_path):].lstrip("/")
            current = relative
            while current:
                if transform_id not in result[current]:
                    result[current].append(transform_id)
                current = current.split("/", 1)[1] if "/" in current else ""
        return result

    def extract_animation(self, file_index: int, path_id: int, model: ModelCandidate) -> AnimationTake:
        tree = self.tree(file_index, path_id)
        use_self_clip = bool(
            int(tree.get("m_SelfClipSize", 0)) and _clip_has_data(tree["m_SelfClip"])
        )
        clip = tree["m_SelfClip"] if use_self_clip else tree["m_MuscleClip"]
        data = clip["m_Clip"]["data"]
        bindings = (
            _decompress_self_bindings(tree)
            if use_self_clip else tree["m_ClipBindingConstant"]["genericBindings"]
        )
        duration = max(0.0, float(clip["m_StopTime"]) - float(clip["m_StartTime"]))
        sample_rate = float(tree["m_SampleRate"] or 30.0)
        hash_map = self.model_hash_map(model)
        root_path = self.transforms[model.file_index, model.transform_id].full_path
        relative_path_map = self.model_path_map(
            model, (int(file_index), int(path_id))
        )
        tracks: Dict[int, AnimationTrack] = {}
        binding_ranges = []
        cursor = 0
        total_transform_bindings = 0
        mapped_bindings = 0

        for binding in bindings:
            count = 1
            is_transform = (
                int(binding.get("classID", 0)) == 0xFFFFFFFF
                if use_self_clip else int(binding["typeID"]) == 4
            )
            attribute = int(binding["attribute"]) if str(binding["attribute"]).isdigit() else -1
            if is_transform:
                total_transform_bindings += 1
                count = 4 if attribute == 2 else 3 if attribute in (1, 3, 4) else 1
            transform_ids = (
                relative_path_map.get(str(binding["path"]), [])
                if use_self_clip else hash_map.get(int(binding["path"]), [])
            )
            transform_id = transform_ids[0] if transform_ids and is_transform else None
            if transform_id is not None and is_transform:
                mapped_bindings += 1
                record = self.transforms[model.file_index, transform_id]
                tracks.setdefault(transform_id, AnimationTrack(transform_id, record.full_path))
            binding_ranges.append((cursor, cursor + count, binding, transform_id))
            cursor += count

        def append_values(time_value: float, values: Dict[int, float]):
            for start, end, binding, transform_id in binding_ranges:
                if transform_id is None or any(index not in values for index in range(start, end)):
                    continue
                track = tracks[transform_id]
                vector = tuple(values[index] for index in range(start, end))
                attribute = int(binding["attribute"])
                if attribute == 1 and len(vector) == 3:
                    track.translations.append((time_value, vector))
                elif attribute == 2 and len(vector) == 4:
                    track.rotations.append((time_value, quaternion_normalize(vector)))
                elif attribute == 3 and len(vector) == 3:
                    track.scales.append((time_value, vector))
                elif attribute == 4 and len(vector) == 3:
                    track.rotations.append((time_value, euler_degrees_to_quaternion(vector)))

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
        frame_count = int(dense["m_FrameCount"])
        curve_count = int(dense["m_CurveCount"])
        dense_values = _dense_sample_values(dense)
        stream_count = int(stream["curveCount"])
        if frame_count and curve_count and len(dense_values) >= frame_count * curve_count:
            for frame_index in range(frame_count):
                offset = frame_index * curve_count
                append_values(
                    float(dense["m_BeginTime"]) + frame_index / float(dense["m_SampleRate"]),
                    {
                        stream_count + index: float(dense_values[offset + index])
                        for index in range(curve_count)
                    },
                )

        constant = data["m_ConstantClip"]["data"]
        if constant:
            base = stream_count + curve_count
            values = {base + index: float(value) for index, value in enumerate(constant)}
            append_values(0.0, values)
            append_values(duration, values)

        for track in tracks.values():
            track.translations.sort(key=lambda item: item[0])
            track.rotations.sort(key=lambda item: item[0])
            track.scales.sort(key=lambda item: item[0])

        return AnimationTake(
            str(tree["m_Name"]), sample_rate, duration, tracks,
            mapped_bindings, total_transform_bindings,
        )


def _sample_vector(keys, time_value: float, fallback):
    """Linearly sample one Unity vector curve without assuming dense keys."""
    if not keys:
        return tuple(float(value) for value in fallback)
    if time_value <= float(keys[0][0]):
        return tuple(float(value) for value in keys[0][1])
    if time_value >= float(keys[-1][0]):
        return tuple(float(value) for value in keys[-1][1])
    low, high = 0, len(keys) - 1
    while low + 1 < high:
        middle = (low + high) // 2
        if float(keys[middle][0]) <= time_value:
            low = middle
        else:
            high = middle
    start_time, start = keys[low]
    end_time, end = keys[high]
    span = max(1e-12, float(end_time) - float(start_time))
    amount = (time_value - float(start_time)) / span
    return tuple(
        float(left) + (float(right) - float(left)) * amount
        for left, right in zip(start, end)
    )


def _sample_quaternion(keys, time_value: float, fallback):
    """Shortest-path normalized quaternion interpolation for preview skinning."""
    if not keys:
        return quaternion_normalize(fallback)
    if time_value <= float(keys[0][0]):
        return quaternion_normalize(keys[0][1])
    if time_value >= float(keys[-1][0]):
        return quaternion_normalize(keys[-1][1])
    low, high = 0, len(keys) - 1
    while low + 1 < high:
        middle = (low + high) // 2
        if float(keys[middle][0]) <= time_value:
            low = middle
        else:
            high = middle
    start_time, start = keys[low]
    end_time, end = keys[high]
    span = max(1e-12, float(end_time) - float(start_time))
    amount = (time_value - float(start_time)) / span
    left = quaternion_normalize(start)
    right = quaternion_normalize(end)
    if sum(a * b for a, b in zip(left, right)) < 0.0:
        right = tuple(-value for value in right)
    return quaternion_normalize(tuple(
        a + (b - a) * amount for a, b in zip(left, right)
    ))


def _unity_matrix(value) -> List[List[float]]:
    """Read Unity 2022 serialized bind-pose rows into row-major math.

    The legacy ``Matrix4x4.__getitem__`` helper treats ``.M`` as column-major,
    but Mesh bind poses are read in their typetree order
    ``e00,e01,e02,e03,e10,...``. Using that helper transposes translation into
    the last row and causes the characteristic exploded v2 skin.
    """
    raw = list(value.M if hasattr(value, "M") else value)
    if len(raw) != 16:
        raise ValueError(f"Expected a 4x4 bind pose, got {len(raw)} values")
    return [[float(raw[row * 4 + column]) for column in range(4)] for row in range(4)]


def _preview_mesh_geometry(mesh):
    """Decode one Unity Mesh into homogeneous vertices and local triangles."""
    import numpy as np

    vertex_count = int(getattr(mesh, "m_VertexCount", 0))
    if vertex_count <= 0 or len(mesh.m_Vertices) < vertex_count * 3:
        raise ValueError("mesh_has_no_vertices")
    source_vertices = np.asarray(mesh.m_Vertices, dtype=np.float64).reshape(
        vertex_count, -1
    )[:, :3].copy()
    homogeneous = np.ones((vertex_count, 4), dtype=np.float64)
    homogeneous[:, :3] = source_vertices
    triangles = []
    index_stride = 2 if bool(getattr(mesh, "m_Use16BitIndices", True)) else 4
    for submesh in mesh.m_SubMeshes:
        first = int(submesh.firstByte) // index_stride
        count = int(submesh.indexCount)
        source = mesh.m_Indices[first:first + count]
        base = int(submesh.baseVertex)
        for index in range(0, len(source) - 2, 3):
            triangles.extend((
                int(source[index]) + base,
                int(source[index + 2]) + base,
                int(source[index + 1]) + base,
            ))
    if not triangles:
        raise ValueError("mesh_has_no_triangles")
    return homogeneous, np.asarray(triangles, dtype=np.uint32)


def _mesh_sequence_step(sequence: MeshSequenceRecord, time_value: float) -> int:
    count = len(sequence.mesh_pointers)
    if count <= 1:
        return 0
    fallback = sequence.default_delay if sequence.default_delay > 1e-6 else 1.0 / 30.0
    durations = [
        float(sequence.delays[index])
        if index < len(sequence.delays) and float(sequence.delays[index]) > 1e-6
        else fallback
        for index in range(count)
    ]
    total = sum(durations)
    if total <= 1e-6:
        return 0
    cursor = float(time_value)
    if sequence.loop:
        cursor %= total
    else:
        cursor = min(max(0.0, cursor), max(0.0, total - 1e-9))
    elapsed = 0.0
    for index, duration in enumerate(durations):
        elapsed += duration
        if cursor < elapsed:
            return index
    return count - 1


def build_animation_preview_payload(
    project: AnimationProjectIndex,
    animation_file_index: int,
    animation_path_id: int,
    model: ModelCandidate,
    max_frames: int = 72,
    include_model: bool = True,
    include_attachments: bool = True,
    cancel_check=None,
) -> dict:
    """Build skinned-model or skeleton-only frames in an isolated worker.

    Version 2 uses concatenated per-frame buffers because MeshSequence assets can
    change topology and vertex count while the AnimationClip is playing.
    """
    import numpy as np

    take = project.extract_animation(animation_file_index, animation_path_id, model)
    if take.total_transform_bindings and take.mapped_bindings == 0:
        raise ValueError("animation_model_mismatch")

    preview_fps = min(30.0, max(1.0, float(take.sample_rate or 30.0)))
    requested_frames = max(2, int(round(max(0.0, take.duration) * preview_fps)) + 1)
    frame_count = min(max(2, int(max_frames)), requested_frames)
    if frame_count == 2 and take.duration <= 0.0:
        times = np.asarray((0.0, 1.0 / preview_fps), dtype=np.float64)
    else:
        times = np.linspace(0.0, max(take.duration, 1.0 / preview_fps), frame_count)
    actual_fps = (
        (frame_count - 1) / max(float(times[-1]) - float(times[0]), 1e-6)
        if frame_count > 1 else preview_fps
    )

    transform_order = [
        transform_id for transform_id in model.transform_ids
        if (model.file_index, transform_id) in project.transforms
    ]
    rest_globals: Dict[int, List[List[float]]] = {}
    for transform_id in transform_order:
        record = project.transforms[model.file_index, transform_id]
        local = _matrix_from_trs(record.position, record.rotation, record.scale)
        parent = rest_globals.get(record.parent_id, _matrix_identity())
        rest_globals[transform_id] = _matrix_multiply(parent, local)

    skinned_parts = []
    skipped_renderers = 0
    renderer_ids = model.skinned_renderer_ids if include_model else ()
    for renderer_id in renderer_ids:
        renderer_obj = project.object(model.file_index, renderer_id)
        if renderer_obj is None:
            skipped_renderers += 1
            continue
        renderer_tree = project.tree(model.file_index, renderer_id)
        _mesh_file_index, mesh_obj = project.resolve_pptr(
            renderer_obj, model.file_index, renderer_tree.get("m_Mesh", {})
        )
        if mesh_obj is None:
            skipped_renderers += 1
            continue
        try:
            mesh = mesh_obj.read()
            homogeneous, triangles = _preview_mesh_geometry(mesh)
        except Exception:
            skipped_renderers += 1
            continue
        vertex_count = len(homogeneous)
        game_object_id = int(renderer_tree["m_GameObject"]["m_PathID"])
        mesh_transform_id = project.transform_by_game_object.get(
            (model.file_index, game_object_id)
        )
        if mesh_transform_id is None:
            skipped_renderers += 1
            continue

        bones = [
            int(pointer.get("m_PathID", 0))
            for pointer in renderer_tree.get("m_Bones", [])
        ]
        bind_poses = list(getattr(mesh, "m_BindPose", []))
        skin = list(getattr(mesh, "m_Skin", []))
        if len(skin) != vertex_count or not bones:
            bone_indices = np.zeros((vertex_count, 1), dtype=np.int32)
            bone_weights = np.ones((vertex_count, 1), dtype=np.float64)
            bones = [mesh_transform_id]
            bind_matrices = [_matrix_identity()]
        else:
            influence_count = max(1, max(len(item.weight) for item in skin))
            bone_indices = np.zeros((vertex_count, influence_count), dtype=np.int32)
            bone_weights = np.zeros((vertex_count, influence_count), dtype=np.float64)
            for vertex_index, item in enumerate(skin):
                for slot, (bone_index, weight) in enumerate(zip(item.boneIndex, item.weight)):
                    if slot >= influence_count:
                        break
                    bone_indices[vertex_index, slot] = int(bone_index)
                    bone_weights[vertex_index, slot] = max(0.0, float(weight))
            totals = bone_weights.sum(axis=1)
            valid_totals = totals > 1e-8
            bone_weights[valid_totals] /= totals[valid_totals, None]
            bind_matrices = []
            mesh_world = rest_globals.get(mesh_transform_id, _matrix_identity())
            for bone_index, transform_id in enumerate(bones):
                if bone_index < len(bind_poses):
                    bind_matrices.append(_unity_matrix(bind_poses[bone_index]))
                else:
                    bone_world = rest_globals.get(transform_id, _matrix_identity())
                    bind_matrices.append(
                        _matrix_multiply(_matrix_inverse(bone_world), mesh_world)
                    )
        skinned_parts.append({
            "vertices": homogeneous,
            "triangles": triangles,
            "bones": bones,
            "bone_indices": bone_indices,
            "bone_weights": bone_weights,
            "bind_matrices": [np.asarray(matrix, dtype=np.float64) for matrix in bind_matrices],
            "mesh_transform_id": mesh_transform_id,
            "vertex_count": vertex_count,
        })

    if include_model and not skinned_parts:
        raise ValueError("animation_model_has_no_skinned_mesh")

    mesh_cache = {}

    def resolved_geometry(source_component_id: int, pointer: dict):
        if not source_component_id or int(pointer.get("m_PathID", 0)) == 0:
            return None
        source_obj = project.object(model.file_index, source_component_id)
        if source_obj is None:
            return None
        target_file_index, mesh_obj = project.resolve_pptr(
            source_obj, model.file_index, pointer
        )
        if mesh_obj is None:
            return None
        key = int(target_file_index), int(mesh_obj.path_id)
        if key not in mesh_cache:
            try:
                mesh_cache[key] = (*_preview_mesh_geometry(mesh_obj.read()), key)
            except Exception:
                mesh_cache[key] = None
        return mesh_cache[key]

    rigid_parts = []
    skipped_attachments = 0
    if include_model and include_attachments:
        for attachment in model.rigid_attachments:
            if not attachment.enabled:
                skipped_attachments += 1
                continue
            source_component_id = attachment.mesh_filter_id or attachment.mesh_renderer_id
            default_geometry = resolved_geometry(
                source_component_id, attachment.mesh_pointer
            )
            sequence_geometries = []
            if attachment.sequence is not None:
                sequence_geometries = [
                    resolved_geometry(attachment.sequence.component_id, pointer)
                    for pointer in attachment.sequence.mesh_pointers
                ]
            if default_geometry is None and not any(
                geometry is not None for geometry in sequence_geometries
            ):
                skipped_attachments += 1
                continue
            rigid_parts.append({
                "attachment": attachment,
                "default": default_geometry,
                "sequence": sequence_geometries,
            })

    vertex_chunks = []
    index_chunks = []
    vertex_counts = []
    index_counts = []
    sequence_mesh_keys = defaultdict(set)
    for time_value in times:
        if cancel_check is not None and cancel_check():
            raise InterruptedError("preview_superseded")
        globals_at_time: Dict[int, List[List[float]]] = {}
        for transform_id in transform_order:
            record = project.transforms[model.file_index, transform_id]
            track = take.tracks.get(transform_id)
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
            _matrix_inverse(globals_at_time.get(model.transform_id, _matrix_identity())),
            dtype=np.float64,
        )

        frame_vertices = []
        frame_indices = []
        vertex_offset = 0
        for part in skinned_parts:
            source = part["vertices"]
            deformed = np.zeros((part["vertex_count"], 4), dtype=np.float64)
            contributed = np.zeros(part["vertex_count"], dtype=np.float64)
            for slot in range(part["bone_weights"].shape[1]):
                slot_weights = part["bone_weights"][:, slot]
                for bone_index in np.unique(part["bone_indices"][:, slot]):
                    bone_index = int(bone_index)
                    mask = (
                        (part["bone_indices"][:, slot] == bone_index)
                        & (slot_weights > 1e-8)
                    )
                    if not np.any(mask) or not 0 <= bone_index < len(part["bones"]):
                        continue
                    transform_id = part["bones"][bone_index]
                    bone_world = globals_at_time.get(transform_id)
                    if bone_world is None or bone_index >= len(part["bind_matrices"]):
                        continue
                    skin_matrix = (
                        root_inverse @ np.asarray(bone_world)
                        @ part["bind_matrices"][bone_index]
                    )
                    transformed = (skin_matrix @ source[mask].T).T
                    weights = slot_weights[mask, None]
                    deformed[mask] += transformed * weights
                    contributed[mask] += slot_weights[mask]
            missing = contributed <= 1e-8
            if np.any(missing):
                mesh_world = np.asarray(
                    globals_at_time.get(part["mesh_transform_id"], _matrix_identity()),
                    dtype=np.float64,
                )
                deformed[missing] = (root_inverse @ mesh_world @ source[missing].T).T
            values = deformed[:, :3]
            values[:, 0] *= -1.0
            frame_vertices.append(values.astype(np.float32, copy=False))
            frame_indices.append(part["triangles"] + vertex_offset)
            vertex_offset += part["vertex_count"]

        if not include_model:
            skeleton_vertices = []
            for transform_id in transform_order:
                record = project.transforms[model.file_index, transform_id]
                if record.parent_id not in globals_at_time:
                    continue
                parent_world = np.asarray(
                    globals_at_time[record.parent_id], dtype=np.float64
                )
                child_world = np.asarray(
                    globals_at_time[transform_id], dtype=np.float64
                )
                parent_point = (root_inverse @ parent_world)[:3, 3]
                child_point = (root_inverse @ child_world)[:3, 3]
                parent_point = parent_point.copy()
                child_point = child_point.copy()
                parent_point[0] *= -1.0
                child_point[0] *= -1.0
                skeleton_vertices.extend((parent_point, child_point))
            if not skeleton_vertices:
                raise ValueError("animation_model_has_no_skeleton")
            values = np.asarray(skeleton_vertices, dtype=np.float32)
            frame_vertices.append(values)
            frame_indices.append(
                np.arange(len(values), dtype=np.uint32) + vertex_offset
            )
            vertex_offset += len(values)

        for rigid in rigid_parts:
            attachment = rigid["attachment"]
            geometry = rigid["default"]
            if attachment.sequence is not None and rigid["sequence"]:
                step = _mesh_sequence_step(attachment.sequence, float(time_value))
                if step < len(rigid["sequence"]):
                    geometry = rigid["sequence"][step]
            if geometry is None:
                continue
            source, triangles, mesh_key = geometry
            sequence_mesh_keys[attachment.name].add(mesh_key)
            attachment_world = np.asarray(
                globals_at_time.get(attachment.transform_id, _matrix_identity()),
                dtype=np.float64,
            )
            transformed = (root_inverse @ attachment_world @ source.T).T[:, :3]
            transformed[:, 0] *= -1.0
            frame_vertices.append(transformed.astype(np.float32, copy=False))
            frame_indices.append(triangles + vertex_offset)
            vertex_offset += len(transformed)

        vertices = np.concatenate(frame_vertices, axis=0)
        indices = np.concatenate(frame_indices, axis=0).astype(np.uint32, copy=False)
        vertex_chunks.append(vertices.tobytes())
        index_chunks.append(indices.tobytes())
        vertex_counts.append(int(len(vertices)))
        index_counts.append(int(len(indices)))

    metadata = {
        "name": take.name,
        "parts": len(skinned_parts),
        "rigid_attachments": len(rigid_parts),
        "sequence_attachments": sum(
            rigid["attachment"].sequence is not None for rigid in rigid_parts
        ),
        "attachment_names": [rigid["attachment"].name for rigid in rigid_parts],
        "attachment_mounts": [rigid["attachment"].mount_name for rigid in rigid_parts],
        "sequence_mesh_variants": {
            name: len(keys) for name, keys in sequence_mesh_keys.items()
        },
        "mapped_bindings": take.mapped_bindings,
        "total_transform_bindings": take.total_transform_bindings,
        "skipped_renderers": skipped_renderers,
        "skipped_attachments": skipped_attachments,
        "includes_model": bool(include_model),
        "primitive": "triangles" if include_model else "lines",
    }
    return {
        "version": 2,
        "primitive": "triangles" if include_model else "lines",
        "frame_bytes": b"".join(vertex_chunks),
        "index_bytes": b"".join(index_chunks),
        "frame_vertex_counts": tuple(vertex_counts),
        "frame_index_counts": tuple(index_counts),
        "frames_per_second": float(actual_fps),
        "metadata": metadata,
    }


def _fbx_array(values: Iterable, per_line: int = 24) -> str:
    items = []
    for value in values:
        if isinstance(value, float):
            items.append(format(value, ".9g"))
        else:
            items.append(str(value))
    if not items:
        return ""
    return ",".join(items)


def _safe_fbx_name(value: str) -> str:
    return value.replace("\\", "_").replace('"', "'").replace("\x00", "")


class AsciiFbxWriter:
    """Write an interoperable FBX 7.4 skeleton, skin and animation scene."""

    def __init__(
        self, project: AnimationProjectIndex, model: ModelCandidate,
        take: AnimationTake, include_model: bool = True,
        include_attachments: bool = True,
    ):
        self.project = project
        self.model = model
        self.take = take
        self.include_model = bool(include_model)
        self.include_attachments = bool(include_attachments)
        self.next_id = 100_000
        self.node_ids: Dict[int, int] = {}
        self.node_global_matrices: Dict[int, List[List[float]]] = {}
        self.objects: List[str] = []
        self.connections: List[str] = []
        self.written_attachment_meshes: List[str] = []

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def write(self, output_path: str) -> dict:
        self._write_nodes()
        if self.include_model:
            self._write_skinned_meshes()
        if self.include_attachments:
            self._write_rigid_attachments()
        self._write_animation()
        text = self._compose()
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        write_binary_fbx(text, output_path)
        return {
            "path": os.path.abspath(output_path),
            "nodes": len(self.node_ids),
            "skinned_meshes": len(self.model.skinned_renderer_ids) if self.include_model else 0,
            "attachment_meshes": len(self.written_attachment_meshes),
            "attachment_names": list(self.written_attachment_meshes),
            "tracks": len(self.take.tracks),
            "mapped_bindings": self.take.mapped_bindings,
            "total_transform_bindings": self.take.total_transform_bindings,
            "format": "binary-fbx-7400",
            "includes_model": self.include_model,
            "includes_attachments": self.include_attachments,
        }

    def _write_nodes(self):
        for transform_id in self.model.transform_ids:
            record = self.project.transforms[self.model.file_index, transform_id]
            node_id = self.new_id()
            attribute_id = self.new_id()
            self.node_ids[transform_id] = node_id
            subtype = "LimbNode" if transform_id != self.model.transform_id else "Root"
            px, py, pz = record.position
            qx, qy, qz, qw = record.rotation
            rx, ry, rz = quaternion_to_euler_degrees((qx, -qy, -qz, qw))
            sx, sy, sz = record.scale
            local_matrix = _matrix_from_trs(
                (-px, py, pz), (qx, -qy, -qz, qw), (sx, sy, sz)
            )
            parent_matrix = self.node_global_matrices.get(
                record.parent_id, _matrix_identity()
            )
            self.node_global_matrices[transform_id] = _matrix_multiply(
                parent_matrix, local_matrix
            )
            self.objects.append(f'''\
    Model: {node_id}, "Model::{_safe_fbx_name(record.name)}", "{subtype}" {{
        Version: 232
        Properties70:  {{
            P: "Lcl Translation", "Lcl Translation", "", "A",{-px:.9g},{py:.9g},{pz:.9g}
            P: "Lcl Rotation", "Lcl Rotation", "", "A",{rx:.9g},{ry:.9g},{rz:.9g}
            P: "Lcl Scaling", "Lcl Scaling", "", "A",{sx:.9g},{sy:.9g},{sz:.9g}
            P: "RotationOrder", "enum", "", "",0
        }}
        Shading: T
        Culling: "CullingOff"
    }}''')
            self.objects.append(f'''\
    NodeAttribute: {attribute_id}, "NodeAttribute::{_safe_fbx_name(record.name)}", "LimbNode" {{
        TypeFlags: "Skeleton"
        Size: 1
    }}''')
            self.connections.append(f'    C: "OO",{attribute_id},{node_id}')
            parent_id = self.node_ids.get(record.parent_id, 0)
            self.connections.append(f'    C: "OO",{node_id},{parent_id}')

    def _write_skinned_meshes(self):
        for renderer_id in self.model.skinned_renderer_ids:
            renderer_obj = self.project.object(self.model.file_index, renderer_id)
            if renderer_obj is None:
                continue
            tree = self.project.tree(self.model.file_index, renderer_id)
            mesh_ref = tree.get("m_Mesh", {})
            _mesh_file_index, mesh_obj = self.project.resolve_pptr(
                renderer_obj, self.model.file_index, mesh_ref
            )
            if mesh_obj is None:
                continue
            try:
                mesh = mesh_obj.read()
            except Exception:
                continue
            game_object_id = int(tree["m_GameObject"]["m_PathID"])
            transform_id = next((
                item.path_id for item in self.project.transforms.values()
                if item.file_index == self.model.file_index and item.game_object_id == game_object_id
            ), None)
            if transform_id is None or transform_id not in self.node_ids:
                continue

            geometry_id = self.new_id()
            mesh_model_id = self.new_id()
            vertices = []
            source_vertices = tuple(mesh.m_Vertices)
            for index in range(0, len(source_vertices), 3):
                vertices.extend((-source_vertices[index], source_vertices[index + 1], source_vertices[index + 2]))
            polygon_indices = []
            for submesh in mesh.m_SubMeshes:
                first = int(submesh.firstByte // 2)
                indices = mesh.m_Indices[first:first + int(submesh.indexCount)]
                for index in range(0, len(indices) - 2, 3):
                    a = int(indices[index]) + int(submesh.baseVertex)
                    b = int(indices[index + 2]) + int(submesh.baseVertex)
                    c = int(indices[index + 1]) + int(submesh.baseVertex)
                    polygon_indices.extend((a, b, -c - 1))
            self.objects.append(f'''\
    Geometry: {geometry_id}, "Geometry::{_safe_fbx_name(mesh.name)}", "Mesh" {{
        GeometryVersion: 124
        Vertices: *{len(vertices)} {{ a: {_fbx_array(vertices)} }}
        PolygonVertexIndex: *{len(polygon_indices)} {{ a: {_fbx_array(polygon_indices)} }}
    }}''')
            self.objects.append(f'''\
    Model: {mesh_model_id}, "Model::{_safe_fbx_name(mesh.name)}", "Mesh" {{
        Version: 232
        Properties70:  {{
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,0
            P: "Lcl Rotation", "Lcl Rotation", "", "A",0,0,0
            P: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
        }}
        Shading: T
        Culling: "CullingOff"
    }}''')
            self.connections.append(f'    C: "OO",{geometry_id},{mesh_model_id}')
            self.connections.append(
                f'    C: "OO",{mesh_model_id},{self.node_ids[transform_id]}'
            )
            self._write_skin(geometry_id, tree, mesh, transform_id)

    def _write_skin(
        self, geometry_id: int, renderer_tree: dict, mesh,
        mesh_transform_id: int,
    ):
        bones = renderer_tree.get("m_Bones", [])
        if not bones or not getattr(mesh, "m_Skin", None):
            return
        skin_id = self.new_id()
        self.objects.append(f'''\
    Deformer: {skin_id}, "Deformer::Skin", "Skin" {{
        Version: 101
        Link_DeformAcuracy: 50
    }}''')
        self.connections.append(f'    C: "OO",{skin_id},{geometry_id}')
        weights_by_bone = defaultdict(list)
        for vertex_index, skin in enumerate(mesh.m_Skin):
            for bone_index, weight in zip(skin.boneIndex, skin.weight):
                if 0 <= int(bone_index) < len(bones) and float(weight) > 0.0:
                    weights_by_bone[int(bone_index)].append((vertex_index, float(weight)))
        mesh_world = self.node_global_matrices[mesh_transform_id]
        armature_world = self.node_global_matrices[self.model.transform_id]
        for bone_index, bone_ref in enumerate(bones):
            transform_id = int(bone_ref.get("m_PathID", 0))
            if transform_id not in self.node_ids or bone_index not in weights_by_bone:
                continue
            pairs = weights_by_bone[bone_index]
            cluster_id = self.new_id()
            bone_world = self.node_global_matrices[transform_id]
            # FBX stores Cluster.Transform in bone space, not as the mesh
            # global matrix presented by Autodesk's API.  This is Unity's
            # bind-pose definition: inverse(boneWorld) * meshWorld.
            bind_pose = _matrix_multiply(_matrix_inverse(bone_world), mesh_world)
            self.objects.append(f'''\
    Deformer: {cluster_id}, "SubDeformer::{_safe_fbx_name(self.project.transforms[self.model.file_index, transform_id].name)}", "Cluster" {{
        Version: 100
        Indexes: *{len(pairs)} {{ a: {_fbx_array(index for index, _ in pairs)} }}
        Weights: *{len(pairs)} {{ a: {_fbx_array(weight for _, weight in pairs)} }}
        Transform: *16 {{ a: {_fbx_array(_matrix_to_fbx_array(bind_pose))} }}
        TransformLink: *16 {{ a: {_fbx_array(_matrix_to_fbx_array(bone_world))} }}
        TransformAssociateModel: *16 {{ a: {_fbx_array(_matrix_to_fbx_array(armature_world))} }}
    }}''')
            self.connections.append(f'    C: "OO",{cluster_id},{skin_id}')
            self.connections.append(f'    C: "OO",{self.node_ids[transform_id]},{cluster_id}')

    def _write_rigid_attachments(self):
        """Export ordinary MeshFilter/MeshRenderer geometry under its socket chain."""
        for attachment in self.model.rigid_attachments:
            if not attachment.enabled or attachment.transform_id not in self.node_ids:
                continue
            source_component_id = attachment.mesh_filter_id or attachment.mesh_renderer_id
            pointer = attachment.mesh_pointer
            if int(pointer.get("m_PathID", 0)) == 0 and attachment.sequence is not None:
                source_component_id = attachment.sequence.component_id
                pointer = next((
                    item for item in attachment.sequence.mesh_pointers
                    if int(item.get("m_PathID", 0))
                ), {})
            source_obj = self.project.object(
                self.model.file_index, source_component_id
            )
            if source_obj is None or int(pointer.get("m_PathID", 0)) == 0:
                continue
            _mesh_file_index, mesh_obj = self.project.resolve_pptr(
                source_obj, self.model.file_index, pointer
            )
            if mesh_obj is None:
                continue
            try:
                mesh = mesh_obj.read()
            except Exception:
                continue

            geometry_id = self.new_id()
            mesh_model_id = self.new_id()
            vertices = []
            source_vertices = tuple(mesh.m_Vertices)
            for index in range(0, len(source_vertices), 3):
                vertices.extend((
                    -source_vertices[index],
                    source_vertices[index + 1],
                    source_vertices[index + 2],
                ))
            polygon_indices = []
            index_stride = 2 if bool(getattr(mesh, "m_Use16BitIndices", True)) else 4
            for submesh in mesh.m_SubMeshes:
                first = int(submesh.firstByte) // index_stride
                indices = mesh.m_Indices[first:first + int(submesh.indexCount)]
                for index in range(0, len(indices) - 2, 3):
                    a = int(indices[index]) + int(submesh.baseVertex)
                    b = int(indices[index + 2]) + int(submesh.baseVertex)
                    c = int(indices[index + 1]) + int(submesh.baseVertex)
                    polygon_indices.extend((a, b, -c - 1))
            if not vertices or not polygon_indices:
                continue

            export_name = _safe_fbx_name(
                f"{attachment.name}_{getattr(mesh, 'name', attachment.mesh_name)}"
            )
            mount_name = _safe_fbx_name(attachment.mount_name)
            sequence_count = attachment.sequence_frame_count
            self.objects.append(f'''\
    Geometry: {geometry_id}, "Geometry::{export_name}", "Mesh" {{
        GeometryVersion: 124
        Vertices: *{len(vertices)} {{ a: {_fbx_array(vertices)} }}
        PolygonVertexIndex: *{len(polygon_indices)} {{ a: {_fbx_array(polygon_indices)} }}
    }}''')
            self.objects.append(f'''\
    Model: {mesh_model_id}, "Model::{export_name}", "Mesh" {{
        Version: 232
        Properties70:  {{
            P: "Lcl Translation", "Lcl Translation", "", "A",0,0,0
            P: "Lcl Rotation", "Lcl Rotation", "", "A",0,0,0
            P: "Lcl Scaling", "Lcl Scaling", "", "A",1,1,1
            P: "AOV Attachment Kind", "KString", "", "", "{_safe_fbx_name(attachment.kind)}"
            P: "AOV Mount", "KString", "", "", "{mount_name}"
            P: "AOV MeshSequence Frames", "int", "Integer", "",{sequence_count}
        }}
        Shading: T
        Culling: "CullingOff"
    }}''')
            self.connections.append(f'    C: "OO",{geometry_id},{mesh_model_id}')
            self.connections.append(
                f'    C: "OO",{mesh_model_id},{self.node_ids[attachment.transform_id]}'
            )
            self.written_attachment_meshes.append(attachment.name)

    def _write_animation(self):
        stack_id, layer_id = self.new_id(), self.new_id()
        stop = int(round(self.take.duration * FBX_TICKS))
        self.objects.append(f'''\
    AnimationStack: {stack_id}, "AnimStack::{_safe_fbx_name(self.take.name)}", "" {{
        Properties70:  {{
            P: "LocalStart", "KTime", "Time", "",0
            P: "LocalStop", "KTime", "Time", "",{stop}
            P: "ReferenceStart", "KTime", "Time", "",0
            P: "ReferenceStop", "KTime", "Time", "",{stop}
        }}
    }}''')
        self.objects.append(f'    AnimationLayer: {layer_id}, "AnimLayer::BaseLayer", "" {{}}')
        self.connections.append(f'    C: "OO",{layer_id},{stack_id}')
        for transform_id, track in self.take.tracks.items():
            if transform_id not in self.node_ids:
                continue
            if track.translations:
                converted = [(time, (-v[0], v[1], v[2])) for time, v in track.translations]
                self._write_curve_group(layer_id, transform_id, "T", "Lcl Translation", converted)
            if track.rotations:
                converted = [
                    (time, quaternion_to_euler_degrees((q[0], -q[1], -q[2], q[3])))
                    for time, q in track.rotations
                ]
                self._write_curve_group(layer_id, transform_id, "R", "Lcl Rotation", converted)
            if track.scales:
                self._write_curve_group(layer_id, transform_id, "S", "Lcl Scaling", track.scales)

    def _write_curve_group(self, layer_id, transform_id, short_name, prop_name, keys):
        curve_node_id = self.new_id()
        defaults = keys[0][1]
        self.objects.append(f'''\
    AnimationCurveNode: {curve_node_id}, "AnimCurveNode::{short_name}", "" {{
        Properties70:  {{
            P: "d|X", "Number", "", "A",{defaults[0]:.9g}
            P: "d|Y", "Number", "", "A",{defaults[1]:.9g}
            P: "d|Z", "Number", "", "A",{defaults[2]:.9g}
        }}
    }}''')
        self.connections.append(f'    C: "OO",{curve_node_id},{layer_id}')
        self.connections.append(
            f'    C: "OP",{curve_node_id},{self.node_ids[transform_id]},"{prop_name}"'
        )
        for component, axis in enumerate(("X", "Y", "Z")):
            curve_id = self.new_id()
            times = [int(round(time * FBX_TICKS)) for time, _ in keys]
            values = [float(value[component]) for _, value in keys]
            flags = [4] * len(keys)
            attrs = [0.0] * (len(keys) * 4)
            refs = [1] * len(keys)
            self.objects.append(f'''\
    AnimationCurve: {curve_id}, "AnimCurve::{short_name}{axis}", "" {{
        Default: {values[0]:.9g}
        KeyVer: 4008
        KeyTime: *{len(times)} {{ a: {_fbx_array(times)} }}
        KeyValueFloat: *{len(values)} {{ a: {_fbx_array(values)} }}
        KeyAttrFlags: *{len(flags)} {{ a: {_fbx_array(flags)} }}
        KeyAttrDataFloat: *{len(attrs)} {{ a: {_fbx_array(attrs)} }}
        KeyAttrRefCount: *{len(refs)} {{ a: {_fbx_array(refs)} }}
    }}''')
            self.connections.append(f'    C: "OP",{curve_id},{curve_node_id},"d|{axis}"')

    def _compose(self) -> str:
        return f'''; FBX 7.4.0 project file
FBXHeaderExtension:  {{
    FBXHeaderVersion: 1003
    FBXVersion: 7400
    Creator: "AOV Asset Workshop 2022"
}}
GlobalSettings:  {{
    Version: 1000
    Properties70:  {{
        P: "UpAxis", "int", "Integer", "",1
        P: "UpAxisSign", "int", "Integer", "",1
        P: "FrontAxis", "int", "Integer", "",2
        P: "FrontAxisSign", "int", "Integer", "",-1
        P: "CoordAxis", "int", "Integer", "",0
        P: "CoordAxisSign", "int", "Integer", "",1
        P: "UnitScaleFactor", "double", "Number", "",100
        P: "OriginalUnitScaleFactor", "double", "Number", "",100
        P: "TimeMode", "enum", "", "",6
        P: "CustomFrameRate", "double", "Number", "",{self.take.sample_rate:.9g}
    }}
}}
Definitions:  {{
    Version: 100
    Count: {len(self.objects)}
}}
Objects:  {{
{os.linesep.join(self.objects)}
}}
Connections:  {{
{os.linesep.join(self.connections)}
}}
Takes:  {{
    Current: "{_safe_fbx_name(self.take.name)}"
}}
'''


def export_animation_fbx(
    project: AnimationProjectIndex,
    animation_file_index: int,
    animation_path_id: int,
    model: ModelCandidate,
    output_path: str,
    include_model: bool = True,
    include_attachments: bool = True,
) -> dict:
    take = project.extract_animation(animation_file_index, animation_path_id, model)
    if take.total_transform_bindings and take.mapped_bindings == 0:
        raise ValueError(
            "The selected animation has no transform bindings matching this model. "
            "Choose the GameObject/effect hierarchy used by the clip."
        )
    return AsciiFbxWriter(
        project, model, take, include_model=include_model,
        include_attachments=include_attachments,
    ).write(output_path)


def validate_fbx(path: str) -> dict:
    import ufbx
    with ufbx.load_file(path) as scene:
        stacks = [stack.name for stack in scene.anim_stacks]
        return {
            "nodes": len(scene.nodes),
            "meshes": len(scene.meshes),
            "bones": len(scene.bones),
            "skins": len(scene.skin_deformers),
            "animations": stacks,
        }


def _empty_optimized_clip(clip: dict, duration: float = 0.0) -> None:
    clip["m_StartTime"] = 0.0
    clip["m_StopTime"] = float(duration)
    data = clip["m_Clip"]["data"]
    streamed = data["m_StreamedClip"]
    streamed["data"] = []
    streamed["curveCount"] = 0
    dense = data["m_DenseClip"]
    dense.update({
        "m_FrameCount": 0, "m_CurveCount": 0, "m_SampleRate": 0.0,
        "m_BeginTime": 0.0, "m_SampleArray": [], "m_SampleOptArray": [],
        "m_SampleCurveInfoArray": [],
    })
    data["m_ConstantClip"]["data"] = []
    raw = data["m_RawClip"]
    for key in (
        "m_QuatKeyframeData", "m_CompressedQuatKeyframeData",
        "m_Vec3KeyframeData", "m_FloatKeyframeData", "m_CurveInfoArray",
    ):
        raw[key] = []
    for key in (
        "m_TotalTrackCount", "rotationCurveCount", "eulerCurveCount",
        "positionCurveCount", "scaleCurveCount", "genericCurveCount",
    ):
        raw[key] = 0


def _subtree_serialized_size(obj, tree: dict, field_name: str) -> int:
    nodes = obj.get_typetree_nodes()
    index = next(
        i for i, node in enumerate(nodes)
        if node.m_Level == 1 and node.m_Name == field_name
    )
    subtree = TypeTreeHelper.get_nodes(nodes, index)
    writer = EndianBinaryWriter(endian=obj.reader.endian)
    TypeTreeHelper.write_value(tree[field_name], subtree, writer, c_uint32(0))
    return len(writer.bytes)


def _component_values(value, names: str) -> Tuple[float, ...]:
    return tuple(float(getattr(value, name)) for name in names)


def replace_animation_from_fbx(
    project: AnimationProjectIndex,
    animation_file_index: int,
    animation_path_id: int,
    model: ModelCandidate,
    fbx_path: str,
    sample_rate: Optional[float] = None,
) -> dict:
    """Replace only the selected AnimationClip's transform curve payload.

    The FBX armature and geometry are used solely to identify animated nodes.
    They are never serialized into the bundle, so the target AnimationClip
    keeps its original PathID and all unrelated Unity metadata.
    """
    import ufbx

    target_obj = project.object(animation_file_index, animation_path_id)
    if target_obj is None or target_obj.type.name != "AnimationClip":
        raise ValueError("The selected Unity object is not an AnimationClip")
    original_path_id = int(target_obj.path_id)
    tree = deepcopy(project.tree(animation_file_index, animation_path_id))
    rate = float(sample_rate or tree.get("m_SampleRate") or 30.0)
    rate = min(240.0, max(1.0, rate))

    target_names: Dict[str, List[int]] = defaultdict(list)
    for transform_id in model.transform_ids:
        target_names[
            project.transforms[model.file_index, transform_id].name
        ].append(transform_id)

    with ufbx.load_file(os.path.abspath(fbx_path)) as scene:
        if not scene.anim_stacks:
            raise ValueError("The FBX does not contain an animation stack")
        stack = scene.anim_stacks[0]
        begin = float(stack.time_begin)
        end = float(stack.time_end)
        duration = max(0.0, end - begin)
        if duration <= 0.0:
            raise ValueError("The FBX animation has no playable duration")

        scene_nodes = []
        for index in range(len(scene.nodes)):
            node = scene.nodes[index]
            scene_nodes.append((int(node.element_id), str(node.name), node))
        baked = ufbx.bake_anim(
            scene, stack.anim, resample_rate=rate,
            key_reduction_enabled=False, bake_transform_props=True,
        )
        try:
            animated_ids = {
                int(baked.nodes[index].element_id)
                for index in range(len(baked.nodes))
            }
        finally:
            baked.free()

        used_targets: Set[int] = set()
        animated_pairs = []
        ambiguous_names = []
        for element_id, node_name, node in scene_nodes:
            if element_id not in animated_ids:
                continue
            simple_name = node_name.rsplit("|", 1)[-1].rsplit(":", 1)[-1]
            choices = [
                transform_id for transform_id in target_names.get(simple_name, [])
                if transform_id not in used_targets
            ]
            if not choices:
                continue
            if len(choices) > 1:
                ambiguous_names.append(simple_name)
            transform_id = choices[0]
            used_targets.add(transform_id)
            animated_pairs.append((transform_id, node))

        if not animated_pairs:
            raise ValueError(
                "No animated FBX node names match the selected model hierarchy"
            )
        order = {path_id: index for index, path_id in enumerate(model.transform_ids)}
        animated_pairs.sort(key=lambda pair: order[pair[0]])
        frame_count = max(2, int(math.ceil(duration * rate)) + 1)
        curve_count = len(animated_pairs) * 10
        dense_values: List[float] = []
        curve_values: List[List[float]] = [[] for _ in range(curve_count)]
        previous_rotations: Dict[int, Tuple[float, float, float, float]] = {}

        for frame_index in range(frame_count):
            time_value = min(end, begin + frame_index / rate)
            frame_values: List[float] = []
            for transform_id, node in animated_pairs:
                transform = node.evaluate_transform(stack.anim, time_value)
                tx, ty, tz = _component_values(transform.translation, "xyz")
                qx, qy, qz, qw = quaternion_normalize(
                    _component_values(transform.rotation, "xyzw")
                )
                unity_rotation = (qx, -qy, -qz, qw)
                previous = previous_rotations.get(transform_id)
                if previous and sum(a * b for a, b in zip(previous, unity_rotation)) < 0.0:
                    unity_rotation = tuple(-value for value in unity_rotation)
                previous_rotations[transform_id] = unity_rotation
                sx, sy, sz = _component_values(transform.scale, "xyz")
                frame_values.extend((
                    -tx, ty, tz,
                    unity_rotation[0], unity_rotation[1],
                    unity_rotation[2], unity_rotation[3],
                    sx, sy, sz,
                ))
            dense_values.extend(frame_values)
            for index, value in enumerate(frame_values):
                curve_values[index].append(float(value))

        # Keep only Unity Transform IDs after sampling.  ufbx node wrappers must
        # be released before the owning scene closes (ufbx 0.0.5 otherwise can
        # report a native-process failure during interpreter shutdown).
        animated_transform_ids = [
            transform_id for transform_id, _node in animated_pairs
        ]
        animated_node_count = len(animated_transform_ids)
        animated_pairs.clear()
        scene_nodes.clear()
        node = None
        transform = None
        stack = None
        baked = None

    root_path = project.transforms[model.file_index, model.transform_id].full_path
    generic_bindings = []
    for transform_id in animated_transform_ids:
        full_path = project.transforms[model.file_index, transform_id].full_path
        relative_path = full_path[len(root_path):].lstrip("/")
        path_hash = unity_crc32(relative_path) if relative_path else 0
        for attribute in (1, 2, 3):
            generic_bindings.append({
                "path": path_hash,
                "attribute": attribute,
                "script": {"m_FileID": 0, "m_PathID": 0},
                "typeID": 4,
                "customType": 0,
                "isPPtrCurve": 0,
                "isIntCurve": 0,
                "isSerializeReferenceCurve": 0,
            })

    for key in (
        "m_RotationCurves", "m_CompressedRotationCurves", "m_EulerCurves",
        "m_PositionCurves", "m_ScaleCurves", "m_FloatCurves", "m_PPtrCurves",
    ):
        tree[key] = []
    tree["m_Legacy"] = False
    tree["m_Compressed"] = False
    tree["m_SampleRate"] = rate
    tree["m_SelfClipSize"] = 0
    _empty_optimized_clip(tree["m_SelfClip"], duration)
    tree["m_CompressedBindings"].update({
        "bindings": [], "pathPartIndices": [], "strTable": [],
    })

    muscle = tree["m_MuscleClip"]
    muscle["m_StartTime"] = 0.0
    muscle["m_StopTime"] = float(duration)
    data = muscle["m_Clip"]["data"]
    data["m_StreamedClip"].update({"data": [], "curveCount": 0})
    data["m_ConstantClip"]["data"] = []
    dense = data["m_DenseClip"]
    dense.update({
        "m_FrameCount": frame_count,
        "m_CurveCount": curve_count,
        "m_SampleRate": rate,
        "m_BeginTime": 0.0,
        "m_SampleArray": dense_values,
        "m_SampleOptArray": [],
        "m_SampleCurveInfoArray": [],
    })
    raw = data["m_RawClip"]
    for key in (
        "m_QuatKeyframeData", "m_CompressedQuatKeyframeData",
        "m_Vec3KeyframeData", "m_FloatKeyframeData", "m_CurveInfoArray",
    ):
        raw[key] = []
    for key in (
        "m_TotalTrackCount", "rotationCurveCount", "eulerCurveCount",
        "positionCurveCount", "scaleCurveCount", "genericCurveCount",
    ):
        raw[key] = 0
    muscle["m_ValueArrayDelta"] = [
        {"m_Start": min(values), "m_Stop": max(values)}
        for values in curve_values
    ]
    muscle["m_ValueArrayReferencePose"] = []
    tree["m_ClipBindingConstant"]["genericBindings"] = generic_bindings
    tree["m_ClipBindingConstant"]["pptrCurveMapping"] = []
    tree["m_HasGenericRootTransform"] = any(
        transform_id == model.transform_id for transform_id in animated_transform_ids
    )
    tree["m_HasMotionFloatCurves"] = False
    tree["m_MuscleClipSize"] = _subtree_serialized_size(
        target_obj, tree, "m_MuscleClip"
    )

    serialized = target_obj.save_typetree(tree)
    if int(target_obj.path_id) != original_path_id:
        raise RuntimeError("Animation PathID changed during serialization")
    verified = target_obj.read_typetree()
    verified_dense = verified["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]
    if (
        int(verified_dense["m_FrameCount"]) != frame_count
        or int(verified_dense["m_CurveCount"]) != curve_count
        or len(verified_dense["m_SampleArray"]) != len(dense_values)
    ):
        raise RuntimeError("AnimationClip typetree reload validation failed")
    project.type_trees[(animation_file_index, animation_path_id)] = verified
    return {
        "name": str(tree["m_Name"]),
        "path_id": original_path_id,
        "duration": duration,
        "sample_rate": rate,
        "frame_count": frame_count,
        "animated_nodes": animated_node_count,
        "curve_count": curve_count,
        "serialized_bytes": len(serialized),
        "ambiguous_names": sorted(set(ambiguous_names)),
    }
