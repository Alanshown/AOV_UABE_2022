"""Safe OBJ-to-Unity Mesh replacement.

The ObjectReader, PathID and container entry retain their identity. Vertex
channels are transferred together using a geometry correspondence, including
skin/extra UV/color streams; a compact layout is only a fallback for packed-only
meshes. Renderer bounds are synchronized separately by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from AssetbundleUtils.TypeTreeImport import clone_typetree
import math
import os
import struct
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


Vec2 = Tuple[float, float]
Vec3 = Tuple[float, float, float]
Vec4 = Tuple[float, float, float, float]


@dataclass
class ObjMesh:
    vertices: List[Vec3]
    normals: List[Vec3]
    uvs: List[Vec2]
    tangents: List[Vec4]
    submeshes: List[Tuple[str, List[int]]]
    has_uvs: bool = True
    has_normals: bool = True

    @property
    def indices(self) -> List[int]:
        return [index for _name, values in self.submeshes for index in values]


@dataclass
class MeshImportResult:
    source_path: str
    path_id: int
    mesh_name: str
    vertex_count: int
    index_count: int
    submesh_count: int
    preserved_vertex_streams: bool
    remapped_skin_weights: bool
    cleared_blend_shapes: bool
    cleared_collision_data: bool
    approximate_mapping: bool = False

    @property
    def summary(self) -> str:
        if self.preserved_vertex_streams:
            mode = "保留原扩展顶点流"
        elif self.remapped_skin_weights:
            mode = "已重建顶点流并映射骨骼权重"
        else:
            mode = "已重建顶点流"
        return (
            f"{self.mesh_name} · {self.vertex_count:,} 顶点 · "
            f"{self.index_count:,} 索引 · {self.submesh_count} 子网格 · {mode}"
        )


def _normalize(value: Vec3, fallback: Vec3 = (0.0, 1.0, 0.0)) -> Vec3:
    length = math.sqrt(sum(component * component for component in value))
    if length <= 1e-12 or not math.isfinite(length):
        return fallback
    return tuple(component / length for component in value)  # type: ignore[return-value]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _resolve_index(text: str, count: int, kind: str, line_number: int) -> int:
    try:
        raw = int(text)
    except ValueError as exc:
        raise ValueError(f"OBJ 第 {line_number} 行的 {kind} 索引无效: {text!r}") from exc
    if raw == 0:
        raise ValueError(f"OBJ 第 {line_number} 行使用了无效的 0 索引")
    index = raw - 1 if raw > 0 else count + raw
    if not 0 <= index < count:
        raise ValueError(
            f"OBJ 第 {line_number} 行的 {kind} 索引 {raw} 超出范围 (共 {count})"
        )
    return index


def _compute_normals(vertices: Sequence[Vec3], indices: Sequence[int]) -> List[Vec3]:
    sums = [[0.0, 0.0, 0.0] for _ in vertices]
    for offset in range(0, len(indices), 3):
        a, b, c = indices[offset : offset + 3]
        edge1 = _sub(vertices[b], vertices[a])
        edge2 = _sub(vertices[c], vertices[a])
        face = _cross(edge1, edge2)
        for index in (a, b, c):
            sums[index][0] += face[0]
            sums[index][1] += face[1]
            sums[index][2] += face[2]
    return [_normalize(tuple(value)) for value in sums]


def _compute_tangents(
    vertices: Sequence[Vec3],
    normals: Sequence[Vec3],
    uvs: Sequence[Vec2],
    indices: Sequence[int],
) -> List[Vec4]:
    tan1 = [[0.0, 0.0, 0.0] for _ in vertices]
    tan2 = [[0.0, 0.0, 0.0] for _ in vertices]
    for offset in range(0, len(indices), 3):
        i1, i2, i3 = indices[offset : offset + 3]
        p1, p2, p3 = vertices[i1], vertices[i2], vertices[i3]
        w1, w2, w3 = uvs[i1], uvs[i2], uvs[i3]
        x1, x2 = _sub(p2, p1), _sub(p3, p1)
        s1, s2 = w2[0] - w1[0], w3[0] - w1[0]
        t1, t2 = w2[1] - w1[1], w3[1] - w1[1]
        denominator = s1 * t2 - s2 * t1
        if abs(denominator) <= 1e-12:
            continue
        r = 1.0 / denominator
        sdir = (
            (t2 * x1[0] - t1 * x2[0]) * r,
            (t2 * x1[1] - t1 * x2[1]) * r,
            (t2 * x1[2] - t1 * x2[2]) * r,
        )
        tdir = (
            (s1 * x2[0] - s2 * x1[0]) * r,
            (s1 * x2[1] - s2 * x1[1]) * r,
            (s1 * x2[2] - s2 * x1[2]) * r,
        )
        for index in (i1, i2, i3):
            for axis in range(3):
                tan1[index][axis] += sdir[axis]
                tan2[index][axis] += tdir[axis]

    result: List[Vec4] = []
    for index, normal in enumerate(normals):
        tangent = tan1[index]
        dot = sum(normal[axis] * tangent[axis] for axis in range(3))
        orthogonal = _normalize(
            tuple(tangent[axis] - normal[axis] * dot for axis in range(3)),
            fallback=_normalize(_cross((0.0, 0.0, 1.0), normal), (1.0, 0.0, 0.0)),
        )
        handedness = -1.0 if sum(
            _cross(normal, orthogonal)[axis] * tan2[index][axis]
            for axis in range(3)
        ) < 0.0 else 1.0
        result.append((orthogonal[0], orthogonal[1], orthogonal[2], handedness))
    return result


def parse_obj(path: str) -> ObjMesh:
    """Parse an OBJ and convert it back to Unity's handedness."""

    positions: List[Vec3] = []
    texcoords: List[Vec2] = []
    source_normals: List[Vec3] = []
    vertices: List[Vec3] = []
    uvs: List[Vec2] = []
    normals: List[Optional[Vec3]] = []
    lookup: Dict[Tuple[int, Optional[int], Optional[int]], int] = {}
    groups: Dict[str, List[int]] = {"default": []}
    group_order = ["default"]
    active_group = "default"
    object_group = "default"

    def select_group(name: str) -> None:
        nonlocal active_group
        clean = name.strip() or "default"
        if clean not in groups:
            groups[clean] = []
            group_order.append(clean)
        active_group = clean

    with open(path, "r", encoding="utf-8-sig", errors="replace") as handle:
        for line_number, raw_line in enumerate(handle, 1):
            line = raw_line.partition("#")[0].strip()
            if not line:
                continue
            parts = line.split()
            command, values = parts[0].lower(), parts[1:]
            if command == "v":
                if len(values) < 3:
                    raise ValueError(f"OBJ 第 {line_number} 行的顶点不足 3 个分量")
                x, y, z = map(float, values[:3])
                positions.append((-x, y, z))
            elif command == "vt":
                if len(values) < 2:
                    raise ValueError(f"OBJ 第 {line_number} 行的 UV 不足 2 个分量")
                texcoords.append((float(values[0]), float(values[1])))
            elif command == "vn":
                if len(values) < 3:
                    raise ValueError(f"OBJ 第 {line_number} 行的法线不足 3 个分量")
                x, y, z = map(float, values[:3])
                if not all(math.isfinite(value) for value in (x, y, z)):
                    raise ValueError("OBJ contains non-finite normals")
                source_normals.append(_normalize((-x, y, z)))
            elif command == "g":
                object_group = " ".join(values) or "default"
                select_group(object_group)
            elif command == "usemtl":
                active_material = " ".join(values)
                # Material names are labels, never external Unity references.
                select_group(f"{object_group}|{active_material}" if active_material else object_group)
            elif command == "f":
                if len(values) < 3:
                    raise ValueError(f"OBJ 第 {line_number} 行的面少于 3 个顶点")
                face: List[int] = []
                for token in values:
                    fields = token.split("/")
                    position_index = _resolve_index(
                        fields[0], len(positions), "顶点", line_number
                    )
                    uv_index = (
                        _resolve_index(fields[1], len(texcoords), "UV", line_number)
                        if len(fields) > 1 and fields[1]
                        else None
                    )
                    normal_index = (
                        _resolve_index(fields[2], len(source_normals), "法线", line_number)
                        if len(fields) > 2 and fields[2]
                        else None
                    )
                    key = (position_index, uv_index, normal_index)
                    if key not in lookup:
                        lookup[key] = len(vertices)
                        vertices.append(positions[position_index])
                        uvs.append(texcoords[uv_index] if uv_index is not None else (0.0, 0.0))
                        normals.append(
                            source_normals[normal_index] if normal_index is not None else None
                        )
                    face.append(lookup[key])
                # Unity uses the opposite winding from the exported OBJ.
                for corner in range(1, len(face) - 1):
                    groups[active_group].extend((face[corner + 1], face[corner], face[0]))

    if not vertices:
        raise ValueError("OBJ 中没有可导入的顶点")
    submeshes = [(name, groups[name]) for name in group_order if groups[name]]
    if not submeshes:
        raise ValueError("OBJ 中没有可导入的三角面")
    # OBJ faces encounter vertices in a different order than Unity's buffers.
    # Retain source position order (and split UV/normal seams deterministically).
    ordered = sorted(lookup, key=lambda key: (key[0], -1 if key[1] is None else key[1],
                                             -1 if key[2] is None else key[2]))
    old_order = [lookup[key] for key in ordered]
    remap = {old: new for new, old in enumerate(old_order)}
    vertices = [vertices[index] for index in old_order]
    uvs = [uvs[index] for index in old_order]
    normals = [normals[index] for index in old_order]
    submeshes = [(name, [remap[index] for index in values]) for name, values in submeshes]
    if not all(math.isfinite(value) for vectors in (vertices, uvs) for vector in vectors for value in vector):
        raise ValueError("OBJ contains non-finite vertex/UV data")
    if not all(math.isfinite(value) for vector in positions + source_normals for value in vector):
        raise ValueError("OBJ contains non-finite position/normal data")
    indices = [index for _name, values in submeshes for index in values]
    generated_normals = _compute_normals(vertices, indices)
    final_normals = [
        _normalize(value) if value is not None else generated_normals[index]
        for index, value in enumerate(normals)
    ]
    tangents = _compute_tangents(vertices, final_normals, uvs, indices)
    return ObjMesh(vertices, final_normals, uvs, tangents, submeshes,
                   bool(texcoords), bool(source_normals))


