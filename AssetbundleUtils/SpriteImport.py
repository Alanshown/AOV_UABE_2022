# -*- coding: utf-8 -*-
"""Safe Sprite replacement through its existing SpriteAtlas slot."""

from __future__ import annotations

from io import BytesIO
from typing import Dict, Optional, Tuple

from PIL import Image

from AssetbundleUtils.TextureImport import (
    replace_texture_region,
    texture_preview_png,
)
from AssetbundleUtils.UnityPy_AOV.enums import SpritePackingRotation


class SpriteProjectIndex:
    """Minimal cross-bundle PPtr index independent of animation parsing."""

    def __init__(self, environments):
        self.environments = list(environments)
        self.objects = [
            (
                {int(obj.path_id): obj for obj in environment.objects}
                if environment is not None else {}
            )
            for environment in self.environments
        ]
        self.serialized_file_indexes = {}
        self.last_resolution_error = None
        for file_index, objects in enumerate(self.objects):
            for obj in objects.values():
                name = str(getattr(obj.assets_file, "name", "")).lower()
                if name:
                    indexes = self.serialized_file_indexes.setdefault(name, [])
                    if int(file_index) not in indexes:
                        indexes.append(int(file_index))

    def object(self, file_index: int, path_id: int):
        if file_index < 0 or file_index >= len(self.objects):
            return None
        return self.objects[int(file_index)].get(int(path_id))

    def resolve_pptr(self, source_obj, source_file_index: int, pointer: dict):
        self.last_resolution_error = None
        path_id = int(pointer.get("m_PathID", 0))
        file_id = int(pointer.get("m_FileID", 0))
        if not path_id:
            return None, None
        if file_id == 0:
            return int(source_file_index), self.object(source_file_index, path_id)
        externals = getattr(source_obj.assets_file, "externals", ())
        if file_id - 1 < 0 or file_id - 1 >= len(externals):
            return None, None
        external_name = str(externals[file_id - 1].name).lower()
        candidates = self.serialized_file_indexes.get(external_name, ())
        matches = [
            index for index in candidates
            if self.object(index, path_id) is not None
        ]
        if not matches:
            return None, None
        if len(matches) > 1:
            self.last_resolution_error = (
                f"CAB {external_name} / PathID {path_id} exists in multiple "
                "loaded bundles. Remove duplicate package versions and retry."
            )
            return None, None
        target_index = matches[0]
        return target_index, self.object(target_index, path_id)


def _pointer_dict(pointer) -> dict:
    return {
        "m_FileID": int(getattr(pointer, "file_id", 0)),
        "m_PathID": int(getattr(pointer, "path_id", 0)),
    }


def _resolve_pointer(project, source_reader, source_file_index, pointer, expected_type):
    path_id = int(getattr(pointer, "path_id", 0))
    if not path_id:
        return None, None

    target_file_index = None
    target = None
    if project is not None:
        target_file_index, target = project.resolve_pptr(
            source_reader, int(source_file_index), _pointer_dict(pointer)
        )
    elif int(getattr(pointer, "file_id", 0)) == 0:
        target_file_index = int(source_file_index)
        target = source_reader.assets_file.objects.get(path_id)

    if target is None or target_file_index is None:
        external = getattr(pointer, "external_name", None) or "external AssetBundle"
        detail = getattr(project, "last_resolution_error", None)
        if detail:
            raise ValueError(detail)
        raise ValueError(
            f"Cannot resolve {expected_type} PathID {path_id} in {external}. "
            "Open the folder containing all referenced AssetBundles so the "
            "modified backing texture can be rebuilt safely."
        )
    if target.type.name != expected_type:
        raise ValueError(
            f"Sprite reference PathID {path_id} resolves to {target.type.name}, "
            f"not {expected_type}"
        )
    pointer._obj = target
    return int(target_file_index), target


def _find_atlas_by_tag(project, sprite, sprite_file_index):
    tags = tuple(str(tag) for tag in getattr(sprite, "m_AtlasTags", ()) if tag)
    if project is None or not tags:
        return None, None
    matches = []
    for file_index, objects in enumerate(project.objects):
        for reader in objects.values():
            if reader.type.name != "SpriteAtlas":
                continue
            try:
                atlas = reader.read(False)
            except Exception:
                continue
            if str(getattr(atlas, "m_Tag", "")) in tags:
                matches.append((int(file_index), reader))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"Sprite atlas tag {tags[0]!r} resolves to multiple SpriteAtlas assets"
        )
    return None, None


