import csv
from hashlib import sha256
import json
from pathlib import Path

import pytest
from PIL import Image

from AssetbundleUtils import BundleProject as project, UnityPy_AOV
from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryWriter
from tests.fixtures import bundle_bytes, mesh_object
from tests.test_bundle_project_compatibility import texture_bundle


def asset_path(project_dir, raw_type, extension):
    manifest = project._load_manifest(str(project_dir))
    item = next(item for item in manifest["assets"] if item["type"] == raw_type)
    return project_dir / item[extension]


def test_export_and_rebuild_asset_name_with_original_non_utf8_bytes(tmp_path):
    name_bytes = "凡恩-🐉".encode() + b"\x8f\xdd" + br"\udc8f"
    name = name_bytes.decode("utf-8", "surrogateescape")
    mesh = mesh_object()
    tree = mesh.read_typetree()
    tree["m_Name"] = name
    mesh.save_typetree(tree)
    source = tmp_path / "中文.assetbundle"
    source.write_bytes(bundle_bytes(mesh))
    exported = project.export_bundle_project(str(source), str(tmp_path / "projects"))
    root = Path(exported["path"])
    manifest = project._load_manifest(str(root))
    assert "\\udc8f\\udcdd" in Path(exported["manifest"]).read_text()
    assert "凡恩-🐉" in Path(exported["manifest"]).read_text()
    assert manifest["assets"][0]["name"].encode("utf-8", "surrogateescape") == name_bytes
    for json_path in root.rglob(project.ASSET_INDEX_JSON):
        assert json.loads(json_path.read_text())["assets"][0]["name"] == name
    for csv_path in root.rglob(project.ASSET_INDEX_CSV):
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            assert r"凡恩-🐉\udc8f\udcdd" in next(csv.DictReader(handle))["name"]
    assert Path(exported["backup"]).read_bytes() == source.read_bytes()
    editable = asset_path(root, "Mesh", "editable")
    assert editable.is_file()
    assert list(Path(tmp_path / "projects").iterdir()) == [root]
    rebuilt = tmp_path / "rebuilt.assetbundle"
    result = project.rebuild_bundle_project(str(root), str(rebuilt))
    assert result["project_sync_status"] == "updated"
    reloaded = UnityPy_AOV.load(str(rebuilt)).objects[0]
    assert reloaded.peek_name().encode("utf-8", "surrogateescape") == name_bytes
    assert reloaded.get_raw_data() == mesh.get_raw_data()


def test_export_failure_never_publishes_partial_project(tmp_path, monkeypatch):
    source = tmp_path / "input.assetbundle"
    source.write_bytes(bundle_bytes(mesh_object()))
    root = tmp_path / "projects"
    previous = root / "input"
    previous.mkdir(parents=True)
    marker = previous / "bundle_manifest.json"
    marker.write_text("already there")
    original = project._write_asset_indexes

    def fail_after_index(directory, manifest):
        original(directory, manifest)
        assert (Path(directory) / "bundle_manifest.json").is_file()
        raise OSError("index failure")

    monkeypatch.setattr(project, "_write_asset_indexes", fail_after_index)
    with pytest.raises(OSError, match="index failure"):
        project.export_bundle_project(str(source), str(root))
    assert list(root.iterdir()) == [previous]
    assert marker.read_text() == "already there"
    monkeypatch.setattr(project, "_write_asset_indexes", original)
    info = project.export_bundle_project(str(source), str(root))
    assert Path(info["path"]) == root / "input_1"
    assert all(Path(info[key]).exists() for key in ("backup", "manifest", "index"))


def test_json_failed_write_retains_previous_document(tmp_path):
    output = tmp_path / "manifest.json"
    project._write_json(str(output), {"intact": True})
    with pytest.raises(TypeError):
        project._write_json(str(output), {"bad": object()})
    assert json.loads(output.read_text()) == {"intact": True}
    assert list(tmp_path.iterdir()) == [output]


def text_asset_fixture(source):
    mesh = mesh_object()
    obj = project._shallow_clone(mesh)
    typ = project._shallow_clone(mesh.serialized_type)
    typ.class_id = int(ClassIDType.TextAsset)
    obj.assets_file.types.append(typ)
    obj.serialized_type, obj.type_id = typ, 1
    obj.path_id, obj.class_id, obj.type = 202, typ.class_id, ClassIDType.TextAsset
    obj.assets_file.objects[202] = obj
    writer = EndianBinaryWriter(endian="<")
    writer.write_aligned_string("voice_table")
    writer.write_byte_array(b"before\x00\xa1")
    writer.align_stream()
    obj.set_raw_data(writer.bytes)
    source.write_bytes(bundle_bytes(obj))
    return source


def test_edit_binary_text_asset_applies_and_survives_second_rebuild(tmp_path):
    source = text_asset_fixture(tmp_path / "sample.assetbundle")
    root = Path(project.export_bundle_project(str(source), str(tmp_path / "projects"))["path"])
    replacement = "标题".encode() + b"\x00\xff\x01\x03"
    asset_path(root, "TextAsset", "editable").write_bytes(replacement)
    for i in range(2):
        output = tmp_path / f"rebuilt{i}.assetbundle"
        report = project.rebuild_bundle_project(str(root), str(output))
        assert report["edited_text_assets"] == (1 if i == 0 else 0)
        assert report["project_sync_status"] == "updated"
        parsed = {obj.path_id: obj for obj in UnityPy_AOV.load(str(output)).objects}
        assert parsed[202].peek_name() == "voice_table"
        assert bytes(parsed[202].read(False).m_Script) == replacement


