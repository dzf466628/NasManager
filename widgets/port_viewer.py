"""
端口管理
- netstat -tlnp 解析成表格：协议、监听地址、端口、PID、进程
- 常用端口高亮，双击行查看进程详情
"""
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
)

import telemetry
from widgets.base import BaseWidget
from widgets.site_colors import site_color_hex
from widgets import msg
from app_paths import resource_path

# 常用端口：从 services/presets.json 的 common_ports 读取（可编辑即生效），读取失败用默认兜底
DEFAULT_COMMON_PORTS = {80, 443, 450, 3000, 5000, 22, 8080, 8443, 21, 23, 3306, 5432, 6379, 27017}


def _load_common_ports() -> set:
    try:
        import json
        data = json.loads((resource_path("services") / "presets.json").read_text(encoding="utf-8"))
        ports = {int(p) for p in (data.get("common_ports") or []) if str(p).isdigit()}
        return ports if ports else DEFAULT_COMMON_PORTS
    except Exception:
        return DEFAULT_COMMON_PORTS


COMMON_PORTS = _load_common_ports()


class PortViewerWidget(BaseWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.conn = None
        self._ports_data = []
        self._build_ui()

    def set_conn_info(self, conn):
        self.conn = conn

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        title = QLabel("端口管理")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        header.addWidget(title)
        header.addStretch()

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("过滤端口 / 进程 / 地址...")
        self.filter_edit.setFixedWidth(220)
        self.filter_edit.textChanged.connect(self._apply_filter)
        header.addWidget(self.filter_edit)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setStyleSheet("padding:6px 16px;")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        outer.addLayout(header)

        hint = QLabel("提示：网站端口用对应网站颜色高亮，常用端口（80/443/450/5000 等）蓝色高亮，双击行查看进程详情")
        hint.setStyleSheet("color:#888; font-size:11px;")
        outer.addWidget(hint)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["协议", "监听地址", "端口", "进程号", "进程"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._on_double_click)
        outer.addWidget(self.table, 1)

    def on_show(self):
        self.refresh()

    def refresh(self):
        if not self.ssh or not self.ssh.is_connected:
            return
        # netstat 可能需要 root 才能看到 PID，先普通执行，没 PID 再试 sudo
        telemetry.track("端口扫描")
        self.run_cmd("netstat -tlnp 2>/dev/null", self._on_netstat, timeout=15)

    def _on_netstat(self, r):
        if not r.stdout.strip():
            # 尝试 sudo
            if self.need_sudo():
                self.run_cmd("netstat -tlnp", self._parse_netstat, timeout=15, sudo=True)
                return
            QMessageBox.information(self, "提示", "netstat 无输出，可能需要 sudo 权限或未安装 netstat")
            return
        self._parse_netstat(r)

    def _parse_netstat(self, r):
        if not r.ok and not r.stdout.strip():
            msg.warn(self, "错误", "执行 netstat 失败:\n" + r.stderr[:200])
            return
        ports = []
        for line in r.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("Active") or line.startswith("Proto"):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            proto = parts[0]
            local = parts[3]
            pid_prog = parts[6] if len(parts) >= 7 else "-"

            # 解析地址:端口
            m = re.match(r'^(.*?)(?:[:])(\d+)$', local)
            if not m:
                # 可能是 :::80 格式
                m2 = re.search(r'(\d+)$', local)
                if not m2:
                    continue
                addr = local[:m2.start()-1] if m2.start() > 0 else local
                port = int(m2.group(1))
            else:
                addr = m.group(1)
                port = int(m.group(2))

            pid, prog = "-", "-"
            if pid_prog and pid_prog != "-":
                pm = re.match(r'(\d+)/(.*)', pid_prog)
                if pm:
                    pid = pm.group(1)
                    prog = pm.group(2)
                else:
                    prog = pid_prog

            ports.append({
                "proto": proto,
                "addr": addr,
                "port": port,
                "pid": pid,
                "prog": prog,
            })

        ports.sort(key=lambda x: x["port"])
        self._ports_data = ports
        self._populate(ports)

    def _populate(self, ports):
        self.table.setRowCount(len(ports))
        # 网站端口归属：{port: color_hex}（每个网站一个颜色）
        site_ports = {}
        sites = self.conn.sites if self.conn else []
        for s in sites:
            p = (s.get("port") or "").strip()
            if p.isdigit():
                site_ports[int(p)] = site_color_hex(sites, s.get("name"))
        for row, p in enumerate(ports):
            items = [
                QTableWidgetItem(p["proto"]),
                QTableWidgetItem(p["addr"]),
                QTableWidgetItem(str(p["port"])),
                QTableWidgetItem(p["pid"]),
                QTableWidgetItem(p["prog"]),
            ]
            port_color = site_ports.get(p["port"])
            for col, item in enumerate(items):
                if port_color:
                    # 属于某网站的端口：用网站颜色（淡背景 + 深色文字）
                    bg = QColor(port_color)
                    bg.setAlpha(26)
                    item.setBackground(QBrush(bg))
                    item.setForeground(QBrush(QColor(port_color)))
                else:
                    is_common = p["port"] in COMMON_PORTS
                    highlight = QBrush(QColor("#e3f2fd"))
                    if is_common:
                        item.setBackground(highlight)
                        item.setForeground(QBrush(QColor("#1565c0")))
                self.table.setItem(row, col, item)

    def _apply_filter(self):
        kw = self.filter_edit.text().strip().lower()
        if not kw:
            self._populate(self._ports_data)
            return
        filtered = [
            p for p in self._ports_data
            if kw in str(p["port"]) or kw in p["prog"].lower() or kw in p["addr"].lower()
        ]
        self._populate(filtered)

    def _on_double_click(self, index):
        row = index.row()
        pid = self.table.item(row, 3).text()
        if pid == "-":
            QMessageBox.information(self, "进程详情", "无法获取 PID（可能需要 sudo）")
            return
        self.run_cmd(f"ps -p {pid} -f", self._on_ps_detail, timeout=8)

    def _on_ps_detail(self, r):
        if r.ok:
            msg.info(self, "进程详情", r.stdout.strip()[:500])
        else:
            QMessageBox.warning(self, "错误", "获取进程信息失败")
