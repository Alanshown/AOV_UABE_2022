import json
import subprocess
from pathlib import Path

import pytest

from AssetbundleUtils import FbxImportWorker


def test_native_worker_death_retries_without_using_partial_output(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        out = Path(args[-1])
        assert not out.exists()
        if len(calls) == 1:
            out.write_text('incomplete', encoding='utf-8')
            return subprocess.CompletedProcess(args, -11)
        out.write_text(json.dumps({"sample": [0.5, 2, [33], [0.0] * 20]}), encoding='utf-8')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(FbxImportWorker.subprocess, "run", run)
    result = FbxImportWorker.sample_fbx_isolated("sample.fbx", 30.0, {"Bone": [33]}, [33])
    assert result[:3] == [0.5, 2, [33]]
    assert len(calls) == 2


def test_worker_domain_error_does_not_retry(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_text('{"error":"No animation stack"}', encoding='utf-8')
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(FbxImportWorker.subprocess, "run", run)
    with pytest.raises(ValueError, match="No animation stack"):
        FbxImportWorker.sample_fbx_isolated("sample.fbx", 30.0, {}, [])
    assert len(calls) == 1
