from enum import StrEnum
from typing import Annotated

from pydantic import BeforeValidator, Field


class ScrapeContext(StrEnum):
    LIVE = "live"
    BACKGROUND = "background"


def normalize_scraper_mode(value: object) -> bool | str:
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "both", "on", "t", "true", "y", "yes"}:
            return True
        if normalized in {"0", "f", "false", "n", "no", "off"}:
            return False
        if normalized in ScrapeContext:
            return normalized
    elif isinstance(value, bool):
        return value
    raise ValueError("scraper mode must be true, false, both, live, or background")


ScraperMode = Annotated[
    bool | str,
    BeforeValidator(normalize_scraper_mode),
    Field(validate_default=True),
]


def normalize_scraper_name(name: str) -> str:
    normalized = name.strip().casefold()
    for suffix in ("scraper", "adapter"):
        if normalized.endswith(suffix):
            normalized = normalized.removesuffix(suffix)
            break
    return normalized


def scraper_timeout(name: str, context: ScrapeContext) -> float:
    """Resolve the sole provider runtime budget for one scrape context."""
    from comet.core.models import settings

    normalized_name = normalize_scraper_name(name.partition(" #")[0])
    overrides = settings.SCRAPER_TIMEOUT_OVERRIDES
    return overrides.get(
        f"{normalized_name}:{context.value}",
        overrides.get(
            normalized_name,
            (
                settings.LIVE_SCRAPE_TIMEOUT
                if context is ScrapeContext.LIVE
                else settings.BACKGROUND_SCRAPE_TIMEOUT
            ),
        ),
    )
