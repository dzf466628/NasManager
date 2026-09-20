"""
内置 SSH 终端
- invoke_shell 保持交互式会话（支持 cd 等状态命令）
- 输出区 + 命令输入框 + 快捷命令栏 + 命令历史
"""
import re
import time
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QTextCursor, QKeySequence
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit, QLineEdit,
    QWidget, QSizePolicy,
)

from widgets.base import BaseWidget
from app_paths import resource_path

ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][0-9A-Ba-z]|\x1b[=>]|\r')


class ShellReaderThread(QThread):
    """持续读取 SSH shell 输出"""
    output = Signal(str)

    def __init__(self, channel):
        super().__init__()
        self.channel = channel
        self._running = False

    def run(self):
        self._running = True
        buf = ""
        while self._running:
            try:
                if self.channel.recv_ready():
                    data = self.channel.recv(4096).decode("utf-8", errors="replace")
                    buf += data
                    if "\n" in buf or len(buf) > 64:
                        self.output.emit(buf)
                        buf = ""
                elif self.channel.exit_status_ready():
                    if buf:
                        self.output.emit(buf)
                    break
                else:
                    time.sleep(0.05)
            except Exception:
                break
        if buf:
            self.output.emit(buf)

    def stop(self):
        self._running = False


class TerminalWidget(BaseWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.channel = None
        self.reader: ShellReaderThread | None = None
        self.history: list[str] = []
        self.history_idx = -1
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        title = QLabel("SSH 终端（命令行）")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        header.addWidget(title)
        header.addStretch()
        self.clear_btn = QPushButton("清屏")
        self.clear_btn.setStyleSheet("padding:5px 12px;")
        self.clear_btn.clicked.connect(self._clear)
        header.addWidget(self.clear_btn)
        outer.addLayout(header)

        # 快捷命令栏
        self.quick_bar = QHBoxLayout()
        self.quick_bar.addWidget(QLabel("快捷命令："))
        self._load_quick_commands()
        self.quick_bar.addStretch()
        outer.addLayout(self.quick_bar)

        # 输出区
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(QFont("Consolas, 'Courier New', monospace", 11))
        self.output.setStyleSheet(
            "QPlainTextEdit { background:#1e1e2e; color:#cdd6f4; border:1px solid #313244; border-radius:6px; padding:6px; }"
        )
        outer.addWidget(self.output, 1)

        # 输入行
        input_row = QHBoxLayout()
        self.prompt = QLabel("$ ")
        self.prompt.setStyleSheet("color:#4caf50; font-weight:bold; font-family:Consolas;")
        self.input = QLineEdit()
        self.input.setFont(QFont("Consolas, 'Courier New', monospace", 11))
        self.input.setPlaceholderText("输入命令后回车执行，↑↓ 切换历史")
        self.input.returnPressed.connect(self._send_command)
        self.input.installEventFilter(self)
        input_row.addWidget(self.prompt)
        input_row.addWidget(self.input, 1)
        outer.addLayout(input_row)

    def _load_quick_commands(self):
        presets_file = resource_path("services") / "presets.json"
        try:
            import json
            data = json.loads(presets_file.read_text(encoding="utf-8"))
            for qc in data.get("quick_commands", []):
                btn = QPushButton(qc["name"])
                btn.setStyleSheet(
                    "padding:4px 10px; font-size:11px; background:#f0f2f5; border:none; border-radius:4px;"
                )
                btn.clicked.connect(lambda checked, c=qc["cmd"]: self._run_quick(c))
                self.quick_bar.addWidget(btn)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if obj == self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_Up:
                self._history_prev()
                return True
            if key == Qt.Key_Down:
                self._history_next()
                return True
            if key == Qt.Key_C and (event.modifiers() & Qt.ControlModifier):
                # Ctrl+C 发送中断
                if self.channel:
                    self.channel.send("\x03")
                return True
            if key == Qt.Key_L and (event.modifiers() & Qt.ControlModifier):
                self._clear()
                return True
        return super().eventFilter(obj, event)

    def on_bind(self):
        self._open_shell()

    def on_show(self):
        if not self.channel and self.ssh and self.ssh.is_connected:
            self._open_shell()
        self.input.setFocus()

    def _open_shell(self):
        if not self.ssh or not self.ssh.is_connected:
            return
        try:
            if not self.ssh._ensure_connected():
                return
            transport = self.ssh._client.get_transport()
            self.channel = transport.open_session()
            self.channel.get_pty(term="xterm", width=120, height=30)
            self.channel.invoke_shell()
            self.reader = ShellReaderThread(self.channel)
            self.reader.output.connect(self._append_output)
            self.reader.start()
            self._append_output("[已连接到 SSH 终端]\n")
        except Exception as e:
            self._append_output(f"[打开终端失败: {e}]\n")

    def disconnect_ssh(self):
        if self.reader:
            self.reader.stop()
            self.reader.wait(500)
            self.reader = None
        if self.channel:
            try:
                self.channel.close()
            except Exception:
                pass
            self.channel = None

    def _send_command(self):
        cmd = self.input.text()
        if not self.channel:
            self._append_output("[终端未连接]\n")
            return
        self.channel.send(cmd + "\n")
        if cmd.strip():
            self.history.append(cmd)
            if len(self.history) > 100:
                self.history.pop(0)
        self.history_idx = len(self.history)
        self.input.clear()

    def _run_quick(self, cmd: str):
        cmd = self._resolve_cmd(cmd)
        self.input.setText(cmd)
        self._send_command()

    def _resolve_cmd(self, cmd: str) -> str:
        """替换 {web_dir}/{node_proc} 占位符为连接配置实际值（不再写死路径）"""
        if not cmd or "{" not in cmd:
            return cmd
        web_dir, node_proc = "", ""
        conn = getattr(self, "conn", None)
        if conn:
            sites = conn.sites or []
            main = next((s for s in sites if s.get("startable")), (sites[0] if sites else None))
            web_dir = conn.web_dir or (main.get("web_dir") if main else "") or ""
            node_proc = conn.node_proc or (main.get("node_proc") if main else "") or ""
        return cmd.replace("{web_dir}", web_dir).replace("{node_proc}", node_proc)

    def set_conn_info(self, conn):
        self.conn = conn

    def _append_output(self, text: str):
        # 过滤 ANSI 转义
        clean = ANSI_ESCAPE.sub("", text)
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(clean)
        self.output.moveCursor(QTextCursor.End)
        sb = self.output.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _clear(self):
        self.output.clear()

    def _history_prev(self):
        if not self.history:
            return
        self.history_idx = max(0, self.history_idx - 1)
        if self.history_idx < len(self.history):
            self.input.setText(self.history[self.history_idx])

    def _history_next(self):
        if not self.history:
            return
        self.history_idx = min(len(self.history), self.history_idx + 1)
        if self.history_idx < len(self.history):
            self.input.setText(self.history[self.history_idx])
        else:
            self.input.clear()
