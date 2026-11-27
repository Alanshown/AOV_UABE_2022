# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller analysis for the Debian 13 x64 release."""
from pathlib import Path
import sys
import archspec

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH).resolve().parents[1]
vendor = root / "AssetbundleUtils" / "UnityPy_AOV"
datas = [(str(root / name), ".") for name in ("icon.ico", "Settings.json", "lang.json")]
datas += [
    (str(vendor / "resources"), "AssetbundleUtils/UnityPy_AOV/resources"),
    (str(vendor / "helpers" / "global-metadata.dat"), "AssetbundleUtils/UnityPy_AOV/helpers"),
    (str(Path(archspec.__file__).parent / "json" / "LICENSE-MIT"), "archspec/cpu"),
]
# Native Linux Python installations may provide Tk 9 outside the system paths.
libdir = Path(sys.base_prefix) / "lib"
binaries = [(str(libdir / name), ".") for name in ("libtcl9.0.so", "libtcl9tk9.0.so") if (libdir / name).is_file()]
hiddenimports = ["PIL._tkinter_finder", "pyfmodex", "OpenGL.platform.egl", "OpenGL.EGL", "OpenGL.raw.EGL.VERSION.EGL_1_5"]
for dependency in ("tkinterdnd2", "archspec", "astc_encoder"):
    extra_data, extra_binaries, extra_imports = collect_all(dependency)
    datas += extra_data
    binaries += extra_binaries
    hiddenimports += extra_imports

a = Analysis([str(root / "main.py")], pathex=[str(root)], binaries=binaries,
             datas=datas, hiddenimports=hiddenimports, excludes=[], noarchive=False, optimize=0)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AOV_UABE_2022",
          debug=False, strip=False, upx=False, console=True)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="AOV_UABE_2022")
