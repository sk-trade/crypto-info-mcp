# Contributing

## Scope

This repository serves read-only market context. Trading, order execution, portfolio actions, financial advice, and live-network tests in default CI are out of scope.

## Set up

```bash
uv sync --frozen
```

## Required checks

Run these before opening a pull request:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
uv run python -m compileall -q src example scripts tests
```

Run `docker compose config --quiet` when changing Docker or deployment files. Build the image locally when changing the Dockerfile or runtime dependencies.

## Change rules

Keep one logical change per pull request. Explain the problem, the chosen change, and the verification evidence.

Preserve these boundaries:

- Stable MCP schemas, error codes, and result types belong in `src/contracts.py`.
- Pure output shaping belongs in `src/formatting.py`.
- Network calls, environment access, and lifecycle behavior belong in `src/main.py`.
- External payloads must be validated before formatting or exposing them through MCP.
- Default tests must not require network access or real credentials.

Do not add abstractions for hypothetical future integrations. Add a module or shared helper only when it creates a clear dependency boundary or removes demonstrated duplication.

## Public contract changes

Treat tool names, parameters, JSON Schema constraints, structured output, and error-code prefixes as public API. Update tests, README, and changelog together when any of them changes.

## Security

Never commit API keys, Telegram session strings, `.env`, production logs containing secrets, or copied credentials in fixtures. Use synthetic values in tests.

## Pull requests

Use a concise title and complete the repository pull-request template. Link an issue only when one exists; a separate Task document is not required.
