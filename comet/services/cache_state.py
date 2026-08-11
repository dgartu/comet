import time
from dataclasses import dataclass
from enum import Enum

from comet.core.database import database
from comet.core.models import settings


async def _upsert_scope_demand(
    media_id: str,
    *,
    scraped: bool,
) -> float | None:
    current_time = time.time()
    if not scraped:
        row = await database.fetch_one(
            """
            SELECT last_seen_at, last_scraped_at
            FROM media_demand
            WHERE media_id = :media_id
            """,
            {"media_id": media_id},
            force_primary=True,
        )
        touch_interval = settings.BACKGROUND_SCRAPER_DEMAND_LOOKBACK / 2
        if row is not None and row["last_seen_at"] >= current_time - touch_interval:
            return row["last_scraped_at"]

    row = await database.fetch_one(
        """
        INSERT INTO media_demand (
            media_id,
            first_seen_at,
            last_seen_at,
            last_scraped_at
        )
        VALUES (
            :media_id,
            :current_time,
            :current_time,
            :last_scraped_at
        )
        ON CONFLICT (media_id) DO UPDATE SET
            last_seen_at = EXCLUDED.last_seen_at,
            last_scraped_at = COALESCE(
                EXCLUDED.last_scraped_at,
                media_demand.last_scraped_at
            )
        RETURNING last_scraped_at
        """,
        {
            "media_id": media_id,
            "current_time": current_time,
            "last_scraped_at": current_time if scraped else None,
        },
        force_primary=True,
    )
    return row["last_scraped_at"]


async def mark_scope_scraped(media_id: str) -> None:
    """Record successful completion for one exact request scope."""
    await _upsert_scope_demand(media_id, scraped=True)


class CacheState(Enum):
    FRESH = "fresh"
    STALE = "stale"
    EMPTY = "empty"
    FIRST_SEARCH = "first_search"


class ScrapeDecision(Enum):
    USE_CACHE = "use_cache"
    SCRAPE_FOREGROUND = "scrape_foreground"
    SCRAPE_BACKGROUND = "scrape_background"


@dataclass(frozen=True, slots=True)
class CacheCheckResult:
    state: CacheState
    decision: ScrapeDecision
    has_cached_torrents: bool

    @property
    def should_scrape_now(self) -> bool:
        return self.decision is ScrapeDecision.SCRAPE_FOREGROUND

    @property
    def should_scrape_background(self) -> bool:
        return self.decision is ScrapeDecision.SCRAPE_BACKGROUND


class CacheStateManager:
    """Tracks reusable results separately from exact-scope scrape coverage."""

    def __init__(self, media_id: str, release_at: float | None = None):
        self.media_id = media_id
        self.release_at = release_at

    async def register_demand(self) -> float | None:
        """Record demand and return the last scrape for this exact media ID."""
        return await _upsert_scope_demand(self.media_id, scraped=False)

    @staticmethod
    def _crossed_release(
        last_scraped_at: float | None,
        release_at: float | None,
        now: float,
    ) -> bool:
        return (
            last_scraped_at is not None
            and release_at is not None
            and last_scraped_at < release_at <= now
        )

    @staticmethod
    def _scope_ttl(last_scraped_at: float, release_at: float | None) -> int:
        ttl = settings.LIVE_TORRENT_CACHE_TTL
        recent_ttl = settings.LIVE_TORRENT_CACHE_RECENT_TTL
        if (
            recent_ttl >= 0
            and release_at is not None
            and release_at <= last_scraped_at < release_at + ttl
        ):
            return min(ttl, recent_ttl)
        return ttl

    @classmethod
    def _is_scope_fresh(
        cls,
        last_scraped_at: float | None,
        release_at: float | None,
        now: float,
        crossed_release: bool,
    ) -> bool:
        if last_scraped_at is None:
            return False
        if settings.LIVE_TORRENT_CACHE_TTL < 0:
            return True
        if crossed_release:
            return False
        return last_scraped_at + cls._scope_ttl(last_scraped_at, release_at) >= now

    @staticmethod
    def _determine_state(
        torrent_count: int,
        last_scraped_at: float | None,
        scope_fresh: bool,
    ) -> CacheState:
        has_cached_torrents = torrent_count > 0

        if last_scraped_at is None:
            return CacheState.FIRST_SEARCH if has_cached_torrents else CacheState.EMPTY
        if scope_fresh:
            return CacheState.FRESH
        return CacheState.STALE if has_cached_torrents else CacheState.EMPTY

    @staticmethod
    def _determine_decision(
        state: CacheState,
        crossed_release: bool,
    ) -> ScrapeDecision:
        if state is CacheState.FRESH:
            return ScrapeDecision.USE_CACHE
        if state is CacheState.STALE and not crossed_release:
            return ScrapeDecision.SCRAPE_BACKGROUND
        return ScrapeDecision.SCRAPE_FOREGROUND

    async def check_and_decide(self, torrent_count: int) -> CacheCheckResult:
        last_scraped_at = await self.register_demand()
        now = time.time()
        crossed_release = (
            settings.LIVE_TORRENT_CACHE_RECENT_TTL >= 0
            and self._crossed_release(
                last_scraped_at,
                self.release_at,
                now,
            )
        )
        scope_fresh = self._is_scope_fresh(
            last_scraped_at,
            self.release_at,
            now,
            crossed_release,
        )
        state = self._determine_state(torrent_count, last_scraped_at, scope_fresh)

        return CacheCheckResult(
            state=state,
            decision=self._determine_decision(state, crossed_release),
            has_cached_torrents=torrent_count > 0,
        )
