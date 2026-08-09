import unittest
from unittest.mock import patch

from comet.playback.base import Readiness
from comet.playback.providers.native_usenet import NativeUsenetProvider


class NativeUsenetProviderTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _server(name="personal", host="news.example.test"):
        return {
            "name": name,
            "host": host,
            "port": 563,
            "tls_mode": "implicit",
            "username": None,
            "password": None,
            "connections": 2,
            "priority": 0,
            "backup": False,
            "pipeline": 16,
        }

    def test_native_provider_describes_local_read_only_work(self):
        self.assertFalse(NativeUsenetProvider.descriptor.mutates_upstream)

    async def test_native_provider_requires_engine_and_authorized_server_source(self):
        provider = NativeUsenetProvider()
        with (
            patch(
                "comet.playback.providers.native_usenet.settings.USENET_ENGINE_ENABLED",
                True,
            ),
            patch(
                "comet.playback.providers.native_usenet.settings.USENET_NATIVE_SERVERS",
                [self._server("news")],
            ),
        ):
            valid = await provider.validate_config({"source": "instance_pool"})

        self.assertEqual(valid.readiness, Readiness.REQUIRES_PREPARE)

    async def test_native_provider_accepts_a_validated_personal_server_list(self):
        provider = NativeUsenetProvider()
        with (
            patch(
                "comet.playback.providers.native_usenet.settings.USENET_ENGINE_ENABLED",
                True,
            ),
            patch(
                "comet.playback.providers.native_usenet.settings.USENET_NATIVE_ALLOW_USER_SERVERS",
                True,
            ),
        ):
            valid = await provider.validate_config(
                {
                    "source": "personal_servers",
                    "servers": [self._server()],
                }
            )

        self.assertEqual(valid.readiness, Readiness.REQUIRES_PREPARE)

    async def test_native_provider_reports_the_access_failure_before_server_checks(
        self,
    ):
        provider = NativeUsenetProvider("native_access_token_required")

        status = await provider.validate_config({"source": "personal_servers"})

        self.assertEqual(status.readiness, Readiness.TERMINAL_FAILURE)
        self.assertEqual(status.code, "native_access_token_required")
        self.assertTrue(status.auth_failed)

    def test_native_provider_resolves_only_the_selected_server_source(self):
        provider = NativeUsenetProvider()
        with patch(
            "comet.playback.providers.native_usenet.settings.USENET_NATIVE_SERVERS",
            [self._server("instance", "news.instance.test")],
        ):
            servers = provider.servers_for({"source": "instance_pool"})

        self.assertEqual(servers[0].name, "instance")
        personal = provider.servers_for(
            {
                "source": "personal_servers",
                "servers": [self._server(host="news.personal.test")],
            }
        )
        self.assertEqual(personal[0].name, "personal")
