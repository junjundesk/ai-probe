import unittest
from threading import Barrier
from unittest.mock import Mock, patch

from ai_probe.ui.models_mixin import ModelsMixin


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
