FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config ./config
RUN uv sync --frozen --no-dev
# Data, migrations and loaders for the setup container; config for the app.
COPY .chainlit ./.chainlit
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts
COPY support-data ./support-data

RUN useradd --system --create-home appuser && chown appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH" SUPPORT_DATA_DIR=/app/support-data
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=10 --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"
CMD ["chainlit", "run", "src/supplyguard/app/chainlit_app.py", "--host", "0.0.0.0", "--port", "8000", "--headless"]
