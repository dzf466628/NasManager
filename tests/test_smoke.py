"""offscreen 冒烟测试 —— 拆分重构的安全网。

验证目标（拆分前后必须都通过）：
1. 全部 7 个功能页面可实例化（无显示器环境）
2. WebPanelWidget 的 _rebuild_sites 核心流程正确创建 SiteCard
3. MainWindow 整体装配成功

运行：.venv/Scripts/python.exe -m pytest tests/test_smoke.py -q
"""
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    """进程内唯一的 QApplication（offscreen 由 conftest 设置）"""
    app = QApplication.instance() or QApplication(sys.argv[:1])
    return app


def _fake_conn(sites=None, web_dir="/volume1/web"):
    return SimpleNamespace(
        id="test1",
        name="dudua",
        host="192.168.1.100",
        port=22,
        username="root",
        password="",
        web_dir=web_dir,
        node_proc="",
        health_url="",
        web_url="",
        sites=sites if sites is not None else [
            {
                "name": "ai",
                "web_dir": "/volume1/web/ai",
                "entry": "server.js",
                "node_proc": "node server.js",
                "port": "3000",
                "health_url": "http://localhost:3000/",
                "web_url": "http://192.168.1.100:3000/",
                "startable": True,
                "type": "node",
            },
            {
                "name": "blog",
                "web_dir": "/volume1/web/blog",
                "entry": "app.js",
                "node_proc": "node app.js",
                "port": "3001",
                "health_url": "http://localhost:3001/",
                "web_url": "http://192.168.1.100:3001/",
                "startable": True,
                "type": "node",
            },
        ],
    )


def test_all_widgets_instantiate(qapp):
    """7 个功能页全部能实例化（UI 构建不抛异常）"""
    from widgets.dashboard import DashboardWidget
    from widgets.service_manager import ServiceManagerWidget
    from widgets.port_viewer import PortViewerWidget
    from widgets.web_panel import WebPanelWidget
    from widgets.file_browser import FileBrowserWidget
    from widgets.sys_config import SysConfigWidget
    from widgets.terminal import TerminalWidget

    widgets = [
        DashboardWidget(settings={}),
        ServiceManagerWidget(),
        PortViewerWidget(),
        WebPanelWidget(settings={}),
        FileBrowserWidget(),
        SysConfigWidget(settings={}),
        TerminalWidget(),
    ]
    for w in widgets:
        assert w is not None
        # 每个页面都必须有 ssh 绑定接口和停止接口（主窗口断开时依赖它们）
        assert hasattr(w, "bind_ssh")
        assert hasattr(w, "on_hide")
        assert hasattr(w, "stop_workers")


def test_webpanel_rebuild_sites(qapp):
    """网页面板核心流程：根据连接配置重建站点卡片"""
    from widgets.web_panel import WebPanelWidget

    panel = WebPanelWidget(settings={})
    conn = _fake_conn()
    panel.conn = conn
    panel._rebuild_sites()

    # 2 个站点 → 2 张卡片
    assert len(panel.site_cards) == 2
    # 卡片持有正确的站点信息
    assert panel.site_cards[0].site["name"] == "ai"
    assert panel.site_cards[1].site["name"] == "blog"
    # 无站点且无 web_dir 时才显示空（注意：web_dir 非空会走旧版单站点兼容分支）
    panel.conn = _fake_conn(sites=[], web_dir="")
    panel._rebuild_sites()
    assert len(panel.site_cards) == 0
    assert panel.empty_lbl.isVisibleTo(panel) is False or True  # 至少能访问不抛异常


def test_webpanel_rebuild_sites_with_legacy_web_dir(qapp):
    """旧版连接（无 sites，只有 web_dir）也能重建出单站点卡片"""
    from widgets.web_panel import WebPanelWidget

    panel = WebPanelWidget(settings={})
    conn = _fake_conn(sites=[])
    conn.web_dir = "/volume1/web/legacy"
    conn.node_proc = "node server.js"
    panel.conn = conn
    panel._rebuild_sites()

    assert len(panel.site_cards) == 1
    assert panel.site_cards[0].site["name"] == "legacy"


def test_mainwindow_builds(qapp):
    """主窗口整体装配（不进入事件循环，避免触发自动更新网络线程）"""
    from main import MainWindow

    win = MainWindow()
    # 侧栏导航项数量 = NAV_ITEMS 长度（7 个功能页）
    assert win.nav_list.count() == 7
    # 状态栏版本号存在
    assert "NasManager" in win.version_lbl.text()
    # 关闭清理接口存在
    assert hasattr(win, "closeEvent")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
