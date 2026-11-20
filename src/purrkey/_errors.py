from __future__ import annotations

from typing import Any


class PurrkeyError(Exception):
    """Error returned by the Purrkey API or raised by the SDK before a request.

    ``status`` is the HTTP status, or 0 when the request never reached the API.
    ``code`` is stable, such as ``insufficient_balance`` or ``max_charge_required``.
    ``retryable`` says whether the same request may be sent again.
    """

    def __init__(
        self,
        *,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        self.details = details

    def __repr__(self) -> str:
        return f"PurrkeyError(status={self.status}, code={self.code!r}, message={self.message!r})"


def api_error(status: int, body: Any) -> PurrkeyError:
    """Build a PurrkeyError from an HTTP error response body."""
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    retryable = error.get("retryable")
    return PurrkeyError(
        status=status,
        code=str(error.get("code") or f"http_{status}"),
        message=str(error.get("message") or f"Purrkey API returned HTTP {status}"),
        retryable=bool(retryable) if retryable is not None else (status == 429 or status >= 500),
        request_id=body.get("requestId") if isinstance(body, dict) else None,
        details=error.get("details"),
    )
