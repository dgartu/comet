"""Small normalization helpers shared by metadata providers."""

from comet.utils.text import has_ascii_control


def metadata_text(value: object) -> str | None:
    if not isinstance(value, str) or not (value := " ".join(value.split())):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if has_ascii_control(value):
        return None
    return value


def episode_coordinate(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def normalize_aliases(value: dict[str, list[str]]) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for raw_scope, raw_titles in value.items():
        scope = raw_scope.lower()
        normalized.setdefault(scope, []).extend(
            title for raw in raw_titles if (title := metadata_text(raw))
        )
    return {scope: list(dict.fromkeys(titles)) for scope, titles in normalized.items()}
