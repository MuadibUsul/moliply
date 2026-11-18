from __future__ import annotations


class RechargeError(Exception):
    """A classified recharge failure safe to show to the customer."""

    def __init__(self, code: str, message: str, release_code: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.release_code = release_code
        # Operator-facing diagnostics; never shown to the customer.
        self.error_detail = ""
        self.artifact_path: str | None = None


class PrecheckError(RechargeError):
    """A rejected pre-check. Nothing was spent, so the code stays untouched."""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(code, message, release_code=True)
        self.status_code = status_code
