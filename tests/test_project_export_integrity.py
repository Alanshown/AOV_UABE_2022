import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from AssetbundleUtils import AssetsList as UI, BundleProject as P, UnityPy_AOV as U
from tests.fixtures import bundle_bytes, mesh_object


def _export_callbacks(monkeypatch, directory, objects, *, kind):
    monkeypatch.setattr(UI, "askdirectory", lambda *args: str(directory))
    completed = []
    browser = SimpleNamespace(
        _selected_records=lambda: list(enumerate(objects)),
        _submit_operation=lambda label, worker, success: completed.append(worker()),
        input_path=str(directory), is_directory=True, window=None,
        sprite_preview_overrides={}, texture_preview_overrides={},
    )
    getattr(UI.AssetBrowser, kind)(browser)
    assert completed == [len(objects)]


@pytest.mark.parametrize("kind,ext", [("export_raw", ".dat"), ("export_mesh", ".obj"),
                                      ("export_texture", ".png")])
def test_batch_exports_keep_previous_file_and_both_matching_assets(
    tmp_path, monkeypatch, kind, ext
):
    old = tmp_path / ("same_101" + ext)
    old.write_bytes(b"existing")
    if kind == "export_texture":
        objects = [
            SimpleNamespace(path_id=101, type=SimpleNamespace(name="Texture2D"),
                            read=lambda _, c=color: SimpleNamespace(
                                m_Name="same", image=Image.new("RGB", (2, 2), c)))
            for color in ("red", "blue")
        ]
    else:
        objects = [
            SimpleNamespace(path_id=101, type=SimpleNamespace(name="Mesh"), peek_name=lambda _: "same",
                            get_raw_data=lambda b=data: b,
                            read=lambda _, b=data: SimpleNamespace(m_Name="same", export=lambda: b.decode()))
            for data in (b"first", b"second")
        ]
    _export_callbacks(monkeypatch, tmp_path, objects, kind=kind)
    assert old.read_bytes() == b"existing"
    outputs = [tmp_path / f"same_101 ({index}){ext}" for index in (2, 3)]
    if kind == "export_texture":
        colors = []
        for path in outputs:
            with Image.open(path) as actual:
                colors.append(actual.getpixel((0, 0)))
        assert colors == [(255, 0, 0), (0, 0, 255)]
    else:
        assert [f.read_bytes() for f in outputs] == [b"first", b"second"]


def test_failed_export_never_leaves_a_partial_file(tmp_path):
    def fail(handle):
        handle.write(b"partial")
        raise OSError("render failed")
    with pytest.raises(OSError, match="render failed"):
        UI._write_distinct_export(str(tmp_path), "asset_101", ".obj", fail)
    assert list(tmp_path.iterdir()) == []


def _two_cabs():
    a = U.load(bundle_bytes(mesh_object())).file
    b = U.load(bundle_bytes(mesh_object(skin=False))).file
    a.files["CAB-aov-second"] = b.files["CAB-aov-test"]
    return a.save("lz4")


def test_cross_cab_duplicate_is_rejected_before_project_exposed(tmp_path):
    source = tmp_path / "multiple.assetbundle"
    source.write_bytes(_two_cabs())
    contents = source.read_bytes()
    owners = {obj.assets_file.name for obj in U.load(contents).objects
              if obj.path_id == 101}
    assert owners == {"CAB-aov-second", "CAB-aov-test"}
    target = tmp_path / "projects"
    with pytest.raises(ValueError, match=r"PathID 101 occurs in multiple CABs.*CAB-aov-second"):
        P.export_bundle_project(str(source), str(target))
    assert source.read_bytes() == contents and list(target.iterdir()) == []


