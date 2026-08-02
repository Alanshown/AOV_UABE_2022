"""Isolated high-quality material-ball previews for Unity Material and Shader assets.

Unity's serialized shader programs cannot be executed directly by desktop OpenGL.
The preview therefore reconstructs the material inputs and renders a deterministic
PBR approximation.  Raw shader programs remain untouched for .effect export.
"""

from __future__ import annotations

from io import BytesIO
import math
import re
from typing import Dict

import numpy as np
from PIL import Image


MAIN_TEXTURE_NAMES = (
    "_MainTex", "_BaseMap", "_BaseColorMap", "_Tex_Color", "_wenli01",
    "_Diffuse", "_Albedo", "_ColorMap",
)
NORMAL_TEXTURE_NAMES = (
    "_BumpMap", "_NormalMap", "_DetailNormalMap", "_NormalTex",
)
METALLIC_TEXTURE_NAMES = (
    "_MetallicGlossMap", "_MaskMap", "_SpecGlossMap", "_SpecularMap",
)
EMISSION_TEXTURE_NAMES = (
    "_EmissionMap", "_EmissiveMap", "_GlowMap", "_LightTex",
)


def _color(value, default=(1.0, 1.0, 1.0, 1.0)):
    if not isinstance(value, dict):
        return tuple(float(item) for item in default)
    return tuple(float(value.get(key, default[index])) for index, key in enumerate(
        ("r", "g", "b", "a")
    ))


def _shader_strings(obj):
    try:
        raw = bytes(obj.get_raw_data())
    except Exception:
        return []
    values = []
    seen = set()
    for match in re.findall(rb"[ -~]{4,}", raw):
        value = match.decode("utf-8", errors="ignore").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _shader_profile(strings, property_names):
    text = " ".join((*strings, *property_names)).casefold()
    if "particle" in text or "_tintcolor" in text or "_softparticles" in text:
        family = "Particle / translucent"
    elif "unlit" in text or "_emission_qiangdu" in text or "_lighttex" in text:
        family = "Unlit / emissive"
    elif "metallic" in text or "smoothness" in text or "specular" in text:
        family = "Lit / PBR"
    else:
        family = "Custom lit"
    features = []
    for token, label in (
        ("normal", "normal map"), ("bump", "normal map"),
        ("emission", "emission"), ("dissolve", "dissolve"),
        ("fresnel", "fresnel"), ("_fnl_", "fresnel"),
        ("alphatest", "alpha test"), ("_cutoff", "alpha test"),
        ("blend", "blending"), ("cull", "culling"),
    ):
        if token in text and label not in features:
            features.append(label)
    return family, features


def _resolve_texture(index, material_obj, file_index, environment):
    pointer = environment.get("m_Texture", {}) if isinstance(environment, dict) else {}
    target_file_index, texture = index.resolve_pptr(material_obj, file_index, pointer)
    if texture is None or texture.type.name != "Texture2D":
        return None
    try:
        image = texture.read(False).image.convert("RGBA")
    except Exception:
        return None
    scale = environment.get("m_Scale", {})
    offset = environment.get("m_Offset", {})
    return {
        "name": index.object_name(target_file_index, texture),
        "image": image,
        "scale": (float(scale.get("x", 1.0)), float(scale.get("y", 1.0))),
        "offset": (float(offset.get("x", 0.0)), float(offset.get("y", 0.0))),
    }


def _pick_texture(textures, preferred, excluded=()):
    for name in preferred:
        if name in textures:
            return textures[name]
    for name, texture in textures.items():
        lowered = name.casefold()
        if not any(word in lowered for word in excluded):
            return texture
    return None


def _sample_texture(texture, u, v, default):
    if texture is None:
        return np.broadcast_to(np.asarray(default, dtype=np.float32), u.shape + (4,)).copy()
    image = np.asarray(texture["image"], dtype=np.float32) / 255.0
    scale_x, scale_y = texture["scale"]
    offset_x, offset_y = texture["offset"]
    mapped_u = np.mod(u * scale_x + offset_x, 1.0)
    mapped_v = np.mod(v * scale_y + offset_y, 1.0)
    source_x = mapped_u * (image.shape[1] - 1)
    source_y = (1.0 - mapped_v) * (image.shape[0] - 1)
    x0 = np.floor(source_x).astype(np.int32)
    y0 = np.floor(source_y).astype(np.int32)
    x1 = np.minimum(image.shape[1] - 1, x0 + 1)
    y1 = np.minimum(image.shape[0] - 1, y0 + 1)
    weight_x = (source_x - x0)[..., None]
    weight_y = (source_y - y0)[..., None]
    top = image[y0, x0] * (1.0 - weight_x) + image[y0, x1] * weight_x
    bottom = image[y1, x0] * (1.0 - weight_x) + image[y1, x1] * weight_x
    return top * (1.0 - weight_y) + bottom * weight_y


