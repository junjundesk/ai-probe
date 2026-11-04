"""UI 主题、窗口尺寸与滚动可达性的回归测试。

Qt 测试必须放在独立进程里：tests/test_qt_app.py 的 tearDownModule 会主动销毁
QApplication，同进程再创建窗口容易踩到 Qt 生命周期问题。

子进程以 UI_COMPAT_OK 作为「断言已全部通过」的哨兵：Qt 在退出阶段可能崩溃
（Linux offscreen 段错误、Windows 高缩放 0xC0000005），所以判定以哨兵为准，
不看退出码。
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import PySide6.QtWidgets  # noqa: F401

    QT_AVAILABLE = True
    QT_SKIP_REASON = ""
except ImportError as exc:  # pragma: no cover - 取决运行环境
    QT_AVAILABLE = False
    QT_SKIP_REASON = f"PySide6 不可用：{exc}"


_CHILD_SCRIPT = r"""
import os
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QWidget,
)

from ai_probe.qt_app import QtMainWindow, QtRelayDialog
from ai_probe.qt_compat import apply_light_theme, fit_window_to_screen

WINDOW = "#f7f9fc"
BASE = "#ffffff"
TEXT = "#1f2937"
PLACEHOLDER = "#6b7280"
DISABLED_TEXT = "#7c8798"
ACTIVE_HIGHLIGHT = "#1565c0"
INACTIVE_HIGHLIGHT = "#cdddf1"


def finish(message="UI_COMPAT_OK"):
    # 打印成功标记后立即结束进程。Linux offscreen 与 Windows 原生环境都出现过
    # 销毁 QApplication 时崩溃（SIGSEGV/SIGBUS/0xC0000409）；断言已全部通过，
    # 不能让析构过程决定退出码。
    app = QApplication.instance()
    if app is not None:
        for widget in QApplication.topLevelWidgets():
            widget.close()
        app.processEvents()
    print(message)
    sys.stdout.flush()
    sys.stderr.flush()
    # Windows 高缩放环境卸载 Qt DLL 时偶发 0xC0000005 访问冲突；场景断言此时已全部
    # 通过，用 TerminateProcess 跳过卸载阶段，再用 os._exit 兜底。
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.TerminateProcess(kernel32.GetCurrentProcess(), 0)
        except Exception:
            pass
    os._exit(0)


def install_dark_palette(app):
    dark = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#202020",
        QPalette.ColorRole.Base: "#202020",
        QPalette.ColorRole.Button: "#202020",
        QPalette.ColorRole.Text: "#ffffff",
        QPalette.ColorRole.WindowText: "#ffffff",
        QPalette.ColorRole.ButtonText: "#ffffff",
        QPalette.ColorRole.PlaceholderText: "#eeeeee",
        QPalette.ColorRole.Highlight: "#ffffff",
        QPalette.ColorRole.HighlightedText: "#202020",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for role, color in colors.items():
            dark.setColor(group, role, QColor(color))
    app.setPalette(dark)


def check_light_widget(widget, base=True):
    palette = widget.palette()
    assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window).name() == WINDOW
    assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.WindowText).name() == TEXT
    assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text).name() == TEXT
    if base:
        assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base).name() == BASE
    assert palette.color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.WindowText).name() == TEXT
    assert palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText).name() == DISABLED_TEXT


def build_window(tmp):
    window = QtMainWindow(b"k" * 32, Path(tmp) / "config.json", Path(tmp) / "usage.json")
    window.project()["models"] = [
        {"id": "sample-model-ok", "status": "可用", "first_ms": 200, "reply": "示例响应"},
        {"id": "sample-model-failed", "status": "不可用", "error": "示例错误（无网络请求）"},
    ]
    window.refresh_models()
    for index in range(30):
        window.remote_list.addItem(f"remote-model-{index}")
    window.log_text.setPlainText("\n".join(f"line {index}" for index in range(40)))
    return window


