"""Shared web panel styles."""

OPEN_DIALOG_STYLE = """
QDialog { background:#f0f2f5; }
QLabel#DlgTitle { font-size:15px; font-weight:bold; color:#2e7163; }
QLabel#DlgSub { font-size:12px; color:#8a9099; }
QLabel#DlgTip { font-size:12px; color:#7a8088; }
QWidget#WarnBar { background:#fff6e0; border:1px solid #f0d488; border-radius:8px; }
QLabel#WarnTxt { color:#8a5a00; font-size:12px; }
QPushButton#WarnBtn {
    background:#e8910c; color:#fff; border:none; border-radius:6px;
    padding:6px 14px; font-size:12px;
}
QPushButton#WarnBtn:hover { background:#d6830a; }
QPushButton#WarnBtn:disabled { background:#e3c07a; }
QFrame#UrlRow {
    background:#ffffff; border:1px solid #e3e6ea; border-radius:8px;
}
QFrame#UrlRow:hover {
    background:#eaf5f1; border:1px solid #43a68d;
}
QLabel#RowTag { color:#9aa0a6; font-size:11px; }
QLabel#RowUrl { color:#263238; font-size:12px; }
QFrame#UrlRow:hover QLabel#RowUrl { color:#2e7163; font-weight:bold; }
QPushButton#DlgPrimary {
    background:#43a68d; color:#fff; border:none; border-radius:6px;
    padding:7px 18px; font-size:12px;
}
QPushButton#DlgPrimary:hover { background:#37917c; }
QPushButton#DlgGhost {
    background:#fff; color:#555; border:1px solid #d6dae0; border-radius:6px;
    padding:7px 18px; font-size:12px;
}
QPushButton#DlgGhost:hover { background:#eef0f3; }
QScrollBar:vertical { background:transparent; width:6px; margin:0; }
QScrollBar::handle:vertical { background:#cfd6dd; border-radius:3px; min-height:36px; }
QScrollBar::handle:vertical:hover { background:#43a68d; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { background:transparent; height:6px; margin:0; }
QScrollBar::handle:horizontal { background:#cfd6dd; border-radius:3px; min-width:36px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
"""
