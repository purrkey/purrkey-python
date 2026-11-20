"""Logic shared by the sync and async clients. Nothing here does I/O."""

from __future__ import annotations

import os
import re
import uuid
from typing import Any

import httpx

from ._errors import PurrkeyError, api_error
from .types import CacheControl, Run, RunRequest, ToolInspection

__version__ = "0.1.0"

DEFAULT_BASE_URL = "https://api.purrkey.app"
DEFAULT_TIMEOUT = 60.0

#: Statuses after which a Run no longer changes on its own. ``UNKNOWN`` is
#: included: the outcome needs reconciliation, so check it later with
#: ``get_run`` instead of submitting the task again.
FINAL_RUN_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT", "UNKNOWN"})

# Price units whose unit price is the most a single call can cost.
_CAPPED_PER_CALL_UNITS = frozenset({"call", "max_call"})
_MICROS = re.compile(r"^[0-9]+$")


def resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("PURRKEY_API_KEY")
    if not key:
        raise PurrkeyError(
            status=0,
            code="missing_api_key",
            message="Pass api_key or set PURRKEY_API_KEY. Create a key at https://purrkey.app/keys",
        )
    return key


def client_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": f"purrkey-python/{__version__}",
    }


def drop_none(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def max_charge(value: int | str | None, inspection: ToolInspection) -> str:
    """Return the Run's spending cap in USD micro-units as an exact string."""
    if value is not None:
        text = str(value) if not isinstance(value, bool) else ""
        if not _MICROS.match(text):
            raise PurrkeyError(
                status=0,
                code="invalid_max_charge",
                message="max_charge_micros must be a non-negative integer number of USD micro-units",
            )
        return text
    tool = inspection["tool"]
    price = tool["price"]
    unit_price = price.get("unitPriceMicros")
    if price["unit"] in _CAPPED_PER_CALL_UNITS and unit_price:
        return unit_price
    raise PurrkeyError(
        status=0,
        code="max_charge_required",
        message=(
            f"{tool['toolId']} is priced per {price['unit']}; pass max_charge_micros "
            f"for this input ({price['description']})"
        ),
    )


def build_run_request(
    inspection: ToolInspection,
    input: Any,
    *,
    max_charge_micros: int | str | None,
    idempotency_key: str | None,
    cache_control: CacheControl | None,
) -> RunRequest:
    tool = inspection["tool"]
    request: RunRequest = {
        "toolId": tool["toolId"],
        "toolRevision": tool["toolRevision"],
        "priceRevision": tool["price"]["priceRevision"],
        "input": input,
        "idempotencyKey": idempotency_key or f"sdk-{uuid.uuid4()}",
        "maxChargeMicros": max_charge(max_charge_micros, inspection),
        "funding": {"mode": "balance"},
    }
    if cache_control is not None:
        request["cacheControl"] = cache_control
    return request


def network_error(cause: Exception) -> PurrkeyError:
    error = PurrkeyError(
        status=0,
        code="network_error",
        message="Could not reach the Purrkey API",
        retryable=True,
    )
    error.__cause__ = cause
    return error


def response_error(response: httpx.Response) -> PurrkeyError:
    try:
        body = response.json()
    except ValueError:
        body = None
    return api_error(response.status_code, body)


def backoff(attempt: int) -> float:
    return 0.5 * float(2**attempt)


def retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("retry-after")
    try:
        return min(float(header), 30.0) if header else None
    except ValueError:
        return None


def poll_delay(run: Run, poll_interval: float, remaining: float) -> float:
    after_ms = run.get("pollAfterMs")
    delay = after_ms / 1000 if after_ms is not None else poll_interval
    return max(0.0, min(delay, remaining))
