"""命令行与桌面启动入口。"""

from __future__ import annotations

import sys
from tkinter import Tk, messagebox

try:
    import requests
except ImportError:
    requests = None

from .config import load_or_create_config_key
from .edit_context import install_edit_context_menu


def main() -> None:
    if "--self-test" in sys.argv:
        from .self_test import self_test

        self_test()
        return
    root = Tk()
    install_edit_context_menu(root)
    root.withdraw()
    if requests is None:
        messagebox.showerror("缺少依赖", "请先运行：pip install -r requirements.txt")
        root.destroy()
        return
    config_key = load_or_create_config_key(root)
    if config_key is None:
        root.destroy()
        return
    if "--lightweight" in sys.argv:
        from .relay_only import RelayOnlyApp

        try:
            RelayOnlyApp(root, config_key)
        except RuntimeError as exc:
            messagebox.showerror("轻量模式", str(exc), parent=root)
            root.destroy()
            return
        root.mainloop()
        return
    try:
        from .ui import ProbeApp

        ProbeApp(root, config_key)
    except RuntimeError as exc:
        messagebox.showerror("启动失败", str(exc), parent=root)
        root.destroy()
        return
    root.deiconify()
    root.mainloop()
