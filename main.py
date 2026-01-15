# -*- coding: utf-8 -*-
"""
AssetBundle Tool - 现代化主界面
"""
import tkinter as tk
from tkinter import Menu, filedialog, messagebox
from Config import Config
from About import About
from UI.CenterWindows import center_window
from UI.ModernTheme import COLORS, FONTS, apply_button_hover, apply_all_styles
from AssetbundleUtils.AssetsList import list_assets_window
import os, shutil, sys

def get_resource_path(filename):
    """获取资源文件路径，兼容PyInstaller打包"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)
    return os.path.abspath(filename)

Selected_Dir = None  # Save Selected Directory
Selected_File = None  # 選擇的檔案

PickDir_Title = ""  # DirectoryPicker 標題
PickFile_Title = ""  # FilePicker 標題

SaveFile_Title = ""  # 存檔 標題


# 即時更新語言設定
def reload_config():
    global PickFile_Title, PickDir_Title, SaveFile_Title
    lang, lang_code = Config.reload_config()

    root.title(lang["title"])
    menu.entryconfig(1, label=lang["file"])
    menu.entryconfig(2, label=lang["help"])
    menu.entryconfig(3, label=lang["lang"])

    subMenu.entryconfig(0, label=lang["open_file"])
    subMenu.entryconfig(1, label=lang["open_dir"])
    subMenu.entryconfig(2, label=lang["save"])
    subMenu.entryconfig(3, label=lang["exit"])

    helpMenu.entryconfig(0, label=lang["about"])

    langMenu.entryconfig(0, label=lang["zh_tw"])
    langMenu.entryconfig(1, label=lang["zh_cn"])
    langMenu.entryconfig(2, label=lang["english"])
    langMenu.entryconfig(3, label=lang["Vietnamese"])

    # 更新主視窗中的文字，**如果沒有選擇檔案，才會顯示 lang["No_File"]**
    if Selected_File is None:
        set_file_text(lang["No_File"])
    Info_BTN.config(text=lang["Info"])

    # FilePicker 標題
    PickFile_Title = lang["Pick_File"]
    PickDir_Title = lang["Pick_Dir"]
    # SaveFile 標題
    SaveFile_Title = lang["Save_File"]


# 變更語言後，立即更新 UI
def setting_languages(new_lang_code):
    Config.setting_languages(new_lang_code)  # 更新語言設定
    reload_config()  # 重新加載並更新 UI


# 打開檔案選擇器並設置檔案類型為 .assetbundle
def open_file():
    global Selected_File, Selected_Dir
    Selected_Dir = None
    file_path = filedialog.askopenfilename(
        title=PickFile_Title, filetypes=[("AssetBundle files", "*.assetbundle")]
    )
    if file_path:
        Selected_File = file_path
        file = os.path.basename(file_path)  # 只取檔名+副檔名
        set_file_text(file)  # 更新標籤顯示檔案名稱


# select folder
def open_dir():
    global Selected_Dir, Selected_File
    Selected_File = None
    file_path = filedialog.askdirectory(title=PickDir_Title)
    if file_path:
        Selected_Dir = file_path
        file = os.path.basename(file_path)  # 只取檔名+副檔名
        set_file_text(file)  # 更新標籤顯示檔案名稱


# 設定 Label 文字
def set_file_text(file_name):
    label_file.config(state="normal")  # 解除不可編輯狀態
    label_file.delete("1.0", tk.END)  # 清除舊內容
    label_file.insert("1.0", file_name)  # 插入新內容
    label_file.config(state="disabled")  # 設回不可編輯狀態


# 開啟 Assets List
def Get_Assests():
    if Selected_File:
        list_assets_window(Selected_File, IsInputDir=False)
    elif Selected_Dir:
        list_assets_window(Selected_Dir, IsInputDir=True)
    else:
        root.bell()


def show_dialog(title, message):
    """顯示一個簡單的對話框"""
    messagebox.showinfo(title, message)


# 保存功能说明：
# 保存功能已移至 AssetsList.py 的 save_and_exit() 函数中
# 用户在资产列表窗口中点击"保存并退出"按钮时，会直接选择输出目录并保存
# 此处保留菜单项用于提示用户
def save_assetbundle(lang):
    show_dialog(lang.get("Dialog_Title", "Info"), 
                lang.get("Dialog_Message_Save_Hint", "请先打开AssetBundle文件，在资产列表窗口中进行修改后点击\"保存并退出\"按钮保存。"))


def on_close():
    root.quit()  # 關閉 Tk() 主視窗
    try:
        shutil.rmtree("./AssetbundleUtils/tmp")
    except:
        print


# 初始化 GUI
root = tk.Tk()
lang, lang_code = Config.reload_config()

# 应用现代化样式
apply_all_styles()

root.title(lang["title"])
root.geometry("420x100")
root.configure(bg=COLORS["bg_light"])

# 禁止最小化和放大
root.resizable(False, False)
try:
    root.iconbitmap(get_resource_path("icon.ico"))
except:
    pass  # 图标加载失败时忽略

# 现代化菜单
menu = Menu(root, bg=COLORS["bg_white"], fg=COLORS["text_primary"], 
            activebackground=COLORS["primary"], activeforeground=COLORS["text_white"],
            font=FONTS["body"])
root.config(menu=menu)

# 文件菜单
subMenu = Menu(menu, tearoff=0, bg=COLORS["bg_white"], fg=COLORS["text_primary"],
               activebackground=COLORS["primary_light"], activeforeground=COLORS["text_primary"],
               font=FONTS["body"])
menu.add_cascade(label=lang["file"], menu=subMenu)
subMenu.add_command(label=lang["open_file"], command=open_file)
subMenu.add_command(label=lang["open_dir"], command=open_dir)
subMenu.add_separator()
subMenu.add_command(label=lang["save"], command=lambda: save_assetbundle(lang))
subMenu.add_separator()
subMenu.add_command(label=lang["exit"], command=on_close)

# 帮助菜单
helpMenu = Menu(menu, tearoff=0, bg=COLORS["bg_white"], fg=COLORS["text_primary"],
                activebackground=COLORS["primary_light"], activeforeground=COLORS["text_primary"],
                font=FONTS["body"])
menu.add_cascade(label=lang["help"], menu=helpMenu)
helpMenu.add_command(label=lang["about"], command=lambda: About.About())

# 语言菜单
langMenu = Menu(menu, tearoff=0, bg=COLORS["bg_white"], fg=COLORS["text_primary"],
                activebackground=COLORS["primary_light"], activeforeground=COLORS["text_primary"],
                font=FONTS["body"])
menu.add_cascade(label=lang["lang"], menu=langMenu)
langMenu.add_command(label=lang["zh_tw"], command=lambda: setting_languages("zh-tw"))
langMenu.add_command(label=lang["zh_cn"], command=lambda: setting_languages("zh-cn"))
langMenu.add_command(label=lang["english"], command=lambda: setting_languages("en"))
langMenu.add_command(label=lang["Vietnamese"], command=lambda: setting_languages("vn"))

PickFile_Title = lang["Pick_File"]
PickDir_Title = lang["Pick_Dir"]
SaveFile_Title = lang["Save_File"]

# 现代化主框架
root_frame = tk.Frame(root, padx=16, pady=16, bg=COLORS["bg_light"])
root_frame.pack(fill=tk.BOTH, expand=True)

# 文件名称显示区域
file_frame = tk.Frame(root_frame, bg=COLORS["bg_light"])
file_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

# 现代化文本框
text_frame = tk.Frame(file_frame, bg=COLORS["border"], bd=1, relief="solid")
text_frame.pack(fill=tk.X, expand=True)

label_file = tk.Text(
    text_frame, 
    font=FONTS["body"], 
    height=1, 
    wrap="none", 
    width=28,
    bg=COLORS["bg_white"],
    fg=COLORS["text_primary"],
    relief="flat",
    padx=8,
    pady=6
)
label_file.insert("1.0", lang["No_File"])
label_file.config(state="disabled")
label_file.pack(fill=tk.X, expand=True)

# 现代化Info按钮
Info_BTN = tk.Button(
    root_frame,
    text=lang["Info"],
    font=FONTS["body_bold"],
    width=10,
    bg=COLORS["primary"],
    fg=COLORS["text_white"],
    activebackground=COLORS["primary_hover"],
    activeforeground=COLORS["text_white"],
    relief="flat",
    cursor="hand2",
    command=Get_Assests,
)
Info_BTN.pack(side=tk.LEFT, padx=(12, 0), ipady=6)
apply_button_hover(Info_BTN, "primary")

root.protocol("WM_DELETE_WINDOW", on_close)

# 視窗畫面置中
root.update()  # 讓 Tkinter 先計算出視窗大小
center_window(root, 100)

# 启动后自动显示关于弹窗
root.after(500, lambda: About.About())

root.mainloop()
