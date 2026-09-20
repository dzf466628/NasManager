"""Site card widget."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSizePolicy, QWidget, QInputDialog, QDialog, QMessageBox,
)

from widgets import msg
from widgets.web_components import ClickableLabel, ColorBar, SpinnerWidget
from widgets.web_helpers import health_lbl_style
from widgets.web_styles import OPEN_DIALOG_STYLE


class SiteCard(QFrame):
    """单个网站的服务卡片"""
    def __init__(self, site: dict, panel: "WebPanelWidget"):
        super().__init__()
        self.site = site
        self.panel = panel
        self.setObjectName("WebCard")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        # 网站主题色（一个网站一个颜色）
        from widgets.site_colors import site_color_hex
        self.accent = site_color_hex(panel.conn.sites if panel.conn else [], site.get("name"))

        # 顶部颜色条（点击选色，运行中流动）
        self.color_bar = ColorBar(self.accent)
        self.color_bar.setToolTip("点击更换网站颜色")
        self.color_bar.clicked.connect(self._pick_color)
        lay.insertWidget(0, self.color_bar)

        # 顶行：状态点 + 名称 + 目录 | 状态
        top = QHBoxLayout()
        top.setSpacing(6)
        self.dot = QLabel("●")
        self.dot.setObjectName("Dot")
        self.dot.setStyleSheet("color:#bbb;")
        self.title = QLabel(site.get("name") or "网站")
        self.title.setStyleSheet(f"font-size:14px; font-weight:bold; color:{self.accent};")
        self.dir_lbl = QLabel(site.get("web_dir") or "")
        self.dir_lbl.setStyleSheet("font-size:11px; color:#999;")
        self.status = QLabel("检测中...")
        self.status.setStyleSheet("font-size:11px; color:#888;")
        top.addWidget(self.dot)
        top.addWidget(self.title)
        top.addSpacing(4)
        top.addWidget(self.dir_lbl)
        top.addStretch()
        # 状态区（右侧）：运行状态文字 + 操作中转圈动画（防止用户以为卡住反复点击）
        self.state_box = QWidget()
        state_lay = QHBoxLayout(self.state_box)
        state_lay.setContentsMargins(0, 0, 0, 0)
        state_lay.setSpacing(5)
        state_lay.addWidget(self.status)
        self.busy_spinner = SpinnerWidget(size=14, color="#2e7163")
        self.busy_spinner.hide()
        state_lay.addWidget(self.busy_spinner)
        self.busy_lbl = QLabel("")
        self.busy_lbl.setStyleSheet("font-size:11px; color:#2e7163; font-weight:bold;")
        self.busy_lbl.hide()
        state_lay.addWidget(self.busy_lbl)
        top.addWidget(self.state_box)
        lay.addLayout(top)

        # 信息行1：启动命令（双击选择网站根目录下的 JS 启动文件）
        info1 = QHBoxLayout()
        info1.setSpacing(8)
        self.entry_lbl = ClickableLabel(f"启动: {site.get('entry') or site.get('node_proc') or '—'}")
        self.entry_lbl.setToolTip("双击选择该网站根目录下的 JS 启动文件")
        self.entry_lbl.doubleClicked.connect(self._pick_entry)
        info1.addWidget(self.entry_lbl)
        info1.addStretch()
        lay.addLayout(info1)

        # 信息行2：进程号 / 运行时长 / 端口 / 健康检查 / 访问
        info2 = QHBoxLayout()
        info2.setSpacing(14)
        self.pid_lbl = QLabel("进程号：—")
        self.etime_lbl = QLabel("运行时长：—")
        self.port_lbl = ClickableLabel(f"端口：{site.get('port') or '—'}")
        self.port_lbl.setToolTip("双击设置端口")
        self.port_lbl.doubleClicked.connect(self._set_port)
        self.health_lbl = ClickableLabel("主页：—")
        # 独立 objectName（非 EditableLbl）：绕开父级 PANEL_STYLE 的灰色 ID 规则，
        # 让状态颜色与悬浮色真正渲染；初始置为 idle 灰
        self.health_lbl.setObjectName("HealthLbl")
        self.health_lbl.setStyleSheet(health_lbl_style("idle"))
        self.health_lbl.setToolTip("双击选择主页文件")
        self.health_lbl.doubleClicked.connect(self._pick_index_file)
        for l in (self.pid_lbl, self.etime_lbl):
            l.setStyleSheet("font-size:12px; color:#555;")
            info2.addWidget(l)
        info2.addWidget(self.port_lbl)
        info2.addWidget(self.health_lbl)
        info2.addStretch()
        lay.addLayout(info2)

        # 按钮组：启停切换 | 重启 | 健康检查 | 实时日志 | 打开网页
        btns = QHBoxLayout()
        btns.setSpacing(6)
        startable = site.get("startable", False)
        self.running = False
        self._busy = False          # 操作中：整卡禁用防重复点击
        self.toggle_btn = QPushButton("启动")
        self.toggle_btn.setObjectName("PrimaryBtn")
        self.toggle_btn.setEnabled(startable)
        self.restart_btn = QPushButton("重启")
        self.restart_btn.setObjectName("WebBtn")
        self.restart_btn.setEnabled(startable)
        self.health_btn = QPushButton("健康检查")
        self.health_btn.setObjectName("WebBtn")
        self.health_btn.setEnabled(bool(site.get("health_url")))
        self.log_btn = QPushButton("实时日志")
        self.log_btn.setObjectName("WebBtn")
        self.log_btn.setCheckable(True)
        self.open_btn = QPushButton("打开网页")
        self.open_btn.setObjectName("WebBtn")
        self.open_btn.setEnabled(bool(site.get("port") or site.get("web_url")))

        self.toggle_btn.clicked.connect(lambda: self.panel._site_toggle(self))
        self.restart_btn.clicked.connect(lambda: self.panel._site_action(self, "restart"))
        self.health_btn.clicked.connect(lambda: self.panel._site_health(self))
        self.log_btn.clicked.connect(lambda: self.panel._toggle_site_log(self))
        self.open_btn.clicked.connect(lambda: self.panel._site_open(self))

        # 主操作按钮等宽铺满整行
        for b in (self.toggle_btn, self.restart_btn,
                  self.health_btn, self.log_btn, self.open_btn):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btns.addWidget(b)
        lay.addLayout(btns)

        # 工具行：依赖 / 反代 / 自启（等宽填满）
        meta = QHBoxLayout()
        meta.setSpacing(6)
        self.dep_btn = QPushButton("依赖：检测中...")
        self.dep_btn.setObjectName("MetaBtn")
        self.dep_btn.clicked.connect(lambda: self.panel._site_deps(self))
        self.proxy_btn = QPushButton("反代：检测中...")
        self.proxy_btn.setObjectName("MetaBtn")
        self.proxy_btn.clicked.connect(lambda: self.panel._site_proxy(self))
        self.auto_btn = QPushButton("自启：检测中...")
        self.auto_btn.setObjectName("MetaBtn")
        self.auto_btn.clicked.connect(lambda: self.panel._site_autostart(self))
        for b in (self.dep_btn, self.proxy_btn, self.auto_btn):
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            meta.addWidget(b)
        meta.addStretch()
        self.del_btn = QPushButton("删除")
        self.del_btn.setToolTip("删除该网站（可连同文件夹一起清理）")
        self.del_btn.setStyleSheet(
            "background:#fef0f0; color:#c62828; border:none; border-radius:5px; "
            "padding:4px 10px; font-size:11px;"
        )
        self.del_btn.clicked.connect(lambda: self.panel._site_delete(self))
        meta.addWidget(self.del_btn)
        lay.addLayout(meta)

    # 工具行状态设置（带颜色：ok绿 / warn橙 / none灰 / pending浅灰）
    @staticmethod
    def _meta_style(state: str) -> str:
        colors = {
            "ok": ("#e8f5e9", "#2e7d32"),
            "warn": ("#fff3e0", "#e65100"),
            "none": ("#f5f5f5", "#888"),
            "pending": ("#f0f2f5", "#999"),
        }
        bg, fg = colors.get(state, colors["none"])
        return (f"background:{bg}; color:{fg}; border:none; border-radius:5px; "
                f"padding:4px 10px; font-size:11px;")

    def set_dep(self, state: str, text: str, tip: str = ""):
        try:
            self.dep_btn.setText(text)
            self.dep_btn.setToolTip(tip)
            self.dep_btn.setStyleSheet(self._meta_style(state))
            self.dep_btn.setEnabled(state != "pending")
        except RuntimeError:
            pass  # 卡片已被销毁（重新扫描），忽略异步回调

    def set_proxy(self, state: str, text: str, tip: str = ""):
        try:
            self.proxy_btn.setText(text)
            self.proxy_btn.setToolTip(tip)
            self.proxy_btn.setStyleSheet(self._meta_style(state))
            self.proxy_btn.setEnabled(state != "pending")
        except RuntimeError:
            pass

    def set_auto(self, state: str, text: str, tip: str = ""):
        try:
            self.auto_btn.setText(text)
            self.auto_btn.setToolTip(tip)
            self.auto_btn.setStyleSheet(self._meta_style(state))
            self.auto_btn.setEnabled(state != "pending")
        except RuntimeError:
            pass

    def _pick_entry(self):
        """双击「启动」：从 NAS 该网站目录下列出 JS 文件选择启动入口（不是本地文件！）"""
        base = (self.site.get("web_dir") or "").strip()
        if not base:
            QMessageBox.information(self, "提示", "该网站没有根目录信息，无法选择启动文件")
            return
        panel = getattr(self, "panel", None)
        if not panel or not panel.ssh or not panel.ssh.is_connected:
            QMessageBox.warning(self, "提示", "SSH 未连接")
            return
        shq = "'" + base.replace("'", "'\\''") + "'"
        cmd = (f"cd {shq} 2>/dev/null && find . -maxdepth 1 -name '*.js' "
               f"-type f 2>/dev/null | sed 's|^\\./||' | sort; true")
        panel.status_msg("正在读取该网站目录下的 JS 文件...")
        panel.run_cmd(cmd, lambda r, b=base: self._on_pick_entry_list(b, r), timeout=12)

    def _on_pick_entry_list(self, base, r):
        """列出 NAS 该网站目录下的 js 文件，弹窗选择并保存"""
        files = sorted({ln.strip() for ln in (r.stdout or "").splitlines()
                        if ln.strip().endswith(".js")})
        if not files:
            msg.warn(self, "提示", f"该网站目录下未找到 .js 文件：\n{base}")
            return
        dlg = QInputDialog(self)
        dlg.setWindowTitle("选择启动 JS 文件")
        dlg.setLabelText(f"该网站目录下找到 {len(files)} 个 JS 文件，请选择启动入口：\n{base}")
        dlg.setComboBoxItems(files)
        dlg.setComboBoxEditable(False)
        dlg.setOkButtonText("确定")
        dlg.setCancelButtonText("取消")
        if dlg.exec() != QInputDialog.Accepted:
            return
        fname = dlg.textValue().strip()
        if not fname:
            return
        self.site["entry"] = fname
        self.site["node_proc"] = f"node {fname}"
        self.site["startable"] = True
        self.entry_lbl.setText(f"启动: {fname}")
        self.entry_lbl.setProperty("edited", True)
        self.entry_lbl.style().unpolish(self.entry_lbl)
        self.entry_lbl.style().polish(self.entry_lbl)
        self.toggle_btn.setEnabled(True)
        self.restart_btn.setEnabled(True)
        # 保存到连接配置，重启软件不丢失
        panel = getattr(self, "panel", None)
        if panel and panel.conn:
            try:
                from config import ConfigManager
                ConfigManager().update(panel.conn)
            except Exception:
                pass
        (panel or self).status_msg(f"已设置启动文件：{fname}")

    def _set_port(self):
        """双击「端口」：手动设置端口，立即更新健康检查/访问地址"""
        cur = (self.site.get("port") or "").strip()
        cur_int = int(cur) if cur.isdigit() else 3001
        dlg = QInputDialog(self)
        dlg.setWindowTitle("设置端口")
        dlg.setLabelText("输入端口号（1-65535）：")
        dlg.setIntValue(cur_int)
        dlg.setIntRange(1, 65535)
        dlg.setOkButtonText("确定")
        dlg.setCancelButtonText("取消")
        if dlg.exec() != QInputDialog.Accepted:
            return
        port_s = str(dlg.intValue())
        host = self.panel.conn.host if self.panel.conn else ""
        self.site["port"] = port_s
        self.site["health_url"] = f"http://localhost:{port_s}/"
        self.site["web_url"] = f"http://{host}:{port_s}/" if host else ""
        self.port_lbl.setText(f"端口：{port_s}")
        self.port_lbl.setProperty("edited", True)
        self.port_lbl.style().unpolish(self.port_lbl)
        self.port_lbl.style().polish(self.port_lbl)
        self.health_btn.setEnabled(bool(self.site.get("health_url")))
        self.open_btn.setEnabled(bool(self.site.get("port") or self.site.get("web_url")))

    def set_status(self, active: bool):
        self.running = active
        self.dot.setStyleSheet("color:#4caf50;" if active else "color:#bbb;")
        self.status.setText("运行中" if active else "已停止")
        self.status.setStyleSheet(
            "font-size:11px; color:#2e7d32;" if active else "font-size:11px; color:#999;"
        )
        # 启停切换按钮：运行中显示"停止"，已停止显示"启动"
        if active:
            self.toggle_btn.setText("停止")
            self.toggle_btn.setObjectName("DangerBtn")
        else:
            self.toggle_btn.setText("启动")
            self.toggle_btn.setObjectName("PrimaryBtn")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)
        # 运行中颜色条流动，停止时静止
        self.color_bar.set_flowing(active)

    def set_busy(self, busy: bool, action: str = ""):
        """操作中：启动/停止/重启 期间整卡禁用 + 右上角转圈动画。

        整卡 setEnabled(False)：鼠标在卡片上任一控件都点不了，彻底防多次点击；
        Qt 会保留各子控件自己的 enabled 状态，恢复后自动还原
        （不会误启用原本就禁用的按钮）。QSS 样式不受禁用影响，卡片不会变灰。
        操作完成(成功或失败)后恢复。
        """
        try:
            if busy:
                if self._busy:
                    return
                self._busy = True
                self.setEnabled(False)
                text = {"start": "启动中...", "stop": "停止中...",
                        "restart": "重启中..."}.get(action, "操作中...")
                self.busy_lbl.setText(text)
                self.busy_spinner.start()
                self.busy_spinner.show()
                self.busy_lbl.show()
            else:
                if not self._busy:
                    return
                self._busy = False
                self.setEnabled(True)
                self.busy_spinner.stop()
                self.busy_spinner.hide()
                self.busy_lbl.hide()
        except RuntimeError:
            pass  # 卡片已销毁（重新扫描），忽略异步回调

    def _pick_color(self):
        """点击颜色条：弹出马卡龙色选择器（一横排按色相排序，去掉被占用颜色，当前色描边）"""
        from widgets.site_colors import PALETTE_HEX, used_colors
        sites = self.panel.conn.sites if self.panel.conn else []
        used = used_colors(sites, self.site.get("name"))
        # 只显示未被其他网站占用的颜色（按色相排序）
        available = [c for c in PALETTE_HEX if c not in used]
        dlg = QDialog(self)
        dlg.setWindowTitle("选择网站颜色")
        dlg.setStyleSheet(OPEN_DIALOG_STYLE)
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(14)
        vbox.setContentsMargins(20, 16, 20, 14)
        title = QLabel("选择网站颜色（每个网站颜色唯一）")
        title.setObjectName("DlgTitle")
        vbox.addWidget(title)
        # 一横排色块
        row = QHBoxLayout()
        row.setSpacing(6)
        chosen = {"c": self.accent}
        color_btns = {}
        for c in available:
            b = QPushButton()
            b.setFixedSize(34, 34)
            is_cur = c.upper() == self.accent.upper()
            border = "3px solid #ffffff" if is_cur else "2px solid rgba(0,0,0,0.08)"
            b.setStyleSheet(
                f"background:{c}; border:{border}; border-radius:8px;")
            b.setCursor(Qt.PointingHandCursor)
            tip = "当前颜色" if is_cur else "点击选择"
            b.setToolTip(tip)

            def select(cc, btns=color_btns):
                chosen["c"] = cc
                # 更新所有色块描边
                for hc, hb in btns.items():
                    bd = "3px solid #ffffff" if hc.upper() == cc.upper() \
                        else "2px solid rgba(0,0,0,0.08)"
                    hb.setStyleSheet(
                        f"background:{hc}; border:{bd}; border-radius:8px;")
            b.clicked.connect(lambda _, cc=c: select(cc))
            color_btns[c] = b
            row.addWidget(b)
        row.addStretch()
        vbox.addLayout(row)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok = QPushButton("确定")
        ok.setObjectName("DlgPrimary")
        cancel = QPushButton("取消")
        cancel.setObjectName("DlgGhost")
        ok.clicked.connect(dlg.accept)
        cancel.clicked.connect(dlg.reject)
        btn_row.addWidget(ok)
        btn_row.addWidget(cancel)
        vbox.addLayout(btn_row)
        if dlg.exec() != QDialog.Accepted:
            return
        new_color = chosen["c"]
        if new_color.upper() == self.accent.upper():
            return
        self.accent = new_color
        self.site["custom_color"] = new_color
        self.color_bar.set_color(new_color)
        self.title.setStyleSheet(f"font-size:14px; font-weight:bold; color:{new_color};")
        self.panel._persist_site_color(self)

    def _pick_index_file(self):
        """双击主页标签：从 NAS 该网站目录下列出网页文件选择"""
        base = (self.site.get("web_dir") or "").strip()
        if not base:
            QMessageBox.information(self, "提示", "该网站没有根目录信息")
            return
        panel = getattr(self, "panel", None)
        if not panel or not panel.ssh or not panel.ssh.is_connected:
            QMessageBox.warning(self, "提示", "SSH 未连接")
            return
        shq = "'" + base.replace("'", "'\\''") + "'"
        cmd = (f"cd {shq} 2>/dev/null && find . -maxdepth 1 -type f "
               r"\( -iname '*.html' -o -iname '*.htm' -o -iname '*.php' "
               r"-o -iname '*.asp' -o -iname '*.aspx' -o -iname '*.jsp' \) "
               "2>/dev/null | sed 's|^\\./||' | sort; true")
        panel.status_msg("正在读取该网站目录下的网页文件...")
        panel.run_cmd(cmd, lambda r, b=base: self._on_pick_index_list(b, r), timeout=12)

    def _on_pick_index_list(self, base, r):
        files = sorted({ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()})
        if not files:
            msg.warn(self, "提示", f"该网站目录下未找到网页文件：\n{base}")
            return
        dlg = QInputDialog(self)
        dlg.setWindowTitle("选择主页文件")
        dlg.setLabelText(f"该网站目录下找到 {len(files)} 个网页文件，请选择主页：\n{base}")
        dlg.setComboBoxItems(files)
        dlg.setComboBoxEditable(False)
        cur = (self.site.get("index_file") or "").strip()
        if cur in files:
            dlg.setTextValue(cur)
        dlg.setOkButtonText("确定")
        dlg.setCancelButtonText("取消")
        if dlg.exec() != QInputDialog.Accepted:
            return
        fname = dlg.textValue().strip()
        if not fname:
            return
        self.site["index_file"] = fname
        self.panel._persist_site_index(self)
        self.panel._site_health(self)

    def reset_info(self):
        self.pid_lbl.setText("进程号：—")
        self.etime_lbl.setText("运行时长：—")
