"""HTTP contract of the TTS service, with a fake synthesizer (no torch needed)."""

import json
import logging

import pytest

from app.server import create_app

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LEARNER_TEXT = "Yesterday I went to the market."


class FakeSynthesizer:
    def __init__(self, result=b"ID3fake-audio", error=None):
        self.calls = []
        self.result = result
        self.error = error

    def __call__(self, text):
        self.calls.append(text)
        if self.error:
            raise self.error
        return self.result


def make_client(synth=None, **kwargs):
    kwargs.setdefault("token", TOKEN)
    kwargs.setdefault("audio_format", "mp3")
    app = create_app(synth or FakeSynthesizer(), **kwargs)
    return app.test_client()


def test_returns_audio_bytes_with_content_type():
    synth = FakeSynthesizer()
    response = make_client(synth).post("/synthesize", json={"text": f"  {LEARNER_TEXT} "}, headers=AUTH)

    assert response.status_code == 200
    assert response.mimetype == "audio/mpeg"
    assert response.data == b"ID3fake-audio"
    assert synth.calls == [LEARNER_TEXT]


def test_wav_format_sets_wav_content_type():
    response = make_client(audio_format="wav").post("/synthesize", json={"text": "Hi."}, headers=AUTH)

    assert response.status_code == 200
    assert response.mimetype == "audio/wav"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}],
)
def test_rejects_missing_or_wrong_token(headers):
    synth = FakeSynthesizer()
    response = make_client(synth).post("/synthesize", json={"text": "Hi."}, headers=headers)

    assert response.status_code == 401
    assert synth.calls == []


@pytest.mark.parametrize("body", [{}, {"text": ""}, {"text": "   "}, {"text": 42}, ["Hi."]])
def test_rejects_missing_or_blank_text(body):
    synth = FakeSynthesizer()
    response = make_client(synth).post("/synthesize", json=body, headers=AUTH)

    assert response.status_code == 400
    assert synth.calls == []


def test_rejects_non_json_body():
    response = make_client().post("/synthesize", data="text=Hi", headers=AUTH)

    assert response.status_code == 400


def test_rejects_oversized_body():
    response = make_client().post("/synthesize", json={"text": "a" * 1_100_000}, headers=AUTH)

    assert response.status_code == 413


def test_synthesis_failure_returns_500_without_leaking_text(caplog):
    synth = FakeSynthesizer(error=RuntimeError(f"cannot read {LEARNER_TEXT}"))
    with caplog.at_level(logging.INFO, logger="exa-tts"):
        response = make_client(synth).post("/synthesize", json={"text": LEARNER_TEXT}, headers=AUTH)

    assert response.status_code == 500
    assert LEARNER_TEXT not in response.get_data(as_text=True)
    assert LEARNER_TEXT not in caplog.text


def test_success_log_has_metrics_but_no_text(caplog):
    with caplog.at_level(logging.INFO, logger="exa-tts"):
        make_client().post("/synthesize", json={"text": LEARNER_TEXT}, headers=AUTH)

    record = json.loads(caplog.records[-1].getMessage())
    assert record["event"] == "synthesize"
    assert record["ok"] is True
    assert record["chars"] == len(LEARNER_TEXT)
    assert isinstance(record["latencyMs"], int)
    assert LEARNER_TEXT not in caplog.text


def test_health_needs_no_token_and_reports_settings():
    response = make_client().get("/health")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["model"] == "hexgrad/Kokoro-82M"
    assert body["voice"] == "af_heart"
    assert body["langCode"] == "a"
    assert body["format"] == "mp3"
    assert {"kokoroVersion", "misakiVersion", "torchVersion"} <= body.keys()


def test_development_without_token_skips_auth():
    response = make_client(token="", env="development").post("/synthesize", json={"text": "Hi."})

    assert response.status_code == 200


@pytest.mark.parametrize("env", ["production", "staging"])
def test_refuses_to_start_without_token_outside_development(env):
    with pytest.raises(RuntimeError, match="TTS_SERVICE_TOKEN"):
        create_app(FakeSynthesizer(), token="", env=env)


def test_refuses_unknown_audio_format():
    with pytest.raises(RuntimeError, match="AUDIO_FORMAT"):
        create_app(FakeSynthesizer(), token=TOKEN, audio_format="ogg")