def _bounds(vertices: Sequence[Vec3], indices: Optional[Iterable[int]] = None) -> dict:
    selected = [vertices[index] for index in indices] if indices is not None else list(vertices)
    if not selected:
        selected = [(0.0, 0.0, 0.0)]
    minimum = [min(value[axis] for value in selected) for axis in range(3)]
    maximum = [max(value[axis] for value in selected) for axis in range(3)]
    center = [(minimum[axis] + maximum[axis]) * 0.5 for axis in range(3)]
    extent = [(maximum[axis] - minimum[axis]) * 0.5 for axis in range(3)]
    return {
        "m_Center": {"x": center[0], "y": center[1], "z": center[2]},
        "m_Extent": {"x": extent[0], "y": extent[1], "z": extent[2]},
    }


def _clear_packed_mesh(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in ("m_NumItems", "m_BitSize", "m_UVInfo"):
                value[key] = 0
            elif key in ("m_Range", "m_Start"):
                value[key] = 0.0
            elif key == "m_Data":
                value[key] = []
            else:
                _clear_packed_mesh(child)
    elif isinstance(value, list):
        value.clear()


def _clear_blend_shapes(value) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, list) and child:
                child.clear()
                changed = True
            elif isinstance(child, (dict, list)):
                changed = _clear_blend_shapes(child) or changed
    return changed


