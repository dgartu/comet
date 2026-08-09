"""
CometNet Utilities Module

Common utility functions for P2P networking, data normalization,
and asynchronous execution.
"""

import asyncio
import email.utils
import ipaddress
import re
import socket
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from typing import Any, TypeVar
from urllib.parse import urlparse, urlsplit, urlunsplit

import aiohttp
import websockets

from comet.core.models import settings

T = TypeVar("T")

_crypto_executor: ThreadPoolExecutor | None = None


def _format_host_port(host: str, port: int) -> str:
    """Format a host and port as a URI authority."""
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def format_websocket_url(host: str, port: int, scheme: str = "ws") -> str:
    """Build a WebSocket URL, adding the brackets required around IPv6 hosts."""
    return f"{scheme}://{_format_host_port(host, port)}"


def replace_websocket_url_port(address: str, port: int) -> str:
    """Replace a WebSocket URL's port without losing its IPv6 host or path."""
    parsed = urlsplit(address)
    if parsed.scheme not in ("ws", "wss") or not parsed.hostname:
        raise ValueError("address must be a WebSocket URL with a hostname")
    return urlunsplit(
        (
            parsed.scheme,
            _format_host_port(parsed.hostname, port),
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def get_websocket_compression() -> str | None:
    """Return the websockets compression mode configured for CometNet."""
    return (
        "deflate" if settings.COMETNET_TRANSPORT_WEBSOCKET_COMPRESSION_ENABLED else None
    )


def _get_crypto_executor() -> ThreadPoolExecutor:
    """Get or create the dedicated crypto thread pool."""
    global _crypto_executor
    if _crypto_executor is None:
        pool_size = max(4, settings.EXECUTOR_MAX_WORKERS)
        _crypto_executor = ThreadPoolExecutor(
            max_workers=pool_size, thread_name_prefix="cometnet-crypto-"
        )
    return _crypto_executor


def shutdown_crypto_executor() -> None:
    """Shutdown the crypto executor (call on application shutdown)."""
    global _crypto_executor
    if _crypto_executor is not None:
        _crypto_executor.shutdown(wait=False)
        _crypto_executor = None


# --- Data Normalization ---


def canonicalize_data(data: Any) -> Any:
    """
    Recursively sort dict keys for deterministic serialization.
    Used for creating stable signatures.
    """
    if isinstance(data, dict):
        try:
            return {k: canonicalize_data(v) for k, v in sorted(data.items())}
        except TypeError:
            # Fallback for mixed types that cannot be compared directly
            return {
                k: canonicalize_data(v)
                for k, v in sorted(data.items(), key=lambda x: str(x[0]))
            }
    elif isinstance(data, list):
        return [canonicalize_data(i) for i in data]
    else:
        return data


# --- Network Utilities ---

_INTERNAL_DOMAIN_RE = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\.local$",
        r"\.internal$",
        r"\.lan$",
        r"\.localdomain$",
        r"\.home$",
        r"\.corp$",
        r"\.intranet$",
        r"\.private$",
        r"^localhost\.",
        r"\.localhost$",
        r"\.nip\.io$",
        r"\.sslip\.io$",
        r"\.xip\.io$",
        r"^(?:\d{1,3}[-.]){3}\d{1,3}\.",
    )
)


def is_internal_domain(hostname: str) -> bool:
    """
    Check if a hostname looks like an internal/private domain.
    This catches domains that resolve to internal IPs even if
    the domain itself isn't an IP address.
    """
    hostname = hostname.lower().strip(".")

    return any(pattern.search(hostname) for pattern in _INTERNAL_DOMAIN_RE)


async def resolve_hostname_to_ip(hostname: str) -> str | None:
    """
    Resolve a hostname to its IP address.
    Returns None if resolution fails.
    """
    try:
        loop = asyncio.get_running_loop()
        result = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        if result:
            return result[0][4][0]
        return None
    except (OSError, IndexError):
        return None


