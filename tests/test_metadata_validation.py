from comet.metadata.validation import (
    episode_coordinate,
    metadata_text,
    normalize_aliases,
)


def test_metadata_text_normalizes_whitespace_and_rejects_unsafe_text():
    assert metadata_text("  A\t valid\n title  ") == "A valid title"
    assert metadata_text("title\x00suffix") is None
    assert metadata_text("title\ud800") is None
    assert metadata_text(42) is None


def test_episode_coordinates_accept_only_integer_shaped_values():
    assert episode_coordinate(0) == 0
    assert episode_coordinate("-2") == -2
    assert episode_coordinate(True) is None
    assert episode_coordinate(1.5) is None
    assert episode_coordinate("episode") is None


def test_alias_normalization_merges_equivalent_scopes_stably():
    assert normalize_aliases(
        {
            "US": [" First ", "Shared"],
            "us": ["Shared", "Second"],
        }
    ) == {"us": ["First", "Shared", "Second"]}
