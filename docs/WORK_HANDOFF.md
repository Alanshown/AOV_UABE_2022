# Work 模式后续维护提示词

继续维护 Alanshown/AOV_UABE_2022，读取仓库 AGENTS.md（若存在）、docs/issue-audit-2026-10-01.md、docs/MESH_IMPORT_GUIDE.md 以及所有 Issue/评论和 PR #3。不要把 Issue 已关闭、预览能画出模型或旧 EXE 发布信息当作修复证据。

本次已建立本地维护分支 `work/fix-mesh-roundtrip-and-issue-feedback-20261001`。先确认 git status/log 和已有本地 commit，不要重新实现已完成的修复，不要覆盖用户修改。以真实代码核对主界面 → import → dirty/preview → Save/Rebuild 的调用链。

最新 Issue #4 作者最后承诺的重点是改进 Mesh 直接导入后的游戏显示；同时包含用户的 Dump 编辑导入/导出和模拟器减少预览负担需求。已有维护实现覆盖顶点/skin/扩展 channel 对应、AOV fallback m_IsInUse、骨与 renderer bounds、SM4 bytes、目录 OBJ 应用、未保存工程快照、修改预览同步、JSON Dump 与预览关闭、保存原子替换及保护；自动回归为 40 项。

下一步先读取最新审计中的边界，并获取/使用授权可访问的失败 AB 与成功案例原 AB、依赖、OBJ、游戏版本和 EXE/build 来源。没有样本也可继续独立代码维护，但绝不伪造“游戏中已修复”的结论。按原包 no-op 保存、原 OBJ 无修改回导、Blender 编辑、第三方 OBJ、作者成功案例五组矩阵验证，保留输入/输出 hash、fingerprint、PathID/CAB/container、skin/bind poses、submesh/material slots、bounds 和运行日志。优先查找回归失败的具体断点，不要笼统要求用户删除 MTL。

任何新增修复必须包含能捕获行为错误的最小回归；不要为过测试删掉验证，也不要把 VariableBoneCountWeights、完整特效包回导或复杂动画标为已支持。EffectPipeline 当前是预览/导出功能；如果需要新增完整包导入，应作为明确独立范围设计，不能靠按钮占位实现。

完成修改后运行适当测试、语法和 diff 检查，更新审计/教程和已验证/待验证清单，然后在云端本地 git commit。可以在必要时自己建立新的本地维护分支，但优先沿用当前维护分支。提交报告应写明分支名、commit SHA、修改与测试、真实游戏/Windows GUI 验证状态。

**提交权限：当前只授权本地修改、分支和 commit。不要 push、创建远程分支/PR、关闭 Issue 或合并 main。等待 Alan 明确发送 push 指令后，才把全部已有维护 commit 推送到 Work 自建的新远程分支，分支名可沿用本地维护分支；绝不提交到 main。此后仍不自动合并，Alan 后续手动合并。**
