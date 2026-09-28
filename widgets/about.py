# -*- coding: utf-8 -*-
# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""关于 / 帮助 声明对话框：软件信息、GPL v3 声明、第三方开源组件、联系方式。"""
import os
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication, QIcon, QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QApplication,
)

from app_paths import resource_path

APP_NAME = "NAS 管理器"
APP_EN = "NasManager"

# 一句话描述软件是干啥的
APP_DESC = (
    "群晖 / Linux NAS 的 Windows 桌面管理工具——"
    "服务启停、网站与反代托管、文件浏览编辑、端口查看、内置终端，一站式远程运维。"
)

QQ = "3140992714"
WEBSITE = "https://duadu.cc"

# 第三方开源组件：(名称, 许可证, 作者/机构)
COMPONENTS = [
    ("PySide6 (Qt for Python)", "LGPL v3", "Qt for Python Team / The Qt Company"),
    ("paramiko", "LGPL v2.1", "Jeff Forcier"),
    ("keyring", "MIT License", "Python keyring contributors"),
    ("cryptography", "Apache-2.0 / BSD-3-Clause", "Python Cryptographic Authority (PyCA)"),
]

ABOUT_STYLE = """
QDialog#AboutDialog { background: #ffffff; }
QLabel { color: #2b2f36; }
QLabel#AppName { color: #2e7163; font-size: 20px; font-weight: bold; }
QLabel#Version { color: #ffffff; background: #2e7163; border-radius: 8px;
                 padding: 2px 10px; font-size: 12px; font-weight: bold; }
QLabel#Section { color: #2e7163; font-size: 12px; font-weight: bold; }
QLabel#Body { color: #444a52; font-size: 12px; line-height: 1.5; }
QLabel#Comp { color: #444a52; font-size: 12px; }
QLabel#Link { color: #2e7163; font-size: 12px; text-decoration: underline; }
QLabel#Link:hover { color: #399d87; }
QPushButton#OkBtn {
    background: #2e7163; color: #fff; border: none; border-radius: 6px;
    padding: 7px 28px; font-size: 12px; font-weight: bold;
}
QPushButton#OkBtn:hover { background: #399d87; }
QFrame#Sep { background: #e3e7ea; max-height: 1px; }
"""


class AboutDialog(QDialog):
    """关于 / 帮助声明窗口。"""

    def __init__(self, version: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("AboutDialog")
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.setStyleSheet(ABOUT_STYLE)
        self.setFixedWidth(560)
        self._build(version)

    def _build(self, version: str):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 24, 26, 20)
        lay.setSpacing(10)

        # 顶部：图标 + 名称 + 版本
        top = QHBoxLayout()
        top.setSpacing(12)
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(48, 48)
        ico = resource_path("assets/app.ico")
        if ico and os.path.exists(str(ico)):
            # 用 QIcon 让其自动挑选 ico 中最接近的高清尺寸，并按屏幕 DPR 渲染，避免放大模糊
            icon = QIcon(str(ico))
            screen = self.screen() or QApplication.primaryScreen()
            dpr = screen.devicePixelRatio() if screen else 1.0
            pm = icon.pixmap(int(48 * dpr), int(48 * dpr))
            pm.setDevicePixelRatio(dpr)
            icon_lbl.setPixmap(pm)
        else:
            icon_lbl.setText("🗄️")
            icon_lbl.setStyleSheet("font-size:34px;")
        top.addWidget(icon_lbl)

        name_box = QVBoxLayout()
        name_box.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        name_lbl = QLabel(APP_NAME)
        name_lbl.setObjectName("AppName")
        title_row.addWidget(name_lbl)
        if version:
            ver_lbl = QLabel(version)
            ver_lbl.setObjectName("Version")
            title_row.addWidget(ver_lbl)
        title_row.addStretch()
        name_box.addLayout(title_row)
        en_lbl = QLabel(APP_EN)
        en_lbl.setStyleSheet("color:#8a9099; font-size:11px;")
        name_box.addWidget(en_lbl)
        top.addLayout(name_box)
        lay.addLayout(top)

        # 一句话描述
        desc = QLabel(APP_DESC)
        desc.setObjectName("Body")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        lay.addWidget(self._sep())

        # 开源声明
        sec1 = QLabel("开源许可")
        sec1.setObjectName("Section")
        lay.addWidget(sec1)
        lic = QLabel(
            "本软件以 <b>GNU GPL v3</b> 开源，你可以自由使用、修改和再分发。<br>"
            "程序按“原样”提供，不附带任何担保。")
        lic.setObjectName("Body")
        lic.setWordWrap(True)
        lay.addWidget(lic)

        # 第三方组件
        sec2 = QLabel("使用的第三方开源组件")
        sec2.setObjectName("Section")
        lay.addWidget(sec2)
        for name, license_, author in COMPONENTS:
            row = QLabel(f"•&nbsp;&nbsp;<b>{name}</b> — {license_} — {author}")
            row.setObjectName("Comp")
            row.setTextFormat(Qt.RichText)
            row.setWordWrap(True)
            lay.addWidget(row)

        lay.addWidget(self._sep())

        # 联系方式
        contact = QHBoxLayout()
        contact.setSpacing(18)
        qq_btn = QLabel(f"📧 联系 QQ：{QQ}（点击复制）")
        qq_btn.setObjectName("Link")
        qq_btn.setCursor(Qt.PointingHandCursor)
        qq_btn.mousePressEvent = lambda e: self._copy_qq()
        contact.addWidget(qq_btn)
        web_btn = QLabel(f"🌐 {WEBSITE}")
        web_btn.setObjectName("Link")
        web_btn.setCursor(Qt.PointingHandCursor)
        web_btn.mousePressEvent = lambda e: QDesktopServices.openUrl(QUrl(WEBSITE))
        contact.addWidget(web_btn)
        contact.addStretch()
        lay.addLayout(contact)

        lay.addSpacing(6)

        # 确定按钮
        ok = QPushButton("确定")
        ok.setObjectName("OkBtn")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(self.accept)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(ok)
        btn_row.addStretch()
        lay.addLayout(btn_row)

    def _sep(self):
        line = QFrame()
        line.setObjectName("Sep")
        line.setFrameShape(QFrame.NoFrame)
        return line

    def _copy_qq(self):
        QGuiApplication.clipboard().setText(QQ)
        # 轻提示：临时改文字
        for w in self.findChildren(QLabel):
            if w.objectName() == "Link" and QQ in w.text() and "复制" in w.text():
                w.setText(f"✅ QQ {QQ} 已复制到剪贴板")
                from PySide6.QtCore import QTimer
                QTimer.singleShot(1500, lambda lbl=w: lbl.setText(
                    f"📧 联系 QQ：{QQ}（点击复制）"))
                break
