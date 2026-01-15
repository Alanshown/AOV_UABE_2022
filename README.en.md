#AOV_UABE_2022
🎮This is a GUI tool based on UnityPy that can be used to extract, preview, modify, and export Assetbundle files for Arena of Valor.🕹️
<div align="center">

# 🎮UABE for Arena of Valor

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Web Version](https://img.shields.io/badge/🌐_Web_Version-Online-brightgreen.svg)](http://ld.ymkeji.xyz/)

**[Simplified Chinese](README.md)** | [English](README.en.md) | [Tiếng Việt](README.vi.md)

<img src="https://github.com/Alanshown/AOV_UABE_2022/icon.ico" width="128" alt="UABE AOV Logo"/>

### 🔧 An AssetBundle editing tool designed specifically for Arena of Valor

---

## 🌐 Try the web version online

**No download required, try it now!** **We offer a fully functional web-based version of the UABE tool:**

### 🚀 [Click to access the web version of UABE](http://ld.ymkeji.xyz/)

**Web Version Features:**

- ✨ No installation required, use directly in your browser

- 🔒 Local data processing, protecting privacy and security

- 📱 Supports multiple platforms (Windows / Mac / Linux)

- 🎯 Functionality is completely identical to the desktop version

- ⚡ Fast response, smooth operation

> 💡 **Tip:** The web version is suitable for quick experience and lightweight operations. For batch processing of large numbers of files, we recommend downloading the desktop version.

---

[📥 Download Desktop Version](https://github.com/KennyYang0726/UABE_AOV/releases) |

</div>

---

## 📋 Table of Contents

- [✨ Project Introduction](#-Project Introduction)

- [🎯 Core Functions](#-Core Functions)

- [🖼️ Function Preview](#️-Function Preview)

- [📦 Installation Guide](#-Installation Guide)

- [🚀 Usage Instructions](#-Usage Instructions)

- [🔍 Function Details](#-Function Details)

- [🛠️ Technical Architecture](#️-Technical Architecture)

- [📂 Project Structure](#-Project Structure)

- [⚙️ Development Guide](#️-Development Guide)

- [🤝 Contribution Guide](#-Contribution Guide)

- [📜 [Open Source License](#-Open Source License)

- [🙏 Acknowledgements](#-Acknowledgements)

---

## ✨ Project Introduction

**UABE for Arena of Valor** is a graphical editing tool designed specifically for the game resource files of Arena of Valor. This project is a modified version based on the **UnityPy** framework of [K0lb3](https://github.com/K0lb3), adding support for AOV-specific encryption and decryption processes.

### 🌟 Key Features

- 🎨 **Modern UI Design** - Intuitive graphical interface built with Tkinter

- 🔐 **AOV Dedicated Encryption Support** - Perfectly supports the encrypted resource formats of Arena of Valor

- 📁 **Batch Processing** - Supports batch operations on single files and entire directories

- 🖼️ **Multi-Format Support** - Supports various resource types including Raw, Texture2D, and Mesh

- 🌍 **Multi-Language Interface** - Supports Traditional Chinese, Simplified Chinese, English, and Vietnamese

- 🎯 **Precise Editing** - Allows for precise export, import, and modification of resources

---

## 🎯 Core Functions

<table>
<thead>
<tr>
<th width="20%">Function Modules</th>
<th width="40%">Function Description</th>
<th width="20%">Supported Formats</th>
<th Operation Type


</tr>

</thead>

<tbody>

<tr>

<td><strong>📤 Export Raw</strong></td>

<td>Directly export the raw data file, preserving complete resource structure information</td>

<td><code>.bytes</code></td>

<td>Export</td>


<tr>

<td><strong>📥 Import Raw</strong></td>

<td>Import the modified raw data, replacing game resources (ensure type matching)</td>

<td><code>.bytes</code></td>

<td>Import</td>


<tr>

<td><strong>🖼️ Export Image</strong></td>

<td>Transfer Texture2D Export Resources as Standard Image Format

<td><code>.png</code></td>

<td>Export</td>

</tr>

<tr>

<td><strong>🎨 Import Image</strong></td>

<td>Import Custom Image to Replace Game Textures (Size Must Be Consistent)</td>

<td><code>.png</code> <code>.jpg</code></td>

<td>Import</td>

</tr>

<tr>

<td><strong>🗿 Export Mesh</strong></td>

<td>Export 3D Model Resources as OBJ Format, Usable in 3D Modeling Software</td>

<td><code>.obj</code></td>

<td>Export</td>

</tr>

<tr>
<td><strong>👁️ Resource Preview</strong></td>

<td>Real-time Preview of Images and 3D Model, supports OpenGL rendering

<td>Multiple formats</td>

<td>View</td>

</tr>

<tr>

<td><strong>💾 Save and Exit</strong></td>

<td>Save all changes to a new AssetBundle file</td>

<td><code>.assetbundle</code></td>

<td>Save</td>

</tr>

<tr>

<td><strong>📂 Batch Operations</strong></td>

<td>Supports opening the entire directory and batch processing multiple AssetBundle files</td>

<td>Directory</td>

<td>Batch</td>

</tr>

</tbody>

</table>

## 🚀 Usage

### Basic Operation Flow

```mermaid
graph LR
A[Start program] --> B[Select file/directory]

B --> C [View resource list]

C --> D [Select resource]

D --> E {Operation type}

E --> |Export| F [Select save location]

E --> |Import| G [Select replacement file]

E --> |Preview| H [View resource]

F --> I [Done]

G --> J [Save and exit]

H --> C

J --> I

```

### Detailed Steps

#### 1️⃣ Install dependencies and restart the program

- Install dependencies in the project path: pip install -r requirements.txt

- Execute `python main.py` (main file)

#### 2️⃣ Open resource files

**Method A: Open a single file**

- Click the menu bar `File` → `Open file`

- Select the `.assetbundle` file

**Method B: Open an entire directory**

- Click the menu bar `File` → `Open directory`

- Select the folder containing multiple `.assetbundle` files

#### 3️⃣ Browse Resource List

- Click the `Info` button on the main interface

- View all resources in the pop-up resource list window

- Sort by name, type, size, etc.

#### 4️⃣ Perform Operations

**Export Resources**

1. Select the target resource in the list

2. Click the corresponding export button on the right

3. Select the save location

**Import Resources**

1. Select the target resource in the list

2. Click the corresponding import button on the right

3. Select the file to import

4. Confirm replacement

**Preview Resources**

- Select the resource in the list

- The preview will automatically appear in the right panel

- For 3D models, you can use the mouse to rotate and view.

#### 5️⃣ Save Changes

- After completing all changes, click the `Save and Exit` button

- Select the output directory

- The program will generate the modified AssetBundle file.

### 🔑 Supported Resource Types

| Resource Type | Description | Operation Support |

|---------|------|---------|

| **Texture2D** | 2D Texture Resources | ✅ Export / ✅ Import / ✅ Preview |

| **Sprite** | Sprite Resources | ✅ Export / ✅ Preview |

| **Mesh** | 3D Model Mesh | ✅ Export 

| **TextAsset** | Text Resources | ✅ Export / ✅ Import |

| **AnimationClip** | Animation Clips | ✅ Export |

| **AudioClip** | Audio Resources | ✅ Export |

| **Material** | Material Resources | ✅ View |

| **Shader** | Shaders | ✅ View |

---