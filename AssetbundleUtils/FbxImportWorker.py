"""Keep native FBX decoding out of the desktop process."""
from pathlib import Path
from tempfile import TemporaryDirectory
import gc
import json
import os
import subprocess
import sys


def sample_fbx_isolated(fbx_path, rate, target_names, transform_ids):
    payload = json.dumps([os.path.abspath(fbx_path), rate, target_names, transform_ids])
    frozen = getattr(sys, "frozen", False)
    command = [sys.executable, "--sample-fbx-worker"] if frozen else [
        sys.executable, "-m", "AssetbundleUtils.FbxImportWorker",
    ]
    options = {
        "capture_output": True, "timeout": 90,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    if not frozen:
        root = str(Path(__file__).resolve().parent.parent)
        existing = os.environ.get("PYTHONPATH")
        options["env"] = {**os.environ, "PYTHONPATH": os.pathsep.join(
            [root, existing] if existing else [root]
        )}
    # Windowed Windows builds have no Python stdin/stdout even with pipes.
    with TemporaryDirectory(prefix="aov-fbx-") as tempdir:
        request, output = (Path(tempdir) / key for key in ("request.json", "result.json"))
        request.write_text(payload, encoding="utf-8")
        command.extend((str(request), str(output)))
        for _ in range(2):
            output.unlink(missing_ok=True)
            try:
                result = subprocess.run(command, **options)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("FBX decoding timed out; no bundle was modified") from exc
            if result.returncode == 0:
                response = json.loads(output.read_text(encoding="utf-8"))
                if "error" in response:
                    raise ValueError(response["error"])
                return response["sample"]
            if result.returncode >= 0 and (result.returncode & 0xC0000000) != 0xC0000000:
                break
    raise RuntimeError(f"FBX decoder exited ({result.returncode}); no bundle was modified")


def worker_main(request, output):
    # ufbx 0.0.5 can leave wrappers that crash during Python cyclic GC after
    # the scene owner is freed. A one-shot process may skip that GC entirely;
    # the OS reclaims the native memory after the response has been closed.
    gc.disable()
    try:
        from AssetbundleUtils.AnimationPipeline import _sample_fbx_transforms

        arguments = json.loads(Path(request).read_text(encoding="utf-8"))
        response = {"sample": _sample_fbx_transforms(*arguments)}
        encoded = json.dumps(response, allow_nan=False, separators=(",", ":"))
    except Exception as exc:
        encoded = json.dumps({"error": f"{type(exc).__name__}: {exc}"})
    Path(output).write_text(encoded, encoding="utf-8")
    os._exit(0)


if __name__ == "__main__":
    worker_main(*sys.argv[1:])
