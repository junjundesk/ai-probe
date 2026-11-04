import unittest

from ai_probe.masking import KEY_MASK, ScreenshotMasker


def _project(project_id, name, key):
    return {
        "id": project_id,
        "name": name,
        "api_keys": [{"id": "default", "name": "默认", "value": key}],
    }


class ScreenshotMaskerTests(unittest.TestCase):
    """打码表本身的单元测试，不依赖 Qt。"""

    def setUp(self):
        self.projects = [
            _project("p1", "Alpha 生产", "sk-alpha-0000000001"),
            _project("p2", "Alpha", "sk-beta-0000000002"),
        ]
        self.masker = ScreenshotMasker(True)
        self.masker.rebuild(self.projects)

    def test_project_labels_become_stable_aliases(self):
        self.assertEqual(self.masker.project_label(self.projects[0]), "项目 #1")
        self.assertEqual(self.masker.project_label(self.projects[1]), "项目 #2")
        # 重复重建后别名不变，截图前后能对应到同一行。
        self.masker.rebuild(self.projects)
        self.assertEqual(self.masker.project_label(self.projects[1]), "项目 #2")

    def test_mask_replaces_longest_name_first(self):
        text = "Alpha 生产 与 Alpha 都在用 sk-alpha-0000000001 和 sk-beta-0000000002"
        masked = self.masker.mask(text)
        self.assertEqual(masked, f"项目 #1 与 项目 #2 都在用 {KEY_MASK} 和 {KEY_MASK}")

    def test_mask_keeps_existing_aliases(self):
        # 别名里含有“项目”字样，二次打码不能把已经打好的别名改错。
        text = "已定位到渠道：项目 #2"
        self.assertEqual(self.masker.mask(text), text)
        self.assertEqual(self.masker.mask(self.masker.mask(text)), text)

    def test_short_values_are_left_alone(self):
        # 过短的值当密钥替换会把普通文案打碎，只屏蔽足够长的密钥。
        masker = ScreenshotMasker(True)
        masker.rebuild([_project("p1", "渠道", "abc")])
        self.assertEqual(masker.mask("abc 只是普通单词"), "abc 只是普通单词")

    def test_key_label_hides_tail(self):
        key = self.projects[0]["api_keys"][0]
        self.assertEqual(self.masker.key_label(key), f"默认 · {KEY_MASK}")
        unnamed = {"id": "k", "name": "", "value": "sk-something-0000"}
        self.assertEqual(self.masker.key_label(unnamed), KEY_MASK)

    def test_disabled_masker_returns_real_values(self):
        self.masker.enabled = False
        self.assertEqual(self.masker.project_label(self.projects[0]), "Alpha 生产")
        self.assertEqual(self.masker.key_label(self.projects[0]["api_keys"][0]), "默认 · ...0001")
        self.assertEqual(self.masker.mask("Alpha 生产"), "Alpha 生产")


if __name__ == "__main__":
    unittest.main()
