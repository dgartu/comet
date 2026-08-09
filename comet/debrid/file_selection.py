"""Deterministic feature-file selection for debrid torrent inventories."""

from collections.abc import Iterable, Mapping

from RTN import ParsedData

_AUXILIARY_EXTRAS = frozenset(
    {"bonus", "extra", "extras", "featurette", "proof", "sample", "trailer"}
)


def _normalized_extras(parsed: ParsedData | None) -> frozenset[str]:
    if parsed is None:
        return frozenset()
    return frozenset(str(extra).strip().casefold() for extra in parsed.extras)


def is_auxiliary_video(
    parsed: ParsedData | None,
    release_parsed: ParsedData | None = None,
) -> bool:
    """Identify a sample/extra without rejecting a work named after that marker."""
    markers = _normalized_extras(parsed) & _AUXILIARY_EXTRAS
    if not markers:
        return False

    if release_parsed is None or not release_parsed.parsed_title:
        return True

    release_title = " ".join(release_parsed.parsed_title.casefold().split())
    return not any(release_title in {marker, f"the {marker}"} for marker in markers)


def _file_priority(file: Mapping[str, object]) -> tuple:
    parsed = file.get("parsed")
    episodes = tuple(parsed.episodes or ()) if isinstance(parsed, ParsedData) else ()
    episode_specificity = 2 if len(episodes) == 1 else 1 if episodes else 0
    size = file.get("size")
    index = file.get("index")
    title = file.get("title")
    return (
        not is_auxiliary_video(parsed if isinstance(parsed, ParsedData) else None),
        episode_specificity,
        file.get("title_match") is True,
        size if type(size) is int and size >= 0 else -1,
        type(index) is int and index >= 0,
        -(index if type(index) is int and index >= 0 else 2**63),
        title.casefold() if isinstance(title, str) else "",
    )


def select_playback_file(
    files: Iterable[dict],
    *,
    preferred_index: object = None,
    preferred_title: str | None = None,
) -> dict | None:
    """Select playback from already scope-filtered feature files."""
    files = list(files)
    if not files:
        return None

    normalized_title = (
        preferred_title.rsplit("/", 1)[-1].casefold() if preferred_title else None
    )
    for require_index, require_title in ((True, True), (False, True), (True, False)):
        if require_index and preferred_index is None:
            continue
        if require_title and normalized_title is None:
            continue
        for file in files:
            index_matches = str(file.get("index")) == str(preferred_index)
            title = file.get("title")
            title_matches = (
                isinstance(title, str)
                and title.rsplit("/", 1)[-1].casefold() == normalized_title
            )
            if (not require_index or index_matches) and (
                not require_title or title_matches
            ):
                return file

    return max(files, key=_file_priority)


def select_best_availability_files(
    files: Iterable[dict],
    releases: Mapping[str, Mapping[str, object]] | None = None,
) -> list[dict]:
    """Select one stable feature file per torrent and media scope.

    Without release metadata auxiliary files remain a last-resort result. With
    release metadata they are rejected unless the requested work itself has an
    auxiliary-looking title (for example, a film genuinely named ``Sample``).
    """
    original = files if isinstance(files, list) else None
    selected: dict[tuple[str, object, object], dict] = {}
    for file in files:
        info_hash = file["info_hash"]
        parsed = file.get("parsed")
        if releases is not None:
            release = releases.get(info_hash)
            release_parsed = release.get("parsed") if release is not None else None
            if is_auxiliary_video(
                parsed if isinstance(parsed, ParsedData) else None,
                release_parsed if isinstance(release_parsed, ParsedData) else None,
            ):
                continue

        key = (info_hash, file.get("season"), file.get("episode"))
        current = selected.get(key)
        priority = _file_priority(file)
        current_priority = _file_priority(current) if current is not None else None
        same_file = current is not None and (
            current.get("index"),
            current.get("title"),
        ) == (file.get("index"), file.get("title"))
        richer_observation = same_file and getattr(
            file.get("media_info"), "preference_key", (0, 0, 0)
        ) > getattr(current.get("media_info"), "preference_key", (0, 0, 0))
        if (
            current is None
            or priority > current_priority
            or (priority == current_priority and richer_observation)
        ):
            selected[key] = file

    result = list(selected.values())
    if (
        original is not None
        and len(original) == len(result)
        and all(
            current is chosen for current, chosen in zip(original, result, strict=True)
        )
    ):
        return original
    return result
