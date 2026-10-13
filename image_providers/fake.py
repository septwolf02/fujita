"""Dependency-free fake provider used for local development and tests."""

from __future__ import annotations

import hashlib
import struct
import zlib

from .base import GeneratedImage, ImageGenerationRequest, ImageProvider, ProviderError


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width
    pixels = zlib.compress(row * height, level=9)
    return signature + _png_chunk(b"IHDR", header) + _png_chunk(b"IDAT", pixels) + _png_chunk(b"IEND", b"")


class FakeImageProvider(ImageProvider):
    name = "fake"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.requests: list[ImageGenerationRequest] = []

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedImage:
        self.requests.append(request)
        if self.fail:
            raise ProviderError("Fake provider failure")
        digest = hashlib.sha256(
            f"{request.scene_id}|{request.prompt}|{request.variation_index}".encode("utf-8")
        ).digest()
        color = tuple(48 + byte % 160 for byte in digest[:3])
        return GeneratedImage(content=_solid_png(640, 360, color))

