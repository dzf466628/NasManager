# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
连接配置对话框 —— 添加 / 编辑 NAS 连接
"""
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QSpinBox, QDialogButtonBox,
    QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QMessageBox,
)

from config import Connection
from ssh_client import SSHClient


class TestThread(QThread):
    """后台测试 SSH 连接"""
    result = Signal(bool, str)

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password

    def run(self):
        try:
            client = SSHClient(self.host, self.port, self.username, self.password)
            ok = client.connect(timeout=8)
            if ok:
                r = client.run_command("uname -a", timeout=8)
                client.disconnect()
                self.result.emit(True, r.stdout.strip()[:120] if r.ok else "连接成功")
            else:
                self.result.emit(False, "连接失败，请检查地址/端口/账号密码")
        except Exception as e:
            self.result.emit(False, str(e))


class ConnectionDialog(QDialog):
    def __init__(self, parent=None, connection: Connection = None):
        super().__init__(parent)
        self.conn = connection
        self.setWindowTitle("编辑连接" if connection else "新建连接")
        self.setMinimumWidth(420)
        self._test_thread = None
        self._build_ui()
        if connection:
            self._load(connection)

    def _build_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：家里的群晖")
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("NAS 的 IP 或域名（如 192.168.1.100）")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("root")
        self.pwd_edit = QLineEdit()
        self.pwd_edit.setEchoMode(QLineEdit.Password)
        self.pwd_edit.setPlaceholderText("SSH 密码")

        form.addRow("名称：", self.name_edit)
        form.addRow("主机/IP：", self.host_edit)
        form.addRow("端口：", self.port_spin)
        form.addRow("用户名：", self.user_edit)
        form.addRow("密码：", self.pwd_edit)
        layout.addLayout(form)

        # 测试按钮
        test_row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._on_test)
        self.test_status = QLabel("")
        test_row.addWidget(self.test_btn)
        test_row.addWidget(self.test_status, 1)
        layout.addLayout(test_row)

        # 确定取消
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load(self, conn: Connection):
        self.name_edit.setText(conn.name)
        self.host_edit.setText(conn.host)
        self.port_spin.setValue(conn.port)
        self.user_edit.setText(conn.username)
        # 密码从 keyring 读取
        pwd = conn.password
        if pwd:
            self.pwd_edit.setText(pwd)

    def _on_test(self):
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "提示", "请填写主机/IP")
            return
        self.test_btn.setEnabled(False)
        self.test_status.setText("正在测试...")
        self._test_thread = TestThread(
            host, self.port_spin.value(),
            self.user_edit.text().strip() or "root",
            self.pwd_edit.text(),
        )
        self._test_thread.result.connect(self._on_test_result)
        self._test_thread.start()

    def _on_test_result(self, ok: bool, msg: str):
        self.test_btn.setEnabled(True)
        if ok:
            self.test_status.setText(f"✓ {msg}")
            self.test_status.setStyleSheet("color:#2e7d32;")
        else:
            self.test_status.setText(f"✗ {msg}")
            self.test_status.setStyleSheet("color:#c62828;")

    def _on_accept(self):
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写连接名称")
            return
        if not self.host_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写主机/IP")
            return
        self.accept()

    def get_connection(self) -> Connection:
        if self.conn:
            self.conn.name = self.name_edit.text().strip()
            self.conn.host = self.host_edit.text().strip()
            self.conn.port = self.port_spin.value()
            self.conn.username = self.user_edit.text().strip() or "root"
            self.conn.password = self.pwd_edit.text()
            return self.conn
        return Connection(
            name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.user_edit.text().strip() or "root",
            password=self.pwd_edit.text(),
        )
