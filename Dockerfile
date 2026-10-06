# AI Probe 本地中转镜像（多阶段构建，amd64 + arm64）
#
# ── 镜像里跑什么 ─────────────────────────────────────────────
# 只跑无 GUI 的中转服务（`python -m ai_probe --serve`）。桌面端是 Windows
# GUI，不进镜像；中转核心（relay/client/protocols/store_service/usage）
# 已确认不依赖 PySide6，所以容器不需要 Qt、X11 或任何系统图形库。
#
# ── 为什么 --no-deps 分开装依赖 ──────────────────────────────
# pyproject.toml 把 PySide6 列为核心依赖，因为桌面端与 GUI 测试需要它。
# 但容器只跑中转：装进来会让镜像多出数百 MB 的 Qt 运行库，而这些库在
# 这里一次都不会被 import。所以先 --no-deps 装包本身（拿到入口点与
# 元数据），再显式安装中转实际用到的两个运行依赖。
# 注意：改动 pyproject.toml 的核心依赖时，这里要同步 —— 新增中转用到的
# 依赖请补进下面的 pip install。
#
# ── 多架构 ───────────────────────────────────────────────────
# 纯 Python，cryptography / requests 都有 amd64 与 arm64 的预编译 wheel，
# 不需要编译工具链；arm64 构建由 buildx 走 QEMU，只用于解包 wheel，开销很小。

FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AI_PROBE_DATA_DIR=/data \
    # 容器里必须监听 0.0.0.0，否则端口只绑在容器回环上、映射不出来。
    AI_PROBE_RELAY_HOST=0.0.0.0

WORKDIR /app

# ca-certificates：请求上游 API 走 HTTPS 需要根证书。
# tzdata：中转日志按本地时区切分文件名（astimezone()），缺了会退回 UTC。
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY ai_probe ./ai_probe

RUN pip install --no-cache-dir --no-deps . \
    && pip install --no-cache-dir "cryptography>=42,<51" "requests[socks]>=2.31,<3"

# 非 root 运行：数据目录要可写（加密配置、用量统计、logs/）。
RUN useradd --create-home --uid 10000 app \
    && mkdir -p /data \
    && chown -R app:app /data
USER app

VOLUME ["/data"]
EXPOSE 8040

# 探活用服务自带的 --healthcheck 子命令，不依赖 curl，也不用在 Dockerfile 里
# 拼 python -c 的转义。判定标准是「有没有 HTTP 回应」，401 也算健康。
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-m", "ai_probe", "--healthcheck"]

ENTRYPOINT ["python", "-m", "ai_probe", "--serve"]
