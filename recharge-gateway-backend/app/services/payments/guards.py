"""Payment safety gates shared by providers and API endpoints."""

from __future__ import annotations

from app.config import settings
from app.services import runtime_settings as runtime


def mock_payments_allowed() -> bool:
    """Return True only for an explicitly enabled non-production mock flow.

    Production must opt in explicitly *and* keep the runtime switch on; a
    leftover ``.env`` value can no longer turn a public store into a free
    checkout.
    """
    if settings.is_production and not settings.allow_mock_payment_in_production:
        return False
    return (
        runtime.get("payment_mode", "mock").lower() == "mock"
        and runtime.get_bool("mock_payment_enabled", settings.mock_payment_enabled)
    )
