#AOV_UABE_2022
🎮Đây là một công cụ GUI dựa trên UnityPy có thể được sử dụng để trích xuất, xem trước, chỉnh sửa và xuất các tệp Assetbundle cho Arena of Valor.🕹️
<div align="center">

# 🎮UABE cho Arena of Valor

[![Giấy phép: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Phiên bản Python](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/)
[![Nền tảng](https://img.shields.io/badge/platform-Windows-lightgrey.svg)](https://www.microsoft.com/windows)
[![Web](https://img.shields.io/badge/platform-Windows-lightgrey.svg)
[![Web](https://img.shields.io/badge/com/windows) Phiên bản](https://img.shields.io/badge/🌐_Web_Version-Online-brightgreen.svg)](http://ld.ymkeji.xyz/)

**[Tiếng Trung Giản Thể](README.md)** | [Tiếng Anh](README.en.md) | [Tiếng Việt](README.vi.md)

<img src="https://github.com/Alanshown/AOV_UABE_2022/blob/main/icon.ico" width="128" alt="UABE AOV Logo"/>

### 🔧 Công cụ chỉnh sửa AssetBundle được thiết kế dành riêng cho Arena of Valor

---

## 🌐 Trải nghiệm phiên bản web trực tuyến

**Không cần tải xuống, hãy thử ngay!** Chúng tôi cung cấp phiên bản web đầy đủ chức năng của công cụ UABE:

### 🚀 [Nhấp vào đây để truy cập phiên bản web của UABE](http://ld.ymkeji.xyz/)

**Tính năng của phiên bản web:**

- ✨ Không cần cài đặt, sử dụng trực tiếp trên trình duyệt

- 🔒 Xử lý dữ liệu cục bộ, bảo vệ quyền riêng tư và bảo mật

- 📱 Hỗ trợ nhiều nền tảng (Windows / Mac / Linux)

- 🎯 Chức năng hoàn toàn giống với phiên bản gốc Phiên bản máy tính để bàn

- ⚡ Phản hồi nhanh, hoạt động mượt mà

> 💡 **Mẹo**: Phiên bản web phù hợp cho trải nghiệm nhanh và các thao tác nhẹ. Đối với việc xử lý hàng loạt số lượng lớn tệp, chúng tôi khuyên bạn nên tải xuống phiên bản máy tính để bàn.

---

[📥 Tải xuống phiên bản máy tính để bàn](https://github.com/Alanshown/AOV_UABE_2022/releases/download/Latest/AOV_UABE_v2.0.0.zip) |

</div>

---

## 📋 Mục lục

- [✨ Giới thiệu dự án](#-Giới thiệu dự án)

- [🎯 Chức năng chính](#-Chức năng chính)

- [🚀 Cách sử dụng](#-Cách sử dụng)

---

## ✨ Giới thiệu dự án

**UABE for Arena of Valor** là một công cụ chỉnh sửa đồ họa được thiết kế đặc biệt cho các tệp tài nguyên trò chơi của Arena of Valor. Dự án này là phiên bản được sửa đổi dựa trên khung **UnityPy** của [K0lb3](https://github.com/K0lb3), bổ sung hỗ trợ cho các quy trình mã hóa và giải mã dành riêng cho AOV.

### 🌟 Các tính năng chính

- 🎨 **Thiết kế giao diện người dùng hiện đại** - Giao diện đồ họa trực quan được xây dựng bằng Tkinter

- 🔐 **Hỗ trợ mã hóa chuyên dụng cho AOV** - Hỗ trợ hoàn hảo các định dạng tài nguyên được mã hóa của Arena of Valor

- 📁 **Xử lý hàng loạt** - Hỗ trợ các thao tác hàng loạt trên các tệp đơn lẻ và toàn bộ thư mục

- 🖼️ **Hỗ trợ nhiều định dạng** - Hỗ trợ nhiều loại tài nguyên bao gồm Raw, Texture2D và Mesh

- 🌍 **Giao diện đa ngôn ngữ** - Hỗ trợ tiếng Trung phồn thể, tiếng Trung giản thể, tiếng Anh và tiếng Việt

- 🎯 **Chỉnh sửa chính xác** - Cho phép xuất, nhập và chỉnh sửa tài nguyên một cách chính xác

---

## 🎯 Các chức năng cốt lõi

<table>

<thead>

<tr>
<th width="20%">Các mô-đun chức năng</th>
<th width="40%">Mô tả chức năng</th> width="20%">Các định dạng được hỗ trợ</th>
<th Loại thao tác

</tr>

</thead>

<tbody>

<tr>

<td><strong>📤 Xuất dữ liệu thô</strong></td>

<td>Xuất trực tiếp tệp dữ liệu thô, giữ nguyên thông tin cấu trúc tài nguyên đầy đủ</td>

<td><code>.bytes</code></td>

<td>Xuất</td>

<tr>

<td><strong>📥 Nhập dữ liệu thô</strong></td>

<td>Nhập dữ liệu thô đã chỉnh sửa, thay thế tài nguyên trò chơi (đảm bảo khớp loại)</td>

<td><code>.bytes</code></td>

<td>Nhập</td>

<tr>

<td><strong>🖼️ Xuất hình ảnh</strong></td>

<td>Chuyển tài nguyên xuất Texture2D dưới dạng hình ảnh chuẩn</strong></td> Định dạng

<td><code>.png</code></td>

<td>Xuất</td>

</tr>

<tr>

<td><strong>🎨 Nhập hình ảnh</strong></td>

<td>Nhập hình ảnh tùy chỉnh để thay thế kết cấu trò chơi (Kích thước phải nhất quán)</td>

<td><code>.png</code> <code>.jpg</code></td>

<td>Nhập</td>

</tr>

<tr>

<td><strong>🗿 Xuất lưới</strong></td>

<td>Xuất tài nguyên mô hình 3D dưới dạng định dạng OBJ, có thể sử dụng trong phần mềm mô hình 3D</td>

<td><code>.obj</code></td>

<td>Xuất</td>

</tr>

<tr>

<td><strong>👁️ Tài nguyên Xem trước</strong></td>

<td>Xem trước hình ảnh và mô hình 3D theo thời gian thực, hỗ trợ kết xuất OpenGL

<td>Nhiều định dạng</td>

<td>Xem</td>

</tr>

<tr>

<td><strong>💾 Lưu và Thoát</strong></td>

<td>Lưu tất cả các thay đổi vào một tệp AssetBundle mới</td>

<td><code>.assetbundle</code></td>

<td>Lưu</td>

</tr>

<tr>

<td><strong>📂 Thao tác hàng loạt</strong></td>

<td>Hỗ trợ mở toàn bộ thư mục và xử lý hàng loạt nhiều tệp AssetBundle</td>

<td>Thư mục</td>

<td>Xử lý hàng loạt</td>

</tr>

</tbody>

</table>

## 🚀 Cách sử dụng

### Cơ bản Luồng hoạt động

```mermaid
đồ thị LR
A[Khởi động chương trình] --> B[Chọn tệp/thư mục]

B --> C [Xem danh sách tài nguyên]

C --> D [Chọn tài nguyên]

D --> E {Loại thao tác}

E --> |Xuất| F [Chọn vị trí lưu]

E --> |Nhập| G [Chọn tệp thay thế]

E --> |Xem trước| H [Xem tài nguyên]

F --> I [Hoàn tất]

G --> J [Lưu và thoát]

H --> C

J --> I

```

### Các bước chi tiết

#### 1️⃣ Cài đặt các thư viện phụ thuộc và khởi động lại chương trình

- Cài đặt các thư viện phụ thuộc vào đường dẫn dự án: pip install -r requirements.txt

- Chạy tệp `python main.py` (tệp chính)

- Hoặc tải trực tiếp gói nén [📥Phiên bản máy tính để bàn](https://github.com/Alanshown/AOV_UABE_2022/releases/download/Latest/AOV_UABE_v2.0.0.zip), giải nén và nhấp đúp vào tệp exe.

#### 2️⃣ Mở Tệp Tài Nguyên

**Phương pháp A: Mở Một Tệp**

- Nhấp vào thanh menu `Tệp` → `Mở Tệp`

- Chọn tệp `.assetbundle`

**Phương pháp B: Mở Toàn Bộ Thư Mục**

- Nhấp vào thanh menu `Tệp` → `Mở Thư Mục`

- Chọn thư mục chứa nhiều tệp `.assetbundle`

#### 3️⃣ Duyệt Danh Sách Tài Nguyên

- Nhấp vào nút `Thông Tin` trên giao diện chính

- Xem tất cả tài nguyên trong cửa sổ danh sách tài nguyên bật lên

- Sắp xếp theo tên, loại, kích thước, v.v.

#### 4️⃣ Thực Hiện Các Thao Tác

**Xuất Tài Nguyên**

1. Chọn tài nguyên cần xuất trong danh sách

2. Nhấp vào nút xuất tương ứng ở bên phải

3. Chọn vị trí lưu

**Nhập Tài Nguyên**

1. Chọn tài nguyên cần nhập trong danh sách

2. Nhấp vào nút nhập tương ứng nút bên phải

3. Chọn tệp cần nhập

4. Xác nhận thay thế

**Xem trước tài nguyên**

- Chọn tài nguyên trong danh sách

- Bản xem trước sẽ tự động xuất hiện ở bảng bên phải

- Đối với mô hình 3D, bạn có thể dùng chuột để xoay và xem.

#### 5️⃣ Lưu thay đổi

- Sau khi hoàn tất tất cả các thay đổi, hãy nhấp vào nút `Lưu và Thoát`.

- Chọn thư mục đầu ra.

- Chương trình sẽ tạo tệp AssetBundle đã được sửa đổi.

### 🔑 Các loại tài nguyên được hỗ trợ

| Loại tài nguyên | Mô tả | Hỗ trợ thao tác |

|---------|------|---------|

| **Texture2D** | Tài nguyên kết cấu 2D | ✅ Xuất / ✅ Nhập / ✅ Xem trước |

| **Sprite** | Tài nguyên Sprite | ✅ Xuất

| **Mesh** | Lưới mô hình 3D | ✅ Xuất / ✅ Xem trước |

| **TextAsset** | Tài nguyên văn bản | ✅ Xuất / ✅ Nhập |

| **AnimationClip** | Đoạn phim hoạt hình | ✅ Xuất |

| **AudioClip** | Tài nguyên âm thanh | ✅ Xuất |

| **Material** | Tài nguyên vật liệu | ✅ Xem |

| **Shader** | Shader | ✅ Xem |

---