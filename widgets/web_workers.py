"""Web 面板后台线程。

线程接口与旧 web_panel.py 中的实现保持一致；WebPanelWidget 通过模块末尾
兼容导入使用这些类，避免改变现有信号和调用方。
"""
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from PySide6.QtCore import QThread, Signal

from widgets.web_helpers import PATH_EXPORT


class LogReaderThread(QThread):
    """通过 SSH channel 实时读取日志（tail -f）。"""
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
    """通过 SSH channel 执行命令并实时流式输出。"""
    line_received = Signal(str)
    finished_cmd = Signal(int)
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


class InstallDepsThread(QThread):
    """后台跑 npm install（可指定包名单独重装）。"""
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
            base = "export HOME=/tmp; " + self.path_export
            if self.pkgs:
                pk = " ".join(p for p in self.pkgs if p)
                cmd = base + f"cd {shq} && npm install {pk} --no-audit --no-fund 2>&1 | tail -20"
            else:
                cmd = base + f"cd {shq} && npm install --no-audit --no-fund 2>&1 | tail -20"
            r = self.ssh.run_command(cmd, timeout=300)
            out = (r.stdout + r.stderr).strip()
            ok = r.exit_code == 0 or "added" in out.lower() or "up to date" in out.lower()
            self.done.emit(ok, out[-600:])
        except Exception as e:
            self.done.emit(False, str(e))


class UrlProbeThread(QThread):
    """从本机并发实测一批地址的连通性。"""
    one_result = Signal(int, str, str)

    def __init__(self, urls, parent=None):
        super().__init__(parent)
        self.urls = list(urls)
        self._stop = False

    def stop(self):
        self._stop = True

    @staticmethod
    def _cert_status(host: str, port: int, timeout: float = 4.0):
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
        cert = None
        if url.startswith("https://"):
            try:
                parsed = urllib.parse.urlsplit(url)
                if parsed.hostname:
                    cert = UrlProbeThread._cert_status(parsed.hostname, parsed.port or 443)
            except Exception:
                cert = None
        ctx = ssl._create_unverified_context()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "NasManager"})
            with urllib.request.urlopen(req, timeout=4, context=ctx) as resp:
                code = getattr(resp, "status", 0) or 200
                if not (200 <= code < 400):
                    return "http", f"HTTP {code}"
                if cert is False:
                    return "cert", "证书异常"
                return "ok", "可访问"
        except urllib.error.HTTPError as e:
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
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.probe_one, url): i for i, url in enumerate(self.urls)}
            for future in as_completed(futures):
                if self._stop:
                    break
                idx = futures[future]
                try:
                    state, detail = future.result()
                except Exception:
                    state, detail = "bad", "失败"
                self.one_result.emit(idx, state, detail)


class RescanThread(QThread):
    """后台重新扫描 NAS 网站环境。"""
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


# 保留旧类名，方便现有导入方使用。
_RescanThread = RescanThread
