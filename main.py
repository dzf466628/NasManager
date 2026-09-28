# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
NAS 管理器 —— 主入口
布局：左侧连接列表 + 功能导航，右侧 QStackedWidget 内容区
"""
import sys
import os
import re
import time
import subprocess
import tempfile
import traceback
import urllib.request
from pathlib import Path
from PySide6.QtCore import Qt, QThread, Signal, QSize, QTimer
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QBrush
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLabel, QPushButton,
    QToolBar, QStatusBar, QMessageBox, QInputDialog, QFrame, QLineEdit,
    QProgressDialog, QSizePolicy,
)

from config import ConfigManager, Settings
from ssh_client import SSHClient
from env_probe import EnvProbe
from splash import SplashScreen, DEFAULT_DURATION_MS
from app_paths import resource_path
import telemetry
from widgets.connection_dialog import ConnectionDialog
from widgets.dashboard import DashboardWidget
from widgets.service_manager import ServiceManagerWidget
from widgets.port_viewer import PortViewerWidget
from widgets.web_panel import WebPanelWidget
from widgets.file_browser import FileBrowserWidget
from widgets.terminal import TerminalWidget
from widgets.sys_config import SysConfigWidget
from widgets.scan_dialog import ScanDialog
from widgets.about import AboutDialog

# 界面版本号（右下角状态栏显示；发版时递增）
APP_VERSION = "v1.1.79"

# ---------- 自动更新 ----------

UPDATE_BASE_URL = "https://dudua.synology.me:8443/download/NasManager/"


def parse_version(v: str) -> tuple:
    """解析版本号为可比较元组，'v1.1.31' -> (1,1,31)；避免字符串比较 1.1.9 > 1.1.10 的错误"""
    v = (v or "").lstrip("vV")
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class UpdateChecker(QThread):
    """后台扫描下载目录，正则提取所有安装包版本号，取最新版"""
    found = Signal(str, str)   # latest_version, download_url
    failed = Signal(str)

    def __init__(self, base_url: str = UPDATE_BASE_URL):
        super().__init__()
        self.base_url = base_url

    def run(self):
        try:
            html = ""
            # 先试目录 URL，失败再试直接访问隐藏的 .index.php
            for url in (self.base_url, self.base_url + ".index.php"):
                try:
                    req = urllib.request.Request(
                        url, headers={"User-Agent": "NasManager-Updater"})
                    import ssl
                    # 启用证书验证（安全）：安装包下载不做完整性校验，
                    # 证书不验证会被中间人替换成恶意 exe 并以管理员静默安装
                    ctx = ssl.create_default_context()
                    with urllib.request.urlopen(req, timeout=12, context=ctx) as resp:
                        html = resp.read().decode("utf-8", errors="replace")
                    if html:
                        break
                except Exception:
                    continue
            versions = re.findall(r"NasManager_Setup_(\d+\.\d+\.\d+)\.exe", html)
            if not versions:
                self.failed.emit("目录中未找到安装包")
                return
            latest = max(versions, key=parse_version)
            url = f"{self.base_url}NasManager_Setup_{latest}.exe"
            self.found.emit(latest, url)
        except Exception as e:
            self.failed.emit(str(e))


class UpdateDownloader(QThread):
    """后台下载安装包，带进度信号"""
    progress = Signal(int, int, float)  # downloaded_bytes, total_bytes, speed_MBps
    finished_ok = Signal(str)           # local file path
    failed = Signal(str)

    def __init__(self, url: str):
        super().__init__()
        self.url = url

    def run(self):
        try:
            filename = self.url.rsplit("/", 1)[-1]
            filepath = os.path.join(tempfile.gettempdir(), filename)
            import ssl
            # 启用证书验证（安全）：安装包将被静默以管理员安装，必须校验来源
            ctx = ssl.create_default_context()
            req = urllib.request.Request(
                self.url, headers={"User-Agent": "NasManager-Updater"})
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                start = time.time()
                with open(filepath, "wb") as f:
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        elapsed = time.time() - start
                        speed = downloaded / elapsed / 1048576 if elapsed > 0 else 0
                        self.progress.emit(downloaded, total, speed)
            if os.path.getsize(filepath) < 100000:
                self.failed.emit("下载文件异常（过小），可能下载失败")
                return
            self.finished_ok.emit(filepath)
        except Exception as e:
            self.failed.emit(str(e))

STYLE = """
QMainWindow { background: #f0f2f5; }
#Sidebar { background: #2e7163; }
#Sidebar QLabel { color: #eafff7; }
#Sidebar QListWidget {
    background: transparent; border: none; color: #eafff7;
    font-size: 13px; outline: none;
}
#Sidebar QListWidget::item { padding: 8px 14px; border-radius: 6px; margin: 1px 6px; }
#Sidebar QListWidget::item:selected { background: #399d87; }
#Sidebar QListWidget::item:hover { background: #3a8a78; }
#SidebarTitle { color: #bfe8db; font-size: 11px; font-weight: bold; padding: 10px 14px 4px; }
#AddBtn {
    background: #43a68d; color: #ffffff; border: none; border-radius: 6px;
    padding: 7px; margin: 6px 10px; font-size: 12px;
}
#AddBtn:hover { background: #54b99f; }
#Content { background: #ffffff; }
QStatusBar { background: #e6e8ec; font-size: 12px; }

/* ---------- 弹窗统一风格（与软件深绿主题一致） ---------- */
QMessageBox {
    background-color: #ffffff;
    border: 1px solid #d5dbe0;
    border-radius: 10px;
}
QMessageBox QLabel {
    color: #2b2f36;
    font-size: 13px;
    padding: 2px 4px;
}
QMessageBox QLabel#qt_msgbox_label {
    min-width: 320px;
    padding: 4px 2px;
}
QMessageBox QPushButton {
    background: #43a68d;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 6px 22px;
    min-height: 22px;
    font-size: 13px;
}
QMessageBox QPushButton:hover { background: #54b99f; }
QMessageBox QPushButton:pressed { background: #399d87; }
QMessageBox QPushButton:default { background: #2e7163; }
QMessageBox QPushButton:focus { border: 1px solid #2e7163; }
QMessageBox QMessageBox { background: #ffffff; }
/* 输入类弹窗（选择启动文件、设置端口等）同样使用深绿主题 */
QInputDialog {
    background-color: #ffffff;
}
QInputDialog QLabel {
    color: #2b2f36;
    font-size: 13px;
}
QInputDialog QLineEdit, QInputDialog QComboBox {
    background: #ffffff;
    border: 1px solid #c9d2d9;
    border-radius: 6px;
    padding: 5px 8px;
    color: #2b2f36;
    font-size: 13px;
    min-width: 220px;
}
QInputDialog QComboBox::drop-down {
    border: none;
    width: 22px;
}
QInputDialog QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #d5dbe0;
    selection-background-color: #43a68d;
    selection-color: #ffffff;
    color: #2b2f36;
}
QInputDialog QPushButton {
    background: #43a68d;
    color: #ffffff;
    border: none;
    border-radius: 6px;
    padding: 6px 22px;
    min-height: 22px;
    font-size: 13px;
}
QInputDialog QPushButton:hover { background: #54b99f; }
QInputDialog QPushButton:pressed { background: #399d87; }
QInputDialog QPushButton:default { background: #2e7163; }
"""

NAV_ITEMS = [
    ("📊 仪表盘", "dashboard"),
    ("⚙️ 服务管理", "services"),
    ("🔌 端口管理", "ports"),
    ("🌐 网页面板", "webpanel"),
    ("📁 文件管理", "files"),
    ("🖥️ 系统配置", "sysconfig"),
    ("💻 终端", "terminal"),
]


def make_status_dot(color: str, size: int = 10) -> QIcon:
    """生成圆形状态灯图标"""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.end()
    return QIcon(pix)


class ConnectThread(QThread):
    """后台建立 SSH 连接"""
    connected = Signal(bool, str)

    def __init__(self, ssh: SSHClient):
        super().__init__()
        self.ssh = ssh

    def run(self):
        ok = self.ssh.connect(timeout=10)
        if ok:
            r = self.ssh.run_command("hostname", timeout=8)
            name = r.stdout.strip() if r.ok else ""
            self.connected.emit(True, name)
        else:
            self.connected.emit(False, "")


class ProbeThread(QThread):
    """后台跑环境体检，动态抓取 NAS 真实环境，供所有页面共用"""
    finished_probe = Signal(dict, str)

    def __init__(self, ssh: SSHClient, use_sudo: bool = True):
        super().__init__()
        self.probe = EnvProbe(ssh, use_sudo=use_sudo)

    def run(self):
        try:
            result = self.probe.probe_all()
            self.finished_probe.emit(result, "")
        except Exception as e:
            self.finished_probe.emit({}, f"{e}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("NAS 管理器")
        self.resize(1200, 780)
        # 标题栏图标（与任务栏/开始菜单一致）
        try:
            icon_p = resource_path("assets") / "app.ico"
            if Path(icon_p).exists():
                self.setWindowIcon(QIcon(str(icon_p)))
        except Exception:
            pass
        self.config = ConfigManager()
        self.settings = Settings()
        self.ssh: SSHClient | None = None
        self.current_conn = None
        self._connect_thread = None
        self._probe_thread = None
        self.probe: dict = {}

        self._build_ui()
        self._refresh_conn_list()
        self.setStyleSheet(STYLE)

        # 启动 2 秒后自动检查更新（不阻塞界面）
        self._update_url = ""
        self._update_version = ""
        self._downloader = None
        QTimer.singleShot(2000, self._check_update)

    # ---------- UI 构建 ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.stack.setObjectName("Content")
        layout.addWidget(self.stack, 1)

        # 欢迎页
        self.welcome = QLabel("请在左侧选择或新建一个 NAS 连接")
        self.welcome.setAlignment(Qt.AlignCenter)
        self.welcome.setStyleSheet("color:#888; font-size:16px;")
        self.stack.addWidget(self.welcome)

        # 功能页
        self.dashboard = DashboardWidget(self.settings)
        self.service_mgr = ServiceManagerWidget()
        self.port_viewer = PortViewerWidget()
        self.web_panel = WebPanelWidget(self.settings)
        self.file_browser = FileBrowserWidget()
        self.sys_config = SysConfigWidget(self.settings)
        self.terminal = TerminalWidget()

        for w in (self.dashboard, self.service_mgr, self.port_viewer,
                  self.web_panel, self.file_browser, self.sys_config, self.terminal):
            self.stack.addWidget(w)

        # 状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("未连接")
        # 右下角永久版本号
        self.version_lbl = QLabel(f"NasManager {APP_VERSION}")
        self.version_lbl.setStyleSheet("color:#9aa0a6; font-size:11px; padding-right:6px;")
        self.status.addPermanentWidget(self.version_lbl)

        # 工具栏
        self._build_toolbar()

    def _build_sidebar(self):
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        v = QVBoxLayout(side)
        v.setContentsMargins(0, 10, 0, 10)
        v.setSpacing(0)

        title = QLabel("  NAS 连接")
        title.setObjectName("SidebarTitle")
        v.addWidget(title)

        self.conn_list = QListWidget()
        self.conn_list.setIconSize(QSize(10, 10))
        self.conn_list.itemDoubleClicked.connect(self._on_conn_double_clicked)
        self.conn_list.setContextMenuPolicy(Qt.CustomContextMenu)
        v.addWidget(self.conn_list, 1)

        add_btn = QPushButton("+ 新建连接")
        add_btn.setObjectName("AddBtn")
        add_btn.clicked.connect(self._add_connection)
        v.addWidget(add_btn)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background:#313244; max-height:1px; margin:6px 10px;")
        v.addWidget(sep)

        nav_title = QLabel("  功能导航")
        nav_title.setObjectName("SidebarTitle")
        v.addWidget(nav_title)

        self.nav_list = QListWidget()
        for label, key in NAV_ITEMS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        self.nav_list.setEnabled(False)
        v.addWidget(self.nav_list, 2)

        return side

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_connect = QAction("连接", self)
        self.act_connect.triggered.connect(self._connect_selected)
        tb.addAction(self.act_connect)

        self.act_disconnect = QAction("断开", self)
        self.act_disconnect.triggered.connect(self._disconnect)
        self.act_disconnect.setEnabled(False)
        tb.addAction(self.act_disconnect)

        tb.addSeparator()

        act_add = QAction("新建连接", self)
        act_add.triggered.connect(self._add_connection)
        tb.addAction(act_add)

        act_edit = QAction("编辑", self)
        act_edit.triggered.connect(self._edit_selected)
        tb.addAction(act_edit)

        act_del = QAction("删除", self)
        act_del.triggered.connect(self._delete_selected)
        tb.addAction(act_del)

        tb.addSeparator()

        act_sudo = QAction("设置 Sudo 密码", self)
        act_sudo.triggered.connect(self._set_sudo)
        tb.addAction(act_sudo)

        act_about = QAction("关于", self)
        act_about.setToolTip("查看软件信息、开源许可与第三方组件声明")
        act_about.triggered.connect(self._show_about)
        tb.addAction(act_about)

        # 弹簧，把「有新版本」推到工具栏最右侧
        update_spacer = QWidget()
        update_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(update_spacer)

        # 有新版本按钮（默认隐藏，检查到更新时显示，醒目橙色）
        self.act_update = QAction("⬇ 有新版本", self)
        self.act_update.setToolTip("点击下载并安装新版本")
        self.act_update.triggered.connect(self._start_update)
        self.act_update.setVisible(False)
        self.act_update.setCheckable(False)
        tb.addAction(self.act_update)

    def _show_about(self):
        """打开关于 / 帮助声明窗口"""
        dlg = AboutDialog(APP_VERSION, self)
        dlg.exec()

    # ---------- 连接列表 ----------

    def _refresh_conn_list(self):
        self.conn_list.clear()
        red = make_status_dot("#f38ba8")
        green = make_status_dot("#a6e3a1")
        for conn in self.config.connections:
            icon = green if (self.ssh and self.ssh.is_connected and self.current_conn and self.current_conn.id == conn.id) else red
            item = QListWidgetItem(icon, f"  {conn.name}")
            item.setData(Qt.UserRole, conn.id)
            item.setToolTip(f"{conn.username}@{conn.host}:{conn.port}")
            self.conn_list.addItem(item)

    def _selected_conn(self):
        item = self.conn_list.currentItem()
        if not item:
            return None
        return self.config.get(item.data(Qt.UserRole))

    def _on_conn_double_clicked(self, item):
        self._connect_selected()

    def _add_connection(self):
        telemetry.track("添加连接")
        dlg = ConnectionDialog(self)
        if dlg.exec():
            self.config.add(dlg.get_connection())
            self._refresh_conn_list()

    def _edit_selected(self):
        conn = self._selected_conn()
        if not conn:
            return
        dlg = ConnectionDialog(self, conn)
        if dlg.exec():
            self.config.update(dlg.get_connection())
            self._refresh_conn_list()

    def _delete_selected(self):
        conn = self._selected_conn()
        if not conn:
            return
        if QMessageBox.question(self, "确认", f"删除连接「{conn.name}」？") == QMessageBox.Yes:
            if self.current_conn and self.current_conn.id == conn.id:
                self._disconnect()
            self.config.delete(conn.id)
            self._refresh_conn_list()

    # ---------- 连接 / 断开 ----------

    def _connect_selected(self):
        conn = self._selected_conn()
        if not conn:
            QMessageBox.information(self, "提示", "请先选择一个连接")
            return
        if self.ssh and self.ssh.is_connected:
            self._disconnect()
        telemetry.track("连接NAS")
        self.status.showMessage(f"正在连接 {conn.host}...")
        self.ssh = SSHClient(conn.host, conn.port, conn.username, conn.password,
                             on_log=lambda m: self.status.showMessage(m))
        self.current_conn = conn
        self._connect_thread = ConnectThread(self.ssh)
        self._connect_thread.connected.connect(self._on_connected)
        self._connect_thread.start()

    def _on_connected(self, ok: bool, hostname: str):
        if ok:
            self.status.showMessage(f"已连接 {self.current_conn.name} ({hostname})")
            self.act_disconnect.setEnabled(True)
            self.act_connect.setEnabled(False)
            self.nav_list.setEnabled(True)
            self._refresh_conn_list()
            # 自动用连接密码作为 sudo 密码（群晖 SSH 密码即 sudo 密码），无需单独输入
            try:
                if self.current_conn.password:
                    self.ssh.set_sudo_password(self.current_conn.password)
            except Exception:
                pass
            # 把 ssh 传给各页面
            for w in (self.dashboard, self.service_mgr, self.port_viewer,
                      self.web_panel, self.file_browser, self.sys_config, self.terminal):
                w.bind_ssh(self.ssh)
            self.sys_config.set_conn_info(self.current_conn)
            self.web_panel.set_conn_info(self.current_conn)
            self.service_mgr.set_conn_info(self.current_conn)
            self.terminal.set_conn_info(self.current_conn)
            self.dashboard.set_conn_info(self.current_conn)
            self.port_viewer.set_conn_info(self.current_conn)
            self._on_nav_changed(self.nav_list.currentRow())
            # 后台跑环境体检（动态抓真实环境，适应不同 NAS，不依赖写死）
            self._start_probe()
            # 首次连接：若该连接还没有扫描过环境，弹窗扫描并保存
            if not self.current_conn.scanned():
                self._scan_environment()
        else:
            self.status.showMessage("连接失败")
            QMessageBox.critical(self, "连接失败", f"无法连接到 {self.current_conn.host}")
            self.ssh = None
            self.current_conn = None

    def _start_probe(self):
        """后台跑环境体检（动态抓真实环境，适应不同 NAS）"""
        if not self.ssh or not self.ssh.is_connected:
            return
        if self._probe_thread and self._probe_thread.isRunning():
            return
        self.status.showMessage("正在环境体检（动态探测域名/端口/路径）...")
        self._probe_thread = ProbeThread(self.ssh, use_sudo=True)
        self._probe_thread.finished_probe.connect(self._on_probe_done)
        self._probe_thread.start()

    def _on_probe_done(self, result: dict, err: str):
        self.probe = result or {}
        for w in (self.dashboard, self.service_mgr, self.port_viewer,
                  self.web_panel, self.file_browser, self.sys_config, self.terminal):
            try:
                w.set_probe_info(self.probe)
            except Exception:
                pass
        if err:
            self.status.showMessage("环境体检未完成：" + err)
        else:
            doms = self.probe.get("domains") or []
            cf = self.probe.get("nginx_conf") or ""
            self.status.showMessage(
                f"环境体检完成 · 域名:{','.join(doms[:2]) or '未发现'} · 主配置:{cf or '未知'}")
        # 重刷当前页面，让体检结果立即生效
        try:
            self._on_nav_changed(self.nav_list.currentRow())
        except Exception:
            pass

    def reprobe(self):
        """重新探测环境（安装 Node.js 等操作后调用，刷新所有页面）"""
        if self.ssh and self.ssh.is_connected:
            self._start_probe()

    def _disconnect(self):
        # 停止环境体检线程
        if self._probe_thread and self._probe_thread.isRunning():
            try:
                self._probe_thread.wait(800)
            except Exception:
                pass
            self._probe_thread = None
        if self.ssh:
            # 先停定时器和日志线程
            for w in (self.dashboard, self.service_mgr, self.port_viewer,
                      self.web_panel, self.file_browser, self.sys_config, self.terminal):
                try:
                    w.on_hide()
                except Exception:
                    pass
            try:
                self.terminal.disconnect_ssh()
            except Exception:
                pass
            # 断开 SSH，让阻塞在 read 上的 worker 抛异常退出
            try:
                self.ssh.disconnect()
            except Exception:
                pass
            # 等线程结束
            for w in (self.dashboard, self.service_mgr, self.port_viewer,
                      self.web_panel, self.file_browser, self.sys_config, self.terminal):
                try:
                    w.stop_workers(wait_ms=1500)
                except Exception:
                    pass
        self.ssh = None
        self.current_conn = None
        self.act_disconnect.setEnabled(False)
        self.act_connect.setEnabled(True)
        self.nav_list.setEnabled(False)
        self.stack.setCurrentWidget(self.welcome)
        self.status.showMessage("未连接")
        self._refresh_conn_list()

    def _hide_all_pages(self):
        """停掉所有页面的定时器和后台任务"""
        for w in (self.dashboard, self.service_mgr, self.port_viewer,
                  self.web_panel, self.file_browser, self.sys_config, self.terminal):
            try:
                w.on_hide()
            except Exception:
                pass
            try:
                w.stop_workers()
            except Exception:
                pass

    def _set_sudo(self):
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.information(self, "提示", "请先连接 NAS")
            return
        telemetry.track("设置Sudo密码")
        pwd, ok = QInputDialog.getText(self, "Sudo 密码", "输入 sudo 密码（仅本次会话缓存）：",
                                       QLineEdit.Password)
        if ok and pwd:
            self.ssh.set_sudo_password(pwd)
            self.status.showMessage("Sudo 密码已设置")

    # ---------- 自动更新 ----------

    def _check_update(self):
        """启动后台线程扫描下载目录，检查最新版本"""
        self._update_checker = UpdateChecker(self.settings.get("update_base_url") or UPDATE_BASE_URL)
        self._update_checker.found.connect(self._on_update_found)
        self._update_checker.failed.connect(lambda _: None)  # 检查失败静默忽略
        self._update_checker.start()

    def _on_update_found(self, version: str, url: str):
        """发现新版本：工具栏显示醒目按钮"""
        current = APP_VERSION.lstrip("vV")
        if parse_version(version) <= parse_version(current):
            return
        self._update_version = version
        self._update_url = url
        self.act_update.setText(f"⬇ 有新版本 v{version}")
        self.act_update.setVisible(True)
        self.status.showMessage(f"发现新版本 v{version}，点击工具栏按钮更新", 8000)

    def _start_update(self):
        """点击更新按钮：确认后开始下载"""
        if not self._update_url:
            return
        telemetry.track("下载更新")
        ok = QMessageBox.question(
            self, "发现新版本",
            f"当前版本：{APP_VERSION}\n最新版本：v{self._update_version}\n\n"
            f"是否立即下载并安装？\n下载完成后将自动关闭软件并静默安装、重启。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ok != QMessageBox.Yes:
            return
        self._dlg = QProgressDialog("正在连接下载服务器...", "取消", 0, 100, self)
        self._dlg.setWindowTitle("软件更新")
        self._dlg.setMinimumDuration(0)
        self._dlg.setFixedWidth(440)
        self._dlg.setModal(True)
        self._dlg.setValue(0)
        self._dlg.canceled.connect(self._cancel_update)
        self._dlg.show()
        self._downloader = UpdateDownloader(self._update_url)
        self._downloader.progress.connect(self._on_download_progress)
        self._downloader.finished_ok.connect(self._on_download_done)
        self._downloader.failed.connect(self._on_download_failed)
        self._downloader.start()

    def _cancel_update(self):
        if self._downloader and self._downloader.isRunning():
            self._downloader.terminate()
            self._downloader.wait(1000)

    def _on_download_progress(self, downloaded: int, total: int, speed: float):
        if total > 0:
            self._dlg.setValue(int(downloaded * 100 / total))
            self._dlg.setLabelText(
                f"正在下载... {downloaded/1048576:.1f}/{total/1048576:.1f} MB"
                f"（{speed:.1f} MB/s）")

    def _on_download_done(self, filepath: str):
        """下载完成：静默安装并自动重启"""
        self._dlg.close()
        QMessageBox.information(
            self, "下载完成",
            "更新已下载完成，软件将自动关闭并安装新版本，安装完成后自动启动。")
        try:
            subprocess.Popen(
                [filepath, "/SILENT", "/CLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            QMessageBox.critical(
                self, "安装失败",
                f"无法启动安装程序：{e}\n请手动运行：{filepath}")
            return
        QApplication.quit()

    def _on_download_failed(self, err: str):
        self._dlg.close()
        QMessageBox.warning(
            self, "下载失败",
            f"更新下载失败：{err}\n请检查网络后稍后重试。")

    def _scan_environment(self):
        """弹窗扫描 NAS 环境，结果存进连接配置"""
        if not self.ssh or not self.ssh.is_connected:
            return
        telemetry.track("扫描NAS环境")
        dlg = ScanDialog(self.ssh, self)
        dlg.exec()
        if dlg.result:
            conn = self.current_conn
            conn.web_dir = dlg.result.get("web_dir", "") or conn.web_dir
            conn.node_proc = dlg.result.get("node_proc", "") or conn.node_proc
            conn.health_url = dlg.result.get("health_url", "") or conn.health_url
            conn.web_url = dlg.result.get("web_url", "") or conn.web_url
            if dlg.result.get("sites"):
                conn.sites = dlg.result["sites"]
            self.config.update(conn)
            self.status.showMessage(f"环境扫描完成，已保存到「{conn.name}」")
            self.sys_config.set_conn_info(conn)
            self.web_panel.set_conn_info(conn)
            self.dashboard.set_conn_info(conn)
            self.port_viewer.set_conn_info(conn)
            # 重刷当前页面
            self._on_nav_changed(self.nav_list.currentRow())

    # ---------- 导航 ----------

    def _on_nav_changed(self, row: int):
        if not self.ssh or not self.ssh.is_connected:
            self.stack.setCurrentWidget(self.welcome)
            return
        # 停掉旧页面的定时器
        old = self.stack.currentWidget()
        if old is not None and hasattr(old, "on_hide"):
            try:
                old.on_hide()
            except Exception:
                pass
        # 使用统计：按页面名记一次
        try:
            _tp = ("仪表盘", "服务管理", "端口管理", "网页面板",
                   "文件管理", "系统配置", "终端")
            telemetry.track("打开页面-" + (_tp[row] if 0 <= row < len(_tp) else "其他"))
        except Exception:
            pass
        widget_map = {
            0: self.dashboard,
            1: self.service_mgr,
            2: self.port_viewer,
            3: self.web_panel,
            4: self.file_browser,
            5: self.sys_config,
            6: self.terminal,
        }
        w = widget_map.get(row)
        if w:
            self.stack.setCurrentWidget(w)
            if hasattr(w, "on_show"):
                w.on_show()

    def closeEvent(self, event):
        # 先停定时器和日志线程
        for w in (self.dashboard, self.service_mgr, self.port_viewer,
                  self.web_panel, self.file_browser, self.sys_config, self.terminal):
            try:
                w.on_hide()
            except Exception:
                pass
        # 断开 SSH，让阻塞在 read 上的 worker 线程抛异常退出
        if self.ssh:
            try:
                self.terminal.disconnect_ssh()
            except Exception:
                pass
            try:
                self.ssh.disconnect()
            except Exception:
                pass
        # 等线程退出，terminate 兜底
        for w in (self.dashboard, self.service_mgr, self.port_viewer,
                  self.web_panel, self.file_browser, self.sys_config, self.terminal):
            try:
                w.stop_workers(wait_ms=1500)
            except Exception:
                pass
        event.accept()


def setup_exception_hook():
    """全局异常捕获，写到日志文件，防止闪退无信息"""
    log_dir = Path(os.environ.get("APPDATA", Path.home())) / "NasManager"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "error.log"

    def handler(exc_type, exc_value, exc_tb):
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
                f.write("\n" + "=" * 60 + "\n")
        except Exception:
            pass
        # 已有的 QApplication 实例弹错误框
        app = QApplication.instance()
        if app:
            from widgets import msg
            msg.critical(None, "程序错误",
                         f"{exc_type.__name__}: {exc_value}\n\n日志: {log_file}")
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = handler


def _startup_log(msg: str):
    """写启动日志，闪退时精确定位到崩溃步骤"""
    try:
        from datetime import datetime
        log_dir = Path(os.environ.get("APPDATA", Path.home())) / "NasManager"
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "startup.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%H:%M:%S.%f')[:-3]} {msg}\n")
    except Exception:
        pass


def main():
    setup_exception_hook()
    # 使用统计（只报计数、已脱敏、全静默；绝不影响软件运行）
    telemetry.start(APP_VERSION)
    telemetry.heartbeat()
    _startup_log("STEP0 app 创建前")
    app = QApplication(sys.argv)
    # 设置窗口图标：标题栏 / 任务栏 / 开始菜单都显示 NAS 图标
    try:
        icon_p = resource_path("assets") / "app.ico"
        if Path(icon_p).exists():
            app.setWindowIcon(QIcon(str(icon_p)))
    except Exception:
        pass
    _startup_log("STEP1 QApplication 已创建")
    app.setStyle("Fusion")
    # 全局样式应用到 app 级：所有窗口/弹窗（含无 parent 的 QMessageBox）统一深绿主题
    app.setStyleSheet(STYLE)

    # 启动动画：第一时间显示 + 淡入
    _startup_log("STEP2 创建 SplashScreen")
    splash = SplashScreen()
    _startup_log(f"STEP3 SplashScreen 创建完成, 帧数={len(splash._frames)}")
    splash.show()
    splash.start()
    splash.fade_in()
    _startup_log("STEP4 splash 已显示并淡入")
    # 让动画先渲染出来
    app.processEvents()

    # 关键：MainWindow 在事件循环开始前构建（与无动画版时序一致，最稳）
    _startup_log("STEP5 事件循环前构建 MainWindow")
    win = MainWindow()
    _startup_log("STEP6 MainWindow 构建完成（暂不显示）")

    def show_main():
        _startup_log("STEP7 显示主窗口")
        # 主窗口屏幕居中
        try:
            scr = app.primaryScreen()
            if scr:
                g = scr.availableGeometry()
                win.move((g.width() - win.width()) // 2,
                         (g.height() - win.height()) // 2)
        except Exception:
            pass
        win.show()
        _startup_log("STEP8 主窗口已显示，开始淡出动画")
        splash.fade_out()

    QTimer.singleShot(DEFAULT_DURATION_MS, show_main)
    _startup_log("STEP9 进入事件循环 app.exec")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
