"""Read Unity resource slices without substituting unrelated serialized data."""

import os
from ..files import File
from ..streams import EndianBinaryReader


def get_resource_data(*args):
    """Read a bounded slice from a named resource or an explicit reader."""
    owned = False
    if len(args) == 4:
        reader, owned = _find_resource(args[0], args[1])
    elif len(args) == 3:
        reader = args[0]
    else:
        raise TypeError(f"3 or 4 arguments required, but only {len(args)} given")

    try:
        offset, size = int(args[-2]), int(args[-1])
        length = reader.Length
        if offset < 0 or size < 0 or offset + size > length:
            label = args[0] if len(args) == 4 else "<reader>"
            raise ValueError(
                f"Resource {label!r} slice [{offset}, {offset + size}) "
                f"exceeds {length} available bytes"
            )
        # Streamable readers report the underlying absolute file position but
        # interpret assignments relative to BaseOffset.
        position = reader.Position
        restore_position = position - reader.BaseOffset if hasattr(reader, "stream") else position
        try:
            reader.Position = offset
            data = reader.read_bytes(size)
            if len(data) != size:
                raise ValueError(f"Truncated resource: expected {size} bytes, got {len(data)}")
            return data
        finally:
            reader.Position = restore_position
    finally:
        if owned:
            reader.dispose()


def search_resource(res_path, assets_file):
    """Resolve the named resource. A returned disk reader belongs to the caller."""
    return _find_resource(res_path, assets_file)[0]


def _find_resource(res_path, assets_file):
    normalized = str(res_path or "").replace("\\", "/")
    base_name = normalized.rsplit("/", 1)[-1]
    if base_name in {"", ".", ".."}:
        raise FileNotFoundError(f"Can't find the resource file {res_path}")
    if base_name.endswith(".resource"):
        alias = base_name[:-len(".resource")] + ".assets.resS"
    elif base_name.endswith(".assets.resS"):
        alias = base_name[:-len(".assets.resS")] + ".resource"
    else:
        alias = base_name
    names = tuple(dict.fromkeys((base_name, alias)))

    nodes = getattr(getattr(assets_file, "parent", None), "files", None) or {}
    for key in dict.fromkeys((res_path, normalized, *names)):
        reader = nodes.get(key)
        if reader is not None:
            if isinstance(reader, File.File):
                reader = reader.reader
            return reader, False

    container = assets_file
    while container is not None:
        current_directory = getattr(container, "path", None)
        if current_directory and os.path.isdir(current_directory):
            for name in names:
                if not normalized.startswith("archive:/"):
                    direct = os.path.realpath(os.path.join(current_directory, normalized))
                    root = os.path.realpath(current_directory)
                    try:
                        contained = os.path.commonpath((root, direct)) == root
                    except ValueError:
                        contained = False
                    if name == base_name and contained and os.path.isfile(direct):
                        return EndianBinaryReader(open(direct, "rb")), True
                candidate = search_resource_file(current_directory, name)
                if candidate:
                    return EndianBinaryReader(open(candidate, "rb")), True
            break
        container = getattr(container, "parent", None)

    raise FileNotFoundError(f"Can't find the resource file {res_path}")


def search_resource_file(path, name):
    root = os.path.realpath(path)
    matches = set()
    for directory, _, files in os.walk(root, followlinks=False):
        for entry in files:
            if os.path.normcase(entry) != os.path.normcase(name):
                continue
            resolved = os.path.realpath(os.path.join(directory, entry))
            try:
                contained = os.path.commonpath((root, resolved)) == root
            except ValueError:
                contained = False
            if contained and os.path.isfile(resolved):
                matches.add(resolved)
    if len(matches) > 1:
        raise ValueError(f"Ambiguous resource {name!r}: {len(matches)} files match")
    return next(iter(matches), "")