def _render_material_ball(parameters, size=720):
    """Render a supersampled material sphere with PBR-style direct/environment light."""
    size = max(384, min(1024, int(size)))
    yy, xx = np.mgrid[0:size, 0:size]
    nx = (xx - size * 0.5) / (size * 0.405)
    ny = -(yy - size * 0.47) / (size * 0.405)
    radius_squared = nx * nx + ny * ny
    mask = radius_squared <= 1.0
    nz = np.sqrt(np.maximum(0.0, 1.0 - radius_squared))
    normals = np.stack((nx, ny, nz), axis=-1).astype(np.float32)
    normals[~mask] = (0.0, 0.0, 1.0)

    u = np.mod(0.5 + np.arctan2(normals[..., 0], normals[..., 2]) / (2.0 * math.pi), 1.0)
    v = np.clip(0.5 - np.arcsin(np.clip(normals[..., 1], -1.0, 1.0)) / math.pi, 0.0, 1.0)
    albedo_sample = _sample_texture(
        parameters.get("albedo_texture"), u, v, parameters["base_color"]
    )
    albedo = np.clip(albedo_sample[..., :3] * np.asarray(parameters["base_color"][:3]), 0, 4)
    alpha = np.clip(albedo_sample[..., 3] * float(parameters["base_color"][3]), 0, 1)

    normal_sample = _sample_texture(
        parameters.get("normal_texture"), u, v, (0.5, 0.5, 1.0, 1.0)
    )[..., :3]
    if parameters.get("normal_texture") is not None:
        tangent_normal = normal_sample * 2.0 - 1.0
        strength = float(parameters.get("normal_strength", 1.0))
        normals[..., 0] += tangent_normal[..., 0] * 0.32 * strength
        normals[..., 1] += tangent_normal[..., 1] * 0.32 * strength
        normals[..., 2] *= np.clip(tangent_normal[..., 2], 0.1, 1.0)
        normals /= np.maximum(np.linalg.norm(normals, axis=-1, keepdims=True), 1e-6)

    metallic = np.full(u.shape, float(parameters["metallic"]), dtype=np.float32)
    smoothness = np.full(u.shape, float(parameters["smoothness"]), dtype=np.float32)
    metal_sample = _sample_texture(
        parameters.get("metallic_texture"), u, v, (1.0, 1.0, 1.0, 1.0)
    )
    if parameters.get("metallic_texture") is not None:
        metallic *= metal_sample[..., 0]
        smoothness *= metal_sample[..., 3]
    metallic = np.clip(metallic, 0, 1)
    smoothness = np.clip(smoothness, 0.02, 0.98)

    light = np.asarray((-0.42, 0.62, 0.66), dtype=np.float32)
    light /= np.linalg.norm(light)
    view = np.asarray((0.0, 0.0, 1.0), dtype=np.float32)
    half_vector = light + view
    half_vector /= np.linalg.norm(half_vector)
    ndotl = np.clip(np.sum(normals * light, axis=-1), 0, 1)
    ndoth = np.clip(np.sum(normals * half_vector, axis=-1), 0, 1)
    ndotv = np.clip(normals[..., 2], 0, 1)
    roughness = 1.0 - smoothness
    exponent = 5.0 + smoothness * smoothness * 250.0
    specular_lobe = np.power(ndoth, exponent) * (0.22 + smoothness * 1.3)
    dielectric_f0 = 0.04
    f0 = dielectric_f0 * (1.0 - metallic[..., None]) + albedo * metallic[..., None]
    fresnel = f0 + (1.0 - f0) * np.power(1.0 - ndotv[..., None], 5.0)

    sky = np.asarray((0.34, 0.45, 0.62), dtype=np.float32)
    ground = np.asarray((0.075, 0.085, 0.105), dtype=np.float32)
    environment = ground + (sky - ground) * np.clip(normals[..., 1:2] * 0.5 + 0.58, 0, 1)
    diffuse = albedo * (1.0 - metallic[..., None]) * (0.22 + ndotl[..., None] * 0.78)
    reflection = environment * fresnel * (0.25 + smoothness[..., None] * 0.95)
    specular = fresnel * specular_lobe[..., None] * 1.8
    rim = np.power(1.0 - ndotv, 3.2)[..., None] * sky * 0.22

    emission_sample = _sample_texture(
        parameters.get("emission_texture"), u, v, (1.0, 1.0, 1.0, 1.0)
    )[..., :3]
    emission_color = np.asarray(parameters["emission_color"][:3], dtype=np.float32)
    emission = emission_sample * emission_color * float(parameters["emission_strength"])
    shaded = diffuse + reflection + specular + rim + emission
    shaded = shaded / (1.0 + shaded)
    shaded = np.power(np.clip(shaded, 0, 1), 1.0 / 2.2)

    top = np.asarray((0.105, 0.13, 0.18), dtype=np.float32)
    bottom = np.asarray((0.035, 0.045, 0.065), dtype=np.float32)
    gradient = yy[..., None] / max(1, size - 1)
    canvas = top * (1.0 - gradient) + bottom * gradient
    canvas = np.broadcast_to(canvas, (size, size, 3)).copy()
    shadow_center_x, shadow_center_y = size * 0.53, size * 0.875
    shadow = np.exp(-(
        ((xx - shadow_center_x) / (size * 0.26)) ** 2
        + ((yy - shadow_center_y) / (size * 0.055)) ** 2
    ) * 2.2)
    canvas *= (1.0 - shadow[..., None] * 0.48)

    edge = np.clip((1.0 - radius_squared) * size * 0.32, 0, 1)
    if parameters.get("alpha_test", False):
        alpha = (alpha >= float(parameters.get("cutoff", 0.5))).astype(np.float32)
    sphere_alpha = edge * alpha * mask
    canvas = canvas * (1.0 - sphere_alpha[..., None]) + shaded * sphere_alpha[..., None]
    image = Image.fromarray(np.uint8(np.clip(canvas * 255.0, 0, 255)), "RGB")
    return image


