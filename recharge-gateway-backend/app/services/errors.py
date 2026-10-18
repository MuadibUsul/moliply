from __future__ import annotations


class RechargeError(Exception):
    """A classified recharge failure safe to show to the customer."""

    def __init__(self, code: str, message: str, release_code: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.release_code = release_code
