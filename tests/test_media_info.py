import unittest

from RTN import DefaultRanking, SettingsModel, get_rank, parse

from comet.metadata.media_info import (
    enrich_parsed,
    media_info_from_json,
    media_info_from_stremthru,
    media_info_to_json,
    prefer_media_info,
)


def _payload(*, source=None):
    return {
        "video": {
            "codec": "hevc",
            "hdr": ["DV", "HDR10+"],
            "w": 3840,
            "h": 1600,
        },
        "audio": [
            {
                "codec": "eac3",
                "profile": "Dolby Digital Plus with Dolby Atmos JOC",
                "lang": "eng",
                "ch_layout": "5.1(side)",
                "default": True,
            },
            {
                "codec": "dts",
                "profile": "DTS-HD MA",
                "lang": "spa",
                "title": "Latin American",
                "ch_layout": "7.1",
                "commentary": True,
            },
        ],
        "subtitle": [
            {
                "codec": "subrip",
                "lang": "por",
                "title": "Brazilian Portuguese",
                "forced": True,
            },
            {
                "codec": "hdmv_pgs_subtitle",
                "lang": "spa",
                "title": "Latin",
                "hearing_impaired": True,
            },
        ],
        "format": {
            "n": "matroska,webm",
            "dur": 7_234_500_000_000,
            "s": 20_000_000_000,
            "br": 22_500_000,
        },
        "has_chapters": True,
        "v": 1,
        **({"src": source} if source else {}),
    }


class MediaInfoTests(unittest.TestCase):
    def test_stremthru_payload_is_normalized_without_losing_track_details(self):
        media_info = media_info_from_stremthru(_payload())

        self.assertEqual(media_info.video.codec, "hevc")
        self.assertEqual(media_info.video.hdr, ("DV", "HDR10+"))
        self.assertEqual(media_info.video.resolution, "2160p")
        self.assertEqual(media_info.audio_languages, ("en", "la"))
        self.assertEqual(
            media_info.audio_codecs,
            (
                "Dolby Digital Plus",
                "Atmos",
                "DTS Lossless",
            ),
        )
        self.assertEqual(media_info.audio_channels, ("5.1", "7.1"))
        self.assertEqual(media_info.subtitle_languages, ("pt", "la"))
        self.assertTrue(media_info.audio[1].commentary)
        self.assertTrue(media_info.subtitles[0].forced)
        self.assertTrue(media_info.subtitles[1].hearing_impaired)
        self.assertEqual(media_info.container.duration_seconds, 7_234.5)
        self.assertEqual(media_info.container.bitrate, 22_500_000)
        self.assertTrue(media_info.has_chapters)

    def test_cache_round_trip_preserves_the_canonical_model(self):
        media_info = media_info_from_stremthru(_payload())

        self.assertEqual(
            media_info_from_json(media_info_to_json(media_info)),
            media_info,
        )

    def test_measured_traits_override_filename_guesses_before_ranking(self):
        original = parse("Movie.2026.1080p.H264.AC3.2.0.English.mkv")
        media_info = media_info_from_stremthru(_payload())

        enriched = enrich_parsed(original, original, media_info)

        self.assertIsNot(enriched, original)
        self.assertEqual(original.resolution, "1080p")
        self.assertEqual(enriched.resolution, "2160p")
        self.assertEqual(enriched.codec, "hevc")
        self.assertEqual(enriched.hdr, ["DV", "HDR10+"])
        self.assertEqual(enriched.languages, ["multi", "en", "la"])
        self.assertEqual(
            enriched.audio,
            ["Dolby Digital Plus", "Atmos", "DTS Lossless"],
        )
        self.assertEqual(enriched.channels, ["5.1", "7.1"])
        self.assertEqual(enriched.bitrate, "22.5mbps")
        self.assertFalse(enriched.commentary)
        self.assertFalse(enriched.subbed)

    def test_store_derived_channels_do_not_replace_filename_channels(self):
        parsed = parse("Movie.2026.1080p.H264.AC3.2.0.mkv")
        media_info = media_info_from_stremthru(_payload(source="realdebrid"))

        enriched = enrich_parsed(parsed, parsed, media_info)

        self.assertEqual(media_info.audio_channels, ())
        self.assertEqual(enriched.channels, ["2.0"])
        self.assertEqual(enriched.codec, "hevc")

    def test_enrichment_materially_affects_rtn_ranking(self):
        parsed = parse("Movie.2026.1080p.H264.AC3.mkv")
        media_info = media_info_from_stremthru(_payload())
        enriched = enrich_parsed(parsed, parsed, media_info)
        settings = SettingsModel()
        ranking = DefaultRanking()

        self.assertGreater(
            get_rank(enriched, settings, ranking),
            get_rank(parsed, settings, ranking),
        )

    def test_native_observation_is_preferred_over_store_metadata(self):
        native = media_info_from_stremthru(_payload())
        store = media_info_from_stremthru(_payload(source="realdebrid"))

        self.assertIs(prefer_media_info(store, native), native)
        self.assertIs(prefer_media_info(native, store), native)

    def test_non_media_payloads_are_ignored(self):
        self.assertIsNone(media_info_from_stremthru(None))
        self.assertIsNone(media_info_from_stremthru({"v": 1, "future": True}))
        self.assertIsNone(media_info_from_json("not-json"))


if __name__ == "__main__":
    unittest.main()
