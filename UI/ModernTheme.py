# -*- coding: utf-8 -*-
"""
现代化GUI主题配置
提供统一的颜色方案、样式和动态效果
"""

# 主色调 - 深蓝渐变风格
COLORS = {
    # 主色调
    "primary": "#2563EB",           # 主蓝色
    "primary_hover": "#1D4ED8",     # 主蓝色悬停
    "primary_active": "#1E40AF",    # 主蓝色按下
    "primary_light": "#DBEAFE",     # 浅蓝背景
    
    # 次要色调
    "secondary": "#64748B",         # 次要灰色
    "secondary_hover": "#475569",   # 次要悬停
    
    # 成功/警告/错误
    "success": "#10B981",           # 绿色
    "warning": "#F59E0B",           # 橙色
    "error": "#EF4444",             # 红色
    
    # 背景色
    "bg_dark": "#1E293B",           # 深色背景
    "bg_medium": "#334155",         # 中等背景
    "bg_light": "#F8FAFC",          # 浅色背景
    "bg_white": "#FFFFFF",          # 白色背景
    "bg_card": "#FFFFFF",           # 卡片背景
    "bg_hover": "#F1F5F9",          # 悬停背景
    
    # 边框色
    "border": "#E2E8F0",            # 边框色
    "border_focus": "#2563EB",      # 聚焦边框
    "border_light": "#F1F5F9",      # 浅边框
    
    # 文字色 - 保持黑色系
    "text_primary": "#1E293B",      # 主文字(深灰黑)
    "text_secondary": "#64748B",    # 次要文字
    "text_muted": "#94A3B8",        # 淡化文字
    "text_white": "#FFFFFF",        # 白色文字
    "text_black": "#000000",        # 纯黑文字
    
    # 列表交替色
    "row_even": "#F8FAFC",          # 偶数行
    "row_odd": "#FFFFFF",           # 奇数行
    "row_selected": "#DBEAFE",      # 选中行
    "row_hover": "#E0F2FE",         # 悬停行
    
    # 3D预览器背景
    "viewer_bg": "#374151",         # 3D查看器背景
    "viewer_grid": "#4B5563",       # 网格线颜色
    
    # 渐变色
    "gradient_start": "#2563EB",    # 渐变起始
    "gradient_end": "#7C3AED",      # 渐变结束
}

# 字体配置
FONTS = {
    "title": ("Microsoft YaHei UI", 14, "bold"),
    "heading": ("Microsoft YaHei UI", 12, "bold"),
    "body": ("Microsoft YaHei UI", 10),
    "body_bold": ("Microsoft YaHei UI", 10, "bold"),
    "small": ("Microsoft YaHei UI", 9),
    "tiny": ("Microsoft YaHei UI", 8),
    "mono": ("Consolas", 10),
}

# 按钮样式
BUTTON_STYLES = {
    "primary": {
        "bg": COLORS["primary"],
        "fg": COLORS["text_white"],
        "activebackground": COLORS["primary_hover"],
        "activeforeground": COLORS["text_white"],
        "relief": "flat",
        "cursor": "hand2",
        "font": FONTS["body_bold"],
        "padx": 16,
        "pady": 8,
    },
    "secondary": {
        "bg": COLORS["bg_white"],
        "fg": COLORS["text_primary"],
        "activebackground": COLORS["bg_hover"],
        "activeforeground": COLORS["text_primary"],
        "relief": "solid",
        "cursor": "hand2",
        "font": FONTS["body"],
        "padx": 12,
        "pady": 6,
        "bd": 1,
    },
    "success": {
        "bg": COLORS["success"],
        "fg": COLORS["text_white"],
        "activebackground": "#059669",
        "activeforeground": COLORS["text_white"],
        "relief": "flat",
        "cursor": "hand2",
        "font": FONTS["body_bold"],
        "padx": 16,
        "pady": 8,
    },
    "danger": {
        "bg": COLORS["error"],
        "fg": COLORS["text_white"],
        "activebackground": "#DC2626",
        "activeforeground": COLORS["text_white"],
        "relief": "flat",
        "cursor": "hand2",
        "font": FONTS["body_bold"],
        "padx": 16,
        "pady": 8,
    },
}

# 输入框样式
ENTRY_STYLE = {
    "bg": COLORS["bg_white"],
    "fg": COLORS["text_primary"],
    "insertbackground": COLORS["primary"],
    "relief": "solid",
    "bd": 1,
    "font": FONTS["body"],
}

# 标签样式
LABEL_STYLE = {
    "bg": COLORS["bg_light"],
    "fg": COLORS["text_primary"],
    "font": FONTS["body"],
}

# 框架样式
FRAME_STYLE = {
    "bg": COLORS["bg_light"],
}

# 圆角半径(用于Canvas绘制)
RADIUS = {
    "small": 4,
    "medium": 8,
    "large": 12,
}

# 阴影效果(用于Canvas)
SHADOW = {
    "color": "#00000015",
    "offset": 2,
    "blur": 4,
}


