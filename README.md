# exa-tts-service

Internal text-to-speech service for EXA expression audio. It turns a corrected sentence or a suggested phrasing into an audio clip with [Kokoro](https://github.com/hexgrad/kokoro) (`hexgrad/Kokoro-82M`, voice `af_heart`, American English).

Only `exa-dashboard-api` calls it. The API caches every clip in S3, so this service does no caching and stores nothing. It never logs request text, which is learner content.

## API

| Route | Auth | Result |
|---|---|---|
| `POST /synthesize` `{"text": "..."}` | `Authorization: Bearer <TTS_SERVICE_TOKEN>` | `200` audio bytes (`audio/mpeg` or `audio/wav`) · `400` missing or blank text · `401` bad token · `413` body over 1 MB · `500` synthesis failed |
| `GET /health` | none | `{status, model, voice, langCode, sampleRate, format, kokoroVersion, misakiVersion, torchVersion}` |

Voice, language and speed (1.0) are fixed in [app/settings.py](app/settings.py). The browser changes playback speed, never this service.

## Configuration

[`.env.example`](.env.example) owns the available environment variables and
their local defaults. For local compose, copy it to an untracked `.env.dev`
(optional). On ECS, set them on the task definition.

## Run locally

```bash
docker network create exa-internal          # once per host, shared with exa-dashboard-api
docker compose --profile dev up --build      # http://localhost:8090, and http://exa-tts:8080 on exa-internal

curl -X POST http://localhost:8090/synthesize \
  -H 'Content-Type: application/json' \
  -d '{"text": "Yesterday I went to the market."}' -o sample.mp3
```

The image bakes the model and the voice in at build time and runs with `HF_HUB_OFFLINE=1`, so it needs no internet at runtime.

Running outside Docker needs Python 3.12 and the system `espeak-ng` package (`apt-get install espeak-ng`), then `pip install -r requirements-dev.txt`. Kokoro's text front end (misaki) normally uses the espeak-ng copy bundled in `espeakng-loader`, but on Linux that copy looks for its data under a build-time path and kills the process. [app/synthesizer.py](app/synthesizer.py) switches it to the system library.

## Tests

```bash
pytest -m "not slow"     # HTTP contract with a fake synthesizer; no torch needed
pytest                   # also runs the real Kokoro smoke tests (slow)

# Everything inside the production image, offline model:
docker run --rm --user root -v "$PWD/tests:/service/tests:ro" -v "$PWD/pytest.ini:/service/pytest.ini:ro" \
  exa-tts-service:local sh -c "pip install -q pytest==9.2.0 && python -m pytest -q"
```

## Deployment (ECS Fargate)

This runs as its **own ECS service** with its own task definition, never as a sidecar in the API task: synthesis is CPU-heavy and would slow every API request, and a crash would stop the API task. The task definition lives in the infrastructure repository. It needs:

- **Image:** published with `scripts/publish-image.sh --env <development|staging|production>`. The ECR repositories `exa-{dev,stg,prod}-exa-tts-service` must exist. Deploy by digest and keep ECR image scanning on.
- **Task:** linux/amd64, starting at 1 vCPU / 3 GB, then sized from the Fargate benchmark. Set `TTS_WORKERS` to about the vCPU count. Fargate has no GPU.
- **Container:** port 8080, `TTS_SERVICE_TOKEN` from Secrets Manager, `awslogs` to CloudWatch. Health check: `python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"`, with a start period of at least 60 s (each worker loads the model).
- **Service:** desired count ≥ 1, private subnets, `assignPublicIp: DISABLED`, Service Connect name `exa-tts`. The security group allows 8080 only from the exa-dashboard-api security group. No load balancer.
- **API side:** the exa-dashboard-api task gets `KOKORO_TTS_URL=http://exa-tts:8080` and the same `TTS_SERVICE_TOKEN`.
- **Capacity:** gunicorn runs sync workers, one synthesis each. Extra requests queue, and the health check waits behind them, so a long queue can fail health checks. Size workers and the desired count from the benchmark rather than relying on the queue.

## Pinned versions

[requirements.txt](requirements.txt) pins every package that can change pronunciation: `kokoro 0.9.4`, `misaki 0.9.4`, `torch 2.15.0+cpu`, `spacy 3.8.18` + `en_core_web_sm 3.8.0`, `phonemizer-fork 3.3.2`, `soundfile 0.14.0` (bundled libsndfile 1.2.2, MP3 supported). The base image is `python:3.12-slim-bookworm`, which provides the `espeak-ng` apt package. The image is about 2.9 GB uncompressed.

## Latency

Measured locally (WSL, 22 cores, 2 workers, warm): 1.7–3.9 s for 17–118 characters. These are **not** production numbers. Run the benchmark on the Fargate task size before release (spec: investigate if first generation regularly takes more than 5 s) and record the results here:

| Task size | 50 chars p50/p95 | 150 chars | 400 chars | 1000 chars | 5 concurrent |
|---|---|---|---|---|---|
| _1 vCPU / 3 GB_ | | | | | |
| _2 vCPU / 4 GB_ | | | | | |

## Upgrade runbook

Any change to the pins, the base image or [app/settings.py](app/settings.py) can change how clips sound. Cached clips in S3 are keyed by `EXPRESSION_AUDIO_KEY_VERSION` (exa-dashboard-api), not by these versions.

1. Bump the pins and rebuild the image.
2. Synthesize a fixed sample set of real grammar and phrasing texts with the old and the new image, and listen to both.
3. If the sound changed, bump `EXPRESSION_AUDIO_KEY_VERSION` in exa-dashboard-api in the same release, so learners get freshly generated clips.
4. Publish, then deploy the new digest.