def scenario_entry():
    app = QApplication([])
    install_dark_palette(app)

    from unittest import mock

    from ai_probe import entry

    seen = {}

    def fake_load_or_create_config_key(ask_password, show_info, show_error):
        seen["palette"] = app.palette().color(QPalette.ColorRole.Text).name()
        seen["base"] = app.palette().color(QPalette.ColorRole.Base).name()
        seen["style"] = app.style().name().casefold()
        assert seen["palette"] == TEXT, seen
        assert seen["base"] == BASE, seen
        assert seen["style"] == "fusion", seen
        # This is the same kind of dialog the real loader creates for a password.
        dialog = QInputDialog()
        dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
        dialog.setLabelText("配置密码")
        dialog.show()
        app.processEvents()
        line = dialog.findChild(QLineEdit)
        assert line is not None
        assert line.echoMode() == QLineEdit.EchoMode.Password
        assert line.palette().color(QPalette.ColorRole.Text).name() == TEXT
        assert dialog.palette().color(QPalette.ColorRole.WindowText).name() == TEXT
        dialog.close()
        return None

    with mock.patch("ai_probe.config.load_or_create_config_key", side_effect=fake_load_or_create_config_key):
        old_argv = sys.argv
        sys.argv = ["ai-probe"]
        try:
            entry.main()
        finally:
            sys.argv = old_argv
    assert seen["palette"] == TEXT
    assert app.styleSheet() == ""

    # Opening further windows must not reset a theme that is already installed.
    sentinel = QPalette(app.palette())
    sentinel.setColor(QPalette.ColorRole.Window, QColor("#123456"))
    app.setPalette(sentinel)
    apply_light_theme(app)
    assert app.palette().color(QPalette.ColorRole.Window).name() == "#123456"
    finish()


def scenario_main():
    app = QApplication([])
    install_dark_palette(app)
    with tempfile.TemporaryDirectory() as tmp:
        window = build_window(tmp)
        # Direct construction under a dark application palette must already be light,
        # before show() or processEvents() get a chance to polish the window.
        assert window.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window).name() == WINDOW
        assert window.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base).name() == BASE
        assert window.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text).name() == TEXT
        window.show()
        app.processEvents()
        window.activateWindow()
        window.project_list.setFocus()
        window.project_list.setCurrentRow(0)
        app.processEvents()

        assert app.style().name().casefold() == "fusion", app.style().name()
        assert app.styleSheet() == ""
        assert "white" not in window.styleSheet().lower()
        assert "background" not in window.styleSheet().lower()
        check_light_widget(window)
        for widget in (
            window.project_list,
            window.remote_list,
            window.model_tree,
            window.log_text,
            window.project_search,
            window.base_url,
            window.api_key,
        ):
            check_light_widget(widget)
            assert widget.palette().color(QPalette.ColorRole.Text).name() == TEXT
        assert window.project_list.viewport().palette().color(QPalette.ColorRole.Base).name() == BASE
        assert window.project_list.viewport().palette().color(QPalette.ColorRole.Text).name() == TEXT

        assert window.project_list.count() >= 1
        assert window.project_list.selectedItems()
        palette = window.project_list.palette()
        assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight).name() == ACTIVE_HIGHLIGHT
        assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText).name() == BASE
        assert palette.color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight).name() == INACTIVE_HIGHLIGHT
        assert palette.color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.HighlightedText).name() == TEXT
        item = window.project_list.item(0)
        rect = window.project_list.visualItemRect(item)
        selected_pixel = window.project_list.viewport().grab().toImage().pixelColor(
            max(rect.left(), rect.right() - 4), max(rect.top(), rect.center().y())
        )
        assert selected_pixel.blue() > selected_pixel.red() + 30, selected_pixel.name()
        assert min(selected_pixel.red(), selected_pixel.green(), selected_pixel.blue()) < 200, selected_pixel.name()

        window.show_key_button.setEnabled(False)
        app.processEvents()
        assert not window.show_key_button.isEnabled()
        assert window.show_key_button.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name() == DISABLED_TEXT
        assert app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base).name() == "#f7f8fa"
        assert window.base_url.placeholderText().startswith("https://")
        assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.PlaceholderText).name() == PLACEHOLDER
        assert window.api_key.echoMode() == QLineEdit.EchoMode.Password

        screen = app.primaryScreen()
        available = screen.availableGeometry()
        assert window.width() <= available.width() - 48, (window.size(), available)
        assert window.height() <= available.height() - 48, (window.size(), available)
        assert window.minimumWidth() <= available.width() - 48
        assert window.minimumHeight() <= available.height() - 48

        main_scroll = window.findChild(QScrollArea, "mainScroll")
        workspace = window.findChild(QScrollArea, "workspaceScroll")
        assert main_scroll is not None
        assert workspace is not None
        assert main_scroll.widget().isAncestorOf(window.project_list)
        assert main_scroll.widget().isAncestorOf(workspace)
        assert workspace.widget().isAncestorOf(window.log_text)
        assert workspace.widget().isAncestorOf(window.show_key_button)

        class FakeLargeScreen:
            @staticmethod
            def availableGeometry():
                return QRect(0, 0, 1920, 1080)

        probe = QWidget()
        fit_window_to_screen(probe, (1500, 940), (1120, 720), screen=FakeLargeScreen())
        assert probe.size() == QSize(1500, 940), probe.size()
        assert probe.minimumSize() == QSize(1120, 720), probe.minimumSize()

        # The scroll wrappers must stay invisible on a full-size desktop window.
        window.resize(1500, 940)
        app.processEvents()
        assert window.size() == QSize(1500, 940), window.size()
        assert main_scroll.horizontalScrollBar().maximum() == 0
        assert main_scroll.verticalScrollBar().maximum() == 0
        assert workspace.horizontalScrollBar().maximum() == 0
        assert workspace.verticalScrollBar().maximum() == 0

        window.close()
    finish()


