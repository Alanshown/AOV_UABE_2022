import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from AssetbundleUtils import BundleProject as project, UnityPy_AOV
from AssetbundleUtils.UnityPy_AOV.helpers.ResourceReader import get_resource_data
from AssetbundleUtils.UnityPy_AOV.streams import EndianBinaryReader
from tests.fixtures import bundle_bytes, mesh_object
from tests.test_project_export_integrity import _named_object_project


def test_named_missing_resource_is_not_read_from_cab(tmp_path):
    af = SimpleNamespace(reader=EndianBinaryReader(b"not resource data"),
                         parent=SimpleNamespace(files={}, parent=None, path=tmp_path))
    with pytest.raises(FileNotFoundError, match="Missing.resS"):
        get_resource_data("archive:/CAB-test/Missing.resS", af, 0, 4)


def test_named_bundle_and_disk_slices_preserve_bounds_and_cursor(tmp_path):
    af = SimpleNamespace(parent=SimpleNamespace(files={}, parent=None, path=tmp_path))
    reader = EndianBinaryReader(b"0123456789")
    reader.Position = 7
    af.parent.files["data.resS"] = reader
    assert get_resource_data("archive:/CAB-test/data.resS", af, 2, 4) == b"2345"
    assert reader.Position == 7
    for offset, size in ((-1, 1), (0, -1), (9, 2)):
        with pytest.raises(ValueError, match="available bytes"):
            get_resource_data("data.resS", af, offset, size)
    assert reader.Position == 7
    (tmp_path / "a.resource").write_bytes(b"abcdefghij")
    assert get_resource_data("archive:/CAB-test/a.assets.resS", af, 3, 3) == b"def"


def test_ambiguous_disk_resources_are_not_guessed(tmp_path):
    af = SimpleNamespace(parent=SimpleNamespace(files={}, parent=None, path=tmp_path))
    for folder in ["first", "second"]:
        path = tmp_path / folder
        path.mkdir()
        (path / "data.resS").write_text(folder)
    with pytest.raises(ValueError, match="Ambiguous resource"):
        get_resource_data("archive:/CAB-test/data.resS", af, 0, 1)
    assert get_resource_data("first/data.resS", af, 0, 5) == b"first"


def exported(tmp_path):
    source = tmp_path / "input.assetbundle"
    source.write_bytes(bundle_bytes(mesh_object()))
    info = project.export_bundle_project(str(source), str(tmp_path / "projects"), export_decoded=False)
    return Path(info["path"]), tmp_path / "output.assetbundle"


def tree_bytes(directory):
    return {f.relative_to(directory): f.read_bytes() for f in directory.rglob("*") if f.is_file()}


def test_partial_writeback_error_does_not_modify_source_or_lose_output(tmp_path):
    directory, out = exported(tmp_path)
    before = tree_bytes(directory)

    def fail_after_write(staged, *args):
        Path(staged, "bundle_manifest.json").write_text("partial")
        Path(staged, "unexpected.txt").write_text("partial")
        raise OSError("simulated manifest write failure")

    with patch.object(project, "_refresh_project_manifest", side_effect=fail_after_write):
        info = project.rebuild_bundle_project(str(directory), str(out))
    assert info["project_sync_status"] == "warning"
    assert "simulated manifest write failure" in info["project_sync_warning"]
    assert len(list(UnityPy_AOV.load(out.read_bytes()).objects)) == 1
    assert tree_bytes(directory) == before
    assert not list(directory.parent.glob(".aov-project-*"))


def test_writeback_commit_failure_rolls_original_back(tmp_path):
    directory, out = exported(tmp_path)
    before = tree_bytes(directory)
    os_replace = os.replace

    def fail_stage_install(source, dest):
        if Path(source).name == "updated" and Path(dest) == directory:
            raise PermissionError("simulated directory rename failure")
        return os_replace(source, dest)

    with patch.object(project.os, "replace", side_effect=fail_stage_install):
        info = project.rebuild_bundle_project(str(directory), str(out))
    assert info["project_sync_status"] == "warning"
    assert "simulated directory rename failure" in info["project_sync_warning"]
    assert out.is_file()
    assert tree_bytes(directory) == before
    assert not list(directory.parent.glob(".aov-project-*"))


