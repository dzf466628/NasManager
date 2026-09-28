# Copyright (C) 2026 dudu <https://duadu.cc>
# SPDX-License-Identifier: GPL-3.0-or-later

"""
网站配色（马卡龙色系，按色相环排序）
- 按 sites 列表顺序为每个网站分配固定颜色
- 软件内凡是属于某网站的数据（端口/进程/卡片/列表）都使用该网站颜色
- 支持用户手动选择颜色（custom_color 优先）
"""
from PySide6.QtGui import QColor

# 马卡龙色系（Material 300 级别，饱和度比 200 更高，按色相环从红→黄排序）
PALETTE_HEX = [
    "#E57373",  # 浅红
    "#F06292",  # 樱粉
    "#BA68C8",  # 浅紫粉
    "#9575CD",  # 薰衣草
    "#7986CB",  # 蓝紫
    "#64B5F6",  # 浅蓝
    "#4FC3F7",  # 天蓝
    "#4DD0E1",  # 青蓝
    "#4DB6AC",  # 青绿
    "#81C784",  # 薄荷绿
    "#AED581",  # 黄绿
    "#FFF176",  # 奶黄
]

FALLBACK_HEX = "#BDBDBD"  # 浅灰：不属于任何网站


def site_names(sites) -> list:
    return [s.get("name") or "" for s in (sites or [])]


def site_color_hex(sites, name: str) -> str:
    """按网站名取固定颜色（custom_color 优先，否则按 sites 列表顺序）"""
    for s in (sites or []):
        if (s.get("name") or "") == name and s.get("custom_color"):
            return s["custom_color"]
    names = site_names(sites)
    if name in names:
        return PALETTE_HEX[names.index(name) % len(PALETTE_HEX)]
    return FALLBACK_HEX


def used_colors(sites, exclude_name: str = "") -> set:
    """返回已被其他网站占用的颜色集合（用于颜色选择时去重）"""
    used = set()
    for i, s in enumerate(sites or []):
        if (s.get("name") or "") == exclude_name:
            continue
        c = s.get("custom_color")
        if not c:
            c = PALETTE_HEX[i % len(PALETTE_HEX)]
        used.add(c)
    return used


def site_color(sites, name: str) -> QColor:
    return QColor(site_color_hex(sites, name))
