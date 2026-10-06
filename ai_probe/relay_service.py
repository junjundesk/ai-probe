"""无 GUI 的中转服务进程，供容器与服务器部署使用。

桌面端（``qt_app.py``）与轻量模式（``relay_only.py``）都依赖 PySide6；
容器里不需要图形界面，所以这里只复用与 GUI 无关的 ``RelayServer``、
``StoreService`` 和 ``UsageStats``，用环境变量完成部署期配置。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from .config import (
    CONFIG_KEY_FILE,
    DATA_FILE,
    USAGE_FILE,
    _derive_config_key,
    decrypt_config,
    load_or_create_config_key,
    save_config_key,
)
from .relay import RelayServer
from .store_service import StoreService
from .usage import UsageStats

LOGGER = logging.getLogger("ai_probe.relay")

# 环境变量到 relay 配置字段的映射。这些覆盖只作用于运行期，不写回加密配置，
# 因此数据目录可以只读挂载，桌面端的配置也不会被容器改坏。
_ENV_OVERRIDES = {
    "AI_PROBE_RELAY_HOST": "host",
    "AI_PROBE_RELAY_PORT": "port",
    "AI_PROBE_RELAY_KEY": "api_key",
    "AI_PROBE_RELAY_USER_AGENT": "user_agent",
}


def resolve_config_key() -> bytes | None:
    """解析配置密钥，不弹任何交互界面。

    优先使用 ``AI_PROBE_CONFIG_PASSWORD``（容器部署时最方便），否则回退到
    数据目录里已有的 ``ai_probe_config.key``。两者都不可用时返回 None。
    """
    password = os.environ.get("AI_PROBE_CONFIG_PASSWORD", "").strip()
    if not password:
        return load_or_create_config_key()
    key = _derive_config_key(password)
    if DATA_FILE.exists():
        # 密码不对时立刻报错，避免带着错误的密钥启动出一个空路由的中转。
        decrypt_config(DATA_FILE.read_text(encoding="utf-8"), key, allow_legacy=False)
    elif not CONFIG_KEY_FILE.exists():
        save_config_key(key)
    return key


def apply_env_overrides(store: dict) -> dict:
    """把部署期环境变量叠加到 relay 配置上，返回同一个 relay 字典。"""
    relay = store.setdefault("relay", {})
    for env_name, field in _ENV_OVERRIDES.items():
        raw = os.environ.get(env_name, "").strip()
        if not raw:
            continue
        if field == "port":
            try:
                port = int(raw)
            except ValueError as exc:
                raise ValueError(f"{env_name} 必须是数字：{raw}") from exc
            if not 1 <= port <= 65535:
                raise ValueError(f"{env_name} 必须在 1 到 65535 之间：{port}")
            relay[field] = port
        else:
            relay[field] = raw
    return relay


def enabled_counts(store: dict) -> tuple[int, int]:
    """返回 (已启用接口数, 已启用模型数)，用于启动前检查路由是否为空。"""
    relay = store.get("relay", {})
    enabled = set(relay.get("project_ids", []))
    projects = [item for item in store.get("projects", []) if item.get("id") in enabled]
    return len(projects), sum(len(item.get("models", [])) for item in projects)


class RelayService:
    """``RelayServer`` 所需回调的无 GUI 宿主实现。"""

    def __init__(self, config_key: bytes, data_file: Path = DATA_FILE, usage_file: Path = USAGE_FILE):
        self.store_service = StoreService(config_key, data_file)
        self.store = self.store_service.load()
        self.usage_stats = UsageStats(usage_file)
        self.relay_server: RelayServer | None = None

    def _post(self, callback, *args):
        callback(*args)

    @staticmethod
    def _log(message):
        LOGGER.info("%s", message)

    def record_relay_usage(self, project, model, input_tokens, output_tokens, cached_tokens):
        self.usage_stats.record(project, model, input_tokens, output_tokens, cached_tokens)

    def start(self) -> RelayServer:
        relay = self.store["relay"]
        self.relay_server = RelayServer(
            self,
            relay["host"],
            relay["port"],
            relay["api_key"],
            relay["error_logging_enabled"],
            relay["request_logging_enabled"],
            relay["request_debug_capture"],
            system_prompt=relay["system_prompt"],
            append_user_prompt=relay["append_user_prompt"],
            user_agent=relay["user_agent"],
        )
        self.relay_server.start()
        return self.relay_server

    def stop(self):
        if self.relay_server:
            self.relay_server.stop()
            self.relay_server = None
        self.usage_stats.save()


def _configure_logging():
    logging.basicConfig(
        level=os.environ.get("AI_PROBE_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def healthcheck(timeout: float = 3.0) -> bool:
    """探测本机中转是否已就绪，供容器 HEALTHCHECK 使用。

    判定标准是「有没有 HTTP 回应」：401 也算健康，因为访问密钥可能来自
    加密配置而不是环境变量，这里读不到它；能返回 401 说明监听正常、
    路由已就绪。只有连不上（OSError）才算失败。
    """
    host = os.environ.get("AI_PROBE_RELAY_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = os.environ.get("AI_PROBE_RELAY_PORT", "8040").strip() or "8040"
    key = os.environ.get("AI_PROBE_RELAY_KEY", "").strip()
    request = urllib.request.Request(
        f"http://{host}:{port}/v1/models",
        headers={"Authorization": f"Bearer {key}"} if key else {},
    )
    try:
        urllib.request.urlopen(request, timeout=timeout).read()
    except urllib.error.HTTPError:
        return True
    except (OSError, ValueError):
        return False
    return True


def main() -> int:
    if "--healthcheck" in sys.argv:
        return 0 if healthcheck() else 1
    _configure_logging()
    try:
        config_key = resolve_config_key()
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.error("配置密钥不可用：%s", exc)
        return 1
    if config_key is None:
        LOGGER.error(
            "未找到配置密钥：请把包含 ai_probe_config.key 与 ai_probe_projects.json 的数据目录挂载到 "
            "AI_PROBE_DATA_DIR，或设置 AI_PROBE_CONFIG_PASSWORD"
        )
        return 1

    try:
        service = RelayService(config_key)
        relay = apply_env_overrides(service.store)
        project_count, model_count = enabled_counts(service.store)
        if not project_count or not model_count:
            LOGGER.error("没有启用的接口或模型：请先在桌面端启用接口并添加模型，再启动容器")
            return 1
        server = service.start()
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.error("中转启动失败：%s", exc)
        return 1

    display_host = "127.0.0.1" if server.host in {"0.0.0.0", "::"} else server.host
    LOGGER.info(
        "中转已启动：%s:%s（对外 Base URL http://%s:%s/v1），接口 %s 个、模型 %s 个、可路由模型 %s 个",
        server.host,
        server.port,
        display_host,
        server.port,
        project_count,
        model_count,
        len(server.model_routes()),
    )
    if not relay["api_key"]:
        LOGGER.warning("未设置访问密钥，任何能访问该端口的人都可以使用这个中转")

    stop_event = threading.Event()

    def handle_signal(signum, _frame):
        LOGGER.info("收到信号 %s，正在停止中转", signum)
        stop_event.set()

    # 容器停止走 SIGTERM；交互式运行时 Ctrl+C 走 SIGINT。Windows 上 Ctrl+Break
    # 映射到 SIGBREAK（该平台没有 SIGTERM 语义），一并接管，免得控制台里
    # 中断时跳过停止流程、用量统计来不及落盘。
    signals = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        signal.signal(sig, handle_signal)

    try:
        # 带超时的轮询比无超时 wait() 更可靠：信号处理器只在主线程运行，
        # 而等待本身必须能被 SIGTERM 打断，容器停止时才不会卡到超时被 kill。
        while not stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    service.stop()
    LOGGER.info("中转已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
