import time
from dataclasses import dataclass
from datetime import UTC, datetime

from comet.core.models import database, settings
from comet.metadata.episode_index import EpisodeIndexService
from comet.metadata.release_policy import release_cache_is_fresh, utc_date_timestamp
from comet.metadata.tmdb import TMDBApi

_LEGACY_UNKNOWN_RELEASE_TIMESTAMP = 253402300799


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    date: str
    timestamp: float


class ReleaseDateService:
    async def resolve(
        self,
        session,
        media_type: str,
        imdb_id: str | None,
        season: int | None = None,
        episode: int | None = None,
    ) -> ReleaseInfo | None:
        if imdb_id is None:
            return None
        if media_type == "series":
            release_date = await EpisodeIndexService(session).get_target_air_date(
                imdb_id, season, episode
            )
        else:
            release_date = await self._movie_release_date(session, imdb_id)
        if release_date is None:
            return None
        return ReleaseInfo(release_date, utc_date_timestamp(release_date))

    async def _movie_release_date(self, session, imdb_id: str) -> str | None:
        now = time.time()
        cached_release_date = None
        row = await database.fetch_one(
            """
            SELECT release_date, release_updated_at
            FROM media_metadata_cache
            WHERE media_id = :media_id
            """,
            {"media_id": imdb_id},
        )
        if row is not None:
            release_at = (
                None if row["release_date"] is None else float(row["release_date"])
            )
            if release_at == _LEGACY_UNKNOWN_RELEASE_TIMESTAMP:
                release_at = None
            elif release_at is not None:
                cached_release_date = (
                    datetime.fromtimestamp(release_at, UTC).date().isoformat()
                )
            if release_cache_is_fresh(
                release_at,
                row["release_updated_at"],
                now,
                settings.METADATA_CACHE_TTL,
            ):
                return cached_release_date

        tmdb = TMDBApi(session)
        tmdb_id = await tmdb.get_tmdb_id_from_imdb(imdb_id, "movie")
        release_date = (
            None if tmdb_id is None else await tmdb.get_home_release_date(tmdb_id)
        )
        if release_date is None:
            release_date = cached_release_date
        await database.execute(
            """
            INSERT INTO media_metadata_cache (
                media_id,
                release_date,
                release_updated_at
            )
            VALUES (:media_id, :release_date, :release_updated_at)
            ON CONFLICT (media_id) DO UPDATE SET
                release_date = EXCLUDED.release_date,
                release_updated_at = EXCLUDED.release_updated_at
            """,
            {
                "media_id": imdb_id,
                "release_date": (
                    None
                    if release_date is None
                    else int(utc_date_timestamp(release_date))
                ),
                "release_updated_at": now,
            },
        )
        return release_date


release_dates = ReleaseDateService()
