# Changelog

本项目遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

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
