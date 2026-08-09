"""Closed HTTP boundary for fixed-origin metadata services."""

from dataclasses import dataclass

import aiohttp

from comet.core.provider_json import (
    ProviderJsonError,
    is_success_status,
    read_json_object,
)

_BASE_HEADERS = {
    "Accept": "application/json",
    "Accept-Encoding": "identity",
}


class MetadataHttpError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetadataHttpResponse:
    status: int
    payload: dict | None

    @property
    def successful(self) -> bool:
        return is_success_status(self.status)


async def get_metadata_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> MetadataHttpResponse:
    """Fetch one JSON object without following redirects."""
    request_headers = dict(headers or {})
    request_headers.update(_BASE_HEADERS)
    try:
        async with session.get(
            url,
            headers=request_headers,
            allow_redirects=False,
        ) as response:
            status = response.status
            if not is_success_status(status):
                return MetadataHttpResponse(status, None)
            try:
                payload = await read_json_object(response)
            except ProviderJsonError:
                raise MetadataHttpError(
                    "metadata service returned an invalid response"
                ) from None
            return MetadataHttpResponse(status, payload)
    except (TimeoutError, aiohttp.ClientError):
        raise MetadataHttpError("metadata service request failed") from None
