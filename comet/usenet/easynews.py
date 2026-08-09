"""Account-bound Easynews primitives shared by discovery and playback."""

import base64
from urllib.parse import urlencode

import aiohttp

from comet.core.provider_json import is_success_status

GENERATE_NZB_URL = "https://members.easynews.com/2.0/api/dl-nzb"


class EasynewsNzbError(RuntimeError):
    """A safe generated-NZB failure with account/retry semantics."""

    def __init__(
        self,
        code: str,
        *,
        retryable: bool = False,
        retry_after: int | None = None,
        auth_failed: bool = False,
    ):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after
        self.auth_failed = auth_failed


def credential(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("Easynews credential is invalid")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Easynews credential is invalid") from exc
    return value


def authorization_header(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def generated_nzb_form(payload: dict) -> bytes:
    """Encode the exact closed Easynews single-item form."""
    content_hash = payload["hash"]
    filename = payload["filename"]
    extension = payload["extension"]
    signature = payload.get("signature")
    encoded_filename = base64.b64encode(filename.encode()).decode().rstrip("=")
    encoded_extension = base64.b64encode(extension.encode()).decode().rstrip("=")
    item_field = "0" if signature is None else f"0&sig={signature}"
    return urlencode(
        (
            ("autoNZB", "1"),
            (
                item_field,
                f"{content_hash}|{encoded_filename}:{encoded_extension}",
            ),
        )
    ).encode("ascii")


async def generate_nzb(
    session,
    payload: dict,
    username: str,
    password: str,
) -> bytes:
    """Generate and bound one NZB from its exact account-owned search row."""
    form = generated_nzb_form(payload)
    headers = {
        **authorization_header(username, password),
        "Accept": "application/x-nzb, */*",
        "Accept-Encoding": "identity",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    try:
        async with session.post(
            GENERATE_NZB_URL,
            data=form,
            headers=headers,
            allow_redirects=False,
        ) as response:
            if response.status in {401, 403}:
                raise EasynewsNzbError(
                    "easynews_auth_failed",
                    auth_failed=True,
                )
            if response.status == 429:
                raise EasynewsNzbError(
                    "easynews_rate_limited",
                    retryable=True,
                    retry_after=bounded_retry_after(
                        response.headers.get("Retry-After")
                    ),
                )
            if response.status >= 500:
                raise EasynewsNzbError(
                    "easynews_generate_unavailable",
                    retryable=True,
                )
            if not is_success_status(response.status):
                raise EasynewsNzbError("easynews_generate_rejected")
            document = await response.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise EasynewsNzbError(
            "easynews_generate_unavailable",
            retryable=True,
        ) from exc
    return document


def bounded_retry_after(value: object) -> int | None:
    """Parse one integer provider delay into the fixed retry window."""
    if (
        not isinstance(value, str)
        or not value
        or not value.isdigit()
        or len(value) > 10
    ):
        return None
    parsed = int(value)
    return min(max(parsed, 1), 300)