def _material_parameters(index, file_index, material_obj):
    tree = index.tree(file_index, int(material_obj.path_id))
    saved = tree.get("m_SavedProperties", {})
    colors = dict(saved.get("m_Colors", []))
    floats = dict(saved.get("m_Floats", []))
    texture_environments = dict(saved.get("m_TexEnvs", []))
    textures: Dict[str, dict] = {}
    for name, environment in texture_environments.items():
        texture = _resolve_texture(index, material_obj, file_index, environment)
        if texture is not None:
            textures[str(name)] = texture

    base_color = next((
        _color(colors[name]) for name in (
            "_Color", "_TintColor", "_Tex_Color", "_MainTexColor", "_BaseColor"
        ) if name in colors
    ), (1.0, 1.0, 1.0, 1.0))
    emission_color = next((
        _color(colors[name], (0.0, 0.0, 0.0, 1.0)) for name in (
            "_EmissionColor", "_Emission_Color", "_EmissiveColor", "_Fnl_Color"
        ) if name in colors
    ), (0.0, 0.0, 0.0, 1.0))
    metallic = float(floats.get("_Metallic", floats.get("_Metalness", 0.0)))
    smoothness = float(floats.get(
        "_Glossiness", floats.get("_Smoothness", floats.get("_Shininess", 0.45))
    ))
    if "_Shininess" in floats:
        smoothness = min(1.0, max(0.0, smoothness * 2.0))
    emission_strength = float(floats.get(
        "_EmissionStrength", floats.get("_Emission_qiangdu", floats.get("_EmissionScaleUI", 1.0))
    ))
    render_queue = int(tree.get("m_CustomRenderQueue", -1))
    property_names = list(colors) + list(floats) + list(texture_environments)
    shader_file_index, shader_obj = index.resolve_pptr(
        material_obj, file_index, tree.get("m_Shader", {})
    )
    shader_strings = _shader_strings(shader_obj) if shader_obj is not None else []
    family, features = _shader_profile(shader_strings, property_names)
    shader_name = (
        index.object_name(shader_file_index, shader_obj)
        if shader_obj is not None else "Unresolved Shader"
    )
    alpha_test = any(name in floats and floats[name] > 0.5 for name in (
        "_AlphaTest", "_AlphaClip", "_UseAlphaTest"
    )) or any(keyword in tree.get("m_ValidKeywords", []) for keyword in (
        "_ALPHATEST_ON", "ALPHA_TEST"
    ))
    return {
        "name": index.object_name(file_index, material_obj),
        "shader": shader_name,
        "shader_id": int(shader_obj.path_id) if shader_obj is not None else 0,
        "family": family,
        "features": features,
        "base_color": base_color,
        "emission_color": emission_color,
        "metallic": max(0.0, min(1.0, metallic)),
        "smoothness": max(0.0, min(1.0, smoothness)),
        "normal_strength": float(floats.get("_BumpScale", 1.0)),
        "emission_strength": max(0.0, min(8.0, emission_strength)),
        "cutoff": float(floats.get("_Cutoff", floats.get("_CutOff", 0.5))),
        "alpha_test": alpha_test,
        "render_queue": render_queue,
        "keywords": list(tree.get("m_ValidKeywords", [])),
        "texture_names": [texture["name"] for texture in textures.values()],
        "albedo_texture": _pick_texture(
            textures, MAIN_TEXTURE_NAMES,
            excluded=("normal", "bump", "metal", "spec", "emission", "mask", "alpha"),
        ),
        "normal_texture": _pick_texture(textures, NORMAL_TEXTURE_NAMES),
        "metallic_texture": _pick_texture(textures, METALLIC_TEXTURE_NAMES),
        "emission_texture": _pick_texture(textures, EMISSION_TEXTURE_NAMES),
        "property_count": len(property_names),
    }


