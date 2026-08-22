import unittest
from unittest.mock import Mock, patch

from ai_probe.ui.models_mixin import ModelsMixin


class ModelResultDisplayTests(unittest.TestCase):
    def test_probe_reply_is_not_displayed(self):
        mixin = ModelsMixin()
        mixin.model_tree_items = {"model": "row"}
        mixin.model_tree = Mock()
        mixin.model_tree.exists.return_value = True
        project = {
            "api_key": "",
            "models": [{"id": "model", "status": "可用", "reply": "旧返回正文", "error": ""}],
        }
        mixin._project = lambda: project
        mixin._selected_model_ids = lambda: ["model"]

        mixin._update_model_row(project["models"][0])

        self.assertEqual(mixin.model_tree.item.call_args.kwargs["values"][-1], "")
        with patch("ai_probe.ui.models_mixin.messagebox.showinfo") as showinfo:
            mixin._show_model_detail()
        showinfo.assert_not_called()

        project["models"][0]["status"] = "不可用"
        project["models"][0]["error"] = "连接失败"
        mixin._update_model_row(project["models"][0])
        self.assertEqual(mixin.model_tree.item.call_args.kwargs["values"][-1], "连接失败")
        with patch("ai_probe.ui.models_mixin.messagebox.showinfo") as showinfo:
            mixin._show_model_detail()
        showinfo.assert_called_once_with("model", "连接失败")


if __name__ == "__main__":
    unittest.main()

