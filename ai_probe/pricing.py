"""本地中转模型定价与花费计算。"""

from __future__ import annotations

import fnmatch
import uuid

DEFAULT_CURRENCY = "$"
PRICE_FIELDS = ("input", "cached_input", "output")


def default_pricing() -> dict:
    return {"currency": DEFAULT_CURRENCY, "rules": []}


def _price(value, allow_none: bool = False):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None if allow_none else 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None if allow_none else 0.0
    if number != number or number < 0 or number == float("inf"):
        return None if allow_none else 0.0
    return number


def _multiplier(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    if number != number or number <= 0 or number == float("inf"):
        return 1.0
    return number


def normalize_rule(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    model = str(raw.get("model") or "").strip()
    if not model:
        return None
    return {
        "id": str(raw.get("id") or "").strip() or uuid.uuid4().hex,
        "project_id": str(raw.get("project_id") or "").strip(),
        "model": model,
        "input": _price(raw.get("input"), allow_none=True),
        "cached_input": _price(raw.get("cached_input"), allow_none=True),
        "output": _price(raw.get("output"), allow_none=True),
        "multiplier": _multiplier(raw.get("multiplier", 1.0)),
    }


def normalize_pricing(raw) -> dict:
    if not isinstance(raw, dict):
        return default_pricing()
    currency = str(raw.get("currency") or "").strip()[:8] or DEFAULT_CURRENCY
    rules = []
    seen = set()
    for item in raw.get("rules") if isinstance(raw.get("rules"), list) else []:
        rule = normalize_rule(item)
        if rule is None or rule["id"] in seen:
            continue
        seen.add(rule["id"])
        rules.append(rule)
    return {"currency": currency, "rules": rules}


def is_priced(rule) -> bool:
    """输入价与输出价都未填写的规则只是占位，不参与计价。"""
    return isinstance(rule, dict) and (
        _price(rule.get("input"), allow_none=True) is not None
        or _price(rule.get("output"), allow_none=True) is not None
    )


def find_rule(pricing, project_id, model) -> dict | None:
    """按 项目精确 > 项目通配 > 全局精确 > 全局通配 的顺序查找定价。"""
    rules = (pricing or {}).get("rules") or []
    model_key = str(model or "").casefold()
    project_id = str(project_id or "")
    best = None
    best_rank = None
    for rule in rules:
        if not is_priced(rule):
            continue
        rule_project = str(rule.get("project_id") or "")
        if rule_project and rule_project != project_id:
            continue
        pattern = str(rule.get("model") or "").strip().casefold()
        if not pattern:
            continue
        if pattern == model_key:
            exact = True
        elif any(char in pattern for char in "*?[") and fnmatch.fnmatchcase(model_key, pattern):
            exact = False
        else:
            continue
        rank = (0 if rule_project else 2) + (0 if exact else 1)
        if best_rank is None or rank < best_rank:
            best, best_rank = rule, rank
    return best


def cost(rule, input_tokens, output_tokens, cached_tokens) -> float:
    input_tokens = max(0, int(input_tokens or 0))
    output_tokens = max(0, int(output_tokens or 0))
    cached_tokens = min(max(0, int(cached_tokens or 0)), input_tokens)
    input_price = _price(rule.get("input"))
    cached_price = _price(rule.get("cached_input"), allow_none=True)
    if cached_price is None:
        cached_price = input_price
    output_price = _price(rule.get("output"))
    base = (input_tokens - cached_tokens) * input_price + cached_tokens * cached_price + output_tokens * output_price
    return base / 1_000_000 * _multiplier(rule.get("multiplier", 1.0))


def summarize(snapshot, pricing) -> dict:
    """计算今日统计每行花费；未定价的行为 None。"""
    rows = {}
    total = 0.0
    unpriced = 0
    for entry_key, row in ((snapshot or {}).get("models") or {}).items():
        rule = find_rule(pricing, row.get("project_id"), row.get("model"))
        if rule is None:
            rows[entry_key] = None
            unpriced += 1
            continue
        value = cost(rule, row.get("input_tokens"), row.get("output_tokens"), row.get("cached_tokens"))
        rows[entry_key] = value
        total += value
    return {"total": total, "rows": rows, "unpriced": unpriced}


def format_cost(value, currency: str = DEFAULT_CURRENCY) -> str:
    value = float(value or 0)
    if 0 < value < 0.01:
        return f"{currency}{value:.4f}"
    return f"{currency}{value:,.2f}"
