import json
from pathlib import Path

import pytest

from AssetbundleUtils import BundleProject as P, UnityPy_AOV as U
from tests.test_project_additions import fingerprint, write_fixture


def _sources(root):
    paths = []
    for filename, dependency in (("original", "CAB-expected"), ("other", "CAB-external")):
        path = write_fixture(
            root / f"{filename}.assetbundle", mesh_id=101, cab="CAB-target",
            add_filter=True, external=(f"archive:/{dependency}/{dependency}", 302),
        )
        # Keep the byte lengths equal to prove that digest verification, not
        # merely the size check, rejects another valid AssetBundle.
        uncompressed = U.load(str(path)).file.save("none")
        path.write_bytes(uncompressed)
        paths.append(path)
    return paths


def _project(tmp_path):
    trusted, other = _sources(tmp_path)
    exported = P.export_bundle_project(str(trusted), str(tmp_path / "export"))
    return trusted, other, Path(exported["path"]), Path(exported["backup"])


def test_rebuild_rejects_same_size_wrong_backup_without_output_or_project_write(tmp_path):
    trusted, other, project, backup = _project(tmp_path)
    output = tmp_path / "out.assetbundle"
    P.rebuild_bundle_project(str(project), str(output))
    existing_output = output.read_bytes()
    assert U.load(existing_output).assets[0].externals[0].path.endswith("CAB-expected")
    data = other.read_bytes()
    assert data != trusted.read_bytes() and len(data) == len(trusted.read_bytes())
    backup.write_bytes(data)
    before = fingerprint(project)

    with pytest.raises(ValueError, match="backup SHA-256 mismatch:"):
        P.rebuild_bundle_project(str(project), str(output))
    assert output.read_bytes() == existing_output
    assert fingerprint(project) == before

    backup.write_bytes(trusted.read_bytes())
    assert P.rebuild_bundle_project(str(project), str(output))["project_sync_status"] == "updated"
    assert U.load(str(output)).assets[0].externals[0].path.endswith("CAB-expected")


def test_cross_bundle_stage_rejects_wrong_backup_and_preserves_workspace(tmp_path):
    _, other, project, backup = _project(tmp_path)
    backup.write_bytes(other.read_bytes())
    donor = write_fixture(tmp_path / "donor.assetbundle", mesh_id=909, cab="CAB-donor")
    before = fingerprint(project)

    with pytest.raises(ValueError, match="backup SHA-256 mismatch"):
        P.stage_cross_bundle_assets(str(project), {str(donor): [909]})
    assert fingerprint(project) == before
    assert not list(project.parent.glob(".aov-import-*"))


@pytest.mark.parametrize("memory_limit", [1024 * 1024, 1])
def test_parser_consumes_checked_snapshot_not_live_backup_path(tmp_path, monkeypatch, memory_limit):
    _, other, project, backup = _project(tmp_path)
    manifest = P._load_manifest(str(project))
    original_parse = U.Environment.load_file
    monkeypatch.setattr(P, "_BACKUP_SNAPSHOT_MEMORY_LIMIT", memory_limit)

    def parse_after_live_file_changes(self, file, *, name):
        assert not isinstance(file, (str, Path))
        backup.write_bytes(other.read_bytes())
        return original_parse(self, file, name=name)

    monkeypatch.setattr(U.Environment, "load_file", parse_after_live_file_changes)
    parsed = P._load_verified_project_backup(str(project), manifest)
    assert parsed.path == str(backup.parent)
    assert parsed.file.name == backup.name
    assert parsed.assets[0].externals[0].path.endswith("CAB-expected")
    assert backup.read_bytes() == other.read_bytes()


def test_legacy_template_alias_rebuilds_when_its_hash_is_known(tmp_path):
    _, _, project, _ = _project(tmp_path)
    path = project / "bundle_manifest.json"
    manifest = P._load_manifest(str(project))
    manifest["$schema"] = P.SCHEMA_V1
    manifest["source"].pop("backup")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "legacy.assetbundle"
    P.rebuild_bundle_project(str(project), str(output))
    assert U.load(str(output)).assets[0].externals[0].path.endswith("CAB-expected")
    assert P._load_manifest(str(project))["$schema"] == P.SCHEMA_V1


@pytest.mark.parametrize("schema", [P.SCHEMA_V1, P.SCHEMA])
@pytest.mark.parametrize("malformed", [False, True])
def test_absent_or_invalid_digest_fails_closed(tmp_path, schema, malformed):
    _, _, project, _ = _project(tmp_path)
    path = project / "bundle_manifest.json"
    manifest = P._load_manifest(str(project))
    manifest["$schema"] = schema
    if malformed:
        manifest["source"]["sha256"] = "not a digest"
    else:
        manifest["source"].pop("sha256")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    output = tmp_path / "invalid.assetbundle"

    with pytest.raises(ValueError, match="backup SHA-256 is missing or invalid"):
        P.rebuild_bundle_project(str(project), str(output))
    assert not output.exists()
