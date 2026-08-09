import hashlib
from typing import Literal

from starlette.requests import Request

from comet.core.provider_governor import ProviderGovernor

LOGIN_RETRY_AFTER_SECONDS = 60
_LOGIN_ATTEMPT_LIMIT = 10


def login_field_bytes(value: str) -> bytes | None:
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        return None


async def admit_login_attempt(
    database,
    request: Request,
    operation: Literal["admin_login", "configure_login"],
) -> bool:
    client = request.client
    peer = client.host if client is not None and client.host else "unknown"
    scope = hashlib.sha256(
        operation.encode("ascii") + b"\0" + peer.encode("utf-8", errors="replace")
    ).digest()
    permit = await ProviderGovernor(database).acquire_window(
        scope,
        f"api_{operation}",
        limit=_LOGIN_ATTEMPT_LIMIT,
        window_seconds=LOGIN_RETRY_AFTER_SECONDS,
    )
    return permit is not None
