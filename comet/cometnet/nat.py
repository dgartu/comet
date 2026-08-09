"""
CometNet NAT Traversal Module

Handles UPnP port mapping using miniupnpc.
"""

import asyncio
import threading

import miniupnpc


class UPnPManager:
    """Manages UPnP port mappings."""

    def __init__(self, port: int, lease_duration: int = 3600):
        self.port = port
        self.lease_duration = lease_duration
        self._running = False
        self._thread: threading.Thread | None = None
        self._external_ip: str | None = None
        self._stop_event = threading.Event()

    async def start(self) -> str | None:
        """
        Start the UPnP manager and attempt to map the port.
        Returns the external IP if successful, None otherwise.
        """

        if self._running:
            return self._external_ip

        self._running = True
        self._stop_event.clear()

        # Run discovery and mapping in a separate thread to avoid blocking
        loop = asyncio.get_running_loop()
        self._external_ip = await loop.run_in_executor(None, self._setup_upnp)

        if self._external_ip:
            # Start keepalive thread
            self._thread = threading.Thread(target=self._keepalive_loop, daemon=True)
            self._thread.start()

        return self._external_ip

    async def stop(self) -> None:
        """Stop the UPnP manager and remove port mapping."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            await asyncio.to_thread(self._thread.join, 2.0)

        await asyncio.to_thread(self._remove_mapping)

    def _setup_upnp(self) -> str | None:
        """Sync function to discover UPnP device and map port."""
        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 200
            ndevices = upnp.discover()
            if ndevices == 0:
                return None

            upnp.selectigd()
            lan_addr = upnp.lanaddr
            ext_ip = upnp.externalipaddress()

            upnp.addportmapping(
                self.port, "TCP", lan_addr, self.port, "CometNet P2P", ""
            )
            return ext_ip
        except Exception:
            return None

    def _remove_mapping(self) -> None:
        """Remove the port mapping."""
        try:
            upnp = miniupnpc.UPnP()
            upnp.discoverdelay = 200
            upnp.discover()
            upnp.selectigd()
            upnp.deleteportmapping(self.port, "TCP")
        except Exception:
            pass

    def _keepalive_loop(self) -> None:
        """Periodically renew the port mapping."""
        while not self._stop_event.is_set():
            sleep_time = min(self.lease_duration / 2, 1800)
            if self._stop_event.wait(sleep_time):
                break
            self._setup_upnp()
