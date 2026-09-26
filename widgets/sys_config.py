"""
系统配置一览
- 自动抓取 NAS 环境信息，汇总到单个文本控件
- 顶部"刷新数据"重新抓取；"复制全部"一键复制整个配置
- 用途：把环境配置完整交给 AI，写 node/nginx 网页时不至于瞎猜
"""
import json
import re
from PySide6.QtCore import QTime
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
    QMessageBox, QApplication, QFrame,
)

import telemetry
from widgets.base import BaseWidget
from app_paths import resource_path
from widgets.sys_config_helpers import shq as _helper_shq, main_site_info as _helper_main_site_info

PRESETS_FILE = resource_path("services") / "presets.json"

CFG_STYLE = """
QPushButton#CfgBtn {
    background:#f0f2f5; border:none; border-radius:5px; padding:5px 14px;
    font-size:12px; color:#333;
}
QPushButton#CfgBtn:hover { background:#e0e3ea; }
QPushButton#CfgPrimary {
    background:#4caf50; border:none; border-radius:5px; padding:6px 16px;
    font-size:12px; color:#fff;
}
QPushButton#CfgPrimary:hover { background:#43a047; }
QPlainTextEdit#CfgText {
    background:#f8f9fb; color:#24292f; font-family:Consolas,'Courier New',monospace;
    font-size:12px; border:1px solid #e0e0e0; border-radius:6px;
}
QFrame#CapCard {
    background:#ffffff; border:1px solid #e6e8ef; border-radius:8px;
}
QFrame#CapCard QPlainTextEdit {
    background:#f8f9fb; color:#24292f; font-family:Consolas,'Courier New',monospace;
    font-size:12px; border:1px solid #e0e0e0; border-radius:6px;
}
"""


