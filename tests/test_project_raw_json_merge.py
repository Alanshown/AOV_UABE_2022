import json

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from AssetbundleUtils.ProjectRepresentationMerge import merge_typetrees
from tests.test_project_export_integrity import _named_object_project


def _rewrite_raw(root, row, **changes):
    manifest = P._load_manifest(str(root))
    source = U.load(str(root / manifest["source"]["backup"]))
    obj = source.objects[0]
    # Start from the current project representation (including prior writes).
    path = root / row["raw"]
    obj.set_raw_data(path.read_bytes())
    tree = obj.read_typetree()
    tree.update(changes)
    path.write_bytes(obj.save_typetree(tree))


def _edit_json(root, row, **changes):
    path = root / row["editable"]
    tree = json.loads(path.read_text(encoding="utf-8"))
    tree.update(changes)
    path.write_text(json.dumps(tree), encoding="utf-8")


def _rebuild(root, output):
    report = P.rebuild_bundle_project(str(root), str(output))
    assert report["project_sync_status"] == "updated", report
    return U.load(str(output)).objects[0].read_typetree()


def test_disjoint_raw_json_edits_survive_and_refresh_both_files(tmp_path):
    root, row = _named_object_project(tmp_path)
    _rewrite_raw(root, row, m_Layer=6)
    _edit_json(root, row, m_IsActive=False)
    first = _rebuild(root, tmp_path / "first.assetbundle")
    assert first["m_Layer"] == 6 and not first["m_IsActive"]
    assert json.loads((root / row["editable"]).read_text(encoding="utf-8")) == first
    assert _rebuild(root, tmp_path / "second.assetbundle") == first

    # A second pair must use the last synchronized base, not the original AB.
    # Here the RAW edit intentionally reverts one field to the original value.
    row = P._load_manifest(str(root))["assets"][0]
    _rewrite_raw(root, row, m_Layer=0)
    _edit_json(root, row, m_IsActive=True)
    third = _rebuild(root, tmp_path / "third.assetbundle")
    assert third["m_Layer"] == 0 and third["m_IsActive"]


def test_overlapping_different_edits_are_atomic(tmp_path):
    root, row = _named_object_project(tmp_path)
    _rewrite_raw(root, row, m_Layer=3)
    _edit_json(root, row, m_Layer=4)
    before = P._project_snapshot(str(root))
    destination = tmp_path / "external.assetbundle"
    destination.write_bytes(b"do not replace")
    with pytest.raises(ValueError, match=r"Conflicting RAW and JSON edits at GameObject PathID 101.m_Layer"):
        P.rebuild_bundle_project(str(root), str(destination))
    assert destination.read_bytes() == b"do not replace"
    assert P._project_snapshot(str(root)) == before


def test_raw_only_rebuild_refreshes_json_before_a_later_json_edit(tmp_path):
    root, row = _named_object_project(tmp_path)
    _rewrite_raw(root, row, m_Layer=11)
    assert _rebuild(root, tmp_path / "raw.assetbundle")["m_Layer"] == 11
    row = P._load_manifest(str(root))["assets"][0]
    assert json.loads((root / row["editable"]).read_text(encoding="utf-8"))["m_Layer"] == 11
    _edit_json(root, row, m_IsActive=False)
    final = _rebuild(root, tmp_path / "json.assetbundle")
    assert final["m_Layer"] == 11 and not final["m_IsActive"]


def test_pointer_pairs_and_lists_are_atomic_conflicts():
    base = {"reference": {"m_FileID": 0, "m_PathID": 3}, "values": [1, 2]}
    with pytest.raises(ValueError, match=r"asset.reference;"):
        merge_typetrees(base, {**base, "reference": {"m_FileID": 1, "m_PathID": 3}},
                        {**base, "reference": {"m_FileID": 0, "m_PathID": 5}}, "asset")
    with pytest.raises(ValueError, match=r"asset.values;"):
        merge_typetrees(base, {**base, "values": [0, 2]},
                        {**base, "values": [1, 3]}, "asset")


def test_matching_changes_and_nested_disjoint_changes_are_merged():
    base = {"nested": {"value": 0, "other": True}, "same": 1}
    raw = {"nested": {"value": 8, "other": True}, "same": 2}
    json_edit = {"nested": {"value": 0, "other": False}, "same": 2}
    assert merge_typetrees(base, raw, json_edit, "asset") == {
        "nested": {"value": 8, "other": False}, "same": 2,
    }
