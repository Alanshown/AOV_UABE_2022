"""Linux OpenGL preview: draw into an EGL pbuffer, not a Tk X11 drawable."""
from ctypes import byref, cast, c_void_p
import os
import tkinter as tk

from PIL import Image, ImageTk

# PyOpenGL must resolve extension entry points and current contexts via EGL.
# This module is imported before any OpenGL module in the Linux GUI.
os.environ["PYOPENGL_PLATFORM"] = "egl"
from OpenGL import EGL, GL

_DISPLAY = None
_PLATFORM_SURFACELESS_MESA = 0x31DD


def _get_display():
    global _DISPLAY
    if _DISPLAY:
        return _DISPLAY
    errors = []
    for get in (
        lambda: EGL.eglGetPlatformDisplay(_PLATFORM_SURFACELESS_MESA, None, None),
        lambda: EGL.eglGetDisplay(EGL.EGL_DEFAULT_DISPLAY),
    ):
        try:
            display = get()
            if display and EGL.eglInitialize(display, byref(EGL.EGLint()), byref(EGL.EGLint())):
                _DISPLAY = display
                return display
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("Could not initialize a Linux EGL renderer" +
                       (": " + errors[-1] if errors else ""))


def _attrs(*values):
    values = (*values, EGL.EGL_NONE)
    return (EGL.EGLint * len(values))(*values)


class LinuxOpenGLFrame(tk.Canvas):
    """A Tk canvas using a process-local, window-independent GL context.

    The pbuffer has no dependency on the visual or lifetime of a Tk XID.
    A bad display/driver is reported to the UI instead of terminating Tk
    through Xlib's fatal default GLX error handler.
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, highlightthickness=0, borderwidth=0,
                         bg="#d9d9e0", **kwargs)
        self.context_created = False
        self._egl_display = None
        self._egl_config = None
        self._egl_context = None
        self._egl_surface = None
        self._egl_size = None
        self._surface_image = self.create_image(0, 0, anchor="nw")
        self._surface_photo = None
        self.bind("<Expose>", self._on_expose, add="+")
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_expose(self, _event):
        if self.context_created and self.winfo_ismapped():
            self.redraw()

    def _on_destroy(self, event):
        if event.widget is self:
            self._release_egl()

    def realize_preview(self):
        """Create the context and propagate initialization errors to the GUI."""
        self.update_idletasks()
        self.tkMakeCurrent()

    def tkMakeCurrent(self):
        if not self._egl_context:
            self._egl_display = _get_display()
            EGL.eglBindAPI(EGL.EGL_OPENGL_API)
            configs = (EGL.EGLConfig * 1)()
            count = EGL.EGLint()
            EGL.eglChooseConfig(self._egl_display, _attrs(
                EGL.EGL_SURFACE_TYPE, EGL.EGL_PBUFFER_BIT,
                EGL.EGL_RENDERABLE_TYPE, EGL.EGL_OPENGL_BIT,
                EGL.EGL_RED_SIZE, 8, EGL.EGL_GREEN_SIZE, 8, EGL.EGL_BLUE_SIZE, 8,
                EGL.EGL_DEPTH_SIZE, 24,
            ), configs, 1, byref(count))
            if count.value < 1:
                raise RuntimeError("No EGL pbuffer configuration supports desktop OpenGL")
            self._egl_config = configs[0]
            self._egl_context = EGL.eglCreateContext(
                self._egl_display, self._egl_config, EGL.EGL_NO_CONTEXT, None
            )
            if not self._egl_context:
                raise RuntimeError("Could not create a desktop OpenGL context")
        size = max(1, self.winfo_width()), max(1, self.winfo_height())
        if self._egl_size != size:
            new_surface = EGL.eglCreatePbufferSurface(
                self._egl_display, self._egl_config,
                _attrs(EGL.EGL_WIDTH, size[0], EGL.EGL_HEIGHT, size[1]),
            )
            if not new_surface:
                raise RuntimeError("Could not allocate the OpenGL preview surface")
            try:
                EGL.eglMakeCurrent(self._egl_display, new_surface, new_surface, self._egl_context)
            except Exception:
                EGL.eglDestroySurface(self._egl_display, new_surface)
                raise
            old_surface, self._egl_surface = self._egl_surface, new_surface
            self._egl_size = size
            if old_surface:
                EGL.eglDestroySurface(self._egl_display, old_surface)
            if self.context_created:
                self.setup_projection()
        else:
            EGL.eglMakeCurrent(self._egl_display, self._egl_surface,
                               self._egl_surface, self._egl_context)
        if not self.context_created:
            self.initgl()
            self.context_created = True

    def tkSwapBuffers(self):
        if not self._egl_surface or not self.winfo_ismapped():
            return
        width, height = self._egl_size
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        GL.glReadBuffer(GL.GL_FRONT)
        pixels = GL.glReadPixels(0, 0, width, height, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        image = Image.frombytes("RGB", (width, height), pixels).transpose(
            Image.Transpose.FLIP_TOP_BOTTOM
        )
        if self._surface_photo is None or (
            self._surface_photo.width(), self._surface_photo.height()
        ) != (width, height):
            self._surface_photo = ImageTk.PhotoImage(image, master=self)
            self.itemconfigure(self._surface_image, image=self._surface_photo)
        else:
            self._surface_photo.paste(image)

    def _release_egl(self):
        if not self._egl_display or not self._egl_context:
            return
        try:
            if cast(EGL.eglGetCurrentContext(), c_void_p).value == cast(self._egl_context, c_void_p).value:
                EGL.eglMakeCurrent(self._egl_display, EGL.EGL_NO_SURFACE,
                                   EGL.EGL_NO_SURFACE, EGL.EGL_NO_CONTEXT)
            if self._egl_surface:
                EGL.eglDestroySurface(self._egl_display, self._egl_surface)
        finally:
            EGL.eglDestroyContext(self._egl_display, self._egl_context)
            self._egl_context = self._egl_surface = self._egl_size = None
            self.context_created = False
