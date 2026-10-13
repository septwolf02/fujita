"""Provider-neutral image generation contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    """A provider could not produce a usable image."""


@dataclass(frozen=True)
class ImageGenerationRequest:
    scene_id: str
    prompt: str
    references: tuple[Path, ...] = ()
    aspect_ratio: str = "16:9"
    quality: str = "standard"
    variation_index: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    extension: str = "png"
    media_type: str = "image/png"


class ImageProvider(ABC):
    """Implement this class to add a real or local image provider.

    Provider adapters obtain credentials from their own runtime configuration,
    normally environment variables. Credentials never belong in scene data.
    """

    name: str
    required_environment_variables: tuple[str, ...] = ()

    @abstractmethod
    def generate_image(self, request: ImageGenerationRequest) -> GeneratedImage:
        raise NotImplementedError

