"""Access-gated Comet native Usenet provider descriptor."""

from comet.core.models import settings
from comet.playback.base import (
    Actionability,
    BytePath,
    ProviderDescriptor,
    ProviderStatus,
    Readiness,
)
from comet.usenet.nntp_config import NntpServerConfig


class NativeUsenetProvider:
    descriptor = ProviderDescriptor(
        kind="comet_native_usenet",
        label="Comet NNTP",
        accepted_locator_kinds=frozenset({"nzb_artifact", "real_nzb"}),
        byte_paths=frozenset({BytePath.NATIVE_ENGINE}),
        mutates_upstream=False,
    )

    def __init__(self, access_error_code: str | None = None):
        self._access_error_code = access_error_code

    @staticmethod
    def servers_for(config: dict) -> tuple[NntpServerConfig, ...]:
        """Resolve exactly one request-authorized native source without persistence."""
        source = config.get("source")
        servers = (
            settings.USENET_NATIVE_SERVERS
            if source == "instance_pool"
            else config["servers"]
        )
        return tuple(NntpServerConfig(**server) for server in servers)

    async def validate_config(self, config: dict) -> ProviderStatus:
        if self._access_error_code is not None:
            return ProviderStatus(
                Readiness.TERMINAL_FAILURE,
                Actionability.NONE,
                code=self._access_error_code,
                auth_failed=True,
            )
        if not settings.USENET_ENGINE_ENABLED:
            return ProviderStatus(
                Readiness.TERMINAL_FAILURE,
                Actionability.NONE,
                code="engine_unavailable",
            )
        source = config["source"]
        if source == "instance_pool" and not settings.USENET_NATIVE_SERVERS:
            return ProviderStatus(
                Readiness.TERMINAL_FAILURE,
                Actionability.NONE,
                code="servers_unavailable",
            )
        if (
            source == "personal_servers"
            and not settings.USENET_NATIVE_ALLOW_USER_SERVERS
        ):
            return ProviderStatus(
                Readiness.TERMINAL_FAILURE,
                Actionability.NONE,
                code="personal_servers_disabled",
            )
        return ProviderStatus(
            Readiness.REQUIRES_PREPARE, Actionability.REMOTE_PREPARE, None
        )
