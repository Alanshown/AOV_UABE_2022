from pathlib import Path

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from tests.test_project_additions import write_fixture
from tests.test_path_id_migration import _move_asset, _open_project


def test_three_asset_id_cycle_rebinds_each_local_reference_once(tmp_path):
    source = write_fixture(tmp_path / "source.assetbundle", mesh_id=101, add_filter=True)
    env = U.load(str(source))
    cab = env.assets[0]
    for mesh_id, filter_id, label in [(102, 506, "square"), (103, 507, "hexagon")]:
        mesh = P._shallow_clone(cab.objects[101])
        mesh.path_id = mesh_id
        cab.objects[mesh_id] = mesh
        mesh_tree = mesh.read_typetree()
        mesh_tree["m_Name"] = label
        mesh.save_typetree(mesh_tree)
        ref = P._shallow_clone(cab.objects[505])
        ref.path_id = filter_id
        cab.objects[filter_id] = ref
        ref_tree = ref.read_typetree()
        ref_tree["m_Mesh"]["m_PathID"] = mesh_id
        ref.save_typetree(ref_tree)
    source.write_bytes(env.file.save("lz4"))
    root = _open_project(tmp_path, source)
    original = P._load_manifest(str(root))
    names = {101: "triangle", 102: "square", 103: "hexagon"}
    cycle = {101: 102, 102: 103, 103: 101}
    for row in original["assets"]:
        if row["path_id"] not in cycle:
            continue
        for field in ("raw", "editable"):
            f = root / row[field]
            f.rename(f.with_name(f"{row['name']}_{cycle[row['path_id']]}{f.suffix}"))
    for i in range(4):
        out = tmp_path / f"cycle-{i}.assetbundle"
        result = P.rebuild_bundle_project(str(root), str(out))
        assert result["project_sync_status"] == "updated"
        objs = {o.path_id: o for o in U.load(str(out)).objects}
        for original_id, filter_id in [(101, 505), (102, 506), (103, 507)]:
            ptr = objs[filter_id].read_typetree()["m_Mesh"]
            assert ptr == {"m_FileID": 0, "m_PathID": cycle[original_id]}
            assert objs[ptr["m_PathID"]].peek_name("") == names[original_id]


def test_imported_asset_migrates_automatically_and_explicitly(tmp_path):
    target = write_fixture(tmp_path / "target.assetbundle", mesh_id=101, cab="CAB-target")
    donor = write_fixture(tmp_path / "donor.assetbundle", mesh_id=404,
                          cab="CAB-donor", add_filter=True)
    root = _open_project(tmp_path, target)
    P.stage_cross_bundle_assets(str(root), {str(donor): [404, 505]})
    P.rebuild_bundle_project(str(root), str(tmp_path / "baseline.assetbundle"))
    for old_id, new_id, explicit in [(404, 707, False), (707, 808, True)]:
        _move_asset(root, old_id, new_id, f"mesh_{new_id}")
        if explicit:
            manifest = P._load_manifest(str(root))
            manifest["path_id_moves"] = [{"type": "Mesh", "from_path_id": old_id, "to_path_id": new_id}]
            P._write_json(str(root / "bundle_manifest.json"), manifest)
        for i in range(3):
            out = tmp_path / f"move-{new_id}-{i}.assetbundle"
            result = P.rebuild_bundle_project(str(root), str(out))
            assert result["project_sync_status"] == "updated"
            objs = {o.path_id: o for o in U.load(str(out)).objects}
            assert set(objs) == {101, 505, new_id}
            assert objs[505].read_typetree()["m_Mesh"] == {"m_FileID": 0, "m_PathID": new_id}
            assert objs[new_id].peek_name("") == f"mesh_{new_id}"


def test_moving_a_shortened_filename_preserves_the_full_unity_name(tmp_path):
    import json
    from tests.test_project_export_integrity import _named_object_project
    root, row = _named_object_project(tmp_path)
    editable = root / row["editable"]
    original = json.loads(editable.read_text(encoding="utf-8"))
    original["m_Name"] = "零" * 88
    editable.write_text(json.dumps(original), encoding="utf-8")
    result = P.rebuild_bundle_project(str(root), str(tmp_path / "renamed.assetbundle"))
    assert result["project_sync_status"] == "updated"
    record = P._load_manifest(str(root))["assets"][0]
    old_stem = record["file_stem"]
    alias, path_id = P._parse_asset_file_stem(old_stem)
    assert alias != original["m_Name"]
    _move_asset(root, path_id, 992, alias)
    for repeat in range(3):
        out = tmp_path / f"unicode-move-{repeat}.assetbundle"
        assert P.rebuild_bundle_project(str(root), str(out))["project_sync_status"] == "updated"
        got = U.load(str(out)).objects[0]
        assert got.path_id == 992
        assert got.read_typetree()["m_Name"] == original["m_Name"]
