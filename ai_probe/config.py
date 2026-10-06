"""配置加密、本地数据路径与启动时的密钥处理。"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None
    InvalidTag = ValueError


def application_dir() -> Path:
    configured_dir = os.environ.get("AI_PROBE_DATA_DIR")
    if configured_dir:
        return Path(configured_dir).expanduser().resolve()
    compiled = globals().get("__compiled__")
    containing_dir = getattr(compiled, "containing_dir", None)
    if containing_dir:
        return Path(containing_dir).resolve()
    if compiled is not None:
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = application_dir()
DATA_FILE = APP_DIR / "ai_probe_projects.json"
CONFIG_KEY_FILE = APP_DIR / "ai_probe_config.key"
USAGE_FILE = APP_DIR / "ai_probe_usage.json"
RELAY_ERROR_LOG = APP_DIR / "logs" / "relay-errors.jsonl"
RELAY_REQUEST_LOG = APP_DIR / "logs" / "relay-requests.jsonl"
USAGE_SAVE_INTERVAL = 1.0
TEST_PROMPT = "现在几点了"
MAX_WORKERS = 16
PROBE_TIMEOUT = 15.0
PROBE_CONNECT_TIMEOUT = 8.0
PROBE_READ_TIMEOUT = 5.0
PROBE_MAX_OUTPUT_TOKENS = 32
PROBE_UI_BATCH_SIZE = 32
MAX_LOG_LINES = 2000
QUICK_USER_AGENT = ""
CONFIG_FORMAT = "AI_PROBE_AES_GCM_V1"
HARD_CODED_AES_KEY = "Eaz6JVwTiFS2H95Zl-PzTx-5Po5tqAAbEhqo5YIV_Iw"
DEFAULT_TEST_CONFIG_KEY = "ai-probe-self-test-key"
MIN_CONFIG_SECRET_LENGTH = 1


def _derive_config_key(password: str) -> bytes:
    return hashlib.md5((password + HARD_CODED_AES_KEY).encode("utf-8")).hexdigest().encode("ascii")


def _config_key(secret: str | bytes) -> bytes:
    return secret if isinstance(secret, bytes) else _derive_config_key(secret)


def save_config_key(key: bytes):
    CONFIG_KEY_FILE.write_text(key.decode("ascii") + "\n", encoding="utf-8")


def encrypt_config(data: dict, secret: str | bytes | None = None) -> str:
    if AESGCM is None:
        raise RuntimeError("缺少 cryptography，请先运行：pip install -r requirements.txt")
    nonce = os.urandom(12)
    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(_config_key(secret or DEFAULT_TEST_CONFIG_KEY)).encrypt(
        nonce, plaintext, CONFIG_FORMAT.encode("ascii")
    )
    envelope = {
        "format": CONFIG_FORMAT,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }
    return json.dumps(envelope, ensure_ascii=False, indent=2)


def decrypt_config(text: str, secret: str | bytes | None = None, allow_legacy: bool = True) -> tuple[dict, bool]:
    """Return (data, encrypted). Plain JSON remains readable for one-time migration."""
    payload = json.loads(text)
    if not isinstance(payload, dict) or payload.get("format") != CONFIG_FORMAT:
        return payload, False
    if AESGCM is None:
        raise RuntimeError("缺少 cryptography，请先运行：pip install -r requirements.txt")
    try:
        nonce = base64.b64decode(payload["nonce"], validate=True)
        ciphertext = base64.b64decode(payload["ciphertext"], validate=True)
        plaintext = AESGCM(_config_key(secret or DEFAULT_TEST_CONFIG_KEY)).decrypt(
            nonce, ciphertext, CONFIG_FORMAT.encode("ascii")
        )
        data = json.loads(plaintext.decode("utf-8"))
    except (KeyError, ValueError, TypeError, UnicodeDecodeError, InvalidTag) as exc:
        raise ValueError("配置文件不是有效的 AES 加密配置，或密钥不匹配") from exc
    if not isinstance(data, dict):
        raise ValueError("配置文件内容必须是 JSON 对象")
    return data, True


def load_or_create_config_key(
    ask_password=None,
    show_info=None,
    show_error=None,
) -> bytes | None:
    """Load the stored key or ask for a password through injected UI callbacks.

    The crypto and file handling remain GUI-independent so console self-tests and
    lightweight relay mode do not import a widget toolkit.
    """
    try:
        if CONFIG_KEY_FILE.exists():
            encoded = CONFIG_KEY_FILE.read_text(encoding="utf-8").strip()
            if len(encoded) == 32 and all(char in "0123456789abcdefABCDEF" for char in encoded):
                key = encoded.lower().encode("ascii")
                if not DATA_FILE.exists():
                    return key
                decrypt_config(DATA_FILE.read_text(encoding="utf-8"), key, allow_legacy=False)
                return key
    except (OSError, ValueError):
        pass

    if ask_password is None:
        return None
    first_setup = not CONFIG_KEY_FILE.exists()
    while True:
        prompt = "首次使用，请设置配置加密密码（不能为空）：" if first_setup else "请输入配置文件的 AES 解密密码："
        secret = ask_password(prompt)
        if secret is None:
            return None
        secret = str(secret).strip()
        if not secret:
            if show_error:
                show_error("配置解密", "配置密码不能为空")
            continue
        key = _derive_config_key(secret)
        try:
            if DATA_FILE.exists():
                _data, encrypted = decrypt_config(DATA_FILE.read_text(encoding="utf-8"), key, allow_legacy=False)
                if not encrypted and show_info:
                    show_info("迁移明文配置", "检测到旧版明文配置，登录后将使用当前密码进行 AES 加密。")
            save_config_key(key)
            return key
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            if show_error:
                show_error("解密失败", str(exc))
            else:
                return None
