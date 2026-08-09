import unittest

from comet.core.sql_batch import chunk_parameters


class SqlBatchTests(unittest.TestCase):
    def test_shared_values_are_bound_once(self):
        self.assertEqual(
            chunk_parameters(
                [
                    {"scope": "movie", "candidate_id": "a"},
                    {"scope": "movie", "candidate_id": "b"},
                ],
                frozenset({"scope"}),
            ),
            {
                "scope": "movie",
                "candidate_id_0": "a",
                "candidate_id_1": "b",
            },
        )


if __name__ == "__main__":
    unittest.main()
