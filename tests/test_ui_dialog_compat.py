"""渠道导入 / 管理 API 密钥两个对话框的小屏高缩放回归测试。

固定 700×440、700×420 的对话框在 400×400 逻辑像素的屏幕上会把标题栏和底部
按钮挤出屏幕；QLabel 不换行还会把最小宽度顶到 ~660px。这里在独立子进程里
用真实 UI 验证：窗口收敛到可用区域、说明文字换行、输入框/表格可滚动、按钮
可见可点，以及取消/保存路径只写临时配置。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

try:
    import PySide6  # noqa: F401

    QT_AVAILABLE = True
    QT_SKIP_REASON = ""
except ImportError as exc:  # pragma: no cover - 取决于运行环境
    QT_AVAILABLE = False
    QT_SKIP_REASON = f"PySide6 不可用：{exc}"

_CHILD_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTableWidget,
)

from ai_probe import config
from ai_probe.qt_app import QtMainWindow
from ai_probe.store_service import StoreService, default_store

SCENARIO = sys.argv[1]
CONFIG_KEY = b"0123456789abcdef0123456789abcdef"
PREFERRED_IMPORT = (700, 440)
PREFERRED_KEYS = (700, 420)
SCREEN_MARGIN = 24


def fail(message):
    raise AssertionError(message)


def settle(app, rounds=5):
    for _ in range(rounds):
        app.processEvents()


def find_button(box, text):
    for button in box.buttons():
        if button.text() == text:
            return button
    fail("找不到按钮：" + text)


def check_button(dialog, button, available):
    if button is None or button.visibleRegion().isEmpty():
        fail("按钮不可见：" + (button.text() if button else "None"))
    center = button.mapTo(dialog, button.rect().center())
    if not dialog.rect().contains(center):
        fail("按钮超出对话框：" + button.text())
    if not available.contains(button.mapToGlobal(button.rect().center())):
        fail("按钮超出屏幕：" + button.text())


def check_dialog(dialog, preferred, available):
    dialog.show()
    settle(app)
    max_width = available.width() - SCREEN_MARGIN * 2
    max_height = available.height() - SCREEN_MARGIN * 2
    expected_width = max(min(360, max_width), min(preferred[0], max_width))
    expected_height = max(min(300, max_height), min(preferred[1], max_height))
    actual = (dialog.width(), dialog.height())
    if actual != (expected_width, expected_height):
        fail("对话框尺寸异常：%s，期望 %s（可用区域 %s）" % (actual, (expected_width, expected_height), available))
    frame = dialog.frameGeometry()
    if not available.contains(frame):
        fail("对话框边框超出屏幕：%s / %s" % (frame, available))
    if dialog.minimumWidth() > dialog.width() or dialog.minimumHeight() > dialog.height():
        fail("对话框最小尺寸大于当前尺寸")
    return frame


app = QApplication([])
if SCENARIO == "native" and app.platformName() != "windows":
    fail("原生测试没有使用 Windows 平台插件")
screen = app.primaryScreen()
if screen is None:
    fail("没有可用屏幕")
available = screen.availableGeometry()
if SCENARIO == "small":
    if available.width() > 400 or available.height() > 400:
        fail("小屏场景可用区域应不超过 400x400，实际 %s" % (available,))
elif SCENARIO == "normal":
    if available.width() < PREFERRED_IMPORT[0] or available.height() < PREFERRED_IMPORT[1]:
        fail("正常屏场景可用区域过小：%s" % (available,))

safety_dir = Path(os.environ["AI_PROBE_DATA_DIR"]).resolve()
default_data_file = Path(config.DATA_FILE).resolve()
if safety_dir not in default_data_file.parents:
    fail("默认数据文件没有落在隔离目录：%s" % (default_data_file,))

work = safety_dir / "explicit"
work.mkdir()
data_file = work / "config.json"
usage_file = work / "usage.json"
payload = default_store()
project = payload["projects"][0]
project["name"] = "默认项目"
project["base_url"] = "https://default.example.com/v1"
project["api_key"] = "sk-default"
project["api_keys"] = [
    {"id": "k-default", "name": "主密钥", "value": "sk-default"},
    {"id": "k-backup", "name": "备用", "value": "sk-backup"},
]
service = StoreService(CONFIG_KEY, data_file)
service.save(payload)
window = QtMainWindow(service.config_key, data_file=data_file, usage_file=usage_file)
window.show()
settle(app)


def import_cancel(dialog):
    check_dialog(dialog, PREFERRED_IMPORT, available)
    if dialog.windowTitle() != "渠道导入":
        fail("导入对话框标题异常：%s" % (dialog.windowTitle(),))
    hint = dialog.findChild(QLabel)
    editor = dialog.findChild(QPlainTextEdit)
    box = dialog.findChild(QDialogButtonBox)
    if hint is None or editor is None or box is None:
        fail("导入对话框缺少控件")
    if not hint.wordWrap():
        fail("说明文字未开启换行")
    line_height = hint.fontMetrics().height()
    if hint.height() < line_height:
        fail("说明文字高度不足一行")
    if hint.fontMetrics().horizontalAdvance(hint.text()) > hint.width() and hint.height() < line_height * 2:
        fail("说明文字需要换行却被压成一行")
    if editor.viewport().visibleRegion().isEmpty() or editor.height() < line_height * 4:
        fail("输入框不可见或高度不足")
    cancel_button = box.button(QDialogButtonBox.Cancel)
    check_button(dialog, cancel_button, available)
    editor.setFocus()
    settle(app)
    QTest.keyClicks(editor, "https://typed.example.com/v1")
    settle(app)
    if editor.toPlainText() != "https://typed.example.com/v1":
        fail("输入框无法键入文本：%r" % (editor.toPlainText(),))
    editor.setPlainText("\n".join("line %d" % index for index in range(60)))
    settle(app)
    if editor.verticalScrollBar().maximum() <= 0:
        fail("输入框内容超高时不能滚动")
    QTest.mouseClick(cancel_button, Qt.LeftButton)
    settle(app)
    return dialog.result()


with mock.patch("ai_probe.qt_app.QDialog.exec", new=import_cancel):
    window.import_channels()
settle(app)
if len(window.store["projects"]) != 1:
    fail("取消导入后项目数不应变化")
if len(StoreService(CONFIG_KEY, data_file).load()["projects"]) != 1:
    fail("取消导入不应写盘")


def import_accept(dialog):
    check_dialog(dialog, PREFERRED_IMPORT, available)
    editor = dialog.findChild(QPlainTextEdit)
    box = dialog.findChild(QDialogButtonBox)
    if editor is None or box is None:
        fail("导入对话框缺少控件")
    editor.setPlainText(json.dumps([{"url": "https://import.example.com/v1", "key": "sk-imported"}]))
    settle(app)
    ok_button = box.button(QDialogButtonBox.Ok)
    check_button(dialog, ok_button, available)
    QTest.mouseClick(ok_button, Qt.LeftButton)
    settle(app)
    return dialog.result()


with (
    mock.patch("ai_probe.qt_app.QDialog.exec", new=import_accept),
    mock.patch("ai_probe.qt_app.QMessageBox.information", return_value=QMessageBox.Ok) as information,
):
    window.import_channels()
settle(app)
if not information.called:
    fail("导入成功后应提示用户")
if len(window.store["projects"]) != 2:
    fail("导入后应有 2 个项目")
imported = window.store["projects"][1]
if imported["base_url"] != "https://import.example.com/v1" or imported["api_key"] != "sk-imported":
    fail("导入渠道的地址/密钥不正确")
if window.current_id != imported["id"]:
    fail("导入后应切换到新渠道")
persisted = StoreService(CONFIG_KEY, data_file).load()
if len(persisted["projects"]) != 2 or persisted["projects"][1]["api_key"] != "sk-imported":
    fail("导入结果未写入临时配置")


def manage_keys(dialog):
    check_dialog(dialog, PREFERRED_KEYS, available)
    if dialog.windowTitle() != "管理 API 密钥":
        fail("密钥对话框标题异常：%s" % (dialog.windowTitle(),))
    table = dialog.findChild(QTableWidget)
    box = dialog.findChild(QDialogButtonBox)
    if table is None or box is None:
        fail("密钥对话框缺少控件")
    if table.rowCount() < 1:
        fail("导入后的项目应有一把默认密钥")
    if table.viewport().visibleRegion().isEmpty():
        fail("表格不可见")
    if table.horizontalScrollBar().maximum() != 0:
        fail("表格需要横向滚动才能看到操作列")
    right_edge = table.columnViewportPosition(2) + table.columnWidth(2)
    if right_edge > table.viewport().width() + 1:
        fail("操作列超出表格可视宽度")
    for row in range(table.rowCount()):
        cell = table.cellWidget(row, 2)
        if cell is None or cell.visibleRegion().isEmpty():
            fail("删除按钮不可见")
    add_button = find_button(box, "新增密钥")
    check_button(dialog, add_button, available)
    QTest.mouseClick(add_button, Qt.LeftButton)
    settle(app)
    if table.rowCount() != 2:
        fail("新增密钥没有增加表格行")
    for _ in range(19):
        QTest.mouseClick(add_button, Qt.LeftButton)
    settle(app)
    if table.rowCount() != 21:
        fail("连续新增后表格行数异常：%d" % (table.rowCount(),))
    if table.verticalScrollBar().maximum() <= 0:
        fail("表格行数超过可视高度时不能滚动")
    table.scrollToBottom()
    settle(app)
    last = table.rowCount() - 1
    last_rect = table.visualItemRect(table.item(last, 0))
    if not table.viewport().rect().contains(last_rect.center()):
        fail("滚动到底部后最后一行仍不可见")
    QTest.mouseClick(table.cellWidget(last, 2), Qt.LeftButton)
    settle(app)
    if table.rowCount() != 20:
        fail("删除按钮没有删除对应行")
    QTest.mouseClick(add_button, Qt.LeftButton)
    settle(app)
    last = table.rowCount() - 1
    table.item(last, 0).setText("第三把")
    table.item(last, 1).setText("sk-third")
    save_button = box.button(QDialogButtonBox.Save)
    check_button(dialog, save_button, available)
    QTest.mouseClick(save_button, Qt.LeftButton)
    settle(app)
    return dialog.result()


project_id = window.current_id
with mock.patch("ai_probe.qt_app.QDialog.exec", new=manage_keys):
    window.manage_api_keys()
settle(app)
if window.current_id != project_id:
    fail("保存密钥后不应切换项目")
saved_project = window.project()
keys = saved_project["api_keys"]
if len(keys) != 21:
    fail("保存后密钥数量异常：%d" % (len(keys),))
if keys[0]["value"] != "sk-imported":
    fail("原有默认密钥被破坏：%r" % (keys[0],))
if (keys[-1]["name"], keys[-1]["value"]) != ("第三把", "sk-third"):
    fail("新增密钥未保存：%r" % (keys[-1],))
reloaded = StoreService(CONFIG_KEY, data_file).load()
saved_again = next((item for item in reloaded["projects"] if item["id"] == project_id), None)
if saved_again is None:
    fail("保存后项目丢失")
if (saved_again["api_keys"][-1]["name"], saved_again["api_keys"][-1]["value"]) != ("第三把", "sk-third"):
    fail("新增密钥未写入临时配置")

window.close()
settle(app)
print("DIALOG_COMPAT_OK")
# 某些 Linux/Windows 环境会在解释器销毁 QApplication 时崩溃，
# 断言已全部通过，这里刷新输出后直接结束进程。
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
"""


