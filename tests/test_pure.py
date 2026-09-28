# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""纯函数单元测试 —— 重构安全网。

覆盖项目中所有不依赖 SSH 连接的纯逻辑函数，这些是拆分时最容易被改坏的地方。
拆分前先固化行为，拆分后跑一遍确保零回归。

运行：.venv/Scripts/python.exe -m pytest tests/test_pure.py -q
"""
import pytest

from main import parse_version
from scan import EnvScanner
from env_probe import EnvProbe
from ssh_client import SSHClient
from widgets.service_manager import filter_warnings
from widgets.site_colors import site_color_hex
from widgets.web_panel import WebPanelWidget


# ---------- main.parse_version ----------

class TestParseVersion:
    def test_strip_v_prefix(self):
        assert parse_version("v1.1.31") == (1, 1, 31)

    def test_no_prefix(self):
        assert parse_version("1.1.9") == (1, 1, 9)

    def test_numeric_compare_not_string(self):
        # 字符串比较会得出 1.1.9 > 1.1.10 的错误，元组比较不会
        assert parse_version("1.1.10") > parse_version("1.1.9")

    def test_non_numeric_part_becomes_zero(self):
        assert parse_version("v1.x.3") == (1, 0, 3)

    def test_empty(self):
        # "" 会被 split 成 [""]，int("") 失败 → append(0)，所以是 (0,) 不是 ()
        assert parse_version("") == (0,)

    def test_uppercase_v(self):
        assert parse_version("V2.0.1") == (2, 0, 1)


# ---------- scan.EnvScanner._extract_port ----------

class TestExtractPort:
    @staticmethod
    def _extract(text):
        return EnvScanner._extract_port(text)

    def test_process_env_default_number_wrapped(self):
        # 修复过：Number( 包裹 process.env 时不能漏端口
        assert self._extract("PORT = Number(process.env.PORT) || 3001") == "3001"

    def test_process_env_nullish(self):
        assert self._extract("PORT = process.env.PORT ?? 4000") == "4000"

    def test_argv_default(self):
        assert self._extract("PORT = process.argv[2] || 3000") == "3000"

    def test_const_assignment(self):
        assert self._extract("const PORT = 8080") == "8080"

    def test_listen_number(self):
        assert self._extract("app.listen(4000, '0.0.0.0')") == "4000"

    def test_listen_variable_backtrack(self):
        assert self._extract("const PORT = 5555;\napp.listen(PORT)") == "5555"

    def test_invalid_out_of_range(self):
        assert self._extract("PORT = 70000") == ""

    def test_no_port(self):
        assert self._extract("const x = 1;") == ""

    def test_word_boundary_avoids_support(self):
        # \b 边界避免把 SUPPORT 之类的词误判成端口
        assert self._extract("SUPPORT = 1234") == ""


# ---------- env_probe.EnvProbe._valid_domain ----------

class TestValidDomain:
    @staticmethod
    def _valid(seg):
        return EnvProbe._valid_domain(seg)

    def test_normal_domain(self):
        assert self._valid("duadu.cc") is True

    def test_subdomain(self):
        assert self._valid("www.duadu.cc") is True

    def test_synology_ddns(self):
        assert self._valid("dudua.synology.me") is True

    def test_reject_ip(self):
        assert self._valid("192.168.1.100") is False

    def test_reject_localhost(self):
        assert self._valid("localhost") is False

    def test_reject_wildcard(self):
        assert self._valid("*.duadu.cc") is False

    def test_reject_no_dot(self):
        assert self._valid("nas") is False

    def test_reject_quickconnect_relay(self):
        assert self._valid("abc.direct.quickconnect.cn") is False

    def test_reject_empty(self):
        assert self._valid("") is False

    def test_reject_synology_default(self):
        assert self._valid("synology") is False


# ---------- ssh_client.SSHClient._shq ----------

class TestShellQuote:
    def test_plain(self):
        assert SSHClient._shq("abc") == "'abc'"

    def test_single_quote_escape(self):
        assert SSHClient._shq("a'b") == "'a'\\''b'"

    def test_path(self):
        assert SSHClient._shq("/volume1/web/ai") == "'/volume1/web/ai'"


# ---------- service_manager.filter_warnings ----------

class TestFilterWarnings:
    def test_filters_nginx_warn(self):
        assert "warn" not in filter_warnings("nginx: [warn] low address bits").lower()

    def test_filters_chdir(self):
        # 过滤后为空 → 返回占位提示文字（不会包含被过滤的内容）
        assert "chdir" not in filter_warnings("could not chdir to home").lower()

    def test_keeps_real_error(self):
        assert "syntax error" in filter_warnings("nginx: syntax error on line 1")

    def test_empty_output_becomes_hint(self):
        assert "无输出" in filter_warnings("")


# ---------- site_colors.site_color_hex ----------

class TestSiteColor:
    def test_unknown_site_fallback_gray(self):
        assert site_color_hex([], "unknown") == "#BDBDBD"

    def test_index_assignment(self):
        sites = [{"name": "ai"}, {"name": "blog"}]
        assert site_color_hex(sites, "ai") == "#E57373"  # PALETTE_HEX[0]

    def test_custom_color_priority(self):
        sites = [{"name": "ai", "custom_color": "#123456"}]
        assert site_color_hex(sites, "ai") == "#123456"


# ---------- web_panel.WebPanelWidget 反代纯逻辑 ----------

class TestSafeName:
    def test_plain(self):
        assert WebPanelWidget._safe_name("ai") == "ai"

    def test_strip_special(self):
        assert WebPanelWidget._safe_name("a b/c") == "abc"

    def test_empty_fallback(self):
        assert WebPanelWidget._safe_name("") == "site"

    def test_truncate_16(self):
        assert WebPanelWidget._safe_name("a" * 20) == "a" * 16


class TestProxyBlockUsesPort:
    def test_exact_match(self):
        block = "location / { proxy_pass http://127.0.0.1:3000; }"
        assert WebPanelWidget._proxy_block_uses_port(block, "3000") is True

    def test_localhost_match(self):
        block = "proxy_pass http://localhost:3000;"
        assert WebPanelWidget._proxy_block_uses_port(block, "3000") is True

    def test_no_false_positive_on_30000(self):
        block = "proxy_pass http://127.0.0.1:30000;"
        assert WebPanelWidget._proxy_block_uses_port(block, "3000") is False

    def test_no_match(self):
        assert WebPanelWidget._proxy_block_uses_port("proxy_pass http://127.0.0.1:4000;", "3000") is False


class TestLocationBlocks:
    def test_single_block(self):
        content = "server {\n  location /ai {\n    proxy_pass http://127.0.0.1:3000;\n  }\n}"
        blocks = WebPanelWidget._location_blocks(content)
        assert len(blocks) == 1
        assert "proxy_pass" in blocks[0][1]

    def test_nested_braces_not_truncated(self):
        # 修复过：嵌套 if/location 的 { } 不能被 [^}]* 截断
        content = (
            "location /ai {\n"
            "  if ($x) {\n"
            "    proxy_pass http://127.0.0.1:3000;\n"
            "  }\n"
            "}\n"
        )
        blocks = WebPanelWidget._location_blocks(content)
        assert len(blocks) == 1
        block = blocks[0][1]
        assert block.count("{") == block.count("}")

    def test_comment_line_skipped(self):
        content = "# location /commented {\nlocation /real {\n}\n"
        blocks = WebPanelWidget._location_blocks(content)
        assert len(blocks) == 1
        assert "/real" in blocks[0][1]

    def test_incomplete_brace_gives_up(self):
        content = "location /broken {\n"  # 没闭合
        assert WebPanelWidget._location_blocks(content) == []


class TestStandardLocation:
    def test_slash_fix_structure(self):
        loc = WebPanelWidget._standard_location("/ai", "3000", slash_fix=True)
        assert "location = /ai {" in loc
        assert "location /ai {" in loc
        assert "@_nas_3000" in loc
        assert "try_files $uri @_nas_3000" in loc

    def test_no_slash_fix(self):
        loc = WebPanelWidget._standard_location("/ai", "3000", slash_fix=False)
        assert "proxy_pass http://127.0.0.1:3000/;" in loc

    def test_custom_web_root(self):
        loc = WebPanelWidget._standard_location("/ai", "3000", web_root="/volume2/web")
        assert "root /volume2/web;" in loc


class TestStandardServer:
    def test_structure(self):
        srv = WebPanelWidget._standard_server("duadu.cc", "443", "/ai", "3000")
        assert "listen 443;" in srv
        assert "server_name duadu.cc;" in srv
        assert "location = /ai/ {" in srv  # path 会被补齐尾部 /
        assert "@_nas_3000" in srv

    def test_path_slash_normalization(self):
        srv = WebPanelWidget._standard_server("x.com", "80", "blog", "3001")
        assert "location = /blog/ {" in srv

    def test_empty_host_fallback(self):
        srv = WebPanelWidget._standard_server("", "80", "/", "3000")
        assert "server_name your.domain.com;" in srv


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
