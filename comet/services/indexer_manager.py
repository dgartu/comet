import asyncio
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import aiohttp

from comet.core.models import normalize_indexer_name, settings
from comet.core.provider_json import is_success_status
from comet.observability import log


class InvalidIndexerResponse(ValueError):
    pass


class IndexerRefreshError(RuntimeError):
    pass


def _usable_indexer_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not any(character.isspace() for character in value)
    )


def _indexer_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return normalize_indexer_name(value)


def is_private_indexer_type(value: object) -> bool:
    return isinstance(value, str) and value.casefold() == "private"


def _reject_json_constant(_value):
    raise InvalidIndexerResponse("invalid indexer JSON constant")


def decode_indexer_json(document: bytes):
    try:
        return json.loads(
            document.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError):
        raise InvalidIndexerResponse("invalid indexer JSON") from None


async def read_indexer_json(response):
    return decode_indexer_json(await response.read())


async def read_indexer_xml(response):
    document = await response.read()
    if b"<!entity" in document.lower():
        raise InvalidIndexerResponse("invalid indexer XML")
    try:
        root = ET.fromstring(document)
    except ET.ParseError:
        raise InvalidIndexerResponse("invalid indexer XML") from None
    if root.tag != "indexers":
        raise InvalidIndexerResponse("invalid indexer XML")
    return root


def _active_jackett_ids(root, configured_ids: list[str]) -> list[str]:
    configured = {value.casefold() for value in configured_ids}
    active_ids = []
    seen = set()
    for indexer in root.findall("indexer"):
        indexer_id = indexer.get("id")
        if not _usable_indexer_id(indexer_id) or indexer_id.casefold() in seen:
            continue
        if configured:
            title = indexer.find("title")
            name = _indexer_name(title.text if title is not None else None)
            if indexer_id.casefold() not in configured and name not in configured:
                continue
        seen.add(indexer_id.casefold())
        active_ids.append(indexer_id)
    return active_ids


def _private_jackett_ids(root, active_ids: list[str]) -> frozenset[str]:
    active = {indexer_id.casefold() for indexer_id in active_ids}
    return frozenset(
        indexer_id.casefold()
        for indexer in root.findall("indexer")
        if (indexer_id := indexer.get("id"))
        and indexer_id.casefold() in active
        and is_private_indexer_type(indexer.findtext("type"))
    )


def _active_prowlarr_ids(
    indexers, statuses, configured_ids: list[str], current_time: datetime
) -> list[str]:
    if not isinstance(indexers, list) or not isinstance(statuses, list):
        raise InvalidIndexerResponse("invalid Prowlarr indexer response")

    status_map = {}
    for status in statuses:
        if (
            not isinstance(status, dict)
            or isinstance(status.get("indexerId"), bool)
            or not isinstance(status.get("indexerId"), int)
        ):
            continue
        indexer_id = status["indexerId"]
        status_map[indexer_id] = status
    configured = {value.casefold() for value in configured_ids}
    active_ids = []
    seen = set()
    for indexer in indexers:
        if not isinstance(indexer, dict):
            continue
        indexer_id = indexer.get("id")
        if (
            indexer.get("enable") is not True
            or indexer.get("protocol") != "torrent"
            or not isinstance(indexer_id, int)
            or isinstance(indexer_id, bool)
            or indexer_id in seen
        ):
            continue

        status = status_map.get(indexer_id)
        if status is not None:
            disabled_till = status.get("disabledTill")
            if isinstance(disabled_till, str):
                try:
                    disabled_until = datetime.fromisoformat(disabled_till)
                except ValueError:
                    pass
                else:
                    if (
                        disabled_until.tzinfo is not None
                        and disabled_until > current_time
                    ):
                        continue

        indexer_id_text = str(indexer_id)
        if configured:
            candidates = {
                indexer_id_text.casefold(),
                _indexer_name(indexer.get("name")),
                _indexer_name(indexer.get("definitionName")),
            }
            if configured.isdisjoint(candidates):
                continue
        seen.add(indexer_id)
        active_ids.append(indexer_id_text)
    return active_ids


def _private_prowlarr_ids(indexers, active_ids: list[str]) -> frozenset[str]:
    active = set(active_ids)
    return frozenset(
        str(indexer["id"])
        for indexer in indexers
        if isinstance(indexer, dict)
        and str(indexer.get("id")) in active
        and is_private_indexer_type(indexer.get("privacy"))
    )


