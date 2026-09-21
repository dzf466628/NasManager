"""
服务管理
- 预设服务（Node / Nginx / Docker）+ 自定义服务，一键启动/停止/重启
- 所有服务状态批量检查（一个 SSH 命令搞定，不卡）
- 进程列表：ps aux 解析成表格
"""
import json
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView, QDialog, QFormLayout,
    QLineEdit, QCheckBox, QDialogButtonBox, QScrollArea, QWidget, QMessageBox,
    QSizePolicy, QListWidget, QListWidgetItem, QInputDialog,
)

from widgets.base import BaseWidget
from app_paths import resource_path
from widgets import msg
from widgets.service_helpers import filter_warnings as _helper_filter_warnings
from widgets.service_card import ServiceCard as _ExtractedServiceCard
from widgets.service_components import ClickableLabel as _ExtractedClickableLabel

PRESETS_FILE = resource_path("services") / "presets.json"

# 源站地址（外网访问，带8443端口）
SOURCE_BASE = "https://dudua.synology.me:8443"

def nas_path_to_web_url(path: str) -> str:
    """把NAS路径 /volume1/web/xxx 转成源站网页地址 https://dudua.synology.me:8443/xxx/"""
    rel = path
    for prefix in ("/volume1/web/", "/volume1/web"):
        if rel.startswith(prefix):
            rel = rel[len(prefix):]
            break
    rel = rel.strip("/")
    return f"{SOURCE_BASE}/{rel}/" if rel else SOURCE_BASE + "/"


def sudo_script(ssh, script: str, timeout: int = 12):
    """以 root 执行复合 shell 脚本（含管道/重定向/&&/;）。
    直接 sudo 只作用于复合命令的第一个子命令，故把整条脚本 base64 编码后
    交给 root bash 解码执行，确保 mkdir/chown/chmod/重定向写文件全部在 root 权限下。"""
    import base64 as _b64
    payload = _b64.b64encode(script.encode("utf-8")).decode()
    return ssh.run_command(f'bash -c "echo {payload} | base64 -d | bash"',
                           timeout=timeout, sudo=True)


def shq(s: str) -> str:
    """shell 单引号安全转义"""
    return "'" + str(s).replace("'", "'" + chr(92) + "''") + "'"



# 群晖常见 node/npm 路径
PATH_EXPORT = ("export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin:"
               "/volume1/@appstore/Node.js/usr/local/bin:/var/packages/Node.js/target/usr/local/bin; ")

CARD_STYLE = """
QFrame#SvcCard {
    background:#fff; border:1px solid #e0e0e0; border-radius:10px;
}
QFrame#SvcCard[active="true"] { border:1px solid #a6e3a1; background:#f6fff6; }
QLabel#Dot { font-size:18px; }
QPushButton#SvcBtn {
    background:#f0f2f5; border:none; border-radius:5px; padding:5px 12px;
    font-size:12px; color:#333;
}
QPushButton#SvcBtn:hover { background:#e0e3ea; }
QPushButton#SvcBtn:disabled { color:#bbb; }
QPushButton#DangerBtn {
    background:#fef0f0; border:none; border-radius:5px; padding:5px 12px;
    font-size:12px; color:#c62828;
}
QPushButton#DangerBtn:hover { background:#fde0e0; }
QPushButton#BrowseOnBtn {
    background:#e8f5e9; border:1px solid #4caf50; border-radius:5px;
    padding:5px 12px; font-size:12px; color:#2e7d32; font-weight:bold;
}
QPushButton#BrowseOnBtn:hover { background:#c8e6c9; }
QPushButton#BrowseCheckBtn {
    background:#fff8e1; border:1px solid #ffc107; border-radius:5px;
    padding:5px 12px; font-size:12px; color:#f57f17; font-weight:bold;
}
QPushButton#BrowseCheckBtn:hover { background:#ffecb3; }
QPushButton#BrowseOffBtn {
    background:#ffebee; border:1px solid #f44336; border-radius:5px;
    padding:5px 12px; font-size:12px; color:#c62828; font-weight:bold;
}
QPushButton#BrowseOffBtn:hover { background:#ffcdd2; }
"""


def filter_warnings(text: str) -> str:
    """兼容入口：委托到独立纯逻辑 helper。"""
    return _helper_filter_warnings(text)


class CustomServiceDialog(QDialog):
    """添加/编辑自定义服务"""

    def __init__(self, parent=None, data: dict = None):
        super().__init__(parent)
        self.setWindowTitle("编辑自定义服务" if data else "添加自定义服务")
        self.setMinimumWidth(460)
        self._build()
        if data:
            self.name_edit.setText(data.get("name", ""))
            self.check_edit.setText(data.get("check_cmd", ""))
            self.start_edit.setText(data.get("start_cmd", ""))
            self.stop_edit.setText(data.get("stop_cmd", ""))
            self.restart_edit.setText(data.get("restart_cmd", ""))
            self.sudo_chk.setChecked(data.get("need_sudo", False))

    def _build(self):
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例如：Redis")
        self.check_edit = QLineEdit()
        self.check_edit.setPlaceholderText("pgrep redis-server | head -1")
        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("redis-server --daemonize yes")
        self.stop_edit = QLineEdit()
        self.stop_edit.setPlaceholderText("pkill redis-server")
        self.restart_edit = QLineEdit()
        self.restart_edit.setPlaceholderText("pkill redis-server; sleep 1; redis-server --daemonize yes")
        self.sudo_chk = QCheckBox("需要 sudo")

        form.addRow("服务名称：", self.name_edit)
        form.addRow("检查命令：", self.check_edit)
        form.addRow("启动命令：", self.start_edit)
        form.addRow("停止命令：", self.stop_edit)
        form.addRow("重启命令：", self.restart_edit)
        form.addRow("", self.sudo_chk)
        lay.addLayout(form)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def get_data(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "check_cmd": self.check_edit.text().strip(),
            "start_cmd": self.start_edit.text().strip(),
            "stop_cmd": self.stop_edit.text().strip(),
            "restart_cmd": self.restart_edit.text().strip(),
            "need_sudo": self.sudo_chk.isChecked(),
            "custom": True,
        }


