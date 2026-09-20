"""Web 面板弹窗。

弹窗通过运行时注入 panel/card 与主面板交互，避免反向导入 web_panel。
"""
import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QCheckBox,
    QMessageBox,
    QScrollArea,
    QWidget,
    QFrame,
    QSizePolicy,
)

from widgets import msg
from widgets.web_workers import InstallDepsThread
from widgets.web_components import SpinnerWidget

# 检测项状态：pending(等待检测) / running(检测中) / ok(正常) / fixed(已修复) / fail(异常)
STATE_META = {
    "pending": ("···", "#bbbbbb", "等待检测"),
    "running": ("", "#2e7163", "检测中"),
    "ok": ("✓", "#2e7d32", "正常"),
    "fixed": ("✓", "#2e7d32", "已修复"),
    "fail": ("! ", "#c62828", "异常"),
}


class _HealthRow(QWidget):
    """检测清单中的一行：状态图标 | 名称 | 说明 | 右侧状态"""

    def __init__(self, name: str, desc: str, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 7, 4, 7)
        lay.setSpacing(10)

        self.icon_lbl = QLabel("···")
        self.icon_lbl.setFixedWidth(22)
        lay.addWidget(self.icon_lbl)

        self.name_lbl = QLabel(name)
        self.name_lbl.setStyleSheet("font-size:13px; font-weight:bold; color:#333;")
        self.name_lbl.setFixedWidth(112)
        lay.addWidget(self.name_lbl)

        self.desc_lbl = QLabel(desc)
        self.desc_lbl.setStyleSheet("font-size:12px; color:#9aa0a6;")
        self.desc_lbl.setWordWrap(True)
        lay.addWidget(self.desc_lbl, 1)

        self.state_lbl = QLabel("等待检测")
        self.state_lbl.setStyleSheet("font-size:12px; color:#bbbbbb;")
        lay.addWidget(self.state_lbl)

        self._spinner = SpinnerWidget(size=14, color="#2e7163")
        self._spinner.hide()
        lay.addWidget(self._spinner)

        self.set_state("pending")

    def set_state(self, state: str):
        meta = STATE_META.get(state, STATE_META["pending"])
        icon, color, text = meta
        self.state_lbl.setText(text)
        self.state_lbl.setStyleSheet(f"font-size:12px; color:{color};")
        if state == "running":
            self.icon_lbl.setText("")
            self._spinner.show()
            self._spinner.start()
        else:
            self._spinner.stop()
            self._spinner.hide()
            self.icon_lbl.setText(icon)
            self.icon_lbl.setStyleSheet(
                f"font-size:15px; font-weight:bold; color:{color};")


class WebHealthDialog(QDialog):
    """网页面板检测与自愈：清单式进度窗口。"""

    def __init__(self, parent=None, items=None):
        super().__init__(parent)
        self.setWindowTitle("NasManager - 网页面板检测")
        self.setMinimumSize(620, 360)
        self.resize(680, 420)
        self.setModal(False)
        self.setStyleSheet(
            "QDialog{background:#f0f2f5;}"
            "QPushButton#DlgGhost{background:#ffffff;color:#2e7163;"
            "border:1px solid #cbded8;border-radius:6px;padding:6px 18px;}"
            "QPushButton#DlgGhost:hover{background:#eef8f5;}"
            "QPushButton#DlgGhost:pressed{background:#dff0eb;}"
        )

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(0)

        # 品牌化标题区：沿用主界面的墨绿色，但保持对话框轻量
        head = QHBoxLayout()
        head.setSpacing(12)
        badge = QLabel("N")
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedSize(34, 34)
        badge.setStyleSheet(
            "background:#2e7163;color:#ffffff;border-radius:17px;"
            "font-size:17px;font-weight:bold;")
        head.addWidget(badge)
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        self.title_lbl = QLabel("正在检测，请稍候...")
        self.title_lbl.setStyleSheet("font-size:16px;font-weight:bold;color:#263238;")
        title_col.addWidget(self.title_lbl)
        self.subtitle_lbl = QLabel("网页服务环境检查与必要的自愈处理")
        self.subtitle_lbl.setStyleSheet("font-size:12px;color:#7b858a;")
        title_col.addWidget(self.subtitle_lbl)
        head.addLayout(title_col, 1)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("DlgGhost")
        self.cancel_btn.clicked.connect(self.close)
        head.addWidget(self.cancel_btn, 0, Qt.AlignVCenter)
        lay.addLayout(head)

        lay.addSpacing(16)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setStyleSheet(
            "QProgressBar{background:#dfe8e5;border:none;border-radius:2px;}"
            "QProgressBar::chunk{background:#43a68d;border-radius:2px;}")
        lay.addWidget(self.progress)
        lay.addSpacing(16)

        # 清单直接落在应用底色上，避免“卡片套卡片”
        list_head = QHBoxLayout()
        list_head.setContentsMargins(10, 0, 10, 7)
        section = QLabel("检测项目")
        section.setStyleSheet("font-size:12px;font-weight:bold;color:#667277;")
        list_head.addWidget(section)
        list_head.addStretch()
        hint = QLabel("实时状态")
        hint.setStyleSheet("font-size:11px;color:#9aa0a6;")
        list_head.addWidget(hint)
        lay.addLayout(list_head)

        self._rows = []
        self._list_lay = QVBoxLayout()
        self._list_lay.setContentsMargins(10, 0, 10, 0)
        self._list_lay.setSpacing(0)
        for i, (name, desc) in enumerate(items or []):
            r = _HealthRow(name, desc)
            if i:
                rule = QFrame()
                rule.setFrameShape(QFrame.HLine)
                rule.setFixedHeight(1)
                rule.setStyleSheet("background:#e1e6e4;border:none;")
                self._list_lay.addWidget(rule)
            self._list_lay.addWidget(r)
            self._rows.append(r)
        lay.addLayout(self._list_lay, 1)
        lay.addSpacing(12)
        self.footer_lbl = QLabel("检测完成后将显示最终状态")
        self.footer_lbl.setStyleSheet("font-size:11px;color:#8a9498;")
        lay.addWidget(self.footer_lbl)

        self.items = list(items or [])

    # ---------- 对外接口 ----------

    def set_state(self, index: int, state: str):
        if 0 <= index < len(self._rows):
            self._rows[index].set_state(state)

    def set_states(self, states):
        for i, s in enumerate(states):
            self.set_state(i, s)

    def set_running(self):
        self.title_lbl.setText("正在检测，请稍候...")
        self.subtitle_lbl.setText("网页服务环境检查与必要的自愈处理")
        self.footer_lbl.setText("正在逐项检查，请稍候")
        self.progress.setRange(0, 0)
        self.cancel_btn.setText("取消")
        self.cancel_btn.setEnabled(True)

    def finish(self, summary: str = "检测完成"):
        self.title_lbl.setText("检测完成")
        self.subtitle_lbl.setText("网页服务环境检查已结束")
        self.footer_lbl.setText(summary)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.cancel_btn.setText("关闭")
        self.cancel_btn.setEnabled(True)
        # 结束时停止剩余动画，但保留异常状态，避免掩盖真实结果。
        for r in self._rows:
            if r.state_lbl.text() == "检测中":
                r.set_state("ok")


