# -*- coding: utf-8 -*-
"""AOV Asset Workshop — OpenAI Aurora desktop shell."""

from __future__ import annotations

import multiprocessing
import os
import shutil
import sys
import tkinter as tk
import traceback

from tkinterdnd2 import COPY, DND_FILES, REFUSE_DROP, TkinterDnD

from UI.FilePicker import askdirectory, askopenfile
from UI.I18n import (
    LANGUAGE_LABELS, LANGUAGES, get_language, set_language, subscribe, tr,
    unsubscribe,
)
from UI.ModernTheme import (
    AuroraBackdrop, COLORS, FONTS, RoundedButton, SegmentedControl,
    apply_all_styles, center_window, rounded_rectangle, set_rounded_window,
    show_dialog,
)
from AssetbundleUtils.AssetsList import list_assets_window


APP_VERSION = "2.3.6"


def get_resource_path(filename: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


def _startup_log_path() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
    folder = os.path.join(base, "AOV_UABE_2022")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "startup_error.log")


def _report_startup_error(error: BaseException) -> None:
    """Make windowed-build startup failures diagnosable instead of silently exiting."""

    log_path = _startup_log_path()
    details = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    try:
        with open(log_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"AOV UABE 2022 v{APP_VERSION}\n\n")
            handle.write(details)
    except OSError:
        pass
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            "程序启动失败，错误日志已保存到：\n"
            f"{log_path}\n\n{type(error).__name__}: {error}",
            "AOV UABE 2022",
            0x10,
        )
    except Exception:
        pass


