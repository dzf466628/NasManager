# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
启动动画（Splash Screen）—— 纯透明底 v3
- 透明窗口，只显示序列帧 + 下方 loading 文字，无背景卡
- 淡入：出现时 QGraphicsOpacityEffect 0→1
- 淡出：切主窗口时 QGraphicsOpacityEffect 1→0（不碰 windowOpacity，避免 Windows 原生崩溃）
- 淡出前停止所有定时器
"""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QWidget, QLabel, QVBoxLayout, QGraphicsOpacityEffect

from app_paths import resource_path

FRAME_MS = 60          # ~16fps（51 帧 × 60ms ≈ 3 秒）
DEFAULT_DURATION_MS = 3000
FADE_IN_MS = 450
FADE_OUT_MS = 450


def splash_frames() -> list:
    d = Path(resource_path("assets")) / "splash"
    if not d.exists():
        return []
    files = sorted(d.glob("f*.png"), key=lambda p: p.stem)
    return [str(p) for p in files]


class SplashScreen(QWidget):
    def __init__(self, size=300, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._size = size
        # 窗口 = 动画 + 正下方紧贴的 loading 文字（不重叠）
        self.setFixedSize(size, size + 46)

        # 内容容器（动画 + loading 文字），透明度效果作用于它
        self._container = QWidget(self)
        self._container.setFixedSize(size, size + 46)
        self._container.setStyleSheet("background:transparent;")

        # 用标准布局排列（透明窗口下比绝对定位更稳定，文字不会被吞掉）
        lay = QVBoxLayout(self._container)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._img = QLabel()
        self._img.setFixedSize(size, size)
        self._img.setStyleSheet("background:transparent;")
        lay.addWidget(self._img)

        self._loading = QLabel("loading....")
        self._loading.setFixedHeight(40)
        self._loading.setAlignment(Qt.AlignCenter)
        self._loading.setStyleSheet(
            "background:transparent; color:#2e7163; font-size:24px; font-weight:bold;"
            "font-family:'Microsoft YaHei';")
        lay.addWidget(self._loading)

        # 透明度效果（淡入/淡出都作用于内容，不碰窗口 windowOpacity）
        self._eff = QGraphicsOpacityEffect(self._container)
        self._container.setGraphicsEffect(self._eff)
        self._eff.setOpacity(0.0)

        self._fade_in = QPropertyAnimation(self._eff, b"opacity", self)
        self._fade_in.setDuration(FADE_IN_MS)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.OutCubic)

        self._fade_out = QPropertyAnimation(self._eff, b"opacity", self)
        self._fade_out.setDuration(FADE_OUT_MS)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self.close)

        # 帧
        self._frames = []
        for p in splash_frames():
            pm = QPixmap(p)
            if not pm.isNull():
                self._frames.append(pm)
        self._idx = 0
        self._centered = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._next_frame)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._centered and self.screen():
            scr = self.screen().availableGeometry()
            # 屏幕完全居中（无偏移）
            self.move((scr.width() - self.width()) // 2,
                      (scr.height() - self.height()) // 2)
            self._centered = True
        if self._frames:
            self._img.setPixmap(self._frames[0])

    def start(self):
        self._timer.start(FRAME_MS)

    def stop(self):
        if self._timer.isActive():
            self._timer.stop()

    def fade_in(self):
        self._fade_in.start()

    def _next_frame(self):
        if not self._frames:
            return
        self._idx = (self._idx + 1) % len(self._frames)
        self._img.setPixmap(self._frames[self._idx])

    def fade_out(self):
        self.stop()
        self._fade_out.start()
