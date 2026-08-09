"""Pure validation for AltMount paths."""

from comet.utils.text import has_ascii_control


def valid_altmount_virtual_path(value: object) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return (
        len(encoded) <= 2048
        and "\\" not in value
        and not has_ascii_control(value)
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )
