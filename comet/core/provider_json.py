"""JSON object codecs with opt-in bounds for untrusted provider responses."""

import json

from comet.utils.http_client import read_bounded_body

MAX_PROVIDER_JSON_BYTES = 2 * 1024 * 1024


class ProviderJsonError(ValueError):
    pass


def is_success_status(status: int) -> bool:
    return 200 <= status < 300


def _reject_constant(_value):
    raise ProviderJsonError("invalid JSON constant")


def decode_json_object(body: bytes) -> dict:
    """Decode a JSON object without imposing a transport-size policy."""
    if not body:
        raise ProviderJsonError("invalid provider body")
    try:
        payload = json.loads(
            body.decode("utf-8"),
            parse_constant=_reject_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise ProviderJsonError("invalid provider JSON") from exc
    if not isinstance(payload, dict):
        raise ProviderJsonError("invalid provider JSON object")
    return payload


async def read_json_object(response) -> dict:
    """Read an unbounded response from a trusted origin and decode its object."""
    return decode_json_object(await response.read())


async def read_provider_body(
    response,
    *,
    maximum: int = MAX_PROVIDER_JSON_BYTES,
) -> bytes:
    """Read one bounded provider body."""
    try:
        return await read_bounded_body(response, maximum)
    except ValueError as exc:
        raise ProviderJsonError(str(exc)) from exc


def decode_provider_json(
    body: bytes,
    *,
    maximum: int = MAX_PROVIDER_JSON_BYTES,
) -> dict:
    """Decode one already-read bounded JSON object."""
    if len(body) > maximum:
        raise ProviderJsonError("invalid provider body")
    return decode_json_object(body)


def decode_provider_data(
    body: bytes,
    *,
    maximum: int = MAX_PROVIDER_JSON_BYTES,
) -> dict:
    """Decode a JSON envelope containing a named data object."""
    payload = decode_provider_json(body, maximum=maximum)
    if not isinstance(payload.get("data"), dict):
        raise ProviderJsonError("invalid provider envelope")
    return payload["data"]


async def read_provider_json(
    response,
    *,
    maximum: int = MAX_PROVIDER_JSON_BYTES,
) -> dict:
    """Read and decode one bounded JSON object."""
    return decode_json_object(
        await read_provider_body(response, maximum=maximum),
    )