class RemoteDirDialog(QDialog):
    """远程目录选择对话框：浏览 NAS 文件夹，选中后返回路径"""

    def __init__(self, ssh, start_path="/volume1/web", parent=None):
        super().__init__(parent)
        self.ssh = ssh
        self.current_path = start_path or "/volume1/web"
        self.setWindowTitle("选择要开启目录浏览的文件夹")
        self.resize(520, 520)
        self._build()
        self._load_dir()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("路径:"))
        self.path_edit = QLineEdit(self.current_path)
        self.path_edit.returnPressed.connect(self._goto_path)
        path_row.addWidget(self.path_edit, 1)
        up_btn = QPushButton("上级")
        up_btn.clicked.connect(self._go_up)
        path_row.addWidget(up_btn)
        go_btn = QPushButton("转到")
        go_btn.clicked.connect(self._goto_path)
        path_row.addWidget(go_btn)
        new_btn = QPushButton("新建文件夹")
        new_btn.clicked.connect(self._new_folder)
        path_row.addWidget(new_btn)
        lay.addLayout(path_row)

        self.list_w = QListWidget()
        self.list_w.itemDoubleClicked.connect(self._on_double_click)
        self.list_w.setStyleSheet(
            "QListWidget{background:#fff;border:1px solid #ccc;font-size:13px;}"
            "QListWidget::item{color:#222;padding:5px;}"
            "QListWidget::item:selected{background:#e94560;color:#fff;}"
        )
        lay.addWidget(self.list_w, 1)

        hint = QLabel("双击进入文件夹，点「确定」在当前路径开启目录浏览")
        hint.setStyleSheet("color:#888; font-size:11px;")
        lay.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.button(QDialogButtonBox.Ok).setText("确定")
        btns.button(QDialogButtonBox.Cancel).setText("取消")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _load_dir(self):
        self.list_w.clear()
        self.path_edit.setText(self.current_path)
        try:
            entries = self.ssh.ssh_listdir(self.current_path)
            dirs = [e for e in entries if e.get("is_dir") and not e["name"].startswith(".") and e["name"] != "@eaDir"]
            dirs.sort(key=lambda e: e["name"].lower())
            for d in dirs:
                item = QListWidgetItem("📁 " + d["name"])
                item.setData(Qt.UserRole, d["name"])
                self.list_w.addItem(item)
        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取目录失败：{e}")

    def _goto_path(self):
        p = self.path_edit.text().strip()
        if p:
            self.current_path = p.rstrip("/") or "/"
            self._load_dir()

    def _go_up(self):
        if self.current_path == "/":
            return
        self.current_path = self.current_path.rstrip("/").rsplit("/", 1)[0] or "/"
        self._load_dir()

    def _new_folder(self):
        """在当前路径新建文件夹"""
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称:", text="")
        if not ok or not name.strip():
            return
        name = name.strip().replace("/", "_").replace("\\", "_")
        new_path = self.current_path.rstrip("/") + "/" + name
        parent = self.current_path.rstrip("/")
        try:
            script = (
                f"mkdir -p {shq(new_path)}\n"
                f"owner=$(stat -c \'%u:%g\' {shq(parent)} 2>/dev/null) && chown \"$owner\" {shq(new_path)} 2>/dev/null || true\n"
                f"chmod 755 {shq(new_path)}\n"
                "echo DONE\n"
            )
            r = sudo_script(self.ssh, script)
            if r.ok and "DONE" in (r.stdout or ""):
                self._load_dir()
                QMessageBox.information(self, "成功", f"已创建文件夹：{name}")
            else:
                QMessageBox.warning(self, "失败", f"创建失败：{r.stderr or r.stdout or '未知错误'}")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"创建失败：{e}")
    def _on_double_click(self, item):
        name = item.data(Qt.UserRole)
        self.current_path = self.current_path.rstrip("/") + "/" + name
        self._load_dir()

    def selected_path(self):
        # 如果选中了某个文件夹，返回选中的文件夹路径；否则返回当前路径
        items = self.list_w.selectedItems()
        if items:
            name = items[0].data(Qt.UserRole)
            if name:
                return self.current_path.rstrip("/") + "/" + name
        return self.current_path


DIR_BROWSE_PHP = r"""<?php
header('Access-Control-Allow-Origin: *');
header('Content-Type: text/html; charset=utf-8');
$title = '{{TITLE}}';
$files = glob('*');
if (!$files) $files = array();
natcasesort($files);
echo "<!DOCTYPE html><html><head><meta charset='utf-8'><title>" . htmlspecialchars($title) . "</title></head>";
echo "<body><pre><hr /><a href='../'>../</a>\n";
foreach ($files as $f) {
    if ($f === '@eaDir') continue;
    $name = htmlspecialchars($f);
    echo "<a href=\"$name\">$name</a>\n";
}
echo "<hr /></pre></body></html>";"""


