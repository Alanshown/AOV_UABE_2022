# AOV_UABE_2022
🎮This is a GUI tool based on UnityPy that can be used to extract, preview, modify, and export Assetbundle files for Arena of Valor.🕹️
<div align="center">

# 🎮 UABE for Arena of Valor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Web Version](https://img.shields.io/badge/🌐_Web_Version-Online-brightgreen.svg)](http://ld.ymkeji.xyz/)

**[繁体中文](README.md)** | [English](README.en.md) | [Tiếng Việt](README.vi.md)

<img src="https://github.com/KennyYang0726/UABE_AOV/raw/refs/heads/main/icon.ico" width="128" alt="UABE AOV Logo"/>

### 🔧 专为《传说对决 / Arena of Valor》设计的 AssetBundle 编辑工具

*适用于 1.58 版本之前的游戏资源文件*

---

## 🌐 在线体验网页版

**无需下载，立即体验！ ** 我们提供了功能完整的网页版 UABE 工具：

### 🚀 [点击访问网页版 UABE](http://ld.ymkeji.xyz/)

**网页版特点：**
- ✨ 无需安装，浏览器直接使用
- 🔒 数据本地处理，保护隐私安全
- 📱 支持多平台（Windows / Mac / Linux）
- 🎯 功能与桌面版完全一致
- ⚡ 快速响应，操作流畅

> 💡 **提示**：网页版适合快速体验和轻量级操作，如需批量处理大量文件，建议下载桌面版。

---

[📥 下载桌面版](https://github.com/KennyYang0726/UABE_AOV/releases) | 

</div>

---

## 📋 目录

- [✨ 项目简介](#-项目简介)
- [🎯 核心功能](#-核心功能)
- [🖼️ 功能预览](#️-功能预览)
- [📦 安装指南](#-安装指南)
- [🚀 使用方式](#-使用方式)
- [🔍 功能详解](#-功能详解)
- [🛠️ 技术架构](#️-技术架构)
- [📂 项目结构](#-项目结构)
- [⚙️ 开发指南](#️-开发指南)
- [🤝 贡献指南](#-贡献指南)
- [📜 开源协议](#-开源协议)
- [🙏 致谢](#-致谢)

---

## ✨ 项目简介

**UABE for Arena of Valor** 是一款专为《传说对决》游戏资源文件设计的图形化编辑工具。本项目基于 [K0lb3](https://github.com/K0lb3) 的 **UnityPy** 框架，并整合了 **常暗踏影** 的魔改版本，添加了 AOV 专属的加解密流程支持。

### 🌟 主要特点

- 🎨 **现代化 UI 设计** - 采用 Tkinter 打造的直观图形界面
- 🔐 **AOV 专属加密支持** - 完美支持《传说对决》的资源加密格式
- 📁 **批量处理** - 支持单文件和整个目录的批量操作
- 🖼️ **多格式支持** - Raw、Texture2D、Mesh 等多种资源类型
- 🌍 **多语言界面** - 支持繁体中文、简体中文、英文、越南文
- 🎯 **精准编辑** - 可对资源进行精确的导出、导入和修改

---

## 🎯 核心功能

<table>
<thead>
<tr>
<th width="20%">功能模块</th>
<th width="40%">功能描述</th>
<th width="20%">支持格式</th>
<th width="20%">操作类型</th>
</tr>
</thead>
<tbody>
<tr>
<td><strong>📤 导出 Raw</strong></td>
<td>直接导出原始数据文件，保留完整的资源结构信息</td>
<td><code>.bytes</code></td>
<td>导出</td>
</tr>
<tr>
<td><strong>📥 导入 Raw</strong></td>
<td>导入修改后的原始数据，替换游戏资源（需确保类型匹配）</td>
<td><code>.bytes</code></td>
<td>导入</td>
</tr>
<tr>
<td><strong>🖼️ 导出图片</strong></td>
<td>将 Texture2D 资源导出为标准图片格式</td>
<td><code>.png</code></td>
<td>导出</td>
</tr>
<tr>
<td><strong>🎨 导入图片</strong></td>
<td>导入自定义图片替换游戏贴图（需保持尺寸一致）</td>
<td><code>.png</code> <code>.jpg</code></td>
<td>导入</td>
</tr>
<tr>
<td><strong>🗿 导出 Mesh</strong></td>
<td>将 3D 模型资源导出为 OBJ 格式，可用于 3D 建模软件</td>
<td><code>.obj</code></td>
<td>导出</td>
</tr>
<tr>
<td><strong>👁️ 资源预览</strong></td>
<td>实时预览图片和 3D 模型，支持 OpenGL 渲染</td>
<td>多种格式</td>
<td>查看</td>
</tr>
<tr>
<td><strong>💾 保存并退出</strong></td>
<td>将所有修改保存到新的 AssetBundle 文件</td>
<td><code>.assetbundle</code></td>
<td>保存</td>
</tr>
<tr>
<td><strong>📂 批量操作</strong></td>
<td>支持打开整个目录，批量处理多个 AssetBundle 文件</td>
<td>目录</td>
<td>批量</td>
</tr>
</tbody>
</table>



## 🚀 使用方式

### 基本操作流程

```mermaid
graph LR
    A[啟動程序] --> B[選擇文件/目錄]
    B --> C[查看資源列表]
    C --> D[選擇資源]
    D --> E{操作類型}
    E -->|導出| F[選擇保存位置]
    E -->|導入| G[選擇替換文件]
    E -->|預覽| H[查看資源]
    F --> I[完成]
    G --> J[保存並退出]
    H --> C
    J --> I
```

### 詳細步驟

#### 1️⃣ 啟動程序

- 執行 `python main.py`（主文件）

#### 2️⃣ 打開資源文件

**方式 A：打開單個文件**
- 點擊菜單欄 `文件` → `打開文件`
- 選擇 `.assetbundle` 文件

**方式 B：打開整個目錄**
- 點擊菜單欄 `文件` → `打開目錄`
- 選擇包含多個 `.assetbundle` 文件的文件夾

#### 3️⃣ 瀏覽資源列表

- 點擊主界面的 `Info` 按鈕
- 在彈出的資源列表窗口中查看所有資源
- 可按名稱、類型、大小等排序

#### 4️⃣ 執行操作

**導出資源**
1. 在列表中選中目標資源
2. 點擊右側對應的導出按鈕
3. 選擇保存位置

**導入資源**
1. 在列表中選中目標資源
2. 點擊右側對應的導入按鈕
3. 選擇要導入的文件
4. 確認替換

**預覽資源**
- 在列表中選中資源
- 右側面板自動顯示預覽
- 對於 3D 模型，可使用鼠標旋轉查看

#### 5️⃣ 保存修改

- 完成所有修改後，點擊 `保存並退出` 按鈕
- 選擇輸出目錄
- 程序將生成修改後的 AssetBundle 文件


### 🔑 支持的资源类型

| 资源类型 | 说明 | 操作支持 |
|---------|------|---------|
| **Texture2D** | 2D 贴图资源 | ✅ 导出 / ✅ 导入 / ✅ 预览 |
| **Sprite** | 精灵图资源 | ✅ 导出 / ✅ 预览 |
| **Mesh** | 3D 模型网格 | ✅ 导出 / ✅ 预览 |
| **TextAsset** | 文本资源 | ✅ 导出 / ✅ 导入 |
| **AnimationClip** | 动画片段 | ✅ 导出 |
| **AudioClip** | 音频资源 | ✅ 导出 |
| **Material** | 材质资源 | ✅ 查看 |
| **Shader** | 着色器 | ✅ 查看 |

---
