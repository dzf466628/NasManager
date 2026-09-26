"""
网页服务器专项面板（多站点版）
- 每个网站一张卡片：状态/PID/运行时长/启停重启/健康检查/实时日志/打开网页
- 依赖管理：一键 npm install，完成显示"依赖完整"可重装
- 反向代理：检测/查看/配置 nginx 反代
- 开机自启：设置/取消群晖开机自启脚本
- Nginx 卡片：状态/配置测试/重载/反代列表
- 数据来自连接配置里的 sites 列表（扫描自动发现，放新网站重扫就会出现）
"""
import json
import re
import time
import base64
import ssl
import socket
import urllib.request
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QTimer, Property, QRect
from PySide6.QtGui import QFont, QTextCursor, QColor, QDesktopServices, QPainter
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QPlainTextEdit,
    QGridLayout, QMessageBox, QSizePolicy, QWidget, QScrollArea, QLineEdit,
    QInputDialog, QDialog, QApplication, QCheckBox, QFileDialog,
    QProgressBar, QProgressDialog, QRadioButton, QButtonGroup,
)

import telemetry
from widgets.base import BaseWidget
from app_paths import resource_path
from widgets import msg
from widgets.web_helpers import (
    safe_name as _helper_safe_name,
    proxy_probe_cmd as _helper_proxy_probe_cmd,
    proxy_block_uses_port as _helper_proxy_block_uses_port,
    location_blocks as _helper_location_blocks,
    nginx_t_passed as _helper_nginx_t_passed,
    PATH_EXPORT as _helper_path_export,
    PROXY_DIRS as _helper_proxy_dirs,
    health_lbl_style as _helper_health_lbl_style,
    standard_location as _helper_standard_location,
    standard_server as _helper_standard_server,
)
from widgets.web_workers import (
    LogReaderThread as _ExtractedLogReaderThread,
    StreamCommandWorker as _ExtractedStreamCommandWorker,
    InstallDepsThread as _ExtractedInstallDepsThread,
    UrlProbeThread as _ExtractedUrlProbeThread,
    RescanThread as _ExtractedRescanThread,
)
from widgets.web_components import (
    ClickableLabel as _ExtractedClickableLabel,
    ColorBar as _ExtractedColorBar,
    UrlRow as _ExtractedUrlRow,
    SpinnerWidget as _ExtractedSpinnerWidget,
)
from widgets.web_dialogs import (
    InstallProgressDialog as _ExtractedInstallProgressDialog,
    DependenciesDialog as _ExtractedDependenciesDialog,
    WebHealthDialog as _ExtractedWebHealthDialog,
)
from widgets.web_site_card import SiteCard
from widgets.web_styles import OPEN_DIALOG_STYLE


class HealthRepairWorker(QThread):
    """后台执行网页自愈的远程检查，避免阻塞检测窗口动画。"""
    finished_repair = Signal(list, str)

    def __init__(self, panel):
        super().__init__()
        self.panel = panel

    def run(self):
        actions = []
        error = ""
        try:
            self.panel._health_worker_mode = True
            self.panel._health_repair_actions = actions
            self.panel._ensure_sites_alive()
            self.panel._ensure_proxy_files()
        except Exception as e:
            error = str(e)
        finally:
            self.panel._health_worker_mode = False
        self.finished_repair.emit(actions, error)


PRESETS_FILE = resource_path("services") / "presets.json"

PANEL_STYLE = """
QFrame#WebCard {
    background:#fff; border:1px solid #e0e0e0; border-radius:10px;
}
QLabel#Dot { font-size:16px; }
QLabel#SectionTitle { font-size:14px; font-weight:bold; color:#333; }
QLabel#EditableLbl { font-size:12px; color:#555; }
QLabel#EditableLbl:hover { color:#2e7163; text-decoration:underline; font-weight:bold; }
QLabel#EditableLbl[edited="true"] { color:#2e7163; font-weight:bold; }
QPushButton#WebBtn {
    background:#f0f2f5; border:none; border-radius:5px; padding:5px 12px;
    font-size:12px; color:#333;
}
QPushButton#WebBtn:hover { background:#e0e3ea; }
QPushButton#WebBtn:disabled { color:#bbb; }
QPushButton#PrimaryBtn {
    background:#4caf50; border:none; border-radius:5px; padding:5px 12px;
    font-size:12px; color:#fff;
}
QPushButton#PrimaryBtn:hover { background:#43a047; }
QPushButton#DangerBtn {
    background:#fef0f0; border:none; border-radius:5px; padding:5px 12px;
    font-size:12px; color:#c62828;
}
QPushButton#DangerBtn:hover { background:#fde0e0; }
QPushButton#MetaBtn {
    background:#eef3ff; border:none; border-radius:5px; padding:4px 10px;
    font-size:11px; color:#3a5bd9;
}
QPushButton#MetaBtn:hover { background:#dfe8ff; }
QPushButton#MetaBtn:disabled { color:#aaa; background:#f5f5f5; }
QPlainTextEdit#LogView {
    background:#1e1e2e; color:#a6e3a1; font-family: Consolas, 'Courier New', monospace;
    font-size:12px; border:1px solid #313244; border-radius:6px;
}
QScrollArea { border:none; background:transparent; }
QScrollArea > QWidget > QWidget { background:transparent; }
"""

# 「打开网页」多地址选择弹窗样式（与主窗口墨绿/白卡片风格统一）


# 群晖常见 node/npm 路径
PATH_EXPORT = ("export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin:"
               "/volume1/@appstore/Node.js/usr/local/bin:/var/packages/Node.js/target/usr/local/bin; ")

# 反代/nginx 配置动态搜索目录（覆盖群晖 Web Station 全部配置位置，不写死单一目录）
PROXY_DIRS = ("/etc/nginx/conf.d/ /etc/nginx/sites-enabled/ "
              "/usr/local/etc/nginx/conf.d/ /usr/local/etc/nginx/sites-enabled/ "
              "/usr/local/etc/nginx/sites-available/")

# 「主页」标签状态配色：(常态颜色, 悬浮颜色) —— 不同网站状态用不同颜色，
# 悬浮色也随状态区分（深一档的同色系），不再一律用主题绿
class LogReaderThread(QThread):
    """通过 SSH channel 实时读取日志（tail -f）"""
    line_received = Signal(str)
    error = Signal(str)

    def __init__(self, ssh, remote_path: str, lines: int = 50):
        super().__init__()
        self.ssh = ssh
        self.remote_path = remote_path
        self.lines = lines
        self._running = False
        self._channel = None

    def run(self):
        self._running = True
        ch = self.ssh.open_channel(f"tail -n {self.lines} -f {self.remote_path}")
        if ch is None:
            self.error.emit("无法打开日志通道")
            return
        self._channel = ch
        buf = ""
        while self._running:
            try:
                if ch.recv_ready():
                    data = ch.recv(4096).decode("utf-8", errors="replace")
                    buf += data
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        self.line_received.emit(line)
                elif ch.exit_status_ready():
                    break
                else:
                    time.sleep(0.2)
            except Exception as e:
                self.error.emit(str(e))
                break
        try:
            ch.close()
        except Exception:
            pass

    def stop(self):
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass


class StreamCommandWorker(QThread):
    """通过 SSH channel 执行命令并实时流式输出每一行（替代 run_cmd 的一次性返回）"""
    line_received = Signal(str)
    finished_cmd = Signal(int)  # 退出码，-1 表示通道异常
    error = Signal(str)

    def __init__(self, ssh, cmd: str, sudo: bool = False):
        super().__init__()
        self.ssh = ssh
        self.cmd = cmd
        self.sudo = sudo
        self._running = False
        self._channel = None

    def run(self):
        self._running = True
        ch = self.ssh.open_channel(self.cmd, sudo=self.sudo)
        if ch is None:
            self.error.emit("无法打开 SSH 通道")
            self.finished_cmd.emit(-1)
            return
        self._channel = ch
        buf = ""
        exit_code = -1
        while self._running:
            try:
                got_data = False
                if ch.recv_ready():
                    data = ch.recv(4096).decode("utf-8", errors="replace")
                    buf += data
                    got_data = True
                if ch.recv_stderr_ready():
                    data = ch.recv_stderr(4096).decode("utf-8", errors="replace")
                    buf += data
                    got_data = True
                if got_data:
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        self.line_received.emit(line.rstrip("\r"))
                elif ch.exit_status_ready():
                    while ch.recv_ready():
                        data = ch.recv(4096).decode("utf-8", errors="replace")
                        buf += data
                    while ch.recv_stderr_ready():
                        data = ch.recv_stderr(4096).decode("utf-8", errors="replace")
                        buf += data
                    if buf.strip():
                        self.line_received.emit(buf.rstrip("\r\n"))
                    try:
                        exit_code = ch.recv_exit_status()
                    except Exception:
                        pass
                    break
                else:
                    time.sleep(0.2)
            except Exception as e:
                self.error.emit(str(e))
                break
        try:
            ch.close()
        except Exception:
            pass
        self.finished_cmd.emit(exit_code)

    def stop(self):
        self._running = False
        if self._channel:
            try:
                self._channel.close()
            except Exception:
                pass


