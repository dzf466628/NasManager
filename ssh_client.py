"""
SSH 连接封装
- paramiko 长连接，断线自动重连
- run_command 统一返回 (stdout, stderr, exit_code)
- run_detached 专用于 nohup ... & 后台命令，不挂住 channel
- sudo 密码会话内缓存
- 提供 SFTP、实时日志 channel，全部线程安全
"""
import time
import datetime
import threading
from typing import Optional, Callable

import paramiko


class CommandResult:
    __slots__ = ("stdout", "stderr", "exit_code")

    def __init__(self, stdout: str, stderr: str, exit_code: int):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def __repr__(self):
        return f"<CommandResult exit={self.exit_code}>"


class SSHClient:
    def __init__(self, host: str, port: int, username: str, password: str,
                 on_log: Optional[Callable[[str], None]] = None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.on_log = on_log or (lambda msg: None)

        self._client: Optional[paramiko.SSHClient] = None
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._sudo_pwd: Optional[str] = None
        self._cmd_lock = threading.RLock()  # 可重入：ssh_write_file 持锁后调 _ensure_connected 不会死锁
        self._sftp_lock = threading.Lock()
        self._connected = False
        self.last_sftp_error: str = ""  # 最近一次 SFTP 错误，供 UI 展示

    def log(self, msg: str):
        try:
            self.on_log(msg)
        except Exception:
            pass

    # ---------- 连接管理 ----------

    def connect(self, timeout: int = 10) -> bool:
        with self._cmd_lock:
            return self._do_connect(timeout)

    def _do_connect(self, timeout: int = 10) -> bool:
        self._close_internal()
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=timeout,
                banner_timeout=timeout,
                auth_timeout=timeout,
                allow_agent=False,
                look_for_keys=False,
            )
            self._client = client
            self._connected = True
            self.log(f"已连接 {self.username}@{self.host}:{self.port}")
            return True
        except Exception as e:
            self._connected = False
            self.log(f"连接失败: {e}")
            return False

    def _ensure_connected(self) -> bool:
        """调用方必须已持有 _cmd_lock"""
        if self._client is not None and self._connected:
            transport = self._client.get_transport()
            if transport is not None and transport.is_active():
                return True
        self.log("连接已断开，尝试重连...")
        return self._do_connect()

    def disconnect(self):
        with self._cmd_lock:
            self._close_internal()

    def _close_internal(self):
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        if self._client is None:
            return False
        t = self._client.get_transport()
        return t is not None and t.is_active()

    # ---------- 普通命令 ----------

    def run_command(self, cmd: str, timeout: int = 15, sudo: bool = False) -> CommandResult:
        """
        执行命令并等待完成，带总超时保护，绝不死锁。
        用 channel + 轮询，命令退出后把剩余输出读干净；超过 timeout 强制返回。
        """
        with self._cmd_lock:
            if not self._ensure_connected():
                return CommandResult("", "SSH 未连接", -1)
            chan = None
            try:
                if sudo:
                    cmd = self._wrap_sudo(cmd)
                chan = self._client.get_transport().open_session()
                chan.settimeout(5)
                # sudo 用 -S 从 stdin 读密码，不需要 pty，避免 pty 下密码时序卡死
                chan.exec_command(cmd)
                if sudo and self._sudo_pwd is not None:
                    try:
                        chan.sendall(self._sudo_pwd + "\n")
                    except Exception:
                        pass

                out_buf = []
                err_buf = []
                deadline = time.time() + timeout
                exited = False
                while time.time() < deadline:
                    if chan.recv_ready():
                        out_buf.append(chan.recv(65536).decode("utf-8", errors="replace"))
                    if chan.recv_stderr_ready():
                        err_buf.append(chan.recv_stderr(65536).decode("utf-8", errors="replace"))
                    if chan.exit_status_ready():
                        exited = True
                        break
                    time.sleep(0.02)

                if exited:
                    # 命令已退出：读到 EOF 为止，绝不丢尾部数据
                    while True:
                        try:
                            d = chan.recv(65536)
                            if not d:
                                break
                            out_buf.append(d.decode("utf-8", errors="replace"))
                        except Exception:
                            break
                    while True:
                        try:
                            d = chan.recv_stderr(65536)
                            if not d:
                                break
                            err_buf.append(d.decode("utf-8", errors="replace"))
                        except Exception:
                            break

                out = "".join(out_buf)
                err = "".join(err_buf)
                code = chan.recv_exit_status() if exited else -2  # -2 表示超时
                if code == -2:
                    out += "\n[命令超时]"
                if sudo and "password" in err.lower():
                    err = ""
                return CommandResult(out, err, code)
            except Exception as e:
                self._connected = False
                return CommandResult("", str(e), -1)
            finally:
                if chan is not None:
                    try:
                        chan.close()
                    except Exception:
                        pass

    def _wrap_sudo(self, cmd: str) -> str:
        return f"sudo -S -p '' {cmd}"

    def set_sudo_password(self, pwd: str):
        self._sudo_pwd = pwd

    def sudo(self, cmd: str, timeout: int = 15) -> CommandResult:
        return self.run_command(cmd, timeout=timeout, sudo=True)

    # ---------- 后台命令（nohup ... &） ----------

    def run_detached(self, cmd: str, wait: float = 2.0, sudo: bool = False) -> CommandResult:
        """
        执行后台命令（如 nohup node server.js > log 2>&1 &）。
        用 sh -c 包装，正确处理带 & 的命令；不等待进程结束，wait 秒后关闭 channel。
        支持 sudo：密码从 stdin 输入。
        """
        with self._cmd_lock:
            if not self._ensure_connected():
                return CommandResult("", "SSH 未连接", -1)
            chan = None
            try:
                # 用 sh -c 包装：命令里即使有 & / && / > 等也不会被破坏
                safe = cmd.replace("'", "'\\''")
                full = f"sh -c '{safe}'"
                # 非 sudo 时重定向 stdin 防止 channel 挂住；sudo 时密码要走 stdin，不能重定向
                if not sudo:
                    full += " < /dev/null"
                if sudo:
                    full = self._wrap_sudo(full)

                chan = self._client.get_transport().open_session()
                chan.settimeout(wait + 5)
                chan.exec_command(full)
                if sudo and self._sudo_pwd is not None:
                    try:
                        chan.sendall(self._sudo_pwd + "\n")
                    except Exception:
                        pass
                # 等待进程启动
                time.sleep(wait)
                err = ""
                try:
                    while chan.recv_stderr_ready():
                        err += chan.recv_stderr(4096).decode("utf-8", errors="replace")
                except Exception:
                    pass
                return CommandResult("", err, 0)
            except Exception as e:
                return CommandResult("", str(e), -1)
            finally:
                if chan is not None:
                    try:
                        chan.close()
                    except Exception:
                        pass

    # ---------- 实时日志 ----------

    def open_channel(self, cmd: str, sudo: bool = False):
        """打开一个 channel 用于实时读取输出（如 tail -f）。调用方负责关闭。"""
        with self._cmd_lock:
            if not self._ensure_connected():
                return None
            try:
                transport = self._client.get_transport()
                channel = transport.open_session()
                # sudo -S 从 stdin 读密码，pty 会干扰行编辑导致密码发不进去；
                # 非 sudo 命令（如 tail -f）保留 pty 以维持原有行为
                if not sudo:
                    channel.get_pty()
                if sudo:
                    cmd = self._wrap_sudo(cmd)
                channel.exec_command(cmd)
                if sudo and self._sudo_pwd is not None:
                    time.sleep(0.3)
                    try:
                        channel.sendall(self._sudo_pwd + "\n")
                    except Exception:
                        pass
                return channel
            except Exception as e:
                self.log(f"打开 channel 失败: {e}")
                return None

    # ---------- SFTP ----------

    def _get_sftp(self):
        """调用方必须持有 _cmd_lock（通过 run_command 路径）或 _sftp_lock"""
        if self._sftp is not None:
            return self._sftp
        if not self._ensure_connected():
            self.last_sftp_error = "SSH 连接不可用"
            return None
        try:
            self._sftp = self._client.open_sftp()
            self.last_sftp_error = ""
            return self._sftp
        except Exception as e:
            self.last_sftp_error = f"SFTP 打开失败: {e}"
            self.log(f"SFTP 打开失败: {e}")
            return None

    def sftp_listdir(self, path: str) -> list:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    # 保留 _get_sftp 里的具体错误；没有才给通用提示
                    if not self.last_sftp_error:
                        self.last_sftp_error = "SFTP 不可用（可能 NAS 未启用 SFTP，或账号无访问权限）"
                    return []
                self.last_sftp_error = ""
                import stat
                result = []
                try:
                    for entry in sftp.listdir_attr(path):
                        is_dir = stat.S_ISDIR(entry.st_mode)
                        result.append({
                            "name": entry.filename,
                            "is_dir": is_dir,
                            "size": entry.st_size,
                            "mtime": entry.st_mtime,
                            "mode": entry.st_mode,
                        })
                except Exception as e:
                    self.last_sftp_error = f"列目录失败 {path}: {e}"
                    self.log(f"列目录失败 {path}: {e}")
                result.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
                return result

    def sftp_read_file(self, path: str) -> Optional[str]:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return None
                try:
                    with sftp.open(path, "r") as f:
                        return f.read().decode("utf-8", errors="replace")
                except Exception as e:
                    self.log(f"读取文件失败 {path}: {e}")
                    return None

    def sftp_write_file(self, path: str, content: str) -> bool:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return False
                try:
                    with sftp.open(path, "w") as f:
                        f.write(content)
                    return True
                except Exception as e:
                    self.log(f"写入文件失败 {path}: {e}")
                    return False

    def sftp_mkdir(self, path: str) -> bool:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return False
                try:
                    sftp.mkdir(path)
                    return True
                except Exception as e:
                    self.log(f"创建目录失败 {path}: {e}")
                    return False

    def sftp_delete(self, path: str, is_dir: bool = False) -> bool:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return False
                try:
                    if is_dir:
                        sftp.rmdir(path)
                    else:
                        sftp.remove(path)
                    return True
                except Exception as e:
                    self.log(f"删除失败 {path}: {e}")
                    return False

    def sftp_rename(self, old: str, new: str) -> bool:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return False
                try:
                    sftp.rename(old, new)
                    return True
                except Exception as e:
                    self.log(f"重命名失败: {e}")
                    return False

    def sftp_download(self, remote: str, local: str, callback=None) -> bool:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return False
                try:
                    sftp.get(remote, local, callback=callback)
                    return True
                except Exception as e:
                    self.log(f"下载失败 {remote}: {e}")
                    return False

    def sftp_upload(self, local: str, remote: str, callback=None) -> bool:
        with self._sftp_lock:
            with self._cmd_lock:
                sftp = self._get_sftp()
                if sftp is None:
                    return False
                try:
                    sftp.put(local, remote, callback=callback)
                    return True
                except Exception as e:
                    self.log(f"上传失败 {local}: {e}")
                    return False

    # ============ SSH 命令版文件操作（不依赖 SFTP，通用可靠） ============

    @staticmethod
    def _shq(s: str) -> str:
        """shell 单引号转义"""
        return "'" + s.replace("'", "'\\''") + "'"

    def ssh_listdir(self, path: str) -> list:
        """用 ls 列目录，返回 [{'name','is_dir','size','mtime'}]"""
        entries = []
        cmd = f"ls -la --time-style=long-iso --color=never {self._shq(path)}"
        r = self.run_command(cmd, timeout=15)
        out = (r.stdout or "").strip()
        if not out:
            if r.stderr and "No such file" in r.stderr:
                self.last_sftp_error = f"目录不存在: {path}"
            elif r.stderr:
                self.last_sftp_error = r.stderr.strip()
            return []
        for line in out.splitlines():
            line = line.rstrip()
            if not line or line.startswith("total "):
                continue
            parts = line.split(None, 8)
            if len(parts) < 8:
                continue
            mode = parts[0]
            name = parts[7] if len(parts) == 8 else parts[8]
            if name in (".", ".."):
                continue
            is_dir = mode.startswith("d")
            is_link = mode.startswith("l")
            try:
                size = int(parts[4])
            except ValueError:
                size = 0
            try:
                mtime = datetime.datetime.strptime(f"{parts[5]} {parts[6]}", "%Y-%m-%d %H:%M").timestamp()
            except Exception:
                mtime = 0
            entries.append({"name": name, "is_dir": is_dir, "is_link": is_link,
                            "size": size, "mtime": mtime})
        return entries

    def ssh_read_file(self, path: str):
        """读取文本文件内容；失败返回 None"""
        r = self.run_command(f"cat {self._shq(path)}", timeout=30)
        if r.exit_code != 0 and not r.stdout:
            self.last_sftp_error = r.stderr.strip() or "读取失败"
            return None
        return r.stdout or ""

    def ssh_write_file(self, path: str, content: str) -> bool:
        """用 base64 写文本文件"""
        import base64 as _b64
        data = _b64.b64encode(content.encode("utf-8")).decode("ascii")
        return self._ssh_write_b64(path, data)

    def ssh_write_bytes(self, path: str, data: bytes) -> bool:
        import base64 as _b64
        data_b64 = _b64.b64encode(data).decode("ascii")
        return self._ssh_write_b64(path, data_b64)

    def _ssh_write_b64(self, path: str, b64_data: str) -> bool:
        with self._cmd_lock:
            try:
                if not self._ensure_connected():
                    return False
                cmd = f"base64 -d > {self._shq(path)}"
                stdin, stdout, stderr = self._client.exec_command(cmd, timeout=60)
                try:
                    # 分块写，避免一次写入过大
                    for i in range(0, len(b64_data), 65536):
                        stdin.write(b64_data[i:i + 65536])
                except Exception:
                    pass
                try:
                    stdin.channel.shutdown_write()
                except Exception:
                    pass
                exit_code = stdout.channel.recv_exit_status()
                err = stderr.read().decode("utf-8", "replace") if stderr.channel.recv_ready() else ""
                if exit_code != 0:
                    self.last_sftp_error = err.strip() or "写入失败"
                    self.log(f"写入失败 {path}: {err.strip()}")
                    return False
                return True
            except Exception as e:
                self.last_sftp_error = f"写入异常: {e}"
                return False
    def ssh_read_bytes(self, path: str):
        """读取二进制文件内容（base64 解码）；失败返回 None"""
        try:
            if not self._ensure_connected():
                return None
            r = self.run_command(f"base64 {self._shq(path)}", timeout=60)
            if r.exit_code != 0 or not r.stdout:
                self.last_sftp_error = r.stderr.strip() or "读取失败"
                return None
            import base64 as _b64
            return _b64.b64decode(r.stdout)
        except Exception as e:
            self.last_sftp_error = f"读取异常: {e}"
            return None

    def ssh_delete(self, path: str, is_dir: bool) -> bool:
        cmd = f"rm -rf {self._shq(path)}" if is_dir else f"rm -f {self._shq(path)}"
        r = self.run_command(cmd, timeout=15)
        if r.exit_code != 0:
            self.last_sftp_error = r.stderr.strip() or "删除失败"
            return False
        return True

    def ssh_rename(self, old: str, new: str) -> bool:
        r = self.run_command(f"mv {self._shq(old)} {self._shq(new)}", timeout=15)
        if r.exit_code != 0:
            self.last_sftp_error = r.stderr.strip() or "重命名失败"
            return False
        return True

    def ssh_mkdir(self, path: str) -> bool:
        r = self.run_command(f"mkdir -p {self._shq(path)}", timeout=15)
        if r.exit_code != 0:
            self.last_sftp_error = r.stderr.strip() or "创建失败"
            return False
        return True

    def ssh_download(self, remote: str, local: str) -> bool:
        data = self.ssh_read_bytes(remote)
        if data is None:
            return False
        try:
            with open(local, "wb") as f:
                f.write(data)
            return True
        except Exception as e:
            self.last_sftp_error = f"本地写入失败: {e}"
            return False

    def ssh_upload(self, local: str, remote: str) -> bool:
        try:
            with open(local, "rb") as f:
                data = f.read()
            return self.ssh_write_bytes(remote, data)
        except Exception as e:
            self.last_sftp_error = f"本地读取失败: {e}"
            return False
