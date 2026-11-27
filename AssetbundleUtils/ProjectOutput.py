"""Coordinate publication of a rebuilt project bundle among application instances."""

from contextlib import contextmanager
import errno
import hashlib
import logging
import os
import shutil
import stat
import unicodedata


_LOG = logging.getLogger(__name__)
_NO_HARDLINK = {
    errno.EACCES, errno.EPERM, errno.EXDEV, errno.ENOSYS, errno.EMLINK,
    errno.ENOTSUP, errno.EOPNOTSUPP,
}


def rebuild_output_snapshot(path):
    """Return a non-content identity for the selected destination and its parent."""
    parent = os.path.normcase(os.path.realpath(os.path.dirname(path)))
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return parent, None
    if not stat.S_ISREG(current.st_mode):
        raise OSError(f"Choose a regular file for the rebuilt AB: {path}")
    return parent, (
        current.st_dev, current.st_ino, current.st_size,
        current.st_mtime_ns, current.st_ctime_ns,
    )


def _lock_reservation(fd):
    if os.name == "nt":
        import msvcrt

        # Byte ranges beyond EOF are valid; no marker contents are needed.
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextmanager
def reserve_project_output(path):
    # Keep the marker inode stable forever: deleting it while another process
    # has it open could let a third writer lock a different inode. Only the
    # kernel lock reserves the output, and the kernel drops it on process exit.
    # The marker is empty and grouped away from the user's bundle outputs.
    parent = os.path.realpath(os.path.dirname(path))
    markers = os.path.join(parent, ".aov-rebuild-locks")
    os.makedirs(markers, exist_ok=True)
    name = os.fsencode(unicodedata.normalize("NFC", os.path.basename(path)).casefold())
    key = hashlib.sha256(name).hexdigest()
    marker = os.path.join(markers, f"{key}.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
    fd = os.open(marker, flags | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError(f"Choose a different destination directory: {marker}")
        try:
            _lock_reservation(fd)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            raise OSError(
                f"The output AB is already reserved by another rebuild: {path}. "
                "Choose another filename or wait for the other rebuild."
            ) from exc
        yield
    finally:
        os.close(fd)


def _publish_new_file(staged, path):
    if os.name == "nt":
        # On Windows rename refuses to overwrite any file already at path.
        os.rename(staged, path)
        return
    try:
        os.link(staged, path)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in _NO_HARDLINK:
            raise
    # Some removable/shared filesystems cannot hard-link. Do not fall back to
    # replacing a just-arrived output: exclusively create and copy instead.
    created = None
    try:
        with open(staged, "rb") as source:
            with open(path, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as output:
                meta = os.fstat(output.fileno())
                created = (meta.st_dev, meta.st_ino)
                shutil.copyfileobj(source, output)
    except BaseException:
        if created is not None:
            try:
                current = os.lstat(path)
                if (current.st_dev, current.st_ino) == created:
                    os.unlink(path)
            except FileNotFoundError:
                pass
            except OSError:
                _LOG.exception("Could not remove partial rebuilt AB %s", path)
        raise


def publish_rebuild_output(staged, path, baseline):
    """Permit sequential overwrite, never overwrite a concurrent app publication."""
    conflict = (
        f"The output AB changed during rebuild: {path}. "
        "No output or project changes were applied. "
        "Choose a different filename or start a new rebuild."
    )
    if rebuild_output_snapshot(path) != baseline:
        raise OSError(conflict)
    if baseline[1] is None:
        try:
            _publish_new_file(staged, path)
        except FileExistsError as exc:
            raise OSError(conflict) from exc
    else:
        # Writers outside our application don't honor the reservation. For
        # existing files the stat check detects prior changes, but cannot
        # lock out an external change during the following replace system call.
        os.replace(staged, path)