class DependenciesDialog(QDialog):
    """依赖管理弹窗：列出所有依赖，可单独选择重装"""
    def __init__(self, panel: "WebPanelWidget", card: "SiteCard"):
        super().__init__(panel)
        self.panel = panel
        self.card = card
        self.web_dir = (card.site.get("web_dir") or "").strip()
        self.deps = []
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
        self.list_box = QWidget()
        self.list_lay = QVBoxLayout(self.list_box)
        self.list_lay.setContentsMargins(4, 4, 4, 4)
        self.list_lay.setSpacing(4)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_box)
        lay.addWidget(scroll, 1)
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
        self.panel.run_cmd(f"cat {self._shq(self.web_dir)}/package.json 2>/dev/null", self._on_loaded, timeout=10)

    def _on_loaded(self, r):
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
        checks = "; ".join(f"test -d {self._shq(self.web_dir)}/node_modules/{name} && echo '{name}:yes' || echo '{name}:no'" for name, _, _ in self.deps)
        self.panel.run_cmd(checks, self._on_installed, timeout=15)

    def _on_installed(self, r):
        status = {}
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if ":" in line:
                n, st = line.rsplit(":", 1)
                status[n.strip()] = st.strip() == "yes"
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
        th = InstallDepsThread(self.panel.ssh, self.web_dir, pkgs, path_export=self.panel._node_path_export())
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
        self.panel._check_site_meta(self.card)
        self._load()

    def _set_busy(self, busy: bool):
        self.select_all_btn.setEnabled(not busy)
        self.reinstall_all_btn.setEnabled(not busy)
        self.reinstall_sel_btn.setEnabled(not busy)


class InstallProgressDialog(QDialog):
    """Node.js 安装进度弹窗：进度条 + 实时日志输出。"""

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
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        lay.addWidget(self.progress)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "background:#1e1e1e; color:#d4d4d4; font-family:Consolas,monospace; font-size:12px;"
        )
        lay.addWidget(self.log_view, 1)

        self.close_btn = QPushButton("关闭")
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        btn_lay.addWidget(self.close_btn)
        lay.addLayout(btn_lay)

    def append_log(self, line: str):
        """追加一行日志并自动滚动到底部。"""
        self.log_view.appendPlainText(line)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def set_finished(self, success: bool):
        """安装结束，停止进度条并显示结果。"""
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if success:
            self.label.setText("✅ Node.js 安装成功！")
            self.label.setStyleSheet("font-size:13px; font-weight:bold; color:#2e7d32;")
        else:
            self.label.setText("❌ 安装失败，请查看上方日志定位原因")
            self.label.setStyleSheet("font-size:13px; font-weight:bold; color:#c62828;")
        self.close_btn.setEnabled(True)
