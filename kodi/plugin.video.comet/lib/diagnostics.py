"""Kodi diagnostics with no request or configuration payloads."""

import xbmc


def emit(event, level=xbmc.LOGERROR, *, outcome=None, error=None, status=None):
    fields = []
    if outcome is not None:
        fields.append("outcome=" + outcome)
    if error is not None:
        fields.append("error_type=" + type(error).__name__)
    if status is not None:
        fields.append("status=" + str(status))
    suffix = " " + " ".join(fields) if fields else ""
    xbmc.log("[Comet] event=" + event + suffix, level)


def run_boundary(event, operation):
    """Own an executable Kodi script boundary without exposing argv or URLs."""
    try:
        return operation()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        emit(event, error=exc, outcome="failed")
        return None
