from __future__ import annotations

import asyncio
import inspect as pyinspect
import json
from typing import Any, Callable

import httpx
import pytest

import purrkey._client as sync_module
import purrkey._async_client as async_module
from purrkey import AsyncPurrkey, Purrkey, PurrkeyError


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sync_module.time, "sleep", lambda _: None)

    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(async_module.asyncio, "sleep", instant)


def inspection(unit: str = "call", unit_price: str = "10000") -> dict[str, Any]:
    return {
        "tool": {
            "toolId": "demo.search",
            "toolRevision": "rev-1",
            "name": "demo.search",
            "price": {
                "priceRevision": "price-1",
                "currency": "USD",
                "unit": unit,
                "unitPriceMicros": unit_price,
                "description": "per call",
                "validUntil": "2099-01-01T00:00:00Z",
            },
        },
        "inputSchema": {},
        "outputSchema": {},
        "inputExamples": [],
        "limitations": [],
        "supportsCancel": False,
        "behavior": {},
        "delivery": {},
    }


def run(status: str, **extra: Any) -> dict[str, Any]:
    return {
        "runId": "run_1",
        "toolId": "demo.search",
        "toolRevision": "rev-1",
        "status": status,
        "billing": {"status": "HELD", "chargedMicros": "0"},
        "createdAt": "2026-11-20T00:00:00Z",
        "updatedAt": "2026-11-20T00:00:00Z",
        "result": {"status": "available" if status == "SUCCEEDED" else "pending"},
        **extra,
    }


class Api:
    """Records requests and answers them with a handler."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        return self.handler(request)

    def submits(self) -> list[dict[str, Any]]:
        return [body for method, path, body in self.calls if path == "/v1/runs" and method == "POST"]


def sync_client(api: Api) -> Purrkey:
    return Purrkey("test-key", http_client=httpx.Client(transport=httpx.MockTransport(api)))


def async_client(api: Api) -> AsyncPurrkey:
    return AsyncPurrkey("test-key", http_client=httpx.AsyncClient(transport=httpx.MockTransport(api)))


def test_run_submits_inspected_revisions_and_waits() -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json=inspection())
        if request.url.path == "/v1/runs":
            return httpx.Response(202, json=run("RUNNING", pollAfterMs=1))
        polls["n"] += 1
        return httpx.Response(200, json=run("SUCCEEDED" if polls["n"] > 1 else "RUNNING"))

    api = Api(handler)
    result = sync_client(api).run("demo.search", {"q": "x"})

    assert result["status"] == "SUCCEEDED"
    [submit] = api.submits()
    assert submit["toolRevision"] == "rev-1"
    assert submit["priceRevision"] == "price-1"
    assert submit["maxChargeMicros"] == "10000"
    assert submit["funding"] == {"mode": "balance"}
    assert submit["idempotencyKey"].startswith("sdk-")
    assert api.calls[0][2] == {"toolId": "demo.search"}


def test_lost_submission_is_retried_with_the_same_key() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json=inspection())
        if request.url.path == "/v1/runs":
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ReadTimeout("lost", request=request)
        return httpx.Response(200, json=run("SUCCEEDED"))

    api = Api(handler)
    sync_client(api).run("demo.search", {})

    keys = [body["idempotencyKey"] for body in api.submits()]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_unknown_stops_waiting_without_resubmitting() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json=inspection())
        if request.url.path == "/v1/runs":
            return httpx.Response(202, json=run("RUNNING"))
        return httpx.Response(200, json=run("UNKNOWN"))

    api = Api(handler)
    assert sync_client(api).run("demo.search", {})["status"] == "UNKNOWN"
    assert len(api.submits()) == 1


def test_input_dependent_price_requires_a_cap() -> None:
    api = Api(lambda _: httpx.Response(200, json=inspection("result", "500")))
    with pytest.raises(PurrkeyError) as caught:
        sync_client(api).run("demo.search", {})
    assert caught.value.code == "max_charge_required"
    assert api.submits() == []


@pytest.mark.parametrize("bad", [1.5, -1, "1e6", True])
def test_invalid_caps_are_rejected(bad: Any) -> None:
    api = Api(lambda _: httpx.Response(200, json=inspection()))
    with pytest.raises(PurrkeyError) as caught:
        sync_client(api).run("demo.search", {}, max_charge_micros=bad)
    assert caught.value.code == "invalid_max_charge"


def test_large_caps_stay_exact() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json=inspection("result", "1"))
        return httpx.Response(200, json=run("SUCCEEDED"))

    api = Api(handler)
    sync_client(api).run("demo.search", {}, max_charge_micros=9007199254740993, wait=False)
    assert api.submits()[0]["maxChargeMicros"] == "9007199254740993"


def test_non_retryable_error_is_raised_once() -> None:
    api = Api(
        lambda _: httpx.Response(
            402,
            json={"requestId": "req_1", "error": {"code": "insufficient_balance", "message": "Top up", "retryable": False}},
        )
    )
    with pytest.raises(PurrkeyError) as caught:
        sync_client(api).balance()
    assert (caught.value.status, caught.value.code, caught.value.request_id) == (402, "insufficient_balance", "req_1")
    assert len(api.calls) == 1


def test_server_errors_are_retried() -> None:
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "x", "retryable": True}})
        return httpx.Response(200, json={"balanceMicros": "1"})

    assert sync_client(Api(handler)).balance() == {"balanceMicros": "1"}
    assert attempts["n"] == 2


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PURRKEY_API_KEY", raising=False)
    with pytest.raises(PurrkeyError, match="PURRKEY_API_KEY"):
        Purrkey()


def test_async_run_retries_with_the_same_key() -> None:
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/inspect":
            return httpx.Response(200, json=inspection())
        if request.url.path == "/v1/runs":
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx.ConnectError("lost", request=request)
            return httpx.Response(202, json=run("RUNNING"))
        return httpx.Response(200, json=run("SUCCEEDED"))

    api = Api(handler)

    async def main() -> dict[str, Any]:
        async with async_client(api) as client:
            return dict(await client.run("demo.search", {}))

    assert asyncio.run(main())["status"] == "SUCCEEDED"
    keys = [body["idempotencyKey"] for body in api.submits()]
    assert len(keys) == 2 and keys[0] == keys[1]


def test_sync_and_async_clients_have_the_same_methods() -> None:
    def public(cls: type) -> set[str]:
        return {name for name, _ in pyinspect.getmembers(cls, callable) if not name.startswith("_")}

    assert public(Purrkey) == public(AsyncPurrkey)
