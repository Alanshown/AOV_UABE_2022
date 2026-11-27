from pathlib import Path

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from AssetbundleUtils.UnityPy_AOV.files.SerializedFile import FileIdentifier
from tests.test_project_additions import write_fixture


def _open_project(tmp_path, source):
    result = P.export_bundle_project(str(source), str(tmp_path / "project"))
    return Path(result["path"])


def _move_asset(root, old_id, new_id, new_name):
    item = next(x for x in P._load_manifest(str(root))["assets"] if x["path_id"] == old_id)
    for key in ("raw", "editable"):
        if item.get(key):
            path = root / item[key]
            path.rename(path.with_name(f"{new_name}_{new_id}{path.suffix}"))


def _add_external_filter(source):
    env = U.load(str(source))
    cab = env.assets[0]
    source_obj = cab.objects[505]
    other = P._shallow_clone(source_obj)
    other.path_id = 506
    cab.objects[506] = other
    external = FileIdentifier.__new__(FileIdentifier)
    external.temp_empty = ""
    external.guid = b"\0" * 16
    external.type = 0
    external.path = "archive:/CAB-external/CAB-external"
    cab.externals.append(external)
    tree = source_obj.read_typetree()
    tree["m_Mesh"] = {"m_FileID": 1, "m_PathID": 101}
    other.save_typetree(tree)
    source.write_bytes(env.file.save("lz4"))


def test_name_and_id_move_reconnects_local_not_external_then_moves_again(tmp_path):
    source = write_fixture(tmp_path / "source.assetbundle", mesh_id=101, add_filter=True)
    write_fixture(tmp_path / "another.assetbundle", mesh_id=101, cab="CAB-external")
    _add_external_filter(source)
    root = _open_project(tmp_path, source)

    def rebuild(label, want_id, want_name):
        out = tmp_path / f"{label}.assetbundle"
        r = P.rebuild_bundle_project(str(root), str(out))
        got = {o.path_id: o for o in U.load(str(out)).objects}
        assert r["project_sync_status"] == "updated"
        assert r["path_id_changes"][0]["to_path_id"] == want_id
        assert r["path_id_changes"][0]["from_path_id"] == 101
        assert got[want_id].peek_name("") == want_name
        assert got[505].read_typetree()["m_Mesh"] == {"m_FileID": 0, "m_PathID": want_id}
        assert got[506].read_typetree()["m_Mesh"] == {"m_FileID": 1, "m_PathID": 101}
        assert len(got) == 3

    _move_asset(root, 101, 701, "mesh_moved")
    rebuild("first", 701, "mesh_moved")
    rebuild("repeat", 701, "mesh_moved")
    _move_asset(root, 701, 702, "mesh_moved_again")
    rebuild("second-move", 702, "mesh_moved_again")
    rebuild("second-repeat", 702, "mesh_moved_again")


def test_external_fileid_rebinds_if_that_external_is_a_cab_in_current_bundle(tmp_path):
    a = write_fixture(tmp_path / "a.assetbundle", mesh_id=101, cab="CAB-a")
    b = write_fixture(tmp_path / "b.assetbundle", mesh_id=606, cab="CAB-b",
                      add_filter=True, external=("archive:/CAB-a/CAB-a",101))
    bundle = U.load(str(a)).file
    bundle.files.update(U.load(str(b)).file.files)
    source = tmp_path / "combined.assetbundle"
    source.write_bytes(bundle.save("lz4"))
    root = _open_project(tmp_path, source)
    _move_asset(root, 101, 701, "mesh_moved")
    out = tmp_path / "rebuilt.assetbundle"
    r = P.rebuild_bundle_project(str(root), str(out))
    assert r["project_sync_status"] == "updated"
    got = {o.path_id: o for o in U.load(str(out)).objects}
    assert got[505].read_typetree()["m_Mesh"] == {"m_FileID": 1, "m_PathID": 701}
    assert 101 not in got and 701 in got


def test_explicit_migration_when_raw_bytes_are_also_replaced(tmp_path):
    from tests.fixtures import mesh_object
    source = write_fixture(tmp_path / "source.assetbundle", mesh_id=101, add_filter=True)
    root = _open_project(tmp_path, source)
    _move_asset(root, 101, 701, "moved_and_changed")
    (root / "assets/mesh/moved_and_changed_701.raw").write_bytes(mesh_object().get_raw_data())
    manifest = P._load_manifest(str(root))
    manifest["path_id_moves"] = [{"type": "Mesh", "from_path_id": 101, "to_path_id": 701}]
    P._write_json(str(root / "bundle_manifest.json"), manifest)
    for i in range(2):
        out = tmp_path / f"explicit-{i}.assetbundle"
        P.rebuild_bundle_project(str(root), str(out))
        got = {o.path_id: o for o in U.load(str(out)).objects}
        assert got[701].peek_name("") == "moved_and_changed"
        assert got[505].read_typetree()["m_Mesh"] == {"m_FileID": 0, "m_PathID": 701}
    manifest = P._load_manifest(str(root))
    assert "path_id_moves" not in manifest
    assert next(x for x in manifest["assets"] if x["path_id"] == 701)["origin_path_id"] == 101


def test_same_name_only_is_not_enough_to_infer_migration(tmp_path):
    from tests.fixtures import mesh_object
    source = write_fixture(tmp_path / "source.assetbundle", mesh_id=101, add_filter=True)
    root = _open_project(tmp_path, source)
    _move_asset(root, 101, 701, "triangle")
    (root / "assets/mesh/triangle_701.raw").write_bytes(mesh_object().get_raw_data())
    out = tmp_path / "replacement.assetbundle"
    with pytest.raises(ValueError, match="moves or separate deletions and additions"):
        P.rebuild_bundle_project(str(root), str(out))
    assert not out.exists()
    manifest = P._load_manifest(str(root))
    manifest["allow_independent_asset_changes"] = True
    P._write_json(str(root / "bundle_manifest.json"), manifest)
    ret = P.rebuild_bundle_project(str(root), str(out))
    assert not ret["path_id_changes"]
    assert ret["added_assets"][0]["path_id"] == 701 and ret["deleted_assets"][0]["path_id"] == 101
    got = {o.path_id: o for o in U.load(str(out)).objects}
    assert got[505].read_typetree()["m_Mesh"]["m_PathID"] == 0
