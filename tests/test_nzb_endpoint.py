import base64
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from comet.api.endpoints.nzb import (
    _artifact_grant_from_token,
    _enforce_nzb_handoff_rate,
    artifact_intent,
)
from comet.playback.manager import (
    PlaybackIntentResolution,
)
from comet.playback.providers.stremio_nntp import StremioNntpProvider
from comet.playback.repository import (
    ResolvedPlaybackIntent,
)
from comet.playback.tokens import CapabilityCodec, PlaybackIntent

ROOT = base64.urlsafe_b64encode(b"a" * 32).decode().rstrip("=")


def test_nzb_artifact_route_accepts_only_a_bound_na1_grant():
    codec = CapabilityCodec(ROOT)
    partition = codec.configuration_partition(b"normalized")
    grant = uuid.uuid4()
    token = codec.encode(
        "na1", partition=partition, suffix=[grant.bytes], ttl=60, now=100
    )

    assert _artifact_grant_from_token(token, partition, codec, now=120) == str(grant)


class ProviderExportPrivacyTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_response_is_private_and_non_leaking(self):
        from comet.api.app import app

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="https://comet.example",
        ) as client:
            response = await client.get("/nzb/export/v1/not-a-token.nzb")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.headers["cache-control"],
            "private, no-store",
        )
        self.assertEqual(
            response.headers["referrer-policy"],
            "no-referrer",
        )


class NzbIntentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_handoff_rate_limit_is_database_coordinated(self):
        with patch(
            "comet.api.endpoints.nzb.ProviderGovernor.acquire_window",
            AsyncMock(return_value=None),
        ) as acquire:
            with self.assertRaises(Exception) as error:
                await _enforce_nzb_handoff_rate(
                    b"a" * 32,
                    "nzb_intent",
                    (str(uuid.uuid4()),),
                )

        self.assertEqual(getattr(error.exception, "status_code", None), 429)
        acquire.assert_awaited_once()

    async def test_lazy_handoff_brokers_and_serves_only_the_transformed_artifact(
        self,
    ):
        candidate_id, provider_id, source_locator_id, artifact_locator_id = (
            str(uuid.uuid4()) for _ in range(4)
        )
        config = {
            "schemaVersion": 2,
            "playbackProviders": [
                {
                    "configurationId": provider_id,
                    "kind": "stremio_nntp",
                    "enabled": True,
                    "options": {},
                }
            ],
        }
        codec = CapabilityCodec(ROOT)
        partition = codec.configuration_partition_for_config(config)
        capability = codec.encode(
            "ni2",
            partition=partition,
            suffix=[
                uuid.UUID(candidate_id).bytes,
                uuid.UUID(provider_id).bytes,
                [uuid.UUID(source_locator_id).bytes],
                [0],
                "stremio",
            ],
            ttl=60,
        )
        intent = PlaybackIntent(
            candidate_id,
            provider_id,
            (source_locator_id,),
            (0,),
            "stremio",
        )
        provider = type(
            "Provider",
            (),
            {
                "descriptor": type(
                    "Descriptor",
                    (),
                    {"kind": "stremio_nntp"},
                )()
            },
        )()
        source_release = ResolvedPlaybackIntent(
            candidate_id,
            "usenet",
            "Movie.2024.1080p",
            42,
            (
                {
                    "locator_id": source_locator_id,
                    "kind": "real_nzb",
                    "payload": {
                        "adapter_configuration_id": "origin",
                        "remote_guid": "opaque-guid",
                    },
                    "policy": {
                        "allowed_provider_kinds": ["stremio_nntp"],
                        "exact_provider_configuration_id": None,
                        "expires_at": None,
                        "owner_configuration_partition": partition.hex(),
                    },
                },
            ),
            "tt123",
        )
        resolution = PlaybackIntentResolution(
            intent,
            provider,
            {},
            source_release,
            b"c" * 32,
            "d" * 64,
        )
        artifact_release = ResolvedPlaybackIntent(
            candidate_id,
            "usenet",
            source_release.title,
            42,
            (
                {
                    "locator_id": artifact_locator_id,
                    "kind": "nzb_artifact",
                    "payload": {
                        "artifact_sha256": "a" * 64,
                        "manifest_identity": "nm1:" + "b" * 64,
                    },
                    "policy": source_release.locators[0]["policy"],
                },
            ),
            "tt123",
        )
        artifact = type(
            "Artifact",
            (),
            {
                "manifest": [
                    {
                        "subject": '"Movie.2024.1080p.mkv" yEnc',
                        "postings": [],
                    }
                ]
            },
        )()
        reader = type(
            "Reader",
            (),
            {
                "path": Path("/tmp/artifact.nzb"),
                "byte_size": 42,
                "close": AsyncMock(),
            },
        )()
        broker = type(
            "Broker",
            (),
            {
                "resolve_owned_artifact": AsyncMock(return_value=artifact),
                "acquire_owned_artifact": AsyncMock(return_value=reader),
            },
        )()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "scheme": "https",
                "server": ("comet.example", 443),
                "path": "/config/nzb/intent/v2/token.nzb",
                "headers": [],
                "client": ("127.0.0.1", 1234),
            }
        )
        adapters = {"origin": object()}

        with (
            patch(
                "comet.api.endpoints.nzb.config_check",
                return_value=config,
            ),
            patch(
                "comet.api.endpoints.nzb.settings.USENET_ENABLED",
                True,
            ),
            patch(
                "comet.api.endpoints.nzb.settings.COMET_CAPABILITY_SECRET",
                ROOT,
            ),
            patch(
                "comet.api.endpoints.nzb.http_client_manager.get_session",
                AsyncMock(return_value=object()),
            ),
            patch(
                "comet.api.endpoints.nzb.resolve_nzb_handoff_intent",
                AsyncMock(return_value=resolution),
            ),
            patch(
                "comet.api.endpoints.nzb._enforce_nzb_handoff_rate",
                AsyncMock(),
            ) as rate_limit,
            patch(
                "comet.api.endpoints.nzb.build_discovery_adapters",
                return_value=adapters,
            ),
            patch(
                "comet.api.endpoints.nzb.broker_nzb_release",
                AsyncMock(return_value=artifact_release),
            ) as transform,
            patch(
                "comet.api.endpoints.nzb._broker",
                return_value=broker,
            ),
        ):
            response = await artifact_intent(
                request,
                "config",
                capability,
            )

        self.assertEqual(response.headers["content-length"], "42")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        transform.assert_awaited_once()
        rate_limit.assert_awaited_once_with(
            partition,
            "nzb_intent",
            (candidate_id, provider_id, source_locator_id),
        )
        self.assertIs(
            transform.await_args.args[3]["origin"],
            adapters["origin"],
        )
        broker.resolve_owned_artifact.assert_awaited_once_with(
            "a" * 64,
            owner_configuration_partition=partition,
        )
        broker.acquire_owned_artifact.assert_awaited_once_with(
            "a" * 64,
            owner_configuration_partition=partition,
        )
        await response.background()
        reader.close.assert_awaited_once_with()


def test_stremio_provider_rejects_ambiguous_artifact_selection():
    provider = StremioNntpProvider()
    with pytest.raises(ValueError, match="unambiguous"):
        provider.render_client_delegated(
            {
                "servers": [
                    {
                        "host": "news.example",
                        "port": 563,
                        "tls_mode": "implicit_tls",
                        "username": "user",
                        "password": "password",
                        "connections": 4,
                    }
                ],
            },
            "https://comet.example/nzb",
            [{}, {}],
        )


@pytest.mark.parametrize("prefix", ["pi2", "pa2", "ni2"])
def test_nzb_artifact_route_rejects_other_capability_audiences(prefix):
    codec = CapabilityCodec(ROOT)
    partition = codec.configuration_partition(b"normalized")
    suffix = [uuid.uuid4().bytes]
    if prefix in {"pi2", "ni2"}:
        suffix = [
            uuid.uuid4().bytes,
            uuid.uuid4().bytes,
            [uuid.uuid4().bytes],
            [0],
            "stremio",
        ]
    token = codec.encode(prefix, partition=partition, suffix=suffix, ttl=60, now=100)

    with pytest.raises(ValueError):
        _artifact_grant_from_token(token, partition, codec)
