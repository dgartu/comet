"""Import-safe public accessors for the optional CometNet runtime."""

from comet.cometnet.interface import CometNetBackend


def get_active_backend() -> CometNetBackend | None:
    from comet.cometnet.manager import get_cometnet_service
    from comet.cometnet.relay import get_relay

    service = get_cometnet_service()
    if service and service.running:
        return service
    relay = get_relay()
    if relay and relay.running:
        return relay
    return None


__all__ = ("CometNetBackend", "get_active_backend")
