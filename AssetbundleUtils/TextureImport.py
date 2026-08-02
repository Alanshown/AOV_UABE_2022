# -*- coding: utf-8 -*-
"""Compatibility-first Texture2D replacement helpers."""

from __future__ import annotations

from io import BytesIO
import math
import os
from typing import Dict

from PIL import Image

from AssetbundleUtils.UnityPy_AOV.enums import TextureFormat
from AssetbundleUtils.UnityPy_AOV.export import Texture2DConverter
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryReader


def texture_preview_png(texture) -> bytes:
    """Render the current in-memory Texture2D, including edited .resS bytes.

    Preview workers intentionally reload the source bundle in another process.
    That isolation keeps the GUI responsive, but it also means they cannot see
    an unsaved external resource-stream edit.  Passing this PNG as an explicit
    preview override keeps the visible Texture2D synchronized with the bytes
    that will be written during rebuild.
    """

    image = Texture2DConverter.get_image_from_texture2d(
        texture, True
    ).convert("RGBA")
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()


def _mipmap_count(texture) -> int:
    if texture.version[:2] < (5, 2):
        return 1
    return max(1, int(getattr(texture, "m_MipCount", 1)))


def _replace_streamed_texture_data(texture, image_data: bytes) -> bool:
    """Replace an existing .resS range without changing its routing metadata."""

    stream = getattr(texture, "m_StreamData", None)
    if stream is None or not stream.path:
        return False
    if len(image_data) != int(stream.size):
        raise ValueError(
            "Encoded texture size changed inside a shared .resS file: "
            f"{len(image_data)} != {stream.size}. Repacking shared resource "
            "offsets is required, so this import was cancelled."
        )

    bundle = texture.assets_file.parent
    resource_name = os.path.basename(stream.path)
    resource_key = next(
        (
            key
            for key in bundle.files
            if key == stream.path or os.path.basename(key) == resource_name
        ),
        None,
    )
    if resource_key is None:
        raise FileNotFoundError(
            f"Texture stream resource is absent from the bundle: {stream.path}"
        )

    resource = bundle.files[resource_key]
    original = bytes(resource.bytes)
    start = int(stream.offset)
    end = start + int(stream.size)
    if start < 0 or end > len(original):
        raise ValueError(
            f"Texture .resS range [{start}, {end}) exceeds "
            f"{resource_name} ({len(original)} bytes)"
        )
    updated = original[:start] + image_data + original[end:]
    replacement = EndianBinaryReader(
        updated, endian=getattr(resource, "endian", ">")
    )
    replacement.flags = getattr(resource, "flags", 0)
    replacement.name = getattr(resource, "name", resource_key)
    bundle.files[resource_key] = replacement
    texture._image_data = image_data
    stream.size = len(image_data)
    return True