def _pack_component(value: float, vertex_format: int, endian: str) -> bytes:
    prefix = "<" if endian == "<" else ">"
    if vertex_format == 0:
        return struct.pack(prefix + "f", float(value))
    if vertex_format == 1:
        return struct.pack(prefix + "e", float(value))
    if vertex_format == 2:
        return bytes((round(max(0.0, min(1.0, value)) * 255.0),))
    if vertex_format == 3:
        encoded = max(-127, min(127, round(max(-1.0, min(1.0, value)) * 127.0)))
        return struct.pack("b", encoded)
    if vertex_format == 4:
        return struct.pack(prefix + "H", round(max(0.0, min(1.0, value)) * 65535.0))
    if vertex_format == 5:
        encoded = max(-32767, min(32767, round(max(-1.0, min(1.0, value)) * 32767.0)))
        return struct.pack(prefix + "h", encoded)
    raise ValueError(f"顶点通道格式 {vertex_format} 不支持浮点 Mesh 导入")


def _vertex_mapping(parsed, mesh: ObjMesh) -> Optional[List[int]]:
    count = int(getattr(parsed, "m_VertexCount", 0))
    flat = list(getattr(parsed, "m_Vertices", ()) or ())
    if count <= 0 or len(flat) < count * 3:
        return None
    components = len(flat) // count
    old = [tuple(flat[i * components:i * components + 3]) for i in range(count)]
    if not all(math.isfinite(v) for point in old for v in point):
        raise ValueError("Target Mesh contains non-finite positions")
    # Exact exported positions tolerate the OBJ exporter's seven-digit rounding.
    if len(mesh.vertices) == count and all(
        all(math.isclose(a, b, rel_tol=2e-6, abs_tol=1e-7) for a, b in zip(point, old[i]))
        for i, point in enumerate(mesh.vertices)
    ):
        return list(range(count))
    if len(mesh.vertices) == count and mesh.indices == list(getattr(parsed, "m_Indices", ())):
        offset = tuple(mesh.vertices[0][axis] - old[0][axis] for axis in range(3))
        if all(all(math.isclose(point[axis] - old[i][axis], offset[axis], rel_tol=2e-6, abs_tol=1e-6)
                   for axis in range(3)) for i, point in enumerate(mesh.vertices)):
            return list(range(count))
    # Nearest-neighbour transfer is explicit fallback for edited/third-party meshes.
    # Use a spatial index; resolve coincident seam vertices by their UVs.
    from scipy.spatial import cKDTree
    import numpy as np
    old_uv = list(getattr(parsed, "m_UV0", ()) or ())
    uv_components = len(old_uv) // count if old_uv else 0
    locator = cKDTree(old)
    distances, candidates = locator.query(mesh.vertices, k=min(8, count))
    distances = np.asarray(distances).reshape(len(mesh.vertices), -1)
    candidates = np.asarray(candidates).reshape(len(mesh.vertices), -1)
    mapping = []
    for i, point in enumerate(mesh.vertices):
        nearest_distance = float(distances[i, 0])
        choices = [int(candidate) for distance, candidate in zip(distances[i], candidates[i])
                   if math.isclose(float(distance), nearest_distance, rel_tol=1e-6, abs_tol=1e-7)]
        if mesh.has_uvs and uv_components >= 2:
            chosen = min(choices, key=lambda j: sum(
                (mesh.uvs[i][axis] - old_uv[j * uv_components + axis]) ** 2 for axis in range(2)))
        else:
            chosen = choices[0]
        mapping.append(chosen)
    return mapping


