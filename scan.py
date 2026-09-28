# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
环境扫描器
- 通过 SSH 实时探测 NAS 环境，找到写网页需要的配置
- 扫描 /volume*/web/ 下所有站点（server.js 或 package.json），返回 sites 列表
- 分步执行，通过回调上报进度，供弹窗进度条显示
"""
import posixpath
import re


class EnvScanner:
    def __init__(self, ssh):
        self.ssh = ssh

    @staticmethod
    def _shq(s: str) -> str:
        return "'" + s.replace("'", "'\\''") + "'"

    def _run(self, cmd: str, timeout: int = 15):
        try:
            return self.ssh.run_command(cmd, timeout=timeout)
        except Exception:
            return None

    def scan(self, progress_cb=None):
        """分步扫描，返回 dict（sites 列表 + 主站点兼容字段）"""
        result = {"web_dir": "", "node_proc": "", "health_url": "", "web_url": "",
                  "node_path": "", "node_ver": "", "sites": []}

        def step(i, total, msg):
            if progress_cb:
                progress_cb(i, total, msg)

        total = 4

        # 1. 探测 Node 环境
        step(1, total, "探测 Node 环境...")
        node_path = ""
        node_ver = ""
        r = self._run("export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin; command -v node 2>/dev/null; node -v 2>/dev/null")
        if r and r.stdout:
            lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
            if lines:
                node_path = lines[0]
            for l in lines:
                if l.startswith("v") and any(c.isdigit() for c in l):
                    node_ver = l
        if not node_path:
            r = self._run("ls -la /usr/local/bin/node 2>/dev/null | awk '{print $NF}'")
            if r and r.stdout.strip():
                node_path = r.stdout.strip().splitlines()[0]

        # 2. 列出所有站点目录（含 server.js 或 package.json）
        step(2, total, "扫描 /volume*/web/ 下的网站...")
        site_dirs = []  # [(dir, has_server_js, has_package_json)]
        r = self._run("find /volume*/web -maxdepth 4 -name 'server.js' -not -path '*/@eaDir/*' -not -path '*/node_modules/*' 2>/dev/null | head -50", timeout=20)
        server_js_dirs = set()
        if r and r.stdout:
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    server_js_dirs.add(posixpath.dirname(line))
        r = self._run("find /volume*/web -maxdepth 4 -name 'package.json' -not -path '*/@eaDir/*' -not -path '*/node_modules/*' 2>/dev/null | head -50", timeout=20)
        pkg_dirs = set()
        if r and r.stdout:
            for line in r.stdout.splitlines():
                line = line.strip()
                if line:
                    pkg_dirs.add(posixpath.dirname(line))

        all_dirs = sorted(server_js_dirs | pkg_dirs)
        for d in all_dirs:
            site_dirs.append((d, d in server_js_dirs, d in pkg_dirs))

        # 3. 解析每个站点
        step(3, total, f"解析 {len(site_dirs)} 个网站...")
        sites = []
        for d, has_js, has_pkg in site_dirs:
            site = self._inspect_site(d, has_js, has_pkg)
            if site:
                sites.append(site)

        # 4. 生成地址，主站点 = 第一个可启动的
        step(4, total, "生成访问地址...")
        main = next((s for s in sites if s["startable"]), (sites[0] if sites else None))
        if main:
            result["web_dir"] = main["web_dir"]
            result["node_proc"] = main["node_proc"]
            result["health_url"] = main["health_url"]
            result["web_url"] = main["web_url"]

        result["node_path"] = node_path
        result["node_ver"] = node_ver
        result["sites"] = sites
        return result

    def _inspect_site(self, web_dir: str, has_js: bool, has_pkg: bool) -> dict:
        """解析单个站点：启动方式、端口、地址"""
        name = posixpath.basename(web_dir)
        node_proc = ""
        entry = ""
        startable = False
        stype = "unknown"

        if has_js:
            # 优先 server.js
            r = self._run(f"ls {self._shq(web_dir)} 2>/dev/null | grep -E '\\.js$' | head -3")
            js_name = "server.js"
            if r and r.stdout.strip():
                js_name = r.stdout.strip().splitlines()[0].strip()
            node_proc = f"node {js_name}"
            entry = js_name
            startable = True
            stype = "node"
        elif has_pkg:
            # package.json 的 scripts.start
            r = self._run(f"grep -E '\\\"start\\\"|scripts' {self._shq(web_dir)}/package.json 2>/dev/null | head -3")
            start_cmd = ""
            if r and r.stdout:
                for line in r.stdout.splitlines():
                    m = re.search(r'\"start\"\s*:\s*\"([^\"]+)\"', line)
                    if m:
                        start_cmd = m.group(1)
                        break
            if start_cmd:
                node_proc = start_cmd
                entry = f"npm start ({start_cmd})"
                startable = True
                stype = "package"

        # 提取端口：扫描目录下所有 js 文件（排除 node_modules），覆盖不同入口文件名
        port = ""
        r = self._run(f"grep -rnsE 'PORT[ ]*=|listen\\(|port[ ]*=' {self._shq(web_dir)} --include='*.js' 2>/dev/null | grep -v node_modules | head -20", timeout=20)
        if r and r.stdout:
            port = self._extract_port(r.stdout)
        if not port and has_pkg:
            r = self._run(f"grep -nE 'port[ ]*=|PORT[ ]*=' {self._shq(web_dir)}/package.json 2>/dev/null | head -5")
            if r and r.stdout:
                port = self._extract_port(r.stdout)

        # 运行时实测兜底：优先取 node 进程实际监听端口。
        # 覆盖跨行写法 / config.port / listen(表达式) 等静态正则抓不到的写法；
        # 服务未运行时实测为空，自动回退上面静态解析结果。
        runtime_port = self._running_port_for(web_dir)
        if runtime_port:
            port = runtime_port

        host = self.ssh.host if self.ssh else ""
        health_url = f"http://localhost:{port}/" if port else ""
        web_url = f"http://{host}:{port}/" if port else ""

        return {
            "name": name,
            "web_dir": web_dir,
            "entry": entry,
            "node_proc": node_proc,
            "port": port,
            "health_url": health_url,
            "web_url": web_url,
            "startable": startable,
            "type": stype,
        }

    def _running_port_for(self, web_dir: str) -> str:
        """运行时实测端口：先锁定该站点目录对应的 node PID，再从 ss/netstat
        监听表里取它实际监听的端口。

        PID 来源按可靠度排序：
          1) {web_dir}/.server.pid —— 本软件启动网站时写入，最准；
          2) 遍历 node 进程，用 /proc/<pid>/cwd 比对站点目录
             （`cd 目录 && node server.js` 的命令行里只有 `node server.js`，
              不含目录，旧版 grep 目录永远抓不到，必须看进程工作目录）；
          3) 退化：命令行里直接带站点绝对路径的写法（node /abs/path/server.js）。
        服务没跑或拿不到时返回空，由静态解析结果兜底。
        """
        try:
            shq = self._shq(web_dir)
            pids = set()
            # 1) 本软件写入的 PID 文件（最可靠），并确认该进程还活着且确实是 node
            r = self._run(f"cat {shq}/.server.pid 2>/dev/null", timeout=8)
            if r and r.stdout.strip():
                cand = r.stdout.strip().splitlines()[0].strip()
                if cand.isdigit():
                    chk = self._run(f"ps -p {cand} -o comm= 2>/dev/null", timeout=8)
                    if chk and "node" in (chk.stdout or "").lower():
                        pids.add(cand)
            # 2)+3) PID 文件没命中：按进程工作目录比对，再退化到命令行含目录
            if not pids:
                r = self._run(
                    "for p in $(pgrep -f node 2>/dev/null); do "
                    f"cwd=$(readlink /proc/$p/cwd 2>/dev/null); "
                    f'if [ "$cwd" = {shq} ]; then echo "$p"; fi; done; '
                    f"ps -ef | grep '[n]ode' | grep {shq} "
                    "| awk '{print $2}'",
                    timeout=12)
                if r and r.stdout:
                    for line in r.stdout.splitlines():
                        cand = line.strip()
                        if cand.isdigit():
                            pids.add(cand)
            if not pids:
                return ""
            # ss 监听表：LISTEN 0 511 0.0.0.0:3001 ... users:(("node",pid=123,fd=20))
            r2 = self._run("ss -tlnp 2>/dev/null | grep -iE 'node' | head -40", timeout=12)
            if r2 and r2.stdout:
                for line in r2.stdout.splitlines():
                    m = re.search(r':(\d{1,5})\s', line)
                    mp = re.search(r'pid=(\d+)', line)
                    if m and mp and mp.group(1) in pids:
                        return m.group(1)
            # netstat 兜底格式：...:3001 ... LISTEN 12345/node
            r3 = self._run("netstat -tlnp 2>/dev/null | grep -iE 'node' | head -40", timeout=12)
            if r3 and r3.stdout:
                for line in r3.stdout.splitlines():
                    m = re.search(r':(\d{1,5})\s', line)
                    mp = re.search(r'(\d+)/[nN]ode', line)
                    if m and mp and mp.group(1) in pids:
                        return m.group(1)
        except Exception:
            return ""
        return ""

    @staticmethod
    def _extract_port(text: str) -> str:
        """从 js/package.json 相关行解析端口。

        兼容：Number(/parseInt( 包裹、|| / ?? 默认值、argv、直接赋值、
        listen(数字)、listen(端口变量) 回溯。\b 边界避免把 SUPPORT 等
        以 port 结尾的词误判成端口。
        """
        if not text:
            return ""

        def _valid(p: str) -> bool:
            try:
                n = int(p)
            except (TypeError, ValueError):
                return False
            return 1 <= n <= 65535

        # PORT = Number(process.env.PORT) || 3001 / process.env.PORT ?? 4000
        # 关键：等号和 process.env 之间允许 Number(、parseInt( 等包裹，旧正则写死
        # process.env 紧跟等号，遇到 Number(...) 就漏端口。
        m = re.search(r'\bPORT\s*=\s*[^;\n]*?(?:\|\||\?\?)\s*(\d{1,5})', text, re.I)
        if m and _valid(m.group(1)):
            return m.group(1)
        # PORT = process.argv[2] || 3000
        m = re.search(r'\bPORT\s*=\s*process\.argv\[\d+\]\s*(?:\|\||\?\?)\s*(\d{1,5})', text, re.I)
        if m and _valid(m.group(1)):
            return m.group(1)
        # const PORT = 3000 / PORT = 3000 / PORT: 3000 / port: 4000
        m = re.search(r'(?:const|let|var)?\s*\bPORT\b\s*[:=]\s*(\d{1,5})', text, re.I)
        if m and _valid(m.group(1)):
            return m.group(1)
        # listen(4000) / app.listen(4000, '0.0.0.0')
        m = re.search(r'listen\(\s*(\d{1,5})', text, re.I)
        if m and _valid(m.group(1)):
            return m.group(1)
        # listen(PORT)：监听的是变量，回溯文本找该变量的取值/默认值
        m = re.search(r'listen\(\s*([A-Za-z_$][\w$]*)\s*[,)]', text, re.I)
        if m:
            var = re.escape(m.group(1))
            m2 = re.search(r'\b' + var + r'\s*=\s*[^;\n]*?(?:\|\||\?\?)\s*(\d{1,5})', text, re.I)
            if m2 and _valid(m2.group(1)):
                return m2.group(1)
            m2 = re.search(r'\b' + var + r'\s*[:=]\s*(\d{1,5})', text, re.I)
            if m2 and _valid(m2.group(1)):
                return m2.group(1)
        return ""
