from pathlib import Path

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from tests.test_project_additions import write_fixture


def _with_cabs(tmp_path, pointer_cab, pointer_id=101):
    first = write_fixture(tmp_path / "first.assetbundle", mesh_id=101, cab="CAB-a")
    second = write_fixture(
        tmp_path / "second.assetbundle", mesh_id=606, cab="CAB-b", add_filter=True,
        external=(f"archive:/{pointer_cab}/{pointer_cab}", pointer_id),
    )
    bundle = U.load(str(first)).file
    bundle.files.update(U.load(str(second)).file.files)
    source = tmp_path / "joined.assetbundle"
    source.write_bytes(bundle.save("lz4"))
    return source


@pytest.mark.parametrize("embedded", [True, False])
def test_delete_cross_cab_resolves_only_embedded_target_and_rebuilds_repeatedly(tmp_path, embedded):
    linked = "CAB-a" if embedded else "CAB-external"
    if not embedded:
        write_fixture(tmp_path / "unrelated.assetbundle", mesh_id=101, cab=linked)
    source = _with_cabs(tmp_path, linked)
    orig = source.read_bytes()
    export = P.export_bundle_project(str(source), str(tmp_path / "export"))
    project = Path(export["path"])
    record = next(item for item in P._load_manifest(str(project))["assets"] if item["path_id"] == 101)
    for key in ("raw", "editable"):
        if record.get(key):
            (project / record[key]).unlink()

    for repeat in range(3):
        output = tmp_path / f"rebuilt-{repeat}.assetbundle"
        report = P.rebuild_bundle_project(str(project), str(output))
        assert report["project_sync_status"] == "updated" and report["reference_issues"] == 0
        objects = {obj.path_id: obj for obj in U.load(str(output)).objects}
        assert set(objects) == {505, 606}
        ptr = objects[505].read_typetree()["m_Mesh"]
        if embedded:
            assert ptr == {"m_FileID": 0, "m_PathID": 0}
            nulled = [{"type": "MeshFilter", "path_id": 505, "locations": ["MeshFilter.m_Mesh"]}]
            assert report["nulled_references"] == (nulled if repeat == 0 else [])
        else:
            assert ptr == {"m_FileID": 1, "m_PathID": 101}
            assert not report["nulled_references"]
    assert source.read_bytes() == orig


def test_validation_resolves_missing_embedded_target_but_not_independent_external(tmp_path):
    embedded = _with_cabs(tmp_path, "CAB-a", pointer_id=900)
    assert P._validate_local_references(U.load(str(embedded))) == [
        {"owner_type": "MeshFilter", "owner_path_id": 505, "location": "MeshFilter.m_Mesh",
         "missing_path_id": 900, "file_id": 1, "target_cab": "CAB-a"}
    ]
    outside = _with_cabs(tmp_path, "CAB-external", pointer_id=900)
    assert not P._validate_local_references(U.load(str(outside)))


def test_catalog_prunes_embedded_reference_but_keeps_independent_same_id(tmp_path):
    from AssetbundleUtils.UnityPy_AOV.files.SerializedFile import FileIdentifier
    from tests.test_catalog_delete_sync import _fixture

    env = _fixture()
    catalog = next(obj for obj in env.objects if obj.path_id == 1)
    owner = catalog.assets_file
    for name in ("CAB-a", "CAB-external"):
        ext = FileIdentifier.__new__(FileIdentifier)
        ext.temp_empty = ""
        ext.guid = b"\0" * 16
        ext.type = 0
        ext.path = f"archive:/{name}/{name}"
        owner.externals.append(ext)

    bundle = catalog.read(False)
    for pointer in (bundle.m_PreloadTable[1], bundle.m_ClassCompatibility[1][1].asset,
                    bundle.m_ContainerEntries[1][1].asset):
        pointer.path_id = 999
    bundle.m_PreloadTable.append(P._make_pointer(owner, 999, file_id=2))
    bundle.m_MainAsset.asset = P._make_pointer(owner, 999, file_id=1)
    bundle.save()
    sibling = write_fixture(tmp_path / "sibling.assetbundle", mesh_id=999, cab="CAB-a")
    joined = env.file
    joined.files.update(U.load(str(sibling)).file.files)
    reread = U.load(joined.save("lz4"))
    deleted = next(obj for obj in reread.objects if obj.path_id == 999)
    del deleted.assets_file.objects[999]
    P._sync_assetbundle_catalogs(
        reread, {999}, [], [], deleted_records=[{"path_id": 999, "source_node": "CAB-a"}]
    )
    final = next(obj for obj in U.load(reread.file.save("lz4")).objects if obj.path_id == 1)
    reopened = final.read(False)
    assert [(p.file_id, p.path_id) for p in reopened.m_PreloadTable] == [(0, 101), (0, 102), (2, 999)]
    assert [key for key, _ in reopened.m_ClassCompatibility] == [22, 44]
    assert [key for key, _ in reopened.m_ContainerEntries] == ["gone", "keep"]
    assert (reopened.m_MainAsset.asset.file_id, reopened.m_MainAsset.asset.path_id) == (0, 0)