def _patch_existing_vertex_streams(obj, tree: dict, parsed, mesh: ObjMesh,
                                   mapping: Optional[List[int]] = None) -> bool:
    vertex_data = tree.get("m_VertexData")
    if not isinstance(vertex_data, dict):
        return False
    channels = vertex_data.get("m_Channels")
    parsed_data = getattr(parsed, "m_VertexData", None)
    if not isinstance(channels, list) or len(channels) <= 4 or parsed_data is None:
        return False
    if mapping is None:
        mapping = _vertex_mapping(parsed, mesh)
    if mapping is None or not channels[0].get("dimension"):
        return False
    # Packed-only skin cannot survive clearing m_CompressedMesh through this path.
    skin = getattr(parsed, "m_Skin", ())
    if skin and not any(ch.get("dimension") for ch in channels[12:14]):
        return False
    streams = getattr(parsed_data, "m_Streams", None)
    if not streams:
        parsed_data.GetStreams()
        streams = parsed_data.m_Streams
    source = bytes(parsed_data.m_DataSize)
    raw = bytearray()
    offsets = {}
    for stream_index, stream in sorted(streams.items()):
        offsets[stream_index] = len(raw)
        for old_index in mapping:
            start = stream.offset + old_index * stream.stride
            end = start + stream.stride
            if start < 0 or end > len(source):
                return False
            raw.extend(source[start:end])
        raw.extend(b"\0" * ((-len(raw)) % 16))
    values_by_channel = {0: mesh.vertices, 1: mesh.normals}
    if mesh.has_uvs:
        values_by_channel.update({2: mesh.tangents, 4: mesh.uvs})
    for channel_index, values in values_by_channel.items():
        channel = channels[channel_index]
        dimension = int(channel.get("dimension", 0)) & 0xF
        if dimension <= 0 or (channel_index == 1 and not mesh.has_normals and skin):
            continue
        stream_index = int(channel.get("stream", 0))
        stream = streams.get(stream_index)
        if stream is None:
            return False
        for vertex_index, vector in enumerate(values):
            cursor = offsets[stream_index] + int(channel.get("offset", 0)) + vertex_index * stream.stride
            for component in range(min(dimension, len(vector))):
                encoded = _pack_component(vector[component], int(channel.get("format", 0)), obj.reader.endian)
                end = cursor + len(encoded)
                if cursor < 0 or end > len(raw):
                    return False
                raw[cursor:end] = encoded
                cursor = end
    vertex_data["m_VertexCount"] = len(mesh.vertices)
    vertex_data["m_DataSize"] = bytes(raw)
    return True