def _encode_payload(kind, image, parameters, extra=None):
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=False)
    metadata = {
        "name": parameters["name"],
        "shader": parameters["shader"],
        "family": parameters["family"],
        "features": parameters["features"],
        "textures": parameters["texture_names"],
        "metallic": parameters["metallic"],
        "smoothness": parameters["smoothness"],
        "render_queue": parameters["render_queue"],
        "property_count": parameters["property_count"],
        "renderer": "AOV PBR material-ball approximation",
    }
    if extra:
        metadata.update(extra)
    return {"version": 1, "kind": kind, "png": buffer.getvalue(), "metadata": metadata}


def build_material_preview_payload(index, file_index, path_id, size=720):
    material = index.object(int(file_index), int(path_id))
    if material is None or material.type.name != "Material":
        raise ValueError("material_not_found")
    parameters = _material_parameters(index, int(file_index), material)
    return _encode_payload(
        "material", _render_material_ball(parameters, size), parameters,
    )


def _representative_material(index, shader_file_index, shader_obj):
    candidates = []
    shader_path_id = int(shader_obj.path_id)
    for file_index, objects in enumerate(index.objects):
        for obj in objects.values():
            if obj.type.name != "Material":
                continue
            try:
                tree = index.tree(file_index, int(obj.path_id))
                target_file_index, target = index.resolve_pptr(
                    obj, file_index, tree.get("m_Shader", {})
                )
                if target is None or int(target.path_id) != shader_path_id or target_file_index != shader_file_index:
                    continue
                environments = dict(tree.get("m_SavedProperties", {}).get("m_TexEnvs", []))
                texture_score = sum(
                    int(environment.get("m_Texture", {}).get("m_PathID", 0) != 0)
                    for environment in environments.values()
                )
                candidates.append((texture_score, int(obj.byte_size), file_index, obj))
            except Exception:
                continue
    return max(candidates, default=None, key=lambda item: (item[0], item[1]))


def build_shader_preview_payload(index, file_index, path_id, size=720):
    shader = index.object(int(file_index), int(path_id))
    if shader is None or shader.type.name != "Shader":
        raise ValueError("shader_not_found")
    strings = _shader_strings(shader)
    properties = [value for value in strings if value.startswith("_")][:96]
    family, features = _shader_profile(strings, properties)
    representative = _representative_material(index, int(file_index), shader)
    if representative is not None:
        _score, _bytes, material_file_index, material = representative
        parameters = _material_parameters(index, material_file_index, material)
        parameters["name"] = index.object_name(int(file_index), shader)
        parameters["family"] = family
        parameters["features"] = features
        return _encode_payload(
            "shader", _render_material_ball(parameters, size), parameters,
            {"representative_material": index.object_name(material_file_index, material)},
        )
    parameters = {
        "name": index.object_name(int(file_index), shader),
        "shader": index.object_name(int(file_index), shader),
        "family": family, "features": features,
        "base_color": (0.72, 0.76, 0.82, 1.0),
        "emission_color": (0.0, 0.0, 0.0, 1.0),
        "metallic": 0.0, "smoothness": 0.5,
        "normal_strength": 1.0, "emission_strength": 0.0,
        "cutoff": 0.5, "alpha_test": "alpha test" in features,
        "render_queue": -1, "keywords": [], "texture_names": [],
        "albedo_texture": None, "normal_texture": None,
        "metallic_texture": None, "emission_texture": None,
        "property_count": len(properties),
    }
    return _encode_payload(
        "shader", _render_material_ball(parameters, size), parameters,
        {"representative_material": None},
    )
