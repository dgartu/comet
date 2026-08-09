def has_ascii_control(value: str) -> bool:
    """Return whether text contains a C0 control character or DEL."""
    return any(character < " " or character == "\x7f" for character in value)
