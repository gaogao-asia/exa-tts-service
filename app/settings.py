"""Fixed TTS settings shared by the server and the synthesizer.

Kept free of heavy imports so the HTTP layer and its tests load without torch.
Changing any value here changes the generated audio: bump
EXPRESSION_AUDIO_KEY_VERSION in exa-dashboard-api when that happens.
"""

MODEL_REPO_ID = "hexgrad/Kokoro-82M"
VOICE = "af_heart"
LANG_CODE = "a"  # American English, matches the af_* voices
SAMPLE_RATE = 24000
SPEED = 1.0  # playback speed is changed in the browser, never here

CONTENT_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav"}

# Guards the request body only. There is no product limit on text length.
MAX_BODY_BYTES = 1_000_000
