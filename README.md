# AI Probe

[![CI](https://github.com/junjundesk/ai-probe/actions/workflows/ci.yml/badge.svg)](https://github.com/junjundesk/ai-probe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)

面向 OpenAI 兼容、Responses 和 Anthropic API 的 PySide6 桌面测活工具，内置多项目管理、模型发现、请求头/代理配置、加密本地配置和本地兼容转发。

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

## Docker 部署（无 GUI 中转）

镜像里跑的是无图形界面的中转服务（`python -m ai_probe --serve`），适合放在服务器或 NAS 上长期运行；桌面端仍在 Windows 上使用。

配置与密钥从挂载的数据目录读取，所以先在桌面端配置好接口与模型，再把数据目录挂进容器：

```bash
docker run -d --name ai-probe \
  -p 8040:8040 \
  -e AI_PROBE_CONFIG_PASSWORD='你的配置密码' \
  -e AI_PROBE_RELAY_KEY='客户端访问密钥' \
  -v /path/to/data:/data \
  ghcr.io/junjundesk/ai-probe:latest
```

数据目录里需要包含 `ai_probe_projects.json`（以及可选的 `ai_probe_config.key`）。启动后客户端 Base URL 填 `http://<服务器>:8040/v1`。

可用的环境变量：

| 变量 | 作用 |
| --- | --- |
| `AI_PROBE_CONFIG_PASSWORD` | 解密配置的密码；不设置时回退到数据目录里的 `ai_probe_config.key` |
| `AI_PROBE_RELAY_HOST` | 监听地址，镜像内默认 `0.0.0.0` |
| `AI_PROBE_RELAY_PORT` | 监听端口，默认 `8040` |
| `AI_PROBE_RELAY_KEY` | 客户端访问密钥，留空表示不校验 |
| `AI_PROBE_RELAY_USER_AGENT` | 覆盖转发到上游的 User-Agent |
| `AI_PROBE_LOG_LEVEL` | 日志级别，默认 `INFO` |

这些变量只作用于运行期，不会写回加密配置，因此数据目录可以只读挂载。

镜像由 GitHub Actions 在推 `main` 与 `v*` tag 时构建并推送到 GHCR（`amd64` 与 `arm64`）。GHCR 新包默认私有，需要公开拉取时到包的 settings 里把 visibility 改成 Public。

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
  relay_service.py 无 GUI 中转服务进程（容器部署入口）
  usage.py        本地用量统计
  qt_app.py       PySide6 主窗口、模型工作区与本地中转界面
  store_service.py 无 GUI 的配置加载、归一化和加密存储
  ui/             旧版 Tkinter UI 兼容代码（不由新入口加载）
  entry.py        应用启动入口
app.py            兼容原有启动方式的薄入口
Dockerfile        中转镜像构建（不含 GUI）
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

## 许可证

本项目使用 [MIT License](LICENSE)。