class ShareEditDialog(QDialog):
    """编辑单个共享项：可更改目录和备注"""

    def __init__(self, ssh, web_dir, share, parent=None):
        super().__init__(parent)
        self.ssh = ssh
        self.web_dir = web_dir
        self.browse_root = "/volume1/web"
        self.path = share["path"]
        self.note = share.get("note", "")
        self.setWindowTitle("编辑共享")
        self.resize(500, 200)
        self.setStyleSheet(SHARE_DIALOG_STYLE)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("目录:"))
        self.path_lbl = QLabel(self.path)
        self.path_lbl.setStyleSheet("font-size:12px; color:#333;")
        path_row.addWidget(self.path_lbl, 1)
        change_btn = QPushButton("更改目录")
        change_btn.clicked.connect(self._change_dir)
        path_row.addWidget(change_btn)
        lay.addLayout(path_row)

        lay.addWidget(QLabel("备注:"))
        self.note_edit = QLineEdit(self.note)
        self.note_edit.setPlaceholderText("可选，例如：软件更新包")
        lay.addWidget(self.note_edit)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("PrimaryBtn")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _change_dir(self):
        dlg = RemoteDirDialog(self.ssh, self.browse_root, self)
        if dlg.exec() == QDialog.Accepted:
            self.path = dlg.selected_path()
            self.path_lbl.setText(self.path)

    def get_result(self):
        return {"path": self.path, "note": self.note_edit.text().strip()}


SHARE_DIALOG_STYLE = """
QDialog { background: #ffffff; }
QLabel { color: #333; font-size: 12px; }
QPushButton {
    background: #f0f2f5; border: none; border-radius: 5px;
    padding: 6px 14px; font-size: 12px; color: #333;
}
QPushButton:hover { background: #e0e3ea; }
QPushButton#PrimaryBtn { background: #2e7163; color: #fff; }
QPushButton#PrimaryBtn:hover { background: #399d87; }
QPushButton#DangerBtn { background: #fef0f0; color: #c62828; }
QPushButton#DangerBtn:hover { background: #fde0e0; }
QListWidget {
    background: #f8f9fa; border: 1px solid #e0e0e0; border-radius: 8px;
    padding: 4px; outline: none;
}
QListWidget::item {
    background: #fff; border: 1px solid #e8e8e8; border-radius: 6px;
    margin: 2px;
}
QListWidget::item:selected {
    background: #e8f5e9;
    border: 1px solid #a5d6a7;
}
QListWidget::item:hover {
    background: #f5f5f5;
}
QLineEdit {
    padding: 6px 10px; border: 1px solid #d0d0d0; border-radius: 5px;
    font-size: 12px;
}
QLineEdit:focus { border: 1px solid #2e7163; }
"""


