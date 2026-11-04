# AI Probe

[![CI](https://github.com/junjundesk/ai-probe/actions/workflows/ci.yml/badge.svg)](https://github.com/junjundesk/ai-probe/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)

面向 OpenAI 兼容、Responses 和 Anthropic API 的 PySide6 桌面测活工具，内置多项目管理、模型发现、请求头/代理配置、加密本地配置和本地兼容转发。

## 功能

- 多项目与多 API Key 管理
- Chat、Responses、Anthropic 协议转换与本地兼容转发
- 模型发现、请求头、代理和自定义提示词
- 截图模式：一键把项目名称与 API 密钥打码（含本地中转窗口），适合截图分享
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
| `AI_PROBE_RELAY_USER_AGENT` | 覆盖转发到上游的 User-Agent；留空时原样转发调用方的 User-Agent |
| `AI_PROBE_LOG_LEVEL` | 日志级别，默认 `INFO` |

这些变量只作用于运行期，不会写回加密配置，因此数据目录可以只读挂载。

镜像由 GitHub Actions 在推 `main` 与 `v*` tag 时构建并推送到 GHCR（`amd64` 与 `arm64`）。GHCR 新包默认私有，需要公开拉取时到包的 settings 里把 visibility 改成 Public。

## 验证

```powershell
python -m ai_probe --self-test
python -m unittest discover -s tests -v
```

## 桌面显示兼容性

- 桌面界面使用统一的浅色配色，避免系统深色主题的白色文字与列表、模型树、日志区的白色背景混用。主窗口、独立中转窗口和启动密码框使用相同的显示策略。
- 窗口大小按当前屏幕的可用区域（Qt 逻辑像素）设置；小屏幕或高 DPI 缩放下通过滚动访问内容，不要求关闭系统缩放。
- Windows CI 使用原生 `windows` 平台插件，覆盖最低声明版本 PySide6 6.7.0，以及当前支持的最新版 PySide6 的额外 1.5× / 2× Qt 缩放（`QT_SCALE_FACTOR`，与系统缩放相乘）。Linux CI 继续使用 `offscreen`。这些自动化检查不能代替所有显卡、远程桌面和 Windows 版本的实机验证。

如果升级后仍出现空白区域，请提供 Windows 版本、显示分辨率与缩放比例、系统深浅色主题，以及是源码启动还是打包 EXE；截图前请开启「截图模式」，不要提供 API 密钥或未脱敏的配置。源码环境可单独运行显示兼容性回归：

```powershell
python -m unittest discover -s tests -p test_ui_compat.py -v
```

在 Windows 上还可显式启用原生平台与额外缩放，检查真实窗口绘制（以下环境变量仅影响当前终端及其启动的程序）：

```powershell
$env:QT_QPA_PLATFORM = "windows"
$env:QT_SCALE_FACTOR = "1.5"
python -m unittest discover -s tests -p test_ui_compat.py -v
```

打包 EXE 的用户需要使用包含本次修复的重新构建版本，仅更新源码不会改变旧 EXE。

## 项目结构

```text
ai_probe/
  config.py       配置加密、路径和启动密钥
  client.py       上游 API 客户端与测活
  projects.py     项目和多 API Key 数据模型
  masking.py      截图模式的名称与密钥打码
  protocols/      Chat、Responses、Anthropic 协议转换
  relay.py        本地兼容转发服务
  relay_service.py 无 GUI 中转服务进程（容器部署入口）
  usage.py        本地用量统计
  qt_app.py       PySide6 主窗口、模型工作区与本地中转界面
  qt_compat.py    统一浅色配色与小屏 / 高 DPI 窗口适配
  store_service.py 无 GUI 的配置加载、归一化和加密存储
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
