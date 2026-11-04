"""截图模式的打码：把项目名与 API 密钥换成稳定别名。

界面只负责“显示什么”，所有可能露出敏感内容的文本都从这里过一遍：
项目名映射成按顺序编号的别名（项目 #1、项目 #2……），密钥统一换成圆点串。
别名与项目一一对应，同一张截图里的多行仍能互相对照，但看不到真实内容。
"""

from __future__ import annotations

import re

from .projects import _project_keys, api_key_label

KEY_MASK = "••••••••"
ALIAS_PREFIX = "项目 #"
UNKNOWN_ALIAS = "项目 #?"
# 过短的值当密钥替换容易把普通文案打碎（例如密钥本身就是 "test"），只屏蔽足够长的值。
MIN_SECRET_LENGTH = 6

# 别名自身也要能从二次替换里排除：项目名里若含有别的项目名，替换顺序会影响结果。
_ALIAS_PATTERN = re.compile(r"项目 #\d+")
_PLACEHOLDER_PATTERN = re.compile("\x00(\\d+)\x00")


class ScreenshotMasker:
    """截图模式的名称与密钥打码表。"""

    def __init__(self, enabled: bool = False) -> None:
        self.enabled = bool(enabled)
        self._alias_by_id: dict[str, str] = {}
        self._alias_by_name: dict[str, str] = {}
        self._replacements: dict[str, str] = {}
        self._pattern: re.Pattern[str] | None = None

    def rebuild(self, projects) -> None:
        """按当前项目顺序重建别名表；项目顺序不变时别名保持不变。"""
        self._alias_by_id = {}
        self._alias_by_name = {}
        pairs: dict[str, str] = {}
        for index, project in enumerate(projects or [], start=1):
            if not isinstance(project, dict):
                continue
            alias = f"{ALIAS_PREFIX}{index}"
            project_id = str(project.get("id") or "").strip()
            if project_id:
                self._alias_by_id[project_id] = alias
            name = str(project.get("name") or "").strip()
            if name:
                self._alias_by_name.setdefault(name, alias)
                pairs.setdefault(name, alias)
            for key in _project_keys(project):
                value = str(key.get("value") or "").strip()
                if len(value) >= MIN_SECRET_LENGTH:
                    pairs.setdefault(value, KEY_MASK)
        # 长的先替换，避免短名先把长名切碎（例如 “Alpha” 与 “Alpha 2”）。
        ordered = sorted(pairs.items(), key=lambda item: len(item[0]), reverse=True)
        self._replacements = dict(ordered)
        self._pattern = re.compile("|".join(re.escape(text) for text, _ in ordered)) if ordered else None

    def project_label(self, project) -> str:
        """侧栏、中转列表等处的项目名；开启打码时返回稳定别名。"""
        project = project if isinstance(project, dict) else {}
        name = str(project.get("name") or "").strip() or "未命名项目"
        if not self.enabled:
            return name
        project_id = str(project.get("id") or "").strip()
        return self._alias_by_id.get(project_id) or self._alias_by_name.get(name) or UNKNOWN_ALIAS

    def key_label(self, key) -> str:
        """模型列表里的密钥列；开启打码时只留密钥名，尾号一并抹掉。"""
        key = key if isinstance(key, dict) else {}
        if not self.enabled:
            return api_key_label(key)
        name = str(key.get("name") or "").strip()
        return f"{name} · {KEY_MASK}" if name else KEY_MASK

    def mask(self, text) -> str:
        """抹掉任意文案里的项目名与密钥，用于日志、上游返回与状态提示。"""
        value = "" if text is None else str(text)
        if not self.enabled or not value or self._pattern is None:
            return value
        # 先把已经打码的别名换成占位符，避免别名里恰好含有某个项目名而被二次替换。
        stashed: list[str] = []

        def stash(match: re.Match[str]) -> str:
            stashed.append(match.group(0))
            return f"\x00{len(stashed) - 1}\x00"

        value = _ALIAS_PATTERN.sub(stash, value)
        value = self._pattern.sub(lambda match: self._replacements[match.group(0)], value)
        if stashed:
            value = _PLACEHOLDER_PATTERN.sub(lambda match: stashed[int(match.group(1))], value)
        return value

    def __bool__(self) -> bool:
        return self.enabled
