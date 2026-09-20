"""
环境扫描进度弹窗
- 新建 NAS 连接首次连接时弹出，后台扫描环境信息
- 进度条分步显示：Node 环境 → 网站目录 → Node 进程 → 端口 → 访问地址
- 完成后展示找到的配置，确定后由调用方保存到连接
"""
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QProgressBar, QPushButton, QPlainTextEdit,
    QHBoxLayout, QMessageBox,
)

from scan import EnvScanner


class ScanThread(QThread):
    progress = Signal(int, int, str)  # step, total, msg
    done = Signal(dict)
    fail = Signal(str)

    def __init__(self, ssh):
        super().__init__()
        self.ssh = ssh

    def run(self):
        try:
            scanner = EnvScanner(self.ssh)
            result = scanner.scan(self.progress.emit)
            self.done.emit(result)
        except Exception as e:
            self.fail.emit(str(e))


class ScanDialog(QDialog):
    def __init__(self, ssh, parent=None):
        super().__init__(parent)
        self.ssh = ssh
        self.result = None
        self.setWindowTitle("环境扫描")
        self.setMinimumWidth(480)
        self._build()
        self._start()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(10)

        title = QLabel("正在扫描 NAS 环境")
        title.setStyleSheet("font-size:15px; font-weight:bold; color:#1a1a2e;")
        lay.addWidget(title)

        tip = QLabel("首次使用需要扫描一次，自动找到网站目录、Node 项目、服务端口等。\n扫描结果会保存，以后连接直接用，不再重复扫描。")
        tip.setWordWrap(True)
        tip.setStyleSheet("font-size:12px; color:#666;")
        lay.addWidget(tip)

        self.step_lbl = QLabel("准备开始...")
        self.step_lbl.setStyleSheet("font-size:12px; color:#555;")
        lay.addWidget(self.step_lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        lay.addWidget(self.bar)

        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setVisible(False)
        self.summary.setFixedHeight(180)
        lay.addWidget(self.summary)

        btns = QHBoxLayout()
        btns.addStretch()
        self.ok_btn = QPushButton("确定")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self.accept)
        btns.addWidget(self.ok_btn)
        lay.addLayout(btns)

    def _start(self):
        self._thread = ScanThread(self.ssh)
        self._thread.progress.connect(self._on_progress)
        self._thread.done.connect(self._on_done)
        self._thread.fail.connect(self._on_fail)
        self._thread.start()

    def _on_progress(self, step, total, msg):
        self.step_lbl.setText(f"[{step}/{total}] {msg}")
        self.bar.setValue(int(step / total * 100))

    def _on_done(self, result):
        self.result = result
        self.bar.setValue(100)
        sites = result.get('sites') or []
        lines = []
        lines.append(f"Node 路径: {result.get('node_path') or '(未找到)'}")
        lines.append(f"Node 版本: {result.get('node_ver') or '(未知)'}")
        lines.append(f"")
        lines.append(f"发现 {len(sites)} 个网站:")
        if sites:
            for s in sites:
                startable = "可启动" if s.get('startable') else "仅浏览"
                lines.append(f"  [{startable}] {s.get('name')}")
                lines.append(f"    目录: {s.get('web_dir')}")
                lines.append(f"    启动: {s.get('node_proc') or '(无 server.js/package.json)'}")
                if s.get('port'):
                    lines.append(f"    端口: {s.get('port')}  |  健康检查: {s.get('health_url')}")
                    lines.append(f"    访问: {s.get('web_url')}")
        else:
            lines.append("  (未找到，NAS 上可能没有 node 项目)")
        self.summary.setPlainText("\n".join(lines))
        self.summary.setVisible(True)
        if sites:
            self.step_lbl.setText("扫描完成，已找到网站，可在网页面板中启动。")
        else:
            self.step_lbl.setText("未找到网站目录（可能 NAS 上没有 node 项目）。可稍后手动配置。")
        self.ok_btn.setEnabled(True)

    def _on_fail(self, msg):
        self.step_lbl.setText(f"扫描失败: {msg}")
        self.ok_btn.setEnabled(True)
