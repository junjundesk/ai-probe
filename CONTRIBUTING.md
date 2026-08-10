# 贡献指南

感谢你愿意参与 AI Probe 的开发。请先阅读本指南，保证提交内容与项目现有规范一致。

## 开发环境

要求 Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[dev]
```

## 代码规范

- 使用 `ruff` 做静态检查：`ruff check .`
- 使用 `ruff format --check .` 检查格式，或运行 `ruff format .` 自动格式化。
- 核心逻辑不依赖 GUI；协议转换、转发和配置逻辑请保持可单测。
- 新增公开函数或数据模型时，在 README 或模块 docstring 中补充说明。

## 提交规范

提交信息使用英文，并采用 Conventional Commits 风格：

```text
feat: add multi-key probe scheduling
fix: handle empty SSE usage payload
test: cover responses stream normalization
docs: document local data directory
```

常见类型包括 `feat`、`fix`、`docs`、`test`、`refactor`、`chore`。

## 测试

提交前至少运行：

```powershell
python -m unittest discover -s tests -v
python -m ai_probe --self-test
```

涉及协议转换或本地转发时，请补充对应的回归测试。

## Pull Request 流程

1. 从 `main` 新建分支，分支名使用 `feat/`、`fix/`、`docs/` 等前缀。
2. 保持改动范围聚焦，避免夹带无关重构。
3. 本地通过 lint 和测试后提交。
4. 在 Pull Request 描述中说明变更内容、验证方式和影响范围。

## 问题报告

- Bug 请使用 `.github/ISSUE_TEMPLATE/bug_report.yml` 模板。
- 新功能建议请使用 `.github/ISSUE_TEMPLATE/feature_request.yml` 模板。
- 安全问题请遵循 [SECURITY.md](SECURITY.md)，不要公开提交漏洞细节。