def test_previously_exported_duplicate_cab_cannot_overwrite_output(tmp_path):
    source = tmp_path / "single.assetbundle"
    source.write_bytes(bundle_bytes(mesh_object()))
    result = P.export_bundle_project(str(source), str(tmp_path / "projects"))
    duplicated = _two_cabs()
    Path(result["backup"]).write_bytes(duplicated)
    # Model an older project exporter whose backup was always a two-CAB
    # bundle; this assertion tests CAB validation, not a swapped backup.
    manifest_path = Path(result["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source"].update(bytes=len(duplicated), sha256=P._sha256(duplicated))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "existing.assetbundle"
    output.write_bytes(b"existing export")
    with pytest.raises(ValueError, match="occurs in multiple CABs"):
        P.rebuild_bundle_project(result["path"], str(output))
    assert output.read_bytes() == b"existing export"


def test_case_only_asset_rename_roundtrips_twice(tmp_path):
    src = tmp_path / "single.assetbundle"
    src.write_bytes(bundle_bytes(mesh_object()))
    report = P.export_bundle_project(str(src), str(tmp_path / "project"))
    folder = Path(report["path"])
    for f in (folder / "assets" / "mesh").glob("triangle_101.*"):
        temp = f.with_name("rename.tmp")
        f.rename(temp)
        temp.rename(f.with_name(f.name.replace("triangle_", "TRIANGLE_")))
    for index in range(2):
        output = tmp_path / f"rebuilt{index}.assetbundle"
        assert P.rebuild_bundle_project(str(folder), str(output))["project_sync_status"] == "updated"
        assert U.load(str(output)).objects[0].peek_name("") == "TRIANGLE"
    assert (folder / "assets" / "mesh" / "TRIANGLE_101.raw").is_file()


def _named_object_project(tmp_path):
    from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType
    from tests.fixtures import default_tree
    obj = mesh_object()
    obj.type = ClassIDType.GameObject
    obj.class_id = obj.serialized_type.class_id = int(obj.type)
    tree = default_tree(obj.get_typetree_nodes())
    tree["m_Name"] = "TestAsset"
    tree["m_IsActive"] = True
    obj.save_typetree(tree)
    source = tmp_path / "named.assetbundle"
    source.write_bytes(bundle_bytes(obj))
    report = P.export_bundle_project(str(source), str(tmp_path / "export"))
    root = Path(report["path"])
    return root, P._load_manifest(str(root))["assets"][0]


def test_edited_json_name_and_payload_survive_repeated_rebuild(tmp_path):
    root, row = _named_object_project(tmp_path)
    editable = root / row["editable"]
    tree = json.loads(editable.read_text(encoding="utf-8"))
    tree["m_Name"] = "NewName"
    tree["m_IsActive"] = False
    editable.write_text(json.dumps(tree), encoding="utf-8")
    for index in range(2):
        output = tmp_path / f"renamed{index}.assetbundle"
        result = P.rebuild_bundle_project(str(root), str(output))
        assert result["project_sync_status"] == "updated"
        assert result["renamed_assets"][0]["to"] == "NewName"
        recovered = U.load(str(output)).objects[0].read_typetree()
        assert recovered["m_Name"] == "NewName"
        assert not recovered["m_IsActive"]
    assert (editable.parent / "NewName_101.json").is_file()


def test_conflicting_json_and_filename_rename_is_explicit_and_keeps_files(tmp_path):
    root, row = _named_object_project(tmp_path)
    editable = root / row["editable"]
    tree = json.loads(editable.read_text(encoding="utf-8"))
    tree["m_Name"] = "ViaJson"
    editable.write_text(json.dumps(tree), encoding="utf-8")
    raw = root / row["raw"]
    raw.rename(raw.with_name("ViaFilename_101.raw"))
    before = P._project_snapshot(str(root))
    output = tmp_path / "conflict.assetbundle"
    with pytest.raises(ValueError, match="filename and JSON m_Name request different names"):
        P.rebuild_bundle_project(str(root), str(output))
    assert not output.exists()
    assert P._project_snapshot(str(root)) == before