def _encode_levels(
    image: Image.Image, target_format: TextureFormat, mipmap_count: int
):
    encoded_levels = []
    level = image.convert("RGBA")
    width, height = level.size
    encoded_format = target_format
    for level_index in range(mipmap_count):
        encoded, encoded_format = Texture2DConverter.image_to_texture2d(
            level, target_format
        )
        if encoded_format != target_format:
            raise ValueError(
                f"Texture format {target_format.name} cannot be encoded "
                "by this build without changing the runtime format."
            )
        encoded_levels.append(encoded)
        if level_index + 1 >= mipmap_count:
            break
        width = max(1, width // 2)
        height = max(1, height // 2)
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        level = level.resize((width, height), resampling)
    return b"".join(encoded_levels), encoded_format


_BLOCK_LAYOUTS = {
    TextureFormat.DXT1: (4, 4, 8),
    TextureFormat.DXT5: (4, 4, 16),
    TextureFormat.ETC_RGB4: (4, 4, 8),
    TextureFormat.ETC2_RGB: (4, 4, 8),
    TextureFormat.ETC2_RGBA8: (4, 4, 16),
    TextureFormat.ASTC_RGB_4x4: (4, 4, 16),
    TextureFormat.ASTC_RGB_5x5: (5, 5, 16),
    TextureFormat.ASTC_RGB_6x6: (6, 6, 16),
    TextureFormat.ASTC_RGB_8x8: (8, 8, 16),
    TextureFormat.ASTC_RGB_10x10: (10, 10, 16),
    TextureFormat.ASTC_RGB_12x12: (12, 12, 16),
    TextureFormat.ASTC_RGBA_4x4: (4, 4, 16),
    TextureFormat.ASTC_RGBA_5x5: (5, 5, 16),
    TextureFormat.ASTC_RGBA_6x6: (6, 6, 16),
    TextureFormat.ASTC_RGBA_8x8: (8, 8, 16),
    TextureFormat.ASTC_RGBA_10x10: (10, 10, 16),
    TextureFormat.ASTC_RGBA_12x12: (12, 12, 16),
    TextureFormat.ASTC_HDR_4x4: (4, 4, 16),
    TextureFormat.ASTC_HDR_5x5: (5, 5, 16),
    TextureFormat.ASTC_HDR_6x6: (6, 6, 16),
    TextureFormat.ASTC_HDR_8x8: (8, 8, 16),
    TextureFormat.ASTC_HDR_10x10: (10, 10, 16),
    TextureFormat.ASTC_HDR_12x12: (12, 12, 16),
}

_PIXEL_LAYOUTS = {
    TextureFormat.Alpha8: 1,
    TextureFormat.RGB24: 3,
    TextureFormat.RGBA32: 4,
    TextureFormat.ARGB32: 4,
    TextureFormat.BGRA32: 4,
    TextureFormat.R8: 1,
}


def _mip_layouts(width: int, height: int, texture_format, mipmap_count: int):
    """Return byte-accurate Unity mip layouts for formats we can re-encode."""

    if texture_format in _BLOCK_LAYOUTS:
        block_width, block_height, block_bytes = _BLOCK_LAYOUTS[texture_format]
        mode = "block"
        unit = (block_width, block_height, block_bytes)
    elif texture_format in _PIXEL_LAYOUTS:
        mode = "pixel"
        unit = _PIXEL_LAYOUTS[texture_format]
    else:
        raise ValueError(
            f"Sprite slot replacement does not support {texture_format.name}. "
            "The operation was cancelled instead of changing the texture format."
        )

    layouts = []
    offset = 0
    level_width = max(1, int(width))
    level_height = max(1, int(height))
    for level_index in range(max(1, int(mipmap_count))):
        if mode == "block":
            block_width, block_height, block_bytes = unit
            columns = max(1, math.ceil(level_width / block_width))
            rows = max(1, math.ceil(level_height / block_height))
            size = columns * rows * block_bytes
            stride = columns * block_bytes
        else:
            columns = level_width
            rows = level_height
            size = level_width * level_height * unit
            stride = level_width * unit
        layouts.append({
            "level": level_index,
            "offset": offset,
            "size": size,
            "width": level_width,
            "height": level_height,
            "columns": columns,
            "rows": rows,
            "stride": stride,
            "mode": mode,
            "unit": unit,
        })
        offset += size
        level_width = max(1, level_width // 2)
        level_height = max(1, level_height // 2)
    return layouts


def replace_texture_region(
    texture,
    raw_region: Image.Image,
    box,
) -> Dict[str, object]:
    """Replace one atlas slot while preserving every unaffected encoded block.

    ``box`` uses the same unflipped coordinate space as SpriteRenderData's
    ``textureRect``.  Compressed textures are decoded only to prepare the new
    blocks; bytes outside blocks intersecting the slot remain bit-identical.
    """

    x, y, width, height = (int(round(value)) for value in box)
    expected_size = (int(texture.m_Width), int(texture.m_Height))
    if width <= 0 or height <= 0:
        raise ValueError("Sprite atlas slot has an empty rectangle")
    if x < 0 or y < 0 or x + width > expected_size[0] or y + height > expected_size[1]:
        raise ValueError(
            f"Sprite atlas slot [{x}, {y}, {width}, {height}] exceeds "
            f"Texture2D {expected_size[0]}x{expected_size[1]}"
        )
    if tuple(raw_region.size) != (width, height):
        raise ValueError(
            f"Prepared Sprite slot is {raw_region.width}x{raw_region.height}; "
            f"expected {width}x{height}"
        )

    target_format = texture.m_TextureFormat
    if "Crunched" in target_format.name:
        raise ValueError(
            f"{target_format.name} is a whole-texture Crunch stream and cannot "
            "be patched without rewriting neighboring Sprite data."
        )
    mipmap_count = _mipmap_count(texture)
    original_data = bytes(texture.image_data)
    layouts = _mip_layouts(
        expected_size[0], expected_size[1], target_format, mipmap_count
    )
    expected_bytes = sum(layout["size"] for layout in layouts)
    if expected_bytes != len(original_data):
        raise ValueError(
            f"Texture mip layout is not byte-compatible with a safe slot patch: "
            f"{expected_bytes} calculated bytes != {len(original_data)} stored bytes"
        )

    raw_atlas = Texture2DConverter.get_image_from_texture2d(
        texture, False
    ).convert("RGBA")
    if tuple(raw_atlas.size) != expected_size:
        raise ValueError("Decoded Texture2D dimensions do not match serialized metadata")
    # Sprite replacement is a destructive slot overwrite, not alpha
    # compositing.  Clear the complete atlas rectangle first so opaque or
    # semi-transparent pixels from a previously hand-painted Texture2D cannot
    # survive underneath transparent pixels in the imported Sprite.
    prepared_region = raw_region.convert("RGBA")
    slot_box = (x, y, x + width, y + height)
    raw_atlas.paste((0, 0, 0, 0), slot_box)
    raw_atlas.paste(prepared_region, (x, y))
    if raw_atlas.crop(slot_box).tobytes() != prepared_region.tobytes():
        raise AssertionError(
            "Texture2D Sprite slot clear-and-replace verification failed"
        )

    updated = bytearray(original_data)
    changed_blocks = 0
    changed_bytes = 0
    level_image = raw_atlas
    base_width, base_height = expected_size
    resampling = getattr(Image, "Resampling", Image).LANCZOS

    for layout in layouts:
        level_width = layout["width"]
        level_height = layout["height"]
        if tuple(level_image.size) != (level_width, level_height):
            level_image = level_image.resize(
                (level_width, level_height), resampling
            )
        encoded, encoded_format = Texture2DConverter.image_to_texture2d(
            level_image, target_format, flip=False
        )
        if encoded_format != target_format:
            raise ValueError(
                f"Texture format {target_format.name} cannot be encoded by "
                "this build without changing the runtime format."
            )
        if len(encoded) != layout["size"]:
            raise ValueError(
                f"Mip {layout['level']} encoded to {len(encoded)} bytes; "
                f"expected {layout['size']}"
            )

        left = max(0, math.floor(x * level_width / base_width))
        top = max(0, math.floor(y * level_height / base_height))
        right = min(
            level_width, math.ceil((x + width) * level_width / base_width)
        )
        bottom = min(
            level_height, math.ceil((y + height) * level_height / base_height)
        )
        if right <= left:
            right = min(level_width, left + 1)
        if bottom <= top:
            bottom = min(level_height, top + 1)

        level_offset = layout["offset"]
        if layout["mode"] == "block":
            block_width, block_height, block_bytes = layout["unit"]
            first_column = left // block_width
            last_column = math.ceil(right / block_width)
            first_row = top // block_height
            last_row = math.ceil(bottom / block_height)
            span = (last_column - first_column) * block_bytes
            for block_row in range(first_row, last_row):
                relative = block_row * layout["stride"] + first_column * block_bytes
                start = level_offset + relative
                updated[start:start + span] = encoded[relative:relative + span]
                changed_bytes += span
            changed_blocks += (
                (last_column - first_column) * (last_row - first_row)
            )
        else:
            bytes_per_pixel = layout["unit"]
            span = (right - left) * bytes_per_pixel
            for pixel_row in range(top, bottom):
                relative = (
                    pixel_row * layout["stride"] + left * bytes_per_pixel
                )
                start = level_offset + relative
                updated[start:start + span] = encoded[relative:relative + span]
                changed_bytes += span
            changed_blocks += (right - left) * (bottom - top)

        if layout["level"] + 1 < len(layouts):
            next_layout = layouts[layout["level"] + 1]
            level_image = level_image.resize(
                (next_layout["width"], next_layout["height"]), resampling
            )

    image_data = bytes(updated)
    streamed = _replace_streamed_texture_data(texture, image_data)
    if not streamed:
        texture.image_data = image_data
    texture.m_CompleteImageSize = len(image_data)
    if texture.version[:2] < (5, 2):
        texture.m_MipMap = mipmap_count > 1
    else:
        texture.m_MipCount = mipmap_count

    return {
        "width": expected_size[0],
        "height": expected_size[1],
        "format": target_format.name,
        "mipmaps": mipmap_count,
        "image_bytes": len(image_data),
        "storage": "external-stream" if streamed else "inline",
        "changed_blocks": changed_blocks,
        "changed_bytes": changed_bytes,
        "slot": (x, y, width, height),
        "overwrite_mode": "clear-then-replace",
        "slot_pixel_exact": True,
    }


def _resource_references(texture, resource_name: str):
    references = []
    for reader in texture.assets_file.objects.values():
        if reader.type.name != "Texture2D" or int(reader.path_id) == int(texture.path_id):
            continue
        try:
            candidate = reader.read(False)
        except Exception:
            continue
        stream = getattr(candidate, "m_StreamData", None)
        if stream and os.path.basename(stream.path) == resource_name:
            references.append((int(stream.offset), int(stream.size)))
    return references


def _externalize_texture_data(texture, image_data: bytes) -> str:
    """Store texture bytes in a bundle .resS and update StreamingInfo safely."""

    bundle = texture.assets_file.parent
    stream = texture.m_StreamData
    resource_name = os.path.basename(stream.path) if stream.path else ""
    if not resource_name:
        resource_name = next(
            (name for name in bundle.files if name.lower().endswith(".ress")),
            "",
        )
    cab_name = next(
        (
            name
            for name in bundle.files
            if not name.lower().endswith(
                (".ress", ".resource", ".config", ".xml", ".dat")
            )
        ),
        "",
    )
    if not resource_name:
        resource_name = f"{cab_name or 'CAB-UnityPy_Mod'}.resS"

    resource_key = next(
        (
            key
            for key in bundle.files
            if key == resource_name or os.path.basename(key) == resource_name
        ),
        resource_name,
    )
    resource = bundle.files.get(resource_key)
    resource_bytes = bytes(resource.bytes) if resource is not None else b""
    other_ranges = _resource_references(texture, resource_name)

    current_is_same_resource = (
        bool(stream.path) and os.path.basename(stream.path) == resource_name
    )
    current_start = int(stream.offset) if current_is_same_resource else 0
    current_end = current_start + int(stream.size) if current_is_same_resource else 0

    if current_is_same_resource and not other_ranges:
        offset = 0
        updated = image_data
    elif (
        current_is_same_resource
        and len(image_data) == int(stream.size)
        and current_end <= len(resource_bytes)
    ):
        offset = current_start
        updated = (
            resource_bytes[:current_start]
            + image_data
            + resource_bytes[current_end:]
        )
    elif not other_ranges:
        offset = 0
        updated = image_data
    else:
        padding = (-len(resource_bytes)) % 16
        offset = len(resource_bytes) + padding
        updated = resource_bytes + (b"\x00" * padding) + image_data

    replacement = EndianBinaryReader(
        updated, endian=getattr(resource, "endian", ">")
    )
    replacement.flags = getattr(resource, "flags", 0)
    replacement.name = getattr(resource, "name", resource_key)
    bundle.files[resource_key] = replacement

    stream.offset = offset
    stream.size = len(image_data)
    stream.path = f"archive:/{cab_name}/{resource_name}" if cab_name else resource_name
    texture._image_data = image_data
    return resource_key


def optimize_texture_runtime_storage(
    texture,
    target_format: TextureFormat = TextureFormat.ETC2_RGBA8,
    *,
    externalize: bool = True,
) -> Dict[str, object]:
    """Transcode an atlas texture to a compact GPU format and external .resS."""

    image = texture.image.convert("RGBA")
    mipmap_count = _mipmap_count(texture)
    image_data, encoded_format = _encode_levels(
        image, target_format, mipmap_count
    )
    resource = None
    if externalize and getattr(texture, "m_StreamData", None) is not None:
        resource = _externalize_texture_data(texture, image_data)
    else:
        texture.image_data = image_data
    texture.m_TextureFormat = encoded_format
    texture.m_CompleteImageSize = len(image_data)
    if texture.version[:2] < (5, 2):
        texture.m_MipMap = mipmap_count > 1
    else:
        texture.m_MipCount = mipmap_count
    return {
        "width": int(texture.m_Width),
        "height": int(texture.m_Height),
        "format": encoded_format.name,
        "mipmaps": mipmap_count,
        "image_bytes": len(image_data),
        "storage": "external-stream" if resource else "inline",
        "resource": resource,
    }


def replace_texture_image(texture, image: Image.Image) -> Dict[str, object]:
    """Replace pixels while preserving Unity runtime texture metadata.

    A SpriteAtlas relies on its Texture2D dimensions and encoded GPU format.
    Silently changing either can leave all PathIDs valid while producing a
    bundle that Unity cannot upload. Unsupported encoders therefore fail
    explicitly instead of falling back to RGBA32.
    """

    expected_size = (int(texture.m_Width), int(texture.m_Height))
    if tuple(image.size) != expected_size:
        raise ValueError(
            "Replacement image dimensions do not match the Texture2D: "
            f"{image.width}x{image.height} != "
            f"{expected_size[0]}x{expected_size[1]}. "
            "Resize the edited atlas to the exact original dimensions."
        )

    target_format = texture.m_TextureFormat
    mipmap_count = _mipmap_count(texture)
    image_data, encoded_format = _encode_levels(
        image, target_format, mipmap_count
    )
    streamed = _replace_streamed_texture_data(texture, image_data)
    if not streamed:
        texture.image_data = image_data
    texture.m_CompleteImageSize = len(image_data)
    texture.m_TextureFormat = target_format
    if texture.version[:2] < (5, 2):
        texture.m_MipMap = mipmap_count > 1
    else:
        texture.m_MipCount = mipmap_count

    return {
        "width": expected_size[0],
        "height": expected_size[1],
        "format": target_format.name,
        "mipmaps": mipmap_count,
        "image_bytes": len(image_data),
        "storage": "external-stream" if streamed else "inline",
    }


def texture_runtime_metadata(texture) -> Dict[str, object]:
    return {
        "width": int(texture.m_Width),
        "height": int(texture.m_Height),
        "format": texture.m_TextureFormat.name,
        "mipmaps": _mipmap_count(texture),
        "image_bytes": len(texture.image_data),
        "complete_image_size": int(texture.m_CompleteImageSize),
        "storage": (
            "external-stream"
            if getattr(getattr(texture, "m_StreamData", None), "path", "")
            else "inline"
        ),
    }


def validate_texture_roundtrip(texture, expected: Dict[str, object]) -> None:
    """Validate the fields Unity needs before uploading a rebuilt Texture2D."""

    actual = texture_runtime_metadata(texture)
    if actual != expected:
        raise ValueError(
            f"Texture2D reload metadata changed: expected {expected}, got {actual}"
        )
    if (
        actual["image_bytes"] > 0
        and int(texture.m_CompleteImageSize) != actual["image_bytes"]
    ):
        raise ValueError(
            "Texture2D m_CompleteImageSize does not match serialized image data"
        )
