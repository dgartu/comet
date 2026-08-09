import math
from typing import Any

_COMPONENTS = ("reputation", "keystore", "discovery", "gossip")


def validate_state(value: Any) -> dict:
    """Validate the persisted envelope; each component owns its own schema."""
    if type(value) is not dict:
        raise ValueError("state must be an object")
    if not {"saved_at", "node_id", *_COMPONENTS} <= value.keys():
        raise ValueError("state does not match the current schema")

    saved_at = value["saved_at"]
    if (
        type(saved_at) not in (int, float)
        or not math.isfinite(saved_at)
        or saved_at < 0
    ):
        raise ValueError("saved_at must be a finite non-negative number")

    node_id = value["node_id"]
    if node_id is not None and (type(node_id) is not str or not node_id):
        raise ValueError("node_id must be a non-empty string or null")

    signature = value.get("integrity_signature")
    if signature is not None and (type(signature) is not str or not signature):
        raise ValueError("integrity_signature must be a non-empty string")

    if any(type(value[name]) is not dict for name in _COMPONENTS):
        raise ValueError("state components must be objects")
    return value
