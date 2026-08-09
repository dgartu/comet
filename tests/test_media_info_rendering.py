from RTN import parse

from comet.api.endpoints.stream import _build_kodi_meta
from comet.metadata.media_info import media_info_from_stremthru
from comet.utils.formatting import (
    format_title,
    get_formatted_components,
    get_formatted_components_plain,
)


def _media_info():
    return media_info_from_stremthru(
        {
            "video": {"codec": "hevc", "w": 3840, "h": 1600},
            "subtitle": [
                {"lang": "eng"},
                {"lang": "spa", "title": "Latin"},
            ],
            "format": {"dur": 7_200_000_000_000, "br": 20_000_000},
            "has_chapters": True,
            "v": 1,
        }
    )


def test_embedded_subtitles_have_a_distinct_result_component():
    parsed = parse("Movie.2026.2160p.HEVC.mkv")
    media_info = _media_info()

    emoji = get_formatted_components(
        parsed, "Movie.mkv", 10, 100, "Comet", ["subtitles"], media_info
    )
    plain = get_formatted_components_plain(
        parsed, "Movie.mkv", 10, 100, "Comet", ["subtitles"], media_info
    )

    assert emoji == {"subtitles": "💬 🇬🇧/💃🏻"}
    assert plain == {"subtitles": "Subtitles: en/la"}
    assert format_title(emoji) == "💬 🇬🇧/💃🏻"


def test_kodi_metadata_exposes_exact_measured_properties():
    parsed = parse("Movie.2026.2160p.HEVC.mkv")
    media_info = _media_info()
    components = get_formatted_components_plain(
        parsed, "Movie.mkv", 10, 100, "Comet", ["subtitles"], media_info
    )

    metadata = _build_kodi_meta(parsed, components, media_info)

    assert metadata["width"] == 3840
    assert metadata["height"] == 1600
    assert metadata["subtitles"] == ["en", "la"]
    assert metadata["subtitlesInfo"] == "Subtitles: en/la"
    assert metadata["duration"] == 7_200
    assert metadata["bitrate"] == 20_000_000
    assert metadata["hasChapters"] is True
