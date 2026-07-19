# crypto-info-mcp

A FastMCP service for crypto market summaries, CoinGecko coin details, and recent Telegram news.

## Tools

- `get_market_overview` - combines Fear & Greed, CoinGecko global market data, and whale alerts when Telegram is configured.
- `get_coin_details(coin_id)` - returns CoinGecko details for a coin ID such as `bitcoin`.
- `get_realtime_news(hours=1)` - lists bounded previews and returns structured channel/message references for recent posts from the configured Telegram channels.
- `get_telegram_message(channel, message_id)` - retrieves the full text for a listed Telegram post from an allowlisted channel.

Use the `channel` and `message_id` shown by `get_realtime_news` when calling `get_telegram_message`. Supported channels are `watcherguru`, `wublockchainenglish`, and `whale_alert_io`; message IDs must be positive integers.

## Prerequisites

- Python 3.11 or newer.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/).

## Quick start

Install the locked dependencies and start the server. Credentials are optional for this first smoke check.

```bash
uv sync --frozen
uv run python -m src.main
```

In a second terminal, connect through the real MCP HTTP endpoint and call `get_market_overview`:

```bash
uv run python example/smoke_client.py
```

The command lists the four expected tools and prints a Korean market overview. Without Telegram credentials, the overview explicitly reports that whale-alert data is unavailable.

## Environment variables

Create `.env` from the checked-in template, then fill only the integrations you
intend to use:

```bash
test -f .env || cp .env.example .env
```

The template leaves optional credentials blank so the server can complete a
credential-free smoke check. Set these variables when enabling their tools:

- `COINGECKO_API_KEY` - required for CoinGecko requests.
- `TELEGRAM_API_ID` - required to open the Telegram session.
- `TELEGRAM_API_HASH` - required to open the Telegram session.
- `TELEGRAM_SESSION_STRING` - required to read Telegram channels.
- `VERSION` - optional Docker image tag; defaults to `local` in Docker Compose.

If Telegram variables are missing, market overview still works and clearly reports that whale-alert data is unavailable because Telegram is not configured.

The server listens on `0.0.0.0:8123` with the streamable HTTP transport.

## Tests

```bash
uv run pytest -q
uv run python -m compileall src example scripts tests
```

## Docker

Build and run the image directly:

```bash
docker build -t crypto-info-mcp .
docker run --rm -p 8123:8123 --env-file .env crypto-info-mcp
```

## Docker Compose

`docker-compose.yml` expects an existing external network named `bridge_server` and an `.env` file in the repo root. It forwards only the CoinGecko and Telegram server credentials listed above. If `VERSION` is omitted, Compose tags the local image as `crypto-info-mcp:local`.

```bash
test -f .env || cp .env.example .env
docker network inspect bridge_server >/dev/null 2>&1 || docker network create bridge_server
docker compose up --build
```

Compose publishes the MCP endpoint at `http://127.0.0.1:18123/mcp`. Verify the
running container through that endpoint:

```bash
uv run python example/smoke_client.py --url http://127.0.0.1:18123/mcp
```

## Telegram session generation

Use the helper script to create a Telegram session string after you have a valid API ID and API hash in `.env`:

```bash
uv run python scripts/generate_session.py
```

Follow the interactive Telegram login prompt, then copy the printed session string into `TELEGRAM_SESSION_STRING`.

## Limitations

- CoinGecko and Telegram requests depend on external services and can still fail at runtime.
- Telegram features require a valid session string and channel access.
- Market overview reports unavailable Alternative.me and configured CoinGecko sources explicitly; CoinGecko dominance is omitted only when no API key is configured.
