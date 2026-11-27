import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from tests.test_project_export_integrity import _named_object_project


def _prepared(tmp_path):
    root, row = _named_object_project(tmp_path)
    sidecar = root / row["editable"]
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    data["m_IsActive"] = False
    sidecar.write_text(json.dumps(data), encoding="utf-8")
    old = sidecar.read_bytes()
    data["m_Layer"] = 9
    new = json.dumps(data).encode()
    return root, sidecar, old, new


@pytest.mark.parametrize("mode", ["before_apply", "after_apply"])
def test_prepublication_edit_aborts_without_replacing_existing_output(tmp_path, mode):
    root, sidecar, old, new = _prepared(tmp_path)
    out = tmp_path / "out.assetbundle"
    out.write_bytes(b"older output")
    before = (root / "bundle_manifest.json").read_bytes()
    original = P._apply_project_objects
    def edit_while_rebuilding(*args, **kwargs):
        if mode == "before_apply":
            sidecar.write_bytes(new)
        result = original(*args, **kwargs)
        if mode == "after_apply":
            sidecar.write_bytes(new)
        return result
    with patch.object(P, "_apply_project_objects", side_effect=edit_while_rebuilding):
        with pytest.raises(OSError, match="source project changed during rebuild"):
            P.rebuild_bundle_project(str(root), str(out))
    assert out.read_bytes() == b"older output"
    assert sidecar.read_bytes() == new
    assert (root / "bundle_manifest.json").read_bytes() == before
    assert {p.name for p in out.parent.glob(".aov-*")} <= {".aov-rebuild-locks"}


def test_edit_during_project_writeback_keeps_edit_and_verified_output(tmp_path):
    root, sidecar, old, new = _prepared(tmp_path)
    out = tmp_path / "out.assetbundle"
    original = P._refresh_project_manifest
    def edit_while_copying(*args, **kwargs):
        result = original(*args, **kwargs)
        sidecar.write_bytes(new)
        return result
    with patch.object(P, "_refresh_project_manifest", side_effect=edit_while_copying):
        result = P.rebuild_bundle_project(str(root), str(out))
    assert result["project_sync_status"] == "warning"
    assert sidecar.read_bytes() == new and not list(root.parent.glob(".aov-project-*"))
    assert U.load(str(out)).objects[0].read_typetree()["m_Layer"] != 9
    fresh = P.rebuild_bundle_project(str(root), str(out))
    assert fresh["project_sync_status"] == "updated"
    assert U.load(str(out)).objects[0].read_typetree()["m_Layer"] == 9


@pytest.mark.parametrize("after_install", [False, True])
def test_edit_to_old_inode_during_directory_swap_is_not_discarded(tmp_path, after_install):
    root, sidecar, old, new = _prepared(tmp_path)
    out = tmp_path / "out.assetbundle"
    rename = os.replace
    retained = []
    def edit_after_directory_move(source, destination):
        result = rename(source, destination)
        src, dst = Path(source), Path(destination)
        if src == root and dst.name == "previous":
            retained.append(dst)
            if not after_install:
                (dst / sidecar.relative_to(root)).write_bytes(new)
        elif dst == root and src.name == "updated" and after_install:
            (retained[0] / sidecar.relative_to(root)).write_bytes(new)
        return result
    with patch.object(P.os, "replace", side_effect=edit_after_directory_move):
        result = P.rebuild_bundle_project(str(root), str(out))
    assert result["project_sync_status"] == "warning"
    if after_install:
        assert str(retained[0]) in result["project_sync_warning"]
        assert (retained[0] / sidecar.relative_to(root)).read_bytes() == new
        assert sidecar.read_bytes() != new
    else:
        assert sidecar.read_bytes() == new
        assert not list(root.parent.glob(".aov-project-*"))
    assert out.is_file()


def test_same_size_concurrent_edit_detected_when_file_stat_is_unchanged(tmp_path):
    root, sidecar, old, new = _prepared(tmp_path)
    out = tmp_path / "out.assetbundle"
    out.write_bytes(b"older output")
    assert old != new and len(old) == len(new)
    original = P._apply_project_objects

    def edit_while_rebuilding(*args, **kwargs):
        result = original(*args, **kwargs)
        sidecar.write_bytes(new)
        return result

    # Model Windows / network storage that has not refreshed the timestamps.
    with patch.object(P, "_project_path_stamp", return_value=(0, 0, 0, 0, 0)), \
         patch.object(P, "_apply_project_objects", side_effect=edit_while_rebuilding):
        with pytest.raises(OSError, match="source project changed during rebuild"):
            P.rebuild_bundle_project(str(root), str(out))
    assert out.read_bytes() == b"older output"
    assert sidecar.read_bytes() == new
