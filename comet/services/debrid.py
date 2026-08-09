import time

from RTN import ParsedData

from comet.debrid.exceptions import DebridAuthError
from comet.debrid.file_selection import select_best_availability_files
from comet.debrid.manager import retrieve_debrid_availability
from comet.metadata.media_info import (
    MediaInfo,
    enrich_parsed,
    media_info_from_json,
    prefer_media_info,
)
from comet.observability import metrics
from comet.services.debrid_cache import (
    get_cached_availability,
    get_cached_availability_any_service,
    schedule_cache_availability,
    schedule_cache_availability_after_response,
)
from comet.utils.parsing import MediaScope, load_cached_parsed


class DebridService:
    def __init__(self, debrid_service: str, debrid_api_key: str, ip: str):
        self.debrid_service = debrid_service
        self.debrid_api_key = debrid_api_key
        self.ip = ip

    @staticmethod
    def _coerce_file_index(value):
        return None if value is None else int(value)

    @classmethod
    def _build_torrent_update(
        cls,
        torrent: dict,
        *,
        file_index,
        title: str | None,
        size: int | None,
        parsed: ParsedData | None,
        media_info: MediaInfo | None = None,
    ) -> dict:
        update = {}
        original_parsed = torrent.get("parsed")
        metadata_is_downgrade = (
            parsed is not None
            and parsed.trash
            and isinstance(original_parsed, ParsedData)
            and not original_parsed.trash
        )

        if (parsed is not None or media_info is not None) and not metadata_is_downgrade:
            merged_parsed = enrich_parsed(original_parsed, parsed, media_info)
            if merged_parsed is not None:
                update["parsed"] = merged_parsed

        file_index = cls._coerce_file_index(file_index)
        if file_index is not None:
            update["fileIndex"] = file_index
        if title is not None and not metadata_is_downgrade:
            update["title"] = title
        if size is not None:
            update["size"] = size
        if media_info is not None and not metadata_is_downgrade:
            update["mediaInfo"] = media_info
        elif not metadata_is_downgrade and (
            (file_index is not None and file_index != torrent.get("fileIndex"))
            or (title is not None and title != torrent.get("title"))
        ):
            update["mediaInfo"] = None

        return update

    @staticmethod
    def _cached_file(row, season: int | None, episode: int | None) -> dict:
        return {
            "info_hash": row["info_hash"],
            "index": row["file_index"],
            "title": row["title"],
            "size": row["size"],
            "season": season,
            "episode": episode,
            "parsed": load_cached_parsed(row["parsed"]),
            "media_info": media_info_from_json(row.get("media_info", None)),
        }

    async def get_and_cache_availability(
        self,
        session,
        info_hashes: list[str],
        seeders_map: dict,
        tracker_map: dict,
        sources_map: dict,
        torrents: dict | None,
        media_id: str,
        media_only_id: str,
        season: int | None,
        episode: int | None,
        media_scope: MediaScope,
        target_air_date: str | None = None,
        add_background_task=None,
    ) -> tuple[set[str], dict[str, dict]]:
        started_at = time.perf_counter() if metrics.enabled else 0.0
        outcome = "success"
        try:
            availability = await retrieve_debrid_availability(
                session,
                media_id,
                media_only_id,
                self.debrid_service,
                self.debrid_api_key,
                self.ip,
                info_hashes,
                seeders_map,
                tracker_map,
                sources_map,
                target_air_date=target_air_date,
            )
        except DebridAuthError:
            outcome = "auth_error"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            if metrics.enabled:
                metrics.observe_debrid(
                    self.debrid_service,
                    "availability",
                    outcome,
                    time.perf_counter() - started_at,
                    len(availability) if "availability" in locals() else 0,
                )

        availability = select_best_availability_files(availability, torrents)
        if len(availability) == 0:
            return set(), {}

        info_hash_set = set(info_hashes)
        cached_hashes = set()
        torrent_updates = {}
        for file in availability:
            if not media_scope.matches_file(
                season,
                episode,
                file["season"],
                file["episode"],
            ):
                continue

            info_hash = file["info_hash"]
            if info_hash not in info_hash_set:
                continue
            cached_hashes.add(info_hash)
            if torrents is not None and not media_scope.is_aggregate:
                torrent = torrents.get(info_hash)
                if torrent is None:
                    continue

                update = self._build_torrent_update(
                    torrent,
                    file_index=file["index"],
                    title=file["title"],
                    size=file["size"],
                    parsed=file["parsed"],
                    media_info=file.get("media_info"),
                )
                if update:
                    torrent_updates.setdefault(info_hash, {}).update(update)

        if add_background_task is None:
            schedule_cache_availability(self.debrid_service, availability)
        else:
            add_background_task(
                schedule_cache_availability_after_response,
                self.debrid_service,
                availability,
            )
        return cached_hashes, torrent_updates

    async def check_existing_availability(
        self,
        info_hashes: list,
        season: int | None,
        episode: int | None,
        media_scope: MediaScope,
        torrents: dict | None,
    ) -> tuple[set[str], dict[str, dict]]:
        if len(info_hashes) == 0:
            return set(), {}

        started_at = time.perf_counter() if metrics.enabled else 0.0
        try:
            rows = await get_cached_availability(
                self.debrid_service,
                info_hashes,
                media_scope,
                season,
                episode,
            )
        except Exception:
            if metrics.enabled:
                metrics.observe_debrid(
                    self.debrid_service,
                    "cache_lookup",
                    "error",
                    time.perf_counter() - started_at,
                    0,
                )
            raise
        if metrics.enabled:
            metrics.observe_debrid(
                self.debrid_service,
                "cache_lookup",
                "hit" if rows else "miss",
                time.perf_counter() - started_at,
                len(rows),
            )

        if media_scope.is_aggregate:
            return {row["info_hash"] for row in rows}, {}

        rows = select_best_availability_files(
            (self._cached_file(row, season, episode) for row in rows),
            torrents,
        )

        cached_hashes = set()
        torrent_updates = {}
        for row in rows:
            info_hash = row["info_hash"]
            cached_hashes.add(info_hash)
            if torrents is not None and not media_scope.is_aggregate:
                torrent = torrents.get(info_hash)
                if torrent is None:
                    continue

                update = self._build_torrent_update(
                    torrent,
                    file_index=row["index"],
                    title=row["title"],
                    size=row["size"],
                    parsed=row["parsed"],
                    media_info=row["media_info"],
                )
                if update:
                    torrent_updates[info_hash] = update

        return cached_hashes, torrent_updates

    @classmethod
    async def apply_cached_availability_any_service(
        cls,
        info_hashes: list,
        season: int | None,
        episode: int | None,
        media_scope: MediaScope,
        torrents: dict | None,
    ):
        if len(info_hashes) == 0 or torrents is None or media_scope.is_aggregate:
            return

        rows = await get_cached_availability_any_service(info_hashes, season, episode)
        rows = select_best_availability_files(
            (cls._cached_file(row, season, episode) for row in rows),
            torrents,
        )

        for row in rows:
            info_hash = row["info_hash"]
            torrent = torrents.get(info_hash)
            if torrent is None:
                continue

            update = cls._build_torrent_update(
                torrent,
                file_index=row["index"],
                title=row["title"],
                size=row["size"],
                parsed=row["parsed"],
                media_info=row["media_info"],
            )
            torrent.update(update)


def prefer_torrent_update(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate
    if (current.get("fileIndex"), current.get("title")) != (
        candidate.get("fileIndex"),
        candidate.get("title"),
    ):
        return current
    preferred = prefer_media_info(
        current.get("mediaInfo"),
        candidate.get("mediaInfo"),
    )
    candidate_media_info = candidate.get("mediaInfo")
    return (
        candidate
        if candidate_media_info is not None and preferred is candidate_media_info
        else current
    )