def resolve_sprite_backing(sprite_reader, sprite_file_index: int, project=None):
    """Resolve authoritative SpriteAtlas/Texture2D objects without name guessing."""

    if sprite_reader.type.name != "Sprite":
        raise ValueError("The selected asset is not a Sprite")
    sprite = sprite_reader.read(False)
    atlas_reader = None
    atlas_file_index = None
    render_data = getattr(sprite, "m_RD", None)
    atlas_pointer = getattr(sprite, "m_SpriteAtlas", None)

    if atlas_pointer is not None and int(getattr(atlas_pointer, "path_id", 0)):
        atlas_file_index, atlas_reader = _resolve_pointer(
            project, sprite_reader, sprite_file_index, atlas_pointer, "SpriteAtlas"
        )
    elif getattr(sprite, "m_AtlasTags", None):
        atlas_file_index, atlas_reader = _find_atlas_by_tag(
            project, sprite, sprite_file_index
        )

    render_source_reader = sprite_reader
    render_source_index = int(sprite_file_index)
    atlas = None
    if atlas_reader is not None:
        atlas = atlas_reader.read(False)
        key = getattr(sprite, "m_RenderDataKey", None)
        if key not in atlas.m_RenderDataMap:
            raise ValueError(
                f"Sprite render-data key {key!r} is absent from "
                f"SpriteAtlas PathID {atlas_reader.path_id}"
            )
        render_data = atlas.m_RenderDataMap[key]
        render_source_reader = atlas_reader
        render_source_index = atlas_file_index
        if atlas_pointer is not None:
            atlas_pointer._obj = atlas_reader

    if render_data is None:
        raise ValueError("Sprite has no render data")
    texture_file_index, texture_reader = _resolve_pointer(
        project, render_source_reader, render_source_index,
        render_data.texture, "Texture2D"
    )

    alpha_file_index = None
    alpha_reader = None
    alpha_pointer = getattr(render_data, "alphaTexture", None)
    if alpha_pointer is not None and int(getattr(alpha_pointer, "path_id", 0)):
        alpha_file_index, alpha_reader = _resolve_pointer(
            project, render_source_reader, render_source_index,
            alpha_pointer, "Texture2D"
        )

    rectangle = render_data.textureRect
    slot = (
        int(round(rectangle.x)),
        int(round(rectangle.y)),
        int(round(rectangle.width)),
        int(round(rectangle.height)),
    )
    if slot[2] <= 0 or slot[3] <= 0:
        raise ValueError(f"Sprite PathID {sprite_reader.path_id} has an empty atlas slot")

    return {
        "sprite": sprite,
        "sprite_reader": sprite_reader,
        "sprite_file_index": int(sprite_file_index),
        "atlas": atlas,
        "atlas_reader": atlas_reader,
        "atlas_file_index": atlas_file_index,
        "render_data": render_data,
        "texture_reader": texture_reader,
        "texture_file_index": texture_file_index,
        "alpha_reader": alpha_reader,
        "alpha_file_index": alpha_file_index,
        "slot": slot,
    }


def _logical_size(slot: Tuple[int, int, int, int], settings) -> Tuple[int, int]:
    width, height = slot[2], slot[3]
    if (
        int(getattr(settings, "packed", 0)) == 1
        and getattr(settings, "packingRotation", None)
        == SpritePackingRotation.kSPRRotate90
    ):
        return height, width
    return width, height


def fit_sprite_image(image: Image.Image, target_size: Tuple[int, int]):
    """Aspect-fit an image into the Sprite's existing logical rectangle."""

    source = image.convert("RGBA")
    target_width, target_height = (max(1, int(v)) for v in target_size)
    if source.size == (target_width, target_height):
        return source.copy(), False
    resampling = getattr(Image, "Resampling", Image).LANCZOS
    ratio = min(target_width / source.width, target_height / source.height)
    fitted_size = (
        max(1, min(target_width, int(round(source.width * ratio)))),
        max(1, min(target_height, int(round(source.height * ratio)))),
    )
    fitted = source.resize(fitted_size, resampling)
    canvas = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
    origin = (
        (target_width - fitted.width) // 2,
        (target_height - fitted.height) // 2,
    )
    canvas.alpha_composite(fitted, origin)
    return canvas, True