def scenario_relay():
    app = QApplication([])
    install_dark_palette(app)
    with tempfile.TemporaryDirectory() as tmp:
        window = build_window(tmp)
        window.show()
        app.processEvents()
        relay = QtRelayDialog(window)
        assert relay.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Window).name() == WINDOW
        assert relay.palette().color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text).name() == TEXT
        relay.show()
        app.processEvents()

        assert relay.parentWidget() is None
        assert relay.windowFlags() & Qt.WindowType.Window
        assert relay.key.echoMode() == QLineEdit.EchoMode.Password
        assert "background" not in relay.styleSheet().lower()
        check_light_widget(relay)
        for widget in (relay.host, relay.port, relay.key, relay.user_agent, relay.project_search, relay.projects):
            check_light_widget(widget)
            assert widget.palette().color(QPalette.ColorRole.Text).name() == TEXT
        assert relay.projects.viewport().palette().color(QPalette.ColorRole.Base).name() == BASE
        assert relay.tabs.count() == 3
        assert relay.tabs.widget(0) is relay.config_tab
        assert relay.tabs.widget(2) is relay.stats_tab
        assert isinstance(relay.config_tab, QScrollArea)
        assert relay.config_tab.objectName() == "relayConfigScroll"
        assert relay.config_tab.widget().isAncestorOf(relay.start_button)
        assert relay.config_tab.widget().isAncestorOf(relay.debug_capture)
        if relay.projects.count():
            relay.projects.setCurrentRow(0)
            assert relay.projects.selectedItems()
        palette = relay.projects.palette()
        assert palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight).name() == ACTIVE_HIGHLIGHT
        assert palette.color(QPalette.ColorGroup.Inactive, QPalette.ColorRole.Highlight).name() == INACTIVE_HIGHLIGHT
        assert app.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name() == DISABLED_TEXT

        available = app.primaryScreen().availableGeometry()
        assert relay.width() <= available.width() - 48, (relay.size(), available)
        assert relay.height() <= available.height() - 48, (relay.size(), available)
        assert relay.minimumWidth() <= available.width() - 48
        assert relay.minimumHeight() <= available.height() - 48

        # The relay config tab only scrolls when the window is actually too small.
        relay.resize(900, 700)
        app.processEvents()
        assert relay.size() == QSize(900, 700), relay.size()
        assert relay.config_tab.horizontalScrollBar().maximum() == 0
        assert relay.config_tab.verticalScrollBar().maximum() == 0

        box = QMessageBox(QMessageBox.Icon.Information, "提示", "独立消息框")
        box.show()
        app.processEvents()
        assert box.parentWidget() is None
        assert box.palette().color(QPalette.ColorRole.Window).name() == WINDOW
        assert box.palette().color(QPalette.ColorRole.WindowText).name() == TEXT
        assert box.palette().color(QPalette.ColorRole.Text).name() == TEXT
        box.close()

        password = QInputDialog()
        password.setTextEchoMode(QLineEdit.EchoMode.Password)
        password.setLabelText("配置密码")
        password.show()
        app.processEvents()
        line = password.findChild(QLineEdit)
        assert line is not None
        assert line.echoMode() == QLineEdit.EchoMode.Password
        assert password.palette().color(QPalette.ColorRole.Window).name() == WINDOW
        assert line.palette().color(QPalette.ColorRole.Text).name() == TEXT
        password.close()
        relay.close()
        window.close()
    finish()