class IndexerManager:
    def __init__(self):
        self.session: aiohttp.ClientSession | None = None
        self.refresh_interval = settings.INDEXER_MANAGER_UPDATE_INTERVAL
        self.original_jackett_config = settings.JACKETT_INDEXERS.copy()
        self.original_prowlarr_config = settings.PROWLARR_INDEXERS.copy()
        self.active_jackett_config = self.original_jackett_config.copy()
        self.active_prowlarr_config: list[str] = []
        self.private_jackett_indexers: frozenset[str] = frozenset()
        self.private_prowlarr_indexers: frozenset[str] = frozenset()
        self.jackett_initialized = asyncio.Event()
        self.prowlarr_initialized = asyncio.Event()
        self._configuration_changed = asyncio.Event()

    async def get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(auto_decompress=False)
        return self.session

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
        self.session = None

    def reconfigure(self, config) -> None:
        self.refresh_interval = config.INDEXER_MANAGER_UPDATE_INTERVAL
        self.original_jackett_config = config.JACKETT_INDEXERS.copy()
        self.original_prowlarr_config = config.PROWLARR_INDEXERS.copy()
        self.active_jackett_config = self.original_jackett_config.copy()
        self.active_prowlarr_config = []
        self.private_jackett_indexers = frozenset()
        self.private_prowlarr_indexers = frozenset()
        self.jackett_initialized.clear()
        self.prowlarr_initialized.clear()
        self._configuration_changed.set()

    async def _fetch_prowlarr_json(self, session, path: str, headers: dict):
        async with session.get(
            f"{settings.PROWLARR_URL}{path}",
            headers={
                **headers,
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=settings.INDEXER_MANAGER_TIMEOUT),
        ) as response:
            if not is_success_status(response.status):
                return response.status, None
            return response.status, await read_indexer_json(response)

    async def update_jackett(self):
        try:
            if (
                not settings.is_any_context_enabled(settings.SCRAPE_JACKETT)
                or not settings.JACKETT_URL
                or not settings.JACKETT_API_KEY
            ):
                return

            session = await self.get_session()
            url = f"{settings.JACKETT_URL}/api/v2.0/indexers/!status:failing/results/torznab/api"
            params = {
                "apikey": settings.JACKETT_API_KEY,
                "t": "indexers",
                "configured": "true",
            }
            async with session.get(
                url,
                params=params,
                headers={
                    "Accept": "application/xml",
                    "Accept-Encoding": "identity",
                },
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=settings.INDEXER_MANAGER_TIMEOUT),
            ) as response:
                if not is_success_status(response.status):
                    raise IndexerRefreshError(f"Jackett HTTP {response.status}")

                root = await read_indexer_xml(response)
                active_ids = _active_jackett_ids(root, self.original_jackett_config)

                self.active_jackett_config = active_ids
                self.private_jackett_indexers = _private_jackett_ids(root, active_ids)

        finally:
            self.jackett_initialized.set()

    async def update_prowlarr(self):
        try:
            if (
                not settings.is_any_context_enabled(settings.SCRAPE_PROWLARR)
                or not settings.PROWLARR_URL
                or not settings.PROWLARR_API_KEY
            ):
                return

            session = await self.get_session()
            headers = {"X-Api-Key": settings.PROWLARR_API_KEY}

            requests = (
                asyncio.create_task(
                    self._fetch_prowlarr_json(session, "/api/v1/indexer", headers)
                ),
                asyncio.create_task(
                    self._fetch_prowlarr_json(session, "/api/v1/indexerstatus", headers)
                ),
            )
            try:
                (
                    (indexers_status, indexers),
                    (statuses_status, statuses),
                ) = await asyncio.gather(*requests)
            except BaseException:
                for request in requests:
                    request.cancel()
                await asyncio.gather(*requests, return_exceptions=True)
                raise

            if not (
                is_success_status(indexers_status)
                and is_success_status(statuses_status)
            ):
                raise IndexerRefreshError(
                    f"Prowlarr HTTP {indexers_status}/{statuses_status}"
                )

            current_time = datetime.now(UTC)
            active_ids = _active_prowlarr_ids(
                indexers,
                statuses,
                self.original_prowlarr_config,
                current_time,
            )

            self.active_prowlarr_config = active_ids
            self.private_prowlarr_indexers = _private_prowlarr_ids(indexers, active_ids)

        finally:
            self.prowlarr_initialized.set()

    async def run(self):
        while True:
            self._configuration_changed.clear()
            for source, refresh in (
                ("jackett", self.update_jackett),
                ("prowlarr", self.update_prowlarr),
            ):
                try:
                    await refresh()
                except (
                    TimeoutError,
                    aiohttp.ClientError,
                    InvalidIndexerResponse,
                    IndexerRefreshError,
                ) as exc:
                    log.warning(
                        "indexer.refresh.failed",
                        "Indexer refresh failed",
                        provider_name=source,
                        operation="refresh",
                        error_code="dependency_warning",
                        exc=exc,
                    )
            try:
                await asyncio.wait_for(
                    self._configuration_changed.wait(),
                    timeout=self.refresh_interval,
                )
            except TimeoutError:
                pass


indexer_manager = IndexerManager()


def active_jackett_indexers() -> list[str]:
    return indexer_manager.active_jackett_config


def active_prowlarr_indexers() -> list[str]:
    return indexer_manager.active_prowlarr_config


def is_private_prowlarr_indexer(indexer_id: object) -> bool:
    return str(indexer_id) in indexer_manager.private_prowlarr_indexers


def is_private_jackett_indexer(indexer_id: object) -> bool:
    return (
        isinstance(indexer_id, str)
        and indexer_id.casefold() in indexer_manager.private_jackett_indexers
    )
