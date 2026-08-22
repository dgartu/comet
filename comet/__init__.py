# Windows compatibility shims must be installed before any third-party
# dependency that assumes a POSIX-only API (e.g. mediaflow-proxy imports
# fcntl at module level).
from comet.utils import win_compat  # noqa: F401  isort: skip