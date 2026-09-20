"""
Widget 基类与通用 Worker
- BaseWidget：所有功能页基类，提供 ssh 绑定、后台命令、文件传输
- CommandWorker：后台执行单条 SSH 命令（带异常兜底，绝不闪退）
- DetachedWorker：后台执行 nohup 类命令
- FileTransferWorker：后台上传/下载文件，带进度信号
"""
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QWidget, QMessageBox

from ssh_client import SSHClient, CommandResult


class CommandWorker(QThread):
    """后台执行单条 SSH 命令，任何异常都转成 CommandResult 返回"""
    finished_result = Signal(object)

    def __init__(self, ssh: SSHClient, cmd: str, timeout: int = 15, sudo: bool = False):
        super().__init__()
        self.ssh = ssh
        self.cmd = cmd
        self.timeout = timeout
        self.sudo = sudo

    def run(self):
        try:
            r = self.ssh.run_command(self.cmd, timeout=self.timeout, sudo=self.sudo)
        except Exception as e:
            r = CommandResult("", f"线程异常: {e}", -1)
        self.finished_result.emit(r)


class DetachedWorker(QThread):
    """后台执行 nohup ... & 类命令（支持 sudo）"""
    finished_result = Signal(object)

    def __init__(self, ssh: SSHClient, cmd: str, wait: float = 2.0, sudo: bool = False):
        super().__init__()
        self.ssh = ssh
        self.cmd = cmd
        self.wait = wait
        self.sudo = sudo

    def run(self):
        try:
            r = self.ssh.run_detached(self.cmd, wait=self.wait, sudo=self.sudo)
        except Exception as e:
            r = CommandResult("", str(e), -1)
        self.finished_result.emit(r)


class FileTransferWorker(QThread):
    """后台上传/下载文件"""
    progress = Signal(int, int)  # transferred, total
    finished_ok = Signal(bool, str)

    def __init__(self, ssh: SSHClient, direction: str, local: str, remote: str):
        super().__init__()
        self.ssh = ssh
        self.direction = direction  # "upload" or "download"
        self.local = local
        self.remote = remote

    def run(self):
        try:
            import os
            total = os.path.getsize(self.local) if self.direction == "upload" else 0

            if self.direction == "upload":
                ok = self.ssh.ssh_upload(self.local, self.remote)
            else:
                ok = self.ssh.ssh_download(self.remote, self.local)
            if ok:
                self.progress.emit(total, total)
            self.finished_ok.emit(ok, "" if ok else (self.ssh.last_sftp_error or "传输失败"))
        except Exception as e:
            self.finished_ok.emit(False, str(e))


class BaseWidget(QWidget):
    """功能页基类"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ssh: SSHClient | None = None
        self._workers: list[QThread] = []
        # 环境体检结果（连接后动态探测，供各页面取真实环境，避免硬编码）
        self.probe: dict = {}

    def set_probe_info(self, probe: dict):
        """收到环境体检结果（连接后自动下发），子类可覆盖使用"""
        self.probe = probe or {}
        self.on_probe()

    def on_probe(self):
        """环境体检结果到达后回调（子类可覆盖刷新）"""
        pass

    def bind_ssh(self, ssh: SSHClient):
        self.ssh = ssh
        self.on_bind()

    def on_bind(self):
        pass

    def on_show(self):
        """页面切换显示时调用"""
        pass

    def on_hide(self):
        """页面切换离开时调用——停掉定时器等后台任务"""
        pass

    def stop_workers(self, wait_ms: int = 2000):
        """停止所有后台 worker 线程，关窗时调用，防止进程退不出"""
        for w in list(self._workers):
            try:
                w.requestInterruption()
                if w.isRunning():
                    w.wait(wait_ms)
                if w.isRunning():
                    w.terminate()
                    w.wait(500)
            except Exception:
                pass
        self._workers.clear()

    def _track(self, worker: QThread):
        self._workers.append(worker)
        worker.finished.connect(lambda: self._cleanup(worker))
        return worker

    def _cleanup(self, worker):
        if worker in self._workers:
            self._workers.remove(worker)
        worker.deleteLater()

    def run_cmd(self, cmd: str, on_result, timeout: int = 15, sudo: bool = False):
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.warning(self, "提示", "SSH 未连接")
            return
        w = CommandWorker(self.ssh, cmd, timeout, sudo)
        w.finished_result.connect(on_result)
        self._track(w)
        w.start()

    def run_detached_cmd(self, cmd: str, on_result, wait: float = 2.0, sudo: bool = False):
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.warning(self, "提示", "SSH 未连接")
            return
        w = DetachedWorker(self.ssh, cmd, wait, sudo)
        w.finished_result.connect(on_result)
        self._track(w)
        w.start()

    def transfer_file(self, direction: str, local: str, remote: str, on_done, on_progress=None):
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.warning(self, "提示", "SSH 未连接")
            return
        w = FileTransferWorker(self.ssh, direction, local, remote)
        w.finished_ok.connect(on_done)
        if on_progress:
            w.progress.connect(on_progress)
        self._track(w)
        w.start()

    def need_sudo(self) -> bool:
        return self.ssh is not None and self.ssh._sudo_pwd is not None

    def status_msg(self, msg: str):
        sb = self.window().statusBar()
        if sb:
            sb.showMessage(msg)
