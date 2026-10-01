FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home \
        --home-dir /home/app --shell /usr/sbin/nologin app \
    && mkdir -p /app/var/uploads \
    && chown -R app:app /app /home/app

COPY --chown=app:app pyproject.toml .
COPY --chown=app:app app ./app
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app alembic.ini .

RUN pip install --no-cache-dir .

USER app:app
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=3).read()"]

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
