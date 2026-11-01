"""Supplier adapter interface.

A supplier buys a code and redeems it on a customer's session, without a human.
Adapters raise the exceptions below so the caller can classify the outcome:

* ``SupplierRejected`` - a definite failure; the code can be released.
* ``SupplierUncertain`` - the charge may have landed; keep the order pending.
* ``SupplierUnavailable`` - transient; keep polling or retry later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SupplierError(Exception):
    pass


class SupplierRejected(SupplierError):
    """Definite failure, safe to release."""


class SupplierUncertain(SupplierError):
    """Outcome unknown - never retry blindly, the balance may be spent."""


class SupplierUnavailable(SupplierError):
    """Transient: busy, timeout, 5xx."""


@dataclass
class SupplierResult:
    status: str  # success | failed | pending
    message: str = ""
    reference: str = ""


class Supplier(Protocol):
    name: str

    def quote(self, sku: str, contact: str) -> int:
        """Price in cents, and fail early if the SKU is unsellable."""

    def deliver(self, sku: str, session_raw: str, request_no: str, contact: str) -> SupplierResult:
        """Buy and redeem. Raises on failure; returns pending while in flight."""
