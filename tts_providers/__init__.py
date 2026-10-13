"""TTS provider adapters."""

from .base import GeneratedAudio, TTSGenerationRequest, TTSProvider, TTSProviderError
from .fake import FakeTTSProvider
from .voicevox import VoicevoxTTSProvider

__all__ = [
    "FakeTTSProvider",
    "GeneratedAudio",
    "TTSGenerationRequest",
    "TTSProvider",
    "TTSProviderError",
    "VoicevoxTTSProvider",
]

