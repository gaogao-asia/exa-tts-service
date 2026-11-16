"""Real Kokoro synthesis. Slow: loads torch and the model (run inside the image).

    pytest -m slow
"""

import io

import pytest

pytest.importorskip("kokoro")

import soundfile as sf  # noqa: E402

from app.settings import SAMPLE_RATE  # noqa: E402
from app.synthesizer import KokoroSynthesizer  # noqa: E402

pytestmark = pytest.mark.slow

FORMATS = [f for f in ("mp3", "wav") if f.upper() in sf.available_formats()]


@pytest.fixture(scope="module", params=FORMATS)
def synth(request):
    return request.param, KokoroSynthesizer(request.param)


def test_produces_a_readable_clip(synth):
    audio_format, synthesize = synth

    audio = synthesize("Yesterday I went to the market and bought some fruit.")

    data, sample_rate = sf.read(io.BytesIO(audio))
    assert sample_rate == SAMPLE_RATE
    assert len(data) / sample_rate > 1.0  # a real sentence, not silence
    if audio_format == "wav":
        assert audio[:4] == b"RIFF"


def test_long_text_is_joined_into_one_clip(synth):
    _, synthesize = synth
    sentence = "There was a traditional event, and we had a nice time chatting. "

    data, sample_rate = sf.read(io.BytesIO(synthesize(sentence * 12)))

    assert len(data) / sample_rate > 20.0
