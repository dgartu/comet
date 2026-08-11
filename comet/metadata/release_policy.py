from datetime import UTC, datetime

UNSTABLE_RELEASE_CACHE_TTL = 86400


def utc_date_timestamp(value: str) -> float:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


def release_cache_is_fresh(
    release_at: float | None,
    updated_at: float | None,
    now: float,
    stable_ttl: int,
) -> bool:
    if updated_at is None:
        return False

    # A date learned before its release must be confirmed once at the boundary;
    # until then it is volatile enough to refresh daily.
    unstable = release_at is None or release_at >= updated_at
    if release_at is not None and updated_at < release_at <= now:
        return False

    ttl = stable_ttl
    if unstable:
        ttl = (
            UNSTABLE_RELEASE_CACHE_TTL
            if ttl < 0
            else min(ttl, UNSTABLE_RELEASE_CACHE_TTL)
        )
    return ttl < 0 or updated_at >= now - ttl
