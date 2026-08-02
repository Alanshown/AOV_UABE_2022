"""Textured, controllable OpenGL viewer for pre-sampled effect payloads."""

from __future__ import annotations

from io import BytesIO
import time

import numpy as np
from PIL import Image
from OpenGL.GL import *

from AssetbundleUtils.OBJ_Viewer import OBJViewer


class EffectViewer(OBJViewer):
    """Play variable-topology effect frames without parsing Unity data in Tk."""

    def __init__(self, master, time_callback=None):
        super().__init__(master)
        self.effect_uv_frames = None
        self.effect_color_frames = None
        self.effect_duration = 0.0
        self.effect_playing = True
        self.effect_loop = True
        self.effect_fps = 30.0
        self.effect_started_at = 0.0
        self.effect_time_callback = time_callback
        self.effect_metadata = {}
        self.atlas_pixels = None
        self.atlas_size = (0, 0)
        self.texture_id = 0
        self.uvs = np.empty((0, 2), dtype=np.float32)
        self.colors = np.empty((0, 4), dtype=np.float32)
        self.last_render_error = ""

    def initgl(self):
        super().initgl()
        glDisable(GL_CULL_FACE)

    def _delete_texture(self):
        if not self.texture_id:
            return
        try:
            glDeleteTextures([int(self.texture_id)])
        except Exception:
            pass
        self.texture_id = 0

    def _ensure_texture(self):
        if self.texture_id or self.atlas_pixels is None or not self.gl_initialized:
            return
        width, height = self.atlas_size
        self.texture_id = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glTexImage2D(
            GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0,
            GL_RGBA, GL_UNSIGNED_BYTE, self.atlas_pixels,
        )
        glBindTexture(GL_TEXTURE_2D, 0)

    def load_effect_payload(self, payload):
        self.stop_animation()
        self.is_loading = True
        try:
            counts = tuple(int(value) for value in payload["frame_vertex_counts"])
            index_counts = tuple(int(value) for value in payload["frame_index_counts"])
            if not counts or len(counts) != len(index_counts):
                raise ValueError("Invalid effect frame counts")
            vertices = np.frombuffer(payload["frame_bytes"], dtype=np.float32).reshape(-1, 3).copy()
            uvs = np.frombuffer(payload["uv_bytes"], dtype=np.float32).reshape(-1, 2).copy()
            colors = np.frombuffer(payload["color_bytes"], dtype=np.float32).reshape(-1, 4).copy()
            indices = np.frombuffer(payload["index_bytes"], dtype=np.uint32).copy()
            if sum(counts) != len(vertices) or len(vertices) != len(uvs) or len(vertices) != len(colors):
                raise ValueError("Effect vertex attributes are inconsistent")
            if sum(index_counts) != len(indices):
                raise ValueError("Effect index buffer is inconsistent")
            vertex_offsets = np.cumsum((0,) + counts)
            index_offsets = np.cumsum((0,) + index_counts)
            self.animation_frames = [
                vertices[vertex_offsets[i]:vertex_offsets[i + 1]] for i in range(len(counts))
            ]
            self.effect_uv_frames = [
                uvs[vertex_offsets[i]:vertex_offsets[i + 1]] for i in range(len(counts))
            ]
            self.effect_color_frames = [
                colors[vertex_offsets[i]:vertex_offsets[i + 1]] for i in range(len(counts))
            ]
            self.animation_index_frames = [
                indices[
                    index_offsets[i]:index_offsets[i + 1]
                ].copy()
                for i in range(len(counts))
            ]
            for frame_index, frame_indices in enumerate(
                self.animation_index_frames
            ):
                if (
                    len(frame_indices)
                    and int(frame_indices.max()) >= counts[frame_index]
                ):
                    raise ValueError(
                        f"Effect frame {frame_index} contains an "
                        "out-of-range vertex index"
                    )
            with Image.open(BytesIO(payload["atlas_png"])) as source:
                atlas = source.convert("RGBA").transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                self.atlas_size = atlas.size
                self.atlas_pixels = np.asarray(atlas, dtype=np.uint8).copy()
            self._delete_texture()
            self.primitive_mode = "triangles"
            self.effect_duration = max(0.001, float(payload.get("duration", 0.0)))
            self.effect_fps = max(1.0, float(payload.get("frames_per_second", 30.0)))
            self.animation_interval_ms = max(16, int(round(1000.0 / self.effect_fps)))
            self.effect_metadata = dict(payload.get("metadata", {}))
            self.effect_playing = True
            self.effect_started_at = time.monotonic()
            self._set_frame(0, notify=True)
            # A few distant particles must not shrink the character/effect root
            # to a dot. Percentile bounds retain the composed model and dense
            # effect geometry while ignoring sparse emission outliers.
            focus = np.asarray(self.animation_frames[0], dtype=np.float32)
            if len(focus) >= 64:
                lower = np.percentile(focus, 1.0, axis=0).astype(np.float32)
                upper = np.percentile(focus, 99.0, axis=0).astype(np.float32)
                self.center = (lower + upper) * 0.5
                self.fit_vertices = np.asarray((lower, upper), dtype=np.float32)
            else:
                self.center = focus.mean(axis=0)
                self.fit_vertices = focus
            self.angle_x, self.angle_y = 25, -35
            self.trans_x, self.trans_y = 0, 0
            self.zoom_factor = self._calculate_fit_zoom()
            self.is_loading = False
            self.update_idletasks()
            self.redraw()
            self.animation_after_job = self.after(self.animation_interval_ms, self._advance_animation)
        except Exception:
            self.is_loading = False
            self.stop_animation()
            raise

    def _set_frame(self, frame_index, notify=False):
        if self.animation_frames is None or not len(self.animation_frames):
            return
        index = max(0, min(int(frame_index), len(self.animation_frames) - 1))
        self.animation_frame_index = index
        self.vertices = self.animation_frames[index].copy()
        self.indices = self.animation_index_frames[index].copy()
        self.uvs = self.effect_uv_frames[index].copy()
        self.colors = self.effect_color_frames[index].copy()
        self.faces = self.indices.reshape(-1, 3)
        self._compute_normals_fast()
        if notify and self.effect_time_callback is not None:
            current = self.effect_duration * index / max(1, len(self.animation_frames) - 1)
            self.effect_time_callback(current, self.effect_duration, self.effect_playing)

    def _advance_animation(self):
        self.animation_after_job = None
        if self.animation_frames is None or not len(self.animation_frames):
            return
        if self.effect_playing:
            next_index = self.animation_frame_index + 1
            if next_index >= len(self.animation_frames):
                if self.effect_loop:
                    next_index = 0
                else:
                    next_index = len(self.animation_frames) - 1
                    self.effect_playing = False
            self._set_frame(next_index, notify=True)
            self.redraw()
        self.animation_after_job = self.after(self.animation_interval_ms, self._advance_animation)

    def play(self):
        self.effect_playing = True
        if self.animation_after_job is None and self.animation_frames is not None:
            self.animation_after_job = self.after(self.animation_interval_ms, self._advance_animation)

    def pause(self):
        self.effect_playing = False

    def set_loop(self, enabled):
        self.effect_loop = bool(enabled)

    def seek_fraction(self, fraction):
        if self.animation_frames is None or not len(self.animation_frames):
            return
        fraction = max(0.0, min(1.0, float(fraction)))
        self._set_frame(round(fraction * (len(self.animation_frames) - 1)), notify=True)
        self.redraw()

    def stop_animation(self):
        super().stop_animation()
        self.effect_uv_frames = None
        self.effect_color_frames = None
        self.effect_playing = False

    def _draw_indexed_mesh(self):
        self._ensure_texture()
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_TEXTURE_COORD_ARRAY)
        glEnableClientState(GL_COLOR_ARRAY)
        if self.texture_id:
            glEnable(GL_TEXTURE_2D)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
        try:
            glVertexPointer(3, GL_FLOAT, 0, self.vertices)
            glTexCoordPointer(2, GL_FLOAT, 0, self.uvs)
            glColorPointer(4, GL_FLOAT, 0, self.colors)
            glDrawElements(GL_TRIANGLES, self.indices.size, GL_UNSIGNED_INT, self.indices)
        finally:
            if self.texture_id:
                glBindTexture(GL_TEXTURE_2D, 0)
                glDisable(GL_TEXTURE_2D)
            glDisableClientState(GL_COLOR_ARRAY)
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glDisableClientState(GL_VERTEX_ARRAY)

    def redraw(self):
        if self.is_loading or len(self.vertices) == 0 or len(self.indices) == 0:
            return
        try:
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            glTranslatef(self.trans_x / 80, -self.trans_y / 80, self.zoom_factor)
            glRotatef(self.angle_x, 1, 0, 0)
            glRotatef(self.angle_y, 0, 1, 0)
            glTranslatef(-self.center[0], -self.center[1], -self.center[2])
            glDisable(GL_LIGHTING)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glDepthMask(GL_FALSE)
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
            self._draw_indexed_mesh()
            glDepthMask(GL_TRUE)
            glEnable(GL_LIGHTING)
            self.tkSwapBuffers()
            error_code = int(glGetError())
            self.last_render_error = (
                "" if error_code == GL_NO_ERROR
                else f"OpenGL error 0x{error_code:04X}"
            )
        except Exception as exc:
            self.last_render_error = f"{type(exc).__name__}: {exc}"
            try:
                glDepthMask(GL_TRUE)
            except Exception:
                pass

    def destroy(self):
        self.stop_animation()
        self._delete_texture()
        super().destroy()
