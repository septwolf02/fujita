"""Offline fake Japanese TTS provider producing valid WAV audio."""

from __future__ import annotations

import io
import math
import struct
import wave

from .base import GeneratedAudio, TTSGenerationRequest, TTSProvider, TTSProviderError


class FakeTTSProvider(TTSProvider):
    name = "fake"

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.requests: list[TTSGenerationRequest] = []

    def generate_voice(self, request: TTSGenerationRequest) -> GeneratedAudio:
        self.requests.append(request)
        if self.fail:
            raise TTSProviderError("Fake TTS provider failure")
        speed = max(request.speed, 0.1)
        duration = max(0.5, min(5.0, len(request.jp_text) * 0.08 / speed))
        sample_rate = 16_000
        frame_count = round(duration * sample_rate)
        frequency = 180 + sum(request.jp_text.encode("utf-8")) % 120
        amplitude = 2_500
        frames = bytearray()
        for index in range(frame_count):
            envelope = min(1.0, index / 800, (frame_count - index) / 800)
            sample = round(amplitude * envelope * math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.extend(struct.pack("<h", sample))
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(frames)
        return GeneratedAudio(content=output.getvalue(), duration=frame_count / sample_rate)

