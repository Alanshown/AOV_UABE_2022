"""Publish a fully verified batch of bundles without replacing existing files."""

import errno
import os
import shutil


_LINK_UNAVAILABLE = {
    errno.EACCES, errno.EPERM, errno.EXDEV, errno.ENOSYS, errno.EMLINK,
    errno.ENOTSUP, errno.EOPNOTSUPP,
}


def _private_opener(path, flags):
    return os.open(path, flags, 0o600)


def _identity(stat):
    return stat.st_dev, stat.st_ino


def _publish_one(staged, target, created):
    staged_identity = _identity(os.stat(staged))
    try:
        os.link(staged, target)
    except FileExistsError:
        raise
    except OSError as exc:
        # NTFS and common Linux filesystems support hard links; removable
        # media and some shared filesystems need an exclusive copy instead.
        if exc.errno not in _LINK_UNAVAILABLE and getattr(exc, "winerror", None) not in (1, 50, 1314):
            raise
        with open(staged, "rb") as source:
            with open(target, "xb", opener=_private_opener) as output:
                created.append((target, _identity(os.fstat(output.fileno()))))
                shutil.copyfileobj(source, output)
    else:
        created.append((target, staged_identity))


def publish_verified_bundle_batch(output, staged_bundles):
    """Write verified staged files as a batch, rolling back this batch on failure.

    Entries are (original basename, staged path), in source order. Hard links
    publish each file atomically where supported. This rollback handles reported
    errors, not abrupt process or system termination.
    """
    occupied = {name.casefold() for name in os.listdir(output)}
    created = []
    targets = []
    try:
        for basename, staged in staged_bundles:
            stem, extension = os.path.splitext(basename)
            sequence = 1
            while True:
                name = basename if sequence == 1 else f"{stem} ({sequence}){extension}"
                folded = name.casefold()
                if folded not in occupied:
                    target = os.path.join(output, name)
                    started = len(created)
                    try:
                        _publish_one(staged, target, created)
                    except FileExistsError:
                        if len(created) != started:
                            # A write failure after we created the output is
                            # not an occupied filename. Roll back the batch.
                            raise
                    else:
                        occupied.add(folded)
                        targets.append(target)
                        break
                    occupied.add(folded)
                sequence += 1
    except BaseException as exc:
        leftovers = []
        for target, expected_identity in reversed(created):
            try:
                if _identity(os.stat(target, follow_symlinks=False)) == expected_identity:
                    os.unlink(target)
            except FileNotFoundError:
                continue
            except OSError:
                leftovers.append(target)
        if leftovers:
            raise OSError(f"{exc}; could not roll back: {', '.join(leftovers)}") from exc
        raise
    return targets
