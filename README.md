# crypto-info-mcp

A FastMCP service for crypto market summaries, CoinGecko coin details, and recent Telegram news.

## Tools

- `get_market_overview` - combines Fear & Greed, CoinGecko global market data, and whale alerts when Telegram is configured.
- `get_coin_details(coin_id)` - returns CoinGecko details for a coin ID such as `bitcoin`.
- `get_realtime_news(hours=1)` - collects recent posts from the configured Telegram channels.

## Environment variables

Set these in `.env` before starting the service:

- `COINGECKO_API_KEY` - required for CoinGecko requests.
- `TELEGRAM_API_ID` - required to open the Telegram session.
- `TELEGRAM_API_HASH` - required to open the Telegram session.
- `TELEGRAM_SESSION_STRING` - required to read Telegram channels.

If Telegram variables are missing, market overview still works and reports no whale movement.

## Local run

```bash
uv sync
uv run python -m src.main
```

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

`docker-compose.yml` expects an existing external network named `bridge_server` and an `.env` file in the repo root.

```bash
docker compose up --build
```

## Telegram session generation

Use the helper script to create a Telegram session string after you have a valid API ID and API hash:

```bash
uv run python scripts/generate_session.py
```

Copy the printed session string into `TELEGRAM_SESSION_STRING`.

## Limitations

- CoinGecko and Telegram requests depend on external services and can still fail at runtime.
- Telegram features require a valid session string and channel access.
- Market overview uses best-effort data sources and may omit sections when an upstream API is unavailable.
