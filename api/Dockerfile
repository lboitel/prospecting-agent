FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.15 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dépendances d'abord : couche mise en cache tant que uv.lock ne change pas.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --system --uid 1000 app
USER app

EXPOSE 8000

# Migrations appliquées au démarrage : une seule instance de l'API, donc pas de course.
CMD ["sh", "-c", "alembic upgrade head && uvicorn prospecting.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
