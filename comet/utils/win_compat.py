"""Windows compatibility shims for third-party dependencies.

mediaflow-proxy (pinned to 2.4.x) imports ``fcntl`` at module level, which only
exists on Unix. This module installs a minimal ``fcntl`` replacement backed by
``msvcrt`` so those imports succeed on Windows. On Unix it is a no-op.

Importing this package must happen before ``mediaflow_proxy`` is imported;
``comet/__init__.py`` takes care of that.
"""

import sys

if sys.platform == "win32" and "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        import errno
        import msvcrt
        import types

        _fcntl = types.ModuleType("fcntl")

        # Mirror the CPython fcntl flag values.
        _fcntl.LOCK_SH = 1
        _fcntl.LOCK_EX = 2
        _fcntl.LOCK_NB = 4
        _fcntl.LOCK_UN = 8

        def _flock(fd, operation):
            # msvcrt.locking operates on byte ranges; locking a single byte at
            # the current position emulates flock() closely enough for the
            # whole-file locks used by mediaflow-proxy.
            if operation & _fcntl.LOCK_UN:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                return

            mode = msvcrt.LK_NBLCK if operation & _fcntl.LOCK_NB else msvcrt.LK_LOCK
            try:
                msvcrt.locking(fd, mode, 1)
            except OSError as exc:
                if operation & _fcntl.LOCK_NB and exc.errno in (
                    errno.EACCES,
                    getattr(errno, "EDEADLOCK", errno.EACCES),
                ):
                    raise BlockingIOError(
                        errno.EAGAIN, "Resource temporarily unavailable"
                    ) from exc
                raise

        _fcntl.flock = _flock
        _fcntl.lockf = _flock

        sys.modules["fcntl"] = _fcntl
