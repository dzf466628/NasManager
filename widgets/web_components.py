# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""Web 面板通用 Qt 组件。

组件保持原有公开属性、信号和绘制行为；主面板通过兼容别名使用它们。
"""
from PySide6.QtCore import Qt, Signal, QTimer, Property, QRect
from PySide6.QtGui import QColor, QPainter, QLinearGradient
from PySide6.QtWidgets import QLabel, QFrame, QWidget, QHBoxLayout


class ClickableLabel(QLabel):
    """支持双击编辑的文本标签。"""
    doubleClicked = Signal()

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setObjectName("EditableLbl")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("双击可编辑")

    def mouseDoubleClickEvent(self, e):
        self.doubleClicked.emit()
        super().mouseDoubleClickEvent(e)


class ColorBar(QFrame):
    """网站卡片顶部颜色条：点击选色、悬浮高光、运行中流动动画。"""
    clicked = Signal()

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self.setCursor(Qt.PointingHandCursor)
        self._color = QColor(color_hex)
        self._flowing = False
        self._offset = 0.0
        self._hover_progress = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        from PySide6.QtCore import QPropertyAnimation
        self._hover_anim = QPropertyAnimation(self, b"hover_progress")
        self._hover_anim.setDuration(350)

    def get_hover_progress(self):
        return self._hover_progress

    def set_hover_progress(self, v):
        self._hover_progress = v
        self.update()

    hover_progress = Property(float, get_hover_progress, set_hover_progress)

    def set_color(self, color_hex: str):
        self._color = QColor(color_hex)
        self.update()

    def set_flowing(self, flowing: bool):
        self._flowing = flowing
        if flowing:
            self._timer.start()
        else:
            self._timer.stop()
            self.update()

    def _light_color(self):
        light = QColor(self._color)
        light.setHsv(light.hue(), max(0, light.saturation() - 75), min(255, light.value() + 60))
        return light

    def _tick(self):
        self._offset = (self._offset + 0.015) % 1.0
        self.update()

    def enterEvent(self, e):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(1.0)
        self._hover_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover_anim.stop()
        self._hover_anim.setStartValue(self._hover_progress)
        self._hover_anim.setEndValue(0.0)
        self._hover_anim.start()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        w, h = r.width(), r.height()
        if self._flowing:
            light = self._light_color()
            start = -w + self._offset * 2 * w
            grad = QLinearGradient(start, 0, start + w, 0)
            grad.setColorAt(0, self._color)
            grad.setColorAt(0.5, light)
            grad.setColorAt(1, self._color)
            p.fillRect(r, grad)
        else:
            p.fillRect(r, self._color)
        if self._hover_progress > 0.01:
            light = self._light_color()
            inset = (w / 2) * self._hover_progress
            if inset < w / 2 - 1:
                p.fillRect(QRect(0, 0, int(w / 2 - inset), h), light)
                p.fillRect(QRect(int(w / 2 + inset), 0, int(w / 2 - inset), h), light)
        p.end()


class UrlRow(QFrame):
    """打开网页弹窗中的可点击地址行。"""
    clicked = Signal(str)

    _BADGE = {
        "ok": ("#e8f5e9", "#2e7d32"),
        "cert": ("#fff3e0", "#e65100"),
        "http": ("#fff8e1", "#b8860b"),
        "bad": ("#fdecea", "#c62828"),
        "pending": ("#eef0f3", "#9aa0a6"),
    }

    def __init__(self, tag: str, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.setObjectName("UrlRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(url)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(10)
        tag_lbl = QLabel(tag)
        tag_lbl.setObjectName("RowTag")
        tag_lbl.setFixedWidth(72)
        url_lbl = QLabel(url)
        url_lbl.setObjectName("RowUrl")
        self.badge = QLabel("检测中…")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setFixedHeight(22)
        self.badge.setMinimumWidth(96)
        self.set_result("pending", "检测中…")
        row.addWidget(tag_lbl)
        row.addWidget(url_lbl, 1)
        row.addWidget(self.badge)
        for widget in (tag_lbl, url_lbl, self.badge):
            widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_result(self, state: str, label: str = ""):
        bg, fg = self._BADGE.get(state, self._BADGE["pending"])
        self.badge.setText(label or "检测中…")
        self.badge.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:11px; "
            "padding:0 12px; font-size:11px; font-weight:600;"
        )
        explain = {
            "ok": "可正常访问",
            "cert": "能连上，但 HTTPS 证书不被浏览器信任（域名不匹配/过期/自签），浏览器会报警",
            "http": "服务器有响应但返回此状态码（如服务未启动/反代或路径不对）",
            "bad": "无法连接（服务未启动、端口未放行或网络不通）",
        }.get(state, "")
        self.setToolTip(f"{self.url}\n{explain}" if explain else self.url)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.url)
        super().mousePressEvent(e)


class SpinnerWidget(QWidget):
    """小型转圈加载动画。"""

    def __init__(self, parent=None, size: int = 14, color: str = "#2e7163"):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._color = QColor(color)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(30)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.update()

    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        r = self.rect().adjusted(2, 2, -2, -2)
        span = 40 * 16
        for i in range(8):
            color = QColor(self._color)
            color.setAlpha(45 + i * 26)
            p.setBrush(color)
            p.drawPie(r, (self._angle - i * 45) * 16, span)
        p.end()
