# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed

- 远程模型列表恢复右键菜单与 Ctrl+C 复制，菜单包含复制模型名称、添加选中、添加全部。
- 修正「添加选中」按钮因 `QListWidgetItem.row()` 误用抛 `AttributeError`、无法添加模型的问题。
- 模型树恢复 Delete 快捷键与「移除全部模型」菜单项。
- 配置归一化容忍 `null` 与错误类型的字段，畸形请求头行不再导致启动、加载或转发失败。

## [1.2.0] - 2026-10-06

### Added

- 本地中转对话框的启用接口列表支持按项目名搜索过滤，过滤时保留隐藏项目的启用状态。
- 中转配置支持上游 User-Agent 覆盖，留空时保持原有行为。
- 新增无 GUI 中转服务（`python -m ai_probe --serve`）与 Docker 镜像，可由环境变量配置部署参数。
- 新增 GHCR 工作流，推送 `main` 与 `v*` tag 时构建并推送 `amd64` 与 `arm64` 镜像。

### Changed

- 桌面界面从 Tkinter 迁移到 PySide6，系统托盘改用 `QSystemTrayIcon`；配置文件格式保持兼容。
- 存储归一化与加密读写抽离到 `store_service.py`。
- Windows 发布构建改用 Nuitka PySide6 插件，运行依赖移除 pystray。

### Fixed

- `restart_application` 移到无 GUI 的 `process.py`，旧版 Tkinter 界面与相关测试不再因它连带加载 Qt。
- CI 测试任务补装 Qt 运行库，Linux 下 Qt 界面测试不再因缺少 `libEGL.so.1` 而失败；环境缺 Qt 时相关测试跳过而非报错。

## [1.1.3] - 2026-10-02

### Added

- 渠道支持配置模型别名，导入重复渠道时给出提示。
- 中转支持全局系统提示词注入设置，可统一为转发请求附加系统提示词。

### Changed

- 界面应用 Material Design 3 主题，替换原有硬编码配色。

### Fixed

- 系统托盘不可用时，最小化改为回退到任务栏。

## [1.1.2] - 2026-09-05

### Added

- 中转界面新增请求记录：每个转发请求的上游项目、状态、耗时与 Token 用量按日写入 `logs/relay-requests-YYYY-MM-DD.jsonl`，与错误日志同目录、同样自动脱敏。
- 中转配置页新增调试模式开关：完整录制入站请求、上游转发请求与返回报文（含流式内容），密钥类字段录制时即脱敏，单字段上限 256KB。
- 中转配置页新增"打开日志目录"按钮。

### Fixed

- 无协议前缀的代理地址（如 `proxy.example.com:8080`）自动补全 `http://`。

## [1.1.1] - 2026-08-23

### Fixed

- 为可发现模型列表添加横向滚动条，长模型名称可以完整查看。

## [1.1.0] - 2026-08-16

### Added

- 添加项目复制功能。
- 添加可选的 SSL 证书校验跳过配置。
- 添加可开关的中转错误详细日志，并自动脱敏敏感字段。

### Changed

- 优化模型测活的并发与结果展示。
- 完善 Responses 推理摘要的流式协议转换。
- 清理会破坏严格工具调用配对的空 assistant 消息。

## [1.0.0] - 2026-08-09

### Added

- 初始版本：OpenAI 兼容、Responses 和 Anthropic API 测活。
- 多项目、多 API Key 管理。
- 本地配置加密与兼容转发。