def _rebuild_vertex_stream(tree: dict, mesh: ObjMesh, endian: str, parsed, mapping=None) -> bool:
    vertex_data = tree.get("m_VertexData")
    if not isinstance(vertex_data, dict):
        raise ValueError("目标 Mesh 没有可写入的 m_VertexData")
    channels = vertex_data.get("m_Channels")
    if not isinstance(channels, list) or len(channels) < 5:
        raise ValueError("目标 Mesh 的顶点通道布局不受支持")
    for channel in channels:
        channel.update({"stream": 0, "offset": 0, "format": 0, "dimension": 0})
    channels[0].update({"stream": 0, "offset": 0, "format": 0, "dimension": 3})
    channels[1].update({"stream": 0, "offset": 12, "format": 0, "dimension": 3})
    channels[2].update({"stream": 0, "offset": 24, "format": 0, "dimension": 4})
    channels[4].update({"stream": 0, "offset": 40, "format": 0, "dimension": 2})
    skin = list(getattr(parsed, "m_Skin", ()) or ())
    if mapping is not None and skin:
        mapped_skin = []
        for index in mapping:
            sample = skin[index]
            weights = [max(0.0, float(value)) for value in sample.weight[:4]]
            total = sum(weights)
            if total <= 1e-8:
                raise ValueError("Target Mesh has zero skin weights")
            mapped_skin.append(([value / total for value in weights], list(sample.boneIndex[:4])))
    else:
        mapped_skin = None
    if mapped_skin is not None and len(channels) > 13:
        channels[12].update({"stream": 0, "offset": 48, "format": 0, "dimension": 4})
        channels[13].update({"stream": 0, "offset": 64, "format": 10, "dimension": 4})
    else:
        mapped_skin = None
    prefix = "<" if endian == "<" else ">"
    raw = bytearray()
    for vertex_index, (position, normal, tangent, uv) in enumerate(zip(
        mesh.vertices, mesh.normals, mesh.tangents, mesh.uvs
    )):
        raw.extend(struct.pack(prefix + "3f3f4f2f", *position, *normal, *tangent, *uv))
        if mapped_skin is not None:
            weights, indices = mapped_skin[vertex_index]
            raw.extend(struct.pack(prefix + "4f4I", *weights, *indices))
    vertex_data["m_VertexCount"] = len(mesh.vertices)
    vertex_data["m_DataSize"] = bytes(raw)
    return mapped_skin is not None


