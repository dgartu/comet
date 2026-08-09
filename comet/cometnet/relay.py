"""
CometNet Relay Client

HTTP client for relaying torrent broadcasts to an external CometNet service.
Used in cluster deployments where Comet workers send torrents to a
dedicated CometNet standalone service.
"""

import asyncio
from typing import Any

import aiohttp
import orjson

from comet.cometnet.interface import CometNetBackend
from comet.cometnet.protocol import TorrentMetadata
from comet.core.provider_json import is_success_status
from comet.observability.context import create_detached_task


class CometNetRelay(CometNetBackend):
    """
    HTTP client for relaying torrents to a standalone CometNet service.

    This is used when COMETNET_RELAY_URL is configured, allowing Comet workers
    to send torrent broadcasts to an external CometNet service instead of
    running their own P2P network.
    """

    def __init__(
        self, relay_url: str, timeout: float = 30.0, api_key: str | None = None
    ):
        """
        Initialize the relay client.

        Args:
            relay_url: Base URL of the CometNet standalone service (e.g., http://cometnet:8766)
            timeout: Request timeout in seconds
            api_key: Optional API key for authentication
        """
        self.relay_url = relay_url.rstrip("/")
        self._display_url = "<redacted-url>"
        self.timeout = timeout
        self.api_key = api_key
        self._session: aiohttp.ClientSession | None = None
        self._batch: list[dict] = []
        self._batch_lock = asyncio.Lock()
        self._flush_event = asyncio.Event()
        self._batch_task: asyncio.Task | None = None
        self._running = False

        self.batch_size = 50
        self.batch_interval = 2.0

        self._total_relayed = 0
        self._total_errors = 0
        self._last_error: str | None = None

    @property
    def running(self) -> bool:
        """Check if the relay is running."""
        return self._running

    async def start(self):
        """Start the relay client."""
        if self._running:
            return

        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            json_serialize=lambda value: orjson.dumps(value).decode(),
            headers=headers,
        )
        self._running = True

        self._flush_event.clear()
        self._batch_task = create_detached_task(
            self._batch_flush_loop(),
            name="cometnet-relay-flush",
        )

    async def stop(self):
        """Stop the relay client and flush remaining batch."""
        self._running = False

        if self._batch_task:
            self._batch_task.cancel()
            try:
                await self._batch_task
            except asyncio.CancelledError:
                pass
            self._batch_task = None

        await self._flush_batch()

        if self._session:
            await self._session.close()
            self._session = None

    async def _batch_flush_loop(self):
        """Periodically flush the batch."""
        while self._running:
            try:
                await asyncio.wait_for(
                    self._flush_event.wait(), timeout=self.batch_interval
                )
            except TimeoutError:
                pass
            self._flush_event.clear()
            await self._flush_batch()

    async def _flush_batch(self):
        """Flush the current batch to the CometNet service."""
        async with self._batch_lock:
            if not self._batch:
                return

            batch_to_send = self._batch
            self._batch = []

        if not self._session:
            async with self._batch_lock:
                self._batch = batch_to_send + self._batch
            return

        try:
            if len(batch_to_send) == 1:
                await self._send_single(batch_to_send[0])
            else:
                await self._send_batch(batch_to_send)
        except asyncio.CancelledError:
            async with self._batch_lock:
                self._batch = batch_to_send + self._batch
            raise

    async def _send_single(self, torrent: dict) -> bool:
        """Send a single torrent to the relay."""
        try:
            async with self._session.post(
                f"{self.relay_url}/broadcast",
                json=torrent,
            ) as response:
                if is_success_status(response.status):
                    self._total_relayed += 1
                    return True
                self._total_errors += 1
                return False
        except (aiohttp.ClientError, TimeoutError):
            self._total_errors += 1
            return False

    async def _send_batch(self, torrents: list[dict]) -> int:
        """Send a batch of torrents to the relay. Returns number successfully queued."""
        try:
            async with self._session.post(
                f"{self.relay_url}/broadcast/batch",
                json={"torrents": torrents},
            ) as response:
                if is_success_status(response.status):
                    data = await response.json()
                    queued = data["queued"]
                    errors = len(data["errors"])
                    self._total_relayed += queued
                    self._total_errors += errors
                    return queued
                self._total_errors += len(torrents)
                return 0
        except (aiohttp.ClientError, TimeoutError, KeyError, TypeError, ValueError):
            self._total_errors += len(torrents)
            return 0

    async def get_stats(self) -> dict:
        """Get relay statistics (merges remote stats with local relay stats)."""
        remote_stats = await self.fetch_remote_stats() or {}
        local_stats = {
            "relay_url": self._display_url,
            "running": self._running,
            "total_relayed": self._total_relayed,
            "total_errors": self._total_errors,
            "batch_pending": len(self._batch),
            "last_error": self._last_error,
        }
        # Merge local relay stats under 'relay' key
        remote_stats["relay"] = local_stats
        return remote_stats

    async def fetch_remote_stats(self) -> dict | None:
        """Fetch stats from the remote CometNet standalone service."""
        if not self._session or not self._running:
            return None

        try:
            async with self._session.get(f"{self.relay_url}/stats") as response:
                if is_success_status(response.status):
                    self._last_error = None
                    return await response.json()
                if response.status == 401:
                    self._last_error = "Authentication failed: API Key required"
                elif response.status == 403:
                    self._last_error = "Authentication failed: Invalid API Key"
                else:
                    self._last_error = f"Remote error: {response.status}"
                return None
        except Exception as error:
            self._last_error = f"Connection failed ({type(error).__name__})"
            return None

    async def get_peers(self) -> dict[str, Any]:
        """Get peers from the remote CometNet standalone service."""
        if not self._session or not self._running:
            return {"peers": [], "count": 0}

        try:
            async with self._session.get(f"{self.relay_url}/peers") as response:
                if is_success_status(response.status):
                    return await response.json()
                return {"peers": [], "count": 0}
        except Exception:
            return {"peers": [], "count": 0}

    # --- Pool Management (proxied to standalone) ---

    async def _pool_request(
        self, method: str, path: str, json_data: dict | None = None
    ) -> dict:
        """Make a pool management request to the standalone service."""
        if not self._session or not self._running:
            raise RuntimeError("Relay not running")

        url = f"{self.relay_url}{path}"
        kwargs = {"json": json_data or {}} if method in {"POST", "PATCH"} else {}
        try:
            async with self._session.request(method, url, **kwargs) as response:
                return await self._handle_pool_response(response)
        except aiohttp.ClientError as error:
            raise RuntimeError("Failed to connect to standalone") from error

    async def _handle_pool_response(self, response) -> dict:
        """Handle response from standalone pool endpoints."""
        if is_success_status(response.status):
            return {} if response.status == 204 else await response.json()
        if response.status == 404:
            raise ValueError("Pool not found")
        if response.status == 400:
            data = await response.json()
            raise ValueError(data.get("detail", "Bad request"))
        if response.status == 403:
            raise PermissionError("Permission denied")
        raise RuntimeError(f"Standalone returned {response.status}")

    async def _pool_action(
        self, method: str, path: str, json_data: dict | None = None
    ) -> bool:
        try:
            await self._pool_request(method, path, json_data)
            return True
        except (RuntimeError, ValueError, PermissionError):
            return False

    async def create_pool(
        self,
        pool_id: str,
        display_name: str,
        description: str = "",
        join_mode: str = "invite",
    ) -> dict:
        """Create a pool on the standalone service."""
        return await self._pool_request(
            "POST",
            "/pools",
            {
                "pool_id": pool_id,
                "display_name": display_name,
                "description": description,
                "join_mode": join_mode,
            },
        )

    async def delete_pool(self, pool_id: str) -> bool:
        """Delete a pool on the standalone service."""
        return await self._pool_action("DELETE", f"/pools/{pool_id}")

    async def get_pools(self) -> dict:
        """Get pools from the standalone service."""
        return await self._pool_request("GET", "/pools")

    async def join_pool_with_invite(
        self, pool_id: str, invite_code: str, node_url: str | None = None
    ) -> bool:
        """Join a pool using an invite code."""
        return await self._pool_action(
            "POST",
            f"/pools/{pool_id}/join",
            {"invite_code": invite_code, "node_url": node_url},
        )

    async def create_pool_invite(
        self,
        pool_id: str,
        expires_in: int | None = None,
        max_uses: int | None = None,
    ) -> str | None:
        """Create an invite for a pool."""
        try:
            result = await self._pool_request(
                "POST",
                f"/pools/{pool_id}/invite",
                {"expires_in": expires_in, "max_uses": max_uses},
            )
            return result.get("invite_link")
        except (RuntimeError, ValueError, PermissionError):
            return None

    async def delete_pool_invite(self, pool_id: str, invite_code: str) -> bool:
        """Delete a pool invite."""
        return await self._pool_action(
            "DELETE", f"/pools/{pool_id}/invites/{invite_code}"
        )

    async def get_pool_invites(self, pool_id: str) -> dict[str, Any]:
        """Get active invites for a pool."""
        try:
            return await self._pool_request("GET", f"/pools/{pool_id}/invites")
        except (RuntimeError, ValueError, PermissionError):
            return {}

    async def subscribe_to_pool(self, pool_id: str) -> bool:
        """Subscribe to a pool."""
        return await self._pool_action("POST", f"/pools/{pool_id}/subscribe")

    async def unsubscribe_from_pool(self, pool_id: str) -> bool:
        """Unsubscribe from a pool."""
        return await self._pool_action("DELETE", f"/pools/{pool_id}/subscribe")

    async def add_pool_member(
        self, pool_id: str, member_key: str, role: str = "member"
    ) -> bool:
        """Add a member to a pool."""
        return await self._pool_action(
            "POST",
            f"/pools/{pool_id}/members",
            {"member_key": member_key, "role": role},
        )

    async def remove_pool_member(self, pool_id: str, member_key: str) -> bool:
        """Remove a member from a pool."""
        return await self._pool_action(
            "DELETE", f"/pools/{pool_id}/members/{member_key}"
        )

    async def get_pool_details(self, pool_id: str) -> dict | None:
        """Get detailed information about a pool including all members."""
        try:
            return await self._pool_request("GET", f"/pools/{pool_id}")
        except Exception:
            return None

    async def update_member_role(
        self, pool_id: str, member_key: str, new_role: str
    ) -> bool:
        """Change a member's role (promote to admin or demote to member)."""
        try:
            await self._pool_request(
                "PATCH",
                f"/pools/{pool_id}/members/{member_key}/role",
                {"role": new_role},
            )
            return True
        except (ValueError, PermissionError):
            raise
        except RuntimeError:
            return False

    async def leave_pool(self, pool_id: str) -> bool:
        """Leave a pool (self-removal). Any member except creator can leave."""
        try:
            await self._pool_request("POST", f"/pools/{pool_id}/leave")
            return True
        except (ValueError, PermissionError):
            raise
        except RuntimeError:
            return False

    async def broadcast_torrents(self, metadata_list: list[TorrentMetadata]) -> None:
        """Broadcast multiple torrents to the network (via relay)."""
        if not self._running:
            return

        if not metadata_list:
            return

        batch_data = [metadata.model_dump() for metadata in metadata_list]

        async with self._batch_lock:
            if not self._running:
                return
            self._batch.extend(batch_data)

            if len(self._batch) >= self.batch_size:
                self._flush_event.set()

    async def broadcast_torrent(self, metadata: TorrentMetadata) -> None:
        """Broadcast a torrent to the network (via relay)."""
        await self.broadcast_torrents([metadata])


_relay_instance: CometNetRelay | None = None


def get_relay() -> CometNetRelay | None:
    """Get the global relay instance."""
    return _relay_instance


async def init_relay(relay_url: str, api_key: str | None = None) -> CometNetRelay:
    """Initialize the global relay instance."""
    global _relay_instance

    if _relay_instance is not None:
        await _relay_instance.stop()

    _relay_instance = CometNetRelay(relay_url, api_key=api_key)
    await _relay_instance.start()

    return _relay_instance


async def stop_relay():
    """Stop the global relay instance."""
    global _relay_instance

    if _relay_instance is not None:
        await _relay_instance.stop()
        _relay_instance = None
