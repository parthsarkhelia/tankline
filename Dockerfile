FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY . .
RUN useradd --create-home app && chown -R app /app
USER app
EXPOSE 8000
# load_stations reads data/fuel-prices.csv, mounted from the host (see compose.yaml); it exits with a clear message if missing.
# --threads: requests mostly wait on the routing API; --preload: lookups load once (see config/wsgi.py).
CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py load_stations && gunicorn config.wsgi -b 0.0.0.0:8000 -w 3 --threads 4 --preload --timeout 60"]