def _write_indices_and_submeshes(tree: dict, mesh: ObjMesh, endian: str) -> None:
    index_size = 2 if len(mesh.vertices) <= 65535 else 4
    if index_size == 2 and mesh.indices and max(mesh.indices) > 65535:
        index_size = 4
    tree["m_IndexFormat"] = 0 if index_size == 2 else 1
    prefix = "<" if endian == "<" else ">"
    code = "H" if index_size == 2 else "I"
    all_indices = mesh.indices
    tree["m_IndexBuffer"] = struct.pack(prefix + code * len(all_indices), *all_indices)
    submeshes = []
    index_offset = 0
    for _name, indices in mesh.submeshes:
        unique = sorted(set(indices))
        first_vertex = unique[0] if unique else 0
        last_vertex = unique[-1] if unique else 0
        submeshes.append(
            {
                "firstByte": index_offset * index_size,
                "indexCount": len(indices),
                "topology": 0,
                "baseVertex": 0,
                "firstVertex": first_vertex,
                "vertexCount": last_vertex - first_vertex + 1 if unique else 0,
                "localAABB": _bounds(mesh.vertices, unique),
            }
        )
        index_offset += len(indices)
    tree["m_SubMeshes"] = submeshes


def replace_mesh_from_obj(obj, obj_path: str) -> MeshImportResult:
    """Replace a Mesh ObjectReader's geometry and validate the new payload."""

    if getattr(getattr(obj, "type", None), "name", None) != "Mesh":
        raise TypeError("只能把 OBJ 导入到 Mesh 资产")
    source_path = os.path.abspath(obj_path)
    mesh = parse_obj(source_path)
    original_path_id = obj.path_id
    original_raw = obj.get_raw_data()
    parsed = obj.read(False)
    original_name = getattr(parsed, "m_Name", None) or f"Mesh_{obj.path_id}"
    tree = clone_typetree(obj.read_typetree())

    try:
        mapping = _vertex_mapping(parsed, mesh)
        identity = mapping == list(range(int(parsed.m_VertexCount)))
        topology_preserved = identity and mesh.indices == list(parsed.m_Indices)
        variable_weights = tree.get("m_VariableBoneCountWeights", {})
        if not topology_preserved and isinstance(variable_weights, dict) and variable_weights.get("m_Data"):
            raise ValueError("Variable bone-count weights require a dedicated remapper; refusing to discard them")
        preserved = _patch_existing_vertex_streams(obj, tree, parsed, mesh, mapping)
        remapped_skin = bool(getattr(parsed, "m_Skin", ())) and not identity
        if not preserved:
            remapped_skin = _rebuild_vertex_stream(tree, mesh, obj.reader.endian, parsed, mapping)
        if not preserved and (tree.get("m_BindPose") or tree.get("m_BoneNameHashes")) and not remapped_skin:
            raise ValueError("Cannot recover target skin weights; refusing an unskinned replacement")
        _write_indices_and_submeshes(tree, mesh, obj.reader.endian)
        tree["m_LocalAABB"] = _bounds(mesh.vertices)
        tree["m_MeshCompression"] = 0
        if tree.get("m_BonesAABB"):
            _update_bone_bounds(tree, mesh, parsed, mapping)

        if isinstance(tree.get("m_CompressedMesh"), dict):
            _clear_packed_mesh(tree["m_CompressedMesh"])
        stream_data = tree.get("m_StreamData")
        if isinstance(stream_data, dict):
            stream_data.update({"offset": 0, "size": 0, "path": ""})
        cleared_shapes = _clear_blend_shapes(tree.get("m_Shapes")) if not topology_preserved else False
        cleared_collision = False
        for field in ("m_BakedConvexCollisionMesh", "m_BakedTriangleCollisionMesh"):
            if field in tree and tree[field]:
                tree[field] = b""
                cleared_collision = True

        obj.save_typetree(tree)
        if obj.path_id != original_path_id:
            raise AssertionError("Mesh PathID 在序列化过程中发生变化")
        validated = obj.read(False)
        if validated.m_VertexCount != len(mesh.vertices):
            raise ValueError("序列化后的顶点数与 OBJ 不一致")
        if len(validated.m_Indices) != len(mesh.indices):
            raise ValueError("序列化后的索引数与 OBJ 不一致")
        if len(validated.m_SubMeshes) != len(mesh.submeshes):
            raise ValueError("序列化后的子网格数与 OBJ 不一致")
        if validated.m_Indices and max(validated.m_Indices) >= validated.m_VertexCount:
            raise ValueError("序列化后的 Mesh 含有越界索引")
        validate_mesh_payload(obj)
        if not validated.export():
            raise ValueError("序列化后的 Mesh 无法重新导出预览")
    except Exception:
        obj.set_raw_data(original_raw)
        raise

    old_count = int(parsed.m_VertexCount)
    old_components = len(parsed.m_Vertices) // old_count
    approximate = bool(mapping is not None and not identity and any(
        any(not math.isclose(float(point[axis]), float(parsed.m_Vertices[old_index * old_components + axis]),
                             rel_tol=2e-6, abs_tol=1e-7) for axis in range(3))
        for point, old_index in zip(mesh.vertices, mapping)
    ))
    return MeshImportResult(
        source_path=source_path,
        path_id=original_path_id,
        mesh_name=original_name,
        vertex_count=len(mesh.vertices),
        index_count=len(mesh.indices),
        submesh_count=len(mesh.submeshes),
        preserved_vertex_streams=preserved,
        remapped_skin_weights=remapped_skin,
        cleared_blend_shapes=cleared_shapes,
        cleared_collision_data=cleared_collision,
        approximate_mapping=approximate,
    )


