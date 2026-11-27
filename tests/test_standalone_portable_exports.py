"""Portable byte-budgeted filenames for standalone UI exports."""
from pathlib import Path

import pytest

from AssetbundleUtils import AssetsList as UI
from tests.fixtures import mesh_object
from tests.test_project_export_integrity import _export_callbacks


def _stat_portable_component(component):
    assert len(component.encode("utf-8")) <= 255
    assert len(component.encode("utf-16-le")) // 2 <= 255


@pytest.mark.parametrize(("extension", "path_id"), [
    (".dump.json", -2**63),
    (".assetbundle.json", None),
    (".fbx", None),
])
def test_single_save_default_budgets_full_filename(extension, path_id):
    name = "🐉" * 130 + "\udc80"
    generated = UI._standalone_export_filename(name, extension, path_id=path_id)
    _stat_portable_component(generated)
    assert generated.endswith((f"_{path_id}" if path_id is not None else "") + extension)
    assert generated.startswith("🐉") and "~" in generated
    assert generated != UI._standalone_export_filename(name + "a", extension, path_id=path_id)


def test_standalone_names_guard_windows_device_components():
    assert UI._standalone_export_filename("con", ".fbx") == "_con.fbx"
    assert UI._standalone_export_filename("con", ".obj", path_id=101) == "con_101.obj"
    assert UI._standalone_export_filename("lPT¹.mesh", ".obj", path_id=101) == "_lPT¹.mesh_101.obj"


def test_long_real_mesh_ui_export_twice_keeps_unity_asset_untouched(tmp_path, monkeypatch):
    name = "材" * 88
    mesh = mesh_object()
    tree = mesh.read_typetree()
    tree["m_Name"] = name
    mesh.save_typetree(tree)
    original_raw = bytes(mesh.get_raw_data())
    _export_callbacks(monkeypatch, tmp_path, [mesh], kind="export_mesh")
    _export_callbacks(monkeypatch, tmp_path, [mesh], kind="export_mesh")
    first = tmp_path / UI._standalone_export_filename(name, ".obj", path_id=mesh.path_id)
    second = tmp_path / UI._standalone_export_filename(
        name, ".obj", path_id=mesh.path_id, collision=2,
    )
    for file in (first, second):
        _stat_portable_component(file.name)
        assert file.read_bytes()
        assert name in file.read_text(encoding="utf-8")
    assert first.read_bytes() == second.read_bytes()
    assert len(list(tmp_path.iterdir())) == 2
    assert mesh.read_typetree()["m_Name"] == name
    assert bytes(mesh.get_raw_data()) == original_raw


def test_repeated_long_exports_allocate_exclusive_names(tmp_path):
    name = "独" * 100
    path_id = -2**63
    outputs = []
    for index in range(1, 13):
        def write(handle):
            handle.write(str(index).encode("ascii"))

        output = Path(UI._write_distinct_export(
            str(tmp_path), name, ".dat", write, path_id=path_id,
        ))
        _stat_portable_component(output.name)
        suffix = f"_{path_id}" + (f" ({index})" if index > 1 else "") + ".dat"
        assert output.name.endswith(suffix)
        outputs.append(output)
    assert len({x.name for x in outputs}) == len(outputs) == 12
    assert [x.read_text(encoding="ascii") for x in outputs] == [str(i) for i in range(1, 13)]
