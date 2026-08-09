from RTN import parse

from comet.debrid.file_selection import (
    select_best_availability_files,
    select_playback_file,
)
from comet.metadata.media_info import media_info_from_stremthru


def _file(index, title, size, *, parsed=None, title_match=False, media_info=None):
    return {
        "info_hash": "a" * 40,
        "index": index,
        "title": title,
        "size": size,
        "season": None,
        "episode": None,
        "parsed": parsed or parse(title),
        "title_match": title_match,
        "media_info": media_info,
    }


def test_playback_keeps_the_file_identity_announced_to_the_user():
    selected = _file(1, "Movie.2026.mkv", 10)
    larger = _file(2, "Movie.2026.Alternate.mkv", 20)

    assert (
        select_playback_file(
            [larger, selected],
            preferred_index="1",
            preferred_title="Movie.2026.mkv",
        )
        is selected
    )


def test_title_match_remains_the_smart_fallback_when_identity_disappears():
    matching = _file(3, "Renamed.Movie.mkv", 10, title_match=True)
    larger = _file(4, "Unrelated.mkv", 20)

    assert select_playback_file([larger, matching]) is matching


def test_richer_media_info_only_breaks_ties_for_the_same_file():
    store = media_info_from_stremthru(
        {"video": {"codec": "avc"}, "src": "realdebrid", "v": 1}
    )
    native = media_info_from_stremthru({"video": {"codec": "hevc"}, "v": 1})
    first = _file(1, "Movie.mkv", 10, media_info=store)
    richer = _file(1, "Movie.mkv", 10, media_info=native)

    assert select_best_availability_files([first, richer]) == [richer]
