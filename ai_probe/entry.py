"""命令行与 PySide6 桌面启动入口。"""

from __future__ import annotations

import sys


def main() -> None:
    if "--self-test" in sys.argv:
        from .self_test import self_test

        self_test()
        return

    if "--healthcheck" in sys.argv:
        from .relay_service import healthcheck

        raise SystemExit(0 if healthcheck() else 1)

    if "--serve" in sys.argv:
        # 容器部署走这条路：只依赖与 GUI 无关的中转核心，不导入 PySide6。
        from .relay_service import main as serve

        raise SystemExit(serve())

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(f"缺少 PySide6，请先运行：pip install -e . ({exc})", file=sys.stderr)
        return

    from .config import load_or_create_config_key

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("AI Probe")
    application.setOrganizationName("AI Probe")

    def ask_password(prompt):
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        value, accepted = QInputDialog.getText(
            None,
            "配置解密",
            prompt,
            QLineEdit.EchoMode.Password,
        )
        return value if accepted else None

    def show_info(title, text):
        QMessageBox.information(None, title, text)

    def show_error(title, text):
        QMessageBox.critical(None, title, text)

    config_key = load_or_create_config_key(ask_password, show_info, show_error)
    if config_key is None:
        return

    if "--lightweight" in sys.argv:
        from .relay_only import RelayOnlyApp

        try:
            RelayOnlyApp(application, config_key)
        except RuntimeError as exc:
            QMessageBox.critical(None, "轻量模式", str(exc))
            return
        application.exec()
        return

    try:
        from .qt_app import QtMainWindow

        window = QtMainWindow(config_key)
    except RuntimeError as exc:
        QMessageBox.critical(None, "启动失败", str(exc))
        return
    window.show()
    application.exec()
