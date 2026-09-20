"""Web 面板的纯逻辑辅助函数。

这些函数不依赖 QWidget 或 WebPanelWidget 实例，集中放置后便于单元测试。
web_panel.py 通过静态方法别名保持原有 self._xxx(...) 调用接口。
"""
import re


PATH_EXPORT = (
    "export PATH=$PATH:/usr/local/bin:/usr/bin:/opt/bin:"
    "/volume1/@appstore/Node.js/usr/local/bin:/var/packages/Node.js/target/usr/local/bin; "
)

PROXY_DIRS = (
    "/etc/nginx/conf.d/ /etc/nginx/sites-enabled/ "
    "/usr/local/etc/nginx/conf.d/ /usr/local/etc/nginx/sites-enabled/ "
    "/usr/local/etc/nginx/sites-available/"
)

HEALTH_LBL_COLORS = {
    "idle": ("#555555", "#2e7163"),
    "pending": ("#f57f17", "#e65100"),
    "ok": ("#2e7d32", "#1b5e20"),
    "http": ("#b8860b", "#8f6a00"),
    "fail": ("#c62828", "#b71c1c"),
}


def health_lbl_style(state: str) -> str:
    color, hover = HEALTH_LBL_COLORS.get(state, HEALTH_LBL_COLORS["idle"])
    return (
        f"QLabel#HealthLbl {{ font-size:12px; color:{color}; }}"
        f"QLabel#HealthLbl:hover {{ color:{hover}; text-decoration:underline; font-weight:bold; }}"
    )


def safe_name(name: str) -> str:
    return "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:16] or "site"


def proxy_probe_cmd(port: str) -> str:
    return (
        "grep -rnE 'proxy_pass[[:space:]]+https?://(127\\.0\\.0\\.1|localhost):"
        f"{port}([^0-9]|$)' {PROXY_DIRS} 2>/dev/null | head -10"
    )


def proxy_block_uses_port(block: str, port: str) -> bool:
    return bool(
        re.search(
            rf"proxy_pass\s+https?://(127\.0\.0\.1|localhost):{port}(?!\d)",
            block,
            re.I,
        )
    )


def standard_location(
    path: str,
    port: str,
    slash_fix: bool = True,
    web_root: str = "/volume1/web",
) -> str:
    named = f"@_nas_{port}"
    if slash_fix:
        head_proxy = (
            f"location = {path} {{\n"
            f"    rewrite ^{path}(.*?)/?$ /$1 break;\n"
            f"    proxy_pass http://127.0.0.1:{port};\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_http_version 1.1;\n"
            "}\n"
        )
        tail_proxy = (
            f"location {named} {{\n"
            f"    rewrite ^{path}(.*)$ /$1 break;\n"
            f"    proxy_pass http://127.0.0.1:{port};\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_http_version 1.1;\n"
            "}\n"
        )
    else:
        head_proxy = (
            f"location = {path} {{\n"
            f"    proxy_pass http://127.0.0.1:{port}/;\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_http_version 1.1;\n"
            "}\n"
        )
        tail_proxy = (
            f"location {named} {{\n"
            f"    rewrite ^{path}(.*)$ /$1 break;\n"
            f"    proxy_pass http://127.0.0.1:{port};\n"
            "    proxy_set_header Host $host;\n"
            "    proxy_set_header X-Real-IP $remote_addr;\n"
            "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
            "    proxy_set_header X-Forwarded-Proto $scheme;\n"
            "    proxy_http_version 1.1;\n"
            "}\n"
        )
    return (
        head_proxy +
        f"location {path} {{\n"
        f"    root {web_root};\n"
        f"    try_files $uri {named};\n"
        "}\n"
        f"{tail_proxy}"
    )


def standard_server(
    host: str,
    listen_port: str,
    path: str,
    port: str,
    slash_fix: bool = True,
    web_root: str = "/volume1/web",
) -> str:
    server_names = (host or "").strip() or "your.domain.com"
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and not path.endswith("/"):
        path += "/"
    named = f"@_nas_{port}"
    if slash_fix:
        head = (
            f"        rewrite ^{path}(.*?)/?$ /$1 break;\n"
            f"        proxy_pass http://127.0.0.1:{port};\n"
        )
        tail = (
            f"        rewrite ^{path}(.*)$ /$1 break;\n"
            f"        proxy_pass http://127.0.0.1:{port};\n"
        )
    else:
        head = f"        proxy_pass http://127.0.0.1:{port}/;\n"
        tail = (
            f"        rewrite ^{path}(.*)$ /$1 break;\n"
            f"        proxy_pass http://127.0.0.1:{port};\n"
        )
    headers = (
        "        proxy_set_header Host $host;\n"
        "        proxy_set_header X-Real-IP $remote_addr;\n"
        "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        "        proxy_set_header X-Forwarded-Proto $scheme;\n"
        "        proxy_http_version 1.1;\n"
    )
    return (
        "server {\n"
        f"    listen {listen_port};\n"
        f"    server_name {server_names};\n"
        f"    root {web_root};\n"
        f"    location = {path} {{\n{head}{headers}    }}\n"
        f"    location {path} {{\n"
        f"        try_files $uri {named};\n"
        "    }\n"
        f"    location {named} {{\n{tail}{headers}    }}\n"
        "}\n"
    )


def location_blocks(content: str) -> list:
    blocks = []
    pos = 0
    n = len(content)
    while True:
        match = re.search(r"location\s", content[pos:])
        if not match:
            break
        start = pos + match.start()
        line_start = content.rfind("\n", 0, start) + 1
        if "#" in content[line_start:start]:
            pos = start + 1
            continue
        brace = content.find("{", start)
        if brace == -1:
            break
        depth = 1
        i = brace + 1
        while i < n and depth > 0:
            if content[i] == "{":
                depth += 1
            elif content[i] == "}":
                depth -= 1
            i += 1
        if depth != 0:
            break
        blocks.append((start, content[start:i]))
        pos = i
    return blocks


def location_targets(probe: dict, safe: str):
    targets = [
        (
            f"/etc/nginx/conf.d/.location.webstation.conf.{safe}",
            "conf.d/.location.webstation.conf*",
        )
    ]
    pattern = (probe or {}).get("webstation_location_include") or ""
    if pattern.startswith("conf.d/"):
        base = pattern[len("conf.d/") :].rstrip("*").rstrip(".")
        if base:
            targets.append((f"/etc/nginx/conf.d/{base}.{safe}", pattern))
    return targets


def nginx_t_passed(out: str) -> bool:
    out = out or ""
    if "test is successful" not in out:
        return False
    return not any(
        bad in out
        for bad in ("test failed", "[emerg]", "[error]", "syntax error", "unknown directive")
    )
