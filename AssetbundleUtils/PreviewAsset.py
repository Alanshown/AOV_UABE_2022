from PIL import Image

"""
這是所有 檢視資產 邏輯集
"""

# These global variables are assumed to be defined in the main script
# and will be accessed and modified here.
env_list= None  # UnityPy environment, to be set from the main script
indexFile = 0
# 檢視 assets
def preview_assets(path_id):
    global env_list, indexFile
    try:
        for obj in env_list[indexFile].objects:
            if str(obj.path_id) == path_id:
                try:
                    data = obj.read()
                    if obj.type.name.lower() in ["texture2d", "sprite"]:
                        # 检查data是否有image属性（NodeHelper对象可能没有）
                        if hasattr(data, 'image'):
                            img = data.image
                            # 确保图片有效
                            if img and hasattr(img, 'size') and img.size[0] > 0 and img.size[1] > 0:
                                return img
                        return None
                    elif obj.type.name.lower() == "mesh":
                        # 检查data是否有export方法
                        if hasattr(data, 'export'):
                            try:
                                obj_data = data.export()
                                if obj_data and len(obj_data) > 0:
                                    return obj_data
                            except Exception as mesh_err:
                                print(f"Mesh export error: {mesh_err}")
                        return None
                except Exception as e:
                    print(f"Preview error for {path_id}: {e}")
                    return None
    except Exception as e:
        print(f"Preview assets error: {e}")
    return None


def resize_image(image, max_size=300):
    """调整图片大小以适应预览区域"""
    width, height = image.size
    # 防止除零错误
    if width <= 0 or height <= 0:
        return image
    scale = min(max_size / width, max_size / height)
    if scale < 1:  # 只缩小不放大
        new_size = (int(width * scale), int(height * scale))
        return image.resize(new_size, Image.Resampling.LANCZOS)
    return image
