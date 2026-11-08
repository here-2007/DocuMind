FROM python:3.12.10-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.6.17 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY backend ./backend
ARG INSTALL_OCR=0
ENV UV_HTTP_TIMEOUT=120
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ "$INSTALL_OCR" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
      && rm -rf /var/lib/apt/lists/* && uv sync --frozen --no-dev --extra ocr; \
    else uv sync --frozen --no-dev; fi
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
COPY alembic.ini ./
COPY migrations ./migrations
RUN useradd --create-home app && mkdir /objects && chown -R app:app /app /objects
USER app
CMD ["uvicorn", "documind.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
