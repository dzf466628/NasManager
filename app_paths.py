"""
资源路径工具
- 开发环境：相对于项目根目录
- PyInstaller 打包后：相对于 sys._MEIPASS 临时解压目录
"""
import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """获取资源文件的绝对路径，兼容开发环境和 PyInstaller onefile"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative
    return Path(__file__).parent / relative


def exe_dir() -> Path:
    """exe 所在目录（onefile 时 sys.executable 指向 exe；开发时返回项目根）"""
    if hasattr(sys, "frozen"):
        return Path(sys.executable).parent
    return Path(__file__).parent