def scenario_high_dpi():
    app = QApplication([])
    install_dark_palette(app)
    available = app.primaryScreen().availableGeometry()
    assert available.width() <= 600, avail_as_text(available)
    with tempfile.TemporaryDirectory() as tmp:
        window = build_window(tmp)
        window.show()
        app.processEvents()
        check_light_widget(window)
        assert window.width() <= available.width() - 48, (window.size(), available)
        assert window.height() <= available.height() - 48, (window.size(), available)
        assert window.minimumWidth() <= available.width() - 48
        assert window.minimumHeight() <= available.height() - 48

        main_scroll = window.findChild(QScrollArea, "mainScroll")
        workspace = window.findChild(QScrollArea, "workspaceScroll")
        assert main_scroll is not None
        assert workspace is not None
        assert main_scroll.horizontalScrollBar().maximum() > 0, "main workspace not horizontally scrollable"
        assert workspace.horizontalScrollBar().maximum() > 0, "project form not horizontally scrollable"
        assert workspace.verticalScrollBar().maximum() > 0, "project form not vertically scrollable"
        assert workspace.width() >= 300, workspace.width()
        assert window.log_text.visibleRegion().isEmpty()
        workspace.ensureWidgetVisible(window.log_text, 0, 0)
        app.processEvents()
        assert not window.log_text.visibleRegion().isEmpty()
        workspace.ensureWidgetVisible(window.api_key, 0, 0)
        main_scroll.horizontalScrollBar().setValue(main_scroll.horizontalScrollBar().maximum())
        app.processEvents()
        assert workspace.width() >= 300

        relay = QtRelayDialog(window)
        relay.show()
        app.processEvents()
        assert relay.width() <= available.width() - 48, (relay.size(), available)
        assert relay.height() <= available.height() - 48, (relay.size(), available)
        check_light_widget(relay)
        assert relay.config_tab.horizontalScrollBar().maximum() > 0, "relay config not horizontally scrollable"
        assert relay.config_tab.verticalScrollBar().maximum() > 0, "relay config not vertically scrollable"
        assert relay.start_button.visibleRegion().isEmpty()
        relay.config_tab.ensureWidgetVisible(relay.start_button, 0, 0)
        app.processEvents()
        assert not relay.start_button.visibleRegion().isEmpty()
        relay.tabs.setCurrentIndex(1)
        app.processEvents()
        assert relay.prompt.isVisible()
        assert relay.append_prompt.isVisible()
        relay.tabs.setCurrentIndex(2)
        app.processEvents()
        assert relay.stats_table.isVisible()
        relay.close()
        window.close()
    finish()


