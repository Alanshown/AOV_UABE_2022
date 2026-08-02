"""OpenAI Aurora / SwiftUI-inspired Tkinter design system."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk
from tkinter import font as tkfont, ttk
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageTk


COLORS = {
    "ink": "#172033",
    "text_primary": "#172033",
    "text_secondary": "#5E6B80",
    "text_muted": "#8995A8",
    "text_white": "#FFFFFF",
    "text_black": "#111827",
    "canvas": "#EAF4FA",
    "bg_light": "#F4F8FB",
    "bg_white": "#FFFFFF",
    "bg_card": "#FBFDFF",
    "bg_hover": "#F0F5FA",
    "bg_dark": "#172033",
    "bg_medium": "#2B3548",
    "surface": "#F9FCFE",
    "surface_alt": "#EEF4F8",
    "border": "#DCE6EE",
    "border_light": "#EAF0F5",
    "border_focus": "#6A67E8",
    "primary": "#5C5BD6",
    "primary_hover": "#4B49C5",
    "primary_active": "#3F3EAA",
    "primary_light": "#E9E8FF",
    "accent": "#6977E8",
    "cyan": "#70CDE0",
    "lavender": "#BCA8F4",
    "success": "#2B9A76",
    "warning": "#D28A32",
    "error": "#CB5360",
    "row_even": "#FBFDFF",
    "row_odd": "#F5F9FC",
    "row_selected": "#E5E7FF",
    "row_hover": "#EEF2FF",
    "viewer_bg": "#1F2838",
    "viewer_grid": "#3D4960",
    "gradient_start": "#8DDCE1",
    "gradient_end": "#BEA8F4",
}

FONTS = {
    "hero": ("Microsoft YaHei UI", 28, "bold"),
    "title": ("Microsoft YaHei UI", 16, "bold"),
    "heading": ("Microsoft YaHei UI", 12, "bold"),
    "body": ("Microsoft YaHei UI", 10),
    "body_bold": ("Microsoft YaHei UI", 10, "bold"),
    "small": ("Microsoft YaHei UI", 9),
    "tiny": ("Microsoft YaHei UI", 8),
    "mono": ("Cascadia Mono", 9),
}

BUTTON_STYLES = {}
HINTS = {
    "mesh_viewer": {
        "zh-cn": "左键拖动旋转  ·  Ctrl + 左键平移  ·  滚轮缩放  ·  Ctrl + W 切换线框",
        "zh-tw": "左鍵拖動旋轉  ·  Ctrl + 左鍵平移  ·  滾輪縮放  ·  Ctrl + W 切換線框",
        "en": "Drag to rotate  ·  Ctrl + drag to pan  ·  Wheel to zoom  ·  Ctrl + W for wireframe",
        "vn": "Kéo để xoay  ·  Ctrl + kéo để di chuyển  ·  Cuộn để thu phóng",
    }
}


def rounded_rectangle(canvas: tk.Canvas, x1, y1, x2, y2, radius=16, **kwargs):
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **kwargs)


def set_rounded_window(window: tk.Misc, dark: bool = False) -> None:
    """Enable native Windows 11 rounded corners and a matching title bar."""

    if os.name != "nt":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        preference = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference)
        )
        backdrop = ctypes.c_int(2)  # Mica-like backdrop where supported
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop)
        )
        title_color = ctypes.c_int(0x00282017 if dark else 0x00FBF8F4)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, 35, ctypes.byref(title_color), ctypes.sizeof(title_color)
        )
    except Exception:
        pass


def center_window(window: tk.Misc, width: int, height: int) -> None:
    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2 - 18)
    window.geometry(f"{width}x{height}+{x}+{y}")


def build_aurora_image(width: int, height: int, calm: bool = True) -> Image.Image:
    """Render the generated 极光冰川 OpenAI mesh-gradient family."""

    width, height = max(2, width), max(2, height)
    scale = min(1.0, 1200.0 / max(width, height))
    work_size = (max(2, int(width * scale)), max(2, int(height * scale)))
    base = Image.new("RGB", work_size, "#D8F0F4")
    layers = [
        ((1.13, 0.27), (0.95, 0.60), "#DDB6FA", 180),
        ((0.49, 0.07), (0.73, 1.25), "#A8A0EF", 150),
        ((0.45, 0.76), (0.68, 1.13), "#B8E1F4", 180),
        ((0.23, 0.91), (1.15, 1.29), "#86D5F4", 145),
        ((0.14, 0.67), (0.52, 0.74), "#D5B7F3", 130),
        ((0.02, 0.04), (0.64, 0.86), "#8FE0DA", 150),
    ]
    for (cx, cy), (rx, ry), color, alpha in layers:
        overlay = Image.new("RGBA", work_size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        w, h = work_size
        box = (
            int((cx - rx / 2) * w), int((cy - ry / 2) * h),
            int((cx + rx / 2) * w), int((cy + ry / 2) * h),
        )
        draw.ellipse(box, fill=(*Image.new("RGB", (1, 1), color).getpixel((0, 0)), alpha))
        overlay = overlay.filter(ImageFilter.GaussianBlur(max(28, int(min(w, h) * 0.11))))
        base = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    if calm:
        veil = Image.new("RGBA", work_size, (250, 253, 255, 62))
        base = Image.alpha_composite(base.convert("RGBA"), veil).convert("RGB")
    if work_size != (width, height):
        base = base.resize((width, height), Image.Resampling.LANCZOS)
    return base


class AuroraBackdrop(tk.Canvas):
    def __init__(self, master, calm=True, **kwargs):
        super().__init__(master, highlightthickness=0, bd=0, **kwargs)
        self.calm = calm
        self._photo = None
        self._render_job = None
        self.bind("<Configure>", self._schedule_render)

    def _schedule_render(self, _event=None):
        if self._render_job:
            self.after_cancel(self._render_job)
        self._render_job = self.after(90, self._render)

    def _render(self):
        self._render_job = None
        width, height = self.winfo_width(), self.winfo_height()
        if width < 2 or height < 2:
            return
        self._photo = ImageTk.PhotoImage(build_aurora_image(width, height, self.calm))
        self.delete("aurora-bg")
        self.create_image(0, 0, image=self._photo, anchor="nw", tags="aurora-bg")
        self.tag_lower("aurora-bg")


class RoundedButton(tk.Canvas):
    def __init__(
        self,
        master,
        text: str,
        command: Optional[Callable] = None,
        width: int = 150,
        height: int = 42,
        style: str = "secondary",
        font=None,
        **kwargs,
    ):
        resolved_font = font or FONTS["body_bold"]
        try:
            button_font = tkfont.Font(master=master, font=resolved_font)
            text_width = button_font.measure(text)
            text_height = button_font.metrics("linespace")
        except tk.TclError:
            text_width = len(text) * 10
            text_height = 20
        requested_width = max(width, text_width + 34)
        requested_height = max(height, text_height + 14)
        super().__init__(
            master, width=requested_width, height=requested_height, highlightthickness=0,
            bd=0, cursor="hand2", bg=kwargs.pop("bg", master.cget("bg")), **kwargs
        )
        self.text = text
        self.command = command
        self.base_width = width
        self.button_width = requested_width
        self.base_height = height
        self.button_height = requested_height
        self.style = style
        self.font = resolved_font
        self.enabled = True
        self.hovered = False
        self.configure(takefocus=True)
        self.bind("<Configure>", self._resize)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._invoke)
        self.bind("<ButtonPress-1>", lambda _e: self._draw(pressed=True))
        self.bind("<Return>", self._invoke)
        self.bind("<space>", self._invoke)
        self._draw()

    def _resize(self, event):
        width = max(2, int(event.width))
        height = max(2, int(event.height))
        if width != self.button_width or height != self.button_height:
            self.button_width = width
            self.button_height = height
            self._draw()

    def _palette(self, pressed=False):
        if not self.enabled:
            return "#E7ECF1", "#A4ADBA", "#E7ECF1"
        if self.style == "primary":
            fill = COLORS["primary_active"] if pressed else (
                COLORS["primary_hover"] if self.hovered else COLORS["primary"]
            )
            return fill, COLORS["text_white"], fill
        if self.style == "danger":
            fill = "#B94250" if self.hovered else COLORS["error"]
            return fill, COLORS["text_white"], fill
        if self.style == "success":
            fill = "#258667" if self.hovered else COLORS["success"]
            return fill, COLORS["text_white"], fill
        fill = "#EEF2F7" if self.hovered else COLORS["surface"]
        return fill, COLORS["text_primary"], COLORS["border"]

    def _draw(self, pressed=False):
        self.delete("all")
        fill, foreground, outline = self._palette(pressed)
        rounded_rectangle(
            self, 1, 1, self.button_width - 1, self.button_height - 1,
            radius=13, fill=fill, outline=outline, width=1
        )
        self.create_text(
            self.button_width / 2, self.button_height / 2,
            text=self.text, fill=foreground, font=self.font
        )

    def _enter(self, _event):
        self.hovered = True
        self._draw()

    def _leave(self, _event):
        self.hovered = False
        self._draw()

    def _invoke(self, _event):
        self._draw()
        if self.enabled and self.command:
            self.command()
        return "break"

    def set_enabled(self, enabled: bool):
        self.enabled = bool(enabled)
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self._draw()

    def set_text(self, text: str):
        self.text = text
        try:
            measured = tkfont.Font(master=self, font=self.font).measure(text) + 34
        except tk.TclError:
            measured = len(text) * 10 + 34
        requested = max(self.base_width, measured)
        self.configure(width=requested)
        self.button_width = max(requested, self.winfo_width())
        self._draw()


class SegmentedControl(tk.Canvas):
    """SwiftUI-style mutually exclusive segmented control."""

    def __init__(
        self, master, options, selected, command=None,
        width=306, height=38, **kwargs
    ):
        self.variant = kwargs.pop("variant", "light")
        takefocus = kwargs.pop("takefocus", True)
        super().__init__(
            master, width=width, height=height, highlightthickness=0, bd=0,
            bg=kwargs.pop("bg", master.cget("bg")), cursor="hand2",
            takefocus=takefocus, **kwargs
        )
        self.options = list(options)
        self.selected = selected
        self.command = command
        self.control_width = width
        self.control_height = height
        self.hover_index = None
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Left>", lambda _event: self._step_selection(-1))
        self.bind("<Right>", lambda _event: self._step_selection(1))
        self.bind("<Return>", self._activate_focused)
        self.bind("<space>", self._activate_focused)
        self.bind("<FocusIn>", lambda _event: self._draw())
        self.bind("<FocusOut>", lambda _event: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        dark = self.variant == "dark"
        surface = "#151C28" if dark else COLORS["surface_alt"]
        outline = "#344158" if dark else COLORS["border"]
        rounded_rectangle(
            self, 1, 1, self.control_width - 1, self.control_height - 1,
            10 if dark else 12, fill=surface, outline=outline, width=1
        )
        segment_width = self.control_width / max(1, len(self.options))
        for index, (value, label) in enumerate(self.options):
            x1 = index * segment_width + 3
            x2 = (index + 1) * segment_width - 3
            active = value == self.selected
            if active:
                rounded_rectangle(
                    self, x1, 4, x2, self.control_height - 4, 9,
                    fill="#2D3B50" if dark else COLORS["primary"],
                    outline="#2D3B50" if dark else COLORS["primary"]
                )
                if dark:
                    self.create_line(
                        x1 + 9, self.control_height - 5,
                        x2 - 9, self.control_height - 5,
                        fill="#7CD7D4", width=2,
                    )
            elif self.hover_index == index:
                rounded_rectangle(
                    self, x1, 4, x2, self.control_height - 4, 9,
                    fill="#222E40" if dark else COLORS["surface"],
                    outline="#222E40" if dark else COLORS["surface"]
                )
            self.create_text(
                (x1 + x2) / 2, self.control_height / 2,
                text=label, font=FONTS["small"],
                fill=(
                    "#F3F7FB" if dark and active else
                    "#9DA9BC" if dark else
                    COLORS["text_white"] if active else COLORS["text_secondary"]
                )
            )
        if self.focus_get() == self:
            rounded_rectangle(
                self, 2, 2, self.control_width - 2, self.control_height - 2,
                9 if dark else 11, fill="", outline="#7CD7D4", width=1,
            )

    def _motion(self, event):
        index = min(len(self.options) - 1, int(event.x / (self.control_width / len(self.options))))
        if index != self.hover_index:
            self.hover_index = index
            self._draw()

    def _leave(self, _event):
        self.hover_index = None
        self._draw()

    def _click(self, event):
        index = min(len(self.options) - 1, max(0, int(event.x / (self.control_width / len(self.options)))))
        value = self.options[index][0]
        if value != self.selected:
            self.selected = value
            self._draw()
            if self.command:
                self.command(value)

    def _step_selection(self, direction):
        if not self.options:
            return "break"
        values = [value for value, _label in self.options]
        try:
            index = values.index(self.selected)
        except ValueError:
            index = 0
        index = (index + direction) % len(values)
        self.selected = values[index]
        self._draw()
        if self.command:
            self.command(self.selected)
        return "break"

    def _activate_focused(self, _event=None):
        if self.command:
            self.command(self.selected)
        return "break"

    def set_selected(self, value):
        self.selected = value
        self._draw()

    def set_options(self, options):
        """Refresh localized labels without replacing the control."""
        self.options = list(options)
        if self.options and self.selected not in {value for value, _label in self.options}:
            self.selected = self.options[0][0]
        self._draw()


def create_modern_button(parent, text, command=None, style="primary", **kwargs):
    return RoundedButton(parent, text, command, style=style, **kwargs)


def apply_button_hover(_button, _style="primary"):
    # RoundedButton owns its animation; retained for older extension modules.
    return None


def apply_all_styles() -> ttk.Style:
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(
        "Aurora.Treeview", background=COLORS["surface"],
        fieldbackground=COLORS["surface"], foreground=COLORS["text_primary"],
        borderwidth=0, rowheight=34, font=FONTS["body"]
    )
    style.configure(
        "Aurora.Treeview.Heading", background=COLORS["surface_alt"],
        foreground=COLORS["text_secondary"], relief="flat",
        borderwidth=0, padding=(10, 9), font=FONTS["small"]
    )
    style.map(
        "Aurora.Treeview", background=[("selected", COLORS["row_selected"])],
        foreground=[("selected", COLORS["text_primary"])]
    )
    style.map(
        "Aurora.Treeview.Heading", background=[("active", COLORS["primary_light"])]
    )
    style.configure(
        "Aurora.Vertical.TScrollbar", troughcolor=COLORS["surface"],
        background="#C7D2DE", borderwidth=0, arrowsize=0
    )
    style.configure(
        "Aurora.Horizontal.TProgressbar", troughcolor=COLORS["surface_alt"],
        background=COLORS["primary"], borderwidth=0, thickness=5
    )
    style.configure(
        "Aurora.TNotebook", background=COLORS["surface"], borderwidth=0
    )
    style.configure(
        "Aurora.TNotebook.Tab", background=COLORS["surface_alt"],
        foreground=COLORS["text_secondary"], padding=(16, 9), borderwidth=0,
        font=FONTS["body"]
    )
    style.map(
        "Aurora.TNotebook.Tab",
        background=[("selected", COLORS["surface"]), ("active", COLORS["primary_light"])],
        foreground=[("selected", COLORS["primary"])]
    )
    for orientation in ("Vertical", "Horizontal"):
        style.configure(
            f"Preview.{orientation}.TScrollbar", troughcolor="#151C28",
            background="#46546B", borderwidth=0, arrowsize=0,
        )
    return style


def show_dialog(
    parent: tk.Misc,
    title: str,
    message: str,
    kind: str = "info",
    confirm: bool = False,
) -> bool:
    """Content-sized modal whose action row remains visible at every DPI."""

    result = {"value": False}
    window = tk.Toplevel(parent)
    window.withdraw()
    window.title(title)
    window.configure(bg=COLORS["surface"])
    window.resizable(False, False)
    window.transient(parent.winfo_toplevel())
    accent = {"error": COLORS["error"], "warning": COLORS["warning"]}.get(
        kind, COLORS["primary"]
    )
    tk.Frame(window, height=5, bg=accent).pack(fill="x")
    body = tk.Frame(window, bg=COLORS["surface"], padx=30, pady=24)
    body.pack(fill="both", expand=True)
    body.grid_columnconfigure(0, weight=1)
    body.grid_rowconfigure(1, weight=1)
    tk.Label(
        body, text=title, bg=COLORS["surface"], fg=COLORS["text_primary"],
        font=FONTS["title"], anchor="w"
    ).grid(row=0, column=0, sticky="ew")
    message_label = tk.Label(
        body, text=message, bg=COLORS["surface"], fg=COLORS["text_secondary"],
        font=FONTS["body"], justify="left", anchor="nw", wraplength=520
    )
    message_label.grid(row=1, column=0, sticky="nsew", pady=(11, 20))
    actions = tk.Frame(body, bg=COLORS["surface"])
    actions.grid(row=2, column=0, sticky="ew")

    def close(value):
        result["value"] = value
        window.destroy()

    from UI.I18n import tr
    if confirm:
        cancel = RoundedButton(
            actions, tr("cancel"), lambda: close(False), 96, 40, "secondary"
        )
        cancel.pack(side="right", padx=(8, 0))
    okay = RoundedButton(
        actions, tr("confirm") if confirm else tr("ack"),
        lambda: close(True), 96, 40, "primary"
    )
    okay.pack(side="right")

    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    width = max(360, min(600, screen_width - 48))
    message_label.configure(wraplength=max(320, width - 60))
    window.update_idletasks()
    requested_height = max(220, window.winfo_reqheight() + 8)
    maximum_height = max(300, screen_height - 96)
    height = min(requested_height, maximum_height)

    if requested_height > maximum_height:
        message_label.grid_remove()
        message_area = tk.Frame(body, bg=COLORS["surface"])
        message_area.grid(row=1, column=0, sticky="nsew", pady=(11, 20))
        message_area.grid_columnconfigure(0, weight=1)
        message_area.grid_rowconfigure(0, weight=1)
        message_text = tk.Text(
            message_area, wrap="word", relief="flat", bd=0,
            highlightthickness=0, bg=COLORS["surface"],
            fg=COLORS["text_secondary"], font=FONTS["body"],
            padx=0, pady=0, cursor="arrow",
        )
        message_text.insert("1.0", message)
        message_text.configure(state="disabled")
        message_scroll = ttk.Scrollbar(
            message_area, orient="vertical", command=message_text.yview,
            style="Aurora.Vertical.TScrollbar",
        )
        message_text.configure(yscrollcommand=message_scroll.set)
        message_text.grid(row=0, column=0, sticky="nsew")
        message_scroll.grid(row=0, column=1, sticky="ns", padx=(10, 0))
        window.resizable(True, True)
        window.minsize(min(500, width), 300)

    center_window(window, width, height)
    set_rounded_window(window)
    window.protocol("WM_DELETE_WINDOW", lambda: close(False))
    window.bind("<Escape>", lambda _event: close(False))
    window.bind("<Return>", lambda _event: close(True))
    window.deiconify()
    window.update_idletasks()
    window.lift()
    okay.focus_set()
    window.grab_set()
    window.wait_window()
    return result["value"]