def _update_bone_bounds(tree, mesh, parsed, mapping):
    """Recompute each skin bone's bounds in the original bind-pose space."""
    poses = tree.get("m_BindPose", [])
    skin = list(getattr(parsed, "m_Skin", ()) or ())
    bounds = tree["m_BonesAABB"]
    if not poses or not skin or mapping is None:
        raise ValueError("Cannot reconstruct bone-space bounds without bind poses and weights")
    points = [[] for _ in bounds]
    for vertex, old_index in zip(mesh.vertices, mapping):
        weights = skin[old_index]
        for weight, bone in zip(weights.weight, weights.boneIndex):
            if weight <= 0:
                continue
            bone = int(bone)
            if not 0 <= bone < len(bounds) or bone >= len(poses):
                raise ValueError("Skin bone index exceeds bind pose/bone bounds")
            pose = poses[bone]
            point = tuple(sum(float(pose[f"e{row}{col}"]) * vertex[col] for col in range(3))
                          + float(pose[f"e{row}3"]) for row in range(3))
            points[bone].append(point)
    for i, values in enumerate(points):
        if values:
            bounds[i] = {
                "m_Min": dict(zip("xyz", [min(p[a] for p in values) for a in range(3)])),
                "m_Max": dict(zip("xyz", [max(p[a] for p in values) for a in range(3)])),
            }


