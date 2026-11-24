FROM ghcr.io/astral-sh/uv:0.9.16@sha256:ae9ff79d095a61faf534a882ad6378e8159d2ce322691153d68d2afac7422840 AS uv

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /bin/uv

WORKDIR /usr/src/app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.11-slim-bookworm@sha256:a83ba18aef0f865b1a4a611c2cf460fe446f2795673bbb9fe43be570439e3b39 AS runtime

ARG VERSION=local

LABEL org.opencontainers.image.title="crypto-info-mcp" \
      org.opencontainers.image.description="Read-only cryptocurrency market context over MCP" \
      org.opencontainers.image.version="${VERSION}"

ENV APP_VERSION=${VERSION} \
    PATH="/usr/src/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /usr/src/app

COPY --from=builder --chown=app:app /usr/src/app/.venv ./.venv
COPY --chown=app:app src ./src

USER app

EXPOSE 8123

CMD ["python", "-m", "src.main"]