async def is_private_or_internal_ip(host: str) -> bool:
    """
    Check if a host is a private/internal IP address.
    Checks for: private, loopback, link-local, and reserved addresses.
    Also resolves hostnames to check their actual IP.
    """
    # First, try direct IP check
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        pass

    # Not a direct IP - check if it's an internal domain pattern
    if is_internal_domain(host):
        return True

    # Try to resolve the hostname and check the resulting IP
    # This catches DNS rebinding attempts where a public-looking domain
    # resolves to a private IP
    resolved_ip = await resolve_hostname_to_ip(host)
    if resolved_ip:
        try:
            ip = ipaddress.ip_address(resolved_ip)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
        except ValueError:
            pass

    return False


def extract_ip_from_address(address: str) -> str:
    """
    Extract the IP/hostname from a WebSocket address.
    Handles ws://, wss://, and raw IP:port formats.
    """
    try:
        address = address.strip()
        if address.startswith(("ws://", "wss://")):
            parsed = urlparse(address)
            return parsed.hostname or "unknown"

        # A bare IPv6 address contains colons but no port delimiter.
        try:
            return str(ipaddress.ip_address(address))
        except ValueError:
            pass

        # Prefixing // makes urlsplit parse raw host:port authorities.
        return urlsplit(f"//{address}").hostname or "unknown"
    except Exception:
        return "unknown"


async def is_valid_peer_address(address: str, allow_private: bool = False) -> bool:
    """
    Validate a peer address for security.

    Args:
        address: WebSocket URL to validate
        allow_private: If True, allow private/internal IPs

    Returns:
        True if the address is valid and safe to connect to
    """
    try:
        parsed = urlparse(address)

        # Must be ws:// or wss:// scheme
        if parsed.scheme not in ("ws", "wss"):
            return False

        # Must have a hostname
        if not parsed.hostname:
            return False

        host = parsed.hostname.lower()

        # Block localhost variants if not allowed
        if host in ("localhost", "localhost.localdomain") and not allow_private:
            return False

        # Check for private/internal IP addresses
        if not allow_private and await is_private_or_internal_ip(host):
            return False

        # Port must be valid if specified
        if parsed.port is not None and not (1 <= parsed.port <= 65535):
            return False

        return parsed.username is None and parsed.password is None
    except Exception:
        return False


# --- Async Utilities ---


async def run_in_executor[T](func: Callable[..., T], *args: Any) -> T:
    """
    Run a blocking function in the dedicated crypto executor.
    """
    loop = asyncio.get_running_loop()
    executor = _get_crypto_executor()
    return await loop.run_in_executor(executor, partial(func, *args))


# --- Reachability Check ---


async def check_advertise_url_reachability(
    advertise_url: str, timeout: float = 10.0
) -> bool:
    """Return whether an advertised WebSocket endpoint is reachable."""
    try:
        async with asyncio.timeout(timeout):
            async with websockets.connect(
                advertise_url,
                close_timeout=2,
                open_timeout=timeout,
                compression=get_websocket_compression(),
            ):
                return True
    except Exception:
        return False


async def check_system_clock_sync(
    tolerance: float = 60.0,
    timeout: float = 5.0,
    endpoints: list[str] | None = None,
) -> bool:
    """Check the system clock against external HTTP date headers."""
    if not endpoints:
        endpoints = [
            "https://www.google.com",
            "https://1.1.1.1",
            "https://www.microsoft.com",
            "https://www.apple.com",
        ]

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        for url in endpoints:
            try:
                async with session.head(url) as resp:
                    if "Date" not in resp.headers:
                        continue

                    server_time = email.utils.parsedate_to_datetime(
                        resp.headers["Date"]
                    )
                    local_time = datetime.now(UTC)
                    return abs((local_time - server_time).total_seconds()) <= tolerance
            except Exception:
                continue

    return False
