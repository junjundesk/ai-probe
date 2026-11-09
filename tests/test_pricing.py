import unittest

from ai_probe.pricing import cost, find_rule, format_cost, normalize_pricing, summarize
from ai_probe.store_service import default_store, normalize_store


class PricingTests(unittest.TestCase):
    def test_normalize_is_tolerant(self):
        self.assertEqual(normalize_pricing(None), {"currency": "$", "rules": []})
        pricing = normalize_pricing(
            {
                "currency": " ¥ ",
                "rules": [
                    "bad",
                    {"model": "  "},
                    {"id": "a", "model": " gpt-x ", "input": "2.5", "output": -1, "multiplier": 0},
                    {"id": "a", "model": "dup", "input": 1},
                ],
            }
        )
        self.assertEqual(pricing["currency"], "¥")
        self.assertEqual(len(pricing["rules"]), 1)
        rule = pricing["rules"][0]
        self.assertEqual(rule["model"], "gpt-x")
        self.assertEqual(rule["input"], 2.5)
        self.assertIsNone(rule["output"])
        self.assertIsNone(rule["cached_input"])
        self.assertEqual(rule["multiplier"], 1.0)

    def test_rule_priority_and_wildcards(self):
        pricing = normalize_pricing(
            {
                "rules": [
                    {"id": "global-wild", "model": "gpt-*", "input": 1},
                    {"id": "global", "model": "GPT-5", "input": 2},
                    {"id": "project-wild", "project_id": "p", "model": "gpt-*", "input": 3},
                    {"id": "project", "project_id": "p", "model": "gpt-5", "input": 4},
                    {"id": "placeholder", "project_id": "q", "model": "gpt-5"},
                ]
            }
        )
        self.assertEqual(find_rule(pricing, "p", "gpt-5")["id"], "project")
        self.assertEqual(find_rule(pricing, "p", "gpt-4o")["id"], "project-wild")
        self.assertEqual(find_rule(pricing, "other", "gpt-5")["id"], "global")
        self.assertEqual(find_rule(pricing, "q", "gpt-5")["id"], "global")
        self.assertEqual(find_rule(pricing, "other", "gpt-4o")["id"], "global-wild")
        self.assertIsNone(find_rule(pricing, "p", "claude"))

    def test_cost_formula_with_cache_price_and_multiplier(self):
        rule = {"input": 2, "cached_input": 0.5, "output": 10, "multiplier": 1.5}
        # (600k*2 + 400k*0.5 + 100k*10)/1M * 1.5
        self.assertAlmostEqual(cost(rule, 1_000_000, 100_000, 400_000), (1.2 + 0.2 + 1.0) * 1.5)
        no_cache_price = {"input": 2, "output": 10}
        self.assertAlmostEqual(cost(no_cache_price, 1_000_000, 0, 400_000), 2.0)
        self.assertAlmostEqual(cost(no_cache_price, 100, 0, 1_000), 0.0002)

    def test_summarize_counts_unpriced_rows(self):
        snapshot = {
            "models": {
                "p\u0001a": {"project_id": "p", "model": "a", "input_tokens": 1_000_000, "output_tokens": 0},
                "p\u0001b": {"project_id": "p", "model": "b", "input_tokens": 10, "output_tokens": 0},
            }
        }
        result = summarize(snapshot, {"rules": [{"model": "a", "input": 3}]})
        self.assertAlmostEqual(result["total"], 3.0)
        self.assertEqual(result["unpriced"], 1)
        self.assertIsNone(result["rows"]["p\u0001b"])

    def test_format_cost(self):
        self.assertEqual(format_cost(0), "$0.00")
        self.assertEqual(format_cost(0.00123, "¥"), "¥0.0012")
        self.assertEqual(format_cost(1234.5), "$1,234.50")

    def test_store_without_pricing_is_normalized(self):
        store = default_store()
        store["relay"].pop("pricing")
        normalized = normalize_store(store)
        self.assertEqual(normalized["relay"]["pricing"], {"currency": "$", "rules": []})


if __name__ == "__main__":
    unittest.main()
