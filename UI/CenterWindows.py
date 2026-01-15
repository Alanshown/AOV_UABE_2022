# 讓視窗置中
def center_window(win, offset_y):
    win.update_idletasks()  # 更新視窗資訊
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    window_width = win.winfo_width()
    window_height = win.winfo_height()
    
    x = (screen_width - window_width) // 2
    y = (screen_height - window_height) // 2 - offset_y  # 上移 offset_y 像素
    
    win.geometry(f"{window_width}x{window_height}+{x}+{y}")
