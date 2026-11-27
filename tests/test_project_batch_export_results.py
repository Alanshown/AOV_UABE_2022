"""Batch project exports report every independent success and failure."""
from pathlib import Path

import pytest

from AssetbundleUtils import AssetsList as UI, BundleProject as P, UnityPy_AOV as U
from UI import I18n
from tests.fixtures import bundle_bytes, mesh_object
from tests.test_browser_workflows import Browser
from tests.test_project_export_integrity import _two_cabs


@pytest.mark.parametrize("locale", ["zh-cn", "en", "vn"])
@pytest.mark.parametrize(
    "outcomes, kind, title_key, status",
    [
        ((True, True), "info", "bundle_project_exported_title", "operation_done"),
        ((True, False, True), "warning", "bundle_project_export_partial_title",
         "bundle_project_export_partial_status"),
        ((False, False), "error", "bundle_project_export_failed_title",
         "bundle_project_export_failed_status"),
    ],
)
def test_project_batch_reports_actual_folders_and_failures(
    tmp_path, monkeypatch, locale, outcomes, kind, title_key, status,
):
    paths = []
    original = []
    for index, ok in enumerate(outcomes, 1):
        path = tmp_path / f"{'good' if ok else 'duplicate'}_{index}.assetbundle"
        binary = bundle_bytes(mesh_object()) if ok else _two_cabs()
        path.write_bytes(binary)
        original.append(binary)
        paths.append(path)

    destination = tmp_path / "projects"
    destination.mkdir()
    previous = destination / "previous"
    previous.mkdir()
    marker = previous / "already.txt"
    marker.write_bytes(b"do not touch")
    browser = Browser(paths[0])
    browser.is_directory = True
    browser.paths = [str(path) for path in paths]
    browser.env_list = [U.load(str(path)) for path in paths]
    dialogs, statuses = [], []
    browser._set_status = lambda key, **values: statuses.append((key, values))
    monkeypatch.setattr(I18n, "_current", locale)
    monkeypatch.setattr(UI, "askdirectory", lambda *args: str(destination))
    monkeypatch.setattr(UI, "show_dialog", lambda *args: dialogs.append(args))
    try:
        browser.export_bundle_projects()
        browser.finish()
    finally:
        browser.executor.shutdown()

    assert len(dialogs) == 1
    assert dialogs[0][1] == I18n.TEXT[locale][title_key]
    assert dialogs[0][3] == kind
    message = dialogs[0][2]
    assert statuses[-1][0] == status
    assert not browser.operation_busy
    if not all(outcomes):
        assert statuses[-1][1] == {"bundles": sum(outcomes), "failures": outcomes.count(False)}
        assert "PathID 101 occurs in multiple CABs" in message
        assert I18n.TEXT[locale]["bundle_project_export_errors"] in message
    if any(outcomes):
        assert I18n.TEXT[locale]["bundle_project_export_paths"] in message
    else:
        assert I18n.TEXT[locale]["bundle_project_export_paths"] not in message
    assert {entry.name for entry in destination.iterdir()} == {
        "previous", *(path.stem for path, ok in zip(paths, outcomes) if ok),
    }
    for path, data, ok in zip(paths, original, outcomes):
        assert path.read_bytes() == data
        if ok:
            project = destination / path.stem
            info = P._load_manifest(str(project))
            assert len(info["assets"]) == 1
            assert (project / info["source"]["backup"]).read_bytes() == data
            assert str(project) in message
        else:
            assert str(path) in message
    assert marker.read_bytes() == b"do not touch"


def test_batch_reports_source_not_loaded_and_continues(tmp_path, monkeypatch):
    valid = tmp_path / "valid.assetbundle"
    valid.write_bytes(bundle_bytes(mesh_object()))
    missing = tmp_path / "missing.assetbundle"
    destination = tmp_path / "projects"
    browser = Browser(valid)
    dialogs = []
    browser.env_list = [None, browser.env_list[0]]
    browser.paths = [str(missing), str(valid)]
    monkeypatch.setattr(UI, "askdirectory", lambda *args: str(destination))
    monkeypatch.setattr(UI, "show_dialog", lambda *args: dialogs.append(args))
    try:
        browser.export_bundle_projects()
        browser.finish()
    finally:
        browser.executor.shutdown()
    assert dialogs[0][3] == "warning"
    assert "missing.assetbundle" in dialogs[0][2]
    assert Path(destination, valid.stem, "bundle_manifest.json").is_file()