def test_noop_project_writeback_does_not_grow_manifest(tmp_path):
    directory, out = exported(tmp_path)
    manifest_path = directory / "bundle_manifest.json"
    before = manifest_path.read_bytes()
    for _ in range(3):
        info = project.rebuild_bundle_project(str(directory), str(out))
        assert info["project_sync_status"] == "updated"
        assert info["project_sync_warning"] is None
        assert manifest_path.read_bytes() == before
    assert not list(directory.parent.glob(".aov-project-*"))


def test_project_history_keeps_legacy_records_and_only_appends_on_changed_state(tmp_path):
    directory, asset = _named_object_project(tmp_path)
    manifest_path = directory / "bundle_manifest.json"
    manifest = project._load_manifest(str(directory))
    legacy_entry = {"added": [], "stream_sync": {"resources": ["legacy"]}}
    manifest["change_history"] = [legacy_entry, legacy_entry]
    project._write_json(str(manifest_path), manifest)
    editable = directory / asset["editable"]
    original_tree = json.loads(editable.read_text(encoding="utf-8"))
    out = tmp_path / "out.assetbundle"

    def rebuild_and_get_history(layer):
        result = project.rebuild_bundle_project(str(directory), str(out))
        assert result["project_sync_status"] == "updated"
        assert UnityPy_AOV.load(out.read_bytes()).objects[0].read_typetree()["m_Layer"] == layer
        return project._load_manifest(str(directory))["change_history"]

    original_layer = original_tree["m_Layer"]
    changed_layer = original_layer + 1
    assert rebuild_and_get_history(original_layer) == [legacy_entry, legacy_entry]
    project._write_json(str(editable), {**original_tree, "m_Layer": changed_layer})
    modified_history = rebuild_and_get_history(changed_layer)
    assert modified_history[:2] == [legacy_entry, legacy_entry]
    assert len(modified_history) == 3
    assert modified_history[-1]["edited_representations"] == {"typetrees": 1}
    after_edit = manifest_path.read_bytes()
    for _ in range(3):
        assert rebuild_and_get_history(changed_layer) == modified_history
        assert manifest_path.read_bytes() == after_edit

    # Revert to the original AB payload. Backup-relative reports can miss
    # this transition, but the previous persisted state is different.
    project._write_json(str(editable), original_tree)
    reverted_history = rebuild_and_get_history(original_layer)
    assert reverted_history[:3] == modified_history
    assert len(reverted_history) == 4
    assert rebuild_and_get_history(original_layer) == reverted_history


def test_cannot_overwrite_exported_project_files(tmp_path):
    directory, _ = exported(tmp_path)
    before = tree_bytes(directory)
    with pytest.raises(ValueError, match="outside the exported project"):
        project.rebuild_bundle_project(str(directory), str(directory / "backup" / "input.assetbundle"))
    assert tree_bytes(directory) == before


def test_ui_reports_saved_output_with_writeback_warning(tmp_path):
    import AssetbundleUtils.AssetsList as ui
    from tests.test_browser_workflows import Browser

    directory, out = exported(tmp_path)
    b = Browser(tmp_path / "input.assetbundle")
    try:
        with (
            patch.object(ui, "askdirectory", return_value=str(directory)),
            patch.object(ui, "asksavefile", return_value=str(out)),
            patch.object(ui, "show_dialog", return_value=True) as dialog,
            patch.object(project, "_refresh_project_manifest",
                         side_effect=PermissionError("no write access")),
        ):
            b.rebuild_from_bundle_project()
            b.finish()
            assert out.is_file()
            assert dialog.call_args.args[-1] == "warning"
            assert str(out) in dialog.call_args.args[2]
            assert "no write access" in dialog.call_args.args[2]
    finally:
        b.executor.shutdown()
