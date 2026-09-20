"""Reusable widgets for the service manager."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel


class ClickableLabel(QLabel):
    """支持单击/双击信号的标签，带悬浮样式。"""
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, text=""):
        super().__init__(text)
        self.setStyleSheet("""
            QLabel { color: #888; font-size: 11px; }
            QLabel:hover { color: #2e7163; text-decoration: underline; }
        """)
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)
