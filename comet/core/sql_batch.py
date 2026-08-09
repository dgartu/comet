"""Bind parameters for chunked multi-row upserts."""

from collections.abc import Mapping, Sequence


def chunk_parameters(
    chunk: Sequence[Mapping[str, object]],
    shared_columns: frozenset[str],
) -> dict[str, object]:
    """Bind caller-owned invariant columns once and the rest per row."""
    values: dict[str, object] = {key: chunk[0][key] for key in shared_columns}
    for index, row in enumerate(chunk):
        for key, value in row.items():
            if key in shared_columns:
                continue
            values[f"{key}_{index}"] = value
    return values
