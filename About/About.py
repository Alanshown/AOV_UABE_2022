import tkinter as tk
from tkinter import Toplevel
from UI.CenterWindows import center_window
from Config import Config  # 引入 Config 模組

about_window = None  # 全域變數，避免重複開啟視窗


def About():
    global about_window
    if about_window and about_window.winfo_exists():  # 確保視窗唯一
        about_window.lift()  # 提升視窗到最前
        return
    
    # 讀取 Config 模組的語言設定
    lang, lang_code = Config.reload_config()  # 假設 reload_config 會返回當前語言和語言代碼

    about_window = Toplevel()
    about_window.title(lang["about"])
    about_window.geometry(lang["geometry_about"])

    # 禁止最小化和放大
    about_window.resizable(False, False)  
    about_window.attributes('-toolwindow', True)  

    # **強制最上層**
    about_window.attributes('-topmost', True)  

    # 設定為模態視窗（阻止主視窗操作）
    about_window.grab_set()  
    about_window.transient()  

    # 多语言About内容
    about_content = {
        "zh-cn": {
            "title": "AOV_UABE",
            "version": "版本号：2.0.0",
            "desc": "基于UnityPy_AOV项目开发的GUI工具，可以用于打包/解包最新的Unity2022版本AssetBundle文件",
            "author": "作者：ShownAlan"
        },
        "zh-tw": {
            "title": "AOV_UABE",
            "version": "版本號：2.0.0",
            "desc": "基於UnityPy_AOV項目開發的GUI工具，可以用於打包/解包最新的Unity2022版本AssetBundle文件",
            "author": "作者：ShownAlan"
        },
        "en": {
            "title": "AOV_UABE",
            "version": "Version: 2.0.0",
            "desc": "A GUI tool based on UnityPy_AOV project for packing/unpacking the latest Unity2022 AssetBundle files",
            "author": "Author: ShownAlan"
        },
        "vn": {
            "title": "AOV_UABE",
            "version": "Phiên bản: 2.0.0",
            "desc": "Công cụ GUI dựa trên dự án UnityPy_AOV để đóng gói/giải nén các tệp AssetBundle Unity2022 mới nhất",
            "author": "Tác giả: ShownAlan"
        }
    }
    
    # 获取当前语言的内容
    content = about_content.get(lang_code, about_content["en"])
    
    # 設計 About 視窗 UI
    title_label = tk.Label(about_window, text=content["title"], font=("Microsoft YaHei", 23, "bold"))
    title_label.grid(row=0, column=0, padx=20, pady=10, sticky="w")
    
    version_label = tk.Label(about_window, text=content["version"], font=("Microsoft YaHei", 13))
    version_label.grid(row=0, column=1, padx=20, pady=10, sticky="w")
    
    desc_label = tk.Label(
        about_window,
        text=content["desc"],
        wraplength=460,
        justify="left",
        font=("Microsoft YaHei", 12)
    )
    desc_label.grid(row=2, column=0, columnspan=2, padx=20, pady=10, sticky="w")

    author_label = tk.Label(about_window, text=content["author"], font=("Microsoft YaHei", 13))
    author_label.grid(row=3, column=0, padx=20, sticky="w")

    # 設定關閉時的行為
    about_window.protocol("WM_DELETE_WINDOW", on_close)

    # 監聽 main 視窗被點擊時的行為
    about_window.bind("<FocusOut>", on_main_click)

    # 視窗畫面置中
    about_window.update()  # 讓 Tkinter 先計算出視窗大小
    center_window(about_window, 0)

    # 延遲焦點設置
    about_window.after(1, force_focus)  # 1 毫秒後強制焦點

def on_main_click(event=None):
    """ 當試圖點擊 main 視窗時，讓 About 閃爍並發出提示音 """
    if about_window:
        about_window.bell()  # 發出聲音
        force_focus()

def force_focus():
    """ 強制 About 視窗保持在最上層並奪回焦點 """
    if about_window:
        about_window.lift()
        about_window.attributes('-topmost', True)  # 讓 About 始終置頂
        about_window.after(50, lambda: about_window.focus_force())  # 確保焦點回到 About

def on_close():
    global about_window
    if about_window:
        about_window.destroy()
        about_window = None  # 清除變數，確保下次能正確開啟
