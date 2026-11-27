from pathlib import Path

from PIL import Image
import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV
from tests.test_bundle_project_compatibility import texture_bundle


_PATH_ID = 206
_RED = (210, 20, 40, 255)
_GREEN = (15, 230, 60, 255)


def _project(tmp_path):
    source = texture_bundle(tmp_path)
    result = P.export_bundle_project(str(source), str(tmp_path / "export"))
    root = Path(result["path"])
    row = _row(root)
    return root, root / row["editable"], root / row["stream"]["payload"]


def _row(root):
    return next(
        row for row in P._load_manifest(str(root))["assets"]
        if row["path_id"] == _PATH_ID
    )


def _pixels(output):
    obj = next(o for o in UnityPy_AOV.load(str(output)).objects if o.path_id == _PATH_ID)
    texture = obj.read(False)
    return texture.image.convert("RGBA").getpixel((0, 0)), bytes(texture.image_data)


@pytest.mark.parametrize("mode", ["png", "ress", "identical"])
def test_edit_synced_stream_texture_single_or_identical_sources_survive_rebuilds(tmp_path, mode):
    root, png, sidecar = _project(tmp_path)
    if mode in {"png", "identical"}:
        Image.new("RGBA", (2, 2), _GREEN).save(png)
    if mode in {"ress", "identical"}:
        sidecar.write_bytes(bytes(_RED if mode == "ress" else _GREEN) * 4)
    expected = _RED if mode == "ress" else _GREEN
    for count in range(2):
        out = tmp_path / f"output{count}.assetbundle"
        report = P.rebuild_bundle_project(str(root), str(out))
        assert report["project_sync_status"] == "updated", report
        assert _pixels(out) == (expected, bytes(expected) * 4)
        with Image.open(png) as preview:
            assert preview.convert("RGBA").getpixel((0, 0)) == expected
        assert sidecar.read_bytes() == bytes(expected) * 4


def test_conflicting_png_and_sidecar_fail_without_touching_project_or_output(tmp_path):
    root, png, sidecar = _project(tmp_path)
    out = tmp_path / "existing.assetbundle"
    out.write_bytes(b"do not replace")
    Image.new("RGBA", (2, 2), _GREEN).save(png)
    sidecar.write_bytes(bytes(_RED) * 4)
    before = P._project_snapshot(str(root))

    for _ in range(2):
        with pytest.raises(ValueError, match=r"Texture2D PathID 206: conflicting PNG and .resS edits"):
            P.rebuild_bundle_project(str(root), str(out))
        assert out.read_bytes() == b"do not replace"
        assert P._project_snapshot(str(root)) == before

    # Reconcile either input explicitly; the same project remains reusable.
    sidecar.write_bytes(bytes(_GREEN) * 4)
    report = P.rebuild_bundle_project(str(root), str(out))
    assert report["project_sync_status"] == "updated", report
    assert _pixels(out) == (_GREEN, bytes(_GREEN) * 4)
