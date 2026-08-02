# -*- coding: utf-8 -*-
"""Threaded AssetBundle browser with isolated dual-process previews."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from io import BytesIO
import hashlib
import json
import multiprocessing
import os
import queue
import re
import threading
import tkinter as tk
import unicodedata
from tkinter import ttk
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from PIL import Image, ImageTk

from AssetbundleUtils import UnityPy_AOV
from AssetbundleUtils.AnimationPipeline import (
    AnimationProjectIndex, export_animation_fbx, replace_animation_from_fbx,
)
from AssetbundleUtils.EffectPipeline import EffectProjectIndex, export_effect_directory
from AssetbundleUtils.AssetBundleMetadata import (
    export_assetbundle_metadata, import_assetbundle_metadata,
)
from AssetbundleUtils.BundleProject import (
    export_bundle_project, rebuild_bundle_project,
    repair_sprite_atlas_preloads,
)
from AssetbundleUtils.MeshImport import replace_mesh_from_obj
from AssetbundleUtils.PreviewWorker import preview_worker
from AssetbundleUtils.SpriteImport import SpriteProjectIndex, replace_sprite_image
from AssetbundleUtils.TextureImport import (
    replace_texture_image, texture_preview_png, texture_runtime_metadata,
    validate_texture_roundtrip,
)
from UI.FilePicker import askdirectory, askopenfile, asksavefile
from UI.I18n import get_language, subscribe, tr, unsubscribe
from UI.ModernTheme import (
    COLORS, FONTS, RoundedButton, SegmentedControl, apply_all_styles, center_window,
    rounded_rectangle, set_rounded_window, show_dialog,
)


list_window = None


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned[:120] or "asset"


def _display_asset_text(value: str) -> str:
    """Make valid but non-renderable Unity names visible without changing data."""
    visible = []
    for character in str(value):
        codepoint = ord(character)
        category = unicodedata.category(character)
        if category in ("Cc", "Cf", "Co", "Cs"):
            escape = "u" if codepoint <= 0xFFFF else "U"
            width = 4 if escape == "u" else 8
            visible.append(f"\\{escape}{codepoint:0{width}X}")
        else:
            visible.append(character)
    return "".join(visible)


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _select_rebuild_packer(bundle, requested: str = "auto") -> str:
    """Choose a compatible outer bundle encoding without downgrading AOV data."""

    if requested != "auto":
        return requested
    storage = getattr(bundle, "special_storage_format", None)
    flags = int(getattr(bundle, "dataflags", 0))
    if (
        storage == "aov-sm4-blockinfo-at-end-lzma"
        or (storage and flags == 0x6C1)
    ):
        return "aov-fingerprint-1"
    if (
        storage == "aov-sm4-blockinfo-prefix-lzma"
        or (storage and flags == 0x641)
    ):
        return "aov-fingerprint-3"
    if (
        storage == "aov-sm4-blockinfo-prefix-lz4hc"
        or (storage and flags == 0x643)
    ):
        return "aov-fingerprint-2"
    if storage is None:
        return "original"
    # The other known encrypted layouts do not yet have a byte-compatible
    # writer. Keep their established ordinary-LZ4 rebuild behavior.
    return "lz4"


def _hex_dump_preview(data: bytes, limit: int = 131072) -> str:
    """Return a bounded readable fallback when a Unity typetree is unavailable."""

    visible = data[:limit]
    lines = []
    for offset in range(0, len(visible), 16):
        chunk = visible[offset:offset + 16]
        hexadecimal = " ".join(f"{value:02X}" for value in chunk)
        ascii_text = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        lines.append(f"{offset:08X}  {hexadecimal:<47}  |{ascii_text}|")
    if len(data) > len(visible):
        lines.append(f"\n... raw fallback limited to {len(visible):,} / {len(data):,} bytes ...")
    return "\n".join(lines)


_DUMP_METADATA_FIELDS = {
    "reader", "assets_file", "type", "path_id", "version", "build_type",
    "platform", "serialized_type", "byte_size", "container", "type_tree",
}


def _dump_scalar_type(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return type(value).__name__


def _iter_serialized_dump(name, value, indent=1, seen=None):
    """Yield AssetStudio-style lines from parsed Unity class fields."""

    seen = seen if seen is not None else set()
    prefix = "  " * indent
    if isinstance(value, Enum):
        raw_value = getattr(value, "value", value)
        yield f"{prefix}int {name} = {raw_value}  // {value}"
        return
    if isinstance(value, bool):
        yield f"{prefix}bool {name} = {'true' if value else 'false'}"
        return
    if isinstance(value, str):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        escaped = _display_asset_text(escaped)
        yield f'{prefix}string {name} = "{escaped}"'
        return
    if value is None or isinstance(value, (int, float)):
        yield f"{prefix}{_dump_scalar_type(value)} {name} = {value}"
        return
    if isinstance(value, (bytes, bytearray, memoryview)):
        yield f"{prefix}TypelessData {name} ({len(value):,} bytes)"
        return
    if isinstance(value, dict):
        yield f"{prefix}map {name}"
        yield f"{prefix}  int size = {len(value)}"
        for index, (key, item) in enumerate(value.items()):
            yield f"{prefix}  [{index}] key = {key!r}"
            yield from _iter_serialized_dump("value", item, indent + 2, seen)
        return
    if isinstance(value, (list, tuple)):
        yield f"{prefix}Array {name}"
        yield f"{prefix}  int size = {len(value)}"
        for index, item in enumerate(value):
            yield f"{prefix}  [{index}]"
            yield from _iter_serialized_dump("data", item, indent + 2, seen)
        return
    identity = id(value)
    if identity in seen:
        yield f"{prefix}{type(value).__name__} {name} = <circular reference>"
        return
    attributes = getattr(value, "__dict__", None)
    if isinstance(attributes, dict):
        seen.add(identity)
        yield f"{prefix}{type(value).__name__} {name}"
        for field, item in attributes.items():
            if field in _DUMP_METADATA_FIELDS:
                continue
            display_name = "m_ImageData" if field == "_image_data" else field
            if field.startswith("_") and field != "_image_data":
                continue
            yield from _iter_serialized_dump(display_name, item, indent + 1, seen)
        seen.remove(identity)
        return
    yield f"{prefix}{type(value).__name__} {name} = {value!r}"


def _dump_parsed_object(target, max_chars: int):
    parsed = target.read(False)
    lines = [f"{target.type.name} Base"]
    length = len(lines[0]) + 1
    truncated = False
    for field, value in parsed.__dict__.items():
        if field in _DUMP_METADATA_FIELDS:
            continue
        display_name = "m_ImageData" if field == "_image_data" else field
        if field.startswith("_") and field != "_image_data":
            continue
        for line in _iter_serialized_dump(display_name, value):
            line_length = len(line) + 1
            if length + line_length > max_chars:
                truncated = True
                break
            lines.append(line)
            length += line_length
        if truncated:
            break
    return "\n".join(lines), truncated


def build_asset_dump(
    bundle_path: str,
    path_id: int,
    replacement_raw: Optional[bytes] = None,
    max_chars: int = 2_000_000,
) -> dict:
    """Build an isolated typetree dump without sharing UI stream cursors."""

    environment = UnityPy_AOV.load(bundle_path)
    target = next(
        (obj for obj in environment.objects if int(obj.path_id) == int(path_id)),
        None,
    )
    if target is None:
        raise KeyError(f"PathID {path_id} was not found in {os.path.basename(bundle_path)}")
    if replacement_raw is not None:
        target.set_raw_data(replacement_raw)
    fallback = False
    pre_truncated = False
    parser_error = None
    try:
        text = target.dump_typetree()
    except Exception as exc:
        fallback = True
        parser_error = str(exc)
        try:
            text, pre_truncated = _dump_parsed_object(target, max_chars)
        except Exception as parsed_exc:
            try:
                structure = target.dump_typetree_structure()
            except Exception as structure_exc:
                structure = f"TypeTree structure unavailable: {structure_exc}"
            raw = bytes(target.get_raw_data())
            text = (
                f"{target.type.name} Base\n"
                f"  // Parsed value dump unavailable: {parsed_exc}\n"
                f"  // Showing TypeTree structure and bounded raw bytes.\n\n"
                f"{structure}\n\nRaw data ({len(raw):,} bytes)\n"
                f"{_hex_dump_preview(raw)}"
            )
    text = str(text or "")
    original_chars = len(text)
    truncated = pre_truncated or original_chars > max_chars
    if truncated:
        text = text[:max_chars]
    return {
        "text": text,
        "truncated": truncated,
        "original_chars": original_chars,
        "fallback": fallback,
        "parser_error": parser_error,
    }


class AssetRow(NamedTuple):
    """Lightweight indexed metadata used by the virtual asset table."""

    file_index: int
    basename: str
    name: str
    asset_type: str
    path_id: int
    byte_size: int
    obj: object
    search_text: str

    @property
    def key(self) -> Tuple[int, int]:
        return self.file_index, self.path_id


class AssetBrowser:
    PREVIEW_PROCESS_COUNT = 2

    def __init__(self, input_path: str, is_directory: bool, parent: Optional[tk.Misc]):
        global list_window
        self.input_path = os.path.abspath(input_path)
        self.is_directory = is_directory
        self.parent = parent
        self.lang_code = get_language()
        self.language_listener = subscribe(self.apply_language)
        self.events: queue.Queue = queue.Queue()
        self.closed = False
        self.loading = True
        self.loaded_assets = 0
        self.load_errors: List[str] = []
        self.paths = self._discover_paths()
        self.env_list = [None] * len(self.paths)
        self.all_rows: List[AssetRow] = []
        self.view_rows: List[AssetRow] = []
        self.row_lookup: Dict[Tuple[int, int], AssetRow] = {}
        self.row_objects: Dict[str, Tuple[int, object]] = {}
        self.visible_keys: Dict[str, Tuple[int, int]] = {}
        self.selected_keys: Set[Tuple[int, int]] = set()
        self.active_key: Optional[Tuple[int, int]] = None
        self.virtual_start = 0
        self.virtual_capacity = 30
        self.virtual_render_job = None
        self.virtual_updating = False
        self.search_after_job = None
        self.selection_after_job = None
        self.view_generation = 0
        self.view_busy = False
        self.active_query = ""
        self.type_filter: Optional[str] = None
        self.type_counts: Dict[str, int] = {}
        self.modified: Dict[Tuple[int, int], str] = {}
        self.sprite_preview_overrides: Dict[Tuple[int, int], bytes] = {}
        self.texture_preview_overrides: Dict[Tuple[int, int], bytes] = {}
        self.preview_generation = 0
        self.preview_photo = None
        self.preview_after_job = None
        self.preview_state_key = "preview_select_hint"
        self.preview_mode = "preview"
        self.dump_generation = 0
        self.dump_state_key = "dump_select_hint"
        self.dump_state_values = {}
        self.current_sort = "name"
        self.sort_reverse = False
        self.status_key = "waiting_load"
        self.status_values = {}
        self.search_placeholder_active = True
        self.executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="AOVGui")
        self.preview_requests = None
        self.preview_results = None
        self.preview_processes = []
        self.preview_latest_generation = None
        self.sprite_project: Optional[SpriteProjectIndex] = None
        self.animation_project: Optional[AnimationProjectIndex] = None
        self.animation_model = None
        self.animation_model_manual = False
        self.animation_index_loading = False
        self.effect_project: Optional[EffectProjectIndex] = None
        self.effect_index_loading = False
        self.effect_root = None
        self.effect_seek_updating = False
        self.drawer_open = False
        self.drawer_width = 396
        self.drawer_x = -400
        self.drawer_animation_job = None
        self.event_drain_job = None
        self.load_thread = None
        self.drawer_toggle_hovered = False
        self.drawer_toggle_focused = False

        self.window = tk.Toplevel(parent) if parent else tk.Toplevel()
        list_window = self.window
        self.include_model_var = tk.BooleanVar(master=self.window, value=True)
        self.include_attachments_var = tk.BooleanVar(master=self.window, value=True)
        self.preview_attachments_var = tk.BooleanVar(master=self.window, value=True)
        self.effect_loop_var = tk.BooleanVar(master=self.window, value=True)
        self.effect_timeline_var = tk.DoubleVar(master=self.window, value=0.0)
        self.window.configure(bg=COLORS["bg_light"])
        self.window.minsize(1080, 680)
        center_window(self.window, 1380, 840)
        set_rounded_window(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        try:
            icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
            self.window.iconbitmap(icon_path)
        except Exception:
            pass
        apply_all_styles()
        self._build_ui()
        self.apply_language(self.lang_code)
        self._start_preview_processes()
        self.event_drain_job = self.window.after(30, self._drain_events)
        self._start_loading()

    def _discover_paths(self) -> List[str]:
        if not self.is_directory:
            return [self.input_path]
        accepted = (".assetbundle", ".bundle", ".ab")
        paths = [
            entry.path for entry in os.scandir(self.input_path)
            if entry.is_file() and (
                entry.name.lower().endswith(accepted)
                or "assetbundle" in entry.name.lower()
            ) and not entry.name.lower().startswith("_animation_import_roundtrip")
        ]
        return sorted(paths, key=lambda value: os.path.basename(value).lower())

    def _build_ui(self):
        header = tk.Frame(self.window, bg=COLORS["surface"], padx=26, pady=17)
        header.pack(fill="x")
        self.drawer_toggle = tk.Canvas(
            header, width=44, height=44, bg=COLORS["surface"],
            highlightthickness=0, bd=0, cursor="hand2", takefocus=True,
        )
        self.drawer_toggle.pack(side="left", padx=(0, 13))
        self.drawer_toggle.bind("<ButtonRelease-1>", self._toggle_action_drawer)
        self.drawer_toggle.bind("<Return>", self._toggle_action_drawer)
        self.drawer_toggle.bind("<space>", self._toggle_action_drawer)
        self.drawer_toggle.bind(
            "<Enter>", lambda _event: self._set_drawer_toggle_hover(True)
        )
        self.drawer_toggle.bind(
            "<Leave>", lambda _event: self._set_drawer_toggle_hover(False)
        )
        self.drawer_toggle.bind(
            "<FocusIn>", lambda _event: self._set_drawer_toggle_focus(True)
        )
        self.drawer_toggle.bind(
            "<FocusOut>", lambda _event: self._set_drawer_toggle_focus(False)
        )
        self._draw_drawer_toggle()
        self.save_button = RoundedButton(header, "", self.save_bundles, 174, 42, "primary")
        self.save_button.pack(side="right")
        self.save_button.set_enabled(False)
        self.fingerprint_save_button = RoundedButton(
            header, "", self.save_fingerprint_bundles, 154, 42, "secondary"
        )
        self.fingerprint_save_button.pack(side="right", padx=(0, 10))
        self.fingerprint_save_button.set_enabled(False)
        title_box = tk.Frame(header, bg=COLORS["surface"])
        title_box.pack(side="left", fill="x", expand=True)
        self.workspace_title_var = tk.StringVar(master=self.window)
        tk.Label(
            title_box, textvariable=self.workspace_title_var,
            bg=COLORS["surface"], fg=COLORS["text_primary"],
            font=FONTS["title"], anchor="w"
        ).pack(fill="x")
        self.subtitle_var = tk.StringVar(master=self.window, value=self.input_path)
        tk.Label(
            title_box, textvariable=self.subtitle_var, bg=COLORS["surface"],
            fg=COLORS["text_muted"], font=FONTS["tiny"], anchor="w"
        ).pack(fill="x", pady=(3, 0))

        toolbar = tk.Frame(self.window, bg=COLORS["surface_alt"], padx=24, pady=11)
        toolbar.pack(fill="x")
        self.search_var = tk.StringVar(master=self.window)
        self.search_entry = tk.Entry(
            toolbar, textvariable=self.search_var, bg=COLORS["surface"],
            fg=COLORS["text_primary"], insertbackground=COLORS["primary"],
            relief="flat", font=FONTS["body"]
        )
        self.search_entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.search_entry.bind("<FocusIn>", self._search_focus_in)
        self.search_entry.bind("<FocusOut>", self._search_focus_out)
        self.search_entry.bind("<KeyRelease>", self._filter_rows)
        self.asset_count_var = tk.StringVar(master=self.window)
        tk.Label(
            toolbar, textvariable=self.asset_count_var, bg=COLORS["surface_alt"],
            fg=COLORS["text_secondary"], font=FONTS["small"]
        ).pack(side="right", padx=(18, 0))

        body = tk.Frame(self.window, bg=COLORS["bg_light"], padx=18, pady=16)
        body.pack(fill="both", expand=True)
        self.body = body
        # Keep the browser useful without starving the live preview.  ``uniform``
        # is important here: the Treeview's requested width must not be allowed
        # to squeeze the OpenGL column back down after a large bundle is loaded.
        body.grid_columnconfigure(0, weight=13, uniform="workspace")
        body.grid_columnconfigure(1, weight=12, uniform="workspace")
        body.grid_rowconfigure(0, weight=1)

        list_card = tk.Frame(body, bg=COLORS["surface"])
        list_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.columns = ("file", "name", "type", "path_id", "size", "modified")
        self.tree = ttk.Treeview(
            list_card, columns=self.columns, show="headings", selectmode="extended",
            style="Aurora.Treeview"
        )
        widths = {"file": 150, "name": 260, "type": 110, "path_id": 160, "size": 82, "modified": 74}
        for column in self.columns:
            self.tree.heading(column, command=lambda value=column: self._sort(value))
            self.tree.column(column, width=widths[column], minwidth=55, anchor="w")
        self.tree.column("modified", anchor="center")
        self.scrollbar = ttk.Scrollbar(
            list_card, orient="vertical", command=self._virtual_scroll,
            style="Aurora.Vertical.TScrollbar"
        )
        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.scrollbar.set(0.0, 1.0)
        self.tree.tag_configure("even", background=COLORS["row_even"])
        self.tree.tag_configure("odd", background=COLORS["row_odd"])
        self.type_menu = tk.Menu(
            self.window, tearoff=False, bg=COLORS["surface"],
            fg=COLORS["text_primary"], activebackground=COLORS["primary_light"],
            activeforeground=COLORS["text_primary"], font=FONTS["small"],
            relief="flat", bd=0,
        )
        self.tree.bind("<<TreeviewSelect>>", self._on_selection)
        self.tree.bind("<ButtonPress-1>", self._on_tree_button_press, add="+")
        self.tree.bind("<Configure>", self._on_tree_configure, add="+")
        self.tree.bind("<MouseWheel>", self._virtual_mousewheel)
        self.tree.bind("<Button-4>", lambda _event: self._scroll_virtual(-3))
        self.tree.bind("<Button-5>", lambda _event: self._scroll_virtual(3))
        self.tree.bind("<Prior>", lambda _event: self._virtual_page(-1))
        self.tree.bind("<Next>", lambda _event: self._virtual_page(1))
        self.tree.bind("<Home>", lambda _event: self._virtual_home_end(False))
        self.tree.bind("<End>", lambda _event: self._virtual_home_end(True))
        self.tree.bind("<Control-a>", self._select_all)

        side = tk.Frame(body, bg=COLORS["viewer_bg"])
        side.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.preview_side = side
        side.grid_rowconfigure(0, weight=1)
        side.grid_columnconfigure(0, weight=1)

        self.preview_mode_bar = tk.Frame(
            side, bg=COLORS["viewer_bg"], padx=7, pady=6,
            highlightthickness=1, highlightbackground="#344158",
        )
        self.preview_mode_control = SegmentedControl(
            self.preview_mode_bar,
            [("preview", "Preview"), ("dump", "Dump")],
            self.preview_mode, self._switch_preview_mode,
            width=184, height=32, bg=COLORS["viewer_bg"], variant="dark",
        )
        self.preview_mode_control.pack()
        self.preview_card = tk.Frame(side, bg=COLORS["viewer_bg"])
        self.preview_card.grid(row=0, column=0, sticky="nsew")
        self.dump_card = tk.Frame(side, bg="#151C28")
        self.dump_card.grid(row=0, column=0, sticky="nsew")
        self.dump_card.grid_remove()
        self.preview_mode_bar.place(relx=1.0, x=-12, y=12, anchor="ne")
        self.preview_mode_bar.lift()

        self.dump_card.grid_rowconfigure(0, weight=1)
        self.dump_card.grid_columnconfigure(0, weight=1)
        self.dump_text = tk.Text(
            self.dump_card, wrap="none", state="disabled", undo=False,
            bg="#151C28", fg="#CED7E5", insertbackground="#7CD7D4",
            selectbackground="#334A5E", selectforeground="#F5F8FC",
            relief="flat", bd=0, highlightthickness=0,
            font=FONTS["mono"], padx=18, pady=58, spacing1=1, spacing3=1,
        )
        self.dump_v_scroll = ttk.Scrollbar(
            self.dump_card, orient="vertical", command=self.dump_text.yview,
            style="Preview.Vertical.TScrollbar",
        )
        self.dump_h_scroll = ttk.Scrollbar(
            self.dump_card, orient="horizontal", command=self.dump_text.xview,
            style="Preview.Horizontal.TScrollbar",
        )
        self.dump_text.configure(
            yscrollcommand=self.dump_v_scroll.set,
            xscrollcommand=self.dump_h_scroll.set,
        )
        self.dump_text.grid(row=0, column=0, sticky="nsew")
        self.dump_v_scroll.grid(row=0, column=1, sticky="ns")
        self.dump_h_scroll.grid(row=1, column=0, sticky="ew")
        self._set_dump_text(tr("dump_select_hint"), "dump_select_hint")
        self.preview_label = tk.Label(
            self.preview_card, bg=COLORS["viewer_bg"], fg="#B8C2D4",
            font=FONTS["body"], justify="center"
        )
        self.preview_label.place(relx=0.5, rely=0.54, anchor="center")
        self.obj_viewer = None

        detail = tk.Frame(
            self.preview_card, bg="#273247", padx=16, pady=11,
            highlightthickness=1, highlightbackground="#36435A",
        )
        detail.place(x=14, y=14, relwidth=1.0, width=-28)
        self.preview_detail = detail
        self.selection_title = tk.StringVar(master=self.window)
        self.selection_meta = tk.StringVar(master=self.window)
        tk.Label(
            detail, textvariable=self.selection_title, bg="#273247",
            fg=COLORS["text_white"], font=FONTS["heading"], anchor="w",
            wraplength=390, justify="left"
        ).pack(fill="x")
        tk.Label(
            detail, textvariable=self.selection_meta, bg="#273247",
            fg="#AEB9CC", font=FONTS["tiny"], anchor="w",
            wraplength=390, justify="left"
        ).pack(fill="x", pady=(5, 0))
        self.preview_model_text_var = tk.StringVar(master=self.window)
        self.preview_model_check = tk.Checkbutton(
            detail, variable=self.include_model_var,
            textvariable=self.preview_model_text_var,
            command=self._toggle_preview_model_export_filter,
            anchor="w", justify="left",
            bg="#273247", activebackground="#273247",
            fg="#DDE4F0", activeforeground=COLORS["text_white"],
            selectcolor=COLORS["primary"], font=FONTS["tiny"],
            relief="flat", bd=0, highlightthickness=0,
        )
        self.preview_attachment_text_var = tk.StringVar(master=self.window)
        self.preview_attachments_check = tk.Checkbutton(
            detail, variable=self.preview_attachments_var,
            textvariable=self.preview_attachment_text_var,
            command=self._toggle_preview_attachments,
            anchor="w", justify="left",
            bg="#273247", activebackground="#273247",
            fg="#DDE4F0", activeforeground=COLORS["text_white"],
            selectcolor=COLORS["primary"], font=FONTS["tiny"],
            relief="flat", bd=0, highlightthickness=0,
        )
        self._build_effect_controls()
        self._build_action_drawer(body)

        status = tk.Frame(self.window, bg=COLORS["surface"], padx=24, pady=10)
        status.pack(fill="x", side="bottom")
        self.status_var = tk.StringVar(master=self.window)
        tk.Label(
            status, textvariable=self.status_var, bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["small"], anchor="w"
        ).pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(
            status, mode="indeterminate", length=190,
            style="Aurora.Horizontal.TProgressbar"
        )
        self.progress.pack(side="right")
        self.progress.start(12)

    def _build_effect_controls(self):
        """Playback controls are overlaid so the 3D canvas keeps the full column."""
        self.effect_controls = tk.Frame(
            self.preview_card, bg="#273247", padx=12, pady=8,
            highlightthickness=1, highlightbackground="#36435A",
        )
        self.effect_play_button = RoundedButton(
            self.effect_controls, "", self._toggle_effect_playback,
            76, 32, "secondary", bg="#273247",
        )
        self.effect_play_button.pack(side="left", padx=(0, 10))
        self.effect_time_var = tk.StringVar(master=self.window, value="0:00 / 0:00")
        tk.Label(
            self.effect_controls, textvariable=self.effect_time_var,
            bg="#273247", fg="#DDE4F0", font=FONTS["tiny"], width=12,
        ).pack(side="right", padx=(10, 0))
        self.effect_loop_check = tk.Checkbutton(
            self.effect_controls, variable=self.effect_loop_var, text="",
            command=self._set_effect_loop, bg="#273247", activebackground="#273247",
            fg="#DDE4F0", activeforeground=COLORS["text_white"],
            selectcolor=COLORS["primary"], font=FONTS["tiny"],
            relief="flat", bd=0, highlightthickness=0,
        )
        self.effect_loop_check.pack(side="right", padx=(8, 0))
        self.effect_timeline = ttk.Scale(
            self.effect_controls, from_=0.0, to=1.0,
            variable=self.effect_timeline_var, command=self._seek_effect,
            style="Aurora.Horizontal.TScale",
        )
        self.effect_timeline.pack(side="left", fill="x", expand=True)

    @staticmethod
    def _format_effect_time(value):
        value = max(0.0, float(value))
        return f"{int(value // 60)}:{value % 60:04.1f}"

    def _toggle_effect_playback(self):
        viewer = self.obj_viewer
        if self.preview_state_key != "effect" or viewer is None:
            return
        if viewer.effect_playing:
            viewer.pause()
        else:
            viewer.play()
        self._refresh_effect_play_button()

    def _refresh_effect_play_button(self):
        playing = bool(
            self.preview_state_key == "effect" and self.obj_viewer is not None
            and getattr(self.obj_viewer, "effect_playing", False)
        )
        self.effect_play_button.set_text(tr("effect_pause" if playing else "effect_play"))

    def _set_effect_loop(self):
        if self.preview_state_key == "effect" and self.obj_viewer is not None:
            self.obj_viewer.set_loop(self.effect_loop_var.get())

    def _seek_effect(self, value):
        if self.effect_seek_updating or self.preview_state_key != "effect":
            return
        if self.obj_viewer is not None:
            self.obj_viewer.seek_fraction(float(value))

    def _effect_time_changed(self, current, duration, playing):
        if self.closed:
            return
        self.effect_seek_updating = True
        try:
            fraction = 0.0 if duration <= 0 else current / duration
            self.effect_timeline_var.set(max(0.0, min(1.0, fraction)))
            self.effect_time_var.set(
                f"{self._format_effect_time(current)} / {self._format_effect_time(duration)}"
            )
            self._refresh_effect_play_button()
        finally:
            self.effect_seek_updating = False

    def _build_action_drawer(self, body):
        """Build an overlay drawer so low-frequency actions consume no preview space."""
        self.drawer_canvas = tk.Canvas(
            body, width=self.drawer_width, height=520,
            bg=COLORS["bg_light"], highlightthickness=0, bd=0,
        )
        self.drawer_x = -self.drawer_width - 8
        self.drawer_canvas.place(
            x=self.drawer_x, y=4, width=self.drawer_width, height=520
        )

        self.drawer_view = tk.Canvas(
            self.drawer_canvas, bg=COLORS["surface"],
            highlightthickness=0, bd=0,
        )
        self.drawer_view_id = self.drawer_canvas.create_window(
            20, 12, anchor="nw", width=self.drawer_width - 54, height=488,
            window=self.drawer_view,
        )
        self.drawer_scrollbar = ttk.Scrollbar(
            self.drawer_canvas, orient="vertical",
            command=self.drawer_view.yview,
            style="Aurora.Vertical.TScrollbar",
        )
        self.drawer_scrollbar_id = self.drawer_canvas.create_window(
            self.drawer_width - 23, 30, anchor="n",
            width=12, height=452, window=self.drawer_scrollbar,
        )
        self.drawer_view.configure(yscrollcommand=self.drawer_scrollbar.set)
        self.drawer_content = tk.Frame(
            self.drawer_view, bg=COLORS["surface"], padx=6, pady=4
        )
        self.drawer_content_id = self.drawer_view.create_window(
            0, 0, anchor="nw", width=self.drawer_width - 56,
            window=self.drawer_content,
        )

        self.drawer_title_var = tk.StringVar(master=self.window)
        self.drawer_hint_var = tk.StringVar(master=self.window)
        self.drawer_assets_var = tk.StringVar(master=self.window)
        self.drawer_animation_var = tk.StringVar(master=self.window)
        self.drawer_effect_var = tk.StringVar(master=self.window)
        tk.Label(
            self.drawer_content, textvariable=self.drawer_title_var,
            bg=COLORS["surface"], fg=COLORS["text_primary"],
            font=FONTS["title"], anchor="w",
        ).pack(fill="x")
        tk.Label(
            self.drawer_content, textvariable=self.drawer_hint_var,
            bg=COLORS["surface"], fg=COLORS["text_muted"],
            font=FONTS["tiny"], anchor="w", justify="left",
            wraplength=self.drawer_width - 62,
        ).pack(fill="x", pady=(3, 8))

        tk.Label(
            self.drawer_content, textvariable=self.drawer_assets_var,
            bg=COLORS["surface"], fg=COLORS["text_secondary"],
            font=FONTS["small"], anchor="w",
        ).pack(fill="x", pady=(0, 3))
        asset_actions = tk.Frame(self.drawer_content, bg=COLORS["surface"])
        asset_actions.pack(fill="x")
        asset_actions.grid_columnconfigure(0, weight=1, uniform="drawer_asset")
        asset_actions.grid_columnconfigure(1, weight=1, uniform="drawer_asset")

        self.buttons = {}
        asset_specs = [
            ("export_raw", self.export_raw), ("import_raw", self.import_raw),
            ("export_png", self.export_texture), ("import_png", self.import_texture),
            ("import_sprite", self.import_sprite),
            ("export_mesh", self.export_mesh), ("import_mesh", self.import_mesh),
            ("export_bundle_metadata", self.export_bundle_metadata),
            ("import_bundle_metadata", self.import_bundle_metadata),
            ("export_bundle_project", self.export_bundle_projects),
            ("rebuild_bundle_project", self.rebuild_from_bundle_project),
        ]
        for index, (key, command) in enumerate(asset_specs):
            button = RoundedButton(
                asset_actions, "", command, 142, 35, "secondary",
                bg=COLORS["surface"],
            )
            button.grid(
                row=index // 2, column=index % 2,
                padx=(0, 5) if index % 2 == 0 else (5, 0),
                pady=2, sticky="ew",
            )
            self.buttons[key] = button
            button.set_enabled(False)

        tk.Frame(
            self.drawer_content, height=1, bg=COLORS["border_light"]
        ).pack(fill="x", pady=(8, 7))
        tk.Label(
            self.drawer_content, textvariable=self.drawer_effect_var,
            bg=COLORS["surface"], fg=COLORS["text_secondary"],
            font=FONTS["small"], anchor="w",
        ).pack(fill="x")
        effect_button = RoundedButton(
            self.drawer_content, "", self.export_effect,
            298, 35, "secondary", bg=COLORS["surface"],
        )
        effect_button.pack(fill="x", pady=(3, 0))
        self.buttons["export_effect_package"] = effect_button
        effect_button.set_enabled(False)

        tk.Frame(
            self.drawer_content, height=1, bg=COLORS["border_light"]
        ).pack(fill="x", pady=(8, 7))
        tk.Label(
            self.drawer_content, textvariable=self.drawer_animation_var,
            bg=COLORS["surface"], fg=COLORS["text_secondary"],
            font=FONTS["small"], anchor="w",
        ).pack(fill="x")

        animation_panel = tk.Frame(self.drawer_content, bg=COLORS["surface"])
        animation_panel.pack(fill="x", pady=(3, 0))
        animation_panel.grid_columnconfigure(0, weight=1, uniform="drawer_animation")
        animation_panel.grid_columnconfigure(1, weight=1, uniform="drawer_animation")
        self.animation_model_var = tk.StringVar(master=self.window)
        tk.Label(
            animation_panel, textvariable=self.animation_model_var,
            bg=COLORS["surface"], fg=COLORS["text_secondary"],
            font=FONTS["tiny"], anchor="w", justify="left",
            wraplength=self.drawer_width - 64,
        ).grid(row=0, column=0, columnspan=2, pady=(0, 3), sticky="ew")
        self.include_model_check = tk.Checkbutton(
            animation_panel, variable=self.include_model_var, text="", anchor="w",
            command=self._toggle_preview_model_export_filter,
            bg=COLORS["surface"], activebackground=COLORS["surface"],
            fg=COLORS["text_primary"], activeforeground=COLORS["text_primary"],
            selectcolor=COLORS["primary_light"], font=FONTS["tiny"],
            relief="flat", bd=0, highlightthickness=0,
        )
        self.include_model_check.grid(
            row=1, column=0, columnspan=2, pady=(0, 2), sticky="ew"
        )
        self.include_attachments_check = tk.Checkbutton(
            animation_panel, variable=self.include_attachments_var, text="", anchor="w",
            bg=COLORS["surface"], activebackground=COLORS["surface"],
            fg=COLORS["text_primary"], activeforeground=COLORS["text_primary"],
            selectcolor=COLORS["primary_light"], font=FONTS["tiny"],
            relief="flat", bd=0, highlightthickness=0,
        )
        self.include_attachments_check.grid(
            row=2, column=0, columnspan=2, pady=(0, 2), sticky="ew"
        )
        self.attachment_summary_var = tk.StringVar(master=self.window)
        self.attachment_summary_label = tk.Label(
            animation_panel, textvariable=self.attachment_summary_var,
            bg=COLORS["surface"], fg=COLORS["text_muted"],
            activebackground=COLORS["surface"], activeforeground=COLORS["primary"],
            font=FONTS["tiny"], anchor="w", justify="left",
            wraplength=self.drawer_width - 64,
            cursor="hand2",
        )
        self.attachment_summary_label.grid(
            row=3, column=0, columnspan=2, pady=(0, 2), sticky="ew"
        )
        self.attachment_summary_label.bind(
            "<ButtonRelease-1>", self.show_animation_attachment_details
        )
        animation_specs = [
            ("select_animation_model", self.select_animation_model, 0, 2),
            ("export_animation_fbx", self.export_animation, 0, 1),
            ("import_animation_fbx", self.import_animation, 1, 1),
        ]
        for key, command, column, span in animation_specs:
            row = 4 if key == "select_animation_model" else 5
            button = RoundedButton(
                animation_panel, "", command,
                298 if span == 2 else 142, 35, "secondary",
                bg=COLORS["surface"],
            )
            button.grid(
                row=row, column=column, columnspan=span,
                padx=(0, 5) if span == 1 and column == 0 else (
                    (5, 0) if span == 1 else (0, 0)
                ),
                pady=2, sticky="ew",
            )
            self.buttons[key] = button
            button.set_enabled(False)

        body.bind("<Configure>", self._resize_action_drawer, add="+")
        self.drawer_content.bind(
            "<Configure>", self._refresh_drawer_scrollregion, add="+"
        )
        for widget in (self.drawer_view, self.drawer_content):
            widget.bind("<MouseWheel>", self._scroll_action_drawer, add="+")
            widget.bind("<Button-4>", lambda _event: self._scroll_action_drawer_steps(-3), add="+")
            widget.bind("<Button-5>", lambda _event: self._scroll_action_drawer_steps(3), add="+")
        for widget in self.drawer_content.winfo_children():
            widget.bind("<MouseWheel>", self._scroll_action_drawer, add="+")
        body.after_idle(self._resize_action_drawer)
        self.drawer_canvas.tk.call("raise", self.drawer_canvas._w)

    def _refresh_drawer_scrollregion(self, _event=None):
        if not hasattr(self, "drawer_view") or self.closed:
            return
        self.drawer_view.configure(scrollregion=self.drawer_view.bbox("all"))
        required = self.drawer_content.winfo_reqheight()
        visible = self.drawer_view.winfo_height()
        self.drawer_canvas.itemconfigure(
            self.drawer_scrollbar_id,
            state="normal" if required > visible else "hidden",
        )

    def _scroll_action_drawer(self, event):
        if not self.drawer_open:
            return
        steps = -int(event.delta / 120) if event.delta else 0
        if steps:
            self.drawer_view.yview_scroll(steps * 3, "units")
        return "break"

    def _scroll_action_drawer_steps(self, steps):
        if self.drawer_open:
            self.drawer_view.yview_scroll(int(steps), "units")
        return "break"

    def _resize_action_drawer(self, event=None):
        if not hasattr(self, "drawer_canvas") or self.closed:
            return
        body_height = int(getattr(event, "height", self.body.winfo_height()))
        height = max(440, body_height - 8)
        self.drawer_canvas.place_configure(height=height)
        self.drawer_canvas.configure(height=height)
        view_height = max(360, height - 28)
        self.drawer_canvas.itemconfigure(
            self.drawer_view_id,
            width=self.drawer_width - 54,
            height=view_height,
        )
        self.drawer_view.itemconfigure(
            self.drawer_content_id, width=self.drawer_width - 56
        )
        self.drawer_canvas.coords(
            self.drawer_scrollbar_id, self.drawer_width - 23, 30
        )
        self.drawer_canvas.itemconfigure(
            self.drawer_scrollbar_id, height=max(250, height - 66)
        )
        self.drawer_canvas.delete("drawer-surface")
        rounded_rectangle(
            self.drawer_canvas, 8, 10, self.drawer_width - 3, height - 2, 22,
            fill="#DCE6EE", outline="#DCE6EE", tags="drawer-surface",
        )
        rounded_rectangle(
            self.drawer_canvas, 5, 5, self.drawer_width - 8, height - 8, 20,
            fill=COLORS["surface"], outline=COLORS["border"], width=1,
            tags="drawer-surface",
        )
        self.drawer_canvas.tag_lower("drawer-surface")
        self._refresh_drawer_scrollregion()

    def _set_drawer_toggle_hover(self, hovered):
        self.drawer_toggle_hovered = bool(hovered)
        self._draw_drawer_toggle()

    def _set_drawer_toggle_focus(self, focused):
        self.drawer_toggle_focused = bool(focused)
        self._draw_drawer_toggle()

    def _draw_drawer_toggle(self):
        if not hasattr(self, "drawer_toggle"):
            return
        canvas = self.drawer_toggle
        canvas.delete("all")
        active = self.drawer_open
        fill = COLORS["primary_light"] if active else (
            COLORS["surface_alt"] if self.drawer_toggle_hovered else COLORS["surface"]
        )
        outline = COLORS["primary"] if self.drawer_toggle_focused else COLORS["border"]
        rounded_rectangle(
            canvas, 1, 1, 43, 43, 13, fill=fill, outline=outline, width=1
        )
        stroke = COLORS["primary"] if active else COLORS["text_muted"]
        rounded_rectangle(
            canvas, 10, 11, 31, 32, 6, fill="", outline=stroke, width=2
        )
        canvas.create_line(17, 12, 17, 31, fill=stroke, width=2)
        dot_fill = COLORS["primary"] if active else "#4B9CFF"
        canvas.create_oval(29, 7, 37, 15, fill=dot_fill, outline=dot_fill)

    def _toggle_action_drawer(self, _event=None):
        if not hasattr(self, "drawer_canvas") or self.closed:
            return "break"
        if self.drawer_animation_job is not None:
            try:
                self.window.after_cancel(self.drawer_animation_job)
            except tk.TclError:
                pass
            self.drawer_animation_job = None
        self.drawer_open = not self.drawer_open
        self._draw_drawer_toggle()
        self.drawer_canvas.tk.call("raise", self.drawer_canvas._w)
        target = 7 if self.drawer_open else -self.drawer_width - 8
        self._animate_action_drawer(self.drawer_x, target, 0, 14)
        return "break"

    def _animate_action_drawer(self, start, target, step, total_steps):
        if self.closed:
            return
        amount = min(1.0, step / max(1, total_steps))
        eased = 1.0 - (1.0 - amount) ** 3
        self.drawer_x = int(round(start + (target - start) * eased))
        self.drawer_canvas.place_configure(x=self.drawer_x)
        if step >= total_steps:
            self.drawer_animation_job = None
            self.drawer_x = int(target)
            self.drawer_canvas.place_configure(x=self.drawer_x)
            return
        self.drawer_animation_job = self.window.after(
            16,
            lambda: self._animate_action_drawer(
                start, target, step + 1, total_steps
            ),
        )

    def apply_language(self, code: str):
        self.lang_code = code
        self.preview_mode_control.set_options([
            ("preview", tr("preview_tab")), ("dump", tr("dump_tab")),
        ])
        self.preview_mode_control.set_selected(self.preview_mode)
        self.window.title(f"{tr('asset_workspace')} · Unity 2022")
        self.workspace_title_var.set(tr("asset_workspace"))
        self.drawer_title_var.set(tr("action_drawer"))
        self.drawer_hint_var.set(tr("action_drawer_hint"))
        self.drawer_assets_var.set(tr("drawer_asset_actions"))
        self.drawer_animation_var.set(tr("drawer_animation_actions"))
        self.drawer_effect_var.set(tr("drawer_effect_actions"))
        self.save_button.set_text(tr("save_rebuild"))
        self.fingerprint_save_button.set_text(tr("fingerprint_rebuild"))
        headings = {
            "file": "column_file", "name": "column_name", "type": "column_type",
            "path_id": "column_pathid", "size": "column_size", "modified": "column_status",
        }
        for column, key in headings.items():
            text = tr(key)
            if column == "type":
                text = f"{self.type_filter or text}   ▾"
            self.tree.heading(column, text=text)
        button_keys = {
            "export_raw": "export_raw", "import_raw": "import_raw",
            "export_png": "export_png", "import_png": "import_png",
            "import_sprite": "import_sprite",
            "export_mesh": "export_mesh", "import_mesh": "import_mesh",
            "export_bundle_metadata": "export_bundle_metadata",
            "import_bundle_metadata": "import_bundle_metadata",
            "export_bundle_project": "export_bundle_project",
            "rebuild_bundle_project": "rebuild_bundle_project",
            "select_animation_model": "select_animation_model",
            "export_animation_fbx": "export_animation_fbx",
            "import_animation_fbx": "import_animation_fbx",
            "export_effect_package": "export_effect_package",
        }
        for button, key in button_keys.items():
            self.buttons[button].set_text(tr(key))
        self.include_model_check.configure(text=tr("include_model_in_fbx"))
        self.include_attachments_check.configure(text=tr("include_attachments_in_fbx"))
        self.effect_loop_check.configure(text=tr("effect_loop"))
        self._refresh_effect_play_button()
        self._refresh_animation_model_label()
        if self.search_placeholder_active:
            self.search_var.set(tr("search_placeholder"))
        self._update_asset_count()
        self._refresh_status()
        if not self._selected_records():
            self.selection_title.set(tr("select_asset"))
            self.selection_meta.set(tr("mesh_button_hint"))
        if self.preview_state_key in (
            "preview_select_hint", "preview_generating", "preview_not_available",
            "animation_preview_select_model", "animation_preview_no_match",
        ):
            self._reset_preview(tr(self.preview_state_key), self.preview_state_key)
        if self.dump_state_key in (
            "dump_select_hint", "dump_generating", "dump_failed",
        ):
            self._set_dump_text(
                tr(self.dump_state_key, **self.dump_state_values),
                self.dump_state_key,
                **self.dump_state_values,
            )
        self._schedule_virtual_render()

    def _set_status(self, key: str, **values):
        self.status_key = key
        self.status_values = values
        self._refresh_status()

    def _refresh_status(self):
        if hasattr(self, "status_var"):
            values = dict(self.status_values)
            operation_key = values.pop("operation_key", None)
            if operation_key:
                values["label"] = tr(operation_key)
            self.status_var.set(tr(self.status_key, **values))

    def _switch_preview_mode(self, mode):
        if mode not in ("preview", "dump"):
            return
        self.preview_mode = mode
        self.preview_mode_control.set_selected(mode)
        if mode == "dump":
            # Keep the OpenGL host mapped.  Removing its parent from the Tk
            # geometry manager invalidates the child HWND/context on Windows;
            # when it is mapped again the viewer can remain permanently black.
            # Dump is therefore an overlay, not a replacement page.
            self.dump_card.grid()
            self.dump_card.lift()
            self.dump_text.focus_set()
        else:
            self.dump_card.grid_remove()
            self.preview_card.lift()
            self._restore_preview_surface()
        self.preview_mode_bar.lift()

    def _restore_preview_surface(self):
        """Expose and repaint the current preview without recreating GL state."""
        if self.closed:
            return
        viewer = self.obj_viewer
        if viewer is not None and self.preview_state_key in (
            "mesh", "animation", "effect",
        ):
            try:
                viewer.lift()
                viewer.update_idletasks()
                viewer.event_generate("<Configure>")
                viewer.after_idle(viewer.redraw)
            except (tk.TclError, RuntimeError):
                # The selected asset may have changed while Dump was visible.
                # Its replacement preview will repaint when the worker returns.
                pass
        else:
            try:
                self.preview_label.lift()
            except tk.TclError:
                pass
        self.preview_detail.lift()
        if self.preview_state_key == "effect":
            self.effect_controls.lift()
        self.preview_mode_bar.lift()

    def _search_focus_in(self, _event=None):
        if self.search_placeholder_active:
            self.search_var.set("")
            self.search_placeholder_active = False

    def _search_focus_out(self, _event=None):
        if not self.search_var.get().strip():
            self.search_placeholder_active = True
            self.search_var.set(tr("search_placeholder"))

    def _start_preview_processes(self):
        try:
            context = multiprocessing.get_context("spawn")
            self.preview_requests = []
            self.preview_results = context.Queue(maxsize=8)
            self.preview_latest_generation = context.Value("q", 0)
            for worker_id in range(1, self.PREVIEW_PROCESS_COUNT + 1):
                request_queue = context.Queue(maxsize=4)
                self.preview_requests.append(request_queue)
                process = context.Process(
                    target=preview_worker,
                    args=(
                        request_queue, self.preview_results, worker_id,
                        self.preview_latest_generation,
                    ),
                    name=f"AOVPreview-{worker_id}",
                    daemon=True,
                )
                process.start()
                self.preview_processes.append(process)
        except Exception as exc:
            self.preview_processes = []
            self._reset_preview(tr("preview_failed", error=exc), "preview_not_available")

    def _start_loading(self):
        if not self.paths:
            self.loading = False
            self.progress.stop()
            self.progress.pack_forget()
            self._set_status("no_bundle")
            self.window.after(
                50, lambda: show_dialog(
                    self.window, tr("no_loadable_title"), tr("no_loadable_body"), "warning"
                )
            )
            return
        self._set_status("loading_bundles", count=len(self.paths))
        self.load_thread = threading.Thread(
            target=self._load_coordinator, name="AOVBundleLoader", daemon=True
        )
        self.load_thread.start()

    def _load_one(self, file_index: int, path: str):
        env = UnityPy_AOV.load(path)
        self.events.put(("environment", file_index, env))
        batch = []
        basename = os.path.basename(path)
        for obj in env.objects:
            try:
                raw_name = obj.peek_name(f"{obj.type.name}_{obj.path_id}")
            except Exception:
                raw_name = f"{obj.type.name}_{obj.path_id}"
            name = _display_asset_text(raw_name)
            asset_type = obj.type.name
            path_id = int(obj.path_id)
            batch.append(AssetRow(
                file_index, basename, name, asset_type, path_id, int(obj.byte_size), obj,
                f"{basename}\n{name}\n{raw_name}\n{asset_type}\n{path_id}".casefold(),
            ))
            if len(batch) >= 1000:
                self.events.put(("asset_batch", batch))
                batch = []
        if batch:
            self.events.put(("asset_batch", batch))
        return len(env.objects)

    def _load_coordinator(self):
        worker_count = min(4, max(1, os.cpu_count() or 1), len(self.paths))
        total = 0
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="AOVBundle") as pool:
            future_map = {
                pool.submit(self._load_one, index, path): (index, path)
                for index, path in enumerate(self.paths)
            }
            for future in as_completed(future_map):
                index, path = future_map[future]
                try:
                    count = future.result()
                    total += count
                    self.events.put(("file_loaded", index, path, count))
                except Exception as exc:
                    self.events.put(("load_error", index, path, str(exc)))
        self.events.put(("load_done", total))

    def _drain_events(self):
        self.event_drain_job = None
        if self.closed:
            return
        for _ in range(60):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "environment":
                _, index, env = event
                self.env_list[index] = env
            elif kind == "asset_batch":
                self._insert_batch(event[1])
            elif kind == "file_loaded":
                _, _index, path, count = event
                self._set_status("parsed_file", name=os.path.basename(path), count=count)
            elif kind == "load_error":
                _, _index, path, error = event
                self.load_errors.append(f"{os.path.basename(path)}: {error}")
            elif kind == "load_done":
                self._finish_loading(event[1])
            elif kind == "view_ready":
                self._apply_view(*event[1:])
            elif kind == "view_error":
                _, generation, error = event
                if generation == self.view_generation:
                    self.view_busy = False
                    self.load_errors.append(f"asset index: {error}")
            elif kind == "operation_done":
                self._operation_done(*event[1:])
            elif kind == "operation_error":
                self._operation_error(*event[1:])
            elif kind == "save_done":
                self._save_done(*event[1:])
            elif kind == "animation_index_ready":
                self.animation_index_loading = False
                self.animation_project = event[1]
                self._set_status(
                    "animation_index_ready",
                    models=len(self.animation_project.models),
                    animations=len(self.animation_project.animations),
                )
                self._update_button_states()
                row = self.row_lookup.get(self.active_key) if self.active_key else None
                if row is not None and row.asset_type == "AnimationClip":
                    self._request_preview(row.file_index, row.obj)
                self._start_effect_index()
            elif kind == "animation_index_error":
                self.animation_index_loading = False
                self.load_errors.append(f"animation index: {event[1]}")
                self._set_status("operation_failed", operation_key="animation_indexing")
                self._start_effect_index()
            elif kind == "effect_index_ready":
                self.effect_index_loading = False
                self.effect_project = event[1]
                self._apply_effect_root_names()
                self._set_status("effect_index_ready", effects=len(self.effect_project.roots))
                self._update_button_states()
                row = self.row_lookup.get(self.active_key) if self.active_key else None
                if row is not None and self._resolve_effect_for_asset(
                    row.file_index, row.path_id, row.asset_type
                ):
                    self._request_preview(row.file_index, row.obj)
            elif kind == "effect_index_error":
                self.effect_index_loading = False
                self.load_errors.append(f"effect index: {event[1]}")
            elif kind == "dump_ready":
                self._apply_dump(*event[1:])
            elif kind == "dump_error":
                _, generation, error = event
                if generation == self.dump_generation:
                    self._set_dump_text(
                        tr("dump_failed", error=error), "dump_failed", error=error
                    )
            elif kind == "animation_model_match":
                _, generation, file_index, path_id, model, error = event
                if generation != self.preview_generation or self.closed:
                    continue
                row = self.row_lookup.get(self.active_key) if self.active_key else None
                if (
                    row is None or row.file_index != file_index
                    or row.path_id != path_id or row.asset_type != "AnimationClip"
                ):
                    continue
                if error or model is None:
                    self._reset_preview(
                        tr("animation_preview_no_match"),
                        "animation_preview_no_match",
                    )
                    self._update_button_states()
                    continue
                self.animation_model = model
                self.animation_model_manual = False
                self._refresh_animation_model_label()
                self._update_button_states()
                self.preview_after_job = self.window.after(
                    1, lambda g=generation, i=file_index, o=row.obj:
                    self._send_preview_request(g, i, o)
                )
        if self.preview_results is not None:
            for _ in range(8):
                try:
                    result = self.preview_results.get_nowait()
                except queue.Empty:
                    break
                self._apply_preview(*result)
        self.event_drain_job = self.window.after(25, self._drain_events)

    def _insert_batch(self, rows):
        self.all_rows.extend(rows)
        for row in rows:
            self.row_lookup[row.key] = row
            self.type_counts[row.asset_type] = self.type_counts.get(row.asset_type, 0) + 1
        if not self.active_query and self.type_filter is None and not self.view_busy:
            self.view_rows.extend(rows)
        self.loaded_assets += len(rows)
        self._update_asset_count()
        self._schedule_virtual_render()

    def _finish_loading(self, total):
        self.loading = False
        self.sprite_project = SpriteProjectIndex(self.env_list)
        self.progress.stop()
        self.progress.pack_forget()
        self.save_button.set_enabled(any(self.env_list))
        self.fingerprint_save_button.set_enabled(any(self.env_list))
        if self.load_errors:
            self._set_status("load_partial", total=total, errors=len(self.load_errors))
            show_dialog(
                self.window, tr("load_partial_title"), "\n".join(self.load_errors[:6]), "warning"
            )
        else:
            self._set_status("load_complete", bundles=len(self.paths), total=total)
        self._queue_view_rebuild(clear_selection=False)
        self._update_button_states()
        if not self._start_animation_index():
            self._start_effect_index()

    def _start_animation_index(self):
        environments = list(self.env_list)
        if not environments or any(env is None for env in environments):
            return False
        # The relationship index is project-wide, not animation-only. Build it
        # for every loaded folder so Material/Texture/Shader/MonoBehaviour and
        # arbitrary custom asset references are available even when no skinned
        # character is present. The work stays on the executor and never blocks
        # virtual-list scrolling.
        self.animation_index_loading = True
        self._set_status("animation_indexing")

        def worker():
            return AnimationProjectIndex(self.paths, environments)

        future = self.executor.submit(worker)
        future.add_done_callback(
            lambda done: self.events.put(
                ("animation_index_error", str(done.exception()))
                if done.exception() is not None
                else ("animation_index_ready", done.result())
            )
        )
        return True

    def _start_effect_index(self):
        if self.effect_project is not None or self.effect_index_loading:
            return False
        environments = list(self.env_list)
        if not environments or any(env is None for env in environments):
            return False
        potential = any(self.type_counts.get(name, 0) for name in (
            "ParticleSystem", "TrailRenderer", "LineRenderer",
            "MeshRenderer", "SkinnedMeshRenderer",
        ))
        if not potential:
            return False
        self.effect_index_loading = True
        self._set_status("effect_indexing")

        # Reuse the completed project graph. This gives animation, effects,
        # materials and scripts one authoritative set of forward/reverse edges.
        project = self.animation_project
        future = self.executor.submit(
            lambda: EffectProjectIndex(self.paths, project=project)
        )
        future.add_done_callback(
            lambda done: self.events.put(
                ("effect_index_error", str(done.exception()))
                if done.exception() is not None
                else ("effect_index_ready", done.result())
            )
        )
        return True

    def _resolve_effect_for_asset(self, file_index, path_id, asset_type=None):
        # GameObject is the only effect preview/export entry. AnimationClip is
        # deliberately kept on the texture-free character/skeleton preview
        # path even when a particle prefab or controller also references it.
        # This prevents a shared character clip from unexpectedly turning into
        # a textured effect preview merely because its owning prefab has VFX.
        if self.effect_project is None or str(asset_type) != "GameObject":
            self.effect_root = None
            return None
        matches = self.effect_project.resolve_effects(file_index, int(path_id))
        matches = [
            root for root in matches
            if root.file_index == int(file_index)
            and root.game_object_id == int(path_id)
        ]
        self.effect_root = matches[0] if matches else None
        return self.effect_root

    def _apply_effect_root_names(self):
        """Expose prefab-style root names without eagerly reading every GameObject."""
        if self.effect_project is None:
            return
        replacements = {}
        for root in self.effect_project.roots:
            key = (root.file_index, root.game_object_id)
            row = self.row_lookup.get(key)
            if row is None or row.name == root.name:
                continue
            replacements[key] = row._replace(
                name=root.name,
                search_text=(
                    f"{row.basename}\n{root.name}\n{row.asset_type}\n{row.path_id}"
                ).casefold(),
            )
        if not replacements:
            return
        self.all_rows = [replacements.get(row.key, row) for row in self.all_rows]
        self.view_rows = [replacements.get(row.key, row) for row in self.view_rows]
        self.row_lookup.update(replacements)
        self._queue_view_rebuild(clear_selection=False)

    def _refresh_animation_model_label(self):
        if not hasattr(self, "animation_model_var"):
            return
        if self.animation_model is None:
            self.animation_model_var.set(tr("animation_model_none"))
        else:
            self.animation_model_var.set(tr(
                "animation_model_selected",
                name=self.animation_model.name,
                nodes=len(self.animation_model.transform_ids),
                meshes=len(self.animation_model.skinned_renderer_ids),
            ))
        self._refresh_attachment_summary()
        self._refresh_preview_attachment_control()

    def _refresh_preview_attachment_control(self):
        if not hasattr(self, "preview_attachments_check"):
            return
        row = self.row_lookup.get(self.active_key) if self.active_key else None
        clip_visible = bool(
            row is not None and row.asset_type == "AnimationClip"
            and self.effect_root is None
            and self.animation_model is not None
        )
        self.preview_model_text_var.set(tr("preview_model_export_toggle"))
        if clip_visible:
            if not self.preview_model_check.winfo_manager():
                self.preview_model_check.pack(fill="x", pady=(7, 0))
        else:
            self.preview_model_check.pack_forget()
        attachments = (
            self.animation_model.rigid_attachments
            if self.animation_model is not None else []
        )
        visible = bool(clip_visible and attachments)
        if not visible:
            self.preview_attachments_check.pack_forget()
            return
        names = ", ".join(item.name for item in attachments[:3])
        if len(attachments) > 3:
            names += f" +{len(attachments) - 3}"
        self.preview_attachment_text_var.set(tr(
            "preview_attachments_toggle", count=len(attachments), names=names
        ))
        if not self.preview_attachments_check.winfo_manager():
            self.preview_attachments_check.pack(fill="x", pady=(7, 0))

    def _toggle_preview_model_export_filter(self):
        """Regenerate the preview while keeping the drawer export state synced."""
        self._refresh_preview_attachment_control()
        self._toggle_preview_attachments()

    def _toggle_preview_attachments(self):
        row = self.row_lookup.get(self.active_key) if self.active_key else None
        if (
            row is None or row.asset_type != "AnimationClip"
            or self.animation_model is None or self.closed
        ):
            return
        self.preview_generation += 1
        generation = self.preview_generation
        if self.preview_after_job is not None:
            try:
                self.window.after_cancel(self.preview_after_job)
            except tk.TclError:
                pass
        self._reset_preview(tr("preview_generating"), "preview_generating")
        self._refresh_preview_attachment_control()
        self.preview_after_job = self.window.after(
            1, lambda: self._send_preview_request(
                generation, row.file_index, row.obj
            )
        )

    def _refresh_attachment_summary(self):
        if not hasattr(self, "attachment_summary_var"):
            return
        attachments = (
            self.animation_model.rigid_attachments
            if self.animation_model is not None else []
        )
        if hasattr(self, "include_attachments_check"):
            self.include_attachments_check.configure(
                state="normal" if attachments else "disabled"
            )
        if not attachments:
            self.attachment_summary_var.set(tr("animation_attachments_none"))
            return
        lines = []
        for attachment in attachments[:2]:
            materials = ", ".join(attachment.material_names) or "—"
            lines.append(tr(
                "animation_attachment_item",
                name=attachment.name,
                mount=attachment.mount_name,
                mesh=attachment.mesh_name or "—",
                material=materials,
                frames=attachment.sequence_frame_count,
            ))
        if len(attachments) > 2:
            lines.append(tr(
                "animation_attachments_more", count=len(attachments) - 2
            ))
        self.attachment_summary_var.set("\n".join(lines))

    def show_animation_attachment_details(self, _event=None):
        if self.animation_model is None or not self.animation_model.rigid_attachments:
            return "break"
        lines = []
        for attachment in self.animation_model.rigid_attachments:
            lines.append(tr(
                "animation_attachment_item",
                name=attachment.name,
                mount=attachment.mount_name,
                mesh=attachment.mesh_name or "—",
                material=", ".join(attachment.material_names) or "—",
                frames=attachment.sequence_frame_count,
            ))
        show_dialog(
            self.window, tr("animation_attachment_details_title"),
            "\n\n".join(lines),
        )
        return "break"

    def _update_asset_count(self):
        if self.active_query or self.type_filter is not None:
            self.asset_count_var.set(tr(
                "asset_count_filtered", shown=len(self.view_rows), total=self.loaded_assets
            ))
        elif self.loaded_assets:
            self.asset_count_var.set(tr("asset_count", count=self.loaded_assets))
        else:
            self.asset_count_var.set(tr("preparing_parse"))

    @staticmethod
    def _build_view(rows, query, type_filter, column, reverse, modified_keys):
        if type_filter is not None:
            rows = [row for row in rows if row.asset_type == type_filter]
        if query:
            rows = [row for row in rows if query in row.search_text]

        def sort_key(row):
            if column == "file":
                primary = row.basename.casefold()
            elif column == "type":
                primary = row.asset_type.casefold()
            elif column == "path_id":
                primary = row.path_id
            elif column == "size":
                primary = row.byte_size
            elif column == "modified":
                primary = row.key in modified_keys
            else:
                primary = row.name.casefold()
            return primary, row.path_id

        rows.sort(key=sort_key, reverse=reverse)
        return rows

    def _queue_view_rebuild(self, clear_selection=False):
        if self.closed:
            return
        self.search_after_job = None
        query = (
            "" if self.search_placeholder_active
            else self.search_var.get().strip().casefold()
        )
        self.view_generation += 1
        generation = self.view_generation
        self.view_busy = True
        snapshot = list(self.all_rows)
        future = self.executor.submit(
            self._build_view, snapshot, query, self.type_filter, self.current_sort,
            self.sort_reverse, frozenset(self.modified),
        )

        def completed(done):
            try:
                result = done.result()
            except Exception as exc:
                self.events.put(("view_error", generation, str(exc)))
            else:
                self.events.put((
                    "view_ready", generation, result, query, clear_selection,
                ))

        future.add_done_callback(completed)

    def _apply_view(self, generation, rows, query, clear_selection):
        if generation != self.view_generation or self.closed:
            return
        self.view_busy = False
        self.view_rows = rows
        self.active_query = query
        self.virtual_start = 0
        if clear_selection:
            self.selected_keys.clear()
            self.active_key = None
            self._reset_preview(tr("preview_select_hint"), "preview_select_hint")
            self._update_button_states()
        self._update_asset_count()
        self._schedule_virtual_render()

    def _on_tree_configure(self, event):
        capacity = max(8, min(120, (max(1, event.height) - 30) // 27 + 2))
        if capacity != self.virtual_capacity:
            self.virtual_capacity = capacity
            self._schedule_virtual_render()

    def _schedule_virtual_render(self):
        if self.closed or self.virtual_render_job is not None:
            return
        self.virtual_render_job = self.window.after_idle(self._render_virtual_rows)

    def _render_virtual_rows(self):
        self.virtual_render_job = None
        if self.closed:
            return
        total = len(self.view_rows)
        capacity = max(1, self.virtual_capacity)
        max_start = max(0, total - capacity)
        self.virtual_start = max(0, min(self.virtual_start, max_start))
        visible_rows = self.view_rows[
            self.virtual_start:self.virtual_start + capacity
        ]
        self.virtual_updating = True
        token = getattr(self, "virtual_update_generation", 0) + 1
        self.virtual_update_generation = token
        children = self.tree.get_children("")
        if children:
            self.tree.delete(*children)
        self.row_objects.clear()
        self.visible_keys.clear()
        selected_iids = []
        active_iid = None
        for offset, row in enumerate(visible_rows):
            index = self.virtual_start + offset
            iid = f"row:{row.file_index}:{row.path_id}"
            self.tree.insert(
                "", "end", iid=iid,
                values=(
                    row.basename, row.name, row.asset_type, str(row.path_id),
                    _format_bytes(row.byte_size),
                    tr("modified") if row.key in self.modified else "",
                ),
                tags=("even" if index % 2 == 0 else "odd",),
            )
            self.row_objects[iid] = (row.file_index, row.obj)
            self.visible_keys[iid] = row.key
            if row.key in self.selected_keys:
                selected_iids.append(iid)
            if row.key == self.active_key:
                active_iid = iid
        if selected_iids:
            self.tree.selection_set(selected_iids)
        if active_iid:
            self.tree.focus(active_iid)
        if total:
            self.scrollbar.set(
                self.virtual_start / total,
                min(1.0, (self.virtual_start + capacity) / total),
            )
        else:
            self.scrollbar.set(0.0, 1.0)
        self.window.after_idle(lambda value=token: self._end_virtual_update(value))

    def _end_virtual_update(self, token):
        if token == getattr(self, "virtual_update_generation", None):
            self.virtual_updating = False

    def _set_virtual_start(self, start):
        maximum = max(0, len(self.view_rows) - self.virtual_capacity)
        start = max(0, min(int(start), maximum))
        if start != self.virtual_start:
            self.virtual_start = start
            self._schedule_virtual_render()

    def _virtual_scroll(self, action, value, units=None):
        if action == "moveto":
            self._set_virtual_start(float(value) * len(self.view_rows))
        elif action == "scroll":
            amount = int(value)
            step = self.virtual_capacity if units == "pages" else 3
            self._set_virtual_start(self.virtual_start + amount * step)

    def _scroll_virtual(self, amount):
        self._set_virtual_start(self.virtual_start + amount)
        return "break"

    def _virtual_mousewheel(self, event):
        delta = -int(event.delta / 120) if event.delta else 0
        if not delta and event.delta:
            delta = -1 if event.delta > 0 else 1
        return self._scroll_virtual(delta * 3)

    def _virtual_page(self, direction):
        return self._scroll_virtual(direction * max(1, self.virtual_capacity - 2))

    def _virtual_home_end(self, go_to_end):
        self._set_virtual_start(
            len(self.view_rows) - self.virtual_capacity if go_to_end else 0
        )
        return "break"

    def _selected_records(self):
        rows = [
            self.row_lookup[key] for key in self.selected_keys
            if key != self.active_key and key in self.row_lookup
        ]
        if self.active_key in self.selected_keys and self.active_key in self.row_lookup:
            rows.append(self.row_lookup[self.active_key])
        return [(row.file_index, row.obj) for row in rows]

    def _on_tree_button_press(self, event):
        if (
            self.tree.identify_region(event.x, event.y) == "heading"
            and self.tree.identify_column(event.x) == "#3"
        ):
            right_edge = sum(
                int(self.tree.column(column, "width"))
                for column in self.columns[:3]
            )
            if event.x >= right_edge - 32:
                x = self.tree.winfo_rootx() + right_edge - 8
                y = self.tree.winfo_rooty() + 30
                self.window.after_idle(
                    lambda: self._show_type_filter_menu(x, y)
                )
                return "break"
        if self.tree.identify_region(event.x, event.y) == "cell" and not (event.state & 0x0005):
            self.selected_keys.clear()
            self.active_key = None

    def _update_type_heading(self):
        label = self.type_filter or tr("column_type")
        self.tree.heading("type", text=f"{label}   ▾")

    def _show_type_filter_menu(self, x, y):
        if self.closed or not self.window.winfo_exists():
            return
        self.type_menu.delete(0, "end")
        all_selected = self.type_filter is None
        self.type_menu.add_command(
            label=f"{'✓' if all_selected else '  '}  {tr('all_asset_types')}  ({self.loaded_assets:,})",
            command=lambda: self._set_type_filter(None),
        )
        self.type_menu.add_separator()
        for asset_type in sorted(self.type_counts, key=str.casefold):
            selected = asset_type == self.type_filter
            count = self.type_counts[asset_type]
            self.type_menu.add_command(
                label=f"{'✓' if selected else '  '}  {asset_type}  ({count:,})",
                command=lambda value=asset_type: self._set_type_filter(value),
            )
        try:
            self.type_menu.tk_popup(x, y)
        except tk.TclError:
            return
        finally:
            if not self.closed:
                try:
                    self.type_menu.grab_release()
                except tk.TclError:
                    pass

    def _set_type_filter(self, asset_type):
        # A type selection is a new navigation action.  Keeping a previous
        # Sprite name query would combine it with ``Texture2D`` and produce an
        # apparently broken empty list after Sprite replacement.  Cancel a
        # pending key debounce as well, otherwise it can reapply the stale
        # query after this view has already been rebuilt.
        search_cleared = bool(self.active_query)
        if self.search_after_job is not None:
            try:
                self.window.after_cancel(self.search_after_job)
            except tk.TclError:
                pass
            self.search_after_job = None
            search_cleared = True
        if not self.search_placeholder_active:
            search_cleared = (
                bool(self.search_var.get().strip()) or search_cleared
            )
        if search_cleared:
            self.search_placeholder_active = True
            self.search_var.set(tr("search_placeholder"))
            self.active_query = ""

        if asset_type == self.type_filter and not search_cleared:
            return
        self.type_filter = asset_type
        self._update_type_heading()
        self._queue_view_rebuild(clear_selection=True)

    def _on_selection(self, _event=None):
        if self.virtual_updating:
            if self.selection_after_job is None:
                def retry_selection():
                    self.selection_after_job = None
                    self._on_selection()
                self.selection_after_job = self.window.after_idle(retry_selection)
            return
        visible = set(self.visible_keys.values())
        selected_iids = self.tree.selection()
        selected_visible = {
            self.visible_keys[iid] for iid in selected_iids if iid in self.visible_keys
        }
        self.selected_keys.difference_update(visible)
        self.selected_keys.update(selected_visible)
        focus = self.tree.focus()
        if focus in self.visible_keys and self.visible_keys[focus] in selected_visible:
            self.active_key = self.visible_keys[focus]
        elif selected_iids:
            self.active_key = self.visible_keys.get(selected_iids[-1])
        elif not self.selected_keys:
            self.active_key = None

        row = self.row_lookup.get(self.active_key) if self.active_key else None
        if row is None:
            self.effect_root = None
            self.selection_title.set(tr("select_asset"))
            self.selection_meta.set(tr("mesh_button_hint"))
            self._reset_preview(tr("preview_select_hint"), "preview_select_hint")
            self.dump_generation += 1
            self._set_dump_text(tr("dump_select_hint"), "dump_select_hint")
            self._refresh_preview_attachment_control()
            self._update_button_states()
            return
        self.selection_title.set(row.name)
        self.selection_meta.set(
            f"{row.asset_type}  ·  PathID {row.path_id}  ·  "
            f"{_format_bytes(row.byte_size)}  ·  {row.basename}"
        )
        self._resolve_effect_for_asset(row.file_index, row.path_id, row.asset_type)
        self._update_button_states()
        self._request_preview(row.file_index, row.obj)

    def _update_button_states(self):
        records = self._selected_records()
        ready = bool(records) and not self.loading
        one = len(records) == 1
        asset_type = records[0][1].type.name.lower() if one else ""
        contains_shader = any(obj.type.name == "Shader" for _, obj in records)
        self.buttons["export_raw"].set_enabled(ready and not contains_shader)
        self.buttons["import_raw"].set_enabled(
            ready and one and asset_type != "shader"
        )
        self.buttons["export_png"].set_enabled(
            ready and all(obj.type.name.lower() in ("texture2d", "sprite") for _, obj in records)
        )
        self.buttons["import_png"].set_enabled(ready and one and asset_type == "texture2d")
        self.buttons["import_sprite"].set_enabled(
            ready and one and asset_type == "sprite"
            and self.sprite_project is not None
        )
        self.buttons["export_mesh"].set_enabled(
            ready and all(obj.type.name == "Mesh" for _, obj in records)
        )
        self.buttons["import_mesh"].set_enabled(ready and one and asset_type == "mesh")
        self.buttons["export_bundle_metadata"].set_enabled(
            ready and one and asset_type == "assetbundle"
        )
        self.buttons["import_bundle_metadata"].set_enabled(
            ready and one and asset_type == "assetbundle"
        )
        project_ready = not self.loading and any(self.env_list)
        self.buttons["export_bundle_project"].set_enabled(project_ready)
        self.buttons["rebuild_bundle_project"].set_enabled(not self.loading)
        animation_ready = self.animation_project is not None
        self.buttons["select_animation_model"].set_enabled(
            ready and one and asset_type == "gameobject" and animation_ready
        )
        clip_ready = (
            ready and one and asset_type == "animationclip"
            and animation_ready and self.animation_model is not None
        )
        self.buttons["export_animation_fbx"].set_enabled(clip_ready)
        self.buttons["import_animation_fbx"].set_enabled(clip_ready)
        self.buttons["export_effect_package"].set_enabled(
            ready and one and asset_type == "gameobject"
            and self.effect_project is not None
            and self.effect_root is not None
        )

    def _request_preview(self, file_index, obj):
        self.preview_generation += 1
        generation = self.preview_generation
        if self.preview_after_job is not None:
            self.window.after_cancel(self.preview_after_job)
        self._reset_preview(tr("preview_generating"), "preview_generating")
        self._request_dump(file_index, obj)
        self._resolve_effect_for_asset(
            file_index, int(obj.path_id), obj.type.name
        )
        if obj.type.name != "AnimationClip" or self.effect_root is not None:
            self.preview_model_check.pack_forget()
            self.preview_attachments_check.pack_forget()
        if (
            obj.type.name == "AnimationClip"
            and self.effect_root is None
            and self.animation_project is not None
            and not self.animation_model_manual
        ):
            self.animation_model = None
            self._refresh_animation_model_label()

            def match_model():
                try:
                    model = self.animation_project.best_model_for_animation(
                        file_index, int(obj.path_id)
                    )
                    self.events.put((
                        "animation_model_match", generation, file_index,
                        int(obj.path_id), model, None,
                    ))
                except Exception as exc:
                    self.events.put((
                        "animation_model_match", generation, file_index,
                        int(obj.path_id), None, str(exc),
                    ))

            self.executor.submit(match_model)
            return
        self.preview_after_job = self.window.after(
            90, lambda: self._send_preview_request(generation, file_index, obj)
        )

    def _set_dump_text(self, text, state_key, **state_values):
        self.dump_state_key = state_key
        self.dump_state_values = dict(state_values)
        if not hasattr(self, "dump_text"):
            return
        self.dump_text.configure(state="normal")
        self.dump_text.delete("1.0", "end")
        self.dump_text.insert("1.0", str(text))
        self.dump_text.mark_set("insert", "1.0")
        self.dump_text.see("1.0")
        self.dump_text.configure(state="disabled")

    def _request_dump(self, file_index, obj):
        """Generate the selected asset dump from an independent read stream."""

        self.dump_generation += 1
        generation = self.dump_generation
        self._set_dump_text(tr("dump_generating"), "dump_generating")
        replacement_raw = (
            bytes(obj.get_raw_data())
            if (file_index, int(obj.path_id)) in self.modified
            else None
        )
        future = self.executor.submit(
            build_asset_dump,
            self.paths[file_index], int(obj.path_id), replacement_raw,
        )

        def completed(done):
            try:
                result = done.result()
            except Exception as exc:
                self.events.put(("dump_error", generation, str(exc)))
            else:
                self.events.put(("dump_ready", generation, result))

        future.add_done_callback(completed)

    def _apply_dump(self, generation, payload):
        if generation != self.dump_generation or self.closed:
            return
        text = payload.get("text", "")
        if payload.get("truncated"):
            text += "\n\n" + tr("dump_truncated")
        self._set_dump_text(text, "dump_ready")

    def _send_preview_request(self, generation, file_index, obj):
        self.preview_after_job = None
        if generation != self.preview_generation or self.closed:
            return
        alive = [process for process in self.preview_processes if process.is_alive()]
        if (
            len(alive) != self.PREVIEW_PROCESS_COUNT
            or not self.preview_requests
        ):
            self._reset_preview(
                tr("preview_failed", error="preview worker process unavailable"),
                "preview_not_available",
            )
            return
        if self.preview_latest_generation is not None:
            self.preview_latest_generation.value = int(generation)
        replacement_raw = (
            obj.get_raw_data()
            if (file_index, int(obj.path_id)) in self.modified
            else None
        )
        object_key = (int(file_index), int(obj.path_id))
        image_override = (
            self.sprite_preview_overrides.get(object_key)
            if obj.type.name == "Sprite"
            else self.texture_preview_overrides.get(object_key)
            if obj.type.name == "Texture2D"
            else None
        )
        if image_override is not None:
            task = {
                "kind": "image_override",
                "generation": generation,
                "bundle_path": self.paths[file_index],
                "path_id": int(obj.path_id),
                "asset_type": obj.type.name,
                "payload": image_override,
            }
        elif obj.type.name in ("Material", "Shader"):
            task = {
                "kind": obj.type.name.lower(),
                "generation": generation,
                "paths": tuple(self.paths),
                "bundle_path": self.paths[file_index],
                "file_index": file_index,
                "path_id": int(obj.path_id),
                "asset_type": obj.type.name,
                "size": 720,
            }
        elif self.effect_root is not None:
            task = {
                "kind": "effect",
                "generation": generation,
                "paths": tuple(self.paths),
                "bundle_path": self.paths[file_index],
                "path_id": int(obj.path_id),
                "asset_type": obj.type.name,
                "root_id": self.effect_root.id,
                "animation_id": (
                    f"f{file_index}:p{int(obj.path_id)}"
                    if obj.type.name == "AnimationClip" else None
                ),
                "max_frames": 96,
                "frames_per_second": 24.0,
            }
        elif obj.type.name == "AnimationClip":
            if self.animation_project is None or self.animation_model is None:
                self._reset_preview(
                    tr("animation_preview_select_model"),
                    "animation_preview_select_model",
                )
                return
            task = {
                "kind": "animation",
                "generation": generation,
                "paths": tuple(self.paths),
                "bundle_path": self.paths[file_index],
                "animation_file_index": file_index,
                "path_id": int(obj.path_id),
                "asset_type": obj.type.name,
                "replacement_raw": replacement_raw,
                "model_file_index": self.animation_model.file_index,
                "model_game_object_id": self.animation_model.game_object_id,
                "include_model": bool(self.include_model_var.get()),
                "include_attachments": bool(self.preview_attachments_var.get()),
                "max_frames": 72,
            }
        else:
            task = (
                generation, self.paths[file_index], int(obj.path_id),
                obj.type.name, replacement_raw,
            )
        # Keep heavyweight caches warm on stable workers: skeletal animation
        # uses worker 1, effects use worker 2. Lightweight assets are balanced.
        kind = task.get("kind") if isinstance(task, dict) else str(obj.type.name).lower()
        if kind == "animation":
            worker_index = 0
        elif kind in ("effect", "material", "shader"):
            worker_index = min(1, len(self.preview_requests) - 1)
        else:
            worker_index = abs(int(file_index) ^ int(obj.path_id)) % len(
                self.preview_requests
            )
        request_queue = self.preview_requests[worker_index]
        while True:
            try:
                request_queue.get_nowait()
            except queue.Empty:
                break
        try:
            request_queue.put_nowait(task)
        except queue.Full:
            request_queue.put(task, timeout=0.2)

    def _reset_preview(self, text=None, state_key="preview_select_hint"):
        self.preview_state_key = state_key
        if self.obj_viewer is not None:
            self.obj_viewer.stop_animation()
            self.obj_viewer.place_forget()
        if hasattr(self, "effect_controls"):
            self.effect_controls.place_forget()
        self.preview_label.configure(image="", text=text or tr(state_key))
        self.preview_label.image = None
        self.preview_label.place(relx=0.5, rely=0.5, anchor="center")
        self.preview_mode_bar.lift()

    def _apply_preview(self, generation, kind, payload, error, worker_id):
        if generation != self.preview_generation or self.closed:
            return
        if error or kind == "none":
            if error and "missing_dependency:" in error:
                dependency = error.split("missing_dependency:", 1)[1]
                message = tr("preview_missing_dependency", dependency=dependency)
            else:
                message = tr(error) if error == "preview_not_available" else tr("preview_failed", error=error)
            self._reset_preview(message, "preview_not_available")
            return
        if kind in ("image", "material", "shader"):
            try:
                preview_bytes = payload if kind == "image" else payload["png"]
                with Image.open(BytesIO(preview_bytes)) as source:
                    image = source.copy()
                image.thumbnail(
                    (max(240, self.preview_card.winfo_width() - 36),
                     max(200, self.preview_card.winfo_height() - 36)),
                    Image.Resampling.LANCZOS,
                )
                self.preview_photo = ImageTk.PhotoImage(image)
                if self.obj_viewer is not None:
                    self.obj_viewer.place_forget()
                self.preview_label.configure(image=self.preview_photo, text="")
                self.preview_label.image = self.preview_photo
                self.preview_label.place(relx=0.5, rely=0.5, anchor="center")
                self.preview_detail.lift()
                self.preview_mode_bar.lift()
                self.preview_state_key = kind
                if kind in ("material", "shader"):
                    metadata = payload.get("metadata", {})
                    base = self.selection_meta.get().split("\n", 1)[0]
                    if kind == "material":
                        summary = tr(
                            "material_preview_summary",
                            family=metadata.get("family", "Material"),
                            textures=len(metadata.get("textures", [])),
                            metallic=float(metadata.get("metallic", 0.0)),
                            smoothness=float(metadata.get("smoothness", 0.0)),
                        )
                    else:
                        representative = metadata.get("representative_material")
                        summary = tr(
                            "shader_preview_summary",
                            family=metadata.get("family", "Shader"),
                            material=(
                                representative if representative
                                else tr("shader_default_material")
                            ),
                            properties=int(metadata.get("property_count", 0)),
                        )
                    self.selection_meta.set(f"{base}\n{summary}")
            except Exception as exc:
                self._reset_preview(tr("preview_failed", error=exc), "preview_not_available")
            return
        if kind in ("mesh", "animation", "effect"):
            try:
                if kind == "effect" and (
                    self.obj_viewer is None
                    or self.obj_viewer.__class__.__name__ != "EffectViewer"
                ):
                    if self.obj_viewer is not None:
                        self.obj_viewer.destroy()
                    from AssetbundleUtils.EffectViewer import EffectViewer
                    self.obj_viewer = EffectViewer(
                        self.preview_card, self._effect_time_changed
                    )
                elif kind != "effect" and (
                    self.obj_viewer is None
                    or self.obj_viewer.__class__.__name__ == "EffectViewer"
                ):
                    if self.obj_viewer is not None:
                        self.obj_viewer.destroy()
                    from AssetbundleUtils.OBJ_Viewer import OBJViewer
                    self.obj_viewer = OBJViewer(self.preview_card)
                self.preview_label.place_forget()
                self.obj_viewer.place(relx=0, rely=0, relwidth=1, relheight=1)
                if kind == "effect":
                    self.obj_viewer.load_effect_payload(payload)
                    self.obj_viewer.set_loop(self.effect_loop_var.get())
                    self.effect_controls.place(
                        x=14, rely=1.0, y=-14, anchor="sw",
                        relwidth=1.0, width=-28,
                    )
                    self.effect_controls.lift()
                    self._refresh_effect_play_button()
                elif kind == "animation":
                    self.obj_viewer.load_animation_buffers(payload)
                else:
                    self.obj_viewer.load_mesh_buffers(payload)
                self.preview_detail.lift()
                self.preview_mode_bar.lift()
                self.preview_state_key = kind
                if kind == "effect":
                    self.effect_controls.lift()
                    self._refresh_effect_play_button()
                self._refresh_preview_attachment_control()
            except Exception as exc:
                self._reset_preview(tr("viewer_unavailable", error=exc), "preview_not_available")

    def _select_all(self, _event=None):
        self.selected_keys = {row.key for row in self.view_rows}
        if self.view_rows and self.active_key not in self.selected_keys:
            self.active_key = self.view_rows[min(self.virtual_start, len(self.view_rows) - 1)].key
        self._schedule_virtual_render()
        self._update_button_states()
        return "break"

    def _filter_rows(self, _event=None):
        if self.search_after_job is not None:
            self.window.after_cancel(self.search_after_job)
        self.search_after_job = self.window.after(
            160, lambda: self._queue_view_rebuild(clear_selection=True)
        )

    def _sort(self, column):
        self.sort_reverse = not self.sort_reverse if self.current_sort == column else False
        self.current_sort = column
        self._queue_view_rebuild(clear_selection=False)

    def _mark_modified(self, file_index: int, obj, label: str):
        self.modified[(file_index, int(obj.path_id))] = label
        self._schedule_virtual_render()

    def _refresh_relationships_after_import(self, file_index: int, obj):
        """Refresh the shared graph after a serialized payload changes."""
        project = self.animation_project
        if project is None and self.effect_project is not None:
            project = self.effect_project.project
        if project is None:
            return
        project.refresh_serialized_objects([
            (int(file_index), int(obj.path_id))
        ])
        if (
            self.effect_project is not None
            and self.effect_project.project is project
        ):
            self.effect_project.rebuild()

    def _submit_operation(self, operation_key, worker, on_success=None):
        self._set_status(operation_key)
        self.progress.pack(side="right")
        self.progress.start(12)
        future = self.executor.submit(worker)

        def complete(done):
            try:
                self.events.put(("operation_done", operation_key, done.result(), on_success))
            except Exception as exc:
                self.events.put(("operation_error", operation_key, str(exc)))

        future.add_done_callback(complete)

    def _operation_done(self, operation_key, value, on_success):
        self.progress.stop()
        self.progress.pack_forget()
        if on_success:
            on_success(value)
        self._set_status("operation_done", operation_key=operation_key)

    def _operation_error(self, operation_key, error):
        self.progress.stop()
        self.progress.pack_forget()
        message = tr("operation_failed", label=tr(operation_key))
        self._set_status("operation_failed", operation_key=operation_key)
        show_dialog(self.window, message, error, "error")

    def export_raw(self):
        records = self._selected_records()
        output = askdirectory(
            self.window, tr("pick_raw_output"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path)
        )
        if not output:
            return

        def worker():
            for _file_index, obj in records:
                name = obj.peek_name(f"{obj.type.name}_{obj.path_id}")
                with open(os.path.join(output, f"{_safe_filename(name)}_{obj.path_id}.dat"), "wb") as handle:
                    handle.write(obj.get_raw_data())
            return len(records)

        self._submit_operation(
            "export_raw", worker,
            lambda count: show_dialog(self.window, tr("export_done"), tr("raw_exported", count=count))
        )

    def import_raw(self):
        records = self._selected_records()
        if len(records) != 1:
            return
        path = askopenfile(
            self.window, tr("pick_raw"),
            [("Raw data", ("*.dat",)), (tr("all_files"), ("*",))]
        )
        if not path:
            return
        file_index, obj = records[0]

        def worker():
            with open(path, "rb") as handle:
                obj.set_raw_data(handle.read())
            self._refresh_relationships_after_import(file_index, obj)
            return file_index, obj

        def success(value):
            self._mark_modified(*value, "Raw")
            self._request_preview(*value)
            show_dialog(self.window, tr("import_done"), tr("raw_replaced"))

        self._submit_operation("import_raw", worker, success)

    def export_texture(self):
        records = self._selected_records()
        output = askdirectory(
            self.window, tr("pick_png_output"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path)
        )
        if not output:
            return

        def worker():
            dependency_cache = {}
            for file_index, obj in records:
                key = (int(file_index), int(obj.path_id))
                override = (
                    self.sprite_preview_overrides.get(key)
                    if obj.type.name == "Sprite"
                    else self.texture_preview_overrides.get(key)
                    if obj.type.name == "Texture2D"
                    else None
                )
                if override is not None:
                    prefix = (
                        "sprite" if obj.type.name == "Sprite" else "texture"
                    )
                    name = obj.peek_name(f"{prefix}_{obj.path_id}")
                    with open(
                        os.path.join(
                            output, f"{_safe_filename(name)}_{obj.path_id}.png"
                        ),
                        "wb",
                    ) as handle:
                        handle.write(override)
                    continue
                source_obj = obj
                missing = []
                if obj.type.name == "Sprite":
                    if file_index not in dependency_cache:
                        from AssetbundleUtils.DependencyResolver import load_bundle_with_dependencies
                        dependency_cache[file_index] = load_bundle_with_dependencies(
                            self.paths[file_index]
                        )
                    _environment, primary_objects, missing = dependency_cache[file_index]
                    source_obj = primary_objects[int(obj.path_id)]
                    if (file_index, int(obj.path_id)) in self.modified:
                        source_obj.set_raw_data(obj.get_raw_data())
                data = source_obj.read(False)
                name = getattr(data, "m_Name", None) or f"texture_{obj.path_id}"
                try:
                    image = data.image
                except AttributeError as exc:
                    if missing:
                        raise RuntimeError(
                            f"Missing Sprite dependency: {', '.join(missing[:3])}"
                        ) from exc
                    raise
                image.save(os.path.join(output, f"{_safe_filename(name)}_{obj.path_id}.png"))
            return len(records)

        self._submit_operation(
            "export_png", worker,
            lambda count: show_dialog(self.window, tr("export_done"), tr("png_exported", count=count))
        )

    def import_texture(self):
        records = self._selected_records()
        if len(records) != 1:
            return
        path = askopenfile(
            self.window, tr("pick_image"),
            [("PNG", ("*.png",)), ("Images", ("*.jpg", "*.jpeg", "*.tga"))]
        )
        if not path:
            return
        file_index, obj = records[0]

        def worker():
            with Image.open(path) as source:
                image = source.convert("RGBA").copy()
            data = obj.read(False)
            info = replace_texture_image(data, image)
            data.save()
            return file_index, obj, info, texture_preview_png(data)

        def success(value):
            index, target, info, preview_png = value
            self.texture_preview_overrides[
                (int(index), int(target.path_id))
            ] = preview_png
            self._mark_modified(index, target, "Texture")
            self._request_preview(index, target)
            show_dialog(
                self.window,
                tr("image_replaced_title"),
                tr("image_replaced", **info),
            )

        self._submit_operation("import_png", worker, success)

    def import_sprite(self):
        records = self._selected_records()
        if (
            len(records) != 1
            or records[0][1].type.name != "Sprite"
            or self.sprite_project is None
        ):
            return
        path = askopenfile(
            self.window, tr("pick_sprite_image"), [("PNG", ("*.png",))]
        )
        if not path:
            return
        file_index, obj = records[0]

        def worker():
            with Image.open(path) as source:
                image = source.convert("RGBA").copy()
            return replace_sprite_image(
                obj, file_index, self.sprite_project, image
            )

        def success(info):
            key = (int(file_index), int(obj.path_id))
            self.sprite_preview_overrides[key] = info["preview_png"]
            self._mark_modified(file_index, obj, "Sprite")
            for target in info["targets"]:
                target_key = (
                    int(target["file_index"]),
                    int(target["reader"].path_id),
                )
                self.texture_preview_overrides[target_key] = target[
                    "preview_png"
                ]
                self._mark_modified(
                    int(target["file_index"]), target["reader"], "Texture"
                )
            self._request_preview(file_index, obj)
            show_dialog(
                self.window,
                tr("sprite_replaced_title"),
                tr(
                    "sprite_replaced",
                    width=info["logical_size"][0],
                    height=info["logical_size"][1],
                    slot_width=info["slot"][2],
                    slot_height=info["slot"][3],
                    format=info["format"],
                    mipmaps=info["mipmaps"],
                    atlas=info["atlas_path_id"],
                    texture=info["texture_path_id"],
                    blocks=info["changed_blocks"],
                ),
            )

        self._submit_operation("import_sprite", worker, success)

    def export_mesh(self):
        records = self._selected_records()
        output = askdirectory(
            self.window, tr("pick_obj_output"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path)
        )
        if not output:
            return

        def worker():
            for _file_index, obj in records:
                data = obj.read(False)
                name = getattr(data, "m_Name", None) or f"mesh_{obj.path_id}"
                with open(
                    os.path.join(output, f"{_safe_filename(name)}_{obj.path_id}.obj"),
                    "w", encoding="utf-8", newline=""
                ) as handle:
                    handle.write(data.export())
            return len(records)

        self._submit_operation(
            "export_mesh", worker,
            lambda count: show_dialog(self.window, tr("export_done"), tr("mesh_exported", count=count))
        )

    def import_mesh(self):
        records = self._selected_records()
        if len(records) != 1 or records[0][1].type.name != "Mesh":
            return
        path = askopenfile(
            self.window, tr("pick_mesh_obj"), [("Wavefront OBJ", ("*.obj",))]
        )
        if not path:
            return
        if not show_dialog(
            self.window, tr("replace_mesh_title"), tr("replace_mesh_body"),
            "warning", confirm=True,
        ):
            return
        file_index, obj = records[0]

        def worker():
            return file_index, obj, replace_mesh_from_obj(obj, path)

        def success(value):
            index, target, result = value
            self._mark_modified(index, target, "Mesh")
            self._request_preview(index, target)
            mode = "mode_preserved" if result.preserved_vertex_streams else (
                "mode_skin" if result.remapped_skin_weights else "mode_rebuilt"
            )
            details = tr(
                "mesh_result", name=result.mesh_name, vertices=result.vertex_count,
                indices=result.index_count, submeshes=result.submesh_count, mode=tr(mode)
            )
            if result.cleared_blend_shapes or result.cleared_collision_data:
                details += tr("cleared_incompatible")
            show_dialog(self.window, tr("mesh_replace_done"), details)

        self._submit_operation("import_verify_mesh", worker, success)

    def select_animation_model(self):
        records = self._selected_records()
        if (
            len(records) != 1 or records[0][1].type.name != "GameObject"
            or self.animation_project is None
        ):
            return
        file_index, obj = records[0]
        model = self.animation_project.find_model(file_index, int(obj.path_id))
        if model is None:
            show_dialog(
                self.window, tr("animation_model_title"),
                tr("animation_model_not_found"), "warning",
            )
            return
        self.animation_model = model
        self.animation_model_manual = True
        self._refresh_animation_model_label()
        self._update_button_states()
        show_dialog(
            self.window, tr("animation_model_title"),
            tr(
                "animation_model_body", name=model.name,
                nodes=len(model.transform_ids),
                meshes=len(model.skinned_renderer_ids),
                attachments=len(model.rigid_attachments),
            ),
        )

    def export_bundle_metadata(self):
        records = self._selected_records()
        if len(records) != 1 or records[0][1].type.name != "AssetBundle":
            return
        file_index, obj = records[0]
        output = asksavefile(
            self.window, tr("pick_bundle_metadata_output"),
            [("JSON", ("*.json",))],
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
            f"{_safe_filename(obj.peek_name('AssetBundle'))}.assetbundle.json",
            ".json",
        )
        if not output:
            return

        def worker():
            return export_assetbundle_metadata(obj, self.paths[file_index], output)

        def success(info):
            show_dialog(
                self.window, tr("bundle_metadata_exported_title"),
                tr(
                    "bundle_metadata_exported_body",
                    preload=info["preload_entries"],
                    container=info["container_entries"],
                    references=info["pptr_references"],
                    path=info["path"],
                ),
            )

        self._submit_operation("export_bundle_metadata", worker, success)

    def import_bundle_metadata(self):
        records = self._selected_records()
        if len(records) != 1 or records[0][1].type.name != "AssetBundle":
            return
        path = askopenfile(
            self.window, tr("pick_bundle_metadata_input"),
            [("JSON", ("*.json",))],
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
        )
        if not path or not show_dialog(
            self.window, tr("replace_bundle_metadata_title"),
            tr("replace_bundle_metadata_body"), "warning", confirm=True,
        ):
            return
        file_index, obj = records[0]

        def worker():
            info = import_assetbundle_metadata(
                obj, file_index, path, self.animation_project
            )
            self._refresh_relationships_after_import(file_index, obj)
            return file_index, obj, info

        def success(value):
            index, target, info = value
            self._mark_modified(index, target, "Metadata")
            show_dialog(
                self.window, tr("bundle_metadata_imported_title"),
                tr(
                    "bundle_metadata_imported_body",
                    preload=info["preload_entries"],
                    references=info["references_checked"],
                    unresolved=len(info["unresolved_external_files"]),
                    path_id=info["path_id"],
                ),
            )

        self._submit_operation("import_bundle_metadata", worker, success)

    def export_bundle_projects(self):
        if self.loading or not any(self.env_list):
            return
        output = askdirectory(
            self.window,
            tr("pick_bundle_project_output"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
        )
        if not output:
            return

        def worker():
            results = []
            for index, environment in enumerate(self.env_list):
                if environment is None:
                    continue
                results.append(
                    export_bundle_project(
                        self.paths[index], output, export_decoded=True
                    )
                )
            return results

        def success(results):
            show_dialog(
                self.window,
                tr("bundle_project_exported_title"),
                tr(
                    "bundle_project_exported_body",
                    bundles=len(results),
                    assets=sum(item["assets"] for item in results),
                    path=output,
                ),
            )

        self._submit_operation("export_bundle_project", worker, success)

    def rebuild_from_bundle_project(self):
        project_dir = askdirectory(
            self.window,
            tr("pick_bundle_project_input"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
        )
        if not project_dir:
            return
        manifest_path = os.path.join(project_dir, "bundle_manifest.json")
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as handle:
                manifest = json.load(handle)
            source_name = manifest["source"]["file_name"]
        except Exception as exc:
            show_dialog(
                self.window,
                tr("bundle_project_invalid_title"),
                tr("bundle_project_invalid_body", error=str(exc)),
                "error",
            )
            return
        stem, extension = os.path.splitext(source_name)
        output = asksavefile(
            self.window,
            tr("pick_bundle_project_rebuild_output"),
            [("AssetBundle", ("*.assetbundle", "*.bundle", "*.ab"))],
            os.path.dirname(project_dir),
            f"{stem}_rebuilt{extension or '.assetbundle'}",
            extension or ".assetbundle",
        )
        if not output or not show_dialog(
            self.window,
            tr("bundle_project_rebuild_title"),
            tr("bundle_project_rebuild_body"),
            "warning",
            confirm=True,
        ):
            return

        def worker():
            return rebuild_bundle_project(
                project_dir,
                output,
                packer="auto",
                optimize_atlas_textures=False,
            )

        def success(info):
            show_dialog(
                self.window,
                tr("bundle_project_rebuilt_title"),
                tr(
                    "bundle_project_rebuilt_body",
                    assets=info["assets"],
                    added=len(info.get("added_assets", [])),
                    deleted=len(info.get("deleted_assets", [])),
                    renamed=len(info.get("renamed_assets", [])),
                    optimized=len(info["optimized_textures"]),
                    repaired=len(info["added_preload_path_ids"]),
                    bytes=info["bytes"],
                    path=info["path"],
                ),
            )

        self._submit_operation("rebuild_bundle_project", worker, success)

    def export_animation(self):
        records = self._selected_records()
        if (
            len(records) != 1 or records[0][1].type.name != "AnimationClip"
            or self.animation_project is None or self.animation_model is None
        ):
            return
        file_index, obj = records[0]
        name = obj.peek_name(f"Animation_{obj.path_id}")
        output = asksavefile(
            self.window, tr("pick_animation_fbx_output"),
            [("FBX", ("*.fbx",))],
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
            f"{_safe_filename(name)}.fbx", ".fbx",
        )
        if not output:
            return
        include_model = bool(self.include_model_var.get())
        include_attachments = bool(self.include_attachments_var.get())

        def worker():
            return export_animation_fbx(
                self.animation_project, file_index, int(obj.path_id),
                self.animation_model, output, include_model=include_model,
                include_attachments=include_attachments,
            )

        def success(info):
            show_dialog(
                self.window, tr("animation_exported_title"),
                tr(
                    "animation_exported_body_v2", name=name,
                    tracks=info["tracks"], meshes=info["skinned_meshes"],
                    attachments=info["attachment_meshes"],
                    path=info["path"],
                ),
            )

        self._submit_operation("export_animation_fbx", worker, success)

    def export_effect(self):
        records = self._selected_records()
        if (
            len(records) != 1 or self.effect_project is None
            or records[0][1].type.name != "GameObject"
        ):
            return
        file_index, selected = records[0]
        root = self._resolve_effect_for_asset(
            file_index, int(selected.path_id), selected.type.name
        )
        if root is None:
            return
        output = askdirectory(
            self.window, tr("pick_effect_output"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
        )
        if not output:
            return

        def worker():
            return export_effect_directory(self.effect_project, root, output)

        def success(info):
            show_dialog(
                self.window, tr("effect_exported_title"),
                tr(
                    "effect_exported_body", name=info["root"],
                    nodes=info["nodes"], animations=info["animations"],
                    assets=info["assets"], path=info["path"],
                ),
            )

        self._submit_operation("export_effect_package", worker, success)

    def import_animation(self):
        records = self._selected_records()
        if (
            len(records) != 1 or records[0][1].type.name != "AnimationClip"
            or self.animation_project is None or self.animation_model is None
        ):
            return
        path = askopenfile(
            self.window, tr("pick_animation_fbx_input"),
            [("FBX", ("*.fbx",))],
            self.input_path if self.is_directory else os.path.dirname(self.input_path),
        )
        if not path or not show_dialog(
            self.window, tr("replace_animation_title"),
            tr("replace_animation_body"), "warning", confirm=True,
        ):
            return
        file_index, obj = records[0]

        def worker():
            info = replace_animation_from_fbx(
                self.animation_project, file_index, int(obj.path_id),
                self.animation_model, path,
            )
            return file_index, obj, info

        def success(value):
            index, target, info = value
            self._mark_modified(index, target, "Animation")
            self._request_preview(index, target)
            show_dialog(
                self.window, tr("animation_replaced_title"),
                tr(
                    "animation_replaced_body", name=info["name"],
                    frames=info["frame_count"], nodes=info["animated_nodes"],
                    path_id=info["path_id"],
                ),
            )

        self._submit_operation("import_animation_fbx", worker, success)

    def save_bundles(self):
        self._save_bundles_with_packer("auto", fingerprint=False)

    def save_fingerprint_bundles(self):
        self._save_bundles_with_packer("aov-fingerprint-3", fingerprint=True)

    def _save_bundles_with_packer(self, packer, fingerprint=False):
        if self.loading or not any(self.env_list):
            return
        output = askdirectory(
            self.window, tr("pick_ab_output"),
            self.input_path if self.is_directory else os.path.dirname(self.input_path)
        )
        if not output or not show_dialog(
            self.window,
            tr("save_verify"),
            tr("fingerprint_save_verify_body" if fingerprint else "save_verify_body"),
            confirm=True,
        ):
            return
        self.save_button.set_enabled(False)
        self.fingerprint_save_button.set_enabled(False)
        self._set_status(
            "saving_fingerprint_validating" if fingerprint else "saving_validating"
        )
        self.progress.pack(side="right")
        self.progress.start(12)

        def save_one(index):
            env = self.env_list[index]
            if env is None:
                return None
            source_bundle = getattr(
                env, "file", next(iter(env.files.values()))
            )
            repair_sprite_atlas_preloads(env)
            # AssetBundle block compression is lossless.  Do not silently
            # transcode RGBA32 atlases to ETC2 here: that makes the output
            # smaller by discarding color detail around UI glyphs and alpha
            # edges.  Texture2D format changes remain an explicit operation.
            selected_packer = _select_rebuild_packer(source_bundle, packer)
            uses_fingerprint3 = selected_packer == "aov-fingerprint-3"
            uses_fingerprint2 = selected_packer == "aov-fingerprint-2"
            uses_fingerprint1 = selected_packer == "aov-fingerprint-1"
            expected_inventory = {
                (int(obj.path_id), obj.type.name) for obj in env.objects
            }
            expected_modified = {
                path_id for file_index, path_id in self.modified
                if file_index == index
            }
            current_objects = {int(obj.path_id): obj for obj in env.objects}
            texture_expectations = {}
            for path_id, current in current_objects.items():
                if current.type.name != "Texture2D":
                    continue
                texture = current.read(False)
                texture_expectations[path_id] = {
                    "metadata": texture_runtime_metadata(texture),
                    "image_sha256": hashlib.sha256(
                        bytes(texture.image_data)
                    ).hexdigest(),
                }
            data = source_bundle.save(selected_packer)
            target = os.path.join(output, os.path.basename(self.paths[index]))
            with open(target, "wb") as handle:
                handle.write(data)
            reloaded = UnityPy_AOV.load(target)
            reloaded_map = {int(obj.path_id): obj for obj in reloaded.objects}
            reloaded_inventory = {
                (int(obj.path_id), obj.type.name) for obj in reloaded.objects
            }
            if reloaded_inventory != expected_inventory:
                raise ValueError("Asset PathID/type inventory changed after reload")
            if uses_fingerprint1:
                bundle = getattr(
                    reloaded, "file", next(iter(reloaded.files.values()))
                )
                if getattr(bundle, "special_storage_format", None) != (
                    "aov-sm4-blockinfo-at-end-lzma"
                ) or int(getattr(bundle, "dataflags", 0)) != 0x6C1:
                    raise ValueError(
                        "Original EOF-LZMA fingerprint verification failed"
                    )
            elif uses_fingerprint3:
                bundle = getattr(
                    reloaded, "file", next(iter(reloaded.files.values()))
                )
                if getattr(bundle, "special_storage_format", None) != (
                    "aov-sm4-blockinfo-prefix-lzma"
                ) or int(getattr(bundle, "dataflags", 0)) != 0x641:
                    raise ValueError("Third-fingerprint header verification failed")
            elif uses_fingerprint2:
                bundle = getattr(
                    reloaded, "file", next(iter(reloaded.files.values()))
                )
                if getattr(bundle, "special_storage_format", None) != (
                    "aov-sm4-blockinfo-prefix-lz4hc"
                ) or int(getattr(bundle, "dataflags", 0)) != 0x643:
                    raise ValueError("Second-fingerprint header verification failed")
            missing = expected_modified.difference(reloaded_map)
            if missing:
                raise ValueError(f"Missing PathID after reload: {sorted(missing)}")
            for path_id, expectation in texture_expectations.items():
                checked = reloaded_map.get(path_id)
                if checked is None or checked.type.name != "Texture2D":
                    raise ValueError(
                        f"Texture2D {path_id} was lost after reload"
                    )
                texture = checked.read(False)
                validate_texture_roundtrip(
                    texture, expectation["metadata"]
                )
                actual_hash = hashlib.sha256(
                    bytes(texture.image_data)
                ).hexdigest()
                if actual_hash != expectation["image_sha256"]:
                    raise ValueError(
                        f"Texture2D {path_id} encoded pixels changed during "
                        "AssetBundle compression"
                    )
            for path_id in expected_modified:
                checked = reloaded_map[path_id]
                if checked.type.name == "Mesh":
                    parsed = checked.read(False)
                    if parsed.m_VertexCount <= 0 or not parsed.m_Indices or not parsed.export():
                        raise ValueError(f"Mesh {path_id} reload validation failed")
                elif checked.type.name == "AnimationClip":
                    tree = checked.read_typetree()
                    dense = tree["m_MuscleClip"]["m_Clip"]["data"]["m_DenseClip"]
                    frame_count = int(dense["m_FrameCount"])
                    curve_count = int(dense["m_CurveCount"])
                    if (
                        frame_count <= 1 or curve_count <= 0
                        or len(dense["m_SampleArray"]) != frame_count * curve_count
                    ):
                        raise ValueError(f"AnimationClip {path_id} reload validation failed")
                elif checked.type.name == "Texture2D":
                    # Every Texture2D, modified or untouched, was already
                    # validated byte-for-byte above.
                    continue
                else:
                    checked.read(False)
            return target

        def worker():
            targets = []
            indexes = [index for index, env in enumerate(self.env_list) if env is not None]
            with ThreadPoolExecutor(max_workers=min(2, len(indexes) or 1), thread_name_prefix="AOVSave") as pool:
                for future in as_completed([pool.submit(save_one, index) for index in indexes]):
                    target = future.result()
                    if target:
                        targets.append(target)
            return sorted(targets)

        future = self.executor.submit(worker)
        future.add_done_callback(
            lambda done: self.events.put(
                ("save_done", done.result(), None, fingerprint)
                if done.exception() is None
                else ("save_done", None, str(done.exception()), fingerprint)
            )
        )

    def _save_done(self, targets, error, fingerprint=False):
        self.progress.stop()
        self.progress.pack_forget()
        self.save_button.set_enabled(True)
        self.fingerprint_save_button.set_enabled(True)
        if error:
            self._set_status("save_validation_failed")
            show_dialog(self.window, tr("save_failed"), error, "error")
            return
        self._set_status("save_complete", count=len(targets))
        show_dialog(
            self.window, tr("save_success"),
            tr(
                "fingerprint_save_success_body" if fingerprint else "save_success_body",
                count=len(targets),
                folder=os.path.dirname(targets[0]) if targets else ""
            ),
        )

    def close(self):
        global list_window
        if self.closed:
            return
        self.closed = True
        unsubscribe(self.language_listener)
        self.preview_generation += 1
        for job in (
            self.preview_after_job, self.search_after_job, self.virtual_render_job,
            self.selection_after_job, self.drawer_animation_job,
            self.event_drain_job,
        ):
            if job is not None:
                try:
                    self.window.after_cancel(job)
                except tk.TclError:
                    pass
        if self.preview_latest_generation is not None:
            self.preview_latest_generation.value = int(self.preview_generation)
        if self.preview_requests:
            for request_queue in self.preview_requests:
                try:
                    request_queue.put_nowait(None)
                except queue.Full:
                    pass
        for process in self.preview_processes:
            process.join(timeout=0.35)
            if process.is_alive():
                process.terminate()
                process.join(timeout=0.25)
        if self.obj_viewer is not None:
            try:
                self.obj_viewer.destroy()
            except Exception:
                pass
            self.obj_viewer = None
        channels = list(self.preview_requests or [])
        channels.append(self.preview_results)
        for channel in channels:
            if channel is not None:
                try:
                    channel.close()
                    channel.join_thread()
                except (OSError, ValueError):
                    pass
        # Ensure futures holding Tk variables release them on the GUI thread.
        # This prevents Tcl_AsyncDelete when several browser windows are opened
        # and closed sequentially in one process.
        self.executor.shutdown(wait=True, cancel_futures=True)
        if self.load_thread is not None and self.load_thread.is_alive():
            self.load_thread.join(timeout=1.5)
        # Tk variables must be released while their interpreter is still alive.
        # Otherwise a final reference dropped by a worker can run Variable.__del__
        # after root.destroy() and abort Tcl with an async-handler error.
        for attribute in (
            "include_model_var", "include_attachments_var",
            "preview_attachments_var", "preview_model_text_var",
            "preview_attachment_text_var",
            "effect_loop_var", "effect_timeline_var", "effect_time_var",
            "workspace_title_var", "subtitle_var", "search_var",
            "asset_count_var", "selection_title", "selection_meta",
            "status_var", "drawer_title_var", "drawer_hint_var",
            "drawer_assets_var", "drawer_animation_var",
            "drawer_effect_var",
            "animation_model_var", "attachment_summary_var",
        ):
            variable = getattr(self, attribute, None)
            if variable is None:
                continue
            try:
                variable._tk.globalunsetvar(variable._name)
            except (tk.TclError, RuntimeError):
                pass
            variable._tk = None
            setattr(self, attribute, None)
        self.window.destroy()
        list_window = None


def list_assets_window(input_path, IsInputDir=False, parent=None):
    global list_window
    if list_window is not None:
        try:
            if list_window.winfo_exists():
                list_window.lift()
                return None
        except tk.TclError:
            list_window = None
    return AssetBrowser(input_path, IsInputDir, parent)
