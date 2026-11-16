"""Internal HTTP wrapper around Kokoro TTS.

    POST /synthesize  {"text": "..."}  -> audio bytes (audio/mpeg or audio/wav)
    GET  /health                        -> versions and fixed settings, no auth

Run with gunicorn (one Kokoro pipeline per worker):
    gunicorn -w 2 -b 0.0.0.0:8080 'app.server:create_app()'

Request text is learner content: it is never logged. Logs carry only
length, size and latency.
"""

import hmac
import json
import logging
import os
import time
from importlib import metadata
from typing import Callable, Optional

from flask import Flask, Response, jsonify, request

from app.settings import (
    CONTENT_TYPES,
    LANG_CODE,
    MAX_BODY_BYTES,
    MODEL_REPO_ID,
    SAMPLE_RATE,
    VOICE,
)

Synthesize = Callable[[str], bytes]

logger = logging.getLogger("exa-tts")


def create_app(
    synthesize: Optional[Synthesize] = None,
    *,
    audio_format: Optional[str] = None,
    token: Optional[str] = None,
    env: Optional[str] = None,
) -> Flask:
    """Build the app. Tests pass a fake `synthesize`; production loads Kokoro."""
    audio_format = (audio_format or os.getenv("AUDIO_FORMAT", "mp3")).lower()
    if audio_format not in CONTENT_TYPES:
        raise RuntimeError(f"AUDIO_FORMAT must be one of {sorted(CONTENT_TYPES)}")

    token = os.getenv("TTS_SERVICE_TOKEN", "") if token is None else token
    env = env or os.getenv("FLASK_ENV", "production")
    if not token and env != "development":
        raise RuntimeError("TTS_SERVICE_TOKEN is required outside development")

    _configure_logging()

    if synthesize is None:
        from app.synthesizer import KokoroSynthesizer  # heavy: torch + model

        synthesize = KokoroSynthesizer(audio_format)

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
    health_body = _health_body(audio_format)

    @app.post("/synthesize")
    def synthesize_route():
        if token and not _is_authorized(request.headers.get("Authorization"), token):
            return jsonify(error="unauthorized"), 401

        payload = request.get_json(silent=True)
        text = payload.get("text") if isinstance(payload, dict) else None
        if not isinstance(text, str) or not text.strip():
            return jsonify(error="text is required"), 400
        text = text.strip()

        started = time.monotonic()
        try:
            audio = synthesize(text)
        except Exception as error:  # noqa: BLE001 - report any failure as 500
            # Log the error type only; messages may quote the input text.
            _log("synthesize", chars=len(text), started=started, ok=False,
                 error=type(error).__name__)
            return jsonify(error="synthesis failed"), 500

        _log("synthesize", chars=len(text), started=started, ok=True, bytes=len(audio))
        return Response(audio, mimetype=CONTENT_TYPES[audio_format])

    @app.get("/health")
    def health_route():
        return jsonify(health_body)

    return app


def _is_authorized(header: Optional[str], token: str) -> bool:
    scheme, _, value = (header or "").partition(" ")
    return scheme == "Bearer" and hmac.compare_digest(value.encode(), token.encode())


def _health_body(audio_format: str) -> dict:
    return {
        "status": "ok",
        "model": MODEL_REPO_ID,
        "voice": VOICE,
        "langCode": LANG_CODE,
        "sampleRate": SAMPLE_RATE,
        "format": audio_format,
        "kokoroVersion": _package_version("kokoro"),
        "misakiVersion": _package_version("misaki"),
        "torchVersion": _package_version("torch"),
    }


def _package_version(name: str) -> Optional[str]:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _log(event: str, *, started: float, **fields) -> None:
    latency_ms = round((time.monotonic() - started) * 1000)
    logger.info(json.dumps({"event": event, "latencyMs": latency_ms, **fields}))


def _configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False


if __name__ == "__main__":
    # Local convenience only; containers run gunicorn.
    create_app().run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
