"""Service manager card widget."""
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QSizePolicy

CARD_STYLE = """
QFrame#SvcCard { background:#fff; border:1px solid #e0e0e0; border-radius:10px; }
QFrame#SvcCard[active="true"] { border:1px solid #a6e3a1; background:#f6fff6; }
QLabel#Dot { font-size:18px; }
QPushButton#SvcBtn { background:#f0f2f5; border:none; border-radius:5px; padding:5px 12px; font-size:12px; color:#333; }
QPushButton#SvcBtn:hover { background:#e0e3ea; }
QPushButton#SvcBtn:disabled { color:#bbb; }
QPushButton#DangerBtn { background:#fef0f0; border:none; border-radius:5px; padding:5px 12px; font-size:12px; color:#c62828; }
QPushButton#DangerBtn:hover { background:#fde0e0; }
QPushButton#BrowseOnBtn { background:#e8f5e9; border:1px solid #4caf50; border-radius:5px; padding:5px 12px; font-size:12px; color:#2e7d32; font-weight:bold; }
QPushButton#BrowseCheckBtn { background:#fff8e1; border:1px solid #ffc107; border-radius:5px; padding:5px 12px; font-size:12px; color:#f57f17; font-weight:bold; }
QPushButton#BrowseOffBtn { background:#ffebee; border:1px solid #f44336; border-radius:5px; padding:5px 12px; font-size:12px; color:#c62828; font-weight:bold; }
"""


class ServiceCard(QFrame):
    """单个服务卡片，mgr 通过运行时注入提供业务回调。"""
    def __init__(self, svc: dict, mgr):
        super().__init__()
        self.svc = svc
        self.mgr = mgr
        self.setObjectName("SvcCard")
        self.setStyleSheet(CARD_STYLE)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(96)
        self.active = False
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)
        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setObjectName("Dot")
        self.dot.setStyleSheet("color:#bbb;")
        self.name_lbl = QLabel(self.svc["name"])
        self.name_lbl.setStyleSheet("font-size:14px; font-weight:bold; color:#1a1a2e;")
        self.status_lbl = QLabel("检测中...")
        self.status_lbl.setStyleSheet("font-size:11px; color:#888;")
        top.addWidget(self.dot)
        top.addWidget(self.name_lbl)
        top.addStretch()
        top.addWidget(self.status_lbl)
        lay.addLayout(top)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.toggle_btn = QPushButton("启动")
        self.toggle_btn.setObjectName("SvcBtn")
        self.toggle_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle_btn.clicked.connect(self._on_toggle)
        btns.addWidget(self.toggle_btn)
        if self.svc.get("name") == "Nginx":
            self.browse_btn = QPushButton("目录共享")
            self.browse_btn.setObjectName("SvcBtn")
            self.browse_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.browse_btn.clicked.connect(lambda: self.mgr.open_share_manager(self))
            btns.addWidget(self.browse_btn)
            self.browse_enabled = False
        if self.svc.get("systemd_service"):
            rst_btn = QPushButton("重启服务")
            rst_btn.setObjectName("SvcBtn")
            rst_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            rst_btn.clicked.connect(lambda: self.mgr.svc_systemd_restart(self))
            btns.addWidget(rst_btn)
        if self.svc.get("config_path"):
            view_btn = QPushButton("查看配置")
            view_btn.setObjectName("SvcBtn")
            view_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            view_btn.clicked.connect(lambda: self.mgr.svc_view_config(self))
            btns.addWidget(view_btn)
        if self.svc.get("log_path"):
            log_btn = QPushButton("日志")
            log_btn.setObjectName("SvcBtn")
            log_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            log_btn.clicked.connect(lambda: self.mgr.show_service_log(self))
            btns.addWidget(log_btn)
        if self.svc.get("custom"):
            del_btn = QPushButton("删除")
            del_btn.setObjectName("DangerBtn")
            del_btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            del_btn.clicked.connect(lambda: self.mgr.delete_custom_service(self))
            btns.addWidget(del_btn)
        lay.addLayout(btns)

    def _on_toggle(self):
        self.mgr.service_action(self, "stop" if self.active else "start")

    def set_active(self, active: bool):
        self.active = active
        self.setProperty("active", "true" if active else "false")
        self.dot.setStyleSheet("color:#4caf50;" if active else "color:#bbb;")
        self.status_lbl.setText("运行中" if active else "已停止")
        self.status_lbl.setStyleSheet("font-size:11px; color:#2e7d32;" if active else "font-size:11px; color:#999;")
        self.toggle_btn.setText("停止" if active else "启动")
        self.toggle_btn.setObjectName("DangerBtn" if active else "SvcBtn")
        self.toggle_btn.style().unpolish(self.toggle_btn)
        self.toggle_btn.style().polish(self.toggle_btn)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_browse_enabled(self, enabled: bool):
        self.browse_enabled = enabled
        self.browse_btn.setText("✓ 目录共享" if enabled else "目录共享")
        self.browse_btn.setObjectName("BrowseOnBtn" if enabled else "SvcBtn")
        self.browse_btn.style().unpolish(self.browse_btn)
        self.browse_btn.style().polish(self.browse_btn)

    def set_share_status(self, online: int, offline: int, checking: bool = False):
        if checking:
            self.browse_btn.setText("目录共享 检测中...")
            self.browse_btn.setObjectName("BrowseCheckBtn")
        elif offline > 0:
            self.browse_btn.setText(f"目录共享 ({offline}个失败)")
            self.browse_btn.setObjectName("BrowseOffBtn")
        elif online > 0:
            self.browse_btn.setText(f"✓ 目录共享 ({online}个在线)")
            self.browse_btn.setObjectName("BrowseOnBtn")
        else:
            self.browse_btn.setText("目录共享")
            self.browse_btn.setObjectName("SvcBtn")
        self.browse_btn.style().unpolish(self.browse_btn)
        self.browse_btn.style().polish(self.browse_btn)
