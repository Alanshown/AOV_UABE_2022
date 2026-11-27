# Debian 13 amd64 installer

Build with Debian 13’s Python 3.13 x64 and the distribution’s Xft-enabled Tk 8.6
(install `python3-tk` before creating the build environment). This keeps Chinese
text readable. The release uses an EGL desktop OpenGL pbuffer for Linux 3D previews and requires
Mesa EGL/DRI at runtime; it does not use a Tk GLX window. Keep the tested native
wheel versions when reproducing the build, especially etcpak 0.9.15,
texture2ddecoder 1.0.6, Pillow 13.0.0 and PyOpenGL 3.1.10. TkinterDnD 0.7.0 supplies both Tcl 8 and Tcl 9 binaries. Older extensions
built only for Tcl 8 cannot load under Tcl 9. If drag and drop does not
initialize, the file chooser still works.
The cross-wheel color
regression tests also cover the Windows etcpak pin.

Use a fresh clean checkout of the exact reviewed commit. See
[the release build runbook](../RELEASE_BUILD.md) for native build, install
verification and artifact provenance.

```sh
python3 -m venv .release-venv
. .release-venv/bin/activate
sh packaging/linux/build.sh <full-sha>
```

The artifact is `AOV_UABE_2022-2.5-Linux-x64.deb`, Debian version `2.5-1`.
The app displays 2.5. The release check verifies the package version is
strictly newer than the previously shipped `2.4-2`.