def _inverse_sprite_transform(image: Image.Image, settings) -> Image.Image:
    # SpriteHelper flips the final exported image after atlas unpacking.
    prepared = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if int(getattr(settings, "packed", 0)) != 1:
        return prepared
    rotation = getattr(settings, "packingRotation", SpritePackingRotation.kSPRNone)
    if rotation == SpritePackingRotation.kSPRFlipHorizontal:
        return prepared.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if rotation == SpritePackingRotation.kSPRFlipVertical:
        return prepared.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    if rotation == SpritePackingRotation.kSPRRotate180:
        return prepared.transpose(Image.Transpose.ROTATE_180)
    if rotation == SpritePackingRotation.kSPRRotate90:
        return prepared.transpose(Image.Transpose.ROTATE_90)
    return prepared


def _clear_image_caches(binding):
    files = {
        binding["sprite"].assets_file,
        binding["texture_reader"].assets_file,
    }
    if binding["alpha_reader"] is not None:
        files.add(binding["alpha_reader"].assets_file)
    for assets_file in files:
        cache = getattr(assets_file, "_cache", None)
        if cache is not None:
            cache.clear()


def render_resolved_sprite(binding) -> Image.Image:
    """Render through UnityPy after explicitly binding cross-bundle PPtrs."""

    sprite = binding["sprite"]
    if binding["atlas_reader"] is not None:
        sprite.m_SpriteAtlas._obj = binding["atlas_reader"]
    binding["render_data"].texture._obj = binding["texture_reader"]
    alpha_pointer = getattr(binding["render_data"], "alphaTexture", None)
    if alpha_pointer is not None and binding["alpha_reader"] is not None:
        alpha_pointer._obj = binding["alpha_reader"]
    _clear_image_caches(binding)
    return sprite.image.convert("RGBA").copy()


def replace_sprite_image(
    sprite_reader,
    sprite_file_index: int,
    project,
    image: Image.Image,
) -> Dict[str, object]:
    """Replace only the pixels inside a Sprite's existing atlas rectangle."""

    binding = resolve_sprite_backing(
        sprite_reader, int(sprite_file_index), project
    )
    render_data = binding["render_data"]
    logical_size = _logical_size(binding["slot"], render_data.settingsRaw)
    fitted, resized = fit_sprite_image(image, logical_size)
    packed = _inverse_sprite_transform(fitted, render_data.settingsRaw)
    if packed.size != binding["slot"][2:]:
        raise ValueError(
            f"Packed Sprite image became {packed.width}x{packed.height}; "
            f"atlas slot is {binding['slot'][2]}x{binding['slot'][3]}"
        )

    targets = []
    texture = binding["texture_reader"].read(False)
    texture_info = replace_texture_region(texture, packed, binding["slot"])
    texture.save()
    targets.append({
        "file_index": binding["texture_file_index"],
        "reader": binding["texture_reader"],
        "channel": "color",
        "info": texture_info,
    })

    if binding["alpha_reader"] is not None:
        alpha = packed.getchannel("A")
        alpha_region = Image.merge(
            "RGBA", (alpha, alpha, alpha, Image.new("L", alpha.size, 255))
        )
        alpha_texture = binding["alpha_reader"].read(False)
        alpha_info = replace_texture_region(
            alpha_texture, alpha_region, binding["slot"]
        )
        alpha_texture.save()
        targets.append({
            "file_index": binding["alpha_file_index"],
            "reader": binding["alpha_reader"],
            "channel": "alpha",
            "info": alpha_info,
        })

    # The preview process owns a separate disk-backed environment.  Capture
    # the authoritative in-memory Texture2D after every channel is patched so
    # selecting the atlas before save cannot silently show its old .resS data.
    for target in targets:
        target["preview_png"] = texture_preview_png(
            target["reader"].read(False)
        )

    preview = render_resolved_sprite(binding)
    output = BytesIO()
    preview.save(output, format="PNG", optimize=False)
    total_blocks = sum(item["info"]["changed_blocks"] for item in targets)
    total_bytes = sum(item["info"]["changed_bytes"] for item in targets)
    return {
        "sprite_file_index": int(sprite_file_index),
        "sprite_reader": sprite_reader,
        "sprite_path_id": int(sprite_reader.path_id),
        "sprite_name": str(getattr(binding["sprite"], "m_Name", "")),
        "atlas_path_id": (
            int(binding["atlas_reader"].path_id)
            if binding["atlas_reader"] is not None else 0
        ),
        "texture_path_id": int(binding["texture_reader"].path_id),
        "slot": binding["slot"],
        "logical_size": logical_size,
        "resized": resized,
        "format": texture_info["format"],
        "mipmaps": texture_info["mipmaps"],
        "changed_blocks": total_blocks,
        "changed_bytes": total_bytes,
        "targets": targets,
        "preview_png": output.getvalue(),
    }
