# -*- coding: utf-8 -*-
"""
现代化AssetBundle资产列表窗口
"""
import tkinter as tk
from tkinter import ttk, Toplevel
from PIL import ImageTk
import os
import sys
from UI.CenterWindows import center_window
from UI.ModernTheme import (
    COLORS, FONTS, BUTTON_STYLES, 
    apply_button_hover, create_modern_button, apply_all_styles, HINTS
)
from Config import Config
import AssetbundleUtils.PreviewAsset
import AssetbundleUtils.AssetOperations
from AssetbundleUtils.OBJ_Viewer import OBJViewer
from AssetbundleUtils import UnityPy_AOV

def get_resource_path(filename):
    """获取资源文件路径，兼容PyInstaller打包"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.abspath(filename)


list_window = None  # 避免重複開啟視窗
global current_sort_col, ascending, small_hint_text_label
env_list = []  # 全域變數儲存 UnityPy 環境
current_sort_col = "Name"
ascending = True
selected_items = []  # 用於記錄使用者選取的項目
modified_assets = {}  # 用來記錄已修改的項目
isDir = False
indexFile = 0
list_path = []

def list_assets_window(input_path , IsInputDir = False):
    global list_window,name_file_entry, name_entry, path_id_entry, type_entry, env, preview_label, obj_viewer , isDir, small_hint_text_label
    isDir = IsInputDir
    if list_window and list_window.winfo_exists():
        list_window.lift()
        return
    
    lang, lang_code = Config.reload_config()
    
    # 应用现代化样式
    apply_all_styles()

    list_window = Toplevel()
    list_window.grab_set()
    list_window.transient()
    list_window.minsize(width=1000, height=600)
    list_window.title(lang["List_Window_title"])
    list_window.geometry("1200x650")
    try:
        list_window.iconbitmap(get_resource_path("icon.ico"))
    except:
        pass
    list_window.configure(bg=COLORS["bg_light"])
    
    # 在Toplevel窗口上应用样式
    apply_all_styles()

    # 主框架 - 现代化背景
    frame = tk.Frame(list_window, bg=COLORS["bg_light"])
    frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    columns = ["Name", "Type", "Path ID", "Size", "Modified"]
    column_widths = {"Name": 280, "Type": 140, "Path ID": 180, "Size": 70, "Modified": 80}

    if isDir:
        columns.insert(0, "File")
        column_widths["File"] = 60

    # 创建列表容器框架
    tree_frame = tk.Frame(frame, bg=COLORS["bg_white"], relief="flat", bd=0)
    tree_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    
    # 现代化Treeview
    tree = ttk.Treeview(tree_frame, columns=columns, show="headings", style="Modern.Treeview")

    for col in columns:
        tree.heading(col, text=col, command=lambda c=col: sort_column(tree, c))
        tree.column(col, anchor="w", width=column_widths[col])
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    # 现代化滚动条
    scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview, style="Modern.Vertical.TScrollbar")
    tree.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    # 右侧信息面板 - 更宽以容纳更大的预览
    info_frame = tk.Frame(frame, width=350, bg=COLORS["bg_white"])
    info_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
    info_frame.pack_propagate(False)

    # 现代化标签页
    notebook = ttk.Notebook(info_frame, style="Modern.TNotebook")
    notebook.pack(fill=tk.BOTH, expand=True)

    info_tab = tk.Frame(notebook, bg=COLORS["bg_white"])
    preview_tab = tk.Frame(notebook, bg=COLORS["viewer_bg"])  # 预览页面使用深色背景
    notebook.add(info_tab, text=lang["Info_tab"])
    notebook.add(preview_tab, text=lang["Preview_tab"])

    # 现代化标签与输入框
    def create_label_entry(parent, label_text):
        label = tk.Label(
            parent, text=label_text, 
            font=FONTS["body_bold"], 
            bg=COLORS["bg_white"], 
            fg=COLORS["text_primary"]
        )
        label.pack(anchor="w", padx=16, pady=(12, 0))
        
        entry_frame = tk.Frame(parent, bg=COLORS["border"], bd=1, relief="solid")
        entry_frame.pack(fill="x", padx=16, pady=(4, 0))
        
        entry = tk.Text(
            entry_frame, height=1, width=30, 
            state="disabled", wrap="none", 
            font=FONTS["body"],
            bg=COLORS["bg_white"],
            fg=COLORS["text_primary"],
            relief="flat",
            padx=8, pady=4
        )
        entry.pack(fill="x")
        return entry

    name_file_entry = create_label_entry(info_tab, lang["File_Name"])
    name_entry = create_label_entry(info_tab, lang["Name"])
    path_id_entry = create_label_entry(info_tab, lang["PathID"])
    type_entry = create_label_entry(info_tab, lang["Type"])


    # 预览标签 - 用于图片预览
    preview_label = tk.Label(
        preview_tab, 
        font=FONTS["body"], 
        bg=COLORS["viewer_bg"],
        fg=COLORS["text_white"]
    )
    
    # 3D模型预览器
    obj_viewer = OBJViewer(preview_tab)
    
    # 初始化时隐藏obj_viewer，显示preview_label
    preview_label.place(relx=0.5, rely=0.5, anchor="center")
    obj_viewer.place_forget()

    # 获取当前语言的操作提示
    hint_text = HINTS["mesh_viewer"].get(lang_code, HINTS["mesh_viewer"]["en"])

    # 现代化操作提示标签
    small_hint_text_label = tk.Label(
        preview_tab,
        text=hint_text,
        font=FONTS["small"],
        bg=COLORS["viewer_bg"],
        fg=COLORS["text_muted"],
        justify="left",
        padx=8,
        pady=4
    )

    # ========== 现代化按钮区域 ==========
    
    # 按钮样式配置
    btn_style = {
        "font": FONTS["body"],
        "bg": COLORS["bg_white"],
        "fg": COLORS["text_primary"],
        "activebackground": COLORS["bg_hover"],
        "activeforeground": COLORS["text_primary"],
        "relief": "solid",
        "bd": 1,
        "cursor": "hand2",
        "padx": 12,
        "pady": 6,
    }

    # 第一排按钮 - Raw操作
    btn_frame = tk.Frame(info_tab, bg=COLORS["bg_white"])
    btn_frame.pack(fill="x", pady=(16, 4), padx=16)
    
    export_raw_btn = tk.Button(btn_frame, text=lang["Export_Raw"], 
                               command=lambda: AssetbundleUtils.AssetOperations.export_raw(lang), **btn_style)
    import_raw_btn = tk.Button(btn_frame, text=lang["Import_Raw"], 
                               command=lambda: AssetbundleUtils.AssetOperations.import_raw(lang, tree), **btn_style)
    
    export_raw_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    import_raw_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
    apply_button_hover(export_raw_btn, "secondary")
    apply_button_hover(import_raw_btn, "secondary")
    
    btn_frame.columnconfigure(0, weight=1)
    btn_frame.columnconfigure(1, weight=1)
    
    # 第二排按钮 - Texture操作
    btn_frame2 = tk.Frame(info_tab, bg=COLORS["bg_white"])
    btn_frame2.pack(fill="x", pady=4, padx=16)
    
    export_texture_btn = tk.Button(btn_frame2, text=lang["Export_Texture"], 
                                   command=lambda: AssetbundleUtils.AssetOperations.export_texture(lang), **btn_style)
    import_texture_btn = tk.Button(btn_frame2, text=lang["Import_Texture"], 
                                   command=lambda: AssetbundleUtils.AssetOperations.import_texture(lang, tree), **btn_style)
    
    export_texture_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))
    import_texture_btn.grid(row=0, column=1, sticky="ew", padx=(4, 0))
    apply_button_hover(export_texture_btn, "secondary")
    apply_button_hover(import_texture_btn, "secondary")
    
    btn_frame2.columnconfigure(0, weight=1)
    btn_frame2.columnconfigure(1, weight=1)

    # 第三排按钮 - Mesh导出
    btn_frame3 = tk.Frame(info_tab, bg=COLORS["bg_white"])
    btn_frame3.pack(fill="x", pady=4, padx=16)
    
    export_mesh_btn = tk.Button(btn_frame3, text=lang["Export_Mesh"], 
                                command=lambda: AssetbundleUtils.AssetOperations.export_mesh(lang), **btn_style)
    export_mesh_btn.pack(fill="x")
    apply_button_hover(export_mesh_btn, "secondary")
    
    # 分隔线
    separator = tk.Frame(info_tab, height=1, bg=COLORS["border"])
    separator.pack(fill="x", pady=16, padx=16)

    # 保存退出按钮 - 主要操作使用主色调
    btn_frame4 = tk.Frame(info_tab, bg=COLORS["bg_white"])
    btn_frame4.pack(fill="x", pady=(0, 16), padx=16)
    
    # 主要保存按钮 - 使用主色调
    save_btn = tk.Button(
        btn_frame4, 
        text=lang["Save"], 
        command=lambda: save_and_exit(),
        font=FONTS["body_bold"],
        bg=COLORS["primary"],
        fg=COLORS["text_white"],
        activebackground=COLORS["primary_hover"],
        activeforeground=COLORS["text_white"],
        relief="flat",
        cursor="hand2",
        padx=16,
        pady=8,
    )
    save_btn.pack(fill="x")
    apply_button_hover(save_btn, "primary")

    # 现代化进度条
    progress_var = tk.IntVar()
    progress_bar = ttk.Progressbar(
        list_window, 
        variable=progress_var, 
        maximum=100, 
        mode='determinate',
        style="Modern.Horizontal.TProgressbar"
    )
    progress_bar.pack(fill=tk.X, padx=8, pady=(0, 8))

    # 綁定 Tab 分頁切換事件
    notebook.bind("<<NotebookTabChanged>>", on_tab_selected)

    # 綁定 Ctrl + A 鍵盤事件
    tree.bind('<Control-a>', lambda event, t=tree: select_all(t))
    tree.bind("<ButtonRelease-1>", lambda event, t=tree: on_item_selected(lang, t, notebook))  # 綁定點擊事件

    list_window.protocol("WM_DELETE_WINDOW", on_close)
    list_window.update()
    center_window(list_window, 0)

    list_window.after(100, lambda: list_assets(input_path, tree, progress_var, progress_bar))

def list_assets(input_path, tree, progress_var, progress_bar):
    global env_list , list_path
    list_path = [ os.path.join(input_path, file_name) for file_name in os.listdir(input_path) if os.path.isfile(os.path.join(input_path, file_name) ) and "assetbundle" in file_name.lower()] if isDir  else [ input_path ] 
    assets = []
    env_list = []
    for j , path in enumerate(list_path, 0):
        env = UnityPy_AOV.load(path)
        total = 0
        total= len(env.objects)
        env_list.append(env)
        for i, obj in enumerate(env.objects, 1):
            try:
                data = obj.read()
                name = getattr(data, 'm_Name', 'Unnamed asset') or "Unnamed"
            except Exception as e:
                # 读取失败时使用默认名称
                name = f"[Error] {obj.type.name}_{obj.path_id}"
            if isDir:
                assets.append((j , name, obj.type.name, obj.path_id, obj.byte_size))
            else:
                assets.append((name, obj.type.name, obj.path_id, obj.byte_size))
            progress_var.set(int((i / total) * 100))
            tree.update_idletasks()

    # 更新 AssetOperations 中的 env
    AssetbundleUtils.AssetOperations.env_list = env_list
    AssetbundleUtils.AssetOperations.list_window = list_window
    # 更新 PreviewAsset 中的 env
    AssetbundleUtils.PreviewAsset.env_list = env_list
    AssetbundleUtils.AssetOperations.PathIDIndex = 3 if isDir else 2
    
    assets.sort(key=lambda x: x[0])
    update_treeview(tree, assets)
    progress_var.set(100)
    progress_bar.pack_forget()

def update_treeview(tree, assets):
    for row in tree.get_children():
        tree.delete(row)
    
    for asset in assets:
        tree.insert("", "end", values=asset)
    
    apply_row_colors(tree)

def apply_row_colors(tree):
    """应用现代化行颜色"""
    for index, item in enumerate(tree.get_children()):
        color = "row_even" if index % 2 == 0 else "row_odd"
        tree.item(item, tags=(color,))
    # 使用现代化配色
    tree.tag_configure("row_even", background=COLORS["row_even"])
    tree.tag_configure("row_odd", background=COLORS["row_odd"])

# 避免切至 Info 點選Mesh 切回 Preview 沒顯示成功
def on_tab_selected(event):
    selected_tab = event.widget.index(event.widget.select())
    if selected_tab == 1:  # 1 表示 Preview 分頁
        if selected_items and selected_items[-1][1].lower() == "mesh":
            preview_label.place_forget()  # 隐藏图片预览
            obj_viewer.place(relx=0, rely=0, relwidth=1, relheight=1)  # 填充整个区域
            if len(obj_viewer.vertices) > 0:
                obj_viewer.after(50, obj_viewer.redraw)
        else:
            obj_viewer.place_forget()  # 隐藏3D预览
            preview_label.place(relx=0.5, rely=0.5, anchor="center")  # 顯示 preview_label

# 因為沒有內建 ctrl + a，自己寫一個
def select_all(tree):
    global selected_items
    selected_items = []  # 清空舊選擇
    # 全選並收集資料
    for item in tree.get_children():
        tree.selection_add(item)  # 選擇項目
        item_values = tree.item(item, "values")  # 獲取項目的資料
        if item_values:
            selected_items.append(item_values)  # 收集資料

    # 在這裡更新 AssetOperations 中的 selected_items
    AssetbundleUtils.AssetOperations.selected_items = selected_items

# 點擊事件
def on_item_selected(lang, tree, notebook):
    """當使用者點選項目時，取得該項目的資料"""
    global selected_items, preview_label, obj_viewer
    selected_items = []  # 清空舊選擇
    for item in tree.selection():
        item_values = tree.item(item, "values")
        if item_values:
            selected_items.append(item_values)
    
    # 在這裡更新 AssetOperations 中的 selected_items
    AssetbundleUtils.AssetOperations.selected_items = selected_items

    if selected_items:
        if isDir:
            i , name, asset_type, path_id, *_= selected_items[-1]
            i = int(i)
        else:
            name, asset_type, path_id, *_ = selected_items[-1]
            i = 0
        
        AssetbundleUtils.AssetOperations.indexFile = i
        AssetbundleUtils.PreviewAsset.indexFile = i
        update_entry(name_file_entry, os.path.basename( list_path[i] ))
        update_entry(name_entry , name)
        update_entry(path_id_entry, path_id)
        update_entry(type_entry, asset_type)

        if asset_type.lower() in ["texture2d", "sprite"]:
            image = AssetbundleUtils.PreviewAsset.preview_assets(path_id)
            if image:
                image = AssetbundleUtils.PreviewAsset.resize_image(image)
                img = ImageTk.PhotoImage(image)
                preview_label.config(image=img)
                preview_label.image = img
                obj_viewer.place_forget()  # 隐藏3D预览
                preview_label.place(relx=0.5, rely=0.5, anchor="center")  # 置中顯示
                small_hint_text_label.place_forget()  # 隱藏提示文字
            else:
                preview_label.config(image="", text=lang["Preview_not_available"])
                obj_viewer.place_forget()  # 隐藏3D预览
                preview_label.place(relx=0.5, rely=0.5, anchor="center")
                small_hint_text_label.place_forget()  # 隱藏提示文字
        elif asset_type.lower() == "mesh":
            obj_data = AssetbundleUtils.PreviewAsset.preview_assets(path_id)
            # 无论是否在Preview标签页，都加载Mesh数据
            if obj_data:
                small_hint_text_label.place(relx=0.0, rely=1.0, anchor="sw", x=10, y=-10)  # 顯示提示文字 # 左下角
                preview_label.place_forget()  # 隐藏图片预览
                # 使用place填充整个Preview区域
                obj_viewer.place(relx=0, rely=0, relwidth=1, relheight=1)
                obj_viewer.load_obj_data(obj_data)
            else:
                preview_label.config(image="", text=lang["Preview_not_available"])
                preview_label.place(relx=0.5, rely=0.5, anchor="center")
                obj_viewer.place_forget()  # 隐藏3D预览
                small_hint_text_label.place_forget()  # 隱藏提示文字

        else:
            preview_label.config(image="", text=lang["Preview_not_available"])
            obj_viewer.place_forget()  # 隐藏3D预览
            preview_label.place(relx=0.5, rely=0.5, anchor="center")  # 置中顯示
            small_hint_text_label.place_forget()  # 隱藏提示文字

    else:
        # 如果沒有選擇項目，也要更新 AssetOperations 中的 selected_items
        AssetbundleUtils.AssetOperations.selected_items = selected_items

# 變更右側文字
def update_entry(entry, value):
    entry.config(state="normal")
    entry.delete("1.0", tk.END)
    entry.insert("1.0", value)
    entry.config(state="disabled")

def sort_column(tree, col):
    global ascending, current_sort_col
    items = [(tree.set(k, col), k) for k in tree.get_children("")]
    
    reverse_order = False
    if col == current_sort_col:
        ascending = not ascending
    else:
        ascending = True

    reverse_order = not ascending
    current_sort_col = col

    def sort_key(val):
        try:
            return float(val[0]) if col in ["Path ID", "Size"] else val[0].lower()
        except ValueError:
            return val[0].lower()

    items.sort(reverse=reverse_order, key=lambda x: sort_key(x))

    for index, (_, k) in enumerate(items):
        tree.move(k, "", index)

    apply_row_colors(tree)

def on_close():
    global list_window, selected_items
    selected_items = []  # 清空舊選擇
    
    if list_window:
        list_window.destroy()
        list_window = None

def save_and_exit():
    global list_window
    from tkinter import filedialog, messagebox
    lang, _ = Config.reload_config()
    
    # 让用户选择输出目录
    output_dir = filedialog.askdirectory(title=lang.get("Pick_Output_Folder", "Select Output Folder"))
    if not output_dir:
        return  # 用户取消
    
    try:
        for i, path in enumerate(list_path, 0):
            name = os.path.basename(list_path[i])
            env = env_list[i]
            output_path = os.path.join(output_dir, name)
            
            # 使用lz4压缩保存，与unity2022_unitypy相同的机制
            if hasattr(env, 'file'):
                data = env.file.save("lz4")
            else:
                # 多文件情况，取第一个文件
                data = list(env.files.values())[0].save("lz4")
            
            with open(output_path, "wb") as f:
                f.write(data)
        
        messagebox.showinfo(
            lang.get("Dialog_Title", "Info"), 
            lang.get("Save_file_to", "Saved to:") + "\n" + output_dir
        )
    except Exception as e:
        messagebox.showerror("Error", f"Save failed: {str(e)}")
        return
    
    if list_window:
        list_window.destroy()
        list_window = None


