import json
import multiprocessing
from pathlib import Path

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from tests.test_project_export_integrity import _named_object_project


def _project(tmp_path, name, layer):
    folder = tmp_path / name
    folder.mkdir()
    root, row = _named_object_project(folder)
    editable = root / row["editable"]
    data = json.loads(editable.read_text(encoding="utf-8"))
    data["m_Layer"] = layer
    editable.write_text(json.dumps(data), encoding="utf-8")
    return root


def _read_layer(output):
    return U.load(str(output)).objects[0].read_typetree()["m_Layer"]


def _rebuild_at_barrier(root, out, entered, release, replies):
    from unittest.mock import patch

    normal = P._apply_project_objects

    def sync(*args, **kwargs):
        result = normal(*args, **kwargs)
        if entered is not None:
            entered.set()
            assert release.wait(20)
        return result

    try:
        with patch.object(P, "_apply_project_objects", side_effect=sync):
            report = P.rebuild_bundle_project(root, out)
    except Exception as exc:
        replies.put(("error", root, str(exc)))
    else:
        replies.put(("ok", root, report["project_sync_status"]))


@pytest.mark.parametrize("existing", [False, True])
def test_two_processes_rebuilding_same_path_cannot_both_succeed(tmp_path, existing):
    roots = {
        str(_project(tmp_path, "project-a", 7)): 7,
        str(_project(tmp_path, "project-b", 9)): 9,
    }
    output = tmp_path / "out.assetbundle"
    if existing:
        output.write_bytes(b"previous result")
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    replies = context.Queue()
    manifests = {
        root: (Path(root) / "bundle_manifest.json").read_bytes() for root in roots
    }
    processes = [
        context.Process(
            target=_rebuild_at_barrier,
            args=(root, str(output), entered if n == 0 else None, release, replies),
        )
        for n, root in enumerate(roots)
    ]
    try:
        processes[0].start()
        assert entered.wait(20)
        processes[1].start()
        rows = [replies.get(timeout=15)]
        assert rows[0][0] == "error"
        release.set()
        rows.append(replies.get(timeout=20))
        for process in processes:
            process.join(5)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.terminate()
                process.join(5)
        replies.close()

    assert sorted(row[0] for row in rows) == ["error", "ok"]
    _, winner, sync_status = next(row for row in rows if row[0] == "ok")
    _, loser, message = next(row for row in rows if row[0] == "error")
    assert sync_status == "updated"
    assert "already reserved by another rebuild" in message
    assert (Path(loser) / "bundle_manifest.json").read_bytes() == manifests[loser]
    assert _read_layer(output) == roots[winner]
    assert {p.name for p in tmp_path.glob(".aov-*")} <= {".aov-rebuild-locks"}

    # A separate user-requested rebuild may overwrite the existing output.
    assert P.rebuild_bundle_project(loser, str(output))["project_sync_status"] == "updated"
    assert _read_layer(output) == roots[loser]


@pytest.mark.parametrize("exists", [False, True])
def test_externally_changed_output_is_preserved(tmp_path, monkeypatch, exists):
    root = _project(tmp_path, "project", 7)
    output = tmp_path / "out.assetbundle"
    if exists:
        output.write_bytes(b"prior")
    manifest = root / "bundle_manifest.json"
    prior_manifest = manifest.read_bytes()
    apply_objects = P._apply_project_objects

    def external_write(*args, **kwargs):
        result = apply_objects(*args, **kwargs)
        output.write_bytes(b"from external application")
        return result

    monkeypatch.setattr(P, "_apply_project_objects", external_write)
    with pytest.raises(OSError, match="output AB changed during rebuild"):
        P.rebuild_bundle_project(str(root), str(output))
    assert output.read_bytes() == b"from external application"
    assert manifest.read_bytes() == prior_manifest
    assert {p.name for p in tmp_path.glob(".aov-*")} <= {".aov-rebuild-locks"}


def test_output_remains_reserved_during_project_writeback(tmp_path, monkeypatch):
    first = _project(tmp_path, "first", 7)
    other = _project(tmp_path, "other", 9)
    output = tmp_path / "out.assetbundle"
    refresh = P._stage_project_refresh

    def attempt_competing_rebuild(*args, **kwargs):
        assert _read_layer(output) == 7  # First build has already published.
        with pytest.raises(OSError, match="already reserved"):
            P.rebuild_bundle_project(str(other), str(output))
        assert _read_layer(output) == 7
        return refresh(*args, **kwargs)

    monkeypatch.setattr(P, "_stage_project_refresh", attempt_competing_rebuild)
    assert P.rebuild_bundle_project(str(first), str(output))["project_sync_status"] == "updated"
    assert _read_layer(output) == 7


