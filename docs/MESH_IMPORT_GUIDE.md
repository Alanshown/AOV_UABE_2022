# Mesh / Dump 导入简明教程

## 原 OBJ 回导

1. 用当前源码打开原始 AssetBundle，选择目标 Mesh，导出 OBJ。
2. 第一次测试不要经过 Blender，不修改 OBJ，直接对同一 Mesh 使用“导入替换 Mesh”。
3. 保存重建 AB 到独立输出目录。选择普通 Save 会自动选择已识别的原始指纹格式；“指纹重建”是显式第三指纹输出，不必对所有包使用。
4. 重新打开输出 AB 检查 PathID 和模型，再进行实际游戏验证。预览成功只代表几何可读取，不能代替游戏的蒙皮、剔除和依赖验证。

如果仅“原包不编辑 → 保存”就不能在游戏使用，应先检查包格式、依赖、实际替换目标和游戏版本，暂时不要调整 Blender。

## Blender / 第三方 OBJ

- 原模型编辑应保留目标坐标空间与原骨架对应关系。整体平移且拓扑保持时会保留顶点对应关系。
- OBJ 没有骨架信息。工具保留目标 AB 的 materials / skeleton / bind poses，第三方 OBJ 不会创建一套新骨架或把 MTL 直接变成 Unity 材质。
- UV/法线存在时会导入；没有 UV 时优先保留原 UV；不同 group 不能靠同一个材质名自动混成一个 submesh。
- 修改或新增顶点时优先保留、映射所有原扩展 channel。显示“空间近邻映射”的结果需要实际检查姿态，不能理解成全自动高质量绑骨。
- 材质槽不足、不可恢复的 skin、VariableBoneCountWeights 的不支持拓扑修改，会明确报错而非生成一个看似成功的损坏包。
- 支持的 OBJ 直接回导不要求 Unity Editor；需要新骨架、重新 rig 或游戏特定复杂动画时，OBJ 替换本身不够。

## 导出目录工程后编辑

“导出目录工程”包含当前已导入但尚未保存的内存修改。编辑 mesh 子目录的 OBJ 后使用“目录重建”，OBJ 修改现在会实际应用。保留对应 RAW/manifest/backup 文件；不要只交付孤立 OBJ 当作完整 AB。

## Dump 编辑

1. 选择资产，点击“导出可编辑 Dump”，保存完整 `.dump.json`。
2. 编辑 `tree` 内允许的值。不要改 `$schema` / `target`，不要从右侧截断或 hex 显示文本拼造 JSON。
3. `{"$bytes":"..."}` 保存 base64 字节，`{"$tuple":[...]}` 保存 map pair 等 tuple；不要把它们直接替换成普通文本或数组。
4. 对同一资产点击“导入 JSON Dump”。类型、PathID、Unity 版本、TypeTree 签名与字段结构必须匹配。
5. 导入成功后仍需“保存重建 AB”。新增不存在的 local PathID/外部 FileID 会被拒绝。

这是本工具自己的无损 JSON Dump 格式，不声称兼容其他 UABE 软件的文本 Dump。

## 模拟器 / 无 OpenGL 环境

取消顶部“启用预览”，设置会保留。关闭后停止预览进程，仍可用资产列表、Dump、导入/导出和 Save/Rebuild，不需要为了编辑强制运行 3D 预览。

## 运行与检查

Python 建议 3.10 以上（numpy 2.x 等依赖不支持 README 旧的 Python 3.7 声明）。Tkinter 是 Python/系统组件，不是 `pip install tkinter`。Linux 若缺少 Tkinter，安装系统对应的 Tk 包；Windows 使用包含 Tcl/Tk 的 Python 安装。

```sh
python -m pip install -r requirements.txt
python main.py
python -m pip install pytest
python -m pytest -q
```

目前发布页 EXE 与当前源代码功能存在差异。请保留可复现信息：源码 commit 或 EXE 版本、游戏版本、原 AB/依赖、目标 Mesh PathID、原 OBJ、导入结果及所用保存模式。
