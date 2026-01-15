# -*- coding: utf-8 -*-
"""
现代化3D模型预览器
支持快速刷新、Ctrl+左键旋转、滚轮缩放等交互
"""
import numpy as np
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
        self.center = np.array([0.0, 0.0, 0.0])
        
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
    
    def load_obj_data(self, obj_text):
        """快速解析并加载OBJ模型数据"""
        self.is_loading = True
        self.vertices = []
        self.faces = []
        self.normals = []
        
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
                        idx = int(p.split("/")[0]) - 1
                        face.append(idx)
                    if len(face) >= 3:
                        self.faces.append(face)
            
            if len(self.vertices) > 0:
                self.vertices = np.array(self.vertices, dtype=np.float32)
                self.center = self.vertices.mean(axis=0)
                
                # 计算模型边界并自动缩放
                model_extent = self.vertices - self.center
                model_size = np.max(np.abs(model_extent))
                if model_size > 0:
                    self.zoom_factor = -model_size * 2.5
                
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
            
            # 绘制实体模型
            if self.wireframe_mode != 1:
                glEnable(GL_LIGHTING)
                glEnable(GL_POLYGON_OFFSET_FILL)
                glPolygonOffset(1.0, 1.0)
                glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
                glColor3f(*VIEWER_COLORS["model"])
                
                glBegin(GL_TRIANGLES)
                for face in self.faces:
                    for idx in face:
                        if idx < len(self.vertices) and idx < len(self.normals):
                            glNormal3fv(self.normals[idx])
                            glVertex3fv(self.vertices[idx])
                glEnd()
                glDisable(GL_POLYGON_OFFSET_FILL)
            
            # 绘制线框
            if self.wireframe_mode in [1, 2]:
                glDisable(GL_LIGHTING)
                glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
                glLineWidth(1.0)
                glColor3f(*VIEWER_COLORS["wireframe"])
                
                glBegin(GL_TRIANGLES)
                for face in self.faces:
                    for idx in face:
                        if idx < len(self.vertices):
                            glVertex3fv(self.vertices[idx])
                glEnd()
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
            model_size = np.max(np.abs(self.vertices - self.center))
            self.zoom_factor = -model_size * 2.5
        else:
            self.zoom_factor = -5
        self.redraw()