FROM debian:bookworm-slim

ARG VERSION
ENV VERSION=${VERSION}

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /usr/src/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    cron \
    fonts-nanum \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock* ./

RUN uv sync --frozen --python 3.11 --no-install-project

COPY . .

ENV PATH="/usr/src/app/.venv/bin:$PATH"

EXPOSE 8123

CMD ["python", "-m", "src.main"]