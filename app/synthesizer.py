"""Kokoro text-to-speech. One instance is created per worker process at startup."""

import contextlib
import io

import numpy as np
import soundfile as sf
from kokoro import KPipeline

from app.settings import LANG_CODE, MODEL_REPO_ID, SAMPLE_RATE, SPEED, VOICE


def _use_system_espeak() -> None:
    """Point phonemizer at the system espeak-ng (apt package `espeak-ng`).

    On import, misaki points phonemizer at the espeak-ng copy bundled in
    `espeakng-loader`. On Linux that copy looks for its data under a build-time
    path and exits the whole process when it is missing. The system library
    and its data always match.
    """
    from misaki import espeak  # noqa: F401  (its import sets the bundled paths)
    from phonemizer.backend.espeak.wrapper import EspeakWrapper

    EspeakWrapper.set_library(None)
    EspeakWrapper.set_data_path(None)


class KokoroSynthesizer:
    """Turns text into one audio clip (mp3 or wav bytes)."""

    def __init__(self, audio_format: str):
        sf_format = audio_format.upper()
        if sf_format not in sf.available_formats():
            raise RuntimeError(
                f"soundfile cannot write {sf_format} here; set AUDIO_FORMAT=wav"
            )
        self._sf_format = sf_format
        _use_system_espeak()
        # Loading the pipeline takes seconds, so it happens once per worker.
        self._pipeline = KPipeline(lang_code=LANG_CODE, repo_id=MODEL_REPO_ID)
        self._pipeline.load_voice(VOICE)

    def __call__(self, text: str) -> bytes:
        # misaki print()s words it cannot parse (learner text) to stdout, which
        # would end up in the container logs. Workers are single-threaded.
        with contextlib.redirect_stdout(io.StringIO()):
            # Long text is split into several chunks; join them into one clip.
            chunks = [
                np.asarray(audio, dtype=np.float32)
                for _, _, audio in self._pipeline(text, voice=VOICE, speed=SPEED)
                if audio is not None
            ]
        if not chunks:
            raise RuntimeError("Kokoro returned no audio")

        buffer = io.BytesIO()
        sf.write(buffer, np.concatenate(chunks), SAMPLE_RATE, format=self._sf_format)
        return buffer.getvalue()