def test_failed_rebuild_after_png_conversion_does_not_modify_project(tmp_path, monkeypatch):
    source = texture_bundle(tmp_path)
    root = Path(project.export_bundle_project(str(source), str(tmp_path / "projects"))["path"])
    editable = asset_path(root, "Texture2D", "editable")
    Image.new("RGBA", (2, 2), (20, 208, 151, 249)).save(editable)

    def fingerprint():
        return {p.relative_to(root): sha256(p.read_bytes()).digest()
                for p in root.rglob("*") if p.is_file()}

    previous = fingerprint()
    bad = tmp_path / "failed.assetbundle"
    original = project._rebuild_stream_resources

    def fail_before_save(environment, directory, manifest, items, recovered):
        assert any("_edited_stream_payload" in entry for entry in items)
        raise OSError("failure after PNG processing")

    monkeypatch.setattr(project, "_rebuild_stream_resources", fail_before_save)
    with pytest.raises(OSError, match="failure after PNG processing"):
        project.rebuild_bundle_project(str(root), str(bad))
    assert fingerprint() == previous
    assert not bad.exists()
    monkeypatch.setattr(project, "_rebuild_stream_resources", original)
    after = tmp_path / "after.assetbundle"
    assert project.rebuild_bundle_project(str(root), str(after))["project_sync_status"] == "updated"
    after_again = tmp_path / "after_again.assetbundle"
    assert project.rebuild_bundle_project(str(root), str(after_again))["edited_textures"] == 0
    assert after_again.read_bytes() == after.read_bytes()


def make_streamed_bundle(source, kind, payload, name=None):
    mesh = mesh_object()
    obj = project._shallow_clone(mesh)
    typ = project._shallow_clone(mesh.serialized_type)
    typ.class_id = int(ClassIDType[kind])
    obj.assets_file.types.append(typ)
    obj.serialized_type, obj.type_id = typ, 1
    obj.path_id, obj.class_id, obj.type = 222, typ.class_id, ClassIDType[kind]
    obj.assets_file.objects[222] = obj
    resource = obj.assets_file.name + ".resource"
    archive = f"archive:/{obj.assets_file.name}/{resource}"
    offset = 16
    w = EndianBinaryWriter(endian="<")
    w.write_aligned_string(name or kind)
    if kind == "AudioClip":
        for v in (0, 1, 8000, 16):
            w.write_int(v)
        w.write_float(.1)
        w.write_boolean(False)
        w.align_stream()
        w.write_int(0)
        for v in (True, False, False):
            w.write_boolean(v)
        w.align_stream()
    elif kind == "VideoClip":
        w.write_aligned_string("demo.mp4")
        for v in (16, 16, 16, 16, 1, 1):
            w.write_u_int(v)
        w.write_double(24.0)
        w.write_u_long(2)
        for v in (0, 0, 0, 0, 0):
            w.write_int(v)
    w.write_aligned_string(archive)
    w.write_u_long(offset)
    w.write_u_long(len(payload))
    if kind == "AudioClip":
        w.write_int(0)
    else:
        w.write_boolean(False)
        w.write_boolean(True)
        w.align_stream()
    obj.set_raw_data(w.bytes)
    source.write_bytes(bundle_bytes(obj, resources={resource: b"\0"*offset+payload}))
    return source


@pytest.mark.parametrize("kind", ["AudioClip", "VideoClip"])
def test_streamed_media_sidecar_replacement(kind, tmp_path):
    from io import BytesIO
    import struct
    import wave

    def audio(sample, n):
        out = BytesIO()
        with wave.open(out, "wb") as handle:
            handle.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            handle.writeframes(struct.pack("<h", sample) * n)
        return out.getvalue()

    if kind == "AudioClip":
        old, new = audio(10, 12), audio(-800, 101)
    else:
        old, new = b"test video stream A", b"large replacement test video stream B" * 3
    src = make_streamed_bundle(tmp_path / "sample.assetbundle", kind, old)
    export = project.export_bundle_project(str(src), str(tmp_path / "projects"))
    root = Path(export["path"])
    manifest = project._load_manifest(str(root))
    record = next(item for item in manifest["assets"] if item["type"] == kind)
    sidecar = root / record["stream"]["payload"]
    assert sidecar.read_bytes() == old
    sidecar.write_bytes(new)
    for i in range(2):
        output = tmp_path / f"result-{i}.assetbundle"
        report = project.rebuild_bundle_project(str(root), str(output))
        assert report["project_sync_status"] == "updated"
        obj = next(o for o in UnityPy_AOV.load(str(output)).objects if o.type.name == kind)
        assert obj.path_id == 222
        clip = obj.read(False)
        if kind == "AudioClip":
            assert bytes(clip.m_AudioData) == new
            with wave.open(BytesIO(clip.samples[f"{kind}.wav"])) as stream:
                assert stream.getnframes() == 101
                assert struct.unpack("<h", stream.readframes(1))[0] == -800
        else:
            assert bytes(clip.m_VideoData) == new
            assert clip.Width == clip.Height == 16


def test_standalone_dump_roundtrips_escaped_name(tmp_path):
    from AssetbundleUtils.TypeTreeImport import export_typetree_dump, import_typetree_dump
    from AssetbundleUtils.AssetsList import _safe_filename

    mesh = mesh_object()
    t = mesh.read_typetree()
    t["m_Name"] = "凡恩🌎" + b"\xff\x80".decode("utf-8", "surrogateescape")
    mesh.save_typetree(t)
    path = tmp_path / "mesh.dump.json"
    export_typetree_dump(mesh, path)
    doc = json.loads(path.read_text())
    assert doc["tree"]["m_Name"] == t["m_Name"]
    assert "凡恩" in _safe_filename(t["m_Name"])
    _safe_filename(t["m_Name"]).encode("utf-8")
    import_typetree_dump(mesh, path)
    assert mesh.peek_name() == t["m_Name"]
