"""进程重启辅助，与 GUI 无关。

``restart_application`` 原先放在 ``relay_only``（PySide6 模块）里，导致
旧版 Tkinter 界面与相关测试只要导入它就会连带加载 Qt。放到这里之后，
两种界面与无 GUI 环境都能直接复用。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def restart_application(lightweight: bool) -> bool:
    """Start the alternate process mode without inheriting the current UI."""

    compiled = getattr(sys, "frozen", False) or globals().get("__compiled__") is not None
    command = [sys.executable] if compiled else [sys.executable, "-m", "ai_probe"]
    if lightweight:
        command.append("--lightweight")
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200
    try:
        subprocess.Popen(command, cwd=str(Path.cwd()), close_fds=True, creationflags=flags)
    except OSError:
        return False
    return True