@unittest.skipUnless(QT_AVAILABLE, QT_SKIP_REASON)
class DialogScreenCompatibilityTests(unittest.TestCase):
    """在真实 Qt 子进程里渲染两个对话框，避免模态循环阻塞测试进程。"""

    def _run_scenario(self, scenario, platform="offscreen"):
        with tempfile.TemporaryDirectory() as safety_dir:
            env = os.environ.copy()
            env["QT_QPA_PLATFORM"] = platform
            env["QT_SCALE_FACTOR"] = (
                os.environ.get("QT_SCALE_FACTOR", "1") if scenario == "native" else "2" if scenario == "small" else "1"
            )
            env.pop("QT_SCREEN_SCALE_FACTORS", None)
            env["AI_PROBE_DATA_DIR"] = safety_dir
            env["PYTHONIOENCODING"] = "utf-8"
            env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH", "")]))
            completed = subprocess.run(
                [sys.executable, "-u", "-c", _CHILD_SCRIPT, scenario],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
            )
            default_dir = Path(safety_dir)
            self.assertFalse((default_dir / "ai_probe_projects.json").exists(), "测试不应写入默认配置路径")
            self.assertFalse((default_dir / "ai_probe_usage.json").exists(), "测试不应写入默认用量文件")
        self.assertEqual(
            completed.returncode,
            0,
            f"子进程失败（{scenario}）：\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        self.assertIn("DIALOG_COMPAT_OK", completed.stdout)

    def test_small_screen_dialogs_stay_reachable(self):
        self._run_scenario("small")

    def test_normal_screen_keeps_preferred_sizes(self):
        self._run_scenario("normal")

    @unittest.skipUnless(
        sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "windows",
        "需要 Windows 原生平台测试环境",
    )
    def test_windows_native_dialogs_at_host_scaling(self):
        self._run_scenario("native", platform="windows")


if __name__ == "__main__":
    unittest.main()
