import json
import os
import tempfile
import unittest
from pathlib import Path
from threading import Barrier
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ai_probe.store_service import StoreService, default_store, normalize_store

# PySide6 依赖 Qt 的系统库（libEGL 等），精简容器与部分 Linux 环境里没有。
# 缺失时只跳过 Qt 界面测试，配置存储测试仍然照跑。
try:
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPlainTextEdit, QTableWidget

    from ai_probe.qt_app import QtMainWindow

    _QT_APP = QApplication.instance() or QApplication([])
    QT_AVAILABLE = True
    QT_SKIP_REASON = ""
except ImportError as exc:  # pragma: no cover - 取决于运行环境
    QT_AVAILABLE = False
    QT_SKIP_REASON = f"PySide6 不可用：{exc}"
    _QT_APP = None


class StoreNormalizationTests(unittest.TestCase):
    """不依赖 Qt 的部分，任何环境都应执行。"""

    def test_store_defaults_and_legacy_normalization(self):
        store = normalize_store({"projects": []})
        self.assertEqual(len(store["projects"]), 1)
        self.assertEqual(store["relay"]["user_agent"], "")
        self.assertEqual(default_store()["version"], 2)

    def test_encrypted_store_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            payload = default_store()
            payload["relay"]["user_agent"] = "qt-test-agent"
            service.save(payload)
            loaded = StoreService(service.config_key, path).load()
            self.assertEqual(loaded["relay"]["user_agent"], "qt-test-agent")

    def test_normalize_tolerates_null_and_wrong_typed_fields(self):
        # setdefault 只补缺失键；显式 null / 错误类型不能让整个配置加载失败。
        store = normalize_store(
            {
                "projects": [
                    {
                        "id": "p1",
                        "name": "畸形",
                        "api_keys": None,
                        "models": None,
                        "discovered_models": "not-a-list",
                        "manual_headers": None,
                    }
                ]
            }
        )
        project = store["projects"][0]
        self.assertEqual(project["models"], [])
        self.assertEqual(project["discovered_models"], [])
        self.assertEqual(project["manual_headers"], [])
        self.assertEqual([key["id"] for key in project["api_keys"]], ["default"])

    def test_normalize_drops_malformed_manual_header_rows(self):
        # 非 dict 的请求头行会让 parse_manual_headers 抛异常，进而拖垮中转与测活。
        store = normalize_store(
            {
                "projects": [
                    {
                        "id": "p1",
                        "manual_headers": ["junk", {"name": "Ok", "value": "v"}, 123, None, {"name": None, "value": 5}],
                    }
                ]
            }
        )
        self.assertEqual(
            store["projects"][0]["manual_headers"],
            [{"name": "Ok", "value": "v"}, {"name": "", "value": "5"}],
        )


