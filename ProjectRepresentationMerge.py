"""Conservative three-way merge of two edits to a serialized Unity TypeTree."""
import json
import math


_MISSING = object()


def _same(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(val, right[key]) for key, val in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and math.isnan(left):
        return math.isnan(right)
    return left is right or left == right


def _merge(base, raw, editable, location):
    if _same(raw, editable) or _same(base, editable):
        return raw
    if _same(base, raw):
        return editable
    if (
        isinstance(base, dict) and isinstance(raw, dict) and isinstance(editable, dict)
        # FileID and PathID form a unit; never combine divergent pointer edits.
        and not {"m_FileID", "m_PathID"}.issubset(base)
    ):
        result = {}
        for key in dict.fromkeys((*base, *raw, *editable)):
            value = _merge(
                base.get(key, _MISSING), raw.get(key, _MISSING),
                editable.get(key, _MISSING), f"{location}.{key}",
            )
            if value is not _MISSING:
                result[key] = value
        return result
    # Array positions may shift or refer to dependent arrays; a whole-array
    # conflict is safer than combining values against the wrong elements.
    raise ValueError(f"Conflicting RAW and JSON edits at {location}; resolve the conflict and retry")


def merge_typetrees(base, raw, editable, location, *, managed_name=None):
    # Typetree maps may be emitted as tuple pairs. JSON represents those with
    # arrays, so compare in the same normalized representation.
    normalized = lambda tree: json.loads(json.dumps(tree))
    base, raw, editable = map(normalized, (base, raw, editable))
    if managed_name is not None and all(
        isinstance(tree, dict) and "m_Name" in tree for tree in (base, raw, editable)
    ):
        # Project naming (filename and explicit JSON name) is reconciled by
        # the project scanner, independently of replacing donor RAW bytes.
        for tree in (base, raw, editable):
            tree["m_Name"] = managed_name
    return _merge(base, raw, editable, location)
