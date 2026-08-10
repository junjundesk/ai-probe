import unittest

from ai_probe.self_test import self_test


class CoreRegressionTests(unittest.TestCase):
    def test_existing_core_self_test(self):
        self_test()


if __name__ == "__main__":
    unittest.main()