class InstallProgressDialog(QDialog):
    """Node.js 安装进度弹窗：进度条 + 实时日志输出，出错也能直接看到原因"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("安装 Node.js")
        self.setFixedSize(580, 400)
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        self.label = QLabel("正在通过 synopkg 安装 Node.js，下方实时显示输出...")
        self.label.setStyleSheet("font-size:13px; font-weight:bold;")
        lay.addWidget(self.label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # 不确定模式，滚动条表示运行中
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:Consolas,monospace; font-size:12px;")
        lay.addWidget(self.log_view, 1)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        btn_lay.addWidget(self.close_btn)
        lay.addLayout(btn_lay)

    def append_log(self, line: str):
        """追加一行日志并自动滚动到底部"""
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_finished(self, success: bool):
        """安装结束，停止进度条并显示结果"""
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if success:
            self.label.setText("✅ Node.js 安装成功！")
            self.label.setStyleSheet("font-size:13px; font-weight:bold; color:#2e7d32;")
        else:
            self.label.setText("❌ 安装失败，请查看上方日志定位原因")
            self.label.setStyleSheet("font-size:13px; font-weight:bold; color:#c62828;")
        self.close_btn.setEnabled(True)


class InstallDepsThread(QThread):
    """后台跑 npm install（可指定包名单独重装）"""
    done = Signal(bool, str)

    def __init__(self, ssh, web_dir: str, pkgs: list = None, path_export: str = None):
        super().__init__()
        self.ssh = ssh
        self.web_dir = web_dir
        self.pkgs = pkgs or []
        self.path_export = path_export or PATH_EXPORT

    def run(self):
        try:
            shq = "'" + self.web_dir.replace("'", "'\\''") + "'"
            # HOME=/tmp：群晖用户 home 目录不存在，npm 写缓存会 ENOTDIR
            base = "export HOME=/tmp; " + self.path_export
            if self.pkgs:
                pk = " ".join(p for p in self.pkgs if p)
                cmd = (base + f"cd {shq} && npm install {pk} --no-audit --no-fund 2>&1 | tail -20")
            else:
                cmd = (base + f"cd {shq} && npm install --no-audit --no-fund 2>&1 | tail -20")
            r = self.ssh.run_command(cmd, timeout=300)
            out = (r.stdout + r.stderr).strip()
            # npm install 成功：exit 0，或输出里含 added/up to date
            ok = r.exit_code == 0 or "added" in out.lower() or "up to date" in out.lower()
            self.done.emit(ok, out[-600:])
        except Exception as e:
            self.done.emit(False, str(e))


class DependenciesDialog(QDialog):
    """依赖管理弹窗：列出所有依赖，可单独选择重装"""
    def __init__(self, panel: "WebPanelWidget", card: "SiteCard"):
        super().__init__(panel)
        self.panel = panel
        self.card = card
        self.web_dir = (card.site.get("web_dir") or "").strip()
        self.deps = []  # [(name, version, installed)]
        self.checkboxes = {}
        self.setWindowTitle(f"依赖管理 - {card.site.get('name') or '网站'}")
        self.setMinimumSize(480, 420)
        self._build()
        self._load()

    @staticmethod
    def _shq(s: str) -> str:
        return "'" + s.replace("'", "'\\''") + "'"

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        self.info_lbl = QLabel("读取依赖中...")
        self.info_lbl.setStyleSheet("font-size:12px; color:#555;")
        lay.addWidget(self.info_lbl)

        # 依赖列表（滚动）
        from PySide6.QtWidgets import QScrollArea
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(4, 4, 4, 4)
        self.list_lay.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_box)
        lay.addWidget(scroll, 1)

        # 底部按钮
        btns = QHBoxLayout()
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(self._toggle_all)
        self.reinstall_all_btn = QPushButton("全部重装")
        self.reinstall_all_btn.setStyleSheet("background:#4caf50; color:#fff; border:none; border-radius:5px; padding:6px 14px;")
        self.reinstall_all_btn.clicked.connect(lambda: self._start_install(None))
        self.reinstall_sel_btn = QPushButton("重装所选")
        self.reinstall_sel_btn.setStyleSheet("background:#2196f3; color:#fff; border:none; border-radius:5px; padding:6px 14px;")
        self.reinstall_sel_btn.clicked.connect(self._reinstall_selected)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btns.addWidget(self.select_all_btn)
        btns.addStretch()
        btns.addWidget(self.reinstall_sel_btn)
        btns.addWidget(self.reinstall_all_btn)
        btns.addWidget(close_btn)
        lay.addLayout(btns)

    def _load(self):
        if not self.web_dir:
            self.info_lbl.setText("该网站没有目录信息")
            return
        self.panel.run_cmd(f"cat {self._shq(self.web_dir)}/package.json 2>/dev/null",
                           self._on_loaded, timeout=10)

    def _on_loaded(self, r):
        import json
        try:
            data = json.loads((r.stdout or "").strip() or "{}")
        except Exception:
            data = {}
        deps = {}
        deps.update(data.get("dependencies") or {})
        deps.update(data.get("devDependencies") or {})
        self.deps = [(name, ver, False) for name, ver in deps.items()]
        if not self.deps:
            self.info_lbl.setText("零依赖网站：package.json 里没有需要安装的依赖（不需要 npm install）。")
            self.select_all_btn.setEnabled(False)
            self.reinstall_all_btn.setEnabled(False)
            self.reinstall_sel_btn.setEnabled(False)
            return
        # 批量检测哪些已安装
        checks = "; ".join(
            f"test -d {self._shq(self.web_dir)}/node_modules/{name} && echo '{name}:yes' || echo '{name}:no'"
            for name, _, _ in self.deps)
        self.panel.run_cmd(checks, self._on_installed, timeout=15)

    def _on_installed(self, r):
        status = {}
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if ":" in line:
                n, st = line.rsplit(":", 1)
                status[n.strip()] = (st.strip() == "yes")
        self.deps = [(n, v, status.get(n, False)) for n, v, _ in self.deps]
        self._render()

    def _render(self):
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()
        self.checkboxes = {}
        if not self.deps:
            return
        self.info_lbl.setText(f"共 {len(self.deps)} 个依赖，勾选后可单独重装：")
        for name, ver, installed in self.deps:
            cb = QCheckBox(f"{name}  {ver or ''}   {'[已安装]' if installed else '[未安装]'}")
            cb.setStyleSheet("font-size:12px; color:#333;" + (" color:#2e7d32;" if installed else " color:#e65100;"))
            self.list_lay.addWidget(cb)
            self.checkboxes[name] = cb
        self.list_lay.addStretch()

    def _toggle_all(self):
        checked = not all(cb.isChecked() for cb in self.checkboxes.values())
        for cb in self.checkboxes.values():
            cb.setChecked(checked)

    def _reinstall_selected(self):
        pkgs = [n for n, cb in self.checkboxes.items() if cb.isChecked()]
        if not pkgs:
            QMessageBox.information(self, "提示", "请先勾选要重装的依赖")
            return
        self._start_install(pkgs)

    def _start_install(self, pkgs):
        if not self.panel.ssh or not self.panel.ssh.is_connected:
            QMessageBox.warning(self, "提示", "未连接")
            return
        self._set_busy(True)
        self.info_lbl.setText("正在安装依赖，请稍候（可能需要几分钟）...")
        th = InstallDepsThread(self.panel.ssh, self.web_dir, pkgs,
                               path_export=self.panel._node_path_export())
        th.done.connect(self._on_install_done)
        self.panel._track(th)
        th.start()

    def _on_install_done(self, ok: bool, out: str):
        self._set_busy(False)
        if ok:
            self.info_lbl.setText("✅ 安装完成，正在刷新状态...")
            QMessageBox.information(self, "完成", "依赖安装完成。")
        else:
            self.info_lbl.setText("安装失败，见下方提示")
            msg.warn(self, "安装失败", out[:600] or "安装失败")
        # 刷新列表状态
        self.panel._check_site_meta(self.card)
        self._load()

    def _set_busy(self, busy: bool):
        self.select_all_btn.setEnabled(not busy)
        self.reinstall_all_btn.setEnabled(not busy)
        self.reinstall_sel_btn.setEnabled(not busy)


class ClickableLabel(QLabel):
    """支持双击编辑的文本标签：hover 悬浮变色/下划线提示可点击，双击发信号"""
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
    """网站卡片顶部颜色条：点击选色，悬浮浅色向中间汇聚，运行中流动动画"""
    clicked = Signal()

    def __init__(self, color_hex: str, parent=None):
        super().__init__(parent)
        self.setFixedHeight(5)
        self.setCursor(Qt.PointingHandCursor)
        self._color = QColor(color_hex)
        self._flowing = False
        self._offset = 0.0
        self._hover_progress = 0.0  # 0=无悬浮高光，1=浅色汇聚到中间
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
        """流动/悬浮用的浅色段（比主色明显更亮）"""
        light = QColor(self._color)
        light.setHsv(light.hue(), max(0, light.saturation() - 75),
                     min(255, light.value() + 60))
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
        from PySide6.QtGui import QPainter, QLinearGradient
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        w, h = r.width(), r.height()
        if self._flowing:
            # 运行中：当前色与浅色横向流动
            light = self._light_color()
            start = -w + self._offset * 2 * w
            grad = QLinearGradient(start, 0, start + w, 0)
            grad.setColorAt(0, self._color)
            grad.setColorAt(0.5, light)
            grad.setColorAt(1, self._color)
            p.fillRect(r, grad)
        else:
            p.fillRect(r, self._color)
        # 悬浮：浅色从两侧向中间汇聚
        if self._hover_progress > 0.01:
            light = self._light_color()
            prog = self._hover_progress
            inset = (w / 2) * prog  # 两侧各向内收缩的距离
            if inset < w / 2 - 1:
                # 左侧汇聚段
                p.fillRect(QRect(0, 0, int(w / 2 - inset), h), light)
                # 右侧汇聚段
                p.fillRect(QRect(int(w / 2 + inset), 0, int(w / 2 - inset), h), light)
        p.end()


class UrlProbeThread(QThread):
    """从本机（运行软件的电脑）并发实测一批地址的连通性。
    浏览器也在本机打开，所以这里测的结果就是"点哪个能打开"。"""
    one_result = Signal(int, str, str)  # 序号, 状态(ok/warn/bad/pending), 说明文字

    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self.urls = list(urls)
        self._stop = False

    def stop(self):
        self._stop = True

    @staticmethod
    def _cert_status(host: str, port: int, timeout: float = 4.0):
        """严格 TLS 握手校验证书（模拟浏览器）。
        True=证书受信任且域名匹配；False=握手成功但证书有问题（过期/自签/不匹配）；
        None=根本连不上（交给后续 HTTP 探测判为不可达）。"""
        ctx = ssl.create_default_context()
        try:
            with socket.create_connection((host, port), timeout=timeout) as raw:
                with ctx.wrap_socket(raw, server_hostname=host):
                    return True
        except (ssl.SSLError, ssl.CertificateError):
            return False
        except Exception:
            return None

    @staticmethod
    def probe_one(url: str):
        """同步测单个地址，返回 (state, label)。state 用于配色，label 是徽章短文案。
        状态细分：ok 可访问 / cert 证书异常 / http 服务器返回4xx5xx / bad 连不上。
        关键：404 等 HTTP 错误不得标成「证书异常」。"""
        cert = None  # True 可信 / False 证书异常 / None 非https或连不上
        if url.startswith("https://"):
            try:
                p = urllib.parse.urlsplit(url)
                if p.hostname:
                    cert = UrlProbeThread._cert_status(p.hostname, p.port or 443)
            except Exception:
                cert = None
        ctx = ssl._create_unverified_context()  # HTTP 状态码照拿，证书单独由 cert 判断
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NasManager"})
            with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
                code = getattr(resp, "status", 0) or 200
                if not (200 <= code < 400):
                    return "http", f"HTTP {code}"
                if cert is False:
                    return "cert", "证书异常"
                return "ok", "可访问"
        except urllib.error.HTTPError as e:  # 服务器有响应但状态码非 2xx（如 404/502）
            if cert is False:
                return "cert", f"HTTP {e.code}·证书异常"
            return "http", f"HTTP {e.code}"
        except (socket.timeout, TimeoutError):
            return "bad", "超时"
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                return "bad", "超时"
            return "bad", "不可达"
        except OSError:
            return "bad", "不可达"
        except Exception:
            return "bad", "失败"

    def run(self):
        if not self.urls:
            return
        workers = max(1, min(8, len(self.urls)))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.probe_one, u): i for i, u in enumerate(self.urls)}
            for fut in as_completed(futs):
                if self._stop:
                    break
                idx = futs[fut]
                try:
                    state, detail = fut.result()
                except Exception:
                    state, detail = "bad", "失败"
                self.one_result.emit(idx, state, detail)


class UrlRow(QFrame):
    """打开网页弹窗里的一行：[来源标签] 地址 ............ 状态徽章，整行可点。
    子 Label 全部设 WA_TransparentForMouseEvents，让鼠标事件落到整行（否则点文字不触发）。"""
    clicked = Signal(str)

    _BADGE = {
        "ok": ("#e8f5e9", "#2e7d32"),       # 绿：可访问
        "cert": ("#fff3e0", "#e65100"),     # 橙：证书异常
        "http": ("#fff8e1", "#b8860b"),     # 黄橙：4xx/5xx（如 404）
        "bad": ("#fdecea", "#c62828"),      # 红：连不上
        "pending": ("#eef0f3", "#9aa0a6"),  # 灰：检测中
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
        # 关键：子控件不拦截鼠标，事件穿透到整行 QFrame
        for w in (tag_lbl, url_lbl, self.badge):
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def set_result(self, state: str, label: str = ""):
        bg, fg = self._BADGE.get(state, self._BADGE["pending"])
        self.badge.setText(label or "检测中…")
        self.badge.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:11px; "
            f"padding:0 12px; font-size:11px; font-weight:600;")
        explain = {
            "ok": "可正常访问",
            "cert": "能连上，但 HTTPS 证书不被浏览器信任（域名不匹配/过期/自签），浏览器会报警",
            "http": "服务器有响应但返回此状态码（如服务未启动/反代或路径不对）",
            "bad": "无法连接（服务未启动、端口未放行或网络不通）",
        }.get(state, "")
        self.setToolTip(f"{self.url}\n{explain}" if explain else self.url)

    def mousePressEvent(self, e):
        # 按下即触发（比 release 跟手），左键整行任意位置
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.url)
        super().mousePressEvent(e)


class SpinnerWidget(QWidget):
    """小型转圈加载动画：start()/stop() 控制，8 段由浅到深的旋转弧线"""

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
        span = 40 * 16  # 每段 40°（Qt 角度单位为 1/16 度）
        for i in range(8):
            c = QColor(self._color)
            c.setAlpha(45 + i * 26)
            p.setBrush(c)
            p.drawPie(r, (self._angle - i * 45) * 16, span)
        p.end()


class WebPanelWidget(BaseWidget):
    def _node_path_export(self) -> str:
        """动态 node 路径前缀：环境体检探测到真实 node 路径就用它（换机器可用），
        否则回退群晖常见路径常量。"""
        np = (self.probe.get("node_path") or "").strip()
        if np and "/" in np:
            d = np.rsplit("/", 1)[0]
            return f"export PATH=$PATH:{d}; "
        return PATH_EXPORT

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.conn = None
        self.presets = self._load_presets()
        self.nginx_svc = self._find_svc("Nginx")
        self.site_cards: list[SiteCard] = []
        self.log_thread: LogReaderThread | None = None
        self.log_active = False
        self._probe_ready = False  # 环境体检是否已完成（未完成时 Node 项显示"检测中"而非"异常"）
        self._build_ui()

        # 定时刷新状态
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self._refresh_status)

    def set_conn_info(self, conn):
        self._probe_ready = False  # 切换连接后重置，等待新的环境体检
        self.conn = conn
        self._rebuild_sites()

    def _rebuild_sites(self):
        """根据连接配置重建网站卡片列表"""
        # 先失效旧卡片 + 停掉所有后台 worker：否则扫描/状态刷新等异步回调会在
        # 旧卡片被 deleteLater 后仍访问其 C++ 对象 → libshiboken RuntimeError（重新扫描时必现）
        for c in self.site_cards:
            c._dead = True
            self.sites_grid.removeWidget(c)
            c.deleteLater()
        self.stop_workers()
        self.site_cards = []
        sites = []
        if self.conn and self.conn.sites:
            sites = self.conn.sites
        elif self.conn and self.conn.web_dir:
            sites = [{
                "name": Path(self.conn.web_dir).name or "网站",
                "web_dir": self.conn.web_dir,
                "entry": self.conn.node_proc,
                "node_proc": self.conn.node_proc,
                "port": "",
                "health_url": self.conn.health_url,
                "web_url": self.conn.web_url,
                "startable": bool(self.conn.node_proc),
                "type": "node",
            }]
        # 两列网格排列
        for i, s in enumerate(sites):
            card = SiteCard(s, self)
            self.site_cards.append(card)
            self.sites_grid.addWidget(card, i // 2, i % 2)
        self.empty_lbl.setVisible(not sites)
        if sites:
            # 两列等宽（奇数时最后一格留空）
            self.sites_grid.setColumnStretch(0, 1)
            self.sites_grid.setColumnStretch(1, 1)
        # 卡片建好后异步检测依赖/反代/自启
        for card in self.site_cards:
            self._check_site_meta(card)

    def _check_site_meta(self, card: SiteCard):
        """检测依赖 / 反代 / 自启 状态"""
        if not self.ssh or not self.ssh.is_connected:
            return
        web_dir = (card.site.get("web_dir") or "").strip()
        site = card.site.get("name", "site")
        if not web_dir:
            return
        shq = "'" + web_dir.replace("'", "'\\''") + "'"
        # 依赖状态
        dep_cmd = f"cd {shq} 2>/dev/null && {{ test -f package.json && echo pkg:yes || echo pkg:no; test -d node_modules && echo mods:yes || echo mods:no; }}"
        self.run_cmd(dep_cmd, lambda r, c=card: self._on_dep_check(c, r), timeout=10)
        # 反代状态：只认真正 proxy_pass 到该端口的配置（精确，不用短站点名模糊匹配）
        port = (card.site.get("port") or "").strip()
        if not self.need_sudo():
            card.set_proxy("warn", "反代：需 sudo", "请先在工具栏设置 sudo 密码，才能检测/配置反向代理")
        elif port:
            proxy_cmd = self._proxy_probe_cmd(port)
            self.run_cmd(proxy_cmd, lambda r, c=card: self._on_proxy_check(c, r), timeout=12, sudo=True)
        else:
            card.set_proxy("none", "反代：—", "该网站没有端口信息")
        # 自启状态
        rc_file = f"/usr/local/etc/rc.d/nas_{self._safe_name(site)}.sh"
        auto_cmd = f"test -f {rc_file} && echo yes || echo no"
        self.run_cmd(auto_cmd, lambda r, c=card: self._on_auto_check(c, r), timeout=10)

    @staticmethod
    def _safe_name(name: str) -> str:
        return _helper_safe_name(name)

    def _site_nginx_path(self, site: dict) -> str:
        """从站点目录动态提取 nginx 反代路径前缀（如 /volume1/web/ft/ai → ft/ai）。
        基于环境体检发现的网站根目录（/volume*/web）动态计算，不写死 /web/。"""
        web_dir = (site.get("web_dir") or "").strip()
        roots = self.probe.get("web_roots") or []
        if web_dir:
            for root in roots:
                root = root.rstrip("/")
                if web_dir == root:
                    return ""
                if web_dir.startswith(root + "/"):
                    p = web_dir[len(root):].strip("/")
                    if p:
                        return p
            # probe 未发现根目录时，退化兼容：取 /web/ 之后的部分
            if "/web/" in web_dir:
                p = web_dir.split("/web/", 1)[-1].strip("/")
                if p:
                    return p
        return (site.get("name") or "").strip() or ""

    # ---------- 反向代理：精确检测 ----------

    @staticmethod
    def _proxy_probe_cmd(port: str) -> str:
        """远端检测命令：只 grep 真正 proxy_pass 到该端口的配置行。
        - 端口带词边界 ([^0-9]|$)，:3000 不会误匹配 :30000；
        - 同时认 127.0.0.1 / localhost；
        - 绝不把站点名(如 ai)裸拼进正则——短名会撞碎 available/email 等系统文件。
        """
        return _helper_proxy_probe_cmd(port)

    @staticmethod
    def _proxy_block_uses_port(block: str, port: str) -> bool:
        """判断一个 location 块是否真的反代到指定端口（精确，不靠站点短名）。"""
        return _helper_proxy_block_uses_port(block, port)

    @staticmethod
    def _standard_location(path: str, port: str, slash_fix: bool = True,
                           web_root: str = "/volume1/web") -> str:
        """标准反代 location 组，委托给独立 helper。"""
        return _helper_standard_location(path, port, slash_fix, web_root)

    @staticmethod
    def _standard_server(host: str, listen_port: str, path: str, port: str,
                         slash_fix: bool = True, web_root: str = "/volume1/web") -> str:
        """标准完整反代 server 块，委托给独立 helper。"""
        return _helper_standard_server(host, listen_port, path, port, slash_fix, web_root)

    @staticmethod
    def _location_blocks(content: str) -> list:
        """花括号配对提取所有 location 块。"""
        return _helper_location_blocks(content)

    # ---------- 依赖管理 ----------

    def _on_dep_check(self, card: SiteCard, r):
        if getattr(card, "_dead", False):
            return
        out = (r.stdout or "").strip()
        has_pkg = "pkg:yes" in out
        has_mods = "mods:yes" in out
        if not has_pkg:
            card.set_dep("ok", "依赖：无需安装", "没有 package.json，零依赖网站")
        elif has_mods:
            card.set_dep("ok", "依赖：完整 ✓（点击重装）", "node_modules 已存在，点击重新安装")
        else:
            card.set_dep("warn", "依赖：未安装", "有 package.json 但还没装依赖，点击安装")

    def _site_deps(self, card: SiteCard):
        """打开依赖管理弹窗（列出所有依赖，可单独选择重装）"""
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.warning(self, "提示", "未连接")
            return
        DependenciesDialog(self, card).exec()
        # 关闭后刷新依赖状态
        self._check_site_meta(card)

    # ---------- 反向代理 ----------

    def _on_proxy_check(self, card: SiteCard, r):
        if getattr(card, "_dead", False):
            return
        out = (r.stdout or "").strip()
        if out:
            card.set_proxy("ok", "反代：已配置", out[:200])
        else:
            card.set_proxy("warn", "反代：未配置", "点击查看/配置反向代理")

    def _site_proxy(self, card: SiteCard):
        port = (card.site.get("port") or "").strip()
        web_dir = (card.site.get("web_dir") or "").strip()
        if not port:
            QMessageBox.information(self, "反向代理", "该网站未检测到端口，无法配置反代")
            return
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "检测/配置反代需要 sudo 密码，请先在工具栏「设置 Sudo 密码」")
            return
        # 手动点击：当场重新查 NAS 真实状态（不信扫描缓存），用精确检测
        proxy_cmd = self._proxy_probe_cmd(port)
        self.run_cmd(proxy_cmd, lambda r, c=card: self._proxy_dialog(c, r), timeout=12, sudo=True)

    def _proxy_dialog(self, card: SiteCard, r):
        site = card.site
        port = (site.get("port") or "").strip()
        web_dir = (site.get("web_dir") or "").strip()
        name = (site.get("name") or "site")
        existing = (r.stdout or "").strip()
        dlg = QDialog(self)
        dlg.setWindowTitle(f"反向代理 - {name}")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        if existing:
            lbl = QLabel("✅ 已检测到该网站的反向代理配置（实时查询结果）：")
            lbl.setStyleSheet("font-size:13px; font-weight:bold; color:#2e7d32;")
            lay.addWidget(lbl)
            txt = QPlainTextEdit()
            txt.setReadOnly(True)
            txt.setPlainText(existing)
            txt.setFixedHeight(140)
            lay.addWidget(txt)
            tip = QLabel("如果外网仍访问不通，可点「重新写入」：会先清掉指向该端口的旧 location，"
                         "再按当前路径/端口写入标准配置并重载。")
            tip.setStyleSheet("font-size:11px; color:#e65100;")
            tip.setWordWrap(True)
            lay.addWidget(tip)
            btns = QHBoxLayout()
            btns.addStretch()
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dlg.reject)
            rewrite_btn = QPushButton("重新写入（覆盖旧配置）")
            rewrite_btn.setStyleSheet(
                "background:#4caf50; color:#fff; border:none; border-radius:5px; padding:6px 14px;")
            # 覆盖写入也走 _do_write_proxy：读取旧配置还原监听端口/域名，
            # 生成完整 server 块（裸 location 块没有 listen，会走错写入分支）
            sp = self._site_nginx_path(site) or name
            loc_path = f"/{sp.strip('/')}/"
            old_port = ""
            old_names = ""
            try:
                first_file = existing.splitlines()[0].split(":", 1)[0].strip()
                if first_file:
                    r_old = self.ssh.run_command(f"cat {first_file} 2>/dev/null", timeout=10, sudo=True)
                    old_cfg = (r_old.stdout or "") if r_old else ""
                    m_old = re.search(r'\blisten\s+(\d+)', old_cfg)
                    if m_old:
                        old_port = m_old.group(1)
                    m_names = re.search(r'server_name\s+([^;]+);', old_cfg)
                    if m_names:
                        old_names = " ".join(m_names.group(1).split())
            except Exception:
                pass
            ssl_ports = self.probe.get("webstation_ssl_ports") or []
            if not old_port:
                old_port = (ssl_ports[0] if ssl_ports else "") or "450"
            if not old_names:
                doms = self.probe.get("domains") or []
                old_names = (doms[0] if doms else "") or "your.domain.com"

            class _Preview:
                def __init__(self, t):
                    self._t = t

                def toPlainText(self):
                    return self._t

            dlg._cfg_preview = _Preview(
                self._standard_server(old_names, old_port, loc_path, port,
                                      web_root=(self.probe.get("web_roots") or ["/volume1/web"])[0]))
            rewrite_btn.clicked.connect(lambda: self._do_write_proxy(dlg, card))
            btns.addWidget(close_btn)
            btns.addWidget(rewrite_btn)
            lay.addLayout(btns)
        else:
            lbl = QLabel(f"未检测到端口 {port} 的反向代理。\n"
                         "配置反代后，可以用 域名:端口/路径 访问，多网站共用端口。")
            lbl.setStyleSheet("font-size:13px; color:#333;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)

            form = QGridLayout()
            form.setSpacing(8)
            # 域名/端口从环境体检动态取（换 NAS 自动发现），不写死
            doms = self.probe.get("domains") or []
            ssl_ports = self.probe.get("webstation_ssl_ports") or []
            default_host = doms[0] if doms else ""
            default_port = (ssl_ports[0] if ssl_ports else "") or ""
            host_edit = QLineEdit(default_host)
            port_edit = QLineEdit(default_port)
            site_path = self._site_nginx_path(card.site)
            path_edit = QLineEdit(f"/{site_path}/" if site_path else f"/{name}/")
            form.addWidget(QLabel("域名:"), 0, 0)
            form.addWidget(host_edit, 0, 1)
            form.addWidget(QLabel("监听端口:"), 1, 0)
            form.addWidget(port_edit, 1, 1)
            form.addWidget(QLabel("路径前缀:"), 2, 0)
            form.addWidget(path_edit, 2, 1)
            lay.addLayout(form)
            if ssl_ports:
                tip2 = QLabel(f"提示：检测到 Web Station 专用 HTTPS 端口 {','.join(ssl_ports)}，"
                              f"自定义反代在该端口不生效。请用其他端口，或由 Web Station 门户配置。")
                tip2.setStyleSheet("font-size:11px; color:#e65100;")
                tip2.setWordWrap(True)
                lay.addWidget(tip2)

            preview = QPlainTextEdit()
            preview.setReadOnly(True)
            preview.setFixedHeight(150)
            lay.addWidget(preview)

            def render_preview():
                host = host_edit.text().strip()
                lp = port_edit.text().strip() or "450"
                path = path_edit.text().strip() or "/"
                # 收集所有域名：用户输入的放第一个，然后是探测到的其他域名（去重）
                all_domains = [host]
                for d in (self.probe.get("domains") or []):
                    if d != host and d not in all_domains:
                        all_domains.append(d)
                preview.setPlainText(
                    self._standard_server(" ".join(all_domains), lp, path, port,
                                          web_root=(self.probe.get("web_roots") or ["/volume1/web"])[0]))
            host_edit.textChanged.connect(lambda _: render_preview())
            port_edit.textChanged.connect(lambda _: render_preview())
            path_edit.textChanged.connect(lambda _: render_preview())
            render_preview()

            btns = QHBoxLayout()
            btns.addStretch()
            copy_btn = QPushButton("复制配置")
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(preview.toPlainText()))
            write_btn = QPushButton("自动写入并重载")
            write_btn.setStyleSheet("background:#4caf50; color:#fff; border:none; border-radius:5px; padding:6px 14px;")
            dlg._cfg_preview = preview
            dlg._host = host_edit
            dlg._port = port_edit
            dlg._path = path_edit
            write_btn.clicked.connect(lambda: self._do_write_proxy(dlg, card))
            btns.addWidget(copy_btn)
            btns.addWidget(write_btn)
            lay.addLayout(btns)
        dlg.exec()

    @staticmethod
    def _needs_slash_rewrite(ssh, port: str) -> bool:
        """探测 node 应用是否对路径尾斜杠敏感：
        /index.html 返回 200 而 /index.html/ 返回 404 → 敏感，反代需加 rewrite 去尾斜杠。
        探测不到 index.html（入口不是 index.html 的应用，如纯 API/服务端渲染）或
        探测失败（node 未运行/连不上）时，默认开启 rewrite（保守兼容，rewrite 幂等无害），
        避免"CDN 回源补尾斜杠 /api/album → /api/album/ 导致精确路由 404"。"""
        try:
            r1 = ssh.run_command(
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 4 "
                f"http://127.0.0.1:{port}/index.html", timeout=8)
            r2 = ssh.run_command(
                f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 4 "
                f"http://127.0.0.1:{port}/index.html/", timeout=8)
            c1 = (r1.stdout or "").strip()
            c2 = (r2.stdout or "").strip()
            if c1 == "200":
                # 有 index.html：尾斜杠 404/403 → 敏感；200 → 不敏感（静态 fallback）
                return c2 in ("404", "403")
            # index.html 不存在/探测无效（非 index.html 入口的应用）：
            # 无法判定，保守默认开启 rewrite（幂等，去尾斜杠安全）
            return True
        except Exception:
            pass
        # 探测失败（node 未运行等）：默认开启 rewrite，避免漏配导致尾斜杠 404
        return True

    @staticmethod
    def _location_targets(probe: dict, safe: str):
        """反代 location 要同时挂两个入口，内外网才都生效：
        1) 80/443 静态入口（外网 CDN 回源命中）：conf.d/.location.webstation.conf.{站点}
        2) Web Station 门户 server（内网 8443）：conf.d/.service.<uuid>.<uuid>.conf.{站点}（探测所得）
        返回 [(target, mount_pattern), ...]
        """
        targets = []
        targets.append((f"/etc/nginx/conf.d/.location.webstation.conf.{safe}",
                        "conf.d/.location.webstation.conf*"))
        pattern = (probe or {}).get("webstation_location_include") or ""
        if pattern.startswith("conf.d/"):
            base = pattern[len("conf.d/"):].rstrip("*").rstrip(".")
            if base:
                # include 模式 *.conf* 会匹配 base + "." + 站点名（如 .service.xxx.conf.ai）
                targets.append((f"/etc/nginx/conf.d/{base}.{safe}", pattern))
        return targets

    def _do_write_proxy(self, dlg: QDialog, card: SiteCard):
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "需要 sudo 密码，请先在工具栏「设置 Sudo 密码」")
            progress.close()
            return
        telemetry.track("写入反向代理配置")
        progress = QProgressDialog("正在准备...", None, 0, 0, self)
        progress.setWindowTitle("配置反向代理")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QApplication.processEvents()

        def _step(text):
            progress.setLabelText(text)
            QApplication.processEvents()

        preview = getattr(dlg, "_cfg_preview")
        cfg = preview.toPlainText()
        if not cfg.strip():
            progress.close()
            return
        name = (card.site.get("name") or "site")
        safe = self._safe_name(name)
        port = (card.site.get("port") or "").strip()

        # 1) 写入前再实时精确查一次（不信弹窗打开时的旧结果）。
        #    只认“proxy_pass 真正指向本端口”，短站点名不参与判断，避免误判“已配置”。
        _step("正在检测已有反代配置...")
        chk = self.ssh.run_command(self._proxy_probe_cmd(port), timeout=12, sudo=True)
        existing = ((chk.stdout or "").strip() if chk else "")
        backups = []  # [(原文件, 备份文件)] —— 旧配置先备份，失败可恢复
        if existing:
            if not msg.question(
                    self, "已存在反向代理",
                    f"实时检测到端口 {port} 已有反代配置：\n\n{existing[:500]}\n\n"
                    "是否先清掉这些旧 location，再按当前设置重新写入？\n"
                    "（只删指向该端口的块，不影响其他网站）"):
                progress.close()
                return
            # 先备份旧配置（写坏/测试失败时可恢复），再清掉指向本端口的旧 location 块
            _step("正在备份并清理旧配置...")
            old_files = sorted({ln.split(":", 1)[0].strip()
                                for ln in existing.splitlines() if ":" in ln})
            for i, fpath in enumerate(old_files):
                if not fpath:
                    continue
                bak = f"/tmp/.nas_proxy_bak_{safe}_{i}"
                b = self.ssh.run_command(f"sh -c 'cp {fpath} {bak}'", sudo=True, timeout=10)
                if b.exit_code == 0:
                    backups.append((fpath, bak))
                self._remove_site_proxy_blocks(fpath, "", port)

        def _restore_backups():
            for fpath, bak in backups:
                self.ssh.run_command(f"sh -c 'cp {bak} {fpath} && rm -f {bak}'",
                                     sudo=True, timeout=10)
            backups.clear()

        # 2) 解析生成配置里的监听端口；cfg 无 listen（纯 location 块）时
        #    归属到 Web Station 扩展端口，避免走错写入分支
        m = re.search(r'listen\s+(\d+)', cfg)
        ssl_ports = set(self.probe.get("webstation_ssl_ports") or [])
        if m:
            listen_port = m.group(1)
        elif ssl_ports:
            listen_port = next(iter(ssl_ports))
        else:
            # 探测不到 Web Station 端口且配置无 listen：提示而不是静默用 450
            # （换机器时 Web Station 端口可能是 8443/5001/任意值，写错就永不命中）
            if not msg.question(
                    self, "未检测到 Web Station 端口",
                    "环境体检未发现 Web Station HTTPS 端口，门户反代入口无法确定。\n\n"
                    "仍按默认 450 端口尝试写入？\n"
                    "若你的 Web Station HTTPS 端口不是 450，反代可能不生效。"):
                progress.close()
                return
            listen_port = "450"

        try:
            # 3) 目标端口是 Web Station 专属 HTTPS 端口（listen N ssl default_server，动态发现）
            #    独立 server 再 listen 同端口 http 会抢占端口导致原网站 404 → 走 Web Station 扩展点（location 块）
            _step("正在写入反代配置...")
            if listen_port in ssl_ports or (not ssl_ports and listen_port == "450"):
                blocks = self._location_blocks(cfg)
                if not blocks:
                    QMessageBox.warning(self, "失败", "无法解析配置中的 location 块")
                    progress.close()
                    return
                loc_block = "\n".join(b[1].strip() for b in blocks)
                # 缺 rewrite 就补：rewrite 对无尾斜杠请求幂等（路径不变），去尾斜杠安全，
                # 彻底兼容 CDN 回源补尾斜杠（/api/album → /api/album/ 精确路由 404）
                if not re.search(r'rewrite\s+.*break', loc_block):
                    m_path = re.search(r'location\s+\^~\s+([^\s{]+)', loc_block)
                    vpath = m_path.group(1) if m_path else "/"
                    web_root = (self.probe.get("web_roots") or ["/volume1/web"])[0]
                    loc_block = self._standard_location(vpath, port, slash_fix=True, web_root=web_root)
                # 双挂载点：80/443 静态入口(外网 CDN 回源) + Web Station 门户(内网 8443)
                targets = self._location_targets(self.probe, safe)
                tmp = "/tmp/_nas_proxy_loc.conf"
                # 命中验证准备：写入前取各入口当前响应指纹（Host 用探测域名，绕过白名单 404）
                m_path = re.search(r'location\s+\^~\s+([^\s{]+)', loc_block)
                vpath = m_path.group(1) if m_path else "/"
                doms = self.probe.get("domains") or []
                vhost = (doms[0] if doms else "") or (self.probe.get("webstation_server_name") or "")
                vchecks = []  # (入口名, url, 指纹命令, before指纹)
                seen_ports = set()
                for port_i, label in ((443, "外网(80/443 静态入口)"),
                                      (int(listen_port), "内网(Web Station 门户)")):
                    if port_i in seen_ports:
                        continue
                    seen_ports.add(port_i)
                    vurl = f"https://127.0.0.1:{port_i}{vpath}"
                    vcmd = (f"curl -sk -H 'Host: {vhost}' {vurl} 2>/dev/null "
                            f"| head -c 300 | md5sum | awk '{{print $1}}'"
                            if vhost else
                            f"curl -sk {vurl} 2>/dev/null | head -c 300 | md5sum | awk '{{print $1}}'")
                    before = (self.ssh.run_command(vcmd, timeout=10).stdout or "").strip() or "empty"
                    vchecks.append((label, vurl, vcmd, before))

                # 写入所有挂载点文件
                write_ok = True
                for target, _mp in targets:
                    if not self.ssh.ssh_write_file(tmp, loc_block + "\n"):
                        write_ok = False
                        break
                    cp = self.ssh.run_command(f"sh -c 'cp {tmp} {target} && chmod 600 {target}'",
                                              sudo=True, timeout=20)
                    if cp.exit_code != 0:
                        write_ok = False
                        break
                if not write_ok:
                    for target, _mp in targets:
                        self.ssh.run_command(f"rm -f {target}", sudo=True, timeout=10)
                    QMessageBox.warning(self, "失败", "写入 nginx 配置失败")
                    progress.close()
                    return
                _step("正在测试 nginx 配置...")
                t = self.ssh.run_command("nginx -t 2>&1", sudo=True, timeout=20)
                out = (t.stdout + t.stderr).strip()
                if not self._nginx_t_passed(out):
                    for target, _mp in targets:
                        self.ssh.run_command(f"rm -f {target}", sudo=True, timeout=20)
                    self.ssh.run_command(f"rm -f {tmp}", timeout=10)
                    _restore_backups()
                    msg.warn(self, "配置未通过", f"nginx -t 失败，已恢复旧配置:\n{out[:2000]}")
                    progress.close()
                    return
                _step("正在重载 nginx...")
                self.ssh.run_command("nginx -s reload", sudo=True, timeout=20)
                self.ssh.run_command(f"rm -f {tmp}", timeout=10)
                time.sleep(1.5)  # reload 平滑切换，等新 worker 生效再验证
                # ---- 命中验证（双层）----
                # ① 规则必须在 nginx 加载树里（重读配置展开 include，不受 upstream 状态影响）
                #    兼容两种 proxy_pass 写法：带 URI（slash_fix=False 的 ...:PORT/）与
                #    无 URI（slash_fix=True 的 ...:PORT; 配 rewrite 去尾斜杠）
                _step("正在验证反代规则...")
                rt = self.ssh.run_command(
                    f"nginx -T 2>/dev/null | grep -c 'proxy_pass[[:space:]]*http://127.0.0.1:{port}[;/]'",
                    timeout=25, sudo=True)
                in_tree = (rt.stdout or "").strip() not in ("", "0")
                # ② 行为验证仅当 node 活着时有意义（node 未运行反代 502 与静态 502 无法区分）
                _step("正在测试访问效果...")
                nr = self.ssh.run_command(
                    f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 4 http://127.0.0.1:{port}/ 2>/dev/null",
                    timeout=8)
                node_up = bool((nr.stdout or "").strip()) and (nr.stdout or "").strip() != "000"
                hit_entries = []
                if node_up:
                    for label, vurl, vcmd, before in vchecks:
                        after = "empty"
                        for _ in range(4):
                            time.sleep(0.5)
                            after = (self.ssh.run_command(vcmd, timeout=10).stdout or "").strip() or "empty"
                            if before != after:
                                break
                        if before != after:
                            hit_entries.append(label)
                # in_tree 通过即认为配置成功：规则已在 nginx 加载树中，nginx -t 通过且已 reload。
                # 行为验证（指纹对比）只作参考——重复写入时写入前后指纹相同是正常的，不能据此判失败。
                if not in_tree:
                    # 不盲目回滚：配置已通过 nginx -t，可能是 include 路径未匹配而非配置错误。
                    # 先诊断写入文件状态和 nginx 实际 include 的模式，保留配置并给出明确提示。
                    diag_lines = []
                    file_contents = []
                    for target, _mp in targets:
                        rr = self.ssh.run_command(f"test -f {target} && echo EXISTS || echo MISSING", timeout=8, sudo=True)
                        state = (rr.stdout or "").strip()
                        diag_lines.append(f"  {target}: {state}")
                        if state == "EXISTS":
                            fc = self.ssh.run_command(f"head -8 {target}", timeout=8, sudo=True)
                            fc_text = (fc.stdout or "").strip()
                            if fc_text:
                                file_contents.append(f"--- {target} (前8行) ---\n{fc_text}")
                    # 检查 nginx -T 实际加载的配置中是否有该文件的痕迹
                    rt2 = self.ssh.run_command(
                        f"nginx -T 2>/dev/null | grep -c 'location.webstation\\|service.*conf'",
                        timeout=20, sudo=True)
                    in_tree_count = (rt2.stdout or "").strip()
                    # 检查 nginx -T 中是否有该端口的任何行
                    rt3 = self.ssh.run_command(
                        f"nginx -T 2>/dev/null | grep -n '127.0.0.1:{port}' | head -5",
                        timeout=20, sudo=True)
                    port_lines = (rt3.stdout or "").strip() or "(无)"
                    ri = self.ssh.run_command("grep -rh 'include.*conf\\.d' /etc/nginx/ 2>/dev/null | sort -u | head -10", timeout=10, sudo=True)
                    actual_includes = (ri.stdout or "").strip() or "(未检测到)"
                    _restore_backups()
                    reason = ("规则未出现在 nginx 加载树（写入文件未被任何生效的 include 匹配）"
                              if not in_tree else
                              "node 已运行但所有入口响应都没有变化（规则未命中任何 server）")
                    detail_parts = [
                        f"配置已写入并通过 nginx -t，但命中验证未通过：{reason}",
                        "",
                        "【写入文件状态】",
                        chr(10).join(diag_lines),
                    ]
                    if file_contents:
                        detail_parts.extend(["", "【写入文件实际内容】"] + file_contents)
                    detail_parts.extend([
                        "",
                        "【nginx -T 诊断】",
                        f"  含 location.webstation/service 的行数: {in_tree_count}",
                        f"  含 127.0.0.1:{port} 的行:",
                        f"    {port_lines}",
                        "",
                        "【/etc/nginx 下搜到的 include 指令（注意：存在于文件中不等于被 nginx 加载）】",
                        actual_includes,
                        "",
                        "配置已保留在磁盘上（未删除）。",
                        "如果 nginx -T 中完全没有该文件内容，说明写入路径未被生效的 include 匹配，",
                        "请用 'nginx -T 2>/dev/null | grep include' 确认实际生效的 include 路径，",
                        "或检查 Web Station 门户配置中是否有对应扩展点。",
                    ])
                    msg.warn(
                        self, "验证未通过（配置已保留）",
                        chr(10).join(detail_parts))
                    card.set_proxy("warn", "反代：待验证", "配置已写入但命中验证未通过，请检查 include 路径")
                    dlg.accept()
                    progress.close()
                    return
                _restore_backups()
                dlg.accept()
                card.set_proxy("ok", "反代：已配置", loc_block[:200])
                dstr = ("  http://" + vhost + " 或 https://" + vhost + "（80/443）") if vhost else "（80/443 端口）"
                if hit_entries:
                    vmsg = f"且本地验证已生效（{'、'.join(hit_entries)}）。"
                elif not node_up:
                    vmsg = ("（node 当前未运行，无法做行为验证；规则已确认写入两个入口的"
                            "nginx 加载树，启动站点后访问即可生效）")
                else:
                    vmsg = "（规则已确认写入 nginx 加载树）"
                QMessageBox.information(
                    self, "成功",
                    "反代已写入 Web Station 扩展点（80/443 静态入口 + 门户）、nginx 测试通过并已重载，"
                    f"{vmsg}\n\n"
                    f"该网站现在可通过以下地址访问：\n{dstr}")
                progress.close()
                return

            # 4) 其他端口：独立 server 写入 sites-enabled（群晖 include 扩展点）
            fname = f"nas_{safe}.conf"
            target = f"/etc/nginx/sites-enabled/{fname}"
            tmp = "/tmp/" + fname
            if not self.ssh.ssh_write_file(tmp, cfg):
                QMessageBox.warning(self, "失败", "写入临时文件失败")
                progress.close()
                return
            cp = self.ssh.run_command(f"cp {tmp} {target}", sudo=True, timeout=20)
            if cp.exit_code != 0:
                msg.warn(self, "失败", f"写入 nginx 配置失败:\n{(cp.stderr or cp.stdout)[:300]}")
                progress.close()
                return
            t = self.ssh.run_command("nginx -t 2>&1", sudo=True, timeout=20)
            out = (t.stdout + t.stderr).strip()
            if self._nginx_t_passed(out):
                self.ssh.run_command("nginx -s reload", sudo=True, timeout=20)
                self.ssh.run_command(f"rm -f {tmp}", timeout=10)
                _restore_backups()
                QMessageBox.information(self, "成功", "反代配置已写入并通过测试，nginx 已重载。")
                dlg.accept()
                card.set_proxy("ok", "反代：已配置", cfg[:200])
            else:
                self.ssh.run_command(f"rm -f {target}", sudo=True, timeout=20)
                self.ssh.run_command(f"rm -f {tmp}", timeout=10)
                _restore_backups()
                msg.warn(self, "配置未通过", f"nginx -t 失败，已恢复旧配置:\n{out[:2000]}")
        except Exception as e:
            _restore_backups()
            QMessageBox.warning(self, "失败", f"配置异常: {e}")

    @staticmethod
    def _nginx_t_passed(out: str) -> bool:
        """nginx -t 结果判定：委托纯逻辑辅助函数。"""
        return _helper_nginx_t_passed(out)

    # ---------- 开机自启 ----------

    def _rc_file(self, card: SiteCard) -> str:
        name = (card.site.get("name") or "site")
        return f"/usr/local/etc/rc.d/nas_{self._safe_name(name)}.sh"

    def _on_auto_check(self, card: SiteCard, r):
        out = (r.stdout or "").strip()
        if out.strip() == "yes":
            card.set_auto("ok", "自启：已设置", "已配置开机自启，点击查看/取消")
        else:
            card.set_auto("warn", "自启：未设置", "点击设置开机自启（NAS 重启后自动启动）")

    def _site_autostart(self, card: SiteCard):
        web_dir = (card.site.get("web_dir") or "").strip()
        node_proc = (card.site.get("node_proc") or "node server.js").strip()
        rc_file = self._rc_file(card)
        if not web_dir or not node_proc:
            QMessageBox.warning(self, "提示", "该网站缺少启动信息")
            return
        # 查当前状态
        self.run_cmd(f"test -f {rc_file} && echo yes || echo no",
                     lambda r, c=card, f=rc_file, w=web_dir, n=node_proc: self._auto_dialog(c, f, w, n, r),
                     timeout=10)

    def _auto_dialog(self, card: SiteCard, rc_file, web_dir, node_proc, r):
        exists = (r.stdout or "").strip() == "yes"
        name = (card.site.get("name") or "site")
        dlg = QDialog(self)
        dlg.setWindowTitle(f"开机自启 - {name}")
        dlg.setMinimumWidth(460)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)

        if exists:
            lbl = QLabel("✅ 已设置开机自启。NAS 重启后会自动启动该网站。")
            lbl.setStyleSheet("font-size:13px; color:#2e7d32;")
            lay.addWidget(lbl)
            script = QPlainTextEdit()
            script.setReadOnly(True)
            script.setFixedHeight(160)
            self.run_cmd(f"cat {rc_file}", lambda rr, s=script: s.setPlainText(rr.stdout or ""), timeout=10)
            lay.addWidget(script)
            btns = QHBoxLayout()
            btns.addStretch()
            cancel_btn = QPushButton("取消自启")
            cancel_btn.setStyleSheet("background:#fef0f0; color:#c62828; border:none; border-radius:5px; padding:6px 14px;")
            close_btn = QPushButton("关闭")
            cancel_btn.clicked.connect(lambda: self._do_remove_auto(dlg, card, rc_file))
            close_btn.clicked.connect(dlg.accept)
            btns.addWidget(cancel_btn)
            btns.addWidget(close_btn)
            lay.addLayout(btns)
        else:
            lbl = QLabel("未设置开机自启。设置后 NAS 重启会自动启动该网站。\n"
                         "原理：在 /usr/local/etc/rc.d/ 创建启动脚本（群晖官方第三方服务方式）。")
            lbl.setStyleSheet("font-size:13px; color:#333;")
            lbl.setWordWrap(True)
            lay.addWidget(lbl)
            preview = QPlainTextEdit()
            preview.setReadOnly(True)
            shq = "'" + web_dir.replace("'", "'\\''") + "'"
            pid_file = f"{web_dir}/.server.pid"
            script = (
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  start)\n"
                f"    export HOME=/tmp; {self._node_path_export().strip()}\n"
                f"    cd {shq}\n"
                f"    nohup {node_proc} >> server.log 2>&1 & echo $! > {pid_file}\n"
                "    ;;\n"
                "  stop)\n"
                f"    if [ -f {pid_file} ]; then kill -9 $(cat {pid_file}) 2>/dev/null; rm -f {pid_file}; fi\n"
                f"    pkill -f {shq} 2>/dev/null\n"
                "    ;;\n"
                "esac\n"
                "exit 0\n"
            )
            preview.setPlainText(script)
            preview.setFixedHeight(170)
            lay.addWidget(preview)
            btns = QHBoxLayout()
            btns.addStretch()
            write_btn = QPushButton("设置开机自启")
            write_btn.setStyleSheet("background:#4caf50; color:#fff; border:none; border-radius:5px; padding:6px 14px;")
            write_btn.clicked.connect(lambda: self._do_set_auto(dlg, card, rc_file, script))
            btns.addWidget(write_btn)
            lay.addLayout(btns)
        dlg.exec()

    def _do_set_auto(self, dlg: QDialog, card: SiteCard, rc_file: str, script: str):
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "需要 sudo 密码，请先在工具栏「设置 Sudo 密码」")
            return
        telemetry.track("设置开机自启")
        # 检测目标机器是否支持 rc.d 自启（换机器/非群晖可能没有该目录）
        rc_dir = rc_file.rsplit("/", 1)[0]
        chk = self.ssh.run_command(f"test -d {rc_dir} && echo yes || echo no", timeout=8, sudo=True)
        if (chk.stdout or "").strip() != "yes":
            if not msg.question(
                    self, "自启目录不存在",
                    f"该机器没有 {rc_dir} 目录，可能不支持这种开机自启方式。\n\n"
                    "仍要尝试写入吗？（若系统不加载该目录，重启后不会自动启动）"):
                return
        tmp = "/tmp/nas_auto_tmp.sh"
        try:
            if not self.ssh.ssh_write_file(tmp, script):
                QMessageBox.warning(self, "失败", "写入临时文件失败")
                return
            # 写入 rc.d 并赋可执行权限
            r1 = self.ssh.run_command(f"cp {tmp} {rc_file}", sudo=True, timeout=20)
            if r1.exit_code != 0:
                QMessageBox.warning(self, "失败", f"写入失败:\n{(r1.stderr or r1.stdout)[:300]}")
                return
            self.ssh.run_command(f"chmod +x {rc_file}", sudo=True, timeout=10)
            self.ssh.run_command(f"rm -f {tmp}", timeout=10)
            QMessageBox.information(self, "成功", "开机自启已设置。NAS 重启后会自动启动该网站。")
            dlg.accept()
            card.set_auto("ok", "自启：已设置", "已配置开机自启，点击查看/取消")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"设置异常: {e}")

    def _do_remove_auto(self, dlg: QDialog, card: SiteCard, rc_file: str):
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "需要 sudo 密码，请先在工具栏「设置 Sudo 密码」")
            return
        r = self.ssh.run_command(f"rm -f {rc_file}", sudo=True, timeout=20)
        if r.exit_code != 0:
            QMessageBox.warning(self, "失败", f"取消失败:\n{(r.stderr or r.stdout)[:300]}")
            return
        QMessageBox.information(self, "成功", "已取消开机自启")
        dlg.accept()
        card.set_auto("warn", "自启：未设置", "点击设置开机自启（NAS 重启后自动启动）")

    # ---------- 通用 ----------

    def _resolve_cmd(self, cmd: str) -> str:
        """把 {web_dir}/{node_proc}/{health_url} 占位符替换成连接配置实际值（不再写死路径）"""
        if not cmd or "{" not in cmd:
            return cmd
        web_dir, node_proc, health = "", "", ""
        if self.conn:
            sites = self.conn.sites or []
            main = next((s for s in sites if s.get("startable")), (sites[0] if sites else None))
            web_dir = self.conn.web_dir or (main.get("web_dir") if main else "") or ""
            node_proc = self.conn.node_proc or (main.get("node_proc") if main else "") or ""
            health = self.conn.health_url or (main.get("health_url") if main else "") or ""
        return (cmd.replace("{web_dir}", web_dir)
                   .replace("{node_proc}", node_proc)
                   .replace("{health_url}", health))

    def _load_presets(self) -> dict:
        try:
            return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _find_svc(self, name: str) -> dict:
        for s in self.presets.get("services", []):
            if s.get("name") == name:
                return s
        return {}

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(8)
        self.setStyleSheet(PANEL_STYLE)

        # 标题行 + 刷新按钮
        title_row = QHBoxLayout()
        title = QLabel("网页服务器面板")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        self.refresh_btn = QPushButton("重新扫描网站")
        self.refresh_btn.setObjectName("WebBtn")
        self.refresh_btn.clicked.connect(self._rescan_sites)
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.refresh_btn)
        outer.addLayout(title_row)

        # Node.js 未安装警告横幅（默认隐藏，探测后按需显示）
        self.node_warn_bar = QFrame()
        self.node_warn_bar.setObjectName("NodeWarnBar")
        self.node_warn_bar.setStyleSheet("""
            QFrame#NodeWarnBar {
                background: #fff8e1; border: 1px solid #ffc107; border-radius: 6px;
            }
            QFrame#NodeWarnBar QLabel { color: #856404; font-size: 13px; }
            QFrame#NodeWarnBar QPushButton {
                background: #2e7163; color: white; border: none;
                padding: 6px 16px; border-radius: 4px; font-size: 13px;
            }
            QFrame#NodeWarnBar QPushButton:hover { background: #256054; }
            QFrame#NodeWarnBar QPushButton:disabled { background: #aaa; }
        """)
        self.node_warn_bar.setFixedHeight(46)
        self.node_warn_bar.hide()
        nwb = QHBoxLayout(self.node_warn_bar)
        nwb.setContentsMargins(12, 0, 12, 0)
        nwb.setSpacing(8)
        nwb_icon = QLabel("\u26a0")
        nwb_icon.setStyleSheet("font-size:18px;")
        nwb.addWidget(nwb_icon)
        self.node_warn_text = QLabel("未检测到 Node.js 运行环境，Node 网站无法启动。")
        nwb.addWidget(self.node_warn_text)
        nwb.addStretch()
        self.node_install_btn = QPushButton("安装 Node.js")
        self.node_install_btn.setCursor(Qt.PointingHandCursor)
        self.node_install_btn.clicked.connect(self._install_nodejs)
        nwb.addWidget(self.node_install_btn)
        outer.addWidget(self.node_warn_bar)

        # 滚动内容区
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(0, 0, 6, 0)
        content_lay.setSpacing(8)

        # Nginx + Node.js 同一行左右排列（各占一半，垂直 Minimum 自动等高不过度拉伸）
        svc_row = QHBoxLayout()
        svc_row.setSpacing(10)

        nginx_card = self._build_nginx_card()
        nginx_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        svc_row.addWidget(nginx_card, 1)

        self.node_panel = self._build_node_panel()
        self.node_panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        svc_row.addWidget(self.node_panel, 1)

        content_lay.addLayout(svc_row)

        # 我的网站区块
        sites_title = QLabel("我的网站")
        sites_title.setObjectName("SectionTitle")
        content_lay.addWidget(sites_title)
        self.sites_box = QWidget()
        self.sites_grid = QGridLayout(self.sites_box)
        self.sites_grid.setContentsMargins(0, 0, 0, 0)
        self.sites_grid.setHorizontalSpacing(10)
        self.sites_grid.setVerticalSpacing(10)
        content_lay.addWidget(self.sites_box)

        roots = self.probe.get("web_roots") or ["/volume*/web"]
        self.empty_lbl = QLabel(f"未找到网站。点击右上角「重新扫描网站」，或检查 {'、'.join(roots)} 目录。")
        self.empty_lbl.setStyleSheet("font-size:12px; color:#999; padding:12px;")
        self.empty_lbl.setVisible(False)
        content_lay.addWidget(self.empty_lbl)

        # 日志区
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setFixedHeight(170)
        self.log_view.setVisible(False)
        content_lay.addWidget(self.log_view)

        content_lay.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        # 底部固定快速操作栏
        quick = QFrame()
        quick.setObjectName("WebCard")
        ql = QHBoxLayout(quick)
        ql.setContentsMargins(12, 8, 12, 8)
        ql.addWidget(QLabel("快速操作："))
        for text, slot in [
            ("查看 Nginx 反代", self._show_proxy),
            ("查看端口监听", self._show_ports),
            ("诊断环境", self._diagnose_env),
        ]:
            btn = QPushButton(text)
            btn.setObjectName("WebBtn")
            btn.clicked.connect(slot)
            ql.addWidget(btn)
        ql.addStretch()
        outer.addWidget(quick)

    def _build_nginx_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("WebCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.nginx_dot = QLabel("●")
        self.nginx_dot.setObjectName("Dot")
        self.nginx_dot.setStyleSheet("color:#bbb;")
        title = QLabel("Nginx")
        title.setStyleSheet("font-size:14px; font-weight:bold; color:#1a1a2e;")
        self.nginx_status = QLabel("检测中...")
        self.nginx_status.setStyleSheet("font-size:11px; color:#888;")
        self.nginx_path_lbl = QLabel("版本：—")
        self.nginx_path_lbl.setStyleSheet("font-size:11px; color:#999;")
        top.addWidget(self.nginx_dot)
        top.addWidget(title)
        top.addSpacing(4)
        top.addWidget(self.nginx_path_lbl)
        top.addStretch()
        top.addWidget(self.nginx_status)
        lay.addLayout(top)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        test_btn = QPushButton("配置测试")
        test_btn.setObjectName("WebBtn")
        test_btn.clicked.connect(self._nginx_test)
        reload_btn = QPushButton("重载配置")
        reload_btn.setObjectName("PrimaryBtn")
        reload_btn.clicked.connect(self._nginx_reload)
        proxy_btn = QPushButton("反代列表")
        proxy_btn.setObjectName("WebBtn")
        proxy_btn.clicked.connect(self._show_proxy)
        for b in (test_btn, reload_btn, proxy_btn):
            btns.addWidget(b)
        btns.addStretch()
        lay.addLayout(btns)
        return card

    def _build_node_panel(self) -> QFrame:
        """Node.js 卡片：与 Nginx 卡片同风格同尺寸，状态/版本/信息 + 启停切换/重启/卸载"""
        card = QFrame()
        card.setObjectName("WebCard")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        # 顶部行：指示灯 + 标题 + 版本标签 + 状态文字（与 Nginx 卡片完全一致）
        top = QHBoxLayout()
        top.setSpacing(6)
        self.node_dot = QLabel("●")
        self.node_dot.setStyleSheet("color:#bbb;")
        node_title = QLabel("Node.js")
        node_title.setStyleSheet("font-size:14px; font-weight:bold; color:#1a1a2e;")
        self.node_ver_lbl = QLabel("版本：—")
        self.node_ver_lbl.setStyleSheet("font-size:11px; color:#999;")
        self.node_status_lbl = QLabel("检测中...")
        self.node_status_lbl.setStyleSheet("font-size:11px; color:#888;")
        top.addWidget(self.node_dot)
        top.addWidget(node_title)
        top.addSpacing(4)
        top.addWidget(self.node_ver_lbl)
        top.addStretch()
        top.addWidget(self.node_status_lbl)
        lay.addLayout(top)

        # 信息行：路径 / npm版本 / 运行进程数
        self.node_info_lbl = QLabel("路径：—  |  npm：—  |  运行中进程：—")
        self.node_info_lbl.setStyleSheet("font-size:11px; color:#999;")
        self.node_info_lbl.setWordWrap(True)
        lay.addWidget(self.node_info_lbl)

        # 按钮行：启停切换（同一按钮）+ 重启 + 卸载
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.node_toggle_btn = QPushButton("启动")
        self.node_toggle_btn.setObjectName("PrimaryBtn")
        self.node_toggle_btn.clicked.connect(self._node_toggle)
        self.node_restart_btn = QPushButton("重启")
        self.node_restart_btn.setObjectName("WebBtn")
        self.node_restart_btn.clicked.connect(self._node_restart_all)
        self.node_uninstall_btn = QPushButton("卸载")
        self.node_uninstall_btn.setObjectName("WebBtn")
        self.node_uninstall_btn.setStyleSheet("color:#c62828;")
        self.node_uninstall_btn.clicked.connect(self._node_uninstall)
        btns.addWidget(self.node_toggle_btn)
        btns.addWidget(self.node_restart_btn)
        btns.addWidget(self.node_uninstall_btn)
        btns.addStretch()
        lay.addLayout(btns)

        for b in (self.node_toggle_btn, self.node_restart_btn, self.node_uninstall_btn):
            b.setEnabled(False)
        return card

    # ---------- 生命周期 ----------

    def on_probe(self):
        """环境体检完成后：反代检测等依赖动态环境的项自动刷新"""
        self._probe_ready = True
        try:
            self._refresh_status()
            self._refresh_node_panel()
            for card in self.site_cards:
                self._check_site_meta(card)
            # 若进入面板时体检尚未完成，Node 项当时显示"检测中"，此处补刷最终状态
            hdlg = getattr(self, "_health_dialog", None)
            if hdlg is not None:
                node_ok = bool((self.probe.get("node_ver") or "").strip())
                hdlg.set_state(1, "ok" if node_ok else "fail")
        except Exception:
            pass

    def _ensure_proxy_files(self):
        """自愈：群晖 Web Station 重建 conf.d 会清空/重置反代扩展点文件，
        导致"配好的反代过一会失效"。切到本页时检测两类问题并自动重写：
        1) 文件缺失/内容被清空（grep 不到本站 proxy_pass）；
        2) 文件在但缺 rewrite，而 node 对尾斜杠敏感（如 CDN 回源自动补尾斜杠
           /api/album → /api/album/ 时 node 精确路由 404）。
        均按当前站点信息重新写入并 reload，兼容大多数环境。"""
        if not self.ssh or not self.ssh.is_connected or not self.need_sudo():
            return
        repaired = False
        written_targets = []  # 记录本次自愈写入的文件，nginx -t 失败时回滚
        for card in self.site_cards:
            port = (card.site.get("port") or "").strip()
            name = card.site.get("name") or "site"
            if not port:
                continue
            safe = self._safe_name(name)
            for target, _mp in self._location_targets(self.probe, safe):
                r = self.ssh.run_command(
                    f"grep -c 'proxy_pass[[:space:]]*http://127.0.0.1:{port}' {target} 2>/dev/null",
                    timeout=8, sudo=True)
                cnt = (r.stdout or "").strip()
                need = cnt in ("", "0")
                if not need:
                    # 文件在但缺 rewrite：CDN 回源补尾斜杠（/api/album → /api/album/）
                    # 会让 node 精确路由 404 → 补 rewrite 去尾斜杠
                    rw = self.ssh.run_command(
                        f"grep -c 'rewrite[[:space:]].*break' {target} 2>/dev/null",
                        timeout=8, sudo=True)
                    if (rw.stdout or "").strip() in ("", "0"):
                        need = True
                if need:
                    site_path = self._site_nginx_path(card.site) or name
                    loc_path = f"/{site_path.strip('/')}/" if site_path else "/"
                    # 一律 slash_fix=True：rewrite 对无尾斜杠请求幂等（路径不变），
                    # 去尾斜杠对绝大多数 node 应用安全，彻底兼容 CDN/浏览器补斜杠
                    web_root = (self.probe.get("web_roots") or ["/volume1/web"])[0]
                    loc = self._standard_location(loc_path, port, slash_fix=True, web_root=web_root)
                    tmp = "/tmp/_nas_heal.conf"
                    if self.ssh.ssh_write_file(tmp, loc + "\n"):
                        cp = self.ssh.run_command(
                            f"sh -c 'cp {tmp} {target} && chmod 600 {target}'",
                            timeout=10, sudo=True)
                        if cp.exit_code == 0:
                            repaired = True
                            written_targets.append(target)
                            if not getattr(self, "_health_worker_mode", False):
                                self.status_msg(f"已自动恢复反代配置：{name}")
                    self.ssh.run_command(f"rm -f {tmp}", timeout=8)
        if repaired:
            t = self.ssh.run_command("nginx -t 2>&1", timeout=20, sudo=True)
            if self._nginx_t_passed((t.stdout + t.stderr).strip()):
                self.ssh.run_command("nginx -s reload", timeout=20, sudo=True)
                if not getattr(self, "_health_worker_mode", False):
                    self.status_msg("反代配置已自动恢复并重载")
            else:
                for target in written_targets:
                    self.ssh.run_command(f"rm -f {target}", timeout=10, sudo=True)
                if not getattr(self, "_health_worker_mode", False):
                    self.status_msg("自愈反代配置测试未通过，已回滚")

    def _ensure_sites_alive(self):
        """自愈：站点"应运行却未运行" → 自动重启。覆盖三种情况且尊重用户意图：
        1) .server.pid 存在但进程已死（异常崩溃/被系统带挂）→ 重启；
        2) .server.pid 丢失（Web Station 重建目录等可能连 pid 一起清掉）但
           server.log 显示曾经启动过、且端口确实未监听 → 重启；
        3) 存在 .nasmanager_stopped 标记（用户在软件里主动停止）→ 不拉起。
        从没启动过的站点（无 server.log 痕迹）→ 不拉起。"""
        if not self.ssh or not self.ssh.is_connected:
            return
        for card in self.site_cards:
            web_dir = (card.site.get("web_dir") or "").strip()
            node_proc = (card.site.get("node_proc") or "").strip()
            port = (card.site.get("port") or "").strip()
            if not web_dir or not node_proc:
                continue
            pid_file = f"{web_dir}/.server.pid"
            stopped_mark = f"{web_dir}/.nasmanager_stopped"
            # 用户主动停止过 → 尊重意图，不自动拉起
            r = self.ssh.run_command(f"test -f {stopped_mark} && echo STOPPED || echo NOT", timeout=8)
            if (r.stdout or "").strip() == "STOPPED":
                continue
            r = self.ssh.run_command(
                f"if [ -f {pid_file} ]; then p=$(cat {pid_file}); "
                f"if kill -0 $p 2>/dev/null; then echo RUN; else echo DEAD; fi; "
                f"else echo NOPID; fi", timeout=8)
            state = (r.stdout or "").strip()
            need = False
            if state == "DEAD":
                need = True
            elif state == "NOPID" and port:
                # pid 丢失：仅当"曾经启动过"（server.log 有启动记录）且端口确实没监听，
                # 避免把从没启动过的站点也拉起来
                r2 = self.ssh.run_command(
                    f"test -s {web_dir}/server.log && echo YES || echo NO", timeout=8)
                if (r2.stdout or "").strip() == "YES":
                    r3 = self.ssh.run_command(
                        f"curl -s -o /dev/null -w '%{{http_code}}' --max-time 2 "
                        f"http://127.0.0.1:{port}/ 2>/dev/null || echo 000", timeout=6)
                    code = (r3.stdout or "").strip()
                    if code in ("", "000", "502"):
                        need = True
            if need:
                name = card.site.get("name") or "?"
                self.status_msg(f"检测到「{name}」未在运行，正在自动重启...")
                if getattr(self, "_health_worker_mode", False):
                    self._health_repair_actions.append(name)
                else:
                    self._site_action(card, "start")

    def on_show(self):
        self._update_node_warning()
        self._refresh_node_panel()
        for card in self.site_cards:
            self._check_site_meta(card)
        self._run_health_repair()
        self._refresh_status()
        self.status_timer.start(5000)

    def _run_health_repair(self):
        """进入网页面板时后台运行自愈，让检测动画持续刷新。"""
        if getattr(self, "_health_repair_running", False):
            return
        if not self.ssh or not self.ssh.is_connected:
            return
        self._health_repair_running = True
        items = [
            ("环境体检", "探测域名 / 端口 / 路径等真实环境"),
            ("Node.js 环境", "检测 Node.js 是否安装可用"),
            ("网站状态", "检查各网站是否正常运行，异常自动拉起"),
            ("反向代理", "检测 nginx 反代配置，缺失自动修复"),
            ("开机自启", "检测网站是否配置开机自启"),
        ]
        dlg = _ExtractedWebHealthDialog(self, items=items)
        self._health_dialog = dlg
        dlg.set_running()
        dlg.show()
        dlg.set_state(0, "ok")
        if not getattr(self, "_probe_ready", False):
            dlg.set_state(1, "running")  # 体检尚未完成，先显示检测中，on_probe 回调里补最终结果
        else:
            dlg.set_state(1, "ok" if (self.probe.get("node_ver") or "").strip() else "fail")
        dlg.set_state(2, "running")
        dlg.set_state(3, "running")
        dlg.set_state(4, "running")

        worker = HealthRepairWorker(self)
        self._health_repair_worker = worker
        worker.finished_repair.connect(self._on_health_repair_finished)
        worker.start()

    def _on_health_repair_finished(self, actions, error):
        """在主线程收尾自愈报告，并执行记录下来的站点启动动作。"""
        dlg = getattr(self, "_health_dialog", None)
        if dlg is None:
            self._health_repair_running = False
            return
        try:
            for name in actions or []:
                for card in self.site_cards:
                    if (card.site.get("name") or "?") == name:
                        self._site_action(card, "start")
                        break
            dlg.set_state(2, "fixed" if actions else "ok")
            dlg.set_state(3, "ok" if not error else "fail")
            dlg.set_state(4, "ok")
            dlg.finish(error or "网页面板检测完成；异常站点启动操作已发起。")
            # finish() 会把残留"检测中"兜底为 ok；Node 状态以体检结果为准，按真实情况校正
            if not getattr(self, "_probe_ready", False):
                dlg.set_state(1, "running")
            else:
                dlg.set_state(1, "ok" if (self.probe.get("node_ver") or "").strip() else "fail")
        finally:
            self._health_repair_running = False
            self._health_repair_worker = None

    def on_hide(self):
        self.status_timer.stop()
        self.stop_log()

    # ---------- Node.js 自动检测与一键安装 ----------

    def _update_node_warning(self):
        """根据环境探测结果显示/隐藏 Node.js 未安装警告横幅"""
        node_ver = (self.probe.get("node_ver") or "").strip()
        if node_ver:
            self.node_warn_bar.hide()
        else:
            self.node_warn_bar.show()
            self.node_install_btn.setEnabled(True)
            self.node_install_btn.setText("安装 Node.js")
            self.node_warn_text.setText("未检测到 Node.js 运行环境，Node 网站无法启动。")

    # ---------- 右侧 Node.js 面板 ----------

    def _refresh_node_panel(self):
        """根据环境探测结果刷新 Node.js 卡片（与 Nginx 卡片同风格）"""
        if not hasattr(self, "node_dot"):
            return
        node_ver = (self.probe.get("node_ver") or "").strip()
        node_path = (self.probe.get("node_path") or "").strip()
        if node_ver:
            self.node_dot.setStyleSheet("color:#4caf50;")
            self.node_status_lbl.setText("已安装")
            self.node_status_lbl.setStyleSheet("font-size:11px; color:#4caf50;")
            self.node_ver_lbl.setText(f"版本：{node_ver}")
            self.node_info_lbl.setText(f"路径：{node_path}  |  npm：检测中...  |  运行中进程：—")
            for b in (self.node_toggle_btn, self.node_restart_btn, self.node_uninstall_btn):
                b.setEnabled(True)
            self._query_node_details()
        else:
            self.node_dot.setStyleSheet("color:#f44336;")
            self.node_status_lbl.setText("未安装")
            self.node_status_lbl.setStyleSheet("font-size:11px; color:#f44336;")
            self.node_ver_lbl.setText("版本：—")
            self.node_info_lbl.setText("路径：—  |  npm：—  |  运行中进程：—")
            self.node_toggle_btn.setText("启动")
            for b in (self.node_toggle_btn, self.node_restart_btn, self.node_uninstall_btn):
                b.setEnabled(False)

    def _node_toggle(self):
        """启停切换：运行中->停止所有，已停止->启动所有"""
        if self.node_toggle_btn.text() == "停止":
            self._node_stop_all()
        else:
            self._node_start_all()

    def _query_node_details(self):
        """异步查询 npm 版本和 node 进程数"""
        if not self.ssh or not self.ssh.is_connected:
            return
        cmd = ("export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin; "
               "echo -n 'npm:'; npm -v 2>/dev/null; echo; "
               "echo -n 'proc:'; ps -ef | grep '[n]ode' | wc -l")
        self.run_cmd(cmd, self._on_node_details, timeout=10)

    def _on_node_details(self, r):
        out = r.stdout or ""
        npm_ver = "—"
        proc_cnt = "0"
        for line in out.splitlines():
            if line.startswith("npm:"):
                npm_ver = line[4:].strip() or "—"
            elif line.startswith("proc:"):
                proc_cnt = line[5:].strip() or "0"
        node_path = (self.probe.get("node_path") or "").strip() or "—"
        self.node_info_lbl.setText(f"路径：{node_path}  |  npm：{npm_ver}  |  运行中进程：{proc_cnt}")
        # 根据进程数切换启停按钮文字
        try:
            self.node_toggle_btn.setText("停止" if int(proc_cnt) > 0 else "启动")
        except ValueError:
            pass

    def _node_start_all(self):
        """启动所有未运行的 Node 网站"""
        targets = [c for c in self.site_cards
                   if c.site.get("type") == "node" and not c.active]
        if not targets:
            msg.info(self, "提示", "所有 Node 网站都已在运行，或没有 Node 网站。")
            return
        for card in targets:
            self._site_action(card, "start")
        self.status_msg(f"正在启动 {len(targets)} 个 Node 网站...")

    def _node_stop_all(self):
        """停止所有 Node 进程（先停站点，再杀残留）"""
        if not msg.question(self, "确认停止",
                           "将停止所有 Node.js 进程（包括所有 Node 网站）。是否继续？"):
            return
        for card in self.site_cards:
            if card.site.get("type") == "node" and card.active:
                self._site_action(card, "stop")
        cmd = "pkill -9 node 2>/dev/null; sleep 1; echo -n '剩余:'; ps -ef | grep '[n]ode' | wc -l"
        self.run_cmd(cmd, lambda r: self.status_msg(f"已停止所有 Node 进程，{(r.stdout or '').strip()}"),
                     timeout=12, sudo=True)

    def _node_restart_all(self):
        """重启所有 Node 网站"""
        targets = [c for c in self.site_cards if c.site.get("type") == "node"]
        if not targets:
            msg.info(self, "提示", "没有 Node 网站。")
            return
        for card in targets:
            self._site_action(card, "restart")
        self.status_msg(f"正在重启 {len(targets)} 个 Node 网站...")

    def _node_uninstall(self):
        """卸载 Node.js（删除安装目录和软链接）"""
        if not msg.question(self, "确认卸载",
                           "将卸载 Node.js：删除 /volume1/@appstore/nodejs 和 /usr/local/bin 下的软链接。\n\n"
                           "所有 Node 网站将无法运行。是否继续？"):
            return
        if not self.need_sudo():
            msg.critical(self, "需要管理员权限", "卸载需要 sudo 权限。")
            return
        cmd = ("rm -rf /volume1/@appstore/nodejs; "
               "rm -f /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx; "
               "echo '卸载完成'")
        self.run_cmd(cmd, self._on_node_uninstalled, timeout=15, sudo=True)
        self.status_msg("正在卸载 Node.js...")

    def _on_node_uninstalled(self, r):
        # 立即手动更新卡片状态（不依赖异步 reprobe，避免显示残留）
        if hasattr(self, "node_dot"):
            self.node_dot.setStyleSheet("color:#f44336;")
            self.node_status_lbl.setText("未安装")
            self.node_status_lbl.setStyleSheet("font-size:11px; color:#f44336;")
            self.node_ver_lbl.setText("版本：—")
            self.node_info_lbl.setText("路径：—  |  npm：—  |  运行中进程：—")
            self.node_toggle_btn.setText("启动")
            for b in (self.node_toggle_btn, self.node_restart_btn, self.node_uninstall_btn):
                b.setEnabled(False)
        self.probe["node_ver"] = ""
        self.probe["node_path"] = ""
        self._update_node_warning()
        msg.info(self, "卸载完成", "Node.js 已卸载。可以点击顶部横幅的「安装 Node.js」重新安装。")

    def _install_nodejs(self):
        """根据环境探测结果安装 Node.js（架构/家目录/已安装状态均从 probe 动态读取，不硬编码）"""
        if not self.ssh or not self.ssh.is_connected:
            msg.critical(self, "未连接", "请先连接 NAS")
            return
        if not self.need_sudo():
            msg.critical(self, "需要管理员权限",
                      "安装需要 sudo 权限。请先点击工具栏的「Sudo」按钮并输入密码。")
            return

        # ---- 从环境探测结果读取真实信息（不硬编码猜）----
        system_info = self.probe.get("system", "")
        # system 格式如 "Linux 4.4.302+ x86_64\n---\nunique=..."
        arch = "x64"
        if "aarch64" in system_info or "arm64" in system_info:
            arch = "arm64"
        elif "armv7" in system_info:
            arch = "armv7l"
        elif "x86_64" in system_info or "amd64" in system_info:
            arch = "x64"

        home_status = self.probe.get("home_status", "")
        home_missing = "MISSING" in home_status

        node_path = self.probe.get("node_path", "")
        node_ver = self.probe.get("node_ver", "")
        if node_path:
            ok = msg.question(self, "Node.js 已安装",
                              f"环境探测已检测到 Node.js：\n路径: {node_path}\n版本: {node_ver}\n\n是否仍要重新安装？")
            if not ok:
                return

        ok = msg.question(self, "安装 Node.js",
                          f"将下载 Node.js 官方二进制包并安装。\n\n"
                          f"探测到的环境：\n"
                          f"  架构: linux-{arch}\n"
                          f"  家目录: {'缺失（将使用 /tmp）' if home_missing else '正常'}\n"
                          f"  已安装: {'是（将覆盖重装）' if node_path else '否'}\n\n"
                          f"安装约 25MB，弹窗内实时显示每一步输出。\n\n是否继续？")
        if not ok:
            return
        self.node_install_btn.setEnabled(False)
        self.node_install_btn.setText("安装中...")
        self.node_warn_text.setText("正在安装 Node.js，弹窗内实时显示输出...")

        self._install_dlg = InstallProgressDialog(self)
        self._install_dlg.append_log(f"=== 开始安装 Node.js（linux-{arch}）===")
        self._install_dlg.show()

        # 安装脚本：架构从探测结果动态传入，家目录缺失时自动 export HOME=/tmp
        import shlex
        home_prefix = "export HOME=/tmp; " if home_missing else ""
        install_script = (
            home_prefix +
            "set -e; "
            "NODE_VERSION='v20.17.0'; "
            f"ARCH='{arch}'; "
            "NODE_TAR=\"node-${NODE_VERSION}-linux-${ARCH}.tar.xz\"; "
            "NODE_URL=\"https://nodejs.org/dist/${NODE_VERSION}/${NODE_TAR}\"; "
            "INSTALL_DIR='/volume1/@appstore/nodejs'; "
            "echo '=== 1/6 清理旧安装 ==='; "
            "rm -rf \"$INSTALL_DIR\"; rm -f /usr/local/bin/node /usr/local/bin/npm /usr/local/bin/npx; "
            "echo '旧安装已清理'; "
            "echo '=== 2/6 下载 Node.js 二进制包 ==='; "
            "cd /tmp; "
            "echo \"下载地址: $NODE_URL\"; "
            "curl -sL \"$NODE_URL\" -o \"$NODE_TAR\"; "
            "echo \"下载完成: $(ls -lh $NODE_TAR 2>/dev/null | awk '{print $5}')\"; "
            "echo '=== 3/6 解压到安装目录 ==='; "
            "mkdir -p \"$INSTALL_DIR\"; "
            "tar -xJf \"$NODE_TAR\" -C \"$INSTALL_DIR\"; "
            "echo \"解压完成: $INSTALL_DIR\"; "
            "echo '=== 4/6 创建软链接 ==='; "
            "NODE_BIN=\"$INSTALL_DIR/node-${NODE_VERSION}-linux-${ARCH}/bin\"; "
            "ln -sf \"$NODE_BIN/node\" /usr/local/bin/node; "
            "ln -sf \"$NODE_BIN/npm\" /usr/local/bin/npm; "
            "ln -sf \"$NODE_BIN/npx\" /usr/local/bin/npx; "
            "ls -la /usr/local/bin/node /usr/local/bin/npm; "
            "test -x /usr/local/bin/node && echo 'node 可执行' || echo 'ERROR: node 不可执行'; "
            "echo '=== 5/6 清理临时文件 ==='; "
            "rm -f \"/tmp/$NODE_TAR\"; "
            "echo '清理完成'; "
            "echo '=== 6/6 验证安装 ==='; "
            "export PATH=$PATH:/usr/local/bin; "
            "echo -n 'node: '; /usr/local/bin/node -v 2>&1; "
            "echo -n 'npm: '; /usr/local/bin/npm -v 2>&1"
        )
        install_cmd = f"bash -c {shlex.quote(install_script)}"
        self._node_worker = StreamCommandWorker(self.ssh, install_cmd, sudo=True)
        self._node_worker.line_received.connect(self._install_dlg.append_log)
        self._node_worker.error.connect(lambda e: self._install_dlg.append_log(f"[通道错误] {e}"))
        self._node_worker.finished_cmd.connect(self._on_node_installed)
        self._track(self._node_worker)
        self._node_worker.start()

    def _on_node_installed(self, exit_code: int):
        """Node.js 安装完成回调（流式命令结束，退出码 exit_code）"""
        self.node_install_btn.setEnabled(True)
        self.node_install_btn.setText("安装 Node.js")
        # 从弹窗日志区收集输出判断是否成功
        log_text = ""
        if hasattr(self, "_install_dlg") and self._install_dlg:
            log_text = self._install_dlg.log_view.toPlainText()
            self._install_dlg.append_log(f"=== 安装结束（退出码: {exit_code}）===")
        has_node = "node: v" in log_text
        if hasattr(self, "_install_dlg") and self._install_dlg:
            self._install_dlg.set_finished(has_node)
        if has_node:
            self.node_warn_text.setText("Node.js 安装成功，正在重新探测环境...")
            msg.info(self, "安装成功", "Node.js 已安装成功，正在重新探测环境并刷新页面。")
            parent = self.parent()
            while parent and not hasattr(parent, "reprobe"):
                parent = parent.parent()
            if parent and hasattr(parent, "reprobe"):
                parent.reprobe()
        else:
            self.node_warn_text.setText("Node.js 安装失败，请查看安装弹窗中的日志定位问题。")

    def on_bind(self):
        self.stop_log()

    def stop_log(self):
        self.log_active = False
        if self.log_thread:
            self.log_thread.stop()
            self.log_thread.wait(1000)
            self.log_thread = None
        for c in self.site_cards:
            c.log_btn.setChecked(False)
            c.log_btn.setText("实时日志")
        self.log_view.setVisible(False)

    # ---------- 状态刷新 ----------

    def _refresh_status(self):
        if not self.ssh or not self.ssh.is_connected:
            return
        for card in self.site_cards:
            node_proc = (card.site.get("node_proc") or "").strip()
            web_dir = (card.site.get("web_dir") or "").strip()
            if not node_proc or not web_dir:
                card.set_status(False)
                card.reset_info()
                continue
            # 状态检测三级回退：① PID 文件 ② 端口反查真实 PID（顺带回写 PID 文件自愈）
            # ③ 只确认端口监听（拿不到 PID 时兜底，前端显示"无PID记录"）
            pid_file = f"{web_dir}/.server.pid"
            port = (card.site.get("port") or "").strip()
            port_grep = f"netstat -tlnp 2>/dev/null | grep -qE ':{port}([^0-9]|$)'" if port else "false"
            pid_find = self._port_pid_cmd(port, web_dir) if port else ""
            path_export = self._node_path_export()
            check = (
                f"{path_export}"
                f"if [ -f {pid_file} ]; then p=$(cat {pid_file}); "
                f"if kill -0 $p 2>/dev/null; then echo \"RUN $p\"; ps -p $p -o etime= 2>/dev/null; exit 0; fi; fi; "
                f"P=$({pid_find}); "
                f"if [ -n \"$P\" ] && kill -0 $P 2>/dev/null; then "
                f"echo $P > {pid_file}; echo \"RUN $P\"; ps -p $P -o etime= 2>/dev/null; exit 0; fi; "
                f"if {port_grep}; then echo \"RUN_PORT {port}\"; else echo \"STOP\"; fi"
            )
            self.run_cmd(check, lambda r, c=card: self._on_site_check(c, r), timeout=12)
        ncheck = "ps -ef | grep -w nginx | grep -v grep | awk '{print $2}' | head -1"
        self.run_cmd(ncheck, self._on_nginx_check, timeout=8)

    def _on_site_check(self, card: SiteCard, r):
        if getattr(card, "_dead", False):
            return
        out = r.stdout.strip() if r.ok else ""
        active = out.startswith("RUN")
        card.set_status(active)
        if active:
            lines = out.splitlines()
            first = lines[0] if lines else ""
            if first.startswith("RUN_PORT"):
                # PID 文件丢失但端口在监听（外部启动/Web Station 重建目录）。
                # 进程号标签只说明运行状态，端口由卡片自带 port_lbl 显示，避免重复。
                card.pid_lbl.setText("进程号：运行中·无PID记录")
                card.etime_lbl.setText("运行时长：—")
            else:
                # 正常：第一行 "RUN <pid>"，第二行为 ps 输出的运行时长
                try:
                    pid = first.split()[1]
                except IndexError:
                    pid = "—"
                card.pid_lbl.setText(f"进程号：{pid}")
                etime = lines[1].strip() if len(lines) > 1 else "—"
                card.etime_lbl.setText(f"运行时长：{etime}")
        else:
            card.reset_info()

    def _on_etime(self, card: SiteCard, pid, r):
        if getattr(card, "_dead", False):
            return
        etime = r.stdout.strip() if r.ok else "—"
        card.etime_lbl.setText(f"运行时长：{etime}")

    def _on_nginx_check(self, r):
        active = r.ok and bool(r.stdout.strip())
        self.nginx_dot.setStyleSheet("color:#4caf50;" if active else "color:#bbb;")
        self.nginx_status.setText("运行中" if active else "已停止")
        self.nginx_status.setStyleSheet(
            "font-size:11px; color:#2e7d32;" if active else "font-size:11px; color:#999;"
        )
        if active and "—" in self.nginx_path_lbl.text():
            self.run_cmd("nginx -v 2>&1 | head -1", self._on_nginx_path, timeout=8)

    def _on_nginx_path(self, r):
        lines = [l for l in r.stdout.strip().splitlines() if l.strip()]
        if lines:
            self.nginx_path_lbl.setText(f"版本：{lines[0]}")

    # ---------- 网站启停 ----------

    def _site_toggle(self, card: SiteCard):
        """启停切换：运行中->停止，已停止->启动"""
        if card.running:
            self._site_action(card, "stop")
        else:
            self._site_action(card, "start")

    def _site_action(self, card: SiteCard, action: str):
        site = card.site
        web_dir = (site.get("web_dir") or "").strip()
        node_proc = (site.get("node_proc") or "node server.js").strip()
        if not web_dir:
            QMessageBox.warning(self, "失败", "该网站没有目录信息，无法操作")
            return
        # 操作中保护：转圈动画 + 禁用按钮，防重复点击（完成后 _after_action 恢复）
        if getattr(card, "_busy", False):
            return
        card.set_busy(True, action)
        base = "export HOME=/tmp; " + self._node_path_export()
        pid_file = f"{web_dir}/.server.pid"
        port = (site.get("port") or "").strip()
        if action == "stop":
            # 停止必须真正杀掉进程：pid_file + cwd 杀 + 按端口杀（否则残留进程占端口，
            # 下次启动必然 EADDRINUSE；pkill -f 目录路径匹配不到 node 命令行，不可靠）
            cwd_kill, port_kill = self._kill_cmds(web_dir, port)
            cmd = (f"{base}cd {web_dir} && {{ "
                   f"[ -f {pid_file} ] && kill -9 $(cat {pid_file}) 2>/dev/null; rm -f {pid_file}; "
                   f"{cwd_kill}"
                   f"{port_kill}"
                   f"{self._path_kill_snippet(web_dir)}"
                   f"touch .nasmanager_stopped; sleep 1; [ -n \"{port}\" ] && netstat -tlnp 2>/dev/null | grep -qE ':{port}([^0-9]|$)' && echo 'PORT_STILL_BUSY' || echo 'PORT_FREE'; true; }}")
            self.run_cmd(cmd, lambda r, a=action, c=card: self._after_action(c, a, r), timeout=20)
            return
        # start / restart：先检测端口是否被占用，占用则弹窗让用户决定杀不杀
        if not port:
            # 没有端口信息：直接启动（只清本网站旧进程，不碰端口）
            self._do_site_start(card, action, web_dir, node_proc, base, pid_file, kill_port=False)
            return
        self.status_msg(f"检测端口 {port} 占用情况...")
        probe = f"{base}{self._port_owner_cmd(port)}"
        self.run_cmd(probe,
                     lambda r: self._on_port_probe(card, action, web_dir, node_proc, base, pid_file, port, r),
                     timeout=12)

    def _on_port_probe(self, card, action, web_dir, node_proc, base, pid_file, port, r):
        """端口占用检测回调。
        区分两种占用：① 占用者就是本站目录（PID 记录丢失但进程在跑）；② 被其他进程占用。"""
        raw = (r.stdout or "").strip() if r.ok else ""
        owners = []  # [(pid, cwd)]
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln or "|" not in ln:
                continue
            pid, cwd = ln.split("|", 1)
            owners.append((pid.strip(), cwd.strip()))
        # 探测失败（拿不到占用者详情，如 node 不可用）时退回旧的 netstat 判断兜底
        if r.ok and not owners:
            # 无任何占用者 → 端口空闲，直接启动
            self._do_site_start(card, action, web_dir, node_proc, base, pid_file, kill_port=False)
            return
        if not r.ok or not owners:
            self._do_site_start(card, action, web_dir, node_proc, base, pid_file, kill_port=False)
            return
        self_pids = [p for p, cwd in owners if cwd == web_dir]
        other = [f"{p}  {cwd or '(无权限读取目录)'}" for p, cwd in owners if cwd != web_dir]
        if self_pids and not other:
            # 占用者全部是本站自己：进程其实在运行，只是 .server.pid 记录丢了
            if action == "start":
                # 用户以为停了想启动，实际在跑：恢复 PID 记录 + 刷新状态，不重复拉起进程
                self_pid = self_pids[0]
                self.run_cmd(f"echo {self_pid} > {pid_file}", lambda rr: None, timeout=8)
                card.set_busy(False)
                card.set_status(True)
                card.pid_lbl.setText(f"进程号：{self_pid}")
                self.status_msg(f"网站实际已在运行（PID {self_pid}），已恢复进程记录")
                self._refresh_status()
                return
            # restart：明确要重启，占用者是自己，直接杀掉重启，不再弹框询问
            self._do_site_start(card, action, web_dir, node_proc, base, pid_file, kill_port=True)
            return
        # 被其他进程占用：弹窗让用户选择杀不杀
        info = "\n".join(other[:6]) if other else raw[:600]
        if msg.question(self, "端口被占用",
                        f"端口 {port} 已被其他进程占用：\n\n{info}\n\n"
                        f"是否杀掉占用进程后{'重启' if action == 'restart' else '启动'}该网站？"):
            self._do_site_start(card, action, web_dir, node_proc, base, pid_file, kill_port=True)
        else:
            self._do_site_start(card, action, web_dir, node_proc, base, pid_file, kill_port=False)

    @staticmethod
    def _path_kill_snippet(web_dir: str) -> str:
        """按路径杀进程但排除当前 shell 自身。

        pkill -f '<路径>' 会匹配到执行命令的 sh -c 进程本身（命令串里就含该路径），
        把 shell 杀掉导致命令以 143 退出，误报"停止失败"（实际已停）。
        改用 ps+grep 过滤命令行含该路径的进程，并排除当前 shell PID($$)，
        只杀真正与该站点路径相关的进程，不影响其他网站。
        """
        shq = "'" + web_dir.replace("'", "'\\''") + "'"
        return (f"for _p in $(ps -ef | grep -F {shq} | grep -v grep | awk '{{print $2}}'); do "
                f"[ \"$_p\" != \"$$\" ] && kill -9 \"$_p\" 2>/dev/null; done; ")

    def _kill_cmds(self, web_dir, port):
        """生成杀进程命令片段（start/stop/restart 共用，普通用户无需 root）。
        纯 shell（awk + /proc），不依赖 node——群晖自带 node 版本可能过老，跑不动
        箭头函数/padStart/Set 等语法会导致杀进程静默失败（表现为'停止不了'）。
        返回 (cwd_kill, port_kill)，均排除当前 shell 自身 $$。"""
        wd = web_dir.replace("'", "'\''")
        awk = '$4=="0A"{n=split($2,a,":");if(toupper(a[n])==hp){print $10;exit}}'
        # 按工作目录杀：node 命令行只有 `node server.js` 不含目录，pkill -f 抓不到，必须比对 cwd
        cwd_kill = (
            "for _x in /proc/[0-9]*; do "
            "_kp=${_x#/proc/}; "
            '[ "$_kp" = "$$" ] && continue; '
            "_cw=$(readlink /proc/$_kp/cwd 2>/dev/null); "
            "if [ \"$_cw\" = '" + wd + "' ]; then kill -9 \"$_kp\" 2>/dev/null; fi; done; "
        )
        port_kill = ""
        if port:
            port_kill = (
                "HP=$(printf '%04X' " + str(port) + "); "
                'INO=$(awk -v hp="$HP" ' + "'" + awk + "'" + " /proc/net/tcp /proc/net/tcp6 2>/dev/null); "
                'if [ -n "$INO" ]; then for d in /proc/[0-9]*/fd; do '
                'pid=${d#/proc/}; pid=${pid%/fd}; '
                '[ "$pid" = "$$" ] && continue; '
                'if ls -l "$d" 2>/dev/null | grep -q "socket:\\[$INO\\]"; then kill -9 "$pid" 2>/dev/null; fi; '
                'done; fi; '
            )
        return cwd_kill, port_kill

    @staticmethod
    def _port_owner_cmd(port: str) -> str:
        """探测占用该端口的进程，每行 'PID|工作目录'。纯 shell（awk+/proc），不依赖 node。"""
        awk = '$4=="0A"{n=split($2,a,":");if(toupper(a[n])==hp){print $10;exit}}'
        awk_part = "INO=$(awk -v hp=\"$HP\" '" + awk + "' /proc/net/tcp /proc/net/tcp6 2>/dev/null); "
        loop_part = (
            'if [ -n "$INO" ]; then for d in /proc/[0-9]*/fd; do '
            'pid=${d#/proc/}; pid=${pid%/fd}; '
            'if ls -l "$d" 2>/dev/null | grep -q "socket:\\[$INO\\]"; then '
            'echo "$pid|$(readlink /proc/$pid/cwd 2>/dev/null)"; fi; done; fi; echo; '
        )
        return "HP=$(printf '%04X' " + str(port) + "); " + awk_part + loop_part

    @staticmethod
    def _port_pid_cmd(port: str, web_dir: str) -> str:
        """按端口反查单个占用 PID（优先 cwd 匹配 web_dir），输出纯 PID，找不到输出空。纯 shell。"""
        wd = web_dir.replace("'", "'\\''")
        awk = '$4=="0A"{n=split($2,a,":");if(toupper(a[n])==hp){print $10;exit}}'
        awk_part = "INO=$(awk -v hp=\"$HP\" '" + awk + "' /proc/net/tcp /proc/net/tcp6 2>/dev/null); "
        loop_part = (
            'RP=""; if [ -n "$INO" ]; then for d in /proc/[0-9]*/fd; do '
            'pid=${d#/proc/}; pid=${pid%/fd}; '
            'if ls -l "$d" 2>/dev/null | grep -q "socket:\\[$INO\\]"; then '
            'cwd=$(readlink /proc/$pid/cwd 2>/dev/null); '
            "if [ \"$cwd\" = '" + wd + "' ]; then RP=$pid; break; fi; "
            '[ -z "$RP" ] && RP=$pid; fi; done; fi; echo "$RP"'
        )
        return "HP=$(printf '%04X' " + str(port) + "); " + awk_part + loop_part



    def _do_site_start(self, card, action, web_dir, node_proc, base, pid_file, kill_port):
        """实际执行启动/重启：可选的端口占用清理 + 旧进程清理 + nohup 启动"""
        port = (card.site.get("port") or "").strip()
        cwd_kill, port_kill = self._kill_cmds(web_dir, port if kill_port else "")
        # 第一步：先同步杀进程（给足超时，等真正杀完）。绝不能和 nohup 启动塞进同一条命令——
        # run_detached 只等 2 秒就关 channel，前面杀进程遍历一慢，nohup 还没执行就被掐断，
        # 造成"根本没启动"或端口残留。杀完再单独用极简命令后台启动。
        kill_cmd = (f"{base}cd {web_dir} && {{ "
                    f"[ -f {pid_file} ] && kill -9 $(cat {pid_file}) 2>/dev/null; rm -f {pid_file}; "
                    f"{cwd_kill}"
                    f"{port_kill}"
                    f"{self._path_kill_snippet(web_dir)}"
                    f"}}")
        self.run_cmd(kill_cmd,
                     lambda r, a=action, c=card: self._start_after_kill(
                         c, a, web_dir, node_proc, base, pid_file),
                     timeout=30)

    def _start_after_kill(self, card: SiteCard, action, web_dir, node_proc, base, pid_file):
        """第二步：杀干净后后台启动。
        群晖 busybox 的 nohup 会 fork 出 node 再自行退出，$! 是 nohup 的瞬态 PID（已死），
        真正监听端口的是它 fork 出的子进程（另一个 PID）。所以启动后要按端口反查真实
        监听 PID 覆盖 .server.pid，否则状态检测 kill -0 失败，卡片误判为'运行中·无PID记录'。"""
        port = (card.site.get("port") or "").strip()
        parts = [
            base + "cd " + web_dir + " && { ",
            "rm -f .nasmanager_stopped; ",
            "nohup " + node_proc + " > server.log 2>&1 & ",
            'NPID=$!; echo "$NPID" > ' + pid_file + '; ',
        ]
        if port:
            find = self._port_pid_cmd(port, web_dir)
            parts.append("sleep 1; ")
            parts.append("REAL=$(" + find + "); ")
            parts.append('if [ -z "$REAL" ]; then sleep 0.8; REAL=$(' + find + '); fi; ')
            parts.append('if [ -z "$REAL" ]; then sleep 0.8; REAL=$(' + find + '); fi; ')
            parts.append('if [ -n "$REAL" ]; then echo "$REAL" > ' + pid_file + '; NPID=$REAL; fi; ')
        else:
            parts.append("sleep 0.5; ")
        parts.append('if kill -0 "$NPID" 2>/dev/null; then ')
        parts.append('echo "STARTED pid=$NPID pidfile=$(cat ' + pid_file + ' 2>/dev/null)" >&2; ')
        parts.append('else echo "FAILED pid=$NPID not alive; server.log tail:" >&2; tail -n 8 server.log 2>/dev/null >&2; fi; }')
        cmd = "".join(parts)
        self.run_detached_cmd(cmd, lambda r, a=action, c=card: self._after_action(c, a, r), wait=10 if port else 2)

    def _after_action(self, card: SiteCard, action, r):
        if getattr(card, "_dead", False):
            return
        # 操作完成（成功或失败）：收起转圈动画、恢复按钮
        card.set_busy(False)
        if r.ok:
            if action == "stop" and "PORT_STILL_BUSY" in (r.stdout or ""):
                msg.warn(self, "停止未完全成功", f"{card.site.get('name')} 已强制终止，但端口 {card.site.get('port')} 仍在监听。")
            else:
                extra = ""
                if action in ("start", "restart") and (r.stderr or "").strip():
                    extra = "（" + (r.stderr or "").strip().replace(chr(10), " ")[:150] + "）"
                self.status_msg(f"{card.site.get('name')} {action} 成功{extra}")
            QTimer.singleShot(1500, self._refresh_status)
            return
        # 失败：读取 NAS 上服务日志尾部，给用户更详细的失败原因（如 EADDRINUSE 的完整堆栈）
        web_dir = (card.site.get("web_dir") or "").strip()
        err = (r.stderr or r.stdout or "失败")[:200]
        if web_dir and action in ("start", "restart"):
            cmd = f"tail -n 12 {web_dir}/server.log 2>/dev/null || true"
            self.run_cmd(cmd, lambda rr, e=err, c=card, a=action: self._after_action_log(c, a, e, rr), timeout=8)
        else:
            msg.warn(self, "失败", f"{card.site.get('name')} {action} 失败:\n{err}")
            QTimer.singleShot(1500, self._refresh_status)

    def _after_action_log(self, card: SiteCard, action, err, r):
        if getattr(card, "_dead", False):
            return
        tail = (r.stdout or "").strip()
        if tail:
            msg.warn(self, f"{card.site.get('name')} {action} 失败",
                     f"命令错误：{err}\n\n--- 服务日志 server.log 末尾 ---\n{tail[:1500]}")
        else:
            msg.warn(self, "失败", f"{card.site.get('name')} {action} 失败:\n{err}")
        QTimer.singleShot(1500, self._refresh_status)

    def _site_health(self, card: SiteCard):
        port = (card.site.get("port") or "").strip()
        web_dir = (card.site.get("web_dir") or "").strip()
        if not port:
            QMessageBox.information(self, "提示", "该网站没有端口信息，无法健康检查")
            return
        card.health_lbl.setText("主页：检测中...")
        card.health_lbl.setStyleSheet(health_lbl_style("pending"))
        # 优先用用户设置的主页文件
        user_index = (card.site.get("index_file") or "").strip()
        if user_index:
            path = user_index if user_index.startswith("/") else f"/{user_index}"
            self._do_health_request(card, port, path)
            return
        # 否则自动检测网站目录下的实际首页文件
        if web_dir:
            safe_dir = web_dir.replace("'", "'\\''")
            idx_cmd = (
                f"for f in index.html index.htm index.php default.html default.htm "
                f"home.html home.htm home.php; do "
                f"test -f '{safe_dir}/$f' && echo \"STD:$f\" && break; done; "
                f"ls '{safe_dir}' 2>/dev/null | grep -iE '\\.(html|htm|php|asp|aspx|jsp)$' | head -5"
            )
            self.run_cmd(idx_cmd,
                         lambda r, c=card, p=port: self._on_index_found(c, p, r), timeout=8)
        else:
            self._do_health_request(card, port, "/")

    def _persist_site_index(self, card: SiteCard):
        """把主页文件设置持久化到连接配置"""
        if not self.conn:
            return
        try:
            from config import ConfigManager
            ConfigManager().update(self.conn)
        except Exception:
            pass

    def _persist_site_color(self, card: SiteCard):
        """把网站颜色设置持久化到连接配置"""
        if not self.conn:
            return
        try:
            from config import ConfigManager
            ConfigManager().update(self.conn)
        except Exception:
            pass

    def _on_index_found(self, card: SiteCard, port: str, r):
        """首页文件检测结果：优先用标准首页，其次用目录下第一个 html/php，兜底用 /"""
        out = (r.stdout or "").strip()
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        index_file = ""
        for l in lines:
            if l.startswith("STD:"):
                index_file = l[4:]
                break
        if not index_file and lines:
            # 没有标准首页，用目录下第一个 html/php
            index_file = lines[0]
        # 记录自动检测到的主页，供打开网页列表拼 URL 使用
        card.site["_auto_index"] = index_file
        path = f"/{index_file}" if index_file else "/"
        self._do_health_request(card, port, path)

    def _do_health_request(self, card: SiteCard, port: str, path: str):
        """用 node 发 HTTP 请求到指定路径，进行健康检查"""
        # 健康检查用 node 发 HTTP 请求（群晖精简版可能没有 curl，node 一定存在）
        node_code = ("const h=require('http');"
                     "const r=h.get({host:'127.0.0.1',port:" + port + ",path:'" + path + "',timeout:5000},"
                     "function(x){console.log(x.statusCode);process.exit(0)});"
                     "r.on('error',function(){console.log('ERR');process.exit(1)});"
                     "r.setTimeout(5000,function(){console.log('TIMEOUT');process.exit(1)})")
        cmd = (f"export HOME=/tmp; {self._node_path_export()}"
               f"node -e \"{node_code}\" 2>/dev/null")
        self.run_cmd(cmd, lambda r, c=card: self._on_site_health(c, r, path), timeout=12)

    def _on_site_health(self, card: SiteCard, r, path: str = "/"):
        if getattr(card, "_dead", False):
            return
        code = (r.stdout or "").strip()
        if code in ("ERR", "TIMEOUT") or not code:
            text, state = "连接失败", "fail"
        elif code.startswith("2") or code.startswith("3"):
            text, state = path, "ok"
        else:
            # 不通：显示错误代码（如 404/500），金褐色与「连接失败」的红色区分开
            text, state = f"{path}（{code}）", "http"
        card.health_lbl.setText(f"主页：{text}")
        # 内联样式会覆盖全局 hover，这里一并补上随状态区分的悬浮动效
        card.health_lbl.setStyleSheet(health_lbl_style(state))

    def _site_candidate_urls(self, site: dict):
        """构造该网站全部候选访问地址，返回 [(来源标签, url)]，去重且保序。"""
        out, seen = [], set()

        def add(tag, url):
            url = (url or "").strip()
            if url and url not in seen:
                seen.add(url)
                out.append((tag, url))

        port = (site.get("port") or "").strip()
        host = self.conn.host if getattr(self, "conn", None) else ""
        path = self._site_nginx_path(site)
        domains = self.probe.get("domains") or []
        # 主页不是 index 时，URL 带上主页文件名（手动设置优先，其次自动检测）
        suffix = (site.get("index_file") or site.get("_auto_index") or "").strip()

        # 优先外网域名（用户最想要的访问方式）
        for d in domains:
            if path:
                add("外网 HTTPS", f"https://{d}/{path}/{suffix}")
                add("外网 HTTP", f"http://{d}/{path}/{suffix}")
            else:
                add("外网 HTTPS", f"https://{d}/{suffix}")
                add("外网 HTTP", f"http://{d}/{suffix}")
        if host and path:
            add("内网反代", f"http://{host}/{path}/{suffix}")
        if host and port:
            add("内网直连", f"http://{host}:{port}/{suffix}")
        add("网站地址", site.get("web_url"))  # 兜底：扫描时记录的原始地址
        return out

    def _site_open(self, card: SiteCard):
        cands = self._site_candidate_urls(card.site)
        if not cands:
            QMessageBox.information(self, "提示", "没有可用的访问地址（缺少端口或域名）")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("打开网页")
        dlg.setMinimumWidth(620)
        dlg.setMinimumHeight(430)
        dlg.setStyleSheet(OPEN_DIALOG_STYLE)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 16, 20, 14)
        v.setSpacing(10)

        # —— 头部：固定置顶，不随地址列表滚动 ——
        title = QLabel("选择访问地址")
        title.setObjectName("DlgTitle")
        v.addWidget(title)

        # 服务未运行提示条：此刻外网打不开多半是因为服务没起，可一键启动
        warn_bar = QWidget()
        wbl = QHBoxLayout(warn_bar)
        wbl.setContentsMargins(12, 8, 12, 8)
        wbl.setSpacing(10)
        wtxt = QLabel("该网站服务当前未启动，外网地址此刻可能打不开（404 / 502）。")
        wtxt.setObjectName("WarnTxt")
        wtxt.setWordWrap(True)
        start_btn = QPushButton("启动服务")
        start_btn.setObjectName("WarnBtn")
        start_btn.setCursor(Qt.PointingHandCursor)
        wbl.addWidget(wtxt, 1)
        wbl.addWidget(start_btn, 0, Qt.AlignVCenter)
        warn_bar.setObjectName("WarnBar")
        if getattr(card, "running", False):
            warn_bar.setVisible(False)
        v.addWidget(warn_bar)

        # 提示行 + 主页文件（可点击选择）在同一行
        tip_row = QHBoxLayout()
        tip = QLabel("绿=可直接访问　橙=证书异常/HTTP错误　红=不通（已按可用性排序）")
        tip.setObjectName("DlgTip")
        cur_idx = (card.site.get("index_file") or card.site.get("_auto_index") or "").strip()
        index_show = QPushButton(f"主页：{cur_idx}" if cur_idx else "主页：点击选择")
        index_show.setCursor(Qt.PointingHandCursor)
        index_show.setStyleSheet(
            "QPushButton { font-size:12px; color:#2e7163; font-weight:bold; "
            "border:none; background:transparent; }"
            "QPushButton:hover { color:#1b5e50; text-decoration:underline; }")

        def choose_open_index():
            web_dir = (card.site.get("web_dir") or "").strip()
            if not web_dir:
                QMessageBox.information(self, "提示", "该网站没有目录信息")
                return
            safe_dir = web_dir.replace("'", "'\\''")
            ls_cmd = (f"cd '{safe_dir}' 2>/dev/null && find . -maxdepth 1 -type f "
                      r"\( -iname '*.html' -o -iname '*.htm' -o -iname '*.php' "
                      r"-o -iname '*.asp' -o -iname '*.aspx' -o -iname '*.jsp' \) "
                      "2>/dev/null | sed 's|^\\./||' | sort; true")
            r = self.ssh.run_command(ls_cmd, timeout=8)
            files = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
            if not files:
                QMessageBox.information(self, "提示", "该网站目录下未找到网页文件")
                return
            pick = QInputDialog(self)
            pick.setWindowTitle("选择主页文件")
            pick.setLabelText(f"目录：{web_dir}\n请选择主页文件：")
            pick.setComboBoxItems(files)
            pick.setComboBoxEditable(False)
            ci = (card.site.get("index_file") or card.site.get("_auto_index") or "").strip()
            if ci in files:
                pick.setTextValue(ci)
            pick.setOkButtonText("确定")
            pick.setCancelButtonText("取消")
            if pick.exec() != QInputDialog.Accepted:
                return
            fname = pick.textValue().strip()
            if not fname:
                return
            card.site["index_file"] = fname
            self._persist_site_index(card)
            dlg.accept()
            self._site_open(card)  # 重新打开，刷新 URL 列表
        index_show.clicked.connect(choose_open_index)
        tip_row.addWidget(tip)
        tip_row.addStretch()
        tip_row.addWidget(index_show)
        v.addLayout(tip_row)

        # —— 地址列表：只有这块滚动 ——
        rows_host = QWidget()
        rows_v = QVBoxLayout(rows_host)
        rows_v.setContentsMargins(2, 2, 8, 2)
        rows_v.setSpacing(8)
        rows = []
        for tag, url in cands:
            row = UrlRow(tag, url)

            def open_and_close(u, _d=dlg):
                QDesktopServices.openUrl(QUrl(u))
                _d.accept()

            row.clicked.connect(open_and_close)
            rows_v.addWidget(row)
            rows.append(row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(rows_host)
        v.addWidget(scroll, 1)  # stretch=1：占满剩余空间，头部因此钉在顶部不动

        bar = QHBoxLayout()
        bar.setSpacing(10)
        bar.addStretch()
        retry_btn = QPushButton("重新测试")
        retry_btn.setObjectName("DlgGhost")
        retry_btn.setCursor(Qt.PointingHandCursor)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("DlgPrimary")
        close_btn.setCursor(Qt.PointingHandCursor)
        bar.addWidget(retry_btn)
        bar.addWidget(close_btn)
        v.addLayout(bar)

        holder = {}
        state_of = {}
        order = {"ok": 0, "cert": 1, "http": 2, "bad": 3, "pending": 4}

        def reorder():
            """探测完成后按可用性重排：证书可信/可访问的地址自动置顶。"""
            seq = sorted(range(len(rows)),
                         key=lambda i: order.get(state_of.get(i, "pending"), 9))
            for i in seq:
                rows_v.addWidget(rows[i])  # addWidget 会把控件移到末尾，借此完成重排

        def start_test():
            old = holder.get("t")
            if old is not None:
                old.stop()
                old.wait(300)
            state_of.clear()
            for r in rows:
                r.set_result("pending", "检测中…")
            t = UrlProbeThread([u for _, u in cands])

            def one(i, s, d):
                rows[i].set_result(s, d)
                state_of[i] = s

            t.one_result.connect(one)
            t.finished.connect(reorder)
            holder["t"] = t
            t.start()

        def start_service():
            # 先关闭当前检查窗再执行启动：本弹窗是模态对话框，若启动的异步回调
            # 在弹窗销毁后仍访问其内部控件（旧代码 QTimer 4 秒后重测），会段错误闪退。
            # 改为：关窗 → 回主窗口启动 → 稍后重新打开检查窗确认结果（新弹窗，安全）。
            dlg.accept()
            self._site_action(card, "start")
            QTimer.singleShot(4000, lambda: self._site_open(card))

        def cleanup():
            t = holder.get("t")
            if t is not None:
                t.stop()
                t.wait(500)

        start_btn.clicked.connect(start_service)
        retry_btn.clicked.connect(start_test)
        close_btn.clicked.connect(dlg.reject)
        dlg.finished.connect(lambda _r=0: cleanup())
        start_test()
        dlg.exec()

    # ---------- 删除网站 ----------

    def _remove_site_proxy_blocks(self, fpath: str, site_path: str, port: str) -> bool:
        """从共享反代配置文件里只删除该网站的 location 块（修改文件，不整删文件）。
        用于 Web Station 共享配置（.location/.service/sites-available 等），避免误删其他网站反代。
        用花括号配对解析（_location_blocks），兼容块内嵌套 if/location，不会截断损坏文件。"""
        try:
            r = self.ssh.run_command(f"cat {fpath}", sudo=True, timeout=10)
        except Exception:
            return False
        content = r.stdout or ""
        if not content.strip():
            return False
        hits = []
        removed = []
        for start, block in self._location_blocks(content):
            # 只认真正 proxy_pass 到本端口的块；绝不靠站点短名(如 ai)判断，避免误删系统配置
            if port and self._proxy_block_uses_port(block, port):
                hits.append(block)
                removed.append((start, start + len(block)))
        if not hits:
            return False
        # 重建文件内容：跳过被删的块，其余原样保留
        parts = []
        prev = 0
        for s, e in removed:
            parts.append(content[prev:s])
            prev = e
        parts.append(content[prev:])
        new = "".join(parts)
        tmp = "/tmp/_nas_rm_proxy.conf"
        try:
            if not self.ssh.ssh_write_file(tmp, new):
                return False
            self.ssh.run_command(f"sh -c 'cp {tmp} {fpath} && rm -f {tmp}'", sudo=True, timeout=15)
            return True
        except Exception:
            return False

    def _site_delete(self, card: SiteCard):
        site = card.site
        name = (site.get("name") or "site")
        web_dir = (site.get("web_dir") or "").strip()
        node_proc = (site.get("node_proc") or "").strip()
        port = (site.get("port") or "").strip()
        site_path = self._site_nginx_path(site)

        # 确认弹窗（带复选项：是否删除网站文件夹）
        dlg = QDialog(self)
        dlg.setWindowTitle(f"删除网站「{name}」")
        dlg.setMinimumWidth(440)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        warn = QLabel(f"确认删除网站「{name}」？将执行以下清理：")
        warn.setStyleSheet("font-size:13px; font-weight:bold; color:#c62828;")
        lay.addWidget(warn)
        items = ["停止网站进程"]
        items.append("删除反向代理配置")
        items.append("删除开机自启设置")
        items.append("删除依赖 (node_modules)")
        for it in items:
            lbl = QLabel("● " + it)
            lbl.setStyleSheet("font-size:12px; color:#333;")
            lay.addWidget(lbl)
        chk = QCheckBox(f"同时删除网站文件夹（{web_dir}）")
        chk.setStyleSheet("font-size:12px; color:#555;")
        if web_dir:
            lay.addWidget(chk)
        if not self.need_sudo():
            tip = QLabel("提示：部分清理需要 sudo 权限，未设置将跳过（文件夹/反代/自启）。")
            tip.setStyleSheet("font-size:11px; color:#e65100;")
            tip.setWordWrap(True)
            lay.addWidget(tip)
        btns = QHBoxLayout()
        btns.addStretch()
        cancel_btn = QPushButton("取消")
        ok_btn = QPushButton("确认删除")
        ok_btn.setStyleSheet("background:#c62828; color:#fff; border:none; border-radius:5px; padding:6px 16px;")
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        lay.addLayout(btns)
        if dlg.exec() != QDialog.Accepted:
            return

        results = []
        try:
            # 1. 停止进程（PID 文件精确停止 + cwd/按端口杀，保证端口释放，不影响其他网站）
            pid_file = f"{web_dir}/.server.pid"
            if node_proc and web_dir:
                _cwd_kill, _port_kill = self._kill_cmds(web_dir, port)
                self.ssh.run_command(
                    f"if [ -f {pid_file} ]; then kill -9 $(cat {pid_file}) 2>/dev/null; rm -f {pid_file}; fi; "
                    f"{_cwd_kill}"
                    f"{_port_kill}"
                    f"{self._path_kill_snippet(web_dir)}"
                    f"true", timeout=10)
                results.append("已停止进程")

            # 2. 删除反向代理配置（先删专属文件，再对共享文件只删该网站的反代块，绝不整删共享配置）
            if self.need_sudo() and (port or site_path):
                removed = 0
                safe = self._safe_name(name)
                # 2a. 专属命名文件（软件自己写的反代文件，含双挂载点文件名）整删，安全
                loc_targets = self._location_targets(self.probe, safe)
                loc_bases = [t.rsplit("/", 1)[-1] for t, _mp in loc_targets]
                for suffix in loc_bases + [f"nas_{safe}.conf"]:
                    for base in ("/etc/nginx/conf.d", "/etc/nginx/sites-enabled"):
                        self.ssh.run_command(f"rm -f {base}/{suffix}", sudo=True, timeout=10)
                # 2b. 精确找“proxy_pass 到本端口”的文件，再只删其中属于本端口的 location 块
                #     （不拿站点短名 grep，避免 ai 撞碎系统文件导致误删）
                fl = self.ssh.run_command(
                    f"grep -rlE 'proxy_pass[[:space:]]+https?://(127\\.0\\.0\\.1|localhost):{port}([^0-9]|$)' "
                    f"{PROXY_DIRS} 2>/dev/null",
                    timeout=12, sudo=True)
                if fl.stdout and fl.stdout.strip():
                    for f in fl.stdout.splitlines():
                        f = f.strip()
                        if not f:
                            continue
                        base = f.rsplit("/", 1)[-1]
                        # 专属文件已在 2a 删掉，跳过
                        if (any(lb in base for lb in loc_bases)
                                or f"nas_{safe}.conf" in base):
                            continue
                        if self._remove_site_proxy_blocks(f, site_path, port):
                            removed += 1
                    if removed:
                        results.append(f"已清理 {removed} 个反代配置")
                else:
                    results.append("未找到反代配置")
                # 2a/2b 都可能删过配置文件：先 nginx -t 校验再 reload，失败不重载不破坏现状
                t = self.ssh.run_command("nginx -t 2>&1", sudo=True, timeout=20)
                if self._nginx_t_passed((t.stdout + t.stderr).strip()):
                    self.ssh.run_command("nginx -s reload", sudo=True, timeout=20)
                    results.append("nginx 已重载")
                else:
                    results.append("⚠ nginx 配置测试未通过，未重载（请检查反代配置）")

            # 3. 删除自启脚本
            rc_file = self._rc_file(card)
            if self.need_sudo():
                self.ssh.run_command(f"rm -f {rc_file}", sudo=True, timeout=10)
                results.append("已删除开机自启设置")

            # 4. 删除依赖（保留文件夹时删 node_modules）
            if web_dir:
                if chk.isChecked():
                    pass  # 文件夹整体删除会带上依赖
                elif self.need_sudo():
                    self.ssh.run_command(f"rm -rf {web_dir}/node_modules", sudo=True, timeout=20)
                    results.append("已删除依赖")

            # 5. 删除网站文件夹（可选）
            if chk.isChecked() and web_dir and self.need_sudo():
                self.ssh.run_command(f"rm -rf {web_dir}", sudo=True, timeout=30)
                results.append("已删除网站文件夹")

            # 6. 从连接配置移除并保存
            if self.conn and self.conn.sites:
                self.conn.sites = [s for s in self.conn.sites
                                   if not (s.get("web_dir") == web_dir and s.get("name") == name)]
                from config import ConfigManager
                ConfigManager().update(self.conn)
            self._rebuild_sites()
            self.status_msg(f"网站「{name}」已删除")
            msg.info(self, "删除完成", f"网站「{name}」已删除。\n\n" + "\n".join(results))
        except Exception as e:
            QMessageBox.warning(self, "删除出错", f"部分操作未完成:\n{e}")

    # ---------- 重新扫描 ----------

    def _rescan_sites(self):
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.warning(self, "提示", "未连接")
            return
        telemetry.track("重新扫描网站")
        self.status_msg("正在重新扫描网站...")
        def on_done(result):
            if self.conn:
                self.conn.web_dir = result.get("web_dir", "")
                self.conn.node_proc = result.get("node_proc", "")
                self.conn.health_url = result.get("health_url", "")
                self.conn.web_url = result.get("web_url", "")
                self.conn.sites = result.get("sites", [])
                from config import ConfigManager
                ConfigManager().update(self.conn)
                self._rebuild_sites()
                self._refresh_status()
                n = len(self.conn.sites)
                self.status_msg(f"扫描完成，发现 {n} 个网站")
        try:
            th = _RescanThread(self.ssh)
            th.done.connect(on_done)
            th.fail.connect(lambda m: QMessageBox.warning(self, "扫描失败", str(m)))
            self._track(th)
            th.start()
        except Exception as e:
            QMessageBox.warning(self, "扫描失败", str(e))

    # ---------- 实时日志 ----------

    def _toggle_site_log(self, card: SiteCard):
        for c in self.site_cards:
            if c is not card:
                c.log_btn.setChecked(False)
        if card.log_btn.isChecked():
            self._start_site_log(card)
        else:
            self.stop_log()

    def _start_site_log(self, card: SiteCard):
        if not self.ssh or not self.ssh.is_connected:
            return
        web_dir = (card.site.get("web_dir") or "").strip()
        if not web_dir:
            QMessageBox.warning(self, "提示", "该网站没有目录信息")
            return
        log_path = f"{web_dir}/server.log"
        self.log_view.clear()
        self.log_view.setVisible(True)
        self.log_active = True
        self.log_thread = LogReaderThread(self.ssh, log_path, lines=50)
        self.log_thread.line_received.connect(self._append_log)
        self.log_thread.error.connect(self._on_log_error)
        self.log_thread.start()

    def _append_log(self, line: str):
        self.log_view.appendPlainText(line)

    def _on_log_error(self, msg: str):
        self._append_log(f"[日志错误] {msg}")

    # ---------- Nginx 操作 ----------

    def _nginx_test(self):
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "需要 sudo 密码，请先在工具栏设置")
            return
        self.run_cmd(self.nginx_svc.get("config_test_cmd", "nginx -t"),
                     self._on_nginx_test, timeout=10, sudo=True)

    def _on_nginx_test(self, r):
        out = (r.stdout + r.stderr).strip()
        from widgets.service_manager import filter_warnings
        out = filter_warnings(out)
        msg.info(self, "Nginx 配置测试", out[:800] or "配置正常（无输出）")

    def _nginx_reload(self):
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "需要 sudo 密码，请先在工具栏设置")
            return
        self.run_cmd("nginx -s reload", self._on_nginx_reload, timeout=10, sudo=True)

    def _on_nginx_reload(self, r):
        from widgets.service_manager import filter_warnings
        if r.ok:
            self.status_msg("Nginx 已重载")
        else:
            err = filter_warnings(r.stderr or r.stdout)
            QMessageBox.warning(self, "失败", f"重载失败 (exit {r.exit_code}):\n{err[:300]}")

    def _show_proxy(self):
        cmd = f"grep -rn proxy_pass {PROXY_DIRS} 2>/dev/null | head -30"
        self.run_cmd(cmd, self._on_proxy, timeout=10, sudo=True)

    def _on_proxy(self, r):
        if not r.stdout.strip():
            QMessageBox.information(self, "反代配置", "未找到 proxy_pass 配置")
            return
        msg.info(self, "Nginx 反代列表", r.stdout.strip()[:1500])

    def _show_ports(self):
        # 动态关注端口：网站端口 + Web Station 端口 + 常用 Web 端口（不写死）
        ports = set(["80", "443", "3000", "5000"])
        if self.conn:
            for s in (self.conn.sites or []):
                p = (s.get("port") or "").strip()
                if p:
                    ports.add(p)
        ports |= set(self.probe.get("webstation_ssl_ports") or [])
        pat = "|".join(sorted(ports))
        self.run_cmd(f"netstat -tlnp 2>/dev/null | grep -E ':({pat})\\b'",
                     self._on_ports, timeout=10, sudo=True)

    def _on_ports(self, r):
        out = r.stdout.strip() or "未找到相关端口监听"
        msg.info(self, "端口监听", out[:1000])

    def _diagnose_env(self):
        web_dir = ""
        if self.conn:
            sites = self.conn.sites or []
            main = next((s for s in sites if s.get("startable")), (sites[0] if sites else None))
            web_dir = self.conn.web_dir or (main.get("web_dir") if main else "") or ""
        cmd = (
            "echo '== PATH =='; echo $PATH; "
            "echo '== node =='; command -v node 2>/dev/null; which node 2>/dev/null; "
            "ls -la /usr/local/bin/node /usr/bin/node /opt/bin/node "
            "/volume1/@appstore/Node.js/usr/local/bin/node /var/packages/Node.js/target/usr/local/bin/node 2>/dev/null; "
            "echo '== node -v =='; node -v 2>/dev/null; "
            f"echo '== 目录 =='; ls {web_dir} 2>/dev/null | head -20; "
            "echo '== END =='"
        )
        self.run_cmd(cmd, self._on_diag, timeout=15)

    def _on_diag(self, r):
        out = (r.stdout + r.stderr).strip()
        if not out:
            out = "无输出"
        msg.info(self, "环境诊断", out[:2000])


class _RescanThread(QThread):
    done = Signal(dict)
    fail = Signal(str)

    def __init__(self, ssh):
        super().__init__()
        self.ssh = ssh

    def run(self):
        try:
            from scan import EnvScanner
            result = EnvScanner(self.ssh).scan(None)
            self.done.emit(result)
        except Exception as e:
            self.fail.emit(str(e))


# 迁移后的基础常量和样式，保留旧名称兼容现有调用点。
PATH_EXPORT = _helper_path_export
PROXY_DIRS = _helper_proxy_dirs
health_lbl_style = _helper_health_lbl_style

# 线程类已迁移到 widgets.web_workers；保留名称兼容现有调用点。
LogReaderThread = _ExtractedLogReaderThread
StreamCommandWorker = _ExtractedStreamCommandWorker
InstallDepsThread = _ExtractedInstallDepsThread
UrlProbeThread = _ExtractedUrlProbeThread
_RescanThread = _ExtractedRescanThread
ClickableLabel = _ExtractedClickableLabel
ColorBar = _ExtractedColorBar
UrlRow = _ExtractedUrlRow
SpinnerWidget = _ExtractedSpinnerWidget
InstallProgressDialog = _ExtractedInstallProgressDialog
DependenciesDialog = _ExtractedDependenciesDialog
WebHealthDialog = _ExtractedWebHealthDialog
