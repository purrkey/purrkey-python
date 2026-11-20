from __future__ import annotations

import time
from typing import Any, cast

import httpx

from ._shared import (
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT,
    FINAL_RUN_STATUSES,
    backoff,
    build_run_request,
    client_headers,
    drop_none,
    network_error,
    poll_delay,
    resolve_api_key,
    response_error,
    retry_after,
)
from .types import (
    Balance,
    CacheControl,
    DiscoverResponse,
    ProviderCatalog,
    PublicCatalog,
    PublicEndpointPage,
    PublicRankings,
    Run,
    RunPage,
    RunRequest,
    ToolInspection,
    ToolPage,
)


class Purrkey:
    """Synchronous Purrkey client.

    Every operation is safe to repeat: reads have no side effects and Run
    submissions are deduplicated by their idempotency key, so network errors,
    HTTP 429 and 5xx are retried up to ``max_retries`` times.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 2,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._max_retries = max_retries
        self._http = http_client or httpx.Client(timeout=timeout)
        self._base_url = base_url.rstrip("/")
        self._headers = client_headers(resolve_api_key(api_key))

    def __enter__(self) -> Purrkey:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def discover(
        self,
        query: str,
        *,
        limit: int | None = None,
        provider_id: str | None = None,
        category: str | None = None,
    ) -> DiscoverResponse:
        """Find tool candidates for one task. Free."""
        body = drop_none({"query": query, "limit": limit, "providerId": provider_id, "category": category})
        return cast(DiscoverResponse, self._request("POST", "/v1/discover", json=body))

    def inspect(self, tool_id: str, *, tool_revision: str | None = None) -> ToolInspection:
        """Read a tool's exact schema, price and delivery rules. Free."""
        body = drop_none({"toolId": tool_id, "toolRevision": tool_revision})
        return cast(ToolInspection, self._request("POST", "/v1/inspect", json=body))

    def run(
        self,
        tool_id: str,
        input: Any,
        *,
        max_charge_micros: int | str | None = None,
        idempotency_key: str | None = None,
        tool_revision: str | None = None,
        cache_control: CacheControl | None = None,
        wait: bool = True,
        timeout: float = 120.0,
        poll_interval: float = 1.0,
    ) -> Run:
        """Inspect the tool, submit a Run from the account balance and, by
        default, wait for a final status.

        ``max_charge_micros`` (1 USD = 1000000) is optional for tools priced per
        call and required when the price depends on the input. A lost response
        is retried with the same ``idempotency_key``, so the task is never
        bought twice.
        """
        inspection = self.inspect(tool_id, tool_revision=tool_revision)
        request = build_run_request(
            inspection,
            input,
            max_charge_micros=max_charge_micros,
            idempotency_key=idempotency_key,
            cache_control=cache_control,
        )
        run = self.submit_run(request)
        if not wait:
            return run
        return self.wait_for_run(run["runId"], timeout=timeout, poll_interval=poll_interval)

    def submit_run(self, request: RunRequest) -> Run:
        """Submit a prepared RunRequest. Prefer ``run`` unless you build requests yourself."""
        return cast(Run, self._request("POST", "/v1/runs", json=request))

    def get_run(self, run_id: str) -> Run:
        """Read a Run and its current result. Free."""
        return cast(Run, self._request("GET", f"/v1/runs/{run_id}"))

    def wait_for_run(self, run_id: str, *, timeout: float = 120.0, poll_interval: float = 1.0) -> Run:
        """Poll until the Run reaches a final status. On timeout, return the Run as it is."""
        deadline = time.monotonic() + timeout
        run = self.get_run(run_id)
        while run["status"] not in FINAL_RUN_STATUSES and time.monotonic() < deadline:
            time.sleep(poll_delay(run, poll_interval, deadline - time.monotonic()))
            run = self.get_run(run_id)
        return run

    def cancel_run(self, run_id: str) -> Run:
        """Request cancellation. Read the returned billing; a request is not a refund."""
        return cast(Run, self._request("POST", f"/v1/runs/{run_id}/cancel"))

    def list_runs(self, **params: Any) -> RunPage:
        """List your Runs. Filters: status, activeOnly, toolId, createdFrom, createdTo, cursor, limit."""
        return cast(RunPage, self._request("GET", "/v1/runs", params=params))

    def balance(self) -> Balance:
        return cast(Balance, self._request("GET", "/v1/balance"))

    def list_tools(self, **params: Any) -> ToolPage:
        """Filters: providerId, category, cursor, limit."""
        return cast(ToolPage, self._request("GET", "/v1/tools", params=params))

    def list_providers(self) -> ProviderCatalog:
        return cast(ProviderCatalog, self._request("GET", "/v1/providers"))

    def catalog(self) -> PublicCatalog:
        return cast(PublicCatalog, self._request("GET", "/v1/catalog"))

    def platform_endpoints(self, platform: str, **params: Any) -> PublicEndpointPage:
        path = f"/v1/catalog/{platform}/endpoints"
        return cast(PublicEndpointPage, self._request("GET", path, params=params))

    def rankings(self) -> PublicRankings:
        return cast(PublicRankings, self._request("GET", "/v1/rankings"))

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        query = drop_none(params or {}) or None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.request(
                    method, self._base_url + path, json=json, params=query, headers=self._headers
                )
            except httpx.TransportError as cause:
                if attempt < self._max_retries:
                    time.sleep(backoff(attempt))
                    continue
                raise network_error(cause) from cause
            if response.is_success:
                return response.json()
            error = response_error(response)
            if error.retryable and attempt < self._max_retries:
                time.sleep(retry_after(response) or backoff(attempt))
                continue
            raise error
        raise AssertionError("unreachable")
