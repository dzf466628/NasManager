# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
环境体检器 EnvProbe
- 连接 NAS 后动态探测真实环境，供所有页面共用，避免硬编码/写死
- 探测项：node 真实路径/版本、nginx 实际主配置、Web Station HTTPS 端口、
         域名(server_name)、网站根目录、init 系统、反代现状(location)
- 所有探测都是"实时抓取"，不依赖保存的静态配置，换 NAS 也能自适应
"""
import re


class EnvProbe:
    def __init__(self, ssh, use_sudo: bool = True):
        self.ssh = ssh
        self.use_sudo = use_sudo
        self.result = {}

    def _run(self, cmd: str, timeout: int = 12, sudo: bool = None):
        try:
            return self.ssh.run_command(cmd, timeout=timeout,
                                        sudo=self.use_sudo if sudo is None else sudo)
        except Exception:
            return None

    def _out(self, r) -> str:
        return (r.stdout or "").strip() if r else ""

    # ---------- 单步探测 ----------

    def probe_system(self):
        r = self._run("uname -srm; echo '---'; "
                      "cat /etc/synoinfo.conf 2>/dev/null | grep -Ei 'upnpmodelname|modelname|unique' | head -2")
        self.result["system"] = self._out(r)

    def probe_node(self):
        # 动态找 node 真实路径：glob 通配版本号（不写死路径/版本），从实际存在的文件里挑
        r = self._run("ls -d /volume*/@appstore/Node.js*/usr/local/bin/node "
                      "/var/packages/Node.js/target/usr/local/bin/node "
                      "/usr/local/bin/node /usr/bin/node /opt/bin/node 2>/dev/null | head -3")
        paths = [p.strip() for p in self._out(r).splitlines() if p.strip()]
        node_path = paths[0] if paths else ""
        if not node_path:
            r2 = self._run("export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin; command -v node 2>/dev/null")
            node_path = (self._out(r2).splitlines() or [""])[0]
        node_ver = ""
        if node_path:
            rv = self._run(f'"{node_path}" -v 2>&1')
            node_ver = (self._out(rv).splitlines() or [""])[0]
        self.result["node_path"] = node_path
        self.result["node_ver"] = node_ver

    def probe_nginx(self):
        # 实际主配置：从 master 进程的 -c 参数拿（群晖是 /etc/nginx/nginx.conf.run）
        r = self._run("ps -ef | grep '[n]ginx: master' | head -1")
        master = self._out(r)
        nginx_conf = ""
        m = re.search(r'-c\s+(\S+)', master)
        if m:
            nginx_conf = m.group(1)
        if not nginx_conf:
            nginx_conf = "/etc/nginx/nginx.conf"  # 找不到时的兜底，探测后通常能拿到真实值
        self.result["nginx_conf"] = nginx_conf
        # nginx 二进制位置 + 版本
        r2 = self._run("command -v nginx 2>/dev/null; nginx -v 2>&1 | head -1")
        self.result["nginx_bin"] = self._out(r2)

    def probe_webstation_port(self):
        # Web Station 门户 HTTPS 端口：动态找 listen N ssl 的端口（这台是 450，换环境自动发现）
        r = self._run("grep -rhoP 'listen\\s+\\K[0-9]+(?=\\s+ssl)' "
                      "/usr/local/etc/nginx/sites-available/*.w3conf "
                      "/etc/nginx/conf.d/*.conf* 2>/dev/null | sort -un | head -5")
        ports = [p for p in self._out(r).splitlines() if p.strip()]
        self.result["webstation_ssl_ports"] = ports

    @staticmethod
    def _valid_domain(seg: str) -> bool:
        """域名合法性过滤：剔除通配/IP/localhost/无点段/默认自签 synology/群晖中继域名。"""
        seg = (seg or "").strip().strip(";").strip().strip('"').strip()
        if not seg or seg in ("_", "localhost", "synology") or "*" in seg or " " in seg:
            return False
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", seg):
            return False
        if "." not in seg:
            return False
        # 排除群晖 QuickConnect 中继域名（*.direct.quickconnect.cn/.to 等），
        # 它是 DSM 中继通道，不能用来访问 Web Station 子站
        if "quickconnect." in seg.lower():
            return False
        return True

    def probe_domain(self):
        # 域名多来源收集，避免只认 nginx server_name 漏掉：
        #   - 走 default_server 的自定义域（server_name 里根本没写，如 duadu.cc）
        #   - 群晖 DDNS 域名（dudua.synology.me）
        # 来源：① nginx 生效配置 server_name ② 证书 SAN/CN（最全）
        #       ③ /etc/ddns.conf 主机名 ④ 证书备注 desc
        names = set()

        # ① nginx -T 的 server_name
        cf = self.result.get("nginx_conf") or "/etc/nginx/nginx.conf"
        r = self._run(f"nginx -T -c {cf} 2>/dev/null | grep -E 'server_name' | head -40")
        for line in self._out(r).splitlines():
            for seg in line.replace("server_name", " ", 1).replace(",", " ").split():
                if self._valid_domain(seg):
                    names.add(seg.strip().strip(";"))
        # ①b 配置文件里的 server_name（单条命令，sudo 覆盖整条 grep）
        r2 = self._run("grep -rhoP 'server_name\\s+\\K[^;]+' "
                       "/usr/local/etc/nginx/sites-available/*.w3conf "
                       "/etc/nginx/conf.d/*.conf* /etc/nginx/sites-enabled/*.conf* 2>/dev/null")
        for seg in self._out(r2).replace(",", " ").split():
            if self._valid_domain(seg):
                names.add(seg.strip().strip(";"))

        # ② 证书 SAN/CN（最全：自定义域、群晖 DDNS 域都会出现在证书里）。
        #    证书目录是 root 700，必须整段 sh -c 提权——sudo 只作用于分号前第一条，
        #    不包 sh -c 时通配符会在普通用户 shell 里因无权列目录而失效。
        cert_inner = (
            'for c in /usr/syno/etc/certificate/_archive/*/cert.pem '
            '/usr/syno/etc/www/certificate/*/*.pem; do '
            'openssl x509 -in "$c" -noout -subject -ext subjectAltName 2>/dev/null; done'
        )
        rc = self._run(f"sh -c '{cert_inner}'")
        for line in self._out(rc).splitlines():
            for seg in re.findall(r"DNS:([^,\s]+)", line):
                if self._valid_domain(seg):
                    names.add(seg)
            m = re.search(r"CN\s*=\s*([^,/\n]+)", line)
            if m and self._valid_domain(m.group(1)):
                names.add(m.group(1).strip())

        # ③ 群晖 DDNS 主机名（/etc/ddns.conf: hostname=xxx.synology.me）
        rd = self._run("grep -E '^hostname=' /etc/ddns.conf 2>/dev/null")
        for line in self._out(rd).splitlines():
            if "=" in line:
                val = line.split("=", 1)[1]
                if self._valid_domain(val):
                    names.add(val.strip())

        self.result["domains"] = sorted(names)

    def probe_web_roots(self):
        # 网站根目录：动态找所有 /volume*/web（换 NAS 自动发现）
        r = self._run("ls -d /volume*/web 2>/dev/null")
        roots = [p for p in self._out(r).splitlines() if p.strip()]
        self.result["web_roots"] = roots

    def probe_init(self):
        # 服务管理器：动态找 systemctl / synosystemctl 真实位置（群晖不同版本位置不同）
        r = self._run("ls /usr/bin/systemctl /bin/systemctl /usr/syno/bin/systemctl "
                      "/usr/bin/synosystemctl 2>/dev/null; command -v systemctl 2>/dev/null; "
                      "command -v synosystemctl 2>/dev/null")
        out = self._out(r)
        self.result["has_systemctl"] = bool(out) and "systemctl" in out
        self.result["has_synosystemctl"] = "synosystemctl" in out

    def probe_home(self):
        r = self._run('echo -n "HOME=$HOME "; test -d "$HOME" && echo OK || echo MISSING')
        out = self._out(r)
        self.result["home_status"] = out

    def probe_proxy_summary(self):
        # 反代现状：动态列出所有含 proxy_pass 的配置文件（全目录，含 Web Station 扩展点）
        r = self._run("grep -rln 'proxy_pass' "
                      "/etc/nginx/conf.d/ /etc/nginx/sites-enabled/ "
                      "/usr/local/etc/nginx/conf.d/ /usr/local/etc/nginx/sites-enabled/ "
                      "/usr/local/etc/nginx/sites-available/ 2>/dev/null | head -20")
        files = [f for f in self._out(r).splitlines() if f.strip()]
        self.result["proxy_files"] = files
        # 域名映射：哪个域名/端口已被占用的 server（用于反代配置时避免冲突）
        r2 = self._run("nginx -T -c " + self.result.get("nginx_conf", "/etc/nginx/nginx.conf") +
                       " 2>/dev/null | grep -E 'listen |server_name ' | head -40")
        self.result["listen_summary"] = self._out(r2)

    def probe_webstation_include(self):
        """探测 Web Station 门户 server 实际 include 的 location 扩展点模式。

        关键：反代 location 必须写进"监听 Web Station 端口那个 server 真正 include
        的扩展点"才会命中。不同 DSM 版本/门户配置 include 的模式不同：
          - 群晖门户常见：include conf.d/.service.<uuid>.<uuid>.conf*（本机 dudua 门户）
          - 老门户/80:443：include conf.d/.location.webstation.conf*
        动态抓取，避免硬编码导致"写入成功但永不命中"。
        结果存 result["webstation_location_include"]，如 "conf.d/.service.xxx.conf*"；
        未识别到时为空串，调用方回退旧机制并提示。
        """
        ports = self.result.get("webstation_ssl_ports") or []
        port = ports[0] if ports else ""
        if not port:
            return
        pattern = ""
        # 1) 找监听该端口的 w3conf（Web Station 门户 server 配置文件）
        r = self._run(
            f"grep -rlE 'listen[ \\t]+{port}[ ;]' "
            "/usr/local/etc/nginx/sites-available/*.w3conf 2>/dev/null | head -3")
        files = [f.strip() for f in self._out(r).splitlines() if f.strip()]
        for f in files:
            # 2) 提取该 server include 的所有 conf.d 模式
            r2 = self._run(f"grep -oE 'include[ \\t]+conf\\.d/[^;]+;' {f} 2>/dev/null")
            includes = [ln.strip() for ln in self._out(r2).splitlines() if ln.strip()]
            for inc in includes:
                inc = inc.replace("include", "").replace(";", "").strip()
                # 3) 挑 location 扩展点：优先 .service.（Web Station 门户专用），
                #    其次 .location.webstation；排除 error_page/alias 等非挂载点
                if ".service." in inc and "error_page" not in inc:
                    pattern = inc
                    break
                if ".location.webstation" in inc and not pattern:
                    pattern = inc
            if pattern:
                # 顺便抓门户 server_name（反代命中验证的 Host 用，域名探测失败时兜底）
                r3 = self._run(f"grep -oE 'server_name[ \\t]+[^;]+;' {f} 2>/dev/null | head -1")
                sn = self._out(r3).replace("server_name", "").replace(";", "").strip()
                if sn:
                    self.result["webstation_server_name"] = sn.split()[0]
                break
        self.result["webstation_location_include"] = pattern

    # ---------- 总入口 ----------

    def probe_all(self, progress_cb=None) -> dict:
        """跑完全部探测，返回结果 dict。progress_cb(i, total, msg) 可选。"""
        steps = [
            ("system", self.probe_system, "系统信息"),
            ("node", self.probe_node, "Node 环境"),
            ("nginx", self.probe_nginx, "Nginx 配置"),
            ("webstation_port", self.probe_webstation_port, "Web Station 端口"),
            ("webstation_include", self.probe_webstation_include, "反代挂载点"),
            ("domain", self.probe_domain, "域名"),
            ("web_roots", self.probe_web_roots, "网站目录"),
            ("init", self.probe_init, "服务管理器"),
            ("home", self.probe_home, "用户目录"),
            ("proxy", self.probe_proxy_summary, "反向代理现状"),
        ]
        total = len(steps)
        for i, (key, fn, msg) in enumerate(steps, 1):
            if progress_cb:
                progress_cb(i, total, msg)
            try:
                fn()
            except Exception:
                pass
        return self.result