class ShareManagerDialog(QDialog):
    """目录共享管理对话框：列出所有共享目录，可添加/编辑/关闭/备注"""

    def __init__(self, ssh, web_dir, shares, parent=None):
        super().__init__(parent)
        self.ssh = ssh
        self.web_dir = web_dir
        self.browse_root = "/volume1/web"  # 目录选择器默认起始路径
        self.state_file = f"{web_dir.rstrip('/')}/.nasmanager_share"
        self.shares = [dict(s) for s in shares]
        self.share_status = {}  # path -> checking/online/offline
        self._blink_on = True
        from PySide6.QtNetwork import QNetworkAccessManager, QSslError
        self.nam = QNetworkAccessManager(self)
        self.nam.sslErrors.connect(lambda reply, errs: reply.ignoreSslErrors())
        self.setWindowTitle("目录共享管理")
        self.resize(600, 440)
        self.setStyleSheet(SHARE_DIALOG_STYLE)
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel("已开启目录共享的文件夹（🟢联通 🟡检测中 🔴不联通，双击备注可编辑）")
        title.setStyleSheet("color:#666; font-size:12px;")
        lay.addWidget(title)

        self.list_w = QListWidget()
        lay.addWidget(self.list_w, 1)
        self._refresh_list()

        # 闪烁定时器
        from PySide6.QtCore import QTimer
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_timer.start(500)

        # 打开后延迟检测连接状态
        QTimer.singleShot(300, self._check_all_shares)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("+ 添加共享")
        add_btn.setObjectName("PrimaryBtn")
        add_btn.clicked.connect(self._add_share)
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("PrimaryBtn")
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    def _refresh_list(self):
        self.list_w.clear()
        if not self.shares:
            item = QListWidgetItem("（暂无共享目录，点下方「添加共享」）")
            item.setForeground(Qt.gray)
            self.list_w.addItem(item)
            return
        for i, s in enumerate(self.shares):
            widget = QWidget()
            h = QHBoxLayout(widget)
            h.setContentsMargins(12, 8, 12, 8)
            # 状态圆点
            status = self.share_status.get(s["path"], "checking")
            dot = QLabel()
            dot.setFixedSize(12, 12)
            if status == "online":
                dot.setStyleSheet("background-color:#4caf50; border-radius:6px;")
                dot.setToolTip("连接正常")
            elif status == "offline":
                dot.setStyleSheet("background-color:#f44336; border-radius:6px;")
                dot.setToolTip("连接失败，检查 .index.php 是否存在或网络是否可达")
            else:
                c = "#ffc107" if self._blink_on else "#fff3cd"
                dot.setStyleSheet(f"background-color:{c}; border-radius:6px;")
                dot.setToolTip("检测中...")
            h.addWidget(dot)
            h.addSpacing(8)
            v = QVBoxLayout()
            v.setSpacing(4)
            # 备注：醒目大字
            note_text = s.get("note") or "（双击添加备注）"
            note_lbl = ClickableLabel(note_text)
            note_lbl.setStyleSheet("""
                QLabel { color: #1a1a2e; font-size: 14px; font-weight: bold; }
                QLabel:hover { color: #2e7163; }
            """)
            note_lbl.setCursor(Qt.IBeamCursor)
            note_lbl.double_clicked.connect(lambda idx=i: self._edit_note(idx))
            # 地址：淡化小字，单击打开网页；连接失败时变红
            path_lbl = ClickableLabel(nas_path_to_web_url(s["path"]) + ".index.php")
            if status == "offline":
                path_lbl.setStyleSheet("""
                    QLabel { color: #f44336; font-size: 11px; }
                    QLabel:hover { color: #d32f2f; text-decoration: underline; }
                """)
                path_lbl.setToolTip("连接失败，单击尝试打开")
            else:
                path_lbl.setToolTip("单击在浏览器中打开")
            path_lbl.clicked.connect(lambda p=s["path"]: self._open_in_browser(p))
            v.addWidget(note_lbl)
            v.addWidget(path_lbl)
            h.addLayout(v, 1)
            # 连接失败时显示重新部署按钮
            if status == "offline":
                redeploy_btn = QPushButton("重新部署")
                redeploy_btn.setObjectName("PrimaryBtn")
                redeploy_btn.clicked.connect(lambda _, p=s["path"]: self._redeploy_share(p))
                h.addWidget(redeploy_btn)
            del_btn = QPushButton("关闭共享")
            del_btn.setObjectName("DangerBtn")
            del_btn.clicked.connect(lambda _, idx=i: self._remove_share(idx))
            h.addWidget(del_btn)
            item = QListWidgetItem()
            item.setData(Qt.UserRole, i)
            item.setSizeHint(widget.sizeHint())
            self.list_w.addItem(item)
            self.list_w.setItemWidget(item, widget)

    def _toggle_blink(self):
        """闪烁切换：有检测中的项才刷新"""
        self._blink_on = not self._blink_on
        if any(v == "checking" for v in self.share_status.values()):
            self._refresh_list()

    def _check_all_shares(self):
        """检测所有共享目录的连接状态"""
        for s in self.shares:
            path = s["path"]
            self.share_status[path] = "checking"
            self._check_share_url(path)
        self._refresh_list()

    def _check_share_url(self, path: str):
        """异步检测单个共享的 HTTP 可达性"""
        rel = path
        for prefix in ("/volume1/web/", "/volume1/web"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
                break
        url = nas_path_to_web_url(path) + ".index.php"
        from PySide6.QtNetwork import QNetworkRequest
        from PySide6.QtCore import QUrl
        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(5000)  # 5秒超时
        reply = self.nam.get(req)
        reply.finished.connect(lambda r=reply, p=path: self._on_check_result(r, p))

    def _on_check_result(self, reply, path: str):
        """检测结果回调"""
        from PySide6.QtNetwork import QNetworkReply
        if reply.error() == QNetworkReply.NoError:
            self.share_status[path] = "online"
        else:
            self.share_status[path] = "offline"
        reply.deleteLater()
        self._refresh_list()

    def _write_php(self, path: str, note: str = ""):
        """即时写入隐藏的 .index.php（整条脚本以 root 执行）"""
        import base64
        title = note.strip() if note and note.strip() else path.rsplit("/", 1)[-1] or path
        title = title.replace("\'", "\\'").replace('"', '\\"')
        php = DIR_BROWSE_PHP.replace("{{TITLE}}", title)
        b64 = base64.b64encode(php.encode("utf-8")).decode()
        parent = path.rsplit("/", 1)[0] or "/"
        p, pp, fphp = shq(path), shq(parent), shq(path + "/.index.php")
        script = (
            f"mkdir -p {p}\n"
            f"owner=$(stat -c \'%u:%g\' {pp} 2>/dev/null) && chown \"$owner\" {p} 2>/dev/null || true\n"
            f"chmod 755 {p}\n"
            f"echo {shq(b64)} | base64 -d > {fphp}\n"
            f"owner2=$(stat -c \'%u:%g\' {p} 2>/dev/null) && chown \"$owner2\" {fphp} 2>/dev/null || true\n"
            f"chmod 644 {fphp}\n"
            f"test -s {fphp} && echo OK || echo MISSING\n"
        )
        r = sudo_script(self.ssh, script)
        out = (r.stdout or "") + (r.stderr or "")
        if not r.ok or "OK" not in (r.stdout or ""):
            raise RuntimeError(f"部署 .index.php 失败：{out.strip() or '未知错误'}")
    def _remove_php(self, path: str):
        """即时删除 .index.php"""
        self.ssh.run_command(f"rm -f {path}/.index.php", timeout=8, sudo=True)

    def _save_state(self):
        """即时保存共享列表到 NAS 状态文件（root 执行重定向）"""
        import base64
        b64 = base64.b64encode(
            json.dumps(self.shares, ensure_ascii=False).encode("utf-8")).decode()
        sudo_script(self.ssh,
                    f"echo {shq(b64)} | base64 -d > {shq(self.state_file)}\n",
                    timeout=8)
    def _edit_share(self, idx):
        dlg = ShareEditDialog(self.ssh, self.web_dir, self.shares[idx], self)
        if dlg.exec() != QDialog.Accepted:
            return
        result = dlg.get_result()
        # 如果改了目录，检查新目录是否已存在
        if result["path"] != self.shares[idx]["path"]:
            for j, s in enumerate(self.shares):
                if j != idx and s["path"] == result["path"]:
                    QMessageBox.information(self, "提示", "该目录已在共享列表中")
                    return
        self.shares[idx] = result
        self._refresh_list()

    def _add_share(self):
        dlg = RemoteDirDialog(self.ssh, self.browse_root, self)
        if dlg.exec() != QDialog.Accepted:
            return
        path = dlg.selected_path()
        for s in self.shares:
            if s["path"] == path:
                QMessageBox.information(self, "提示", "该目录已在共享列表中")
                return
        note, ok = QInputDialog.getText(self, "添加备注", f"为 {path} 添加备注（可选）：")
        if not ok:
            note = ""
        self.shares.append({"path": path, "note": note.strip()})
        try:
            self._write_php(path, note.strip())
        except Exception as e:
            QMessageBox.warning(self, "部署失败", f".index.php 写入失败：\n{e}\n\n共享已添加，但需要手动部署或检查权限。")
        self._save_state()
        self.share_status[path] = "checking"
        self._refresh_list()
        self._check_share_url(path)

    def _open_in_browser(self, path: str):
        """双击路径：在浏览器中打开对应的网页"""
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        url = nas_path_to_web_url(path) + ".index.php"
        QDesktopServices.openUrl(QUrl(url))

    def _edit_note(self, idx: int):
        """双击备注：直接编辑备注"""
        s = self.shares[idx]
        note, ok = QInputDialog.getText(
            self, "编辑备注", f"目录：{s['path']}\n备注：", text=s.get("note", ""))
        if ok:
            self.shares[idx]["note"] = note.strip()
            self._save_state()
            self._refresh_list()

    def _remove_share(self, idx):
        s = self.shares[idx]
        if QMessageBox.question(
                self, "确认", f"关闭 {s['path']} 的目录共享？\n（将删除该目录下的 .index.php）"
        ) != QMessageBox.Yes:
            return
        self.shares.pop(idx)
        self._remove_php(s["path"])
        self._save_state()
        self._refresh_list()

    def _redeploy_share(self, path: str):
        """重新部署：重新写入 .index.php 并重新检测连接状态"""
        note = ""
        for s in self.shares:
            if s["path"] == path:
                note = s.get("note", "")
                break
        try:
            self._write_php(path, note)
            QMessageBox.information(self, "成功", "重新部署成功！")
        except Exception as e:
            QMessageBox.warning(self, "部署失败", f".index.php 写入失败：\n{e}")
        self.share_status[path] = "checking"
        self._refresh_list()
        self._check_share_url(path)

    def get_shares(self):
        return self.shares


class ServiceCard(QFrame):
    """单个服务卡片"""

    def __init__(self, svc: dict, mgr):
        super().__init__()
        self.svc = svc
        self.mgr = mgr
        self.setObjectName("SvcCard")
        self.setStyleSheet(CARD_STYLE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(96)
        self.active = False
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setObjectName("Dot")
        self.dot.setStyleSheet("color:#bbb;")
        self.name_lbl = QLabel(self.svc["name"])
        self.name_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#1a1a2e;")
        self.status_lbl = QLabel("检测中...")
        self.status_lbl.setStyleSheet("font-size:11px; color:#888;")
        top.addWidget(self.dot)
        top.addWidget(self.name_lbl)
        top.addStretch()
        top.addWidget(self.status_lbl)
        lay.addLayout(top)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        # 启动/停止合为一个按钮，根据运行状态切换
        self.toggle_btn = QPushButton("启动")
        self.toggle_btn.setObjectName("SvcBtn")
        self.toggle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_btn.clicked.connect(self._on_toggle)
        btns.addWidget(self.toggle_btn)

        # 目录共享（仅 Nginx 卡片显示，绿色=有共享目录）
        if self.svc.get("name") == "Nginx":
            self.browse_btn = QPushButton("目录共享")
            self.browse_btn.setObjectName("SvcBtn")
            self.browse_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.browse_btn.clicked.connect(lambda: self.mgr.open_share_manager(self))
            btns.addWidget(self.browse_btn)
            self.browse_enabled = False

        # Nginx 等高级按钮（按配置字段动态添加）
        if self.svc.get("systemd_service"):
            rst_btn = QPushButton("重启服务")
            rst_btn.setObjectName("SvcBtn")
            rst_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            rst_btn.clicked.connect(lambda: self.mgr.svc_systemd_restart(self))
            btns.addWidget(rst_btn)
        if self.svc.get("config_path"):
            view_btn = QPushButton("查看配置")
            view_btn.setObjectName("SvcBtn")
            view_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view_btn.clicked.connect(lambda: self.mgr.svc_view_config(self))
            btns.addWidget(view_btn)

        # 有日志路径的服务提供"日志"按钮
        if self.svc.get("log_path"):
            log_btn = QPushButton("日志")
            log_btn.setObjectName("SvcBtn")
            log_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            log_btn.clicked.connect(lambda: self.mgr.show_service_log(self))
            btns.addWidget(log_btn)

        if self.svc.get("custom"):
            del_btn = QPushButton("删除")
            del_btn.setObjectName("DangerBtn")
            del_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            del_btn.clicked.connect(lambda: self.mgr.delete_custom_service(self))
            btns.addWidget(del_btn)

        lay.addLayout(btns)

    def _on_toggle(self):
        """根据当前状态决定启动还是停止"""
        action = "stop" if self.active else "start"
        self.mgr.service_action(self, action)

    def set_active(self, active: bool):
        self.active = active
        self.setProperty("active", "true" if active else "false")
        self.dot.setStyleSheet("color:#4caf50;" if active else "color:#bbb;")
        self.status_lbl.setText("运行中" if active else "已停止")
        self.status_lbl.setStyleSheet(
            "font-size:11px; color:#2e7d32;" if active else "font-size:11px; color:#999;"
        )
        # 启动/停止按钮随状态切换文字和颜色
        self.toggle_btn.setText("停止" if active else "启动")
        self.toggle_btn.setObjectName("DangerBtn" if active else "SvcBtn")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_browse_enabled(self, enabled: bool):
        """目录共享状态：绿色=有共享目录"""
        self.browse_enabled = enabled
        self.browse_btn.setText("✓ 目录共享" if enabled else "目录共享")
        self.browse_btn.setObjectName("BrowseOnBtn" if enabled else "SvcBtn")
        self.browse_btn.style().unpolish(self.browse_btn)
        self.browse_btn.style().polish(self.browse_btn)

    def set_share_status(self, online: int, offline: int, checking: bool = False):
        """目录共享连接状态：绿=全部正常，红=有失败，黄=检测中"""
        if checking:
            self.browse_btn.setText("目录共享 检测中...")
            self.browse_btn.setObjectName("BrowseCheckBtn")
        elif offline > 0:
            self.browse_btn.setText(f"目录共享 ({offline}个失败)")
            self.browse_btn.setObjectName("BrowseOffBtn")
        elif online > 0:
            self.browse_btn.setText(f"✓ 目录共享 ({online}个在线)")
            self.browse_btn.setObjectName("BrowseOnBtn")
        else:
            self.browse_btn.setText("目录共享")
            self.browse_btn.setObjectName("SvcBtn")
        self.browse_btn.style().unpolish(self.browse_btn)
        self.browse_btn.style().polish(self.browse_btn)


# Runtime compatibility aliases; extracted widgets preserve existing contracts.
ClickableLabel = _ExtractedClickableLabel
ServiceCard = _ExtractedServiceCard


class ServiceManagerWidget(BaseWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.presets = self._load_presets()
        self.cards: list[ServiceCard] = []
        self.conn = None
        self._share_check_results = {}  # path -> online/offline
        from PySide6.QtNetwork import QNetworkAccessManager
        self._share_nam = QNetworkAccessManager(self)
        self._share_nam.sslErrors.connect(lambda reply, errs: reply.ignoreSslErrors())
        self._build_ui()

    def set_conn_info(self, conn):
        """传入连接配置，用于替换 {web_dir} 等占位符"""
        self.conn = conn
        self._build_cards()

    def _resolve_cmd(self, cmd: str) -> str:
        """把命令里的占位符替换成连接配置的实际值（不再写死路径）"""
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
            return {"services": [], "custom_services": []}

    def _save_presets(self):
        try:
            PRESETS_FILE.write_text(json.dumps(self.presets, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "错误", f"保存配置失败: {e}")

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        header = QHBoxLayout()
        title = QLabel("服务管理")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        header.addWidget(title)
        header.addStretch()
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.setStyleSheet("padding:6px 16px;")
        self.refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.refresh_btn)
        self.add_btn = QPushButton("+ 自定义服务")
        self.add_btn.setStyleSheet("padding:6px 16px;")
        self.add_btn.clicked.connect(self._add_custom)
        header.addWidget(self.add_btn)
        outer.addLayout(header)

        # 卡片区（置顶紧凑，按内容高度，不留大片空白）
        cards_wrap = QWidget()
        self.cards_grid = QGridLayout(cards_wrap)
        self.cards_grid.setContentsMargins(0, 0, 0, 0)
        self.cards_grid.setHorizontalSpacing(10)
        self.cards_grid.setVerticalSpacing(10)
        outer.addWidget(cards_wrap)
        self._build_cards()

        proc_title = QLabel("进程列表")
        proc_title.setStyleSheet("font-size:15px; font-weight:bold; color:#1a1a2e; margin-top:8px;")
        outer.addWidget(proc_title)

        self.proc_table = QTableWidget(0, 4)
        self.proc_table.setHorizontalHeaderLabels(["进程号", "CPU 使用率", "内存使用率", "命令"])
        self.proc_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.proc_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.proc_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.proc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.proc_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.proc_table.setAlternatingRowColors(True)
        outer.addWidget(self.proc_table, 1)

    def _build_cards(self):
        while self.cards_grid.count():
            item = self.cards_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.cards.clear()

        all_svcs = list(self.presets.get("services", [])) + list(self.presets.get("custom_services", []))
        # 每个服务横向占满整行
        for i, svc in enumerate(all_svcs):
            card = ServiceCard(svc, self)
            self.cards.append(card)
            self.cards_grid.addWidget(card, i, 0, 1, 2)
        self.cards_grid.setColumnStretch(0, 1)
        self.cards_grid.setColumnStretch(1, 1)

    def on_show(self):
        self.refresh()

    def refresh(self):
        if not self.ssh or not self.ssh.is_connected:
            return
        # 批量检查所有服务（进程检测不需要 sudo）
        cmds = []
        for card in self.cards:
            check = self._resolve_cmd(card.svc.get("check_cmd", ""))
            if not check:
                card.set_active(False)
                continue
            tag = f"@@{card.svc['name']}@@"
            cmds.append(f"echo '{tag}'; {check}")
        if cmds:
            self.run_cmd("; ".join(cmds), self._on_batch_check, timeout=12)

        # 进程列表
        self.run_cmd("ps aux", self._on_ps, timeout=15)

        # 目录共享连接状态检测
        for card in self.cards:
            if card.svc.get("name") == "Nginx" and hasattr(card, "browse_btn"):
                self.check_shares(card)

    def _on_batch_check(self, r):
        if not r.stdout:
            return
        current_name = None
        has_output = {}
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("@@") and line.endswith("@@"):
                current_name = line.strip("@")
                has_output[current_name] = ""
            elif current_name and line:
                has_output[current_name] = line
        for card in self.cards:
            if card.svc["name"] in has_output:
                card.set_active(bool(has_output[card.svc["name"]]))
            # Nginx 卡片同时检测目录共享状态
            if hasattr(card, "browse_btn"):
                self.check_shares(card)

    # ---------- Nginx 高级控制 ----------

    def _need_sudo_tip(self):
        if not self.need_sudo():
            QMessageBox.warning(self, "提示", "该操作需要 sudo 权限（已自动使用连接密码，如需覆盖请在工具栏设置）")
            return False
        return True

    def svc_reload(self, card: ServiceCard):
        """热重载（nginx -s reload，不断连接）"""
        if not self._need_sudo_tip():
            return
        cmd = self._resolve_cmd(card.svc.get("reload_cmd", "nginx -s reload"))
        self.run_cmd(cmd, lambda r, c=card: self._on_svc_reload(c, r), timeout=10, sudo=True)

    def _on_svc_reload(self, card: ServiceCard, r):
        if r.ok:
            self.status_msg(f"{card.svc['name']} 已热重载")
        else:
            msg.warn(self, "重载失败", filter_warnings(r.stderr or r.stdout)[:300])

    def svc_config_test(self, card: ServiceCard):
        if not self._need_sudo_tip():
            return
        cmd = self._resolve_cmd(card.svc.get("config_test_cmd", "nginx -t"))
        self.run_cmd(cmd, lambda r, c=card: self._on_svc_config_test(c, r), timeout=10, sudo=True)

    def _on_svc_config_test(self, card: ServiceCard, r):
        out = filter_warnings((r.stdout + r.stderr).strip())
        msg.info(self, f"{card.svc['name']} 配置测试", out[:800] or "配置正常（无输出）")

    def svc_systemd_restart(self, card: ServiceCard):
        """完整重启服务（systemctl restart，解决卡死/配置问题）"""
        if not self._need_sudo_tip():
            return
        svc = card.svc.get("systemd_service", "")
        if not svc:
            return
        if QMessageBox.question(self, "确认", f"完整重启 {svc} 服务？\n（会短暂中断 Nginx，几十秒后恢复）") != QMessageBox.Yes:
            return
        self.run_cmd(f"systemctl restart {svc}", lambda r, c=card: self._on_svc_systemd(c, r),
                     timeout=30, sudo=True)

    def _on_svc_systemd(self, card: ServiceCard, r):
        if r.ok:
            self.status_msg(f"{card.svc['name']} 已重启")
            QTimer.singleShot(1500, self.refresh)
        else:
            msg.warn(self, "重启失败", filter_warnings(r.stderr or r.stdout)[:300])

    # ---------- 目录共享管理 ----------

    def _share_state_file(self) -> str:
        web = self._resolve_cmd("{web_dir}") or "/volume1/web"
        return f"{web.rstrip('/')}/.nasmanager_share"

    def check_shares(self, card: "ServiceCard"):
        """从 NAS 读取共享列表，有共享则按钮变绿"""
        sf = self._share_state_file()
        self.run_cmd(
            f"cat {sf} 2>/dev/null",
            lambda r, c=card: self._on_check_shares(c, r), timeout=8)

    def _on_check_shares(self, card, r):
        try:
            shares = json.loads((r.stdout or "").strip() or "[]")
        except Exception:
            shares = []
        if not shares:
            card.set_share_status(0, 0, False)
            return
        # 有共享：先显示检测中，再异步检测每个的 HTTP 可达性
        self._share_check_results = {}
        card.set_share_status(0, 0, True)
        for s in shares:
            self._check_share_url(card, s["path"], len(shares))

    def _check_share_url(self, card, path: str, total: int):
        """异步检测单个共享的 HTTP 可达性"""
        rel = path
        for prefix in ("/volume1/web/", "/volume1/web"):
            if rel.startswith(prefix):
                rel = rel[len(prefix):]
                break
        url = nas_path_to_web_url(path) + ".index.php"
        from PySide6.QtNetwork import QNetworkRequest
        from PySide6.QtCore import QUrl
        req = QNetworkRequest(QUrl(url))
        req.setTransferTimeout(5000)
        reply = self._share_nam.get(req)
        reply.finished.connect(
            lambda r=reply, c=card, p=path, t=total: self._on_share_url_result(r, c, p, t))

    def _on_share_url_result(self, reply, card, path: str, total: int):
        """单个共享检测结果：全部完成后更新按钮状态"""
        from PySide6.QtNetwork import QNetworkReply
        self._share_check_results[path] = (
            "online" if reply.error() == QNetworkReply.NoError else "offline")
        reply.deleteLater()
        if len(self._share_check_results) >= total:
            online = sum(1 for v in self._share_check_results.values() if v == "online")
            offline = sum(1 for v in self._share_check_results.values() if v == "offline")
            card.set_share_status(online, offline, False)

    def open_share_manager(self, card: "ServiceCard"):
        """打开目录共享管理对话框"""
        web = self._resolve_cmd("{web_dir}") or "/volume1/web"
        sf = self._share_state_file()
        # 读取当前共享列表
        r = self.ssh.run_command(f"cat {sf} 2>/dev/null", timeout=8)
        try:
            shares = json.loads((r.stdout or "").strip() or "[]")
        except Exception:
            shares = []
        dlg = ShareManagerDialog(self.ssh, web, shares, self)
        dlg.exec()  # 操作在对话框内即时执行，不等确定
        card.set_browse_enabled(len(dlg.shares) > 0)

    def _write_share_php(self, path: str, note: str = ""):
        """在目标目录写入隐藏的 .index.php（整条脚本以 root 执行）"""
        import base64
        title = note.strip() if note and note.strip() else path.rsplit("/", 1)[-1] or path
        title = title.replace("\'", "\\'").replace('"', '\\"')
        php = DIR_BROWSE_PHP.replace("{{TITLE}}", title)
        b64 = base64.b64encode(php.encode("utf-8")).decode()
        parent = path.rsplit("/", 1)[0] or "/"
        p, pp, fphp = shq(path), shq(parent), shq(path + "/.index.php")
        script = (
            f"mkdir -p {p}\n"
            f"owner=$(stat -c \'%u:%g\' {pp} 2>/dev/null) && chown \"$owner\" {p} 2>/dev/null || true\n"
            f"chmod 755 {p}\n"
            f"echo {shq(b64)} | base64 -d > {fphp}\n"
            f"owner2=$(stat -c \'%u:%g\' {p} 2>/dev/null) && chown \"$owner2\" {fphp} 2>/dev/null || true\n"
            f"chmod 644 {fphp}\n"
            f"test -s {fphp} && echo OK || echo MISSING\n"
        )
        r = sudo_script(self.ssh, script)
        out = (r.stdout or "") + (r.stderr or "")
        if not r.ok or "OK" not in (r.stdout or ""):
            raise RuntimeError(f"部署 .index.php 失败：{out.strip() or '未知错误'}")
    def svc_view_config(self, card: ServiceCard):
        if not self._need_sudo_tip():
            return
        path = self._resolve_cmd(card.svc.get("config_path", ""))
        if not path:
            return
        self.run_cmd(f"cat {path} 2>/dev/null | head -120", lambda r, c=card, p=path: self._on_svc_view_config(c, p, r),
                     timeout=10, sudo=True)

    def _on_svc_view_config(self, card: ServiceCard, path, r):
        content = (r.stdout or "").strip()
        if not content:
            content = "(配置为空或读取失败)"
        msg.info(self, f"{card.svc['name']} 配置 ({path})", content[:3000])

    def service_action(self, card: ServiceCard, action: str):
        cmd_key = {"start": "start_cmd", "stop": "stop_cmd", "restart": "restart_cmd"}[action]
        cmd = self._resolve_cmd(card.svc.get(cmd_key, ""))
        if not cmd:
            QMessageBox.information(self, "提示", f"该服务未配置{action}命令")
            return
        sudo = card.svc.get("need_sudo", False)
        if sudo and not self.need_sudo():
            QMessageBox.warning(self, "提示", "该服务需要 sudo，请先在工具栏设置 Sudo 密码")
            return

        # start/restart 一律走 detached：进程可能前台运行，detached 不会等它结束而超时
        # stop 用 run_cmd 等待完成
        if action in ("start", "restart"):
            self.run_detached_cmd(cmd, lambda r, c=card, a=action: self._after_action(c, a, r),
                                  wait=2, sudo=sudo)
        else:
            self.run_cmd(cmd, lambda r, c=card, a=action: self._after_action(c, a, r),
                         timeout=20, sudo=sudo)

    def _after_action(self, card: ServiceCard, action: str, r):
        if r.ok:
            self.status_msg(f"{card.svc['name']} {action} 成功")
        else:
            err = filter_warnings(r.stderr or r.stdout or "命令执行失败")
            QMessageBox.warning(self, "操作失败",
                                f"{card.svc['name']} {action} 失败 (exit {r.exit_code}):\n{err[:500]}")
        QTimer.singleShot(1500, self.refresh)

    def _on_ps(self, r):
        if not r.ok:
            return
        lines = r.stdout.strip().splitlines()
        if len(lines) < 2:
            return
        procs = []
        for line in lines[1:]:
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            pid, cpu, mem, cmd = parts[1], parts[2], parts[3], parts[10]
            try:
                cpu_f = float(cpu)
            except ValueError:
                cpu_f = 0
            procs.append((int(pid), cpu_f, mem, cmd))
        procs.sort(key=lambda x: x[1], reverse=True)
        procs = procs[:100]

        # 网站进程归属：{匹配关键字: 颜色}
        from widgets.site_colors import site_color_hex
        sites = self.conn.sites if self.conn else []
        site_procs = {}
        for s in sites:
            np_ = (s.get("node_proc") or "").strip()
            if np_:
                key = np_.split()[-1]  # "node server.js" -> "server.js"
                if key:
                    site_procs[key] = site_color_hex(sites, s.get("name"))

        self.proc_table.setRowCount(len(procs))
        for row, (pid, cpu, mem, cmd) in enumerate(procs):
            self.proc_table.setItem(row, 0, QTableWidgetItem(str(pid)))
            cpu_item = QTableWidgetItem(f"{cpu:.1f}")
            if cpu > 50:
                cpu_item.setForeground(QColor("#c62828"))
            self.proc_table.setItem(row, 1, cpu_item)
            self.proc_table.setItem(row, 2, QTableWidgetItem(mem))
            self.proc_table.setItem(row, 3, QTableWidgetItem(cmd[:200]))
            # 属于某网站的进程：整行用网站颜色
            color = next((c for key, c in site_procs.items() if key and key in cmd), None)
            if color:
                bg = QColor(color)
                bg.setAlpha(26)
                for col in range(4):
                    it = self.proc_table.item(row, col)
                    it.setBackground(QBrush(bg))
                    it.setForeground(QBrush(QColor(color)))

    def _add_custom(self):
        dlg = CustomServiceDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            if not data["name"]:
                return
            self.presets.setdefault("custom_services", []).append(data)
            self._save_presets()
            self._build_cards()
            self.refresh()

    def _on_show_log(self, card: ServiceCard, r):
        content = (r.stdout + r.stderr).strip()
        if not content:
            content = "日志为空（文件可能不存在）"
        msg.info(self, f"{card.svc['name']} 日志", content[:3000])

    def delete_custom_service(self, card: ServiceCard):
        if QMessageBox.question(self, "确认", f"删除自定义服务「{card.svc['name']}」？") != QMessageBox.Yes:
            return
        self.presets["custom_services"] = [
            s for s in self.presets.get("custom_services", [])
            if s.get("name") != card.svc["name"]
        ]
        self._save_presets()
        self._build_cards()

    def show_service_log(self, card: ServiceCard):
        """读取服务日志尾部并显示，排查启动失败"""
        log_path = self._resolve_cmd(card.svc.get("log_path", ""))
        if not log_path:
            QMessageBox.information(self, "提示", "该服务未配置日志路径")
            return
        self.run_cmd(f"tail -n 100 {log_path} 2>&1", lambda r, c=card: self._on_show_log(c, r),
                     timeout=10)
