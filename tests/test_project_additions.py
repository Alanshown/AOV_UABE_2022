from hashlib import sha256
from pathlib import Path

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from AssetbundleUtils.UnityPy_AOV.enums import ClassIDType
from AssetbundleUtils.UnityPy_AOV.files.SerializedFile import FileIdentifier
from tests.fixtures import bundle_bytes, default_tree, mesh_object


def fingerprint(root):
    return {p.relative_to(root): sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def write_fixture(source, *, mesh_id, add_filter=False, external=None, cab=None):
    mesh = mesh_object(skin=False)
    af = mesh.assets_file
    af.objects.clear()
    mesh.path_id = mesh_id
    af.objects[mesh_id] = mesh
    if add_filter:
        obj = P._shallow_clone(mesh)
        typ = P._shallow_clone(mesh.serialized_type)
        typ.class_id = int(ClassIDType.MeshFilter)
        typ.old_type_hash = b"\x03"*16
        af.types.append(typ)
        obj.serialized_type, obj.type_id = typ, 1
        obj.path_id, obj.class_id, obj.type = 505, typ.class_id, ClassIDType.MeshFilter
        af.objects[obj.path_id] = obj
        t = default_tree(obj.get_typetree_nodes())
        if external is not None:
            path, ext_pid = external
            ext = FileIdentifier.__new__(FileIdentifier)
            ext.temp_empty = ""; ext.guid = b"\0"*16; ext.type = 0; ext.path=path
            af.externals.append(ext)
            t["m_Mesh"] = {"m_FileID": 1, "m_PathID": ext_pid}
        else:
            t["m_Mesh"] = {"m_FileID": 0, "m_PathID": mesh_id}
        t["m_GameObject"] = {"m_FileID": 0, "m_PathID": 0}
        obj.save_typetree(t)
    data = bundle_bytes(mesh)
    if cab is not None:
        bundle = U.load(data).file
        bundle.files[cab] = bundle.files.pop("CAB-aov-test")
        data = bundle.save("lz4")
    source.write_bytes(data)
    return source


def test_same_bundle_raw_clone_with_new_pathid_survives_rebuild(tmp_path):
    target = write_fixture(tmp_path / "target.assetbundle", mesh_id=101)
    root = Path(P.export_bundle_project(str(target), str(tmp_path/"export"))["path"])
    original = root/"assets"/"mesh"/"triangle_101.raw"
    clone = original.with_name("triangle_clone_9002.raw")
    clone.write_bytes(original.read_bytes())
    for i in range(2):
        result = tmp_path/f"built{i}.assetbundle"
        ret=P.rebuild_bundle_project(str(root),str(result))
        assert ret["project_sync_status"]=="updated"
        assert [item["path_id"] for item in ret["added_assets"]]==[9002]
        items={o.path_id:o for o in U.load(str(result)).objects}
        assert items[101].get_raw_data()==original.read_bytes()
        assert items[9002].peek_name()=="triangle_clone"
        assert items[9002].read(False).m_VertexCount==3


def test_cross_import_new_types_and_rebase_exact_external_cab(tmp_path):
    target = write_fixture(tmp_path/"target.assetbundle",mesh_id=101,cab="CAB-target")
    source_a = write_fixture(tmp_path/"donor-a.assetbundle",mesh_id=404,cab="CAB-donor-a",
                             add_filter=True,external=("archive:/CAB-donor-b/CAB-donor-b",222))
    source_b = write_fixture(tmp_path/"donor-b.assetbundle",mesh_id=222,cab="CAB-donor-b")
    root = Path(P.export_bundle_project(str(target),str(tmp_path/"export"))["path"])
    before=fingerprint(root)

    with pytest.raises(ValueError, match="duplicate"):
        P.stage_cross_bundle_assets(str(root),{str(source_a):[505,505]})
    with pytest.raises(ValueError, match="no matching CAB"):
        P.stage_cross_bundle_assets(str(root),{str(source_a):[505]},remove_path_ids=[101])
    assert fingerprint(root)==before
    assert not list(root.parent.glob(".aov-import-*"))

    report=P.stage_cross_bundle_assets(str(root),{str(source_a):[505],str(source_b):[222]})
    by_id={item["path_id"]:item for item in report["imported"]}
    assert by_id[505]["rebased_external_pointers"]==1
    assert Path(report["project"]) == root
    with pytest.raises(ValueError,match="already exist"):
        P.stage_cross_bundle_assets(str(root),{str(source_b):[222]})
    for i in range(2):
        result=tmp_path/f"built-{i}.assetbundle"
        ret=P.rebuild_bundle_project(str(root),str(result))
        assert ret["project_sync_status"]=="updated"
        objects={o.path_id:o for o in U.load(str(result)).objects}
        assert set(objects)=={101,222,505}
        assert objects[505].type.name=="MeshFilter"
        assert objects[505].read_typetree()["m_Mesh"]=={"m_FileID":0,"m_PathID":222}


def test_pathid_move_reconnects_same_bundle_reference(tmp_path):
    src = write_fixture(tmp_path/"source.assetbundle",mesh_id=101,add_filter=True)
    root = Path(P.export_bundle_project(str(src),str(tmp_path/"export"))["path"])
    obj = root/"assets"/"mesh"
    for f in obj.glob("triangle_101.*"):
        f.rename(f.with_name(f.name.replace("_101.", "_109.")))
    result=tmp_path/"built.assetbundle"
    ret=P.rebuild_bundle_project(str(root),str(result))
    assert ret["path_id_changes"][0]["to_path_id"]==109
    objects={o.path_id:o for o in U.load(str(result)).objects}
    assert set(objects)=={109,505}
    assert objects[505].read_typetree()["m_Mesh"]=={"m_FileID":0,"m_PathID":109}