@unittest.skipUnless(QT_AVAILABLE, QT_SKIP_REASON)
class QtApplicationSmokeTests(unittest.TestCase):
    def test_main_window_and_relay_dialog_construct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            payload["relay"]["user_agent"] = ""
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            try:
                window.open_relay()
                _QT_APP.processEvents()
                self.assertIsNotNone(window.relay_dialog)
                window.relay_dialog.user_agent.setText("qt-agent")
                window.relay_dialog.save_settings()
                self.assertEqual(window.store["relay"]["user_agent"], "qt-agent")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_relay_dialog_project_search_filters_without_losing_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            base = dict(payload["projects"][0])
            payload["projects"] = [
                {**base, "id": "p-alpha", "name": "Alpha"},
                {**base, "id": "p-beta", "name": "Beta"},
            ]
            payload["selected_project_id"] = "p-alpha"
            payload["relay"]["project_ids"] = ["p-beta"]
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            try:
                window.open_relay()
                dialog = window.relay_dialog
                dialog.project_search.setText("alp")
                hidden = {
                    dialog.projects.item(i).data(0x0100): dialog.projects.item(i).isHidden()
                    for i in range(dialog.projects.count())
                }
                self.assertEqual(hidden, {"p-alpha": False, "p-beta": True})
                dialog._set_all_projects(True)
                self.assertEqual(sorted(window.store["relay"]["project_ids"]), ["p-alpha", "p-beta"])
                dialog._set_all_projects(False)
                self.assertEqual(window.store["relay"]["project_ids"], ["p-beta"])
                dialog.project_search.clear()
                self.assertFalse(any(dialog.projects.item(i).isHidden() for i in range(dialog.projects.count())))
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_relay_dialog_lists_enabled_projects_first(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            base = dict(payload["projects"][0])
            payload["projects"] = [
                {**base, "id": "p-1", "name": "Alpha"},
                {**base, "id": "p-2", "name": "Beta"},
                {**base, "id": "p-3", "name": "Gamma"},
                {**base, "id": "p-4", "name": "Delta"},
            ]
            payload["selected_project_id"] = "p-1"
            payload["relay"]["project_ids"] = ["p-3"]
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            try:
                window.open_relay()
                dialog = window.relay_dialog

                def names():
                    return [dialog.projects.item(i).data(Qt.UserRole + 1) for i in range(dialog.projects.count())]

                self.assertEqual(names(), ["Gamma", "Alpha", "Beta", "Delta"], "已启用的渠道应排在前面")

                # 勾选后重新同步，列表顺序随之调整，新启用的渠道排到最前。
                dialog.projects.item(1).setCheckState(Qt.Checked)
                _QT_APP.processEvents()
                dialog.refresh_projects()
                self.assertEqual(names(), ["Alpha", "Gamma", "Beta", "Delta"])
                self.assertEqual(
                    [dialog.projects.item(i).data(Qt.UserRole) for i in range(2)],
                    ["p-1", "p-3"],
                )
            finally:
                dialog.close()
                window.close()
                _QT_APP.processEvents()

    def test_remote_model_list_context_menu_and_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            project = payload["projects"][0]
            project["discovered_models"] = [{"id": "gpt-4o"}, {"id": "claude-3"}, {"id": "deepseek-chat"}]
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            try:
                self.assertEqual(window.remote_list.count(), 3)
                self.assertEqual(window.remote_list.contextMenuPolicy(), Qt.ContextMenuPolicy.CustomContextMenu)
                self.assertFalse(window._build_remote_menu().actions()[0].isEnabled())

                window.remote_list.item(1).setSelected(True)
                window.remote_list.setCurrentItem(window.remote_list.item(1))
                window.copy_remote_model()
                self.assertEqual(_QT_APP.clipboard().text(), "claude-3")
                actions = window._build_remote_menu().actions()
                self.assertTrue(actions[0].isEnabled())
                self.assertTrue(actions[2].isEnabled())
                self.assertTrue(actions[3].isEnabled())

                actions[2].trigger()
                self.assertEqual([model["id"] for model in window.project()["models"]], ["claude-3"])
            finally:
                window.close()
                _QT_APP.processEvents()

    def _window_with_projects(self, directory, count):
        path = Path(directory) / "config.json"
        payload = default_store()
        base = dict(payload["projects"][0])
        payload["projects"] = [
            {**base, "id": f"p-{index:03d}", "name": f"项目{index:03d}"} for index in range(1, count + 1)
        ]
        payload["selected_project_id"] = "p-001"
        service = StoreService(b"0123456789abcdef0123456789abcdef", path)
        service.save(payload)
        window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
        window.resize(1120, 720)
        window.show()
        _QT_APP.processEvents()
        return window

    def test_project_list_click_after_scroll_keeps_selection_consistent(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory, 300)
            try:
                project_list = window.project_list
                scrollbar = project_list.verticalScrollBar()
                self.assertGreater(scrollbar.maximum(), 0, "需要足够多的项目才能覆盖滚动场景")
                scrollbar.setValue(scrollbar.maximum())
                _QT_APP.processEvents()

                row_height = max(project_list.sizeHintForRow(0), 1)
                for index in range(3):
                    point = QPoint(5, index * row_height + row_height // 2)
                    clicked = project_list.itemAt(point)
                    scroll_before = scrollbar.value()
                    QTest.mouseClick(project_list.viewport(), Qt.LeftButton, Qt.NoModifier, point)
                    _QT_APP.processEvents()
                    current = project_list.currentItem()
                    self.assertIsNotNone(current)
                    self.assertEqual(current.text(), clicked.text())
                    self.assertEqual(current.data(Qt.UserRole), window.current_id)
                    self.assertEqual(window.project_name.text(), clicked.text())
                    self.assertEqual(scrollbar.value(), scroll_before, "点击不应重置滚动位置")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_project_list_blank_click_keeps_highlight(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory, 5)
            try:
                project_list = window.project_list
                project_list.setCurrentRow(1)
                _QT_APP.processEvents()
                self.assertEqual(window.current_id, "p-002")

                QTest.mouseClick(
                    project_list.viewport(),
                    Qt.LeftButton,
                    Qt.NoModifier,
                    QPoint(5, project_list.viewport().height() - 4),
                )
                _QT_APP.processEvents()
                self.assertEqual(window.current_id, "p-002")
                self.assertEqual(project_list.currentRow(), 1, "点击空白处后高亮不应丢失")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_rename_project_applies_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory, 3)
            try:
                with mock.patch("ai_probe.qt_app.QInputDialog.getText", return_value=("新名称", True)):
                    window.rename_project()
                _QT_APP.processEvents()
                self.assertEqual(window.project()["name"], "新名称")
                self.assertEqual(window.project_name.text(), "新名称")
                self.assertEqual(window.project_list.currentItem().text(), "新名称")
                reloaded = StoreService(window.store_service.config_key, Path(directory) / "config.json").load()
                self.assertEqual(reloaded["projects"][0]["name"], "新名称")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_manage_api_keys_delete_middle_row_keeps_key_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            project = payload["projects"][0]
            project["api_keys"] = [
                {"id": "a", "name": "A", "value": "key-AAA"},
                {"id": "b", "name": "B", "value": "key-BBB"},
                {"id": "c", "name": "C", "value": "key-CCC"},
            ]
            project["models"] = [
                {
                    "id": "m-on-c",
                    "api_key_id": "c",
                    "route_name": "",
                    "status": "未测试",
                    "first_ms": None,
                    "total_ms": None,
                    "reply": "",
                    "error": "",
                }
            ]
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            window.resize(1200, 800)
            window.show()
            _QT_APP.processEvents()

            def fake_exec(dialog):
                dialog.show()
                for _ in range(5):
                    _QT_APP.processEvents()
                table = dialog.findChild(QTableWidget)
                self.assertIsNotNone(table)
                table.resize(600, 200)
                for _ in range(5):
                    _QT_APP.processEvents()
                remove_button = table.cellWidget(1, 2)  # 删除第二把密钥 B
                QTest.mouseClick(remove_button, Qt.LeftButton, Qt.NoModifier, remove_button.rect().center())
                _QT_APP.processEvents()
                return QDialog.Accepted

            try:
                with mock.patch("ai_probe.qt_app.QDialog.exec", new=fake_exec):
                    window.manage_api_keys()
                _QT_APP.processEvents()
                keys = [(key["name"], key["value"]) for key in window.project()["api_keys"]]
                self.assertEqual(keys, [("A", "key-AAA"), ("C", "key-CCC")])
                model = window.project()["models"][0]
                bound = next(key for key in window.project()["api_keys"] if key["id"] == model["api_key_id"])
                self.assertEqual(bound["value"], "key-CCC", "绑定密钥 C 的模型不应被切到其他密钥")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_detect_all_keeps_models_when_nothing_discovered_or_probed(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory, 1)
            project = window.project()
            project["models"] = [
                {
                    "id": "gpt-4o",
                    "api_key_id": "default",
                    "route_name": "my-alias",
                    "status": "可用",
                    "first_ms": 10,
                    "total_ms": 20,
                    "reply": "ok",
                    "error": "",
                }
            ]
            try:
                window._apply_detected(window.current_id, ([], [], ["upstream 500"]))
                self.assertEqual([model["id"] for model in window.project()["models"]], ["gpt-4o"])

                window._apply_detected(
                    window.current_id, ([{"id": "text-embedding-3", "api_key_id": "default"}], [], [])
                )
                self.assertEqual([model["id"] for model in window.project()["models"]], ["gpt-4o"])

                probing = [({"id": "gpt-4o", "api_key_id": "default"}, {"ok": True, "status": "可用", "reply": "hi"})]
                window._apply_detected(window.current_id, ([{"id": "gpt-4o", "api_key_id": "default"}], probing, []))
                models = {model["id"]: model for model in window.project()["models"]}
                self.assertEqual(models["gpt-4o"]["route_name"], "my-alias", "重新检测不应清空渠道模型名")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_close_flushes_pending_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(default_store())
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            try:
                window.base_url.setText("https://edited.example.com/v1")
                # 不等待防抖计时器，直接关闭，编辑也必须落盘。
                window.close()
                reloaded = StoreService(service.config_key, path).load()
                self.assertEqual(reloaded["projects"][0]["base_url"], "https://edited.example.com/v1")
            finally:
                _QT_APP.processEvents()

    def test_remove_all_models_clears_current_project_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            base = dict(payload["projects"][0])
            payload["projects"] = [
                {**base, "id": "p1", "name": "一", "models": [{"id": "a"}, {"id": "b"}]},
                {**base, "id": "p2", "name": "二", "models": [{"id": "c"}]},
            ]
            payload["selected_project_id"] = "p1"
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            try:
                with mock.patch("ai_probe.qt_app.QMessageBox.question", return_value=QMessageBox.Yes):
                    window.remove_all_models()
                _QT_APP.processEvents()
                self.assertEqual(window.project("p1")["models"], [])
                self.assertEqual([model["id"] for model in window.project("p2")["models"]], ["c"])
                self.assertEqual(window.model_tree.topLevelItemCount(), 0)
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_relay_dialog_refreshes_when_projects_change(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory, 2)
            try:
                window.open_relay()
                _QT_APP.processEvents()
                dialog = window.relay_dialog
                self.assertEqual(dialog.projects.count(), 2)

                window.new_project()
                _QT_APP.processEvents()
                self.assertEqual(len(window.store["projects"]), 3)
                self.assertEqual(dialog.projects.count(), 3, "新建项目后中转对话框应同步接口列表")

                with mock.patch("ai_probe.qt_app.QInputDialog.getText", return_value=("改名了", True)):
                    window.rename_project()
                _QT_APP.processEvents()
                names = {dialog.projects.item(i).data(Qt.UserRole + 1) for i in range(dialog.projects.count())}
                self.assertIn("改名了", names, "重命名后中转对话框应显示新名称")
            finally:
                dialog.close()
                window.close()
                _QT_APP.processEvents()

    def test_import_config_invalidates_relay_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            payload = default_store()
            project = payload["projects"][0]
            project["base_url"] = "https://x/v1"
            project["api_key"] = "sk-1"
            project["models"] = [
                {
                    "id": "old-model",
                    "api_key_id": "default",
                    "route_name": "",
                    "status": "可用",
                    "first_ms": 1,
                    "total_ms": 2,
                    "reply": "r",
                    "error": "",
                }
            ]
            payload["relay"]["project_ids"] = [project["id"]]
            payload["relay"]["port"] = 0
            service = StoreService(b"0123456789abcdef0123456789abcdef", path)
            service.save(payload)
            window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
            window.resize(1200, 800)
            try:
                window.start_relay()
                _QT_APP.processEvents()
                self.assertIsNotNone(window.relay_server)
                self.assertIn("old-model", window.relay_server.model_routes())

                imported = default_store()
                imported_project = imported["projects"][0]
                imported_project["base_url"] = "https://y/v1"
                imported_project["api_key"] = "sk-2"
                imported_project["models"] = [
                    {
                        "id": "new-model",
                        "api_key_id": "default",
                        "route_name": "",
                        "status": "可用",
                        "first_ms": 1,
                        "total_ms": 2,
                        "reply": "r",
                        "error": "",
                    }
                ]
                imported["relay"]["project_ids"] = [imported_project["id"]]
                import_path = Path(directory) / "import.json"
                import_path.write_text(json.dumps(imported, ensure_ascii=False), encoding="utf-8")

                with (
                    mock.patch("ai_probe.qt_app.QFileDialog.getOpenFileName", return_value=(str(import_path), "")),
                    mock.patch("ai_probe.qt_app.QMessageBox.question", return_value=QMessageBox.Yes),
                ):
                    window.import_config()
                _QT_APP.processEvents()
                routes = window.relay_server.model_routes()
                self.assertIn("new-model", routes, "导入配置后中转应立即改用新路由")
                self.assertNotIn("old-model", routes)
            finally:
                window.stop_relay()
                window.close()
                _QT_APP.processEvents()


@unittest.skipUnless(QT_AVAILABLE, QT_SKIP_REASON)
class ChannelImportDuplicateTests(unittest.TestCase):
    """渠道导入遇到重复时应报出渠道名，并支持一键定位。"""

    def _window(self, directory, projects):
        path = Path(directory) / "config.json"
        payload = default_store()
        base = dict(payload["projects"][0])
        payload["projects"] = [{**base, **project} for project in projects]
        payload["selected_project_id"] = payload["projects"][0]["id"]
        service = StoreService(b"0123456789abcdef0123456789abcdef", path)
        service.save(payload)
        window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
        window.resize(1200, 800)
        window.show()
        _QT_APP.processEvents()
        return window

    def _import(self, window, text):
        def fake_exec(dialog):
            dialog.show()
            _QT_APP.processEvents()
            editor = dialog.findChild(QPlainTextEdit)
            editor.setPlainText(text)
            return QDialog.Accepted

        with mock.patch("ai_probe.qt_app.QDialog.exec", new=fake_exec):
            window.import_channels()
        _QT_APP.processEvents()

    def test_import_reports_duplicate_channel_names(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(
                directory,
                [
                    {
                        "id": "p-existing",
                        "name": "已存在渠道",
                        "base_url": "https://dup.example.com/v1",
                        "api_key": "sk-existing",
                    }
                ],
            )
            seen = []
            try:
                with mock.patch.object(
                    type(window),
                    "_prompt_duplicate_channels",
                    lambda self, duplicates, imported: seen.append((duplicates, imported)),
                ):
                    self._import(
                        window,
                        json.dumps(
                            [
                                {"url": "https://dup.example.com/v1", "key": "sk-existing"},
                                {"url": "https://fresh.example.com/v1", "key": "sk-fresh"},
                            ]
                        ),
                    )
                self.assertEqual(len(window.store["projects"]), 2)
                self.assertEqual(len(seen), 1)
                duplicates, imported = seen[0]
                self.assertEqual([item["id"] for item in duplicates], ["p-existing"])
                self.assertEqual(imported, 1)
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_duplicate_labels_include_host_without_exposing_keys(self):
        labels = QtMainWindow._duplicate_labels(
            [
                {"id": "a", "name": "同名", "base_url": "https://one.example.com/v1", "api_key": "sk-secret"},
                {"id": "b", "name": "同名", "base_url": "https://two.example.com/v1", "api_key": "sk-other"},
                {"id": "c", "name": "同名", "base_url": "https://two.example.com/v1", "api_key": "sk-third"},
            ]
        )
        self.assertEqual(len(set(labels)), 3, "下拉框各项必须唯一，否则无法选中目标渠道")
        for label in labels:
            self.assertNotIn("sk-", label)
        self.assertIn("one.example.com", labels[0])

    def _run_prompt(self, window, duplicates, imported=0, click="定位", choice=0, accepted=True):
        """在无头环境下驱动重复提示：模拟点击按钮与选择渠道。"""
        pressed = {}

        def fake_exec(box):
            for button in box.buttons():
                pressed[button.text()] = button
            return 0

        with (
            mock.patch.object(QMessageBox, "exec", fake_exec),
            mock.patch.object(QMessageBox, "clickedButton", lambda self: pressed.get(click)),
            mock.patch(
                "ai_probe.qt_app.QInputDialog.getItem",
                return_value=(QtMainWindow._duplicate_labels(duplicates)[choice], accepted),
            ) as get_item,
        ):
            window._prompt_duplicate_channels(duplicates, imported)
        return get_item

    def test_duplicate_prompt_locates_single_existing_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(
                directory,
                [
                    {"id": "p-first", "name": "第一个", "base_url": "https://one.example.com/v1", "api_key": "k1"},
                    {"id": "p-dup", "name": "重复渠道", "base_url": "https://dup.example.com/v1", "api_key": "k2"},
                ],
            )
            try:
                window.project_list.setCurrentRow(0)
                window.project_search.setText("第一个")
                _QT_APP.processEvents()
                self.assertNotIn(
                    "重复渠道",
                    [window.project_list.item(i).text() for i in range(window.project_list.count())],
                )

                get_item = self._run_prompt(window, [window.project("p-dup")], click="定位")
                _QT_APP.processEvents()

                self.assertEqual(window.current_id, "p-dup")
                self.assertEqual(window.project_name.text(), "重复渠道")
                self.assertEqual(window.project_search.text(), "", "定位前应清空搜索词")
                self.assertIn("重复渠道", window.project_list.currentItem().text())
                self.assertIn("重复渠道", window.status_label.text())
                get_item.assert_not_called()
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_duplicate_prompt_asks_which_channel_when_several(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(
                directory,
                [
                    {"id": "p-1", "name": "一号", "base_url": "https://one.example.com/v1", "api_key": "k1"},
                    {"id": "p-2", "name": "二号", "base_url": "https://two.example.com/v1", "api_key": "k2"},
                ],
            )
            try:
                window.project_list.setCurrentRow(0)
                _QT_APP.processEvents()
                duplicates = [window.project("p-1"), window.project("p-2")]

                get_item = self._run_prompt(window, duplicates, click="定位", choice=1)
                _QT_APP.processEvents()

                get_item.assert_called_once()
                self.assertEqual(window.current_id, "p-2", "多选时应定位到用户选中的渠道")

                # 取消渠道选择则保持原项目不动。
                window.current_id = "p-1"
                window.store["selected_project_id"] = "p-1"
                self._run_prompt(window, duplicates, click="定位", choice=1, accepted=False)
                _QT_APP.processEvents()
                self.assertEqual(window.current_id, "p-1")
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_duplicate_prompt_cancel_keeps_current_project(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window(
                directory,
                [
                    {"id": "p-1", "name": "一号", "base_url": "https://one.example.com/v1", "api_key": "k1"},
                    {"id": "p-2", "name": "二号", "base_url": "https://two.example.com/v1", "api_key": "k2"},
                ],
            )
            try:
                window.project_list.setCurrentRow(0)
                _QT_APP.processEvents()
                self.assertEqual(window.current_id, "p-1")

                # 点击「知道了」不应改变当前项目。
                self._run_prompt(window, [window.project("p-2")], click="知道了")
                _QT_APP.processEvents()
                self.assertEqual(window.current_id, "p-1")
            finally:
                window.close()
                _QT_APP.processEvents()


@unittest.skipUnless(QT_AVAILABLE, QT_SKIP_REASON)
class ModelCollectionTests(unittest.TestCase):
    """远程模型收集与测活的并发行为，随界面迁移到 Qt 后仍需保持。"""

    def test_remote_model_lists_are_collected_concurrently(self):
        barrier = Barrier(2, timeout=2)

        class FakeClient:
            def __init__(self, model_id):
                self.model_id = model_id

            def list_models(self):
                barrier.wait()
                return [self.model_id]

        entries, errors = QtMainWindow._collect_models(
            {
                "first": FakeClient("z-model"),
                "second": FakeClient("a-model"),
            }
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            entries,
            [
                {"id": "a-model", "api_key_id": "second"},
                {"id": "z-model", "api_key_id": "first"},
            ],
        )

    def test_probes_are_dispatched_concurrently(self):
        barrier = Barrier(2, timeout=2)

        class FakeClient:
            def __init__(self, reply):
                self.reply = reply

            def probe(self, _model_id):
                barrier.wait()
                return {
                    "ok": True,
                    "status": "可用",
                    "first_ms": 1,
                    "total_ms": 2,
                    "reply": self.reply,
                    "error": "",
                }

        window = QtMainWindow.__new__(QtMainWindow)
        results = window._probe_models(
            {
                "first": FakeClient("one"),
                "second": FakeClient("two"),
            },
            [
                {"id": "model-one", "api_key_id": "first"},
                {"id": "model-two", "api_key_id": "second"},
            ],
        )

        self.assertEqual({entry["id"] for entry, _result in results}, {"model-one", "model-two"})


@unittest.skipUnless(QT_AVAILABLE, QT_SKIP_REASON)
class MinimizeFallbackTests(unittest.TestCase):
    """托盘不可用时应退回任务栏最小化，而不是把窗口藏起来找不回来。"""

    def _window_with_projects(self, directory, count=1):
        path = Path(directory) / "config.json"
        payload = default_store()
        base = dict(payload["projects"][0])
        payload["projects"] = [
            {**base, "id": f"p-{index:03d}", "name": f"项目{index:03d}"} for index in range(1, count + 1)
        ]
        payload["selected_project_id"] = "p-001"
        service = StoreService(b"0123456789abcdef0123456789abcdef", path)
        service.save(payload)
        window = QtMainWindow(service.config_key, data_file=path, usage_file=Path(directory) / "usage.json")
        window.resize(1120, 720)
        window.show()
        _QT_APP.processEvents()
        return window

    def test_minimize_falls_back_to_taskbar_when_tray_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory)
            try:
                window.tray_available = False
                window.minimize_to_tray()
                _QT_APP.processEvents()
                self.assertTrue(window.isMinimized(), "托盘不可用时应最小化到任务栏而不是隐藏窗口")
                self.assertFalse(window.isHidden())
            finally:
                window.close()
                _QT_APP.processEvents()

    def test_minimize_hides_window_when_tray_available(self):
        with tempfile.TemporaryDirectory() as directory:
            window = self._window_with_projects(directory)
            try:
                window.tray_available = True
                window.minimize_to_tray()
                _QT_APP.processEvents()
                self.assertTrue(window.isHidden(), "托盘可用时应隐藏到托盘")
                window.restore_from_tray()
                _QT_APP.processEvents()
                self.assertFalse(window.isHidden())
            finally:
                window.close()
                _QT_APP.processEvents()


if __name__ == "__main__":
    unittest.main()
