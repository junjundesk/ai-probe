import json
import unittest
from threading import Barrier
from unittest.mock import Mock, patch

from ai_probe.projects import new_project
from ai_probe.ui.models_mixin import ModelsMixin
from ai_probe.ui.projects_mixin import ProjectsMixin


class ChannelCopyTests(unittest.TestCase):
    def test_context_channel_is_copied_as_newapi_json(self):
        mixin = ProjectsMixin()
        mixin.context_project_id = "project"
        mixin._project = lambda project_id=None: {
            "id": project_id or "project",
            "name": "测试渠道",
            "base_url": "https://example.test/v1",
            "api_key": "sk-test",
        }
        mixin._commit_form = Mock()
        mixin._log = Mock()
        mixin.status = Mock()
        mixin.root = Mock()

        mixin._copy_context_channel()

        copied = mixin.root.clipboard_append.call_args.args[0]
        self.assertEqual(
            json.loads(copied),
            {"_type": "newapi_channel_conn", "key": "sk-test", "url": "https://example.test/v1"},
        )
        mixin.root.clipboard_clear.assert_called_once_with()
        mixin.root.update_idletasks.assert_called_once_with()


class ProjectSearchBehaviorTests(unittest.TestCase):
    class _FakeSearch:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    def test_search_change_refreshes_without_reloading_form(self):
        mixin = ProjectsMixin()
        calls = []
        mixin._refresh_project_list = lambda load_form=True: calls.append(load_form)
        mixin._project_search_changed()
        self.assertEqual(calls, [False])

    def test_main_list_filters_case_insensitively(self):
        mixin = ProjectsMixin()
        mixin.project_list = Mock()
        mixin.visible_project_ids = []
        mixin.project_search = self._FakeSearch("BETA")
        alpha = new_project("Alpha")
        beta = new_project("Beta")
        mixin.store = {"projects": [alpha, beta]}
        mixin.current_id = alpha["id"]
        mixin.updating_projects = False
        mixin._load_current_project = Mock()

        mixin._refresh_project_list(load_form=False)

        self.assertEqual(mixin.visible_project_ids, [beta["id"]])
        mixin._load_current_project.assert_not_called()


class ModelResultDisplayTests(unittest.TestCase):
    def test_probe_reply_is_displayed_and_errors_remain_visible(self):
        mixin = ModelsMixin()
        mixin.model_tree_items = {"model": "row"}
        mixin.model_tree = Mock()
        mixin.model_tree.exists.return_value = True
        project = {
            "api_key": "",
            "models": [{"id": "model", "status": "可用", "reply": "返回正文", "error": ""}],
        }
        mixin._project = lambda: project
        mixin._selected_model_ids = lambda: ["model"]

        mixin._update_model_row(project["models"][0])

        self.assertEqual(mixin.model_tree.item.call_args.kwargs["values"][-1], "返回正文")
        with patch("ai_probe.ui.models_mixin.messagebox.showinfo") as showinfo:
            mixin._show_model_detail()
        showinfo.assert_called_once_with("model", "返回正文")

        project["models"][0]["status"] = "不可用"
        project["models"][0]["error"] = "连接失败"
        mixin._update_model_row(project["models"][0])
        self.assertEqual(mixin.model_tree.item.call_args.kwargs["values"][-1], "连接失败")
        with patch("ai_probe.ui.models_mixin.messagebox.showinfo") as showinfo:
            mixin._show_model_detail()
        showinfo.assert_called_once_with("model", "连接失败")

    def test_context_menu_copies_full_probe_reply_or_error(self):
        mixin = ModelsMixin()
        mixin.context_model_id = "model"
        mixin.root = Mock()
        mixin.status = Mock()
        project = {
            "models": [{"id": "model", "status": "可用", "reply": "完整返回内容\n第二行", "error": ""}],
        }
        mixin._project = lambda: project

        mixin._copy_context_model_reply()

        self.assertEqual(mixin.root.clipboard_append.call_args.args[0], "完整返回内容\n第二行")
        mixin.root.clipboard_clear.assert_called_once_with()
        mixin.root.update_idletasks.assert_called_once_with()

        mixin.root.reset_mock()
        project["models"][0].update({"status": "不可用", "reply": "", "error": "完整错误信息"})
        mixin._copy_context_model_reply()

        self.assertEqual(mixin.root.clipboard_append.call_args.args[0], "完整错误信息")


class ModelCollectionConcurrencyTests(unittest.TestCase):
    def test_remote_model_lists_are_collected_concurrently(self):
        barrier = Barrier(2, timeout=1)

        class FakeClient:
            def __init__(self, model_id):
                self.model_id = model_id

            def list_models(self):
                barrier.wait()
                return [self.model_id]

        entries, errors = ModelsMixin()._collect_remote_models(
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
        barrier = Barrier(2, timeout=1)

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

        mixin = ModelsMixin()
        mixin._post = Mock()
        results = mixin._run_probes(
            {
                "first": FakeClient("one"),
                "second": FakeClient("two"),
            },
            [
                {"id": "model-one", "api_key_id": "first"},
                {"id": "model-two", "api_key_id": "second"},
            ],
            "project",
            progress_label="测试模型",
            update_existing=False,
        )

        self.assertEqual({model_id for model_id, _result in results}, {"model-one", "model-two"})


if __name__ == "__main__":
    unittest.main()
