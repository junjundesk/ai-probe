import io
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from ai_probe import relay_service
from ai_probe.config import _derive_config_key
from ai_probe.relay_service import RelayService, apply_env_overrides, enabled_counts, resolve_config_key
from ai_probe.store_service import StoreService, default_store


def _store_with_enabled_project() -> dict:
    store = default_store()
    project = store["projects"][0]
    project["base_url"] = "https://example.test/v1"
    project["models"] = [{"id": "model-a", "api_key_id": "", "route_name": ""}]
    store["relay"]["project_ids"] = [project["id"]]
    return store


class RelayServiceTests(unittest.TestCase):
    def test_env_overrides_apply_and_ignore_blank_values(self):
        store = {"relay": {"host": "127.0.0.1", "port": 8040, "api_key": "", "user_agent": ""}}
        env = {
            "AI_PROBE_RELAY_HOST": "0.0.0.0",
            "AI_PROBE_RELAY_PORT": "9000",
            "AI_PROBE_RELAY_KEY": "secret",
            "AI_PROBE_RELAY_USER_AGENT": "container-agent",
            "AI_PROBE_RELAY_UNRELATED": "ignored",
        }
        with patch.dict(os.environ, env, clear=False):
            relay = apply_env_overrides(store)
        self.assertEqual(
            relay,
            {"host": "0.0.0.0", "port": 9000, "api_key": "secret", "user_agent": "container-agent"},
        )

        with patch.dict(os.environ, {"AI_PROBE_RELAY_PORT": "", "AI_PROBE_RELAY_KEY": ""}, clear=False):
            relay = apply_env_overrides(store)
        # 空字符串表示「不覆盖」，保留原值。
        self.assertEqual(relay["port"], 9000)
        self.assertEqual(relay["api_key"], "secret")

    def test_env_overrides_reject_invalid_port(self):
        for raw in ("abc", "0", "70000"):
            with self.subTest(raw=raw):
                store = {"relay": {"port": 8040}}
                with (
                    patch.dict(os.environ, {"AI_PROBE_RELAY_PORT": raw}, clear=False),
                    self.assertRaises(ValueError),
                ):
                    apply_env_overrides(store)

    def test_enabled_counts_ignores_disabled_projects(self):
        store = _store_with_enabled_project()
        store["projects"].append({"id": "disabled", "name": "off", "models": [{"id": "m1"}, {"id": "m2"}]})
        self.assertEqual(enabled_counts(store), (1, 1))
        store["relay"]["project_ids"] = []
        self.assertEqual(enabled_counts(store), (0, 0))

    def test_resolve_config_key_uses_password_env(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ai_probe_projects.json"
            key = _derive_config_key("unit-test-password")
            StoreService(key, path).save(_store_with_enabled_project())
            with (
                patch.object(relay_service, "DATA_FILE", path),
                patch.dict(os.environ, {"AI_PROBE_CONFIG_PASSWORD": "unit-test-password"}, clear=False),
            ):
                self.assertEqual(resolve_config_key(), key)
            with (
                patch.object(relay_service, "DATA_FILE", path),
                patch.dict(os.environ, {"AI_PROBE_CONFIG_PASSWORD": "wrong-password"}, clear=False),
                self.assertRaises(ValueError),
            ):
                resolve_config_key()

    def test_resolve_config_key_falls_back_to_stored_key(self):
        with (
            patch.object(relay_service, "load_or_create_config_key", return_value=b"stored-key") as loader,
            patch.dict(os.environ, {"AI_PROBE_CONFIG_PASSWORD": ""}, clear=False),
        ):
            self.assertEqual(resolve_config_key(), b"stored-key")
        loader.assert_called_once_with()

    def test_service_starts_relay_and_serves_model_list(self):
        with tempfile.TemporaryDirectory() as directory:
            data_file = Path(directory) / "ai_probe_projects.json"
            usage_file = Path(directory) / "ai_probe_usage.json"
            key = b"0123456789abcdef0123456789abcdef"
            store = _store_with_enabled_project()
            store["relay"]["api_key"] = "container-key"
            StoreService(key, data_file).save(store)

            service = RelayService(key, data_file, usage_file)
            # 端口 0 让系统分配空闲端口，测试之间不会互相抢占。
            service.store["relay"]["port"] = 0
            server = service.start()
            try:
                self.assertIsNotNone(server.port)
                self.assertEqual(sorted(server.model_routes()), ["model-a"])
            finally:
                service.stop()

    def test_service_rejects_unavailable_config_key(self):
        # 既没有 AI_PROBE_CONFIG_PASSWORD，数据目录里也没有已保存的密钥。
        with (
            patch.object(relay_service, "load_or_create_config_key", return_value=None),
            patch.dict(os.environ, {"AI_PROBE_CONFIG_PASSWORD": ""}, clear=False),
        ):
            self.assertIsNone(resolve_config_key())

    def test_healthcheck_treats_401_as_healthy(self):
        from ai_probe import relay_service as module

        # 401 说明监听正常，只是没带对密钥 —— 对 healthcheck 而言就是健康。
        error = urllib.error.HTTPError("http://127.0.0.1:1/v1/models", 401, "Unauthorized", {}, None)
        with (
            patch.object(module.urllib.request, "urlopen", side_effect=error),
            patch.dict(os.environ, {"AI_PROBE_RELAY_PORT": "8040"}, clear=False),
        ):
            self.assertTrue(module.healthcheck())

        with (
            patch.object(module.urllib.request, "urlopen", side_effect=OSError("refused")),
            patch.dict(os.environ, {"AI_PROBE_RELAY_PORT": "8040"}, clear=False),
        ):
            self.assertFalse(module.healthcheck())

    def test_healthcheck_rewrites_wildcard_host_to_loopback(self):
        from ai_probe import relay_service as module

        captured = {}

        def fake_urlopen(request, timeout=0):
            captured["url"] = request.full_url
            return io.BytesIO(b"{}")

        with (
            patch.object(module.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.dict(
                os.environ,
                {"AI_PROBE_RELAY_HOST": "0.0.0.0", "AI_PROBE_RELAY_PORT": "9000"},
                clear=False,
            ),
        ):
            self.assertTrue(module.healthcheck())
        # 容器里监听 0.0.0.0，探活必须打到回环地址。
        self.assertEqual(captured["url"], "http://127.0.0.1:9000/v1/models")


if __name__ == "__main__":
    unittest.main()
