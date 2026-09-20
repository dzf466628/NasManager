"""pytest 全局配置：项目根加入 sys.path + Qt offscreen 模式。

作用：让 tests/ 下的测试能 import main / widgets 等模块，
且无显示器环境（CI/后台）也能实例化 Qt 组件。
"""
import os
import sys
from pathlib import Path

# 必须在导入 Qt 前设置 offscreen，保证测试环境不依赖桌面显示器。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import pytest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def qapp():
    """全测试进程复用唯一 QApplication。"""
    return QApplication.instance() or QApplication(sys.argv[:1])
