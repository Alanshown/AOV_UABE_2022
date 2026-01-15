import json
import os
import sys

def get_resource_path(filename):
    """
    獲取檔案的實際路徑，適用於開發環境和 PyInstaller 打包後的 EXE。
    用于读取只读资源文件（如lang.json）
    """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, filename)  # PyInstaller 內部打包後的路徑
    return os.path.abspath(filename)  # 開發環境直接返回絕對路徑

def get_writable_path(filename):
    """
    获取可写文件路径，用于保存用户配置（如Settings.json）
    打包后保存在exe所在目录，开发环境保存在当前目录
    """
    if hasattr(sys, '_MEIPASS'):
        # 打包后，保存在exe所在的目录
        exe_dir = os.path.dirname(sys.executable)
        return os.path.join(exe_dir, filename)
    return os.path.abspath(filename)


# 讀取設定
def load_settings():
    # 先尝试从可写路径读取（用户自定义配置）
    settings_path = get_writable_path("Settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                return settings.get("Language", "en")  # 默认语言 en
        except json.JSONDecodeError:
            print("Settings.json 格式錯誤，使用預設值。")
    
    # 如果可写路径不存在，尝试从资源路径读取（默认配置）
    settings_path = get_resource_path("Settings.json")
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
                return settings.get("Language", "en")
        except json.JSONDecodeError:
            print("Settings.json 格式錯誤，使用預設值。")
    return "en"

# 讀取語言包
def load_language(lang_code):
    lang_path = get_resource_path("lang.json")
    if os.path.exists(lang_path):
        try:
            with open(lang_path, "r", encoding="utf-8") as f:
                lang_data = json.load(f)
                return lang_data.get(lang_code, lang_data.get("en", {}))  # 默认 en
        except json.JSONDecodeError:
            print("lang.json 格式錯誤，使用空字典。")
    return {}

# 變更語言設置
def setting_languages(new_lang_code):
    # 使用可写路径保存配置
    settings_path = get_writable_path("Settings.json")
    settings = {"Language": new_lang_code}
    
    # 如果文件已存在，先读取现有配置
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except json.JSONDecodeError:
            print("Settings.json 格式錯誤，將創建新文件。")
    
    # 更新语言设置
    settings["Language"] = new_lang_code
    
    # 写入配置文件
    try:
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
        print(f"語言已切換為: {new_lang_code}，配置已保存到: {settings_path}")
    except Exception as e:
        print(f"無法保存設置: {e}")

# 重新載入設定
def reload_config():
    lang_code = load_settings()
    lang = load_language(lang_code)
    return lang, lang_code
