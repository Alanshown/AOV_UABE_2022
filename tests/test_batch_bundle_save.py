import errno
from pathlib import Path
import threading

import pytest

from AssetbundleUtils import AssetsList as UI, BundleSaveTransaction as T, UnityPy_AOV as U
from tests.fixtures import bundle_bytes, mesh_object
from tests.test_browser_workflows import Browser


def _browser(tmp_path):
    first = tmp_path / "first.assetbundle"
    second = tmp_path / "second.assetbundle"
    first.write_bytes(bundle_bytes(mesh_object()))
    second.write_bytes(bundle_bytes(mesh_object(skin=False)))
    b = Browser(first)
    b.paths.append(str(second))
    b.env_list.append(U.load(str(second)))
    return b, first, second


def _dialogs(monkeypatch, output):
    monkeypatch.setattr(UI, "show_dialog", lambda *a, **kw: True)
    monkeypatch.setattr(UI, "askdirectory", lambda *a, **kw: str(output))


def test_save_to_input_dir_preserves_originals_and_existing_outputs(tmp_path, monkeypatch):
    b, first, second = _browser(tmp_path)
    original = {p: p.read_bytes() for p in (first, second)}
    existing = tmp_path / "first (2).assetbundle"
    existing.write_bytes(b"existing output")
    _dialogs(monkeypatch, tmp_path)
    try:
        obj = b.target
        tree = obj.read_typetree()
        tree["m_Name"] = "EditedMesh"
        obj.save_typetree(tree)
        b.modified[(0, int(obj.path_id))] = "Dump"
        b.save_bundles()
        event = b.finish()
        assert event[2] is None
        assert {Path(p).name for p in event[1]} == {"first (3).assetbundle", "second (2).assetbundle"}
        assert not b.operation_busy
    finally:
        b.executor.shutdown()
    assert existing.read_bytes() == b"existing output"
    assert all(p.read_bytes() == original[p] for p in original)
    edited = U.load(str(tmp_path / "first (3).assetbundle")).objects[0]
    assert edited.peek_name("") == "EditedMesh"
    assert not list(tmp_path.glob(".aov-save-*"))


def test_failed_second_validation_never_publishes_first(tmp_path, monkeypatch):
    b, _, _ = _browser(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    _dialogs(monkeypatch, out)
    # Force the second worker to fail only after the first has generated
    # valid bundle bytes; the first bundle may only live in staging.
    ready = threading.Event()
    first_save = b.env_list[0].file.save
    def save_first(*args):
        data = first_save(*args)
        ready.set()
        return data
    def fail_second(*args):
        assert ready.wait(5)
        assert not list(out.glob("*.assetbundle"))
        raise OSError("second verification failed")
    monkeypatch.setattr(b.env_list[0].file, "save", save_first)
    monkeypatch.setattr(b.env_list[1].file, "save", fail_second)
    try:
        b.save_bundles()
        event = b.finish()
        assert event[1] is None and event[2] == "second.assetbundle: second verification failed"
        assert not b.operation_busy and list(out.iterdir()) == []
    finally:
        b.executor.shutdown()


def test_publish_falls_back_to_exclusive_copy_without_overwrite(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.write_bytes(b"verified")
    existing = tmp_path / "CASE.assetbundle"
    existing.write_bytes(b"untouched")
    def no_links(*args):
        raise OSError(errno.ENOTSUP, "unsupported filesystem")
    monkeypatch.setattr(T.os, "link", no_links)
    saved = T.publish_verified_bundle_batch(tmp_path, [("case.assetbundle", stage)] * 2)
    assert existing.read_bytes() == b"untouched"
    assert [Path(f).name for f in saved] == ["case (2).assetbundle", "case (3).assetbundle"]
    assert all(Path(f).read_bytes() == b"verified" for f in saved)


def test_publish_error_rolls_back_existing_new_files_and_partial_copy(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.write_bytes(b"verified")
    existing = tmp_path / "existing.assetbundle"
    existing.write_bytes(b"keep")
    def no_links(*args):
        raise OSError(errno.ENOTSUP, "unsupported filesystem")
    monkeypatch.setattr(T.os, "link", no_links)
    copy = T.shutil.copyfileobj
    def fail_after_one(source, dest):
        if dest.name.endswith("second.assetbundle"):
            dest.write(b"partial")
            raise OSError(errno.ENOSPC, "out of space")
        return copy(source, dest)
    monkeypatch.setattr(T.shutil, "copyfileobj", fail_after_one)
    with pytest.raises(OSError, match="out of space"):
        T.publish_verified_bundle_batch(tmp_path, [("first.assetbundle", stage), ("second.assetbundle", stage)])
    assert set(tmp_path.iterdir()) == {stage, existing}
    assert existing.read_bytes() == b"keep"


def test_name_occupied_during_publish_is_never_replaced(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.write_bytes(b"verified")
    original_publish = T._publish_one
    def competing_file(staged, target, created):
        if str(target).endswith("first.assetbundle"):
            Path(target).write_bytes(b"competing file")
        return original_publish(staged, target, created)
    monkeypatch.setattr(T, "_publish_one", competing_file)
    paths = T.publish_verified_bundle_batch(tmp_path, [("first.assetbundle", stage)])
    assert Path(paths[0]).name == "first (2).assetbundle"
    assert (tmp_path / "first.assetbundle").read_bytes() == b"competing file"
    assert Path(paths[0]).read_bytes() == b"verified"
