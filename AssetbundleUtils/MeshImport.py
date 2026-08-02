"""Safe OBJ-to-Unity Mesh replacement.

Only the serialized mesh payload is changed.  The ObjectReader, PathID,
container entry and every unrelated Mesh field remain attached to the same
object.  A same-vertex-count import patches the existing vertex streams in
place; topology-changing imports build a compact Unity 2022 vertex layout.
"""

from __future__ import annotations

from dataclasses import dataclass
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
                source_normals.append(_normalize((-x, y, z)))
            elif command in ("g", "usemtl"):
                select_group(" ".join(values))
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
    indices = [index for _name, values in submeshes for index in values]
    generated_normals = _compute_normals(vertices, indices)
    final_normals = [
        _normalize(value) if value is not None else generated_normals[index]
        for index, value in enumerate(normals)
    ]
    tangents = _compute_tangents(vertices, final_normals, uvs, indices)
    return ObjMesh(vertices, final_normals, uvs, tangents, submeshes)


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


def _patch_existing_vertex_streams(obj, tree: dict, parsed, mesh: ObjMesh) -> bool:
    vertex_data = tree.get("m_VertexData")
    if not isinstance(vertex_data, dict):
        return False
    channels = vertex_data.get("m_Channels")
    if not isinstance(channels, list) or len(channels) <= 4:
        return False
    parsed_data = getattr(parsed, "m_VertexData", None)
    if parsed_data is None or parsed_data.m_VertexCount != len(mesh.vertices):
        return False
    streams = getattr(parsed_data, "m_Streams", None)
    if not streams:
        parsed_data.GetStreams()
        streams = parsed_data.m_Streams
    raw = bytearray(bytes(parsed_data.m_DataSize))
    values_by_channel = {
        0: mesh.vertices,
        1: mesh.normals,
        2: mesh.tangents,
        4: mesh.uvs,
    }
    if not channels[0].get("dimension"):
        return False

    for channel_index, values in values_by_channel.items():
        if channel_index >= len(channels):
            continue
        channel = channels[channel_index]
        dimension = int(channel.get("dimension", 0))
        if dimension <= 0:
            continue
        vertex_format = int(channel.get("format", 0))
        stream_index = int(channel.get("stream", 0))
        stream = streams.get(stream_index)
        if stream is None:
            return False
        component_count = min(dimension, len(values[0]))
        for vertex_index, vector in enumerate(values):
            cursor = stream.offset + channel.get("offset", 0) + vertex_index * stream.stride
            for component in range(component_count):
                encoded = _pack_component(vector[component], vertex_format, obj.reader.endian)
                end = cursor + len(encoded)
                if cursor < 0 or end > len(raw):
                    return False
                raw[cursor:end] = encoded
                cursor = end

    vertex_data["m_VertexCount"] = len(mesh.vertices)
    vertex_data["m_DataSize"] = bytes(raw)
    return True


def _nearest_skin_weights(parsed, vertices: Sequence[Vec3]):
    """Map new vertices to the nearest old skin sample using a spatial grid."""

    old_count = int(getattr(parsed, "m_VertexCount", 0))
    old_flat = list(getattr(parsed, "m_Vertices", ()) or ())
    old_skin = list(getattr(parsed, "m_Skin", ()) or ())
    if old_count <= 0 or len(old_skin) != old_count or len(old_flat) < old_count * 3:
        return None
    components = len(old_flat) // old_count
    old_vertices = [
        tuple(old_flat[index * components : index * components + 3])
        for index in range(old_count)
    ]
    minimum = [min(value[axis] for value in old_vertices) for axis in range(3)]
    maximum = [max(value[axis] for value in old_vertices) for axis in range(3)]
    diagonal = math.sqrt(sum((maximum[axis] - minimum[axis]) ** 2 for axis in range(3)))
    cell_size = max(diagonal / max(8.0, old_count ** (1.0 / 3.0)), 1e-7)

    def cell(value):
        return tuple(math.floor((value[axis] - minimum[axis]) / cell_size) for axis in range(3))

    grid: Dict[Tuple[int, int, int], List[int]] = {}
    for index, value in enumerate(old_vertices):
        grid.setdefault(cell(value), []).append(index)

    mapped = []
    fallback_step = max(1, old_count // 4096)
    fallback = range(0, old_count, fallback_step)
    for value in vertices:
        origin = cell(value)
        candidates = []
        for radius in range(4):
            for x in range(origin[0] - radius, origin[0] + radius + 1):
                for y in range(origin[1] - radius, origin[1] + radius + 1):
                    for z in range(origin[2] - radius, origin[2] + radius + 1):
                        if radius and max(abs(x-origin[0]), abs(y-origin[1]), abs(z-origin[2])) != radius:
                            continue
                        candidates.extend(grid.get((x, y, z), ()))
            if candidates:
                break
        search = candidates or fallback
        nearest = min(
            search,
            key=lambda index: sum(
                (old_vertices[index][axis] - value[axis]) ** 2 for axis in range(3)
            ),
        )
        skin = old_skin[nearest]
        weights = [float(item) for item in skin.weight[:4]]
        total = sum(max(0.0, item) for item in weights)
        if total <= 1e-8:
            weights = [1.0, 0.0, 0.0, 0.0]
        else:
            weights = [max(0.0, item) / total for item in weights]
        indices = [max(0, int(item)) for item in skin.boneIndex[:4]]
        mapped.append((weights, indices))
    return mapped


def _rebuild_vertex_stream(tree: dict, mesh: ObjMesh, endian: str, parsed) -> bool:
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
    mapped_skin = _nearest_skin_weights(parsed, mesh.vertices)
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
    tree = obj.read_typetree()

    try:
        preserved = _patch_existing_vertex_streams(obj, tree, parsed, mesh)
        remapped_skin = False
        if not preserved:
            remapped_skin = _rebuild_vertex_stream(tree, mesh, obj.reader.endian, parsed)
        _write_indices_and_submeshes(tree, mesh, obj.reader.endian)
        tree["m_LocalAABB"] = _bounds(mesh.vertices)

        if isinstance(tree.get("m_CompressedMesh"), dict):
            _clear_packed_mesh(tree["m_CompressedMesh"])
        stream_data = tree.get("m_StreamData")
        if isinstance(stream_data, dict):
            stream_data.update({"offset": 0, "size": 0, "path": ""})
        cleared_shapes = _clear_blend_shapes(tree.get("m_Shapes")) if not preserved else False
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
        if not validated.export():
            raise ValueError("序列化后的 Mesh 无法重新导出预览")
    except Exception:
        obj.set_raw_data(original_raw)
        raise

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
    )
