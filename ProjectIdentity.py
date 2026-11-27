"""Plan a project ID transition without attaching references by name alone."""

from collections import defaultdict


def plan_asset_assignments(current, scanned, manifest):
    """Return final slot -> prior slot. An unassigned slot is an addition."""
    prior = current
    pairs = {}
    taken = set()

    def assign(old, new):
        if old in pairs or new in taken:
            raise ValueError(f"Conflicting PathID migration: {old} -> {new}")
        pairs[old] = new
        taken.add(new)

    explicit = manifest.get("path_id_moves", [])
    if not isinstance(explicit, list):
        raise ValueError("path_id_moves must be a list of explicit asset moves")
    for move in explicit:
        try:
            kind = move["type"]
            old = (kind, int(move["from_path_id"]))
            new = (kind, int(move["to_path_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Each path_id_moves entry needs type, from_path_id and to_path_id") from exc
        if old not in prior or new not in scanned or old == new:
            raise ValueError(f"PathID move needs a known source and a different destination: {old} -> {new}")
        assign(old, new)

    unchanged = {
        key for key in (set(prior) & set(scanned)) - pairs.keys() - taken
        if prior[key].get("sha256") == scanned[key]["sha256"]
    }
    old_hashes, new_hashes = defaultdict(list), defaultdict(list)
    for key in sorted(set(prior) - pairs.keys() - unchanged):
        item = prior[key]
        if item.get("sha256"):
            old_hashes[(key[0], item["sha256"])].append(key)
    for key in sorted(set(scanned) - taken - unchanged):
        new_hashes[(key[0], scanned[key]["sha256"])].append(key)
    inferred = {
        old_hashes[digest][0]: new_hashes[digest][0]
        for digest in old_hashes.keys() & new_hashes.keys()
        if len(old_hashes[digest]) == len(new_hashes[digest]) == 1
        and old_hashes[digest][0] != new_hashes[digest][0]
    }

    # An occupied source slot must also get an incoming asset (swap or a
    # chain). Otherwise this could be an edited asset plus a separate clone.
    # A destination's previous owner must move, not silently be replaced.
    while True:
        combined = pairs | inferred
        occupied_new = set(combined.values())
        invalid = {
            old for old, new in inferred.items()
            if (old in scanned and old not in occupied_new)
            or (new in prior and new not in combined)
        }
        if not invalid:
            break
        for old in invalid:
            del inferred[old]
    for old, new in sorted(inferred.items()):
        assign(old, new)

    destinations = set(pairs.values())
    for old, new in pairs.items():
        if old in scanned and old not in destinations:
            raise ValueError(f"PathID move leaves its original slot unaccounted for: {old}")
        if new in prior and new not in pairs:
            raise ValueError(f"PathID move would replace an independently occupied slot: {new}")

    mapping = {new: old for old, new in pairs.items()}
    for key in (set(prior) & set(scanned)) - destinations:
        if key not in pairs:
            mapping[key] = key

    lost = set(prior) - set(mapping.values())
    created = set(scanned) - mapping.keys()
    ambiguous = sorted({kind for kind, _ in lost} & {kind for kind, _ in created})
    if ambiguous and manifest.get("allow_independent_asset_changes") is not True:
        raise ValueError(
            f"Cannot decide whether {', '.join(ambiguous)} entries are moves or separate deletions "
            "and additions. Declare path_id_moves in bundle_manifest.json, or set "
            "allow_independent_asset_changes to true if they really are independent."
        )
    return mapping
