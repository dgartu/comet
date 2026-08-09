import re

POOL_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]{1,63}$"
_POOL_ID = re.compile(POOL_ID_PATTERN)


def canonical_pool_id(value: object) -> str:
    if type(value) is not str or _POOL_ID.fullmatch(value) is None:
        raise ValueError("pool_id must be 2-64 lowercase ASCII characters")
    return value
