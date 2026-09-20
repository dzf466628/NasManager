# -*- coding: utf-8 -*-
"""统一弹窗工具：与软件深绿主题一致的样式，长文本自动换行、限制宽度避免撑满屏幕。

用法：import msg; msg.info(self, "标题", "内容"); ok = msg.question(self, "确认", "...")
外观由 main.STYLE 里的 QMessageBox 样式统一控制，本工具只负责内容布局。
"""
from PySide6.QtWidgets import QMessageBox

MAX_WIDTH = 560  # 弹窗最大宽度 px，超出自动换行


def _box(parent, icon, title, text):
    mb = QMessageBox(parent) if parent is not None else QMessageBox()
    mb.setIcon(icon)
    mb.setWindowTitle(title)
    mb.setText(text)
    # 注意：QMessageBox 没有 setWordWrap 方法（内部文本控件默认自动换行），
    # 只需限制最大宽度即可让长文本换行不撑满屏幕。
    mb.setMaximumWidth(MAX_WIDTH)
    return mb


def info(parent, title, text):
    _box(parent, QMessageBox.Information, title, text).exec()


def warn(parent, title, text):
    _box(parent, QMessageBox.Warning, title, text).exec()


def critical(parent, title, text):
    _box(parent, QMessageBox.Critical, title, text).exec()


def question(parent, title, text) -> bool:
    """返回 True=是 / False=否"""
    mb = _box(parent, QMessageBox.Question, title, text)
    mb.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    mb.setDefaultButton(QMessageBox.Yes)
    return mb.exec() == QMessageBox.Yes
