import unittest
from unittest.mock import AsyncMock, Mock, patch

from comet.services import trackers as tracker_service


class TrackerDownloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = list(tracker_service.trackers)

    def tearDown(self):
        tracker_service.trackers[:] = self.original

    async def test_download_uses_shared_official_session_and_deduplicates(self):
        document = (
            b"udp://tracker.example:80/announce\n"
            b"\n"
            b"udp://tracker.example:80/announce\n"
            b"https://tracker-two.example/announce\n"
        )
        response = AsyncMock()
        response.__aenter__.return_value = response
        response.read.return_value = document
        response.raise_for_status = lambda: None
        session = Mock()
        session.get.return_value = response
        with patch.object(
            tracker_service.http_client_manager,
            "get_session",
            new=AsyncMock(return_value=session),
        ):
            await tracker_service.download_best_trackers()

        self.assertEqual(
            tracker_service.trackers,
            [
                "udp://tracker.example:80/announce",
                "https://tracker-two.example/announce",
            ],
        )
        session.get.assert_called_once_with(
            tracker_service._TRACKERS_URL,
            headers={"Accept": "text/plain"},
        )

    async def test_unusable_document_keeps_last_good_trackers_and_is_observed(self):
        tracker_service.trackers[:] = ["udp://last-good.example:80/announce"]
        document = b"unsupported://tracker.example/announce\n"

        response = AsyncMock()
        response.__aenter__.return_value = response
        response.read.return_value = document
        response.raise_for_status = lambda: None
        session = Mock()
        session.get.return_value = response
        with (
            patch.object(
                tracker_service.http_client_manager,
                "get_session",
                new=AsyncMock(return_value=session),
            ),
            patch.object(tracker_service.log, "warning") as warning,
        ):
            await tracker_service.download_best_trackers()

        self.assertEqual(
            tracker_service.trackers,
            ["udp://last-good.example:80/announce"],
        )
        warning.assert_called_once()

    async def test_unusable_lines_do_not_discard_valid_or_future_urls(self):
        document = (
            b"\xff\n"
            b"unsupported://tracker.example/announce\n"
            b"https://user:secret@t\xc3\xa4cker.example/announce#channel\n"
        )

        response = AsyncMock()
        response.__aenter__.return_value = response
        response.read.return_value = document
        response.raise_for_status = lambda: None
        session = Mock()
        session.get.return_value = response
        with patch.object(
            tracker_service.http_client_manager,
            "get_session",
            new=AsyncMock(return_value=session),
        ):
            await tracker_service.download_best_trackers()

        self.assertEqual(
            tracker_service.trackers,
            ["https://user:secret@täcker.example/announce#channel"],
        )

    async def test_internal_download_failure_is_not_swallowed(self):
        with (
            patch.object(
                tracker_service.http_client_manager,
                "get_session",
                new=AsyncMock(side_effect=AssertionError("implementation")),
            ),
            self.assertRaisesRegex(AssertionError, "implementation"),
        ):
            await tracker_service.download_best_trackers()
