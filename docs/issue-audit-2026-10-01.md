# AOV_UABE_2022：Issue 审计与维护记录

审计日期：2026-10-01。仓库：Alanshown/AOV_UABE_2022。

基线为 main 的 `d11788b91c967ee5db1a5f00bc7b9e5b07b068f0`；本地维护分支为 `work/fix-mesh-roundtrip-and-issue-feedback-20261001`。本次按用户授权实现修复并本地提交，**没有 push、创建远程 PR、关闭 Issue 或合并 main**。

## 1. Issue 与作者承诺

- [Issue #2](https://github.com/Alanshown/AOV_UABE_2022/issues/2)：Blender 修改后、甚至工具导出的 OBJ 不修改直接回导，都出现“预览正常、游戏模型不可见”。后续评论还询问第三方 OBJ/MTL、是否需要 Unity Editor、是否有教程。
- [Issue #2 的可用案例](https://github.com/Alanshown/AOV_UABE_2022/issues/2#issuecomment-5042617067)：作者报告替换联体武器 Mesh 在游戏中可用，并提供视频链接和截图。作者的其他回复建议合并网格、对齐位置/T-pose、移除外部材质引用。
- [最新 Issue #4](https://github.com/Alanshown/AOV_UABE_2022/issues/4)：请求 Dump 导入/导出和编辑，重复反馈回导 Mesh 不可见，并表示在模拟器使用，希望减少预览负担。关闭状态不能作为代码已修好的证据。
- [作者在最新 Issue 最后的升级承诺](https://github.com/Alanshown/AOV_UABE_2022/issues/4#issuecomment-5771990554)：重访 Mesh 导入流程；下一版本应允许直接导入并得到预期游戏效果，减少为了正确显示而必须额外在 Blender 编辑的情况。该承诺没有要求引入第三方新骨架或完整特效包导入。
- [PR #3](https://github.com/Alanshown/AOV_UABE_2022/pull/3)：已合并；引入 MeshImport、AnimationPipeline、EffectPipeline 和现代资产窗口。

**版本差异**：`latest2.0` 标签指向 `0b70a38a308fb84fb952415a30e264a9128d66c7`。该标签的源码只有 Mesh 导出，没有 MeshImport 或 Mesh 导入入口；该 Release 的公开 EXE 于 2026-07-20 发布，而 PR #3 于 2026-08-02 合并。不能把用户使用的 EXE、标签源码、当前主分支直接当作同一版本。本次未反编译或运行该 EXE。

## 2. 主界面入口与完整调用链

### 共同入口

`main.py:main()` → `Launcher(root)` → `Launcher._build_shell()` 绑定打开按钮 → `Launcher.open_assets()` → `AssetsList.list_assets_window()` → `AssetBrowser.__init__()` → `_build_ui()` / `_build_action_drawer()`。

加载：`_start_loading()` → `_load_coordinator()` → `_load_one()` → `UnityPy_AOV.load()` → `Environment.load_file()` → `BundleFile.__init__()/read_fs()` → `SerializedFile` → `ObjectReader`；事件经 `_drain_events()` → `_finish_loading()`。

索引：`_finish_loading()` → `_start_animation_index()` → `AnimationProjectIndex(self.paths, self.env_list)` → `animation_index_ready` → `_start_effect_index()` → `EffectProjectIndex(project=self.animation_project)`。主界面和两个 Pipeline 共用当前内存中的对象。

### Mesh

入口：`_build_action_drawer()` 的 `import_mesh` → `AssetBrowser.import_mesh()` → 文件选择和确认 → `_submit_operation()` → `replace_mesh_from_obj()`。

底层：`parse_obj()` → `_vertex_mapping()` → `_patch_existing_vertex_streams()`，无法保留布局时才走 `_rebuild_vertex_stream()` → `_write_indices_and_submeshes()` → Mesh/bone bounds 更新 → `ObjectReader.save_typetree()` → `TypeTreeHelper.write_typetree()` → `ObjectReader.set_raw_data()` → `assets_file.mark_changed()` → `read(False)` / `validate_mesh_payload()`。

界面成功回调：`synchronize_mesh_renderer_bounds()` 的结果 → `_mark_modified()` 标记 Mesh 和变更的 renderer → `_refresh_relationships_after_import()` → `_request_preview()` → `_send_preview_request()` → `PreviewWorker.preview_worker()` → 读取替换后的 raw → Mesh 顶点预览 → `_apply_preview()` → `OBJViewer`。

### Animation

UI：`_build_action_drawer()` 绑定 `select_animation_model`、`export_animation_fbx`、`import_animation_fbx`。

导入：`import_animation()` → `_submit_operation()` → `replace_animation_from_fbx(project, file_index, PathID, model, fbx)` → ufbx 读取/采样 → 节点匹配和 Transform binding → 更新 dense clip → `save_typetree()` → 回读验证 → `_mark_modified(..., "Animation")` → 刷新关系和预览。

预览：`_request_preview()` → `best_model_for_animation()` / 手动选择的 model → `_send_preview_request(kind="animation")` → worker 的 `AnimationProjectIndex` → 应用全部当前修改 → `build_animation_preview_payload()` → viewer。

导出：`export_animation()` → `export_animation_fbx()` → FBX writer。此路径与 FBX **导入写回目标 AnimationClip** 是两条不同调用链。

### Effect

UI：`_build_action_drawer()` 绑定 `export_effect_package` → `export_effect()` → `export_effect_directory()` → 导出资源目录。GameObject 为特效预览/导出的入口。

预览：`_resolve_effect_for_asset()` → `_send_preview_request(kind="effect")` → worker 的 `EffectProjectIndex` → `build_effect_preview_payload()` → `_apply_preview()` → `EffectViewer`。

**EffectPipeline 的预览和导出确实接入了。没有 `import_effect` 或特效包反向导入函数，因此不能声称“完整特效包可编辑后回导”。** 现有 Raw、JSON Dump、纹理、Mesh、Animation 修改可以通过共同 Save 写回；EffectPipeline 自身的导出不会修改 bundle。本次没有虚构一个完整特效包导入功能。

### 两条保存路径

**界面 Save**：`save_bundles()` / `save_fingerprint_bundles()` → `_save_bundles_with_packer()` → 内部 `save_one()` → `_select_rebuild_packer()` → `BundleFile.save()` / `save_fs()` → 对 CAB 节点调用 `SerializedFile.save()` → `ObjectReader.write()` 写入替换后的 `.data` → 内存重载输出包 → inventory / 指纹头 / 修改对象 raw SHA256 / Mesh / Animation / texture 验证 → 同目录临时文件 → `os.replace()` → `save_done`。

**目录 Rebuild**：UI `rebuild_from_bundle_project()` → `rebuild_bundle_project()` → 备份包加载 → `_restore_resource_nodes()` / `_install_imported_types()` → `_apply_project_objects()` → v1 legacy 或 v2 dynamic 路径（RAW、PNG、JSON、现在也包括 OBJ）→ `_rebuild_stream_resources()` → catalog/preload/reference 校验 → `BundleFile.save()` → 内存重载与 Mesh payload 验证 → 原子写文件 → 更新 manifest。

**导出目录工程**：`export_bundle_projects()` → 有未保存修改时先序列化内存快照 → `export_bundle_project(source_bytes=...)`。修复前它只读磁盘旧文件，导致刚导入的数据没有进入工程目录。

## 3. 模型消失的断点与修复

| 断点 | 基线证据/风险 | 本次处理 |
|---|---|---|
| OBJ 顶点次序 | parser 按面首次出现顺序建立顶点；导出逆序面使未修改的三角形顶点从 `[0,1,2]` 变成 `[2,1,0]`，等数量分支却仍保留 `[0,1,2]` 的骨骼索引 | 按 OBJ 源 position 排序；重排/新增顶点时一并映射所有原顶点流 |
| 等顶点数≠同顶点对应 | 旧分支只比较顶点数，忽略颜色、UV1+、skin 与新 position 的对应关系；静态预览只画三角形不能发现 | 几何对应和 UV seam 区分；同拓扑的整体平移保留对应；空间近邻作为显式有提示的近似回退 |
| 拓扑变化丢扩展通道 | 原 compact rebuild 只保留 position/normal/tangent/UV0 与最多4骨权重 | 优先复制并重排原所有 stream，重新计算 stream offset/16-byte 对齐；packed-only 才 compact fallback；无法恢复 skin 时拒绝写入 |
| AOV Mesh TypeTree | 手写 Mesh parser 读取 `m_IsInUse`，stock TPK fallback 缺少该 UInt32，导致 TypeTree 与 runtime parser 布局不同 | 只修正没有嵌入 TypeTree 时的 AOV Mesh fallback；不改嵌入树，不污染 TPK cache；保留该字段 |
| renderer / bone bounds | 原导入只更新 Mesh local bounds，移动后 renderer/bone culling 范围可能过期；viewer 自动居中并不使用游戏剔除规则 | 用 bind pose 更新 bone-space bounds；扩展引用该 Mesh 的 SkinnedMeshRenderer bounds；renderer 一起 dirty/save |
| submesh / material slot | `g` 与 `usemtl` 混为一个 label，同名材质能合并不同 group；MTL 本身不带 Unity PPtr | 保留 group+material 分组，保留原 renderer 材质引用，超过可用材料槽时拒绝，不创建外部 MTL 依赖 |
| MeshStream 外置数据 | OBJ 不携带 skin 与 resS；重建后若旧 locator 与实际 buffer 冲突可能损坏 | 导入将解析后的完整顶点流内置，清空 m_StreamData locator；回归覆盖外置 resS 输入 |
| 压缩/形变 | 清空 packed mesh 却保留旧 compression 标志，或 reordered topology 保留索引相关 BlendShape | 压缩标志同步；不兼容 BlendShape 清除；VariableBoneCountWeights 的不支持拓扑修改明确拒绝并回滚 |
| Save 验证不足 | 仅“有顶点、有索引、能 export”不能证明 skin/stream 没损坏，且先覆盖目标再校验 | 校验有限数、有效 stream、skin/bone、submesh/bounds 和 raw SHA256；成功验证后原子替换输出 |
| 指纹重载 | SM4 库严格要求 bytes，读取器提供 memoryview 会失败 | 在 SM4 边界转 bytes；三种指纹格式合成包实际写入/重载测试 |
| 目录 OBJ 编辑 | 导出 manifest 将 OBJ 标成 editable，但 legacy/dynamic apply 均没有 Mesh OBJ 分支 | 两条路径均接入同一个 replace_mesh_from_obj；返回 edited_meshes |
| 未保存修改与预览 | effect/material worker 从磁盘建 cache，animation 只传所选 clip 的 raw，未传其他改过的 Mesh | revision 使 cache 失效；传输所有 dirty raw overrides；更新结构节点时重建 materialized model index |
| 导入与保存竞态 | 共享编辑对象可同时被操作线程修改并保存 | 统一 busy 状态阻止导入/导出/Save 操作重叠；异常回调恢复按钮 |

**根因结论的边界**：顶点/skin 错配、缺失 Rebuild OBJ 分支、TypeTree 布局差异等已由代码和回归明确证明。它们能解释为何几何预览正常而游戏动画/渲染失败，但没有失败 AB/游戏日志，不能确定某位 Issue 用户的唯一根因。包指纹、资源依赖、替换目标是否正确、骨架/坐标空间和游戏侧校验仍需实际样本区分。

## 4. 与作者可用案例的差异

| 维度 | 可用案例公开信息 | 失败反馈 | 目前能得出的结论 |
|---|---|---|---|
| 操作对象 | 原模型/联体武器，作者在 Blender 编辑并对齐 | 有原 OBJ 无修改回导，也有第三方模型 | 部分武器成功不能覆盖所有蒙皮模型 |
| skeleton/skin | 原始 AB 和权重统计没有随评论公开 | 失败对象的 bones、bind poses 和 vertex streams 未提供 | 是否单骨刚性绑定、是否多骨蒙皮未知；不能猜测 |
| submesh/material | 作者建议合并及移除 MTL；提供效果截图 | 第三方 OBJ 带 MTL，位置角度已调整仍失败 | 当前源码不加载 MTL 为 Unity 材质；删除 MTL 不是足够的根因诊断 |
| 保存/指纹 | 没有完整打包配置及输出文件 hash | 用户怀疑 Save/Rebuild | 必须先做不改动重建，区分 mesh 导入与 outer bundle 问题 |
| 软件版本 | Release/EXE 的 build commit 没有证据 | 使用 AOV_UABE_2022.exe | 当前源码、旧标签、EXE 不可直接等同 |
| 验证级别 | 作者报告游戏成功，附图片和视频链接 | 用户报告游戏不可见 | 本次读取了讨论，但没有获取并重放成功案例的原 AB、输出 AB 和游戏；不声称独立复验该案例 |

## 5. 最新 Issue 的功能落实

- 新增“导出可编辑 Dump” / “导入 JSON Dump”，三语言界面文案。完整 JSON 与只读/截断的显示 Dump 分离；bytes/memoryview 编码为 base64，map pair tuple 无损编码。
- 导入核验 schema、PathID、类型、Unity 版本、TypeTree 签名、字段/值类型；拒绝新增 dangling local PPtr 和不存在的外部 FileID；序列化失败恢复原 raw。更改名称后资产行/搜索索引随之更新。
- 新增“启用预览”设置，保存到 Settings.json。关闭后停止预览进程、不创建 OpenGL viewer，仍能查看 Dump、导入、Save/Rebuild。保留普通用户的预览功能。
- Raw Mesh 导入也增加结构验证和失败回滚。
- Animation FBX 写入失败回滚；重复骨名不再任意选第一个；包含材质、激活、PPtr 等非 Transform 曲线的 clip 拒绝被 transform-only replacement 悄悄清空。它还不是完整通用动画编辑器。
- 新教程见 [MESH_IMPORT_GUIDE.md](MESH_IMPORT_GUIDE.md)。提供文本操作说明，没有制作视频。

## 6. 最小回归方案与已执行结果

执行命令：`python -m pytest -q`。本次最终 **40 项通过**；另执行全部 Python 文件语法解析、`git diff --check`、主界面模块 import 检查。

自动回归使用人工生成的小型 AOV Mesh / SerializedFile / UnityFS 包，不分发游戏资产。重点覆盖：

1. 未修改导出 OBJ → import → 保存 → load：检查 PathID、顶点、索引、skin、UV1 和 m_IsInUse。
2. 顶点重排、同拓扑整体平移、增加顶点/三角形：skin 与全部原扩展 channel 对应，bone bounds 不过期。
3. 外置 resS Mesh → OBJ 回导：stream 内置与 locator 清空后，skin 和 raw 保存重载不变。
4. 原始/LZ4/三种 AOV 指纹写入重载：modified mesh raw 字节一致。
5. 目录工程：无编辑重建 raw 不变；OBJ 编辑真正应用；UI 未保存编辑进入导出工程快照。
6. Dump bytes/map tuple 往返、改名写回、错误目标/不完整字段/非法新引用拒绝。
7. UI 回调：Mesh/Dump dirty 标记和 Save，Save 失败不覆盖原文件、忙碌时 Save 不执行、关闭 preview 不调度渲染。
8. 非法数、损坏 RAW、导入校验失败、unsupported variable weights：原 raw 不被破坏。
9. 当前修改同时进入动画/特效的 preview index；Transform 变化刷新 materialized position；动画非 Transform 曲线保护。

**实际游戏验收仍需做的最小矩阵**：取得一组失败资产和一组作者成功资产，各保留原 AB/OBJ/依赖与游戏版本。

- A：原 AB 不修改、仅 Save/Rebuild → 游戏仍能显示（否则先查打包、依赖、替换目标）。
- B：原 OBJ 不修改回导 → Save → 游戏静止/待机/动作三种状态均显示。
- C：同模型平移或局部编辑 → 检查镜头远近、角色运动和 culling。
- D：第三方 OBJ → 保留目标 materials/skeleton → 验证材质槽、绑定姿态和动画；空间近邻映射不能保证艺术质量。
- E：成功案例按原流程重做，并对照失败样本的 bone数、streams、submesh、fingerprint、CAB/dependency/container。

本环境未运行 Windows EXE、没有可视化验收 Windows/OpenGL 界面，也未运行游戏。UI 自动检查调用真实浏览器操作方法，但使用对话框/控件 stub；因此不会把回调通过描述成 Windows GUI 视觉验收。依赖安装使用当前可用兼容包，不代表原旧版 requirements 的每个固定版本都在本环境复验过。

## 7. 仍存在的明确边界

- OBJ 不携带骨架、bind pose 或新蒙皮；第三方角色必须适配原骨架。近邻映射是启发式，不等于重新绑骨。
- VariableBoneCountWeights 的拓扑改动暂不实现，安全拒绝；完整特效包回导不存在；复杂非 Transform 动画仍需专门实现，不能静默丢曲线。
- 原有资产目录和部分索引以 bundle_index + PathID 定位；一个 AB 内多个 CAB 重复 PathID 的泛化支持未在本次验证，不把单 CAB 合成样本结论外推到该场景。
- 某些非三种已知指纹的加密存储仍使用既有 LZ4 回退，是否被具体游戏接受需 no-op 游戏验证。
- 本次不更改游戏校验/资源管理机制、不发布 EXE，不自动合并或关闭 Issue。
