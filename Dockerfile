FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

RUN groupadd --system governance \
    && useradd --system --gid governance --home-dir /app governance

COPY pyproject.toml README.md /app/
COPY src /app/src
RUN python -m pip install --no-cache-dir ".[online]"

RUN mkdir -p /app/workspace \
    && chown -R governance:governance /app
USER governance

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"

CMD ["python", "-m", "governance_app.server", "--workspace", "/app/workspace", "--host", "0.0.0.0", "--port", "8765"]
