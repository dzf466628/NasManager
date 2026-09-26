"""
SFTP 文件管理
- 文件列表（名称/大小/修改时间）
- 上传 / 下载 / 删除 / 重命名 / 新建文件夹
- 双击文本文件 → 内置编辑器 → 保存写回 NAS
"""
import datetime
import stat
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont, QAction
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QTableWidget,
    QTableWidgetItem, QHeaderView, QFileDialog, QMessageBox, QMenu,
    QPlainTextEdit, QDialog, QDialogButtonBox, QSizePolicy, QInputDialog,
)

import telemetry
from widgets.base import BaseWidget

TEXT_EXTENSIONS = {
    ".conf", ".cfg", ".ini", ".json", ".xml", ".yml", ".yaml", ".txt", ".md",
    ".sh", ".py", ".js", ".ts", ".html", ".css", ".log", ".env", ".conf",
    ".nginx", ".service", ".crt", ".pem", ".csv", ".sql", ".php", ".rb",
}


class ListDirWorker(QThread):
    """后台列目录，避免网络操作卡 UI"""
    result = Signal(list, str)  # entries, error

    def __init__(self, ssh, path: str):
        super().__init__()
        self.ssh = ssh
        self.path = path

    def run(self):
        try:
            entries = self.ssh.ssh_listdir(self.path)
            self.result.emit(entries, self.ssh.last_sftp_error)
        except Exception as e:
            self.result.emit([], f"列目录异常: {e}")


class FileEditorDialog(QDialog):
    """内置文本编辑器"""

    def __init__(self, ssh, remote_path: str, parent=None):
        super().__init__(parent)
        self.ssh = ssh
        self.remote_path = remote_path
        self.setWindowTitle(f"编辑 - {remote_path}")
        self.resize(800, 600)
        self._build()
        self._load()

    def _build(self):
        lay = QVBoxLayout(self)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas, 'Courier New', monospace", 11))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.editor)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _load(self):
        content = self.ssh.ssh_read_file(self.remote_path)
        if content is None:
            QMessageBox.critical(self, "错误", f"读取文件失败: {self.ssh.last_sftp_error}")
            self.reject()
            return
        self.editor.setPlainText(content)

    def _save(self):
        if self.ssh.ssh_write_file(self.remote_path, self.editor.toPlainText()):
            QMessageBox.information(self, "成功", "文件已保存")
            self.accept()
        else:
            QMessageBox.critical(self, "错误", f"保存失败: {self.ssh.last_sftp_error}")


