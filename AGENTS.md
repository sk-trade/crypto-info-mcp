# Repository guide

Read `README.md` and `CONTRIBUTING.md` before changing code.

The service boundary is small and intentional:

- `src/contracts.py`: public schemas, stable error codes, domain result types
- `src/formatting.py`: pure bounded formatting
- `src/main.py`: environment, upstream I/O, MCP tools, lifecycle
- `tests/`: offline contract and boundary verification

Do not move network or environment access into contracts or formatting. Do not change MCP schemas or error prefixes without updating contract tests and documentation. Do not add live credentials or network-dependent default tests.

Validate every change with:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q
uv run python -m compileall -q src example scripts tests
```

For container changes, also run `docker compose config --quiet` and build the Docker image.
