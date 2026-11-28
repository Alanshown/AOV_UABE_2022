"""Regression for Issue #2's blank, impossibly large Texture2D stream offset."""
from pathlib import Path

import pytest
from PIL import Image

from AssetbundleUtils import BundleProject as project, UnityPy_AOV
from AssetbundleUtils import AssetsList as ui
from tests.test_browser_workflows import Browser
from tests.test_bundle_project_compatibility import texture_bundle

OFFSET = 6500424412626944
LENGTH = 1513498
BAD_ID = 207


def objects(data):
    return {int(obj.path_id): obj for obj in UnityPy_AOV.load(data).objects}


def fixture(tmp_path):
    return texture_bundle(tmp_path, offset=OFFSET, size=LENGTH)


def test_export_and_rebuild_preserve_unreadable_texture_without_claiming_recovery(tmp_path):
    source = fixture(tmp_path)
    original = source.read_bytes()
    with pytest.raises(FileNotFoundError, match=str(OFFSET)):
        objects(original)[BAD_ID].read(False).image_data
    result = project.export_bundle_project(str(source), str(tmp_path / "projects"))
    root = Path(result["path"])
    row = next(a for a in project._load_manifest(str(root))["assets"] if a["path_id"] == BAD_ID)
    assert result["unreadable_textures"][0]["offset"] == OFFSET
    assert (root / row["raw"]).read_bytes() == objects(original)[BAD_ID].get_raw_data()
    assert "stream" not in row and "editable" not in row
    for index in range(2):
        out = tmp_path / f"rebuilt-{index}.assetbundle"
        rebuilt = project.rebuild_bundle_project(str(root), str(out))
        assert rebuilt["unreadable_textures"][0]["path_id"] == BAD_ID
        assert not rebuilt["texture_quality_preserved"]
        after = objects(out.read_bytes())
        assert after[BAD_ID].get_raw_data() == objects(original)[BAD_ID].get_raw_data()
        assert after[206].read(False).image_data == bytes(range(16))
    assert source.read_bytes() == original


def test_ui_save_reports_warning_and_does_not_drop_other_edits(tmp_path, monkeypatch):
    source = fixture(tmp_path)
    original = source.read_bytes()
    b = Browser(source)
    out = tmp_path / "output"
    out.mkdir()
    capture = []
    monkeypatch.setattr(ui, "askdirectory", lambda *args: str(out))
    monkeypatch.setattr(ui, "show_dialog", lambda *args, **_: capture.append(args) or True)
    try:
        mesh = next(o for o in b.env_list[0].objects if o.path_id == 101)
        tree = mesh.read_typetree()
        tree["m_Name"] = "edited triangle"
        mesh.save_typetree(tree)
        b.modified[(0,101)] = "Raw"
        b.save_bundles()
        event = b.finish()
        assert event[2] is None
        after = objects((out / source.name).read_bytes())
        assert after[101].read_typetree()["m_Name"] == "edited triangle"
        assert after[BAD_ID].get_raw_data() == objects(original)[BAD_ID].get_raw_data()
        assert after[206].read(False).image_data == bytes(range(16))
        assert capture[-1][-1] == "warning" and str(OFFSET) in capture[-1][2]
        assert source.read_bytes() == original

        bad = next(o for o in b.env_list[0].objects if o.path_id == BAD_ID)
        b.modified[(0, BAD_ID)] = "Raw"
        b.save_bundles()
        event = b.finish()
        assert event[2] and "empty stream path" in event[2]
        assert len(list(out.glob("*.assetbundle"))) == 1
    finally:
        b.executor.shutdown()


def test_project_does_not_accept_edits_to_unreadable_texture(tmp_path):
    source = fixture(tmp_path)
    root = Path(project.export_bundle_project(str(source), str(tmp_path / "projects"))["path"])
    row = next(a for a in project._load_manifest(str(root))["assets"] if a["path_id"] == BAD_ID)
    raw = root / row["raw"]
    raw.write_bytes(raw.read_bytes().replace(OFFSET.to_bytes(8, "little"), (OFFSET + 16).to_bytes(8, "little"), 1))
    out = tmp_path / "bad-edit.assetbundle"
    with pytest.raises(ValueError, match="empty stream path"):
        project.rebuild_bundle_project(str(root), str(out))
    assert not out.exists()

def test_ui_can_replace_bad_texture_and_save_without_warning(tmp_path, monkeypatch):
    source = fixture(tmp_path)
    b = Browser(source)
    b.target = next(o for o in b.env_list[0].objects if o.path_id == BAD_ID)
    output = tmp_path / "saved"
    output.mkdir()
    png = tmp_path / "replacement.png"
    color = (20, 40, 80, 255)
    Image.new("RGBA", (2, 2), color).save(png)
    capture = []
    monkeypatch.setattr(ui, "askopenfile", lambda *args: str(png))
    monkeypatch.setattr(ui, "askdirectory", lambda *args: str(output))
    monkeypatch.setattr(ui, "show_dialog", lambda *args, **_: capture.append(args) or True)
    try:
        b.import_texture()
        b.finish()
        assert b.modified[(0, BAD_ID)] == "Texture"
        b.save_bundles()
        event = b.finish()
        assert event[2] is None and not event[-1]
        after = objects((output / source.name).read_bytes())
        assert after[BAD_ID].read(False).image.convert("RGBA").getpixel((0,0)) == color
    finally:
        b.executor.shutdown()
