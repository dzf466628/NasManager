"""
系统仪表盘
- CPU / 内存 / 网络 / 运行时间 / 系统信息
- 网站信息列表（来自连接配置的多站点扫描）
- 5 秒自动刷新，命令在后台线程执行
"""
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QColor
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QProgressBar,
    QFrame, QScrollArea, QWidget, QSizePolicy,
    QTableWidget, QTableWidgetItem, QHeaderView,
)

from widgets.base import BaseWidget
from widgets.site_colors import site_color

REFRESH_CMD = (
    "echo '===CPU==='; "
    "awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat; "
    "sleep 0.5; "
    "awk '/^cpu /{print $2+$3+$4+$5+$6+$7+$8, $5+$6}' /proc/stat; "
    "echo '===MEM==='; free -m; "
    "echo '===NET==='; cat /proc/net/dev; "
    "echo '===UP==='; uptime; "
    "echo '===SYS==='; uname -a; "
    "echo '===END==='"
)

CARD_STYLE = """
QFrame#Card {
    background: #ffffff; border: 1px solid #e0e0e0; border-radius: 10px;
}
QLabel#CardTitle { color: #666; font-size: 12px; font-weight: bold; }
QLabel#CardValue { color: #1a1a2e; font-size: 22px; font-weight: bold; }
QProgressBar {
    border: none; border-radius: 4px; background: #eef0f4;
    height: 10px; text-align: center; font-size: 10px; color: #555;
}
QProgressBar::chunk { border-radius: 4px; }
"""


