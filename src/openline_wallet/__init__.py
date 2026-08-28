"""OpenLine Wallet v0.1 reference implementation."""

from .errors import WalletError
from .receiver import ReferenceGate, create_presentation
from .wallet import Wallet, verify_bundle

__all__ = [
    "ReferenceGate",
    "Wallet",
    "WalletError",
    "create_presentation",
    "verify_bundle",
]

__version__ = "0.1.0"
