import unittest
from unittest.mock import patch

from comet.utils.memory import trim_process_memory


class MemoryUtilityLifecycleTests(unittest.TestCase):
    def test_active_mimalloc_precedes_platform_fallback(self):
        with (
            patch("comet.utils.memory.gc.collect") as collect,
            patch("comet.utils.memory._is_mimalloc_active", return_value=True),
            patch(
                "comet.utils.memory._trim_with_mimalloc",
                return_value=True,
            ) as mimalloc,
            patch("comet.utils.memory._trim_with_libc") as fallback,
        ):
            self.assertTrue(trim_process_memory())

        collect.assert_called_once_with()
        mimalloc.assert_called_once_with(aggressive=True)
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