class Card(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setStyleSheet(CARD_STYLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)
        self.title = t
        self.content_layout = lay

    def add_widget(self, w):
        self.content_layout.addWidget(w)


class DashboardWidget(BaseWidget):
    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.conn = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        # 刷新间隔读 settings（默认 5 秒），不再硬编码
        try:
            self._interval = int((settings or {}).get("dashboard_interval") or 5) * 1000
        except Exception:
            self._interval = 5000
        self._last_net = {}  # iface -> (rx_bytes, tx_bytes, ts)
        self._build_ui()

    def set_conn_info(self, conn):
        self.conn = conn

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        self.title = QLabel("系统仪表盘")
        self.title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        header.addWidget(self.title)
        header.addStretch()
        self.host_label = QLabel("")
        self.host_label.setStyleSheet("color:#666;")
        header.addWidget(self.host_label)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.grid = QGridLayout(inner)
        self.grid.setSpacing(14)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # CPU 卡片
        self.cpu_card = Card("CPU 使用率")
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setRange(0, 100)
        self.cpu_bar.setFormat("%v%")
        self.cpu_card.add_widget(self.cpu_bar)
        self.cpu_detail = QLabel("—")
        self.cpu_detail.setStyleSheet("color:#888; font-size:11px;")
        self.cpu_card.add_widget(self.cpu_detail)
        self.grid.addWidget(self.cpu_card, 0, 0)

        # 内存卡片
        self.mem_card = Card("内存")
        self.mem_bar = QProgressBar()
        self.mem_bar.setRange(0, 100)
        self.mem_bar.setFormat("%v%")
        self.mem_card.add_widget(self.mem_bar)
        self.mem_detail = QLabel("—")
        self.mem_detail.setStyleSheet("color:#888; font-size:11px;")
        self.mem_card.add_widget(self.mem_detail)
        self.grid.addWidget(self.mem_card, 0, 1)

        # 运行时间卡片
        self.up_card = Card("运行时间")
        self.up_value = QLabel("—")
        self.up_value.setObjectName("CardValue")
        self.up_card.add_widget(self.up_value)
        self.load_label = QLabel("负载：—")
        self.load_label.setStyleSheet("color:#888; font-size:11px;")
        self.up_card.add_widget(self.load_label)
        self.grid.addWidget(self.up_card, 0, 2)

        # 系统信息卡片
        self.sys_card = Card("系统信息")
        self.sys_value = QLabel("—")
        self.sys_value.setWordWrap(True)
        self.sys_value.setStyleSheet("font-size:12px; color:#333;")
        self.sys_card.add_widget(self.sys_value)
        self.grid.addWidget(self.sys_card, 1, 0, 1, 2)

        # 网络卡片
        self.net_card = Card("网络流量")
        self.net_layout = QVBoxLayout()
        self.net_layout.setSpacing(4)
        self.net_labels = {}
        net_placeholder = QLabel("等待数据...")
        net_placeholder.setStyleSheet("color:#888; font-size:12px;")
        self.net_layout.addWidget(net_placeholder)
        self.net_card.content_layout.addLayout(self.net_layout)
        self.grid.addWidget(self.net_card, 1, 2)

        # 网站信息卡片（占整行）
        self.sites_card = Card("网站信息")
        self.sites_table = QTableWidget(0, 5)
        self.sites_table.setHorizontalHeaderLabels(["网站", "目录", "启动命令", "端口", "状态"])
        self.sites_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sites_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.sites_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.sites_table.verticalHeader().setVisible(False)
        self.sites_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.sites_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.sites_table.setAlternatingRowColors(True)
        self.sites_table.setMinimumHeight(120)
        self.sites_card.add_widget(self.sites_table)
        self.grid.addWidget(self.sites_card, 2, 0, 1, 3)

        self.grid.setRowStretch(3, 1)

    def on_bind(self):
        self._last_net.clear()

    def on_show(self):
        self.refresh()
        self.timer.start(self._interval)

    def on_hide(self):
        self.timer.stop()

    def stop(self):
        self.timer.stop()

    def refresh(self):
        if not self.ssh or not self.ssh.is_connected:
            return
        self.run_cmd(REFRESH_CMD, self._on_data, timeout=15)
        self._refresh_sites()

    def _refresh_sites(self):
        sites = self.conn.sites if self.conn else []
        if not sites:
            self._render_sites(set())
            return
        cmds = []
        for s in sites:
            np_ = (s.get("node_proc") or "").strip()
            wd = (s.get("web_dir") or "").strip()
            if not np_ or not wd:
                continue
            pid_file = f"{wd}/.server.pid"
            # PID 文件检测：只判断本网站进程
            cmds.append(
                f"echo '@@{s.get('name')}@@'; "
                f"if [ -f {pid_file} ]; then p=$(cat {pid_file}); "
                f"if kill -0 $p 2>/dev/null; then echo RUN; else echo STOP; fi; "
                f"else echo STOP; fi"
            )
        if cmds:
            self.run_cmd("; ".join(cmds), self._on_sites, timeout=12)
        else:
            self._render_sites(set())

    def _on_sites(self, r):
        running = set()
        cur = None
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("@@") and line.endswith("@@"):
                cur = line.strip("@")
            elif cur and "RUN" in line:
                running.add(cur)
        self._render_sites(running)

    def _render_sites(self, running: set):
        sites = self.conn.sites if self.conn else []
        if not sites:
            self.sites_table.setRowCount(1)
            self.sites_table.clearSpans()
            self.sites_table.setSpan(0, 0, 1, 5)
            for j in range(5):
                self.sites_table.setItem(0, j, QTableWidgetItem(""))
            self.sites_table.setItem(0, 0, QTableWidgetItem("暂无网站（到网页面板重新扫描）"))
            return
        self.sites_table.clearSpans()
        self.sites_table.setRowCount(len(sites))
        for i, s in enumerate(sites):
            name = s.get("name") or ""
            web_dir = s.get("web_dir") or ""
            proc = s.get("node_proc") or s.get("entry") or ""
            port = s.get("port") or ""
            is_run = bool(s.get("node_proc")) and name in running
            accent = site_color(sites, name)
            items = [name, web_dir, proc, f":{port}" if port else "—", "运行中" if is_run else "已停止"]
            for j, t in enumerate(items):
                it = QTableWidgetItem(t)
                if j == 0:
                    it.setForeground(accent)  # 网站名用网站颜色
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if j == 4:
                    it.setForeground(accent if is_run else QColor("#999"))
                self.sites_table.setItem(i, j, it)

    def _on_data(self, r):
        if not r.ok:
            return
        self._parse(r.stdout)

    def _parse(self, text: str):
        sections = {}
        current = None
        buf = []
        for line in text.splitlines():
            if line.startswith("===") and line.endswith("==="):
                if current:
                    sections[current] = "\n".join(buf)
                current = line.strip("=").strip()
                buf = []
            else:
                buf.append(line)
        if current:
            sections[current] = "\n".join(buf)

        self._parse_cpu(sections.get("CPU", ""))
        self._parse_mem(sections.get("MEM", ""))
        self._parse_uptime(sections.get("UP", ""))
        self._parse_sys(sections.get("SYS", ""))
        self._parse_net(sections.get("NET", ""))

    def _set_bar(self, bar: QProgressBar, pct: float):
        pct = max(0, min(100, pct))
        bar.setValue(int(pct))
        if pct < 60:
            color = "#4caf50"
        elif pct < 85:
            color = "#ff9800"
        else:
            color = "#f44336"
        bar.setStyleSheet(f"QProgressBar::chunk {{ background:{color}; border-radius:4px; }}")

    def _parse_cpu(self, text: str):
        # /proc/stat 两次采样，每行: total idle
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        if len(lines) >= 2:
            try:
                t1, i1 = map(int, lines[0].split()[:2])
                t2, i2 = map(int, lines[1].split()[:2])
                total_d = t2 - t1
                idle_d = i2 - i1
                if total_d > 0:
                    used = (1 - idle_d / total_d) * 100
                    self._set_bar(self.cpu_bar, used)
                    self.cpu_detail.setText(f"采样间隔 1s，差值 {total_d} ticks")
                    return
            except (ValueError, IndexError):
                pass
        self.cpu_bar.setValue(0)
        self.cpu_detail.setText("无法读取 CPU 数据")

    def _parse_mem(self, text: str):
        # Mem: total used free ...
        lines = text.strip().splitlines()
        for line in lines:
            if line.lower().startswith("mem:"):
                parts = line.split()
                if len(parts) >= 4:
                    total = int(parts[1])
                    used = int(parts[2])
                    if total > 0:
                        pct = used / total * 100
                        self._set_bar(self.mem_bar, pct)
                        self.mem_detail.setText(f"{used} MB / {total} MB")
                break

    def _parse_uptime(self, text: str):
        # up 10 days, 3:20,  2 users,  load average: 0.5, 0.6, 0.7
        import re
        m = re.search(r'up\s+(.*?),\s*\d+\s+user', text)
        if m:
            self.up_value.setText(m.group(1).strip())
        lm = re.search(r'load average[s]?:\s*(.+)', text)
        if lm:
            self.load_label.setText(f"负载：{lm.group(1).strip()}")

    def _parse_sys(self, text: str):
        self.sys_value.setText(text.strip()[:200])
        # 主机名
        parts = text.strip().split()
        if len(parts) >= 2:
            self.host_label.setText(f"主机：{parts[1]}")

    def _parse_net(self, text: str):
        import re
        now = time.time()
        new_data = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            iface, rest = line.split(":", 1)
            iface = iface.strip()
            if iface == "lo":
                continue
            fields = rest.split()
            if len(fields) < 16:
                continue
            rx = int(fields[0])
            tx = int(fields[8])
            new_data[iface] = (rx, tx, now)

        # 清掉旧标签
        while self.net_layout.count():
            item = self.net_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for iface, (rx, tx, ts) in new_data.items():
            if iface in self._last_net:
                rx0, tx0, ts0 = self._last_net[iface]
                dt = max(ts - ts0, 0.1)
                rx_rate = (rx - rx0) / dt
                tx_rate = (tx - tx0) / dt
                lbl = QLabel(f"{iface}:  ↓ {self._fmt_size(rx_rate)}/s   ↑ {self._fmt_size(tx_rate)}/s")
            else:
                lbl = QLabel(f"{iface}:  ↓ —   ↑ —")
            lbl.setStyleSheet("font-size:12px; color:#333;")
            self.net_layout.addWidget(lbl)

        self._last_net = new_data

    @staticmethod
    def _fmt_size(n: float) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"
