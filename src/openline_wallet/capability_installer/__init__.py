"""Local receiver-controlled capability installer (developer preview)."""

from .installer import (
    InstallerError,
    accept_package,
    import_package,
    inspect_package,
    invoke,
    revoke,
    settle_demo,
)

__all__ = [
    "InstallerError",
    "accept_package",
    "import_package",
    "inspect_package",
    "invoke",
    "revoke",
    "settle_demo",
]