class SysConfigWidget(BaseWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.presets = self._load_presets()
        self.conn = None
        self._build_ui()

    def _load_presets(self) -> dict:
        try:
            return json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set_conn_info(self, conn):
        self.conn = conn
        self._prefill_conn()

    def _build_ui(self):
        self.setStyleSheet(CFG_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(8)

        # 标题 + 操作
        head = QHBoxLayout()
        title = QLabel("系统配置一览")
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#1a1a2e;")
        head.addWidget(title)
        head.addStretch()
        self.copy_btn = QPushButton("复制全部")
        self.copy_btn.setObjectName("CfgPrimary")
        self.copy_btn.clicked.connect(self._copy_all)
        head.addWidget(self.copy_btn)
        self.refresh_btn = QPushButton("刷新数据")
        self.refresh_btn.setObjectName("CfgBtn")
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        outer.addLayout(head)

        tip = QLabel("点「复制全部」把整套环境配置交给 AI，写网页 / 配 node / nginx 直接参考，不用再猜环境。")
        tip.setStyleSheet("font-size:12px; color:#888;")
        outer.addWidget(tip)

        # ---------- 网站能力评估容器（供 AI 写网站参考，动态根据体检结果生成） ----------
        cap_card = QFrame()
        cap_card.setObjectName("CapCard")
        cap_lay = QVBoxLayout(cap_card)
        cap_lay.setContentsMargins(12, 10, 12, 10)
        cap_lay.setSpacing(6)
        cap_head = QHBoxLayout()
        cap_title = QLabel("网站能力评估（供 AI 写网站参考）")
        cap_title.setStyleSheet("font-size:14px; font-weight:bold; color:#1a1a2e;")
        cap_head.addWidget(cap_title)
        cap_head.addStretch()
        self.cap_copy_btn = QPushButton("复制评估")
        self.cap_copy_btn.setObjectName("CfgPrimary")
        self.cap_copy_btn.clicked.connect(self._copy_capability)
        cap_head.addWidget(self.cap_copy_btn)
        cap_lay.addLayout(cap_head)
        self.cap_text = QPlainTextEdit()
        self.cap_text.setObjectName("CfgText")
        self.cap_text.setReadOnly(True)
        self.cap_text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.cap_text.setFixedHeight(230)
        self.cap_text.setPlaceholderText("环境体检完成后自动生成本 NAS 能胜任的网站形式...")
        cap_lay.addWidget(self.cap_text)
        outer.addWidget(cap_card)

        # 单个大文本框（所有配置放一起）
        self.text = QPlainTextEdit()
        self.text.setObjectName("CfgText")
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setPlaceholderText("点击「刷新数据」抓取 NAS 环境配置...")
        outer.addWidget(self.text, 1)

        # 状态标签
        self.state_lbl = QLabel("")
        self.state_lbl.setStyleSheet("font-size:11px; color:#888;")
        outer.addWidget(self.state_lbl)

        self._prefill_conn()

    def _prefill_conn(self):
        """先填连接信息，保证页面不空"""
        if not self.conn:
            return
        lines = [
            "===== NAS 系统配置一览（供 AI 参考） =====",
            "",
            "【连接信息】",
            f"名称: {self.conn.name}",
            f"地址: {self.conn.host}:{self.conn.port}",
            f"用户名: {self.conn.username}",
        ]
        # 已保存的网站列表（不刷新也显示）
        if self.conn.sites:
            lines += ["", "【网站列表】"]
            for i, s in enumerate(self.conn.sites, 1):
                lines.append(f"站点{i}: {s.get('name')}  ({s.get('type') or 'node'})")
                lines.append(f"  目录: {s.get('web_dir')}")
                lines.append(f"  启动: {s.get('node_proc') or s.get('entry') or '(未知)'}")
                if s.get('port'):
                    lines.append(f"  端口: {s.get('port')}")
                if s.get('health_url'):
                    lines.append(f"  健康检查: {s.get('health_url')}")
                if s.get('web_url'):
                    lines.append(f"  访问地址: {s.get('web_url')}")
        cur = self.text.toPlainText()
        if cur:
            self.text.setPlainText(cur + "\n\n" + "\n".join(lines))
        else:
            self.text.setPlainText("\n".join(lines))

    # ---------- 数据抓取 ----------

    def _main_site_info(self):
        """从连接配置动态取主网站目录/启动命令。"""
        return _helper_main_site_info(self.conn)

    def on_probe(self):
        """环境体检完成后自动刷新，显示动态发现的 nginx 配置等"""
        try:
            if self.ssh and self.ssh.is_connected:
                self.refresh()
                self._update_capability()
        except Exception:
            pass

    def on_show(self):
        # 进入页面自动刷新
        if self.ssh and self.ssh.is_connected:
            self.refresh()
            self._update_capability()

    def refresh(self):
        if not self.ssh or not self.ssh.is_connected:
            QMessageBox.warning(self, "提示", "SSH 未连接")
            return
        self.state_lbl.setText("正在抓取环境信息...")
        self._blocks = {}

        # 从连接配置动态读主网站（已扫描/已配置则用，否则留空交给抓取）
        web_dir, node_proc = self._main_site_info()

        # nginx 实际主配置文件（环境体检动态发现，群晖是 nginx.conf.run）
        nginx_conf = self.probe.get("nginx_conf") or "/etc/nginx/nginx.conf"
        # 反代配置全目录动态搜索（覆盖 Web Station 全部配置位置）
        proxy_dirs = ("/etc/nginx/conf.d/ /etc/nginx/sites-enabled/ "
                      "/usr/local/etc/nginx/conf.d/ /usr/local/etc/nginx/sites-enabled/ "
                      "/usr/local/etc/nginx/sites-available/")

        # 站点运行状态快照：进程/端口/反代文件/最近错误，快速排查与发 AI
        site_parts = []
        if self.conn and self.conn.sites:
            for s in self.conn.sites:
                wd = (s.get("web_dir") or "").strip()
                port = (s.get("port") or "").strip()
                nm = (s.get("name") or "?")
                if not wd:
                    continue
                pid_f = f"{wd}/.server.pid"
                p1 = (f"echo '站点[{nm}] 目录:{wd} 端口:{port or '-'}'; "
                      f"if [ -f {pid_f} ]; then p=$(cat {pid_f}); "
                      f"if kill -0 $p 2>/dev/null; then echo '  进程: 运行中 pid='$p; "
                      f"else echo '  进程: 已停止(PID失效)'; fi; "
                      f"else echo '  进程: 未启动(无PID文件)'; fi; ")
                p2 = (f"echo '  node端口{port}: '$(curl -s -o /dev/null -w '%{{http_code}}' "
                      f"--max-time 3 http://127.0.0.1:{port}/ 2>/dev/null); " if port else "")
                site_parts.append(p1 + p2)
        # 每段自带分号结尾，join 用空格分隔，避免产生 `; ;` 语法错误
        site_cmd = " ".join(site_parts) if site_parts else "echo 无站点"

        # 拆成多条独立短命令，某条失败不影响其他，且能看到失败原因
        probes = {
            "system": "uname -a 2>/dev/null; echo '---'; cat /etc/synoinfo.conf 2>/dev/null | grep -Ei 'unique|upnpmodelname|version|modelname' | head -4",
            "node": "echo -n 'node路径: '; command -v node 2>/dev/null; echo -n 'node版本: '; (export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin; node -v 2>/dev/null); echo -n 'node进程: '; ps -ef | grep -F '" + node_proc + "' | grep -v grep | head -1",
            "npm": "export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin; echo -n 'npm路径: '; command -v npm 2>/dev/null; echo -n 'npm版本: '; npm -v 2>/dev/null",
            "curl": "echo -n 'curl: '; command -v curl 2>/dev/null || echo 未安装",
            "nginx": "nginx -v 2>&1 | head -1; echo '配置主文件: " + nginx_conf + "'; ps -ef | grep -w nginx | grep -v grep | head -3",
            "proxy": "grep -rn proxy_pass " + proxy_dirs + " 2>/dev/null | head -30",
            "status": (
                "echo '=== 站点运行状态 ==='; " + site_cmd + " "
                "echo; echo '=== server.log 大小(日志膨胀检测) ==='; "
                + "".join(
                    f"f={self._shq(r)}/server.log; [ -f $f ] && echo \"$f: $(du -h $f 2>/dev/null | cut -f1)\"; "
                    for r in (self.probe.get("web_roots") or ["/volume1/web"]))
                + "echo; echo '=== 反代扩展点文件(大小 名称) ==='; "
                "ls -la /etc/nginx/conf.d/ 2>/dev/null | grep -E '\\.service\\..*\\.conf\\.|\\.location\\.webstation\\.conf\\.' "
                "| awk '{print $5, $NF}' | head -15; "
                "echo; echo '=== 最近 nginx 错误(Web Station) ==='; "
                "tail -4 /var/packages/WebStation/var/log/nginx_error_log 2>/dev/null"
            ),
            "dir": f"ls -la {self._shq(web_dir)} 2>/dev/null",
        }
        for key, cmd in probes.items():
            self.run_cmd(cmd, lambda r, k=key: self._on_block(k, r), timeout=20)

    @staticmethod
    def _shq(s: str) -> str:
        return _helper_shq(s)

    def _on_block(self, key: str, r):
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        if out:
            self._blocks[key] = out
        elif err and "chdir" not in err:
            self._blocks[key] = f"(抓取失败: {err[:120]})"
        else:
            self._blocks[key] = "(无数据，可能需要权限)"
        self._render()

    def _render(self):
        parts = ["===== NAS 系统配置一览（供 AI 参考） =====", ""]

        # 连接信息
        if self.conn:
            parts += ["【连接信息】",
                      f"名称: {self.conn.name}",
                      f"地址: {self.conn.host}:{self.conn.port}",
                      f"用户名: {self.conn.username}",
                      ""]

        if "system" in self._blocks:
            parts += ["【系统信息】", self._blocks["system"], ""]
        if "node" in self._blocks:
            web_dir, _ = self._main_site_info()
            health = (self.conn.health_url if self.conn else "") or ""
            node_parts = [self._blocks["node"]]
            if web_dir:
                node_parts.append(f"日志文件: {web_dir}/server.log")
                node_parts.append(f"代码目录: {web_dir}")
            if health:
                node_parts.append(f"健康检查: {health}")
            parts += ["【Node.js 环境】", "\n".join(node_parts), ""]

        # 网站列表（多站点）
        if self.conn and self.conn.sites:
            parts += ["【网站列表】"]
            for i, s in enumerate(self.conn.sites, 1):
                parts.append(f"站点{i}: {s.get('name')}  ({s.get('type') or 'node'})")
                parts.append(f"  目录: {s.get('web_dir')}")
                parts.append(f"  启动: {s.get('node_proc') or s.get('entry') or '(未知)'}")
                if s.get('port'):
                    parts.append(f"  端口: {s.get('port')}")
                if s.get('health_url'):
                    parts.append(f"  健康检查: {s.get('health_url')}")
                if s.get('web_url'):
                    parts.append(f"  访问地址: {s.get('web_url')}")
            parts.append("")
        if "nginx" in self._blocks:
            parts += ["【Nginx 信息】", self._blocks["nginx"], ""]
        if "proxy" in self._blocks:
            parts += ["【反向代理列表】", self._blocks["proxy"], ""]
        # 反代挂载点（动态探测：门户 include 模式 + Host 白名单，反代/CDN 配置关键）
        loc_inc = (self.probe.get("webstation_location_include") or "").strip()
        srv_name = (self.probe.get("webstation_server_name") or "").strip()
        if loc_inc or srv_name:
            parts += ["【反代挂载点】"]
            if loc_inc:
                parts.append(f"门户 include 模式: {loc_inc}")
            if srv_name:
                parts.append(f"门户 Host 白名单: {srv_name}（CDN 回源 Host 必须用它，否则 404）")
            parts.append("")
        if "status" in self._blocks:
            parts += ["【NAS 状态快照（最近抓取）】", self._blocks["status"], ""]
        if "dir" in self._blocks:
            web_dir, _ = self._main_site_info()
            if web_dir:
                parts += [f"【网站目录 {web_dir}】", self._blocks["dir"], ""]

        self.text.setPlainText("\n".join(parts))
        self._update_capability()
        done = len(self._blocks)
        self.state_lbl.setText(f"已抓取 {done}/8 项 · {QTime.currentTime().toString('HH:mm:ss')}")

    def _svc(self, name: str) -> dict:
        for s in self.presets.get("services", []):
            if s.get("name") == name:
                return s
        return {}

    # ---------- 复制 ----------

    def _copy_all(self):
        cap = self.cap_text.toPlainText().strip()
        text = self.text.toPlainText().strip()
        if not cap and not text:
            QMessageBox.information(self, "提示", "还没有内容，请先点「刷新数据」")
            return
        telemetry.track("复制全部环境配置")
        full = cap + ("\n\n" if cap and text else "") + text
        QApplication.clipboard().setText(full)
        self.state_lbl.setText("已复制全部配置到剪贴板")

    def _copy_capability(self):
        text = self.cap_text.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "提示", "还没有评估内容，请先连接 NAS 完成环境体检")
            return
        telemetry.track("复制网站能力评估")
        QApplication.clipboard().setText(text)
        self.state_lbl.setText("已复制网站能力评估到剪贴板")

    # ---------- 网站能力评估（动态根据体检结果生成，不写死） ----------

    def _update_capability(self):
        """基于环境体检结果动态生成本 NAS 能胜任的网站形式（供 AI 写网站参考）"""
        p = self.probe or {}
        node_ver = p.get("node_ver") or ""
        node_path = p.get("node_path") or ""
        ssl_ports = p.get("webstation_ssl_ports") or []
        domains = p.get("domains") or []
        roots = p.get("web_roots") or []
        has_systemctl = p.get("has_systemctl")
        home_status = p.get("home_status") or ""
        system = p.get("system") or ""
        nginx_conf = p.get("nginx_conf") or ""
        proxy_files = p.get("proxy_files") or []
        loc_inc = p.get("webstation_location_include") or ""
        srv_name = p.get("webstation_server_name") or ""

        # 从抓取块补充（npm/curl 可用性）
        npm_ok = "未检测"
        if "npm" in self._blocks and "npm" in (self._blocks.get("npm") or ""):
            npm_ok = "可用"
        elif node_path:
            npm_ok = "可用（与 node 同目录）"
        curl_ok = "未检测"
        if "curl" in self._blocks:
            curl_ok = "可用" if "curl" in (self._blocks.get("curl") or "") else "未安装"

        lines = ["===== 网站能力评估（供 AI 写网站参考） =====", ""]

        # 运行环境
        lines += ["【运行环境】"]
        if system:
            lines.append(f"- 系统: {system.splitlines()[0]}")
        if node_ver:
            try:
                major = int(re.search(r"v?(\d+)\.", node_ver).group(1))
            except Exception:
                major = 0
            fw = ("Express / Koa / Fastify / 现代 ESM 语法" if major >= 18
                  else "Express / Koa（较新语法受限）" if major >= 14
                  else "基础 Node 脚本")
            lines.append(f"- Node.js: {node_ver}（{node_path}）→ 支持 {fw}")
        lines.append(f"- npm 包管理: {npm_ok}")
        if home_status and "MISSING" in home_status:
            lines.append("- ⚠ 用户无家目录：所有 node/npm 命令必须先加 export HOME=/tmp;")
        lines.append("")

        # 托管方式（实测动态值）
        lines += ["【网站托管方式（本 NAS 实测）】"]
        if roots:
            lines.append(f"- 网站根目录: {'、'.join(roots)}（自动发现 /volume*/web）")
        if domains:
            lines.append(f"- 可用域名: {'、'.join(domains[:4])}")
        if ssl_ports:
            lines.append(f"- Web Station HTTPS 端口: {'、'.join(ssl_ports)}（该端口专属，自定义反代请勿 listen）")
        lines.append("- Node 服务: 监听任意端口，经反代挂到域名/路径（location /站点路径/ → proxy_pass 127.0.0.1:端口）")
        lines.append("- 静态站: 直接放网站根目录子文件夹，nginx 自动托管")
        if loc_inc:
            lines.append(f"- 反代挂载点（门户实际 include 的扩展点）: {loc_inc}")
        if srv_name:
            lines.append(f"- 门户 Host 白名单: {srv_name}（CDN 回源 Host 必须用它，否则 404）")
        if has_systemctl:
            lines.append("- 开机自启: 支持（第三方服务脚本放 /usr/local/etc/rc.d/nas_<站点>.sh）")
        if nginx_conf:
            lines.append(f"- nginx 主配置: {nginx_conf}")
        lines.append("")

        # 推荐网站形式（按需选用，不是所有条都强制）
        lines += ["【推荐网站形式（可直接落地）】"]
        lines.append("1. Node.js + Express 单页/API 服务：server.js 监听端口，配反代挂域名/路径")
        lines.append("2. 静态网站（HTML/CSS/JS）：把文件放网站根目录子文件夹，无需 node")
        lines.append("3. React/Vue 等前端构建：本地 build 后把产物（dist）放入网站目录")
        lines.append("4. 带后端的工具站：Express 起后端端口 + 前端静态页，反代整合到同一路径")
        lines.append("5. 目录型主页（如 zzz 主页）：扫描 web/app 子目录生成工具目录，用 framework.json 注册项目")
        lines.append("")

        # 给 AI 写网站的约定（按网站类型选用，不是军规）
        lines += ["【给 AI 写网站的约定（按需选用）】"]
        lines.append("如果网站需要挂在子路径下访问（如 /zzz/、/myapp/）：")
        lines.append("  - 前端请求 API 必须用相对路径（fetch('api/xxx')），不要写死绝对路径（fetch('/api/xxx')）")
        lines.append("  - 原因：绝对路径会打到域名根，绕过反代前缀，导致 404（zzz 主页踩过的坑）")
        lines.append("  - 静态资源链接也用相对路径（img.mp4、css、js），避免同样的问题")
        lines.append("  - 不要假设自己部署在域名根 /")
        lines.append("")
        lines.append("如果网站用 Node.js + Express/Koa/Fastify：")
        lines.append("  - 根目录放一个 server.js（Node 服务唯一入口）")
        lines.append("  - 端口声明：const PORT = process.env.PORT || 3001（本软件可自动识别端口）")
        lines.append("  - 提供 GET /api/health 健康检查端点（返回 JSON），便于本软件确认服务存活")
        lines.append("  - Node 监听 0.0.0.0（或明确指定地址），便于反代和局域网访问")
        lines.append("  - 不要提交 node_modules；依赖由本软件在 NAS 上 npm install 安装")
        lines.append("")
        lines.append("如果网站要被软件反代托管（Node 挂到域名/路径下）：")
        lines.append("  - 本软件反代已做静态兜底：网站目录里真实存在的文件（页面/图标/字体/媒体）由 nginx 直接服务，node 只管 /api 和动态路由")
        lines.append("  - node 的 server.js 不需要（也不要）写静态白名单拦截文件，否则会导致图标/资源 404（ptptpt 踩过的坑）")
        lines.append("  - 前端入口、图片、CSS、JS 都放网站目录下即可，nginx 会自动服务，node 无需处理")
        lines.append("  - 只需要 node 处理 /api/* 和 SPA 前端路由 fallback（无扩展名的路径返回 index.html）")
        lines.append("")
        lines.append("如果需要被目录型主页（如 zzz 主页）扫描收录：")
        lines.append("  - 在网站根目录放 framework.json，至少包含 name/description/icon/version/link 字段")
        lines.append("  - icon 路径区分大小写（Linux 敏感）：目录里是 AG.png 就不要写 ag.png")
        lines.append("  - link 写相对路径（如 index.html）或绝对路径（如 /web/ai/index.html）")
        lines.append("")
        lines.append("如果网站包含 PHP（如 dy 下载器）：")
        lines.append("  - 放独立子目录，让 Web Station 原生处理 PHP")
        lines.append("  - 不要和 Node 反代路径重叠（反代会劫持 .php 请求，导致 PHP 不执行）")
        lines.append("")
        lines.append("通用约定（所有网站都建议遵守）：")
        lines.append("  - 目录和文件名避免空格与中文（SSH 命令、grep、反代路径都能顺畅处理）")
        lines.append("  - 不要提交 node_modules；依赖由本软件在 NAS 上 npm install 安装")
        lines.append("  - 端口避免使用 Web Station 专属 HTTPS 端口（如 450），反代监听另选端口")
        lines.append("  - API 路由兼容尾斜杠：/api/xxx 与 /api/xxx/ 要等价（浏览器/CDN 可能带斜杠）")
        lines.append("  - 代码不要依赖 Host/域名判断（CDN 回源可能改写 Host）；用相对路径或从请求头动态取")
        lines.append("  - 跨域场景（前端与 API 不同端口/域名）响应加 Access-Control-Allow-Origin")
        lines.append("")
        lines.append("好处：按上述条件选用后，本软件的扫描、启动入口识别、反代配置、依赖安装都能一次跑通。")
        lines.append("")

        # 已知限制（写代码必须注意）
        lines += ["【已知限制（写代码注意）】"]
        lines.append(f"- 该 NAS 的 curl: {curl_ok}；若未安装，调试/健康检查请用 node 脚本而非 curl")
        if home_status and "MISSING" in home_status:
            lines.append("- 无用户家目录，所有 node/npm 命令加 export HOME=/tmp;")
        if ssl_ports:
            lines.append(f"- {'、'.join(ssl_ports)} 是 Web Station HTTPS 专属端口，反代不要 listen 该端口")
        lines.append(f"- 反代挂载点：写进门户实际 include 的扩展点模式（{loc_inc or '自动探测'}），不是固定文件名")
        lines.append("- ⚠ 群晖 Web Station 可能重建 conf.d 清空反代文件；本软件在网页面板会自动检测并重写")
        if srv_name:
            lines.append(f"- ⚠ 门户有 Host 白名单（{srv_name}），非白名单 Host 直接 404；CDN 回源 Host 必须用白名单域名")
        lines.append("- ⚠ 外网 80/443 可能被封，外网访问靠 CDN 回源或带端口；网站内不要写死 http://域名/ 绝对地址")
        lines.append("- ⚠ 外网走 CDN 时，改动网站/反代后需在 CDN 控制台刷新缓存（API 路径建议不缓存），否则外网仍是旧内容/旧 404")
        lines.append("- ⚠ 网站 403 时先查目录权限（nginx http 用户需可读，目录 chmod 755）")
        lines.append("- ⚠ CDN 回源到自签证书源站需在 CDN 配置忽略证书校验，否则回源失败")
        lines.append("- ⚠ 不要在 CDN 配「重写访问URL」补斜杠/补 index.html 的规则（如 ^/([^.?#]+)$ → /$1/、^/(.+)/$ → /$1/index.html）：目录补 index.html 会让 node 精确路由 404，且反代 rewrite 无法完美兜底。尾斜杠问题一律用反代 rewrite 在 NAS 侧解决（rewrite ^/路径/(.*?)/?$ /$1 break;），不要动 CDN 重写规则")
        lines.append("- 本软件自愈：站点异常停止自动重启、反代文件被 Web Station 清空自动重写（进入网页面板时自动执行）")
        if proxy_files:
            lines.append("- 反代共享文件勿整体删除，只改其中该网站的 location 块")

        self.cap_text.setPlainText("\n".join(lines))