def scenario_native():
    app = QApplication([])
    assert app.platformName() == "windows", app.platformName()
    install_dark_palette(app)
    with tempfile.TemporaryDirectory() as tmp:
        window = build_window(tmp)
        window.show()
        app.processEvents()
        relay = QtRelayDialog(window)
        relay.show()
        app.processEvents()
        assert app.style().name().casefold() == "fusion"
        for widget in (
            window, window.project_list, window.remote_list, window.model_tree,
            window.log_text, window.base_url, relay, relay.projects, relay.key,
            relay.prompt, relay.stats_table,
        ):
            check_light_widget(widget)
        for top in (window, relay):
            available = top.screen().availableGeometry()
            assert top.width() <= available.width() - 48, (top.size(), available)
            assert top.height() <= available.height() - 48, (top.size(), available)
            assert top.frameGeometry().width() <= available.width(), (top.frameGeometry(), available)
            assert top.frameGeometry().height() <= available.height(), (top.frameGeometry(), available)
            assert not top.grab().isNull()

        # Actual raster output must contain the blue selection, not an empty white view.
        # QPixmap pixels are physical pixels; visualItemRect uses logical pixels.
        window.project_list.setCurrentRow(0)
        window.activateWindow()
        window.project_list.setFocus()
        app.processEvents()
        view = window.project_list
        rect = view.visualItemRect(view.item(0))
        image = view.viewport().grab().toImage()
        dpr = image.devicePixelRatio()
        x = min(image.width() - 1, int((rect.right() - 4) * dpr))
        y = min(image.height() - 1, int(rect.center().y() * dpr))
        selected = image.pixelColor(x, y)
        assert selected.blue() > selected.red() + 30, selected.name()
        assert min(selected.red(), selected.green(), selected.blue()) < 240, selected.name()

        # Scrollbars are optional on large screens, but their targets must stay reachable.
        outer = window.findChild(QScrollArea, "mainScroll")
        workspace = window.findChild(QScrollArea, "workspaceScroll")
        outer.ensureWidgetVisible(workspace, 0, 0)
        workspace.ensureWidgetVisible(window.log_text, 0, 0)
        relay.config_tab.ensureWidgetVisible(relay.start_button, 0, 0)
        app.processEvents()
        assert not window.log_text.visibleRegion().isEmpty()
        assert not relay.start_button.visibleRegion().isEmpty()

        capture_dir = os.environ.get("AI_PROBE_TEST_SCREENSHOT_DIR")
        if capture_dir:
            output = Path(capture_dir)
            output.mkdir(parents=True, exist_ok=True)
            outer.horizontalScrollBar().setValue(0)
            workspace.verticalScrollBar().setValue(0)
            relay.config_tab.verticalScrollBar().setValue(0)
            app.processEvents()
            assert window.grab().save(str(output / "main.png"))
            assert relay.grab().save(str(output / "relay.png"))
        print("native", app.primaryScreen().availableGeometry(), "DPR", window.devicePixelRatioF())
        relay.close()
        window.close()
    finish()


def avail_as_text(rect):
    return f"available={rect.width()}x{rect.height()}"


def main():
    scenario = sys.argv[1]
    if scenario == "entry":
        scenario_entry()
    elif scenario == "main":
        scenario_main()
    elif scenario == "relay":
        scenario_relay()
    elif scenario == "high_dpi":
        scenario_high_dpi()
    elif scenario == "native":
        scenario_native()
    else:
        raise SystemExit(f"unknown scenario: {scenario}")


if __name__ == "__main__":
    main()
    # 兜底：若某个场景没走到 finish()，也不要让 Qt 析构影响退出码。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
"""


@unittest.skipUnless(QT_AVAILABLE, QT_SKIP_REASON)
class UiCompatibilityTests(unittest.TestCase):
    def run_scenario(self, scenario, scale_factor="1", platform="offscreen"):
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = platform
        env["QT_SCALE_FACTOR"] = scale_factor
        env.pop("QT_SCREEN_SCALE_FACTORS", None)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
        result = subprocess.run(
            [sys.executable, "-u", "-c", _CHILD_SCRIPT, scenario],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        # 成功标记代表场景里的断言已全部跑到并通过；Qt 在部分环境卸载 DLL 时会
        # 崩溃（Linux offscreen 段错误、Windows 高缩放 0xC0000005），不能据此判定失败。
        self.assertIn(
            "UI_COMPAT_OK",
            result.stdout,
            f"Qt 子进程场景 {scenario!r} 失败（exit {result.returncode}）\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}",
        )

    def test_entry_initializes_theme_before_password(self):
        self.run_scenario("entry")

    def test_direct_main_window_overrides_dark_palette(self):
        self.run_scenario("main")

    def test_relay_and_standalone_dialogs_are_readable(self):
        self.run_scenario("relay")

    def test_small_high_dpi_windows_remain_scrollable(self):
        self.run_scenario("high_dpi", scale_factor="2")

    @unittest.skipUnless(
        sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "windows",
        "需要显式启用 Windows 原生 Qt 平台",
    )
    def test_windows_native_dark_theme_and_host_scaling(self):
        self.run_scenario("native", scale_factor=os.environ.get("QT_SCALE_FACTOR", "1"), platform="windows")


if __name__ == "__main__":
    unittest.main()
