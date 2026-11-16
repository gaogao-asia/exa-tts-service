# ---- base: system deps, pinned Python deps, baked model weights ----
# Pinned Debian release: the espeak-ng apt version affects pronunciation.
FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models \
    PORT=8080

WORKDIR /service

RUN apt-get update \
    && apt-get install -y --no-install-recommends espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt pins the CPU-only torch wheel (no CUDA, much smaller image).
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && groupadd --system appgroup \
    && useradd --system --gid appgroup --no-create-home appuser

# Bake the model and the voice so the runtime never needs the internet.
COPY app/__init__.py app/settings.py ./app/
COPY app/synthesizer.py ./app/synthesizer.py
RUN python -c "from app.synthesizer import KokoroSynthesizer; KokoroSynthesizer('wav')('Ready.')" \
    && chmod -R a+rX /models

# Torch prints harmless deprecation warnings on every model load.
ENV HF_HUB_OFFLINE=1 \
    PYTHONWARNINGS="ignore::UserWarning,ignore::FutureWarning"

# The system espeak-ng audio layer (pulse) wants a writable ~/.config and
# logs an error on every worker start without one.
RUN mkdir -p /home/appuser && chown appuser:appgroup /home/appuser

EXPOSE 8080


# ---- dev: Flask dev server; compose mounts ./app for live edits ----
FROM base AS dev

ENV FLASK_ENV=development

COPY app/ ./app/

USER appuser

HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["python", "-m", "app.server"]


# ---- staging: production code under gunicorn ----
FROM base AS staging

ENV FLASK_ENV=staging

COPY app/ ./app/

USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# One Kokoro pipeline per worker. Keep TTS_WORKERS close to the task's vCPU.
CMD ["sh", "-c", "exec gunicorn --workers ${TTS_WORKERS:-2} --timeout ${TTS_TIMEOUT_S:-30} --bind 0.0.0.0:${PORT} 'app.server:create_app()'"]


# ---- prod: same as staging, production env ----
FROM base AS prod

ENV FLASK_ENV=production

COPY app/ ./app/

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["sh", "-c", "exec gunicorn --workers ${TTS_WORKERS:-2} --timeout ${TTS_TIMEOUT_S:-30} --bind 0.0.0.0:${PORT} 'app.server:create_app()'"]
