"""
配置管理模块
- 连接配置（名称/IP/端口/用户名）存在 %APPDATA%/NasManager/connections.json
- 密码通过 keyring 写入 Windows 凭据管理器，不落地明文
"""
import json
import os
import uuid
from pathlib import Path
from typing import Optional

try:
    import keyring
    HAS_KEYRING = True
except Exception:
    HAS_KEYRING = False

APP_NAME = "NasManager"
KEYRING_SERVICE = "NasManager"
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
CONNECTIONS_FILE = CONFIG_DIR / "connections.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"


def _ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


class Connection:
    """单个 NAS 连接配置"""

    def __init__(self, name: str, host: str, port: int = 22,
                 username: str = "root", password: str = "",
                 conn_id: Optional[str] = None,
                 web_dir: str = "", node_proc: str = "",
                 health_url: str = "", web_url: str = "",
                 sites: Optional[list] = None):
        self.id = conn_id or uuid.uuid4().hex[:8]
        self.name = name
        self.host = host
        self.port = int(port)
        self.username = username
        self._password = password
        # 扫描得到的环境信息（已扫描的连接直接用，不再重复扫）
        self.web_dir = web_dir        # 主网站目录（兼容旧字段）
        self.node_proc = node_proc    # 主 node 进程匹配串
        self.health_url = health_url  # 主健康检查地址
        self.web_url = web_url        # 主对外网页地址
        # 多站点列表：每个站点 dict
        # {name, web_dir, entry, node_proc, port, health_url, web_url, startable, type}
        self.sites = sites if sites is not None else []

    def scanned(self) -> bool:
        """是否已扫描出多站点信息（旧版连接无 sites，会触发重新扫描）"""
        return bool(self.sites)

    @property
    def password(self) -> str:
        if self._password:
            return self._password
        if HAS_KEYRING:
            try:
                return keyring.get_password(KEYRING_SERVICE, self._keyring_account()) or ""
            except Exception:
                return ""
        return ""

    @password.setter
    def password(self, value: str):
        self._password = value

    def _keyring_account(self) -> str:
        return f"{self.id}"

    def save_password(self):
        """把密码写入 Windows 凭据管理器"""
        if not self._password:
            return
        if HAS_KEYRING:
            try:
                keyring.set_password(KEYRING_SERVICE, self._keyring_account(), self._password)
            except Exception:
                pass  # keyring 不可用时降级为仅内存

    def delete_password(self):
        if HAS_KEYRING:
            try:
                keyring.delete_password(KEYRING_SERVICE, self._keyring_account())
            except Exception:
                pass

    def to_dict(self, include_password: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "web_dir": self.web_dir,
            "node_proc": self.node_proc,
            "health_url": self.health_url,
            "web_url": self.web_url,
            "sites": self.sites,
        }
        if include_password:
            d["password"] = self.password
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Connection":
        return cls(
            name=d.get("name", ""),
            host=d.get("host", ""),
            port=d.get("port", 22),
            username=d.get("username", "root"),
            password=d.get("password", ""),
            conn_id=d.get("id"),
            web_dir=d.get("web_dir", ""),
            node_proc=d.get("node_proc", ""),
            health_url=d.get("health_url", ""),
            web_url=d.get("web_url", ""),
            sites=d.get("sites") or [],
        )

    def __repr__(self):
        return f"<Connection {self.name}@{self.host}:{self.port}>"


class ConfigManager:
    """连接配置的增删改查"""

    def __init__(self):
        _ensure_dir()
        self.connections: list[Connection] = []
        self.load()

    def load(self):
        if CONNECTIONS_FILE.exists():
            try:
                data = json.loads(CONNECTIONS_FILE.read_text(encoding="utf-8"))
                self.connections = [Connection.from_dict(d) for d in data.get("connections", [])]
            except Exception:
                self.connections = []
        else:
            self.connections = []

    def save(self):
        _ensure_dir()
        data = {"connections": [c.to_dict() for c in self.connections]}
        CONNECTIONS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def add(self, conn: Connection):
        conn.save_password()
        self.connections.append(conn)
        self.save()

    def update(self, conn: Connection):
        for i, c in enumerate(self.connections):
            if c.id == conn.id:
                conn.save_password()
                self.connections[i] = conn
                break
        self.save()

    def delete(self, conn_id: str):
        for c in self.connections:
            if c.id == conn_id:
                c.delete_password()
        self.connections = [c for c in self.connections if c.id != conn_id]
        self.save()

    def get(self, conn_id: str) -> Optional[Connection]:
        for c in self.connections:
            if c.id == conn_id:
                return c
        return None


class Settings:
    """通用设置（窗口大小、刷新间隔等）"""

    DEFAULTS = {
        "dashboard_interval": 5,
        "log_lines": 200,
        "web_url": "",
        "update_base_url": "https://dudua.synology.me:8443/download/NasManager/",
    }

    def __init__(self):
        _ensure_dir()
        self.data = dict(self.DEFAULTS)
        if SETTINGS_FILE.exists():
            try:
                self.data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except Exception:
                pass

    def get(self, key, default=None):
        return self.data.get(key, default if default is not None else self.DEFAULTS.get(key))

    def set(self, key, value):
        self.data[key] = value
        _ensure_dir()
        SETTINGS_FILE.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