class FileBrowserWidget(BaseWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_path = "/"
        self._list_worker = None
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        title = QLabel("文件管理")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        outer.addWidget(title)

        # 路径栏
        nav = QHBoxLayout()
        self.up_btn = QPushButton("上级")
        self.up_btn.setStyleSheet("padding:5px 12px;")
        self.up_btn.clicked.connect(self._go_up)
        self.home_btn = QPushButton("根目录")
        self.home_btn.setStyleSheet("padding:5px 12px;")
        self.home_btn.clicked.connect(lambda: self._cd("/"))
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setStyleSheet("padding:5px 12px;")
        self.refresh_btn.clicked.connect(self.refresh)

        self.path_edit = QLineEdit()
        self.path_edit.setText("/")
        self.path_edit.returnPressed.connect(lambda: self._cd(self.path_edit.text().strip()))

        nav.addWidget(self.up_btn)
        nav.addWidget(self.home_btn)
        nav.addWidget(self.path_edit, 1)
        nav.addWidget(self.refresh_btn)
        outer.addLayout(nav)

        # 操作按钮
        ops = QHBoxLayout()
        self.upload_btn = QPushButton("上传文件")
        self.upload_btn.clicked.connect(self._upload)
        self.mkdir_btn = QPushButton("新建文件夹")
        self.mkdir_btn.clicked.connect(self._mkdir)
        self.download_btn = QPushButton("下载")
        self.download_btn.clicked.connect(self._download)
        self.rename_btn = QPushButton("重命名")
        self.rename_btn.clicked.connect(self._rename)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setStyleSheet("color:#c62828;")
        self.delete_btn.clicked.connect(self._delete)
        for b in (self.upload_btn, self.mkdir_btn, self.download_btn, self.rename_btn, self.delete_btn):
            b.setStyleSheet(b.styleSheet() + "padding:5px 12px;")
            ops.addWidget(b)
        ops.addStretch()
        outer.addLayout(ops)

        # 文件列表
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["名称", "大小", "修改时间", "类型"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        outer.addWidget(self.table, 1)

    def on_show(self):
        if self.current_path == "/":
            self.refresh()

    def refresh(self):
        if not self.ssh or not self.ssh.is_connected:
            return
        if hasattr(self, "_list_worker") and self._list_worker is not None and self._list_worker.isRunning():
            return
        self.status_msg("加载中...")
        w = ListDirWorker(self.ssh, self.current_path)
        w.result.connect(self._on_list_result)
        w.finished.connect(lambda: setattr(self, "_list_worker", None))
        self._list_worker = w
        w.start()

    def _on_list_result(self, entries, error):
        if error and not entries:
            self.status_msg(f"文件列表错误: {error}")
        else:
            self.status_msg("")
        self._populate(entries)

    def _populate(self, entries):
        self.table.setRowCount(len(entries))
        for row, e in enumerate(entries):
            name = e["name"]
            is_dir = e["is_dir"]
            display = ("📁 " if is_dir else "📄 ") + name
            name_item = QTableWidgetItem(display)
            if is_dir:
                name_item.setForeground(Qt.darkBlue)
            self.table.setItem(row, 0, name_item)

            size = "" if is_dir else self._fmt_size(e["size"])
            self.table.setItem(row, 1, QTableWidgetItem(size))

            mtime = datetime.datetime.fromtimestamp(e["mtime"]).strftime("%Y-%m-%d %H:%M")
            self.table.setItem(row, 2, QTableWidgetItem(mtime))
            self.table.setItem(row, 3, QTableWidgetItem("目录" if is_dir else "文件"))

    def _cd(self, path: str):
        if not path:
            return
        if not path.startswith("/"):
            path = self.current_path.rstrip("/") + "/" + path
        self.current_path = path
        self.path_edit.setText(path)
        self.refresh()

    def _go_up(self):
        if self.current_path == "/":
            return
        parent = self.current_path.rstrip("/").rsplit("/", 1)[0] or "/"
        self._cd(parent)

    def _on_double_click(self, index):
        row = index.row()
        name = self.table.item(row, 0).text().strip("📁📄 ").strip()
        is_dir = self.table.item(row, 3).text() == "目录"
        path = self.current_path.rstrip("/") + "/" + name
        if is_dir:
            self._cd(path)
        else:
            self._open_file(path, name)

    def _open_file(self, path: str, name: str):
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext in TEXT_EXTENSIONS:
            dlg = FileEditorDialog(self.ssh, path, self)
            dlg.exec()
        else:
            # 非文本文件，直接下载
            self._download_path(path, name)

    def _selected_path(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None, None, False
        row = rows[0].row()
        name = self.table.item(row, 0).text().strip("📁📄 ").strip()
        is_dir = self.table.item(row, 3).text() == "目录"
        path = self.current_path.rstrip("/") + "/" + name
        return path, name, is_dir

    def _upload(self):
        local, _ = QFileDialog.getOpenFileName(self, "选择文件上传")
        if not local:
            return
        import os
        name = os.path.basename(local)
        remote = self.current_path.rstrip("/") + "/" + name
        self.status_msg(f"正在上传 {name}...")
        telemetry.track("上传文件")
        self.transfer_file("upload", local, remote,
                           on_done=lambda ok, msg, n=name: self._on_transfer_done("上传", n, ok, msg),
                           on_progress=self._on_transfer_progress)

    def _download(self):
        path, name, is_dir = self._selected_path()
        if not path:
            QMessageBox.information(self, "提示", "请先选择文件")
            return
        if is_dir:
            QMessageBox.information(self, "提示", "暂不支持下载文件夹")
            return
        telemetry.track("下载文件")
        self._download_path(path, name)

    def _download_path(self, remote: str, name: str):
        local, _ = QFileDialog.getSaveFileName(self, "保存到", name)
        if not local:
            return
        self.status_msg(f"正在下载 {name}...")
        self.transfer_file("download", local, remote,
                           on_done=lambda ok, msg, n=name, p=local: self._on_transfer_done("下载", n, ok, msg, p),
                           on_progress=self._on_transfer_progress)

    def _on_transfer_progress(self, transferred, total):
        if total > 0:
            pct = transferred * 100 // total
            self.status_msg(f"传输中... {pct}%")

    def _on_transfer_done(self, action: str, name: str, ok: bool, msg: str, local: str = ""):
        if ok:
            self.status_msg(f"{action}完成: {name}")
            if action == "上传":
                self.refresh()
        else:
            QMessageBox.critical(self, "失败", f"{action}失败: {msg}")

    def _delete(self):
        path, name, is_dir = self._selected_path()
        if not path:
            return
        if QMessageBox.question(self, "确认", f"删除「{name}」？") != QMessageBox.Yes:
            return
        telemetry.track("删除文件")
        if self.ssh.ssh_delete(path, is_dir):
            self.refresh()
        else:
            QMessageBox.critical(self, "失败", f"删除失败: {self.ssh.last_sftp_error}")

    def _rename(self):
        path, name, is_dir = self._selected_path()
        if not path:
            return
        telemetry.track("重命名")
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称：", text=name)
        if not ok or not new_name.strip():
            return
        new_path = self.current_path.rstrip("/") + "/" + new_name.strip()
        if self.ssh.ssh_rename(path, new_path):
            self.refresh()
        else:
            QMessageBox.critical(self, "失败", f"重命名失败: {self.ssh.last_sftp_error}")

    def _mkdir(self):
        telemetry.track("新建文件夹")
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称：")
        if not ok or not name.strip():
            return
        path = self.current_path.rstrip("/") + "/" + name.strip()
        if self.ssh.ssh_mkdir(path):
            self.refresh()
        else:
            QMessageBox.critical(self, "失败", f"创建失败: {self.ssh.last_sftp_error}")

    def _on_context_menu(self, pos):
        menu = QMenu(self)
        menu.addAction("下载", self._download)
        menu.addAction("重命名", self._rename)
        menu.addSeparator()
        menu.addAction("删除", self._delete)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    @staticmethod
    def _fmt_size(n: int) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if n < 1024:
                return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
            n /= 1024
        return f"{n:.1f} TB"
