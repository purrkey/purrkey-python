# Purrkey Python SDK

[![PyPI](https://img.shields.io/pypi/v/purrkey)](https://pypi.org/project/purrkey/)

The official Python client for [Purrkey](https://purrkey.app/?utm_source=github&utm_medium=readme&utm_campaign=geo_launch), a pay-per-call tool marketplace for AI agents. One API key reaches 1,100+ data tools: social media from TikTok, Instagram, YouTube, Xiaohongshu and Douyin, Amazon and eBay listings, Google Maps reviews, SEO data, and web search and scraping. Every tool has a published price, and each call is paid from one balance.

Python 3.10+. Sync and async clients. Fully typed.

## Install

```sh
pip install purrkey
```

## Quick start

Create a key at [purrkey.app/keys](https://purrkey.app/keys?utm_source=github&utm_medium=readme&utm_campaign=geo_launch) and set it as `PURRKEY_API_KEY`. New accounts get $1 in free credit.

```python
from purrkey import Purrkey

with Purrkey() as purrkey:  # reads PURRKEY_API_KEY
    # 1. Find a tool (free)
    candidates = purrkey.discover("google maps reviews")["candidates"]

    # 2. Read its schema and price (free)
    tool = purrkey.inspect("tikhub.tiktok.app.v3.fetch_video_search_result")
    print(tool["tool"]["price"])  # {'unit': 'call', 'unitPriceMicros': '1000', ...}

    # 3. Run it and wait for the result
    run = purrkey.run(
        "tikhub.tiktok.app.v3.fetch_video_search_result",
        {"query": {"keyword": "matcha latte", "count": 10}},
    )
    print(run["status"])                    # SUCCEEDED
    print(run["billing"]["chargedMicros"])  # 1000 = $0.001
    print(run["result"]["output"])
```

Async:

```python
import asyncio
from purrkey import AsyncPurrkey

async def main() -> None:
    async with AsyncPurrkey() as purrkey:
        run = await purrkey.run("anyapi.web.scrape", {"url": "https://purrkey.app/about"})
        print(run["result"]["output"]["data"]["markdown"])

asyncio.run(main())
```

Browse the [tool catalog](https://purrkey.app/tools?utm_source=github&utm_medium=readme&utm_campaign=geo_launch) to see what is available.

## Paying safely

`run()` handles the rules that keep a task from being charged twice:

- **Spending cap.** Each Run carries `maxChargeMicros`, the most it may cost (1 USD = 1,000,000 micros). For tools priced per call, the SDK uses the inspected price. When the price depends on the input (per result, per URL, per credit), pass `max_charge_micros` yourself; otherwise `run()` raises `max_charge_required` before anything is charged.
- **Idempotency.** Every Run has an `idempotencyKey`. If a response is lost, the SDK sends the same request again with the same key, and Purrkey returns the original Run instead of running and charging again. Pass your own key to make retries safe across processes:

  ```python
  purrkey.run(tool_id, payload, idempotency_key=f"report-{report_id}")
  ```

  A new key means a new, separately charged task.
- **Unknown outcomes.** `UNKNOWN` means the provider's outcome needs reconciliation. The SDK stops waiting and returns the Run. Check it later with `get_run(run_id)`; don't submit the task again.
- **Billing is separate from status.** Read `run["billing"]` (`status`, `heldMicros`, `chargedMicros`). A failed Run can still be charged for verified work when the tool's pricing allows it.

Account-wide daily and per-run caps can be set at [purrkey.app/budget](https://purrkey.app/budget?utm_source=github&utm_medium=readme&utm_campaign=geo_launch).

## API

| Method | What it does | Cost |
| --- | --- | --- |
| `discover(query, *, limit, provider_id, category)` | Find tool candidates for one task | Free |
| `inspect(tool_id, *, tool_revision)` | Exact input/output schema, price and delivery rules | Free |
| `run(tool_id, input, *, max_charge_micros, idempotency_key, wait=True, timeout=120)` | Inspect, submit and (by default) wait for a final status | Tool price |
| `submit_run(request)` | Submit a prepared `RunRequest` | Tool price |
| `get_run(run_id)` | Read a Run and its result | Free |
| `wait_for_run(run_id, *, timeout)` | Poll until a final status; returns the Run as it is on timeout | Free |
| `cancel_run(run_id)` | Request cancellation | — |
| `list_runs(**filters)` | List your Runs (`status`, `toolId`, `cursor`, `limit`, …) | Free |
| `balance()` | Balance and held amounts | Free |
| `list_tools()`, `list_providers()`, `catalog()`, `platform_endpoints(platform)`, `rankings()` | Browse the catalog | Free |

`AsyncPurrkey` has the same methods as coroutines. Client options: `api_key` (default `PURRKEY_API_KEY`), `base_url`, `timeout`, `max_retries` (default 2; network errors, 429 and 5xx), `http_client`.

Responses are plain dicts typed with `TypedDict`s from `purrkey.types`, generated from the [public OpenAPI contract](https://purrkey.app/docs/openapi.yaml).

## Errors

API errors and client-side checks raise `PurrkeyError`:

```python
from purrkey import PurrkeyError

try:
    purrkey.run(tool_id, payload)
except PurrkeyError as error:
    print(error.status, error.code, error.message, error.request_id)
```

Common codes: `insufficient_balance`, `schema_invalid` (input doesn't match the tool's schema), `max_charge_required`, `network_error`.

## Other ways to connect

- [Purrkey plugin](https://github.com/purrkey/plugins) for Claude Code and Cursor (MCP)
- [Purrkey skills](https://github.com/purrkey/skills): task recipes for agents
- [Purrkey CLI](https://github.com/purrkey-ai/cli)
- [TypeScript SDK](https://github.com/purrkey/purrkey-js)
- [API reference](https://purrkey.app/docs?utm_source=github&utm_medium=readme&utm_campaign=geo_launch)

## Development

```sh
uv sync
uv run pytest
uv run mypy
uv run python scripts/generate.py --fetch   # pull the live contract and regenerate types
```

`src/purrkey/types.py` is generated from `openapi/openapi.yaml`; don't edit it by hand. CI fails when they disagree, and a weekly job checks the vendored contract against the live API.

## License

[MIT](LICENSE)
