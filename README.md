# AI Probe

[![CI](https://github.com/junjundesk/ai-probe/actions/workflows/ci.yml/badge.svg)](https://github.com/junjundesk/ai-probe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)

面向 OpenAI 兼容、Responses 和 Anthropic API 的桌面测活工具，内置多项目管理、模型发现、请求头/代理配置、加密本地配置和本地兼容转发。

## 功能

- 多项目与多 API Key 管理
- Chat、Responses、Anthropic 协议转换与本地兼容转发
- 模型发现、请求头、代理和自定义提示词
- AES 加密的本地配置与用量统计
- 无 GUI 的核心自检与自动化回归测试

## 快速开始

要求：Python 3.10 或更高版本。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python app.py
```

也可以运行：

```powershell
python -m ai_probe
```

安装后会提供 Windows GUI 启动命令：`ai-probe`。

## 验证

```powershell
python -m ai_probe --self-test
python -m unittest discover -s tests -v
```

## 项目结构

```text
ai_probe/
  config.py       配置加密、路径和启动密钥
  client.py       上游 API 客户端与测活
  projects.py     项目和多 API Key 数据模型
  protocols/      Chat、Responses、Anthropic 协议转换
  relay.py        本地兼容转发服务
  usage.py        本地用量统计
  ui/             Tkinter 主窗口及按职责拆分的 UI mixin
  entry.py        应用启动入口
app.py            兼容原有启动方式的薄入口
tests/            核心回归测试
```

## 本地数据

从源码目录启动时，配置数据保留在项目根目录，以兼容旧版：

- `ai_probe_projects.json`
- `ai_probe_config.key`
- `ai_probe_usage.json`

这些文件已被 `.gitignore` 排除，不会上传到 GitHub。需要将数据放到其他目录时，启动前设置 `AI_PROBE_DATA_DIR`。

```powershell
$env:AI_PROBE_DATA_DIR = "D:\AI-Probe-Data"
python -m ai_probe
```

## 开发

- 核心逻辑不依赖 GUI，可通过 `--self-test` 和 `unittest` 快速验证。
- 协议转换和转发逻辑位于 `ai_probe/protocols/` 与 `ai_probe/relay.py`；修改后请运行完整测试。
- 使用 `ruff` 保持代码风格：`ruff check .`、`ruff format --check .`
- GitHub Actions 会在 Python 3.10 至 3.13 上执行 lint、核心回归测试和构建检查。

## 安全

发现安全问题请通过 [SECURITY.md](SECURITY.md) 中描述的私有方式报告，不要公开提交漏洞细节。

## 许可证

本项目使用 [MIT License](LICENSE)。