def test_external_new_output_survives_when_it_arrives_after_conflict_check(tmp_path, monkeypatch):
    from AssetbundleUtils import ProjectOutput

    root = _project(tmp_path, "project", 7)
    output = tmp_path / "out.assetbundle"
    manifest = root / "bundle_manifest.json"
    prior_manifest = manifest.read_bytes()
    publish = ProjectOutput._publish_new_file

    def external_arrival(staged, destination):
        output.write_bytes(b"arrived just before publish")
        return publish(staged, destination)

    monkeypatch.setattr(ProjectOutput, "_publish_new_file", external_arrival)
    with pytest.raises(OSError, match="output AB changed during rebuild"):
        P.rebuild_bundle_project(str(root), str(output))
    assert output.read_bytes() == b"arrived just before publish"
    assert manifest.read_bytes() == prior_manifest
    assert {p.name for p in tmp_path.glob(".aov-*")} <= {".aov-rebuild-locks"}


def test_exclusive_copy_fallback_never_replaces_existing_destination(tmp_path, monkeypatch):
    import errno
    import os

    from AssetbundleUtils import ProjectOutput

    if os.name == "nt":
        pytest.skip("Windows rename does not require hard links")

    root = _project(tmp_path, "project", 7)
    output = tmp_path / "out.assetbundle"

    def link_fails(staged, destination):
        output.write_bytes(b"arrived on a drive without links")
        raise OSError(errno.ENOTSUP, "links not supported")

    monkeypatch.setattr(ProjectOutput.os, "link", link_fails)
    with pytest.raises(OSError, match="output AB changed during rebuild"):
        P.rebuild_bundle_project(str(root), str(output))
    assert output.read_bytes() == b"arrived on a drive without links"
    assert {p.name for p in tmp_path.glob(".aov-*")} <= {".aov-rebuild-locks"}

    output.unlink()

    def link_unsupported(staged, destination):
        raise OSError(errno.ENOTSUP, "links not supported")

    monkeypatch.setattr(ProjectOutput.os, "link", link_unsupported)
    assert P.rebuild_bundle_project(str(root), str(output))["project_sync_status"] == "updated"
    assert _read_layer(output) == 7
    assert {p.name for p in tmp_path.glob(".aov-*")} <= {".aov-rebuild-locks"}


@pytest.mark.parametrize("existing", [False, True])
def test_rebuild_can_retry_after_process_is_killed_without_removing_marker(tmp_path, existing):
    first = _project(tmp_path, "first", 7)
    retry = _project(tmp_path, "retry", 9)
    output = tmp_path / "out.assetbundle"
    if existing:
        assert P.rebuild_bundle_project(str(first), str(output))["project_sync_status"] == "updated"
        original_output = output.read_bytes()

    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    replies = context.Queue()
    crashed = context.Process(
        target=_rebuild_at_barrier,
        args=(str(first), str(output), entered, release, replies),
    )
    manifest = first / "bundle_manifest.json"
    original_manifest = manifest.read_bytes()
    try:
        crashed.start()
        assert entered.wait(20)
        markers = list((tmp_path / ".aov-rebuild-locks").glob("*.lock"))
        assert len(markers) == 1 and markers[0].stat().st_size == 0
        with pytest.raises(OSError, match="already reserved"):
            P.rebuild_bundle_project(str(retry), str(output))
        crashed.kill()
        crashed.join(10)
        assert crashed.exitcode not in (None, 0)
    finally:
        if crashed.pid is not None and crashed.is_alive():
            crashed.kill()
            crashed.join(5)
        replies.close()

    assert manifest.read_bytes() == original_manifest
    assert markers[0].is_file()
    if existing:
        assert output.read_bytes() == original_output
    else:
        assert not output.exists()
    assert P.rebuild_bundle_project(str(retry), str(output))["project_sync_status"] == "updated"
    assert _read_layer(output) == 9
    assert markers[0].is_file()


def test_distinct_outputs_do_not_contend(tmp_path, monkeypatch):
    first = _project(tmp_path, "first", 7)
    second = _project(tmp_path, "second", 9)
    out1 = tmp_path / "one.assetbundle"
    out2 = tmp_path / "two.assetbundle"
    apply_objects = P._apply_project_objects

    def try_second(*args, **kwargs):
        if Path(args[1]) == first:
            assert P.rebuild_bundle_project(str(second), str(out2))["project_sync_status"] == "updated"
        return apply_objects(*args, **kwargs)

    monkeypatch.setattr(P, "_apply_project_objects", try_second)
    assert P.rebuild_bundle_project(str(first), str(out1))["project_sync_status"] == "updated"
    assert (_read_layer(out1), _read_layer(out2)) == (7, 9)
