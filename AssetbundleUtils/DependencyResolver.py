"""Resolve external CAB dependencies needed by Sprite atlases."""

from __future__ import annotations

import os
import sys
from typing import Dict, Iterable, List, Sequence, Tuple

from AssetbundleUtils import UnityPy_AOV


BUNDLE_EXTENSIONS = (".assetbundle", ".bundle", ".ab")


def _runtime_dependency_roots() -> List[str]:
    """Return sidecar locations that work in source and PyInstaller builds.

    Preview workers are spawned from the packaged executable.  In that case
    ``__file__`` points inside ``_internal`` and cannot by itself discover a
    sibling study workspace or a user-provided ``dependencies`` directory.
    Walk only a small, deterministic set of application ancestors; candidate
    bundle enumeration remains non-recursive.
    """
    anchors = [
        os.path.dirname(os.path.abspath(__file__)),
        os.path.dirname(os.path.abspath(sys.executable)),
        os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv else "",
        getattr(sys, "_MEIPASS", ""),
    ]
    roots: List[str] = []
    seen_anchors = set()
    for anchor in anchors:
        if not anchor:
            continue
        current = os.path.abspath(anchor)
        for _ in range(6):
            key = os.path.normcase(current)
            if key not in seen_anchors:
                seen_anchors.add(key)
                roots.extend((
                    current,
                    os.path.join(current, "dependencies"),
                    os.path.join(current, "unity2022_unitypy", "OUTPUT"),
                ))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    return roots


def _external_cabs(environment) -> List[str]:
    names = {
        os.path.basename(external.name)
        for asset_file in environment.assets
        for external in getattr(asset_file, "externals", ())
        if getattr(external, "name", None)
    }
    return sorted(
        name for name in names if environment.get_cab(name) is None
    )


def _candidate_roots(bundle_path: str, extra_roots: Sequence[str]) -> List[str]:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    study_root = os.path.dirname(project_root)
    configured = [
        value for value in os.environ.get("AOV_ASSET_DEPENDENCY_PATH", "").split(os.pathsep)
        if value
    ]
    roots = [
        os.path.dirname(os.path.abspath(bundle_path)),
        os.path.join(study_root, "unity2022_unitypy", "OUTPUT"),
        *_runtime_dependency_roots(),
        *extra_roots,
        *configured,
    ]
    result = []
    seen = set()
    for root in roots:
        root = os.path.abspath(os.path.expanduser(root))
        key = os.path.normcase(root)
        if key not in seen and os.path.isdir(root):
            seen.add(key)
            result.append(root)
    return result


def _candidate_bundles(bundle_path: str, roots: Iterable[str]) -> List[str]:
    source = os.path.normcase(os.path.abspath(bundle_path))
    candidates = []
    seen = {source}
    for root in roots:
        try:
            entries = os.scandir(root)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                lower = entry.name.lower()
                if not (lower.endswith(BUNDLE_EXTENSIONS) or "assetbundle" in lower):
                    continue
                path = os.path.abspath(entry.path)
                key = os.path.normcase(path)
                if key not in seen:
                    seen.add(key)
                    candidates.append(path)
    return sorted(
        candidates,
        key=lambda path: (
            0 if "allshared" in os.path.basename(path).lower() else 1,
            os.path.basename(path).lower(),
        ),
    )


def _contains_required_cab(path: str, required: Sequence[str]) -> bool:
    tokens = [name.casefold().encode("ascii", "ignore") for name in required]
    tokens = [token for token in tokens if token]
    if not tokens:
        return False
    overlap = max(map(len, tokens)) - 1
    tail = b""
    try:
        with open(path, "rb") as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    return False
                data = (tail + block).lower()
                if any(token in data for token in tokens):
                    return True
                tail = data[-overlap:] if overlap > 0 else b""
    except OSError:
        return False


def load_bundle_with_dependencies(
    bundle_path: str, extra_roots: Sequence[str] = (),
) -> Tuple[object, Dict[int, object], List[str]]:
    """Load a bundle, register matching sidecar CABs, and retain primary objects."""
    environment = UnityPy_AOV.load(bundle_path)
    primary_objects = {int(obj.path_id): obj for obj in environment.objects}
    unresolved = _external_cabs(environment)
    if not unresolved:
        return environment, primary_objects, []

    roots = _candidate_roots(bundle_path, extra_roots)
    for candidate in _candidate_bundles(bundle_path, roots):
        lower_name = os.path.basename(candidate).lower()
        if "shared" not in lower_name and not _contains_required_cab(candidate, unresolved):
            continue
        try:
            environment.load_file(candidate)
        except Exception:
            continue
        unresolved = _external_cabs(environment)
        if not unresolved:
            break
    return environment, primary_objects, unresolved
