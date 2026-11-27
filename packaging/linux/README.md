# Debian 13 amd64 installer

Build in a Python 3.12 x64 environment with Tk and native Linux wheels. The
release uses an EGL desktop OpenGL pbuffer for Linux 3D previews and requires
Mesa EGL/DRI at runtime; it does not use a Tk GLX window. Keep the tested native
wheel versions when reproducing the build, especially etcpak 0.9.15,
texture2ddecoder 1.0.6, Pillow 13.0.0 and PyOpenGL 3.1.10. The cross-wheel color
regression tests also cover the Windows etcpak pin.

```sh
python -m pytest -q
python -m PyInstaller --noconfirm --clean packaging/linux/AOV_UABE_2022.spec
sh packaging/linux/package.sh
```

The artifact remains named `AOV_UABE_2022-2.4-Linux-x64.deb`; its Debian version
is `2.4-1` so package managers recognize the upgrade from the first 2.4 build.
The app continues to display version 2.4.
