"""Round trips for project file aliases that cannot be full Unity names."""
import json
from pathlib import Path

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from tests.fixtures import bundle_bytes, mesh_object
from tests.test_project_export_integrity import _named_object_project


def _component_budget(stem):
    for extension in (".raw", ".json", ".resS"):
        component = stem + extension
        assert len(component.encode("utf-8")) <= 255
        assert len(component.encode("utf-16-le")) // 2 <= 255


def test_portable_project_name_hash_and_signed_pathid():
    prefix = "🐉" * 120
    path_id = -2**63
    stem = P._asset_file_stem(prefix + "a", path_id, "Mesh")
    _component_budget(stem)
    alias, recovered_id = P._parse_asset_file_stem(stem)
    assert recovered_id == path_id and alias.startswith("🐉") and "~" in alias
    assert stem == P._asset_file_stem(prefix + "a", path_id, "Mesh")
    assert stem != P._asset_file_stem(prefix + "b", path_id, "Mesh")
    assert stem != P._asset_file_stem(prefix + "\udc80", path_id, "Mesh")


@pytest.mark.parametrize("name, expected", [
    ("short-资源", "short-资源_101"),
    ("con", "con_101"),
    ("cOn.ext", "_cOn.ext_101"),
    ("LPT¹.name", "_LPT¹.name_101"),
])
def test_safe_short_names_preserve_existing_convention(name, expected):
    assert P._asset_file_stem(name, 101, "Mesh") == expected


def test_long_cjk_source_name_survives_export_and_two_rebuilds(tmp_path):
    mesh = mesh_object()
    tree = mesh.read_typetree()
    full_name = "材" * 88 + "\udc8f"
    tree["m_Name"] = full_name
    mesh.save_typetree(tree)
    source = tmp_path / "source.assetbundle"
    source.write_bytes(bundle_bytes(mesh))
    root = Path(P.export_bundle_project(str(source), str(tmp_path / "projects"))["path"])

    first = P._load_manifest(str(root))["assets"][0]
    stem = first["file_stem"]
    _component_budget(stem)
    assert first["name"] == full_name
    assert (root / first["raw"]).stem == (root / first["editable"]).stem == stem
    for number in range(2):
        destination = tmp_path / f"rebuild-{number}.assetbundle"
        result = P.rebuild_bundle_project(str(root), str(destination))
        assert result["project_sync_status"] == "updated", result
        obj = U.load(str(destination)).objects[0]
        assert obj.read_typetree()["m_Name"] == full_name
        assert obj.get_raw_data() == mesh.get_raw_data()
        refreshed = P._load_manifest(str(root))["assets"][0]
        assert refreshed["file_stem"] == stem
        assert refreshed["name"] == full_name


@pytest.mark.parametrize("rename_raw", [False, True])
def test_long_json_rename_refreshes_project_without_losing_full_name(tmp_path, rename_raw):
    root, row = _named_object_project(tmp_path)
    editable = root / row["editable"]
    tree = json.loads(editable.read_text(encoding="utf-8"))
    full_name = "风" * 88
    tree["m_Name"] = full_name
    tree["m_IsActive"] = False
    editable.write_text(json.dumps(tree), encoding="utf-8")
    stem = P._asset_file_stem(full_name, row["path_id"], row["type"])
    _component_budget(stem)
    if rename_raw:
        raw = root / row["raw"]
        raw.rename(raw.with_name(stem + ".raw"))
    for number in range(2):
        destination = tmp_path / f"renamed-{number}.assetbundle"
        result = P.rebuild_bundle_project(str(root), str(destination))
        assert result["project_sync_status"] == "updated", result
        obj = U.load(str(destination)).objects[0]
        assert obj.read_typetree()["m_Name"] == full_name
        assert obj.read_typetree()["m_IsActive"] is False
        refreshed = P._load_manifest(str(root))["assets"][0]
        assert refreshed["name"] == full_name
        assert (root / refreshed["raw"]).stem == (root / refreshed["editable"]).stem == stem
        assert not editable.exists()


def test_legacy_export_scan_without_file_stem_preserves_unity_name(tmp_path):
    root, row = _named_object_project(tmp_path)
    manifest = P._load_manifest(str(root))
    for records in (manifest["assets"], manifest["backup_assets"]):
        records[0].pop("file_stem")
    P._write_json(str(root / "bundle_manifest.json"), manifest)
    destination = tmp_path / "legacy.assetbundle"
    rebuilt = P.rebuild_bundle_project(str(root), str(destination))
    assert rebuilt["project_sync_status"] == "updated", rebuilt
    assert U.load(str(destination)).objects[0].read_typetree()["m_Name"] == row["name"]
    assert (root / row["raw"]).is_file()
    assert (root / row["editable"]).is_file()
