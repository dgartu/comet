import unittest
from unittest.mock import AsyncMock, patch

from comet.metadata.release_date import ReleaseDateService


class ReleaseDateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cached_epoch_release_is_reused(self):
        service = ReleaseDateService()
        with (
            patch("comet.metadata.release_date.time.time", return_value=1_000),
            patch(
                "comet.metadata.release_date.database.fetch_one",
                new=AsyncMock(
                    return_value={"release_date": 0, "release_updated_at": 900}
                ),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_tmdb_id_from_imdb",
                new=AsyncMock(),
            ) as tmdb_lookup,
        ):
            release = await service.resolve(None, "movie", "tt1234567")

        self.assertEqual(release.date, "1970-01-01")
        self.assertEqual(release.timestamp, 0)
        tmdb_lookup.assert_not_awaited()

    async def test_fresh_unknown_release_is_negative_cached(self):
        service = ReleaseDateService()
        with (
            patch("comet.metadata.release_date.time.time", return_value=1_000),
            patch(
                "comet.metadata.release_date.database.fetch_one",
                new=AsyncMock(
                    return_value={"release_date": None, "release_updated_at": 900}
                ),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_tmdb_id_from_imdb",
                new=AsyncMock(),
            ) as tmdb_lookup,
        ):
            release = await service.resolve(None, "movie", "tt1234567")

        self.assertIsNone(release)
        tmdb_lookup.assert_not_awaited()

    async def test_legacy_far_future_sentinel_is_unknown(self):
        service = ReleaseDateService()
        with (
            patch("comet.metadata.release_date.time.time", return_value=1_000),
            patch(
                "comet.metadata.release_date.database.fetch_one",
                new=AsyncMock(
                    return_value={
                        "release_date": 253402300799,
                        "release_updated_at": 900,
                    }
                ),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_tmdb_id_from_imdb",
                new=AsyncMock(),
            ) as tmdb_lookup,
        ):
            release = await service.resolve(None, "movie", "tt1234567")

        self.assertIsNone(release)
        tmdb_lookup.assert_not_awaited()

    async def test_unknown_movie_release_fails_open_and_is_cached(self):
        service = ReleaseDateService()
        with (
            patch("comet.metadata.release_date.time.time", return_value=1_000),
            patch(
                "comet.metadata.release_date.database.fetch_one",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_tmdb_id_from_imdb",
                new=AsyncMock(return_value="123"),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_home_release_date",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "comet.metadata.release_date.database.execute",
                new=AsyncMock(),
            ) as execute,
        ):
            release = await service.resolve(None, "movie", "tt1234567")

        self.assertIsNone(release)
        self.assertIsNone(execute.await_args.args[1]["release_date"])

    async def test_release_is_revalidated_when_cached_boundary_is_crossed(self):
        service = ReleaseDateService()
        with (
            patch("comet.metadata.release_date.time.time", return_value=86_400),
            patch(
                "comet.metadata.release_date.database.fetch_one",
                new=AsyncMock(
                    return_value={
                        "release_date": 86_400,
                        "release_updated_at": 80_000,
                    }
                ),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_tmdb_id_from_imdb",
                new=AsyncMock(return_value="123"),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_home_release_date",
                new=AsyncMock(return_value="1970-01-03"),
            ),
            patch(
                "comet.metadata.release_date.database.execute",
                new=AsyncMock(),
            ),
        ):
            release = await service.resolve(None, "movie", "tt1234567")

        self.assertEqual(release.date, "1970-01-03")
        self.assertEqual(release.timestamp, 172_800)

    async def test_failed_revalidation_preserves_known_release_boundary(self):
        service = ReleaseDateService()
        with (
            patch("comet.metadata.release_date.time.time", return_value=86_400),
            patch(
                "comet.metadata.release_date.database.fetch_one",
                new=AsyncMock(
                    return_value={
                        "release_date": 86_400,
                        "release_updated_at": 80_000,
                    }
                ),
            ),
            patch(
                "comet.metadata.release_date.TMDBApi.get_tmdb_id_from_imdb",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "comet.metadata.release_date.database.execute",
                new=AsyncMock(),
            ) as execute,
        ):
            release = await service.resolve(None, "movie", "tt1234567")

        self.assertEqual(release.timestamp, 86_400)
        self.assertEqual(execute.await_args.args[1]["release_date"], 86_400)

    async def test_series_uses_episode_index_without_movie_cache(self):
        service = ReleaseDateService()
        with patch(
            "comet.metadata.release_date.EpisodeIndexService.get_target_air_date",
            new=AsyncMock(return_value="2026-08-11"),
        ) as lookup:
            release = await service.resolve(None, "series", "tt1234567", 2, 3)

        self.assertEqual(release.date, "2026-08-11")
        lookup.assert_awaited_once_with("tt1234567", 2, 3)

    async def test_missing_canonical_id_is_unknown(self):
        service = ReleaseDateService()
        self.assertIsNone(await service.resolve(None, "series", None, 1, 1))
