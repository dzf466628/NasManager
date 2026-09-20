"""已拆分 Web 模块的接口一致性测试。"""


def test_extracted_modules_are_runtime_implementations():
    """主面板运行时类名必须指向拆出的模块，而非旧实现。"""
    import widgets.web_panel as legacy_module
    from widgets.web_components import ClickableLabel, ColorBar, UrlRow, SpinnerWidget
    from widgets.web_workers import (
        LogReaderThread, StreamCommandWorker, InstallDepsThread,
        UrlProbeThread, RescanThread,
    )
    from widgets.web_dialogs import InstallProgressDialog, DependenciesDialog, WebHealthDialog

    assert legacy_module.ClickableLabel is ClickableLabel
    assert legacy_module.ColorBar is ColorBar
    assert legacy_module.UrlRow is UrlRow
    assert legacy_module.SpinnerWidget is SpinnerWidget
    assert legacy_module.LogReaderThread is LogReaderThread
    assert legacy_module.StreamCommandWorker is StreamCommandWorker
    assert legacy_module.InstallDepsThread is InstallDepsThread
    assert legacy_module.UrlProbeThread is UrlProbeThread
    assert legacy_module._RescanThread is RescanThread
    assert legacy_module.InstallProgressDialog is InstallProgressDialog
    assert legacy_module.DependenciesDialog is DependenciesDialog
    assert legacy_module._ExtractedWebHealthDialog is WebHealthDialog


def test_extracted_components_construct(qapp):
    """拆出的 UI 组件可独立构造，公开信号和状态接口保持不变。"""
    from widgets.web_components import ClickableLabel, ColorBar, UrlRow, SpinnerWidget

    label = ClickableLabel("启动")
    bar = ColorBar("#E57373")
    row = UrlRow("本机", "http://127.0.0.1:3000/")
    spinner = SpinnerWidget(size=14)

    assert label.objectName() == "EditableLbl"
    assert bar.height() == 5
    assert row.url == "http://127.0.0.1:3000/"
    row.set_result("ok", "可访问")
    assert row.badge.text() == "可访问"
    spinner.start()
    assert spinner._timer.isActive()
    spinner.stop()
    assert not spinner._timer.isActive()


def test_web_health_dialog_contract(qapp):
    from widgets.web_dialogs import WebHealthDialog

    dlg = WebHealthDialog(items=[("网站状态", "检查网站运行"), ("反向代理", "检测配置")])
    assert len(dlg._rows) == 2
    dlg.set_state(0, "ok")
    assert dlg._rows[0].state_lbl.text() == "正常"
    dlg.set_state(1, "fail")
    assert dlg._rows[1].state_lbl.text() == "异常"
    dlg.finish("完成")
    assert dlg.progress.value() == 100
    assert dlg.cancel_btn.isEnabled()
    assert dlg.footer_lbl.text() == "完成"
    dlg.close()


def test_sys_config_helpers_are_runtime_implementations():
    import widgets.sys_config as legacy_module
    from widgets.sys_config_helpers import shq, main_site_info
    from types import SimpleNamespace

    assert legacy_module._helper_shq is shq
    assert legacy_module._helper_main_site_info is main_site_info
    assert shq("a'b") == "'a'\\''b'"
    conn = SimpleNamespace(
        sites=[{"name": "first", "web_dir": "/first", "node_proc": "node first"},
               {"name": "main", "web_dir": "/main", "node_proc": "node main", "startable": True}],
        web_dir="", node_proc="",
    )
    assert main_site_info(conn) == ("/main", "node main")


def test_service_component_is_runtime_implementation(qapp):
    import widgets.service_manager as legacy_module
    from widgets.service_components import ClickableLabel

    assert legacy_module.ClickableLabel is ClickableLabel
    label = ClickableLabel("note")
    assert label.text() == "note"
    assert hasattr(label, "clicked")
    assert hasattr(label, "double_clicked")


def test_service_card_is_runtime_implementation(qapp):
    import widgets.service_manager as legacy_module
    from widgets.service_card import ServiceCard
    from types import SimpleNamespace

    mgr = SimpleNamespace()
    card = ServiceCard({"name": "Node"}, mgr)
    assert legacy_module.ServiceCard is ServiceCard
    assert card.active is False
    card.set_active(True)
    assert card.active is True
    assert card.toggle_btn.text() == "停止"


def test_service_helper_is_runtime_implementation():
    import widgets.service_manager as legacy_module
    from widgets.service_helpers import filter_warnings

    assert legacy_module._helper_filter_warnings is filter_warnings
    sample = "nginx: [warn] warning\nreal error"
    assert legacy_module.filter_warnings(sample) == "real error"


def test_site_card_contract(qapp):
    """SiteCard 的关键公开属性保持稳定，便于后续独立迁移。"""
    from types import SimpleNamespace
    from widgets.web_panel import SiteCard

    panel = SimpleNamespace(conn=SimpleNamespace(sites=[]), ssh=None)
    site = {
        "name": "ai",
        "web_dir": "/volume1/web/ai",
        "entry": "server.js",
        "node_proc": "node server.js",
        "port": "3000",
        "health_url": "http://localhost:3000/",
        "web_url": "http://127.0.0.1:3000/",
        "startable": True,
    }
    card = SiteCard(site, panel)
    assert card.site is site
    assert card.panel is panel
    assert card.running is False
    assert card.toggle_btn.isEnabled()
    card.set_status(True)
    assert card.running is True
    assert card.toggle_btn.text() == "停止"
    card.set_status(False)
    assert card.toggle_btn.text() == "启动"