def apply_button_hover(button, style="primary"):
    """为按钮添加悬停效果"""
    if style == "primary":
        normal_bg = COLORS["primary"]
        hover_bg = COLORS["primary_hover"]
        active_bg = COLORS["primary_active"]
    elif style == "secondary":
        normal_bg = COLORS["bg_white"]
        hover_bg = COLORS["bg_hover"]
        active_bg = COLORS["border"]
    elif style == "success":
        normal_bg = COLORS["success"]
        hover_bg = "#059669"
        active_bg = "#047857"
    else:
        normal_bg = COLORS["secondary"]
        hover_bg = COLORS["secondary_hover"]
        active_bg = "#374151"
    
    def on_enter(e):
        button.config(bg=hover_bg)
    
    def on_leave(e):
        button.config(bg=normal_bg)
    
    def on_press(e):
        button.config(bg=active_bg)
    
    def on_release(e):
        button.config(bg=hover_bg)
    
    button.bind("<Enter>", on_enter)
    button.bind("<Leave>", on_leave)
    button.bind("<ButtonPress-1>", on_press)
    button.bind("<ButtonRelease-1>", on_release)


def create_modern_button(parent, text, command=None, style="primary", **kwargs):
    """创建现代化按钮"""
    import tkinter as tk
    
    btn_style = BUTTON_STYLES.get(style, BUTTON_STYLES["primary"]).copy()
    btn_style.update(kwargs)
    
    button = tk.Button(
        parent,
        text=text,
        command=command,
        **btn_style
    )
    
    apply_button_hover(button, style)
    return button


def configure_treeview_style():
    """配置Treeview样式"""
    from tkinter import ttk
    
    style = ttk.Style()
    
    # 配置Treeview整体样式
    style.configure(
        "Modern.Treeview",
        background=COLORS["bg_white"],
        foreground=COLORS["text_primary"],
        fieldbackground=COLORS["bg_white"],
        rowheight=32,
        font=FONTS["body"],
    )
    
    # 配置Treeview标题样式
    style.configure(
        "Modern.Treeview.Heading",
        background=COLORS["bg_light"],
        foreground=COLORS["text_primary"],
        font=FONTS["body_bold"],
        relief="flat",
        padding=(8, 8),
    )
    
    # 配置选中状态
    style.map(
        "Modern.Treeview",
        background=[("selected", COLORS["row_selected"])],
        foreground=[("selected", COLORS["text_primary"])],
    )
    
    # 配置标题悬停
    style.map(
        "Modern.Treeview.Heading",
        background=[("active", COLORS["bg_hover"])],
    )
    
    return style


def configure_notebook_style():
    """配置Notebook标签页样式"""
    from tkinter import ttk
    
    style = ttk.Style()
    
    style.configure(
        "Modern.TNotebook",
        background=COLORS["bg_light"],
        borderwidth=0,
    )
    
    style.configure(
        "Modern.TNotebook.Tab",
        background=COLORS["bg_light"],
        foreground=COLORS["text_primary"],
        padding=(16, 8),
        font=FONTS["body"],
    )
    
    style.map(
        "Modern.TNotebook.Tab",
        background=[("selected", COLORS["bg_white"]), ("active", COLORS["bg_hover"])],
        foreground=[("selected", COLORS["primary"])],
    )
    
    return style


def configure_scrollbar_style():
    """配置滚动条样式"""
    from tkinter import ttk
    
    style = ttk.Style()
    
    style.configure(
        "Modern.Vertical.TScrollbar",
        background=COLORS["bg_light"],
        troughcolor=COLORS["bg_white"],
        borderwidth=0,
        arrowsize=14,
    )
    
    style.map(
        "Modern.Vertical.TScrollbar",
        background=[("active", COLORS["secondary"]), ("pressed", COLORS["primary"])],
    )
    
    return style


def configure_progressbar_style():
    """配置进度条样式"""
    from tkinter import ttk
    
    style = ttk.Style()
    
    style.configure(
        "Modern.Horizontal.TProgressbar",
        background=COLORS["primary"],
        troughcolor=COLORS["bg_light"],
        borderwidth=0,
        thickness=6,
    )
    
    return style


def apply_all_styles():
    """应用所有现代化样式"""
    try:
        from tkinter import ttk
        # 确保使用默认主题以避免打包后的兼容性问题
        style = ttk.Style()
        available_themes = style.theme_names()
        # 优先使用clam主题，它在所有平台上都有较好的兼容性
        if 'clam' in available_themes:
            style.theme_use('clam')
        elif 'default' in available_themes:
            style.theme_use('default')
        
        configure_treeview_style()
        configure_notebook_style()
        configure_scrollbar_style()
        configure_progressbar_style()
    except Exception as e:
        print(f"样式应用错误: {e}")


# 操作提示文本
HINTS = {
    "mesh_viewer": {
        "zh-cn": "🖱️ 左键拖动: 旋转 | Ctrl+左键: 平移 | 滚轮: 缩放 | Ctrl+W: 切换线框",
        "zh-tw": "🖱️ 左鍵拖動: 旋轉 | Ctrl+左鍵: 平移 | 滾輪: 縮放 | Ctrl+W: 切換線框",
        "en": "🖱️ Left Drag: Rotate | Ctrl+Left: Pan | Wheel: Zoom | Ctrl+W: Wireframe",
        "vn": "🖱️ Kéo trái: Xoay | Ctrl+Trái: Di chuyển | Cuộn: Thu phóng | Ctrl+W: Khung dây",
    }
}
