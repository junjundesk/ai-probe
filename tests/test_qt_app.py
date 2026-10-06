import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ai_probe.qt_app import QtMainWindow
from ai_probe.store_service import StoreService, default_store, normalize_store

_QT_APP = QApplication.instance() or QApplication([])


class QtApplicationSmokeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
