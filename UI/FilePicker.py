"""Windows-native file and folder dialog helpers.

The application keeps one small wrapper so every import/export flow gets the
same parent ownership, initial-directory handling and localized fallbacks.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog
from typing import Iterable, Optional, Sequence, Tuple

from UI.I18n import tr


FileType = Tuple[str, Sequence[str]]


def _dialog_parent(parent: tk.Misc) -> tk.Misc:
    try:
        return parent.winfo_toplevel()
    except (AttributeError, tk.TclError):
        return parent


def _safe_initial_directory(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    candidate = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(candidate):
        return candidate
    parent = os.path.dirname(candidate)
    return parent if os.path.isdir(parent) else None


def _native_filetypes(filetypes: Optional[Iterable[FileType]]):
    rows = []
    for label, patterns in filetypes or [(tr("all_files"), ("*.*",))]:
        if isinstance(patterns, str):
            values = [patterns]
        else:
            values = list(patterns)
        values = ["*.*" if value == "*" else value for value in values]
        rows.append((label, " ".join(values) or "*.*"))
    return rows


def askopenfile(
    parent: tk.Misc,
    title: Optional[str] = None,
    filetypes: Optional[Iterable[FileType]] = None,
    initialdir: Optional[str] = None,
) -> Optional[str]:
    """Open the operating system's file picker and return an absolute path."""
    options = {
        "parent": _dialog_parent(parent),
        "title": title or tr("choose_file"),
        "filetypes": _native_filetypes(filetypes),
    }
    safe_initial = _safe_initial_directory(initialdir)
    if safe_initial:
        options["initialdir"] = safe_initial
    result = filedialog.askopenfilename(**options)
    return os.path.abspath(result) if result else None


def asksavefile(
    parent: tk.Misc,
    title: Optional[str] = None,
    filetypes: Optional[Iterable[FileType]] = None,
    initialdir: Optional[str] = None,
    initialfile: Optional[str] = None,
    defaultextension: Optional[str] = None,
) -> Optional[str]:
    """Open the operating system's save picker and return an absolute path."""
    options = {
        "parent": _dialog_parent(parent),
        "title": title or tr("choose_file"),
        "filetypes": _native_filetypes(filetypes),
    }
    safe_initial = _safe_initial_directory(initialdir)
    if safe_initial:
        options["initialdir"] = safe_initial
    if initialfile:
        options["initialfile"] = initialfile
    if defaultextension:
        options["defaultextension"] = defaultextension
    result = filedialog.asksaveasfilename(**options)
    return os.path.abspath(result) if result else None


def askdirectory(
    parent: tk.Misc,
    title: Optional[str] = None,
    initialdir: Optional[str] = None,
) -> Optional[str]:
    """Open the operating system's folder picker and return an absolute path."""
    options = {
        "parent": _dialog_parent(parent),
        "title": title or tr("choose_folder"),
        "mustexist": True,
    }
    safe_initial = _safe_initial_directory(initialdir)
    if safe_initial:
        options["initialdir"] = safe_initial
    result = filedialog.askdirectory(**options)
    return os.path.abspath(result) if result else None
