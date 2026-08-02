"""Load UnityPy's optional native accelerator without importing UnityPy itself.

Importing ``UnityPy`` initializes its global type package in every spawned
preview process. The accelerator is a standalone extension and can be loaded
directly, avoiding hundreds of megabytes of duplicated startup state.
"""

from __future__ import annotations

import glob
import importlib.util
import os
import sys
import sysconfig


def _load_extension():
    roots = []
    for key in ("purelib", "platlib"):
        root = sysconfig.get_paths().get(key)
        if root and root not in roots:
            roots.append(root)
    candidates = []
    module_folder = os.path.dirname(os.path.abspath(__file__))
    candidates.extend(glob.glob(os.path.join(module_folder, "native", "UnityPyBoost*.pyd")))
    candidates.extend(glob.glob(os.path.join(module_folder, "native", "UnityPyBoost*.so")))
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        bundled_folder = os.path.join(
            bundled_root, "AssetbundleUtils", "UnityPy_AOV", "native"
        )
        candidates.extend(glob.glob(os.path.join(bundled_folder, "UnityPyBoost*.pyd")))
        candidates.extend(glob.glob(os.path.join(bundled_folder, "UnityPyBoost*.so")))
    for root in roots:
        candidates.extend(glob.glob(os.path.join(root, "UnityPy", "UnityPyBoost*.pyd")))
        candidates.extend(glob.glob(os.path.join(root, "UnityPy", "UnityPyBoost*.so")))
    for path in candidates:
        try:
            spec = importlib.util.spec_from_file_location("UnityPyBoost", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        except (ImportError, OSError):
            continue
    return None


UnityPyBoost = _load_extension()
