"""Regression coverage using synthetic assets, no third-party bundles required."""
import json
from copy import copy
from hashlib import sha256

import pytest
from AssetbundleUtils import BundleProject as project, UnityPy_AOV
from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryWriter
from tests.fixtures import bundle_bytes, mesh_object


def texture_bundle(tmp_path, *, offset=16, size=16, same_owner=True):
    mesh = mesh_object(skin=False)
    af = mesh.assets_file
    name = af.name + ".resS"
    path = f"archive:/{af.name}/{name}"
    for i, stream in enumerate((
        dict(offset=0, size=16, path=path),
        dict(offset=offset, size=size, path=""),
    )):
        obj = project._shallow_clone(mesh)
        typ = copy(mesh.serialized_type)
        typ.class_id = int(ClassIDType.Texture2D)
        af.types.append(typ)
        obj.type_id = len(af.types) - 1
        obj.class_id, obj.type, obj.path_id = typ.class_id, ClassIDType.Texture2D, i + 206
        obj.serialized_type = typ
        af.objects[obj.path_id] = obj
        w = EndianBinaryWriter(endian="<")
        w.write_aligned_string(f"img{i}")
        # Runtime Android Texture2D layout in the fixture's Unity 2022.3.
        w.write_int(0)
        w.write_boolean(False)
        w.write_boolean(False)
        w.align_stream()
        for value in (2, 2, size if i else 16, 0, 4, 1):
            w.write_int(value)
        for _ in range(3):
            w.write_boolean(False)
        w.align_stream()
        w.write_aligned_string("")
        w.write_boolean(False)
        w.align_stream()
        for value in (0, 1, 2, 1, 1):
            w.write_int(value)
        w.write_float(0)
        for _ in range(5):
            w.write_int(0)
        w.write_byte_array(b"")
        w.align_stream()
        w.write_int(0)
        w.write_u_long(stream["offset"])
        w.write_u_int(stream["size"])
        w.write_aligned_string(stream["path"])
        obj.set_raw_data(w.bytes)
    resources = {name: bytes(range(32))} if same_owner else {"unrelated.resS": bytes(range(32))}
    source = tmp_path / "sample.assetbundle"
    source.write_bytes(bundle_bytes(mesh, resources=resources))
    return source


def test_legacy_animation_folder_can_contain_distinct_types(tmp_path):
    raw = tmp_path / "assets" / "animation"
    raw.mkdir(parents=True)
    items = []
    for path_id, kind in ((123, "Animation"), (456, "AnimationClip")):
        name = f"item{path_id}"
        path = raw / f"{name}_{path_id}.raw"
        b = bytes([path_id % 256])
        path.write_bytes(b)
        items.append(dict(path_id=path_id, type=kind, name=name, raw=str(path.relative_to(tmp_path)),
                          sha256=sha256(b).hexdigest(), file_stem=path.stem))
    scan = project._scan_project_assets(str(tmp_path), {"assets": items, "backup_assets": items})
    assert {i["path_id"]: i["type"] for i in scan["items"]} == {123: "Animation", 456: "AnimationClip"}
    assert scan["added"] == scan["deleted"] == []
    assert project.TYPE_DIRECTORIES["Animation"] != project.TYPE_DIRECTORIES["AnimationClip"]


def test_empty_path_recovers_known_cab_slice_and_exports(tmp_path):
    source = texture_bundle(tmp_path)
    original = source.read_bytes()
    env = UnityPy_AOV.load(original)
    assert project.recover_unambiguous_texture_streams(env) == {207}
    rebuilt = env.file.save("lz4")
    reloaded = UnityPy_AOV.load(rebuilt)
    assert project.recover_unambiguous_texture_streams(reloaded) == set()
    obj = next(x for x in reloaded.objects if x.path_id == 207)
    assert obj.read(False).image_data == bytes(range(16, 32))

    export = project.export_bundle_project(str(source), str(tmp_path / "export"), export_decoded=False)
    assert (tmp_path / "export" / "sample" / "backup" / source.name).read_bytes() == original
    manifest = json.loads((tmp_path / "export" / "sample" / "bundle_manifest.json").read_text())
    known = next(i for i in manifest["assets"] if i["path_id"] == 207)
    assert known["stream"]["payload_sha256"] == sha256(bytes(range(16, 32))).hexdigest()
    dst = tmp_path / "out.assetbundle"
    assert project.rebuild_bundle_project(export["path"], str(dst))["stream_sync"]["active_stream_assets"] == 2
    verified = next(o for o in UnityPy_AOV.load(dst.read_bytes()).objects if o.path_id == 207)
    assert verified.read(False).image_data == bytes(range(16, 32))
    assert source.read_bytes() == original


@pytest.mark.parametrize("kwargs", [{"offset": 8}, {"offset": 24}, {"same_owner": False}])
def test_blank_nonempty_texture_rejects_ambiguous_or_invalid_slice(tmp_path, kwargs):
    source = texture_bundle(tmp_path, **kwargs)
    env = UnityPy_AOV.load(source.read_bytes())
    obj = next(o for o in env.objects if o.path_id == 207)
    original = obj.get_raw_data()
    with pytest.raises(ValueError, match=r"Texture2D 207.*empty stream path"):
        project.recover_unambiguous_texture_streams(env)
    assert obj.get_raw_data() == original


def test_empty_texture_with_zero_bytes_does_not_read_resource(tmp_path):
    source = texture_bundle(tmp_path, offset=0, size=0)
    env = UnityPy_AOV.load(source.read_bytes())
    obj = next(o for o in env.objects if o.path_id == 207)
    assert obj.read(False).image_data == b""
    assert project.recover_unambiguous_texture_streams(env) == set()


def test_legacy_export_without_sidecar_restores_only_recovered_payload(tmp_path, monkeypatch):
    source = texture_bundle(tmp_path)
    with monkeypatch.context() as old_export:
        old_export.setattr(project, "recover_unambiguous_texture_streams", lambda env, **kwargs: set())
        info = project.export_bundle_project(
            str(source), str(tmp_path / "legacy"), export_decoded=False
        )
    manifest_path = tmp_path / "legacy" / "sample" / "bundle_manifest.json"
    old = json.loads(manifest_path.read_text())
    assert not next(i for i in old["assets"] if i["path_id"] == 207).get("stream")
    dst = tmp_path / "rebuilt.assetbundle"
    project.rebuild_bundle_project(info["path"], str(dst))
    updated = json.loads(manifest_path.read_text())
    item = next(i for i in updated["assets"] if i["path_id"] == 207)
    assert (manifest_path.parent / item["stream"]["payload"]).read_bytes() == bytes(range(16, 32))
    project.rebuild_bundle_project(info["path"], str(tmp_path / "rebuilt_again.assetbundle"))