def validate_mesh_payload(obj):
    """Runtime invariants beyond the viewer's ability to draw triangles."""
    mesh = obj.read(False)
    count = int(mesh.m_VertexCount)
    if count <= 0 or not mesh.m_Indices or max(mesh.m_Indices) >= count or min(mesh.m_Indices) < 0:
        raise ValueError(f"Mesh {obj.path_id} has empty/out-of-range geometry")
    if not all(math.isfinite(float(value)) for value in mesh.m_Vertices):
        raise ValueError(f"Mesh {obj.path_id} has non-finite positions")
    if len(mesh.m_Indices) % 3:
        raise ValueError("Mesh triangle index count is not divisible by three")
    skin = list(getattr(mesh, "m_Skin", ()) or ())
    tree = obj.read_typetree()
    poses = tree.get("m_BindPose", [])
    bounds = tree.get("m_LocalAABB", {})
    if bounds:
        center, extent = bounds["m_Center"], bounds["m_Extent"]
        if any(not math.isfinite(float(center[a])) or not math.isfinite(float(extent[a]))
               or extent[a] < 0 for a in "xyz"):
            raise ValueError("Mesh has invalid bounds")
    index_size = 2 if int(tree.get("m_IndexFormat", 0)) == 0 else 4
    for submesh in tree.get("m_SubMeshes", []):
        start, length = int(submesh["firstByte"]), int(submesh["indexCount"])
        if start < 0 or start % index_size or length <= 0 or start + length * index_size > len(tree["m_IndexBuffer"]):
            raise ValueError("Mesh submesh exceeds index buffer")
    if poses and len(skin) != count:
        raise ValueError(f"Mesh {obj.path_id} lost skin weights")
    for sample in skin:
        if any(not math.isfinite(float(w)) or w < 0 for w in sample.weight):
            raise ValueError("Mesh skin contains invalid weights")
        if sum(sample.weight) <= 1e-8:
            raise ValueError("Mesh skin contains zero-weight vertices")
        for weight, index in zip(sample.weight, sample.boneIndex):
            if weight > 0 and (int(index) < 0 or (poses and int(index) >= len(poses))):
                raise ValueError("Mesh skin contains out-of-range bone indices")
    vertex_data = getattr(mesh, "m_VertexData", None)
    if vertex_data is not None:
        for channel in vertex_data.m_Channels:
            if channel.dimension:
                stream = vertex_data.m_Streams[channel.stream]
                from AssetbundleUtils.UnityPy_AOV.classes.Mesh import MeshHelper
                size = MeshHelper.GetFormatSize(MeshHelper.ToVertexFormat(channel.format, obj.version))
                end = stream.offset + (count - 1) * stream.stride + channel.offset + channel.dimension * size
                if end > len(vertex_data.m_DataSize):
                    raise ValueError("Mesh vertex stream exceeds serialized payload")
    return mesh


def synchronize_mesh_renderer_bounds(environments, mesh_obj):
    """Expand referencing renderer bounds and validate existing material slots.

    Original materials/PPtrs are retained. OBJ MTL names never create dependencies.
    Returns changed renderer ObjectReaders for dirty tracking and rollback.
    """
    new_bounds = mesh_obj.read_typetree()["m_LocalAABB"]
    submesh_count = len(mesh_obj.read(False).m_SubMeshes)
    updates = []
    for file_index, environment in enumerate(environments):
        if environment is None:
            continue
        for renderer in environment.objects:
            if renderer.type.name != "SkinnedMeshRenderer":
                continue
            tree = renderer.read_typetree()
            pointer = tree.get("m_Mesh", {})
            if int(pointer.get("m_PathID", 0)) != int(mesh_obj.path_id):
                continue
            if int(pointer.get("m_FileID", 0)) == 0:
                matches = renderer.assets_file is mesh_obj.assets_file
            else:
                target = renderer.read(False).m_Mesh.get_obj()
                matches = target is not None and target.assets_file is mesh_obj.assets_file and target.path_id == mesh_obj.path_id
            if not matches:
                continue
            materials = tree.get("m_Materials", [])
            if materials and submesh_count > len(materials):
                raise ValueError("Imported Mesh has more submeshes than the target renderer's material slots")
            updated = clone_typetree(tree)
            changed_bounds = False
            for field_name in ("m_AABB", "m_LocalAABB"):
                original = tree.get(field_name)
                if not isinstance(original, dict) or "m_Center" not in original:
                    continue
                points = []
                for bounds in (original, new_bounds):
                    center, extent = bounds["m_Center"], bounds["m_Extent"]
                    points.extend(tuple(center[a] + sign * extent[a] for a in "xyz") for sign in (-1, 1))
                expanded = _bounds(points)
                if expanded != original:
                    updated[field_name] = expanded
                    changed_bounds = True
            if changed_bounds:
                updates.append((file_index, renderer, updated))
    changed = []
    try:
        for file_index, renderer, tree in updates:
            raw = renderer.get_raw_data()
            changed.append((file_index, renderer, raw))
            renderer.save_typetree(tree)
    except Exception:
        for _, renderer, raw in changed:
            renderer.set_raw_data(raw)
        raise
    return changed
