# crypto-info-mcp

`crypto-info-mcp` is a read-only FastMCP service for concise cryptocurrency market context. It combines Alternative.me sentiment, CoinGecko market data, and selected public Telegram channels. It does not trade, manage portfolios, or provide financial advice.

## MCP tools

| Tool | Result | Required configuration |
| --- | --- | --- |
| `get_market_overview` | Korean-language sentiment, BTC/ETH dominance, and recent whale-alert summary | CoinGecko and Telegram sections degrade independently when unconfigured |
| `get_coin_details(coin_id)` | CoinGecko name, symbol, market-cap rank, KRW price, and homepage | `COINGECKO_API_KEY` |
| `get_realtime_news(hours=1)` | Bounded previews and stable channel/message references for the previous 1–72 hours | Telegram credentials and an authorized session |
| `get_telegram_message(channel, message_id)` | Full text for an allowlisted Telegram message | Telegram credentials and an authorized session |

The news tool returns `channel` and `message_id`; pass both to `get_telegram_message` when a full post is needed. Allowlisted channels are `watcherguru`, `wublockchainenglish`, and `whale_alert_io`.

## Code map

The service is a small modular monolith with explicit boundaries:

- `src/contracts.py` contains stable error codes, schemas, and result types.
- `src/formatting.py` contains bounded, side-effect-free response formatting.
- `src/main.py` owns environment loading, upstream integrations, MCP tools, and server lifecycle.
- `example/` contains the protocol smoke client and an optional Gemini client.
- `tests/` contains offline contract, boundary, deployment, and client tests.

Keep external payload validation at the integration boundary. Keep formatting helpers independent of network clients. Changes to MCP tool names, input schemas, structured output, or error-code prefixes are public contract changes.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)

## Run locally

```bash
uv sync --frozen
uv run python -m src.main
```

The streamable HTTP endpoint is `http://127.0.0.1:8123/mcp`.

In another terminal, verify the real MCP endpoint:

```bash
uv run python example/smoke_client.py
```

The smoke client checks the four required tools and calls `get_market_overview`. Missing optional credentials are reported as unavailable sections rather than startup failures.

## Configuration

Create a local file from the checked-in example and set only the integrations you use:

```bash
test -f .env || cp .env.example .env
```

| Variable | Purpose |
| --- | --- |
| `COINGECKO_API_KEY` | CoinGecko demo API key for dominance and coin details |
| `TELEGRAM_API_ID` | Telegram application ID |
| `TELEGRAM_API_HASH` | Telegram application hash |
| `TELEGRAM_SESSION_STRING` | Authorized Telethon string session |
| `LOG_LEVEL` | Optional server log level; defaults to `INFO` |
| `VERSION` | Optional Compose image tag; defaults to `local` |

Do not commit `.env`, session strings, API keys, or captured upstream responses containing credentials.

## Verification

Run the same quality gates used by CI:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
uv run python -m compileall -q src example scripts tests
```

To apply formatting locally:

```bash
uv run ruff format .
```

Default tests are offline and use fakes at external-service boundaries.

## Docker

Build and run the non-root image:

```bash
docker build --build-arg VERSION=local -t crypto-info-mcp:local .
docker run --rm -p 8123:8123 --env-file .env crypto-info-mcp:local
```

The image contains only the locked runtime dependencies and `src/`. It runs as an unprivileged user.

## Docker Compose

Compose expects an existing external network named `bridge_server`:

```bash
test -f .env || cp .env.example .env
docker network inspect bridge_server >/dev/null 2>&1 \
  || docker network create bridge_server
docker compose config --quiet
docker compose up --build
```

The endpoint is then available at `http://127.0.0.1:18123/mcp`. The Compose service uses a read-only filesystem, drops Linux capabilities, and passes only server configuration.

## Telegram session generation

After setting `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`, run:

```bash
uv run python scripts/generate_session.py
```

Complete the interactive Telegram login, then copy the emitted value to `TELEGRAM_SESSION_STRING` in `.env`.

## Optional Gemini client

The conversational example is not part of the server runtime dependencies. Supply its SDK only for that command:

```bash
GOOGLE_API_KEY=your-key \
  uv run --with google-genai python example/client.py --host localhost --port 8123
```

The Google key is used only by the example client and is not sent to the MCP server.

## Failure behavior

- Alternative.me, CoinGecko, and Telegram failures are isolated where the tool contract permits partial results.
- CoinGecko and Telegram calls have bounded timeouts.
- Telegram news previews and upstream text fields are length-bounded before inclusion in tool output.
- Tool errors start with a stable machine-readable code such as `coin_not_found` or `telegram_timeout`.
