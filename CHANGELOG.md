# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
