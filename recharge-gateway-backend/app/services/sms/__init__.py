"""Self-service SMS verification: upstream drivers and the receipt state machine.

The service functions are re-exported here so callers can use one name
(``from app.services import sms as sms_service``) the way every other service
module is used.
"""

from __future__ import annotations

from app.services.sms.providers import (
    BaseSMSProvider,
    FiveSimProvider,
    LeasedNumber,
    NoNumbersAvailable,
    ReceivedSms,
    SMSPoolProvider,
    SmsProviderError,
    available_providers,
    build_provider,
    choose_provider,
    extract_otp,
)
from app.services.sms.service import (
    COUNTRY_LABELS,
    SERVICE_LABELS,
    SmsError,
    change_number,
    lease,
    options,
    poll,
    serialize,
    sweep_expired,
    window_seconds,
)

__all__ = [
    "BaseSMSProvider",
    "COUNTRY_LABELS",
    "FiveSimProvider",
    "LeasedNumber",
    "NoNumbersAvailable",
    "ReceivedSms",
    "SERVICE_LABELS",
    "SMSPoolProvider",
    "SmsError",
    "SmsProviderError",
    "available_providers",
    "build_provider",
    "change_number",
    "choose_provider",
    "extract_otp",
    "lease",
    "options",
    "poll",
    "serialize",
    "sweep_expired",
    "window_seconds",
]
