from starlette.requests import Request

from comet.utils.network import (
    extract_ip_from_headers,
    get_client_ip,
    get_client_ip_any,
)


def _request(client: tuple[str, int], headers=()) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/playback",
            "raw_path": b"/playback",
            "query_string": b"",
            "headers": headers,
            "client": client,
            "server": ("test", 80),
        }
    )


def test_private_peer_is_available_for_local_telemetry_but_not_upstream_forwarding():
    request = _request(("127.0.0.1", 42_000))

    assert get_client_ip(request) == ""
    assert get_client_ip_any(request) == "127.0.0.1"


def test_forwarded_public_address_is_used_for_both_purposes():
    request = _request(
        ("10.0.0.2", 42_000),
        headers=((b"x-forwarded-for", b"8.8.8.8"),),
    )

    assert get_client_ip(request) == "8.8.8.8"
    assert get_client_ip_any(request) == "8.8.8.8"


def test_aiostreams_forwarded_address_is_recognized():
    request = _request(
        ("10.0.0.2", 42_000),
        headers=((b"x-aiostreams-user-ip", b"8.8.4.4"),),
    )

    assert get_client_ip(request) == "8.8.4.4"


def test_reserved_forwarded_address_is_not_treated_as_public():
    headers = {"X-Forwarded-For": "192.0.2.1, 1.1.1.1"}

    assert extract_ip_from_headers(headers) == "1.1.1.1"
    assert extract_ip_from_headers(headers, require_public=False) == "192.0.2.1"


def test_forwarded_ipv6_address_is_unwrapped_without_reparsing():
    headers = {"Forwarded": 'for="[2606:4700:4700::1111]:443";proto=https'}

    assert extract_ip_from_headers(headers) == "2606:4700:4700::1111"
