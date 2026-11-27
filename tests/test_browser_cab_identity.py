"""Reject multi-CAB PathID ambiguity before a browser can select any row."""
import queue
from types import SimpleNamespace

from AssetbundleUtils.AssetsList import AssetBrowser
from AssetbundleUtils import UnityPy_AOV as Unity
from tests.fixtures import bundle_bytes, mesh_object
from tests.test_project_export_integrity import _two_cabs


def _load_events(*paths):
    browser = SimpleNamespace(paths=[str(path) for path in paths], events=queue.Queue())
    browser._load_one = lambda *args: AssetBrowser._load_one(browser, *args)
    AssetBrowser._load_coordinator(browser)
    return list(browser.events.queue)


def test_browser_rejects_ambiguous_bundle_before_any_environment_or_rows(tmp_path):
    source = tmp_path / "ambiguous.assetbundle"
    original = _two_cabs()
    source.write_bytes(original)

    events = _load_events(source)
    assert [event[0] for event in events] == ["load_error", "load_done"]
    assert events[0][1:3] == (0, str(source))
    assert all(token in events[0][3] for token in
               ("PathID 101", "CAB-aov-test", "CAB-aov-second"))
    assert events[-1] == ("load_done", 0)
    assert source.read_bytes() == original


def test_browser_keeps_unambiguous_bundles_in_a_folder_with_colliding_bundle(tmp_path):
    first, rejected, last = [tmp_path / f"{n}.assetbundle" for n in range(3)]
    first.write_bytes(bundle_bytes(mesh_object(skin=False)))
    rejected.write_bytes(_two_cabs())
    last.write_bytes(bundle_bytes(mesh_object()))

    events = _load_events(first, rejected, last)
    environments = [event[1] for event in events if event[0] == "environment"]
    rows = [row for event in events if event[0] == "asset_batch" for row in event[1]]
    errors = [event[1] for event in events if event[0] == "load_error"]
    assert sorted(environments) == [0, 2]
    assert errors == [1]
    assert sorted(row.key for row in rows) == [(0, 101), (2, 101)]
    assert rows[0].obj.get_raw_data() != rows[1].obj.get_raw_data()
    assert events[-1] == ("load_done", 2)


def test_browser_accepts_distinct_pathids_in_same_multicab_bundle(tmp_path):
    donor = mesh_object(skin=False)
    donor.assets_file.objects[102] = donor.assets_file.objects.pop(101)
    donor.path_id = 102
    bundle = Unity.load(bundle_bytes(mesh_object())).file
    other = Unity.load(bundle_bytes(donor)).file
    bundle.files["CAB-aov-second"] = other.files["CAB-aov-test"]
    source = tmp_path / "distinct.assetbundle"
    source.write_bytes(bundle.save("lz4"))

    events = _load_events(source)
    assert not any(event[0] == "load_error" for event in events)
    rows = [row for event in events if event[0] == "asset_batch" for row in event[1]]
    assert {(row.key, row.obj.assets_file.name) for row in rows} == {
        ((0, 101), "CAB-aov-test"), ((0, 102), "CAB-aov-second"),
    }
    assert events[-1] == ("load_done", 2)
