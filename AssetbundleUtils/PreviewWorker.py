"""Isolated preview process workers.

Unity ObjectReaders share seekable streams and must not be parsed concurrently
from GUI threads. Each worker owns its own cached UnityPy environment, so fast
selection changes cannot race the editor's in-memory AssetBundle state.
"""

from __future__ import annotations

from io import BytesIO
from collections import OrderedDict
import multiprocessing
import queue


def preview_worker(
    request_queue, result_queue, worker_id: int, latest_generation=None
):
    from AssetbundleUtils.DependencyResolver import load_bundle_with_dependencies

    cached_path = None
    cached_environment = None
    cached_objects = {}
    cached_missing_dependencies = []
    cached_animation_paths = None
    cached_animation_project = None
    cached_effect_paths = None
    cached_effect_project = None
    payload_cache = OrderedDict()
    parent = multiprocessing.parent_process()

    def remember(cache_key, payload):
        payload_cache[cache_key] = payload
        payload_cache.move_to_end(cache_key)
        while len(payload_cache) > 2:
            payload_cache.popitem(last=False)

    def cancelled(generation):
        return (
            latest_generation is not None
            and int(latest_generation.value) != int(generation)
        )

    while True:
        try:
            task = request_queue.get(timeout=0.5)
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                break
            continue
        except (EOFError, OSError):
            break
        if task is None:
            break

        # Collapse queued pointer/keyboard selection changes to the newest task.
        # A second worker can still process the next request concurrently.
        should_stop = False
        while True:
            try:
                newer = request_queue.get_nowait()
            except queue.Empty:
                break
            if newer is None:
                # Leave a stop token for the peer worker if this process drained
                # more than one queue item during shutdown.
                request_queue.put(None)
                should_stop = True
                break
            task = newer
        if should_stop:
            break

        if isinstance(task, dict):
            generation = int(task["generation"])
            bundle_path = task.get("bundle_path", "")
            path_id = int(task.get("path_id", 0))
            asset_type = str(task.get("asset_type", ""))
            replacement_raw = task.get("replacement_raw")
        else:
            generation, bundle_path, path_id, asset_type, replacement_raw = task
        try:
            if isinstance(task, dict) and task.get("kind") == "image_override":
                payload = bytes(task.get("payload") or b"")
                if not payload:
                    raise ValueError("Image preview override is empty")
                result_queue.put(
                    (generation, "image", payload, None, worker_id)
                )
                continue
            if isinstance(task, dict) and task.get("kind") in ("material", "shader"):
                from AssetbundleUtils.EffectPipeline import EffectProjectIndex
                from AssetbundleUtils.MaterialPreview import (
                    build_material_preview_payload, build_shader_preview_payload,
                )

                effect_paths = tuple(task["paths"])
                if cached_effect_project is None or cached_effect_paths != effect_paths:
                    cached_effect_project = EffectProjectIndex(effect_paths)
                    cached_effect_paths = effect_paths
                builder = (
                    build_material_preview_payload
                    if task["kind"] == "material"
                    else build_shader_preview_payload
                )
                payload = builder(
                    cached_effect_project, int(task["file_index"]), path_id,
                    size=int(task.get("size", 720)),
                )
                result_queue.put((generation, task["kind"], payload, None, worker_id))
                continue
            if isinstance(task, dict) and task.get("kind") == "effect":
                from AssetbundleUtils.EffectPipeline import (
                    EffectProjectIndex, build_effect_preview_payload,
                )

                effect_paths = tuple(task["paths"])
                if cached_effect_project is None or cached_effect_paths != effect_paths:
                    cached_effect_project = EffectProjectIndex(effect_paths)
                    cached_effect_paths = effect_paths
                cache_key = (
                    "effect", effect_paths, task["root_id"],
                    task.get("animation_id"), int(task.get("max_frames", 96)),
                    float(task.get("frames_per_second", 24.0)),
                )
                payload = payload_cache.get(cache_key)
                if payload is None:
                    quick_key = ("effect_quick",) + cache_key[1:4]
                    quick_payload = payload_cache.get(quick_key)
                    if quick_payload is None:
                        quick_payload = build_effect_preview_payload(
                            cached_effect_project, task["root_id"],
                            max_frames=min(24, int(task.get("max_frames", 96))),
                            frames_per_second=min(
                                12.0, float(task.get("frames_per_second", 24.0))
                            ),
                            animation_id=task.get("animation_id"),
                            cancel_check=lambda: cancelled(generation),
                        )
                        quick_payload["metadata"]["preview_quality"] = "quick"
                        remember(quick_key, quick_payload)
                    if not cancelled(generation):
                        result_queue.put((
                            generation, "effect", quick_payload, None, worker_id
                        ))
                    if cancelled(generation):
                        continue
                    payload = build_effect_preview_payload(
                        cached_effect_project, task["root_id"],
                        max_frames=int(task.get("max_frames", 96)),
                        frames_per_second=float(
                            task.get("frames_per_second", 24.0)
                        ),
                        animation_id=task.get("animation_id"),
                        cancel_check=lambda: cancelled(generation),
                    )
                    payload["metadata"]["preview_quality"] = "full"
                    remember(cache_key, payload)
                if cancelled(generation):
                    continue
                result_queue.put((generation, "effect", payload, None, worker_id))
                continue
            if isinstance(task, dict) and task.get("kind") == "animation":
                from AssetbundleUtils.AnimationPipeline import (
                    AnimationProjectIndex, build_animation_preview_payload,
                )

                animation_paths = tuple(task["paths"])
                if (
                    cached_animation_project is None
                    or cached_animation_paths != animation_paths
                ):
                    cached_animation_project = AnimationProjectIndex(animation_paths)
                    cached_animation_paths = animation_paths
                animation_file_index = int(task["animation_file_index"])
                if replacement_raw is not None:
                    target = cached_animation_project.object(animation_file_index, path_id)
                    if target is None:
                        raise KeyError(f"AnimationClip PathID {path_id} was not found")
                    target.set_raw_data(replacement_raw)
                    cached_animation_project.type_trees.pop(
                        (animation_file_index, path_id), None
                    )
                model = cached_animation_project.find_model(
                    int(task["model_file_index"]), int(task["model_game_object_id"])
                )
                if model is None:
                    raise ValueError("animation_model_not_found")
                cache_key = (
                    "animation", animation_paths, animation_file_index, path_id,
                    int(task["model_file_index"]),
                    int(task["model_game_object_id"]),
                    bool(task.get("include_model", True)),
                    bool(task.get("include_attachments", True)),
                    hash(replacement_raw) if replacement_raw is not None else None,
                )
                payload = payload_cache.get(cache_key)
                if payload is None:
                    quick_key = ("animation_quick",) + cache_key[1:]
                    quick_payload = payload_cache.get(quick_key)
                    if quick_payload is None:
                        quick_payload = build_animation_preview_payload(
                            cached_animation_project, animation_file_index,
                            path_id, model,
                            max_frames=min(18, int(task.get("max_frames", 72))),
                            include_model=bool(task.get("include_model", True)),
                            include_attachments=bool(
                                task.get("include_attachments", True)
                            ),
                            cancel_check=lambda: cancelled(generation),
                        )
                        quick_payload["metadata"]["preview_quality"] = "quick"
                        remember(quick_key, quick_payload)
                    if not cancelled(generation):
                        result_queue.put((
                            generation, "animation", quick_payload, None, worker_id
                        ))
                    if cancelled(generation):
                        continue
                    payload = build_animation_preview_payload(
                        cached_animation_project, animation_file_index, path_id, model,
                        max_frames=int(task.get("max_frames", 72)),
                        include_model=bool(task.get("include_model", True)),
                        include_attachments=bool(task.get("include_attachments", True)),
                        cancel_check=lambda: cancelled(generation),
                    )
                    payload["metadata"]["preview_quality"] = "full"
                    remember(cache_key, payload)
                if cancelled(generation):
                    continue
                result_queue.put((generation, "animation", payload, None, worker_id))
                continue
            if cached_environment is None or cached_path != bundle_path:
                (
                    cached_environment,
                    cached_objects,
                    cached_missing_dependencies,
                ) = load_bundle_with_dependencies(bundle_path)
                cached_path = bundle_path
            obj = cached_objects.get(int(path_id))
            if obj is None:
                raise KeyError(f"PathID {path_id} was not found in {bundle_path}")
            if replacement_raw is not None:
                obj.set_raw_data(replacement_raw)

            kind = asset_type.lower()
            if kind in ("texture2d", "sprite"):
                data = obj.read(False)
                try:
                    image = data.image
                except AttributeError as exc:
                    if kind == "sprite" and cached_missing_dependencies:
                        missing = ", ".join(cached_missing_dependencies[:3])
                        raise RuntimeError(f"missing_dependency:{missing}") from exc
                    raise
                buffer = BytesIO()
                image.save(buffer, format="PNG", optimize=False)
                payload = buffer.getvalue()
                result_queue.put((generation, "image", payload, None, worker_id))
            elif kind == "mesh":
                data = obj.read(False)
                import numpy as np

                vertex_count = int(data.m_VertexCount)
                if vertex_count <= 0 or not data.m_Vertices or not data.m_Indices:
                    raise ValueError("Mesh has no previewable geometry")
                vertex_components = len(data.m_Vertices) // vertex_count
                vertices = np.asarray(data.m_Vertices, dtype=np.float32).reshape(
                    vertex_count, vertex_components
                )[:, :3].copy()
                vertices[:, 0] *= -1.0
                indices = np.asarray(data.m_Indices, dtype=np.uint32)
                indices = indices[: indices.size - (indices.size % 3)].reshape(-1, 3)
                indices = indices[:, ::-1].copy().reshape(-1)
                if len(data.m_Normals) >= vertex_count * 3:
                    normal_components = len(data.m_Normals) // vertex_count
                    normals = np.asarray(data.m_Normals, dtype=np.float32).reshape(
                        vertex_count, normal_components
                    )[:, :3].copy()
                    normals[:, 0] *= -1.0
                else:
                    normals = np.zeros_like(vertices, dtype=np.float32)
                    triangles = indices.reshape(-1, 3)
                    faces = np.cross(
                        vertices[triangles[:, 1]] - vertices[triangles[:, 0]],
                        vertices[triangles[:, 2]] - vertices[triangles[:, 0]],
                    )
                    for corner in range(3):
                        np.add.at(normals, triangles[:, corner], faces)
                    lengths = np.linalg.norm(normals, axis=1)
                    valid = lengths > 1e-7
                    normals[valid] /= lengths[valid, None]
                    normals[~valid] = (0.0, 1.0, 0.0)
                payload = (
                    vertices.tobytes(), indices.tobytes(), normals.tobytes(),
                    vertex_count, int(indices.size),
                )
                result_queue.put((generation, "mesh", payload, None, worker_id))
            else:
                result_queue.put(
                    (generation, "none", None, "preview_not_available", worker_id)
                )
        except Exception as exc:
            result_queue.put(
                (generation, "none", None, f"{type(exc).__name__}: {exc}", worker_id)
            )
