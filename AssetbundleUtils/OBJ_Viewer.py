# -*- coding: utf-8 -*-
"""
现代化3D模型预览器
支持快速刷新、Ctrl+左键旋转、滚轮缩放等交互
"""
import numpy as np
from PIL import Image
from OpenGL.GL import *
from OpenGL.GLUT import *
from OpenGL.GLU import *
from pyopengltk import OpenGLFrame
import tkinter as tk

# 现代化配色
VIEWER_COLORS = {
    "bg": (0.18, 0.20, 0.25, 1.0),        # 深色背景
    "grid": (0.3, 0.32, 0.38, 1.0),       # 网格线
    "model": (0.75, 0.78, 0.82),          # 模型颜色
    "wireframe": (0.2, 0.6, 1.0),         # 线框颜色(蓝色)
    "light_ambient": (0.3, 0.3, 0.35, 1.0),
    "light_diffuse": (0.85, 0.85, 0.9, 1.0),
    "light_specular": (1.0, 1.0, 1.0, 1.0),
}


class OBJViewer(OpenGLFrame):
    """现代化3D OBJ模型预览器"""
    
    def __init__(self, master):
        super().__init__(master, width=400, height=500)
        # 模型数据
        self.vertices = []
        self.faces = []
        self.normals = []
        self.indices = np.empty(0, dtype=np.uint32)
        self.center = np.array([0.0, 0.0, 0.0])
        self.fit_vertices = None
        self.animation_frames = None
        self.animation_index_frames = None
        self.primitive_mode = "triangles"
        self.animation_frame_index = 0
        self.animation_interval_ms = 33
        self.animation_after_job = None
        
        # 视角控制
        self.angle_x, self.angle_y = 25, -35  # 初始角度，更好的默认视角
        self.trans_x, self.trans_y = 0, 0
        self.zoom_factor = -5
        self.wireframe_mode = 0  # 0: solid, 1: wireframe, 2: solid+wireframe
        
        # 状态标志
        self.gl_initialized = False
        self.is_loading = False
        self.last_x, self.last_y = 0, 0
        self.ctrl_pressed = False
        
        # 绑定鼠标事件 - Ctrl+左键旋转，普通左键也旋转
        self.bind("<Button-1>", self.on_mouse_down)
        self.bind("<B1-Motion>", self.on_mouse_drag)
        self.bind("<ButtonRelease-1>", self.on_mouse_up)
        
        # 右键平移
        self.bind("<Button-3>", self.on_mouse_down)
        self.bind("<B3-Motion>", self.on_pan_drag)
        
        # Ctrl+左键平移
        self.bind("<Control-Button-1>", self.on_ctrl_mouse_down)
        self.bind("<Control-B1-Motion>", self.on_pan_drag)
        
        # 滚轮缩放
        self.bind("<MouseWheel>", self.on_zoom)
        self.bind("<Button-4>", lambda e: self.on_zoom_linux(e, 1))   # Linux
        self.bind("<Button-5>", lambda e: self.on_zoom_linux(e, -1))  # Linux
        
        # 窗口大小变化
        self.bind("<Configure>", self.on_resize)
        
        # Ctrl+W切换线框模式
        self.bind("<Control-w>", self.toggle_wireframe)
        self.bind("<Control-W>", self.toggle_wireframe)
    
    def initgl(self):
        """初始化OpenGL环境 - 浅灰色背景便于查看模型"""
        # 使用浅灰色背景，便于查看模型
        glClearColor(0.85, 0.85, 0.88, 1.0)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        
        # 设置光照 - 更亮的光照
        glLightfv(GL_LIGHT0, GL_POSITION, [1, 1, 1, 0])
        glLightfv(GL_LIGHT0, GL_AMBIENT, [0.4, 0.4, 0.4, 1.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE, [0.9, 0.9, 0.9, 1.0])
        glLightfv(GL_LIGHT0, GL_SPECULAR, [1.0, 1.0, 1.0, 1.0])
        
        # 启用抗锯齿
        glEnable(GL_LINE_SMOOTH)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
        
        self.gl_initialized = True
        self.setup_projection()
    
    def setup_projection(self):
        """设置投影矩阵"""
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 0 or height <= 0:
            width, height = 400, 500
        glViewport(0, 0, width, height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        aspect = width / height if height > 0 else 1.0
        gluPerspective(45, aspect, 0.1, 10000.0)
        glMatrixMode(GL_MODELVIEW)
    
    def on_resize(self, event):
        """窗口大小改变时重新设置投影"""
        if self.gl_initialized:
            self.setup_projection()
            if len(self.vertices) > 0:
                self.after(5, self.redraw)

    def _calculate_fit_zoom(self):
        """Return a camera distance that fits the model in the current viewport."""
        if len(self.vertices) == 0:
            return -5.0

        # Tk may not have assigned the final grid size when a preview result first
        # arrives.  Flush geometry only (never regular events) before measuring.
        self.update_idletasks()
        width = max(1, int(self.winfo_width()))
        height = max(1, int(self.winfo_height()))
        aspect = max(0.12, float(width) / float(height))

        fit_vertices = (
            self.fit_vertices
            if self.fit_vertices is not None and len(self.fit_vertices)
            else self.vertices
        )
        centered = np.asarray(fit_vertices, dtype=np.float32) - self.center
        radius = float(np.max(np.linalg.norm(centered, axis=1)))
        if not np.isfinite(radius) or radius <= 1e-6:
            return -5.0

        vertical_half_fov = np.radians(45.0 * 0.5)
        horizontal_half_fov = np.arctan(np.tan(vertical_half_fov) * aspect)
        limiting_half_fov = max(0.02, min(vertical_half_fov, horizontal_half_fov))

        # A bounding sphere keeps the whole character visible at the default
        # three-quarter rotation as well as in unusually narrow preview panes.
        distance = radius / np.sin(limiting_half_fov)
        return -max(distance * 1.08, 0.1)
    
    def load_obj_data(self, obj_text):
        """快速解析并加载OBJ模型数据"""
        self.stop_animation()
        self.is_loading = True
        self.vertices = []
        self.fit_vertices = None
        self.faces = []
        self.normals = []
        self.indices = np.empty(0, dtype=np.uint32)
        self.primitive_mode = "triangles"
        
        # 重置视角到默认
        self.angle_x, self.angle_y = 25, -35
        self.trans_x, self.trans_y = 0, 0
        self.zoom_factor = -5
        
        try:
            # 快速解析OBJ
            for line in obj_text.split("\n"):
                line = line.strip()
                if line.startswith("v "):
                    parts = line.split()
                    if len(parts) >= 4:
                        self.vertices.append([float(parts[1]), float(parts[2]), float(parts[3])])
                elif line.startswith("f "):
                    parts = line.split()[1:]
                    face = []
                    for p in parts:
                        raw_idx = int(p.split("/")[0])
                        idx = raw_idx - 1 if raw_idx > 0 else len(self.vertices) + raw_idx
                        face.append(idx)
                    if len(face) >= 3:
                        self.faces.extend(
                            [face[0], face[i], face[i + 1]]
                            for i in range(1, len(face) - 1)
                        )
            
            if len(self.vertices) > 0:
                self.vertices = np.array(self.vertices, dtype=np.float32)
                self.indices = np.asarray(self.faces, dtype=np.uint32).reshape(-1)
                self.center = self.vertices.mean(axis=0)
                
                # 计算模型边界并自动缩放
                self.zoom_factor = self._calculate_fit_zoom()
                
                self.compute_normals()
                
                # 立即刷新显示
                self.is_loading = False
                self.update_idletasks()  # 强制更新UI
                self.redraw()  # 立即绘制，不延迟
            else:
                self.is_loading = False
                
        except Exception as e:
            self.is_loading = False
            print(f"OBJ加载错误: {e}")

    def load_mesh_buffers(self, payload):
        """Load buffers already decoded by an isolated preview process."""
        self.stop_animation()
        vertex_bytes, index_bytes, normal_bytes, vertex_count, index_count = payload
        self.is_loading = True
        self.primitive_mode = "triangles"
        try:
            self.vertices = np.frombuffer(vertex_bytes, dtype=np.float32).reshape(
                int(vertex_count), 3
            ).copy()
            self.indices = np.frombuffer(index_bytes, dtype=np.uint32)[:int(index_count)].copy()
            self.faces = self.indices.reshape(-1, 3)
            self.normals = np.frombuffer(normal_bytes, dtype=np.float32).reshape(
                int(vertex_count), 3
            ).copy()
            self.center = self.vertices.mean(axis=0)
            self.angle_x, self.angle_y = 25, -35
            self.trans_x, self.trans_y = 0, 0
            self.zoom_factor = self._calculate_fit_zoom()
            self.is_loading = False
            self.update_idletasks()
            self.redraw()
        except Exception:
            self.is_loading = False
            raise

    def load_animation_buffers(self, payload):
        """Play CPU-skinned frames produced by an isolated preview process."""
        self.stop_animation()
        self.is_loading = True
        try:
            if isinstance(payload, dict) and int(payload.get("version", 0)) >= 2:
                self.primitive_mode = str(payload.get("primitive", "triangles"))
                vertex_counts = tuple(
                    int(value) for value in payload["frame_vertex_counts"]
                )
                index_counts = tuple(
                    int(value) for value in payload["frame_index_counts"]
                )
                if not vertex_counts or len(vertex_counts) != len(index_counts):
                    raise ValueError("Invalid variable-topology animation payload")
                all_vertices = np.frombuffer(
                    payload["frame_bytes"], dtype=np.float32
                ).reshape(-1, 3).copy()
                all_indices = np.frombuffer(
                    payload["index_bytes"], dtype=np.uint32
                ).copy()
                if sum(vertex_counts) != len(all_vertices):
                    raise ValueError("Animation vertex buffer size mismatch")
                if sum(index_counts) != len(all_indices):
                    raise ValueError("Animation index buffer size mismatch")
                vertex_offsets = np.cumsum((0,) + vertex_counts)
                index_offsets = np.cumsum((0,) + index_counts)
                self.animation_frames = [
                    all_vertices[vertex_offsets[index]:vertex_offsets[index + 1]]
                    for index in range(len(vertex_counts))
                ]
                self.animation_index_frames = [
                    all_indices[index_offsets[index]:index_offsets[index + 1]]
                    for index in range(len(index_counts))
                ]
                self.indices = self.animation_index_frames[0].copy()
                frames_per_second = float(payload["frames_per_second"])
            else:
                self.primitive_mode = "triangles"
                (
                    frame_bytes, index_bytes, frame_count, vertex_count,
                    index_count, frames_per_second, _metadata,
                ) = payload
                self.animation_frames = np.frombuffer(
                    frame_bytes, dtype=np.float32
                ).reshape(int(frame_count), int(vertex_count), 3).copy()
                self.animation_index_frames = None
                self.indices = np.frombuffer(index_bytes, dtype=np.uint32)[
                    :int(index_count)
                ].copy()
            stride = 2 if self.primitive_mode == "lines" else 3
            self.faces = self.indices.reshape(-1, stride)
            self.animation_frame_index = 0
            self.animation_interval_ms = max(
                16, int(round(1000.0 / max(1.0, float(frames_per_second))))
            )
            self.vertices = self.animation_frames[0].copy()
            self.center = self.vertices.mean(axis=0)
            self.angle_x, self.angle_y = 25, -35
            self.trans_x, self.trans_y = 0, 0
            self.zoom_factor = self._calculate_fit_zoom()
            self._compute_normals_fast()
            self.is_loading = False
            self.update_idletasks()
            self.redraw()
            self.animation_after_job = self.after(
                self.animation_interval_ms, self._advance_animation
            )
        except Exception:
            self.is_loading = False
            self.animation_frames = None
            self.animation_index_frames = None
            raise

    def _compute_normals_fast(self):
        """Vectorized smooth normals used while animation frames advance."""
        self.normals = np.zeros_like(self.vertices, dtype=np.float32)
        if self.primitive_mode == "lines":
            self.normals[:] = (0.0, 1.0, 0.0)
            return
        if self.indices.size < 3:
            return
        triangles = self.indices[:self.indices.size - self.indices.size % 3].reshape(-1, 3)
        valid = np.all(triangles < len(self.vertices), axis=1)
        triangles = triangles[valid]
        if not len(triangles):
            self.normals[:] = (0.0, 1.0, 0.0)
            return
        face_normals = np.cross(
            self.vertices[triangles[:, 1]] - self.vertices[triangles[:, 0]],
            self.vertices[triangles[:, 2]] - self.vertices[triangles[:, 0]],
        )
        for corner in range(3):
            np.add.at(self.normals, triangles[:, corner], face_normals)
        lengths = np.linalg.norm(self.normals, axis=1)
        normal = lengths > 1e-7
        self.normals[normal] /= lengths[normal, None]
        self.normals[~normal] = (0.0, 1.0, 0.0)

    def _advance_animation(self):
        self.animation_after_job = None
        if self.animation_frames is None or len(self.animation_frames) == 0:
            return
        self.animation_frame_index = (
            self.animation_frame_index + 1
        ) % len(self.animation_frames)
        self.vertices = self.animation_frames[self.animation_frame_index].copy()
        if self.animation_index_frames is not None:
            self.indices = self.animation_index_frames[
                self.animation_frame_index
            ].copy()
            stride = 2 if self.primitive_mode == "lines" else 3
            self.faces = self.indices.reshape(-1, stride)
        self._compute_normals_fast()
        self.redraw()
        self.animation_after_job = self.after(
            self.animation_interval_ms, self._advance_animation
        )

    def stop_animation(self):
        if self.animation_after_job is not None:
            try:
                self.after_cancel(self.animation_after_job)
            except Exception:
                pass
        self.animation_after_job = None
        self.animation_frames = None
        self.animation_index_frames = None
        self.animation_frame_index = 0

    def destroy(self):
        self.stop_animation()
        super().destroy()

    def compute_normals(self):
        """计算顶点法线"""
        if len(self.vertices) == 0:
            return
        
        self.normals = np.zeros_like(self.vertices, dtype=np.float32)
        
        for face in self.faces:
            if len(face) >= 3:
                try:
                    i0, i1, i2 = face[0], face[1], face[2]
                    if i0 < len(self.vertices) and i1 < len(self.vertices) and i2 < len(self.vertices):
                        v0, v1, v2 = self.vertices[i0], self.vertices[i1], self.vertices[i2]
                        edge1 = v1 - v0
                        edge2 = v2 - v0
                        normal = np.cross(edge1, edge2)
                        norm_len = np.linalg.norm(normal)
                        if norm_len > 1e-6:
                            normal = normal / norm_len
                            for idx in face:
                                if idx < len(self.normals):
                                    self.normals[idx] += normal
                except:
                    pass
        
        # 归一化
        for i in range(len(self.normals)):
            norm_len = np.linalg.norm(self.normals[i])
            if norm_len > 1e-6:
                self.normals[i] = self.normals[i] / norm_len
            else:
                self.normals[i] = np.array([0, 1, 0], dtype=np.float32)
    
    def _draw_indexed_mesh(self):
        """Submit the whole mesh in one OpenGL call instead of Python loops."""
        glEnableClientState(GL_VERTEX_ARRAY)
        use_normals = self.primitive_mode != "lines"
        if use_normals:
            glEnableClientState(GL_NORMAL_ARRAY)
        try:
            glVertexPointer(3, GL_FLOAT, 0, self.vertices)
            if use_normals:
                glNormalPointer(GL_FLOAT, 0, self.normals)
            glDrawElements(
                GL_LINES if self.primitive_mode == "lines" else GL_TRIANGLES,
                self.indices.size, GL_UNSIGNED_INT, self.indices
            )
        finally:
            if use_normals:
                glDisableClientState(GL_NORMAL_ARRAY)
            glDisableClientState(GL_VERTEX_ARRAY)

    def redraw(self):
        """重绘3D模型 - 现代化渲染"""
        if self.is_loading or len(self.vertices) == 0 or len(self.faces) == 0:
            return
        
        try:
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glLoadIdentity()
            
            # 应用变换
            glTranslatef(self.trans_x / 80, -self.trans_y / 80, self.zoom_factor)
            glRotatef(self.angle_x, 1, 0, 0)
            glRotatef(self.angle_y, 0, 1, 0)
            glTranslatef(-self.center[0], -self.center[1], -self.center[2])

            if self.primitive_mode == "lines":
                glDisable(GL_LIGHTING)
                glLineWidth(2.4)
                glColor3f(*VIEWER_COLORS["wireframe"])
                self._draw_indexed_mesh()
                glLineWidth(1.0)
                glEnable(GL_LIGHTING)
                self.tkSwapBuffers()
                return

            # 绘制实体模型
            if self.wireframe_mode != 1:
                glEnable(GL_LIGHTING)
                glEnable(GL_POLYGON_OFFSET_FILL)
                glPolygonOffset(1.0, 1.0)
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
                glColor3f(*VIEWER_COLORS["model"])
                
                self._draw_indexed_mesh()
                glDisable(GL_POLYGON_OFFSET_FILL)
            
            # 绘制线框
            if self.wireframe_mode in [1, 2]:
                glDisable(GL_LIGHTING)
                glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
                glLineWidth(1.0)
                glColor3f(*VIEWER_COLORS["wireframe"])
                
                self._draw_indexed_mesh()
                glEnable(GL_LIGHTING)
            
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
            self.tkSwapBuffers()
            
        except Exception:
            pass
    
    # ========== 鼠标交互事件 ==========
    
    def on_mouse_down(self, event):
        """鼠标按下"""
        self.last_x, self.last_y = event.x, event.y
        self.ctrl_pressed = False
    
    def on_ctrl_mouse_down(self, event):
        """Ctrl+鼠标按下 - 平移模式"""
        self.last_x, self.last_y = event.x, event.y
        self.ctrl_pressed = True
    
    def on_mouse_drag(self, event):
        """鼠标拖动 - 旋转"""
        dx = event.x - self.last_x
        dy = event.y - self.last_y
        self.angle_y += dx * 0.5
        self.angle_x += dy * 0.5
        self.last_x, self.last_y = event.x, event.y
        self.redraw()
    
    def on_pan_drag(self, event):
        """平移拖动"""
        dx = event.x - self.last_x
        dy = event.y - self.last_y
        self.trans_x += dx
        self.trans_y += dy
        self.last_x, self.last_y = event.x, event.y
        self.redraw()
    
    def on_mouse_up(self, event):
        """鼠标释放"""
        self.ctrl_pressed = False
    
    def on_zoom(self, event):
        """滚轮缩放"""
        # Windows
        delta = event.delta / 120
        self.zoom_factor += delta * abs(self.zoom_factor) * 0.1
        # 限制缩放范围
        self.zoom_factor = max(min(self.zoom_factor, -0.1), -10000)
        self.redraw()
    
    def on_zoom_linux(self, event, direction):
        """Linux滚轮缩放"""
        self.zoom_factor += direction * abs(self.zoom_factor) * 0.1
        self.zoom_factor = max(min(self.zoom_factor, -0.1), -10000)
        self.redraw()
    
    def toggle_wireframe(self, event=None):
        """切换线框模式"""
        self.wireframe_mode = (self.wireframe_mode + 1) % 3
        self.redraw()
        return "break"  # 阻止事件传播
    
    def reset_view(self):
        """重置视角"""
        self.angle_x, self.angle_y = 25, -35
        self.trans_x, self.trans_y = 0, 0
        if len(self.vertices) > 0:
            self.zoom_factor = self._calculate_fit_zoom()
        else:
            self.zoom_factor = -5
        self.redraw()

    def save_render_screenshot(self, path):
        """Save the actual OpenGL front buffer, including embedded previews.

        Desktop screen capture APIs commonly return a blank rectangle for a
        child OpenGL surface. Reading the viewer framebuffer makes automated
        visual regression artifacts deterministic and useful.
        """
        self.update_idletasks()
        self.tkMakeCurrent()
        self.redraw()
        width = max(1, int(self.winfo_width()))
        height = max(1, int(self.winfo_height()))
        glPixelStorei(GL_PACK_ALIGNMENT, 1)
        captures = []
        for buffer_name in (GL_FRONT, GL_BACK):
            try:
                glReadBuffer(buffer_name)
                pixels = glReadPixels(
                    0, 0, width, height, GL_RGB, GL_UNSIGNED_BYTE
                )
                sample = np.frombuffer(pixels, dtype=np.uint8)
                captures.append((
                    float(sample.std()) if sample.size else -1.0,
                    pixels,
                ))
            except Exception:
                continue
        if not captures:
            raise RuntimeError("OpenGL framebuffer could not be read")
        _score, pixels = max(captures, key=lambda item: item[0])
        image = Image.frombytes("RGB", (width, height), pixels)
        image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        image.save(path)
        return path
