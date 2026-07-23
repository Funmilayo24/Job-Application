FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY tests ./tests
RUN pip install --no-cache-dir ".[dev]"

COPY CV ./CV

RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/artifacts /app/data/cache \
    && chown -R agent:agent /app

USER agent

CMD ["python", "-m", "app.worker"]
