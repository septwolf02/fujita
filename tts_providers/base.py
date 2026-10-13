"""Provider-neutral Japanese TTS contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class TTSProviderError(RuntimeError):
    """A TTS provider could not produce usable audio."""


@dataclass(frozen=True)
class TTSGenerationRequest:
    block_id: str
    jp_text: str
    voice_id: str
    speed: float = 1.0
    style: str = "neutral"
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedAudio:
    content: bytes
    duration: float
    extension: str = "wav"
    media_type: str = "audio/wav"
    metadata: dict[str, Any] = field(default_factory=dict)


class TTSProvider(ABC):
    """Implement this class to connect a real or local TTS provider.

    Adapters read credentials from runtime configuration such as environment
    variables. Credentials never belong in Content Block JSON.
    """

    name: str
    required_environment_variables: tuple[str, ...] = ()

    @abstractmethod
    def generate_voice(self, request: TTSGenerationRequest) -> GeneratedAudio:
        raise NotImplementedError