class Launcher:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.selected_path: str | None = None
        self.is_directory = False
        self.path_mode = "file"
        self.lang_code = get_language()
        self.language_listener = subscribe(self.apply_language)
        self.vars: dict[str, tk.StringVar] = {}

        root.title(tr("app_title"))
        root.minsize(920, 580)
        root.configure(bg=COLORS["canvas"])
        center_window(root, 1060, 680)
        set_rounded_window(root)
        try:
            root.iconbitmap(get_resource_path("icon.ico"))
        except Exception:
            pass
        root.protocol("WM_DELETE_WINDOW", self.close)
        apply_all_styles()

        self.backdrop = AuroraBackdrop(root, calm=True)
        self.backdrop.pack(fill="both", expand=True)
        self.shell = tk.Frame(self.backdrop, bg=COLORS["surface"])
        self.shell_id = self.backdrop.create_window(34, 30, anchor="nw", window=self.shell)
        self.backdrop.bind("<Configure>", self._resize_shell, add="+")
        self._build_shell()
        self._build_drop_overlay()
        self.apply_language(self.lang_code)
        self._register_drop_targets()

    def _var(self, key: str) -> tk.StringVar:
        value = tk.StringVar(value=tr(key))
        self.vars[key] = value
        return value

    def _resize_shell(self, event):
        self.backdrop.itemconfigure(
            self.shell_id,
            width=max(760, event.width - 68),
            height=max(500, event.height - 60),
        )

    def _build_shell(self):
        top = tk.Frame(self.shell, bg=COLORS["surface"], padx=34, pady=19)
        top.pack(fill="x")
        brand = tk.Frame(top, bg=COLORS["surface"])
        brand.pack(side="left")
        logo = tk.Canvas(brand, width=44, height=44, bg=COLORS["surface"], highlightthickness=0)
        logo.pack(side="left", padx=(0, 12))
        rounded_rectangle(logo, 1, 1, 43, 43, 13, fill=COLORS["ink"], outline=COLORS["ink"])
        logo.create_text(22, 22, text="A", fill="#A6E2E7", font=("Segoe UI", 18, "bold"))
        title_box = tk.Frame(brand, bg=COLORS["surface"])
        title_box.pack(side="left")
        tk.Label(
            title_box, text="AOV Asset Workshop", bg=COLORS["surface"],
            fg=COLORS["text_primary"], font=FONTS["heading"], anchor="w"
        ).pack(anchor="w")
        tk.Label(
            title_box, textvariable=self._var("brand_subtitle"),
            bg=COLORS["surface"], fg=COLORS["text_muted"],
            font=FONTS["tiny"], anchor="w"
        ).pack(anchor="w", pady=(2, 0))

        self.language_control = SegmentedControl(
            top,
            [(code, LANGUAGE_LABELS[code]) for code in LANGUAGES],
            self.lang_code,
            self.change_language,
            width=306,
            height=38,
            bg=COLORS["surface"],
        )
        self.language_control.pack(side="right")
        self.about_button = RoundedButton(top, tr("about"), self.about, 84, 38, "secondary")
        self.about_button.pack(side="right", padx=(0, 10))

        tk.Frame(self.shell, height=1, bg=COLORS["border_light"]).pack(fill="x")
        content = tk.Frame(self.shell, bg=COLORS["surface"], padx=44, pady=26)
        content.pack(fill="both", expand=True)
        tk.Label(
            content, textvariable=self._var("hero"), bg=COLORS["surface"],
            fg=COLORS["text_primary"], font=FONTS["hero"],
            justify="left", anchor="w"
        ).pack(fill="x")
        tk.Label(
            content, textvariable=self._var("hero_subtitle"),
            bg=COLORS["surface"], fg=COLORS["text_secondary"],
            font=FONTS["body"], justify="left", anchor="w", wraplength=860
        ).pack(fill="x", pady=(10, 22))

        picker = tk.Frame(content, bg=COLORS["surface_alt"], padx=22, pady=18)
        picker.pack(fill="x")
        picker_top = tk.Frame(picker, bg=COLORS["surface_alt"])
        picker_top.pack(fill="x")
        self.kind_var = tk.StringVar(value=tr("no_project"))
        tk.Label(
            picker_top, textvariable=self.kind_var, bg=COLORS["surface_alt"],
            fg=COLORS["primary"], font=FONTS["small"], anchor="w"
        ).pack(side="left")
        self.path_mode_control = SegmentedControl(
            picker_top,
            [("file", tr("bundle_file")), ("folder", tr("project_folder"))],
            self.path_mode,
            self.change_path_mode,
            width=286,
            height=36,
            bg=COLORS["surface_alt"],
        )
        self.path_mode_control.pack(side="right")

        self.path_var = tk.StringVar(value=tr("pick_hint_file"))
        self.path_row = tk.Frame(
            picker, bg=COLORS["surface"], padx=8, pady=7,
            highlightthickness=2, highlightbackground=COLORS["primary_light"]
        )
        self.path_row.pack(fill="x", pady=(12, 0))
        self.path_entry = tk.Entry(
            self.path_row, textvariable=self.path_var, state="readonly",
            readonlybackground=COLORS["surface"], fg=COLORS["text_primary"],
            selectbackground=COLORS["primary_light"], selectforeground=COLORS["text_primary"],
            relief="flat", bd=0, font=FONTS["body"], cursor="hand2",
            takefocus=True,
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(10, 12), ipady=9)
        self.path_entry.bind("<ButtonRelease-1>", lambda _event: self.browse_path())
        self.path_entry.bind("<Return>", lambda _event: self.browse_path())
        self.path_entry.bind("<space>", lambda _event: self.browse_path())
        self.browse_button = RoundedButton(
            self.path_row, tr("browse"), self.browse_path, 108, 42,
            "secondary", bg=COLORS["surface"]
        )
        self.browse_button.pack(side="right", padx=(0, 8))
        self.open_button = RoundedButton(
            self.path_row, tr("load_browse"), self.open_assets, 180, 42,
            "primary", bg=COLORS["surface"]
        )
        self.open_button.pack(side="right", padx=(0, 8))
        self.open_button.set_enabled(False)
        self.drop_hint_label = tk.Label(
            picker, textvariable=self._var("drop_hint"),
            bg=COLORS["surface_alt"], fg=COLORS["text_muted"],
            font=FONTS["tiny"], anchor="w", justify="left",
        )
        self.drop_hint_label.pack(fill="x", pady=(9, 0))

        features = tk.Frame(content, bg=COLORS["surface"])
        features.pack(fill="x", pady=(22, 0))
        self.feature_vars = []
        feature_keys = [
            ("feature_parallel", "feature_parallel_detail"),
            ("feature_mesh", "feature_mesh_detail"),
            ("feature_verify", "feature_verify_detail"),
        ]
        for index, (label_key, detail_key) in enumerate(feature_keys):
            card = tk.Frame(features, bg=COLORS["surface"], padx=2)
            card.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 10, 0))
            label_var, detail_var = tk.StringVar(), tk.StringVar()
            self.feature_vars.append((label_key, label_var, detail_key, detail_var))
            tk.Label(
                card, textvariable=label_var, bg=COLORS["surface"],
                fg=COLORS["text_primary"], font=FONTS["body_bold"], anchor="w"
            ).pack(fill="x")
            tk.Label(
                card, textvariable=detail_var, bg=COLORS["surface"],
                fg=COLORS["text_muted"], font=FONTS["tiny"], anchor="w",
                wraplength=260, justify="left"
            ).pack(fill="x", pady=(4, 0))
            features.columnconfigure(index, weight=1)

        footer = tk.Frame(self.shell, bg=COLORS["surface_alt"], padx=34, pady=12)
        footer.pack(fill="x", side="bottom")
        tk.Label(
            footer, textvariable=self._var("research_use"), bg=COLORS["surface_alt"],
            fg=COLORS["text_muted"], font=FONTS["tiny"]
        ).pack(side="left")
        tk.Label(
            footer, textvariable=self._var("ready"), bg=COLORS["surface_alt"],
            fg=COLORS["success"], font=FONTS["tiny"]
        ).pack(side="right")

    def _build_drop_overlay(self):
        self.drop_overlay_visible = False
        self.drop_overlay = tk.Frame(
            self.root, bg=COLORS["canvas"],
            highlightthickness=2, highlightbackground=COLORS["primary"],
        )
        self.drop_overlay.grid_columnconfigure(0, weight=1)
        self.drop_overlay.grid_rowconfigure(0, weight=1)

        self.drop_panel = tk.Frame(
            self.drop_overlay, bg=COLORS["surface"], padx=68, pady=54,
            highlightthickness=2, highlightbackground=COLORS["primary_light"],
        )
        self.drop_panel.grid(
            row=0, column=0, padx=74, pady=62, sticky="nsew",
        )
        self.drop_panel.grid_columnconfigure(0, weight=1)
        self.drop_panel.grid_rowconfigure(0, weight=1)
        drop_content = tk.Frame(self.drop_panel, bg=COLORS["surface"])
        drop_content.grid(row=0, column=0)

        self.drop_icon = tk.Canvas(
            drop_content, width=104, height=104, bg=COLORS["surface"],
            highlightthickness=0,
        )
        self.drop_icon.pack(pady=(0, 24))
        self.drop_icon.create_oval(
            3, 3, 101, 101, fill=COLORS["primary_light"],
            outline=COLORS["primary"], width=2, tags="drop_ring",
        )
        self.drop_icon.create_line(
            52, 28, 52, 67, fill=COLORS["primary"], width=6,
            capstyle="round", tags="drop_arrow",
        )
        self.drop_icon.create_line(
            35, 51, 52, 69, 69, 51, fill=COLORS["primary"], width=6,
            capstyle="round", joinstyle="round", tags="drop_arrow",
        )
        self.drop_icon.create_line(
            31, 80, 73, 80, fill=COLORS["cyan"], width=5,
            capstyle="round", tags="drop_arrow",
        )

        tk.Label(
            drop_content, textvariable=self._var("drop_overlay_title"),
            bg=COLORS["surface"], fg=COLORS["text_primary"],
            font=FONTS["hero"], anchor="center", justify="center",
        ).pack()
        tk.Label(
            drop_content, textvariable=self._var("drop_overlay_body"),
            bg=COLORS["surface"], fg=COLORS["text_secondary"],
            font=FONTS["body"], anchor="center", justify="center",
            wraplength=620,
        ).pack(pady=(13, 0))

    def _register_drop_targets(self):
        for widget in (self.root, self.drop_overlay):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<DropEnter>>", self._on_drop_enter)
            widget.dnd_bind("<<DropPosition>>", self._on_drop_position)
            widget.dnd_bind("<<DropLeave>>", self._on_drop_leave)
            widget.dnd_bind("<<Drop>>", self._on_drop)

    def _show_drop_overlay(self):
        if self.drop_overlay_visible:
            return
        self.drop_overlay_visible = True
        self.drop_overlay.place(x=0, y=0, relwidth=1, relheight=1)
        self.drop_overlay.lift()
        self.drop_icon.itemconfigure("drop_ring", outline=COLORS["accent"], width=3)

    def _hide_drop_overlay(self):
        if not self.drop_overlay_visible:
            return
        self.drop_overlay_visible = False
        self.drop_overlay.place_forget()
        self.drop_icon.itemconfigure("drop_ring", outline=COLORS["primary"], width=2)

    def _on_drop_enter(self, _event):
        self._show_drop_overlay()
        return COPY

    def _on_drop_position(self, _event):
        self._show_drop_overlay()
        return COPY

    def _on_drop_leave(self, _event):
        self._hide_drop_overlay()

    def _on_drop(self, event):
        self._hide_drop_overlay()
        try:
            paths = list(self.root.tk.splitlist(event.data))
        except (AttributeError, tk.TclError):
            paths = []
        if not paths:
            return REFUSE_DROP
        self.root.after_idle(self.handle_dropped_paths, paths)
        return COPY

    def apply_language(self, code: str):
        self.lang_code = code
        self.root.title(tr("app_title"))
        for key, variable in self.vars.items():
            variable.set(tr(key))
        for label_key, label_var, detail_key, detail_var in self.feature_vars:
            label_var.set(tr(label_key))
            detail_var.set(tr(detail_key))
        self.about_button.set_text(tr("about"))
        self.browse_button.set_text(tr("browse"))
        self.open_button.set_text(tr("load_browse"))
        self.language_control.set_selected(code)
        self.path_mode_control.set_options(
            [("file", tr("bundle_file")), ("folder", tr("project_folder"))]
        )
        self.path_mode_control.set_selected(self.path_mode)
        if self.selected_path:
            self.kind_var.set(tr("project_folder" if self.is_directory else "bundle_file"))
            self.path_var.set(self.selected_path)
        else:
            self.kind_var.set(tr("no_project"))
            self.path_var.set(tr("pick_hint_folder" if self.path_mode == "folder" else "pick_hint_file"))

    def change_language(self, code: str):
        set_language(code)

    def change_path_mode(self, mode: str):
        if mode == self.path_mode:
            return
        self.path_mode = mode
        self.selected_path = None
        self.is_directory = mode == "folder"
        self.kind_var.set(tr("no_project"))
        self.path_var.set(tr("pick_hint_folder" if self.is_directory else "pick_hint_file"))
        self.open_button.set_enabled(False)

    def browse_path(self):
        if self.path_mode == "folder":
            self.pick_directory()
        else:
            self.pick_file()

    def _set_selection(self, path: str, is_directory: bool):
        self.selected_path = os.path.abspath(path)
        self.is_directory = is_directory
        self.path_mode = "folder" if is_directory else "file"
        self.path_mode_control.set_selected(self.path_mode)
        self.kind_var.set(tr("project_folder" if is_directory else "bundle_file"))
        self.path_var.set(self.selected_path)
        self.open_button.set_enabled(True)

    @staticmethod
    def _is_bundle_file(path: str) -> bool:
        name = os.path.basename(path).lower()
        extension = os.path.splitext(name)[1]
        return name.endswith((".assetbundle", ".bundle", ".ab")) or (
            not extension and "assetbundle" in name
        )

    def handle_dropped_paths(self, paths: list[str]):
        existing = [path for path in paths if os.path.exists(path)]
        if len(existing) != 1:
            show_dialog(
                self.root, tr("drop_invalid_title"), tr("drop_single_body"), "warning",
            )
            return

        path = existing[0]
        is_directory = os.path.isdir(path)
        if not is_directory and not (os.path.isfile(path) and self._is_bundle_file(path)):
            show_dialog(
                self.root, tr("drop_invalid_title"), tr("drop_invalid_body"), "warning",
            )
            return

        self._set_selection(path, is_directory)
        self.kind_var.set(tr("drop_loading_folder" if is_directory else "drop_loading_file"))
        self.root.after(120, self.open_assets)

    def pick_file(self):
        path = askopenfile(
            self.root,
            tr("pick_bundle_title"),
            [("AssetBundle", ("*.assetbundle", "*.ab", "*.bundle")), (tr("all_files"), ("*",))],
            os.path.dirname(self.selected_path) if self.selected_path else None,
        )
        if path:
            self._set_selection(path, False)

    def pick_directory(self):
        path = askdirectory(
            self.root,
            tr("pick_bundle_folder_title"),
            self.selected_path if self.selected_path and os.path.isdir(self.selected_path) else None,
        )
        if path:
            self._set_selection(path, True)

    def open_assets(self):
        if self.selected_path:
            list_assets_window(self.selected_path, self.is_directory, self.root)

    def about(self):
        show_dialog(self.root, tr("app_title"), tr("about_body"))

    def close(self):
        unsubscribe(self.language_listener)
        self._hide_drop_overlay()
        temp_path = os.path.join(os.path.dirname(__file__), "AssetbundleUtils", "tmp")
        try:
            if os.path.isdir(temp_path):
                shutil.rmtree(temp_path)
        except OSError:
            pass
        self.root.destroy()


def main():
    multiprocessing.freeze_support()
    root = TkinterDnD.Tk()
    Launcher(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        _report_startup_error(error)
        raise SystemExit(1)
