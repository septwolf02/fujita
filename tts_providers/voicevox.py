"""VOICEVOX Engine adapter using only Python's standard library."""

from __future__ import annotations

import io
import json
import wave
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .base import GeneratedAudio, TTSGenerationRequest, TTSProvider, TTSProviderError


OpenUrl = Callable[..., Any]


class VoicevoxTTSProvider(TTSProvider):
    name = "voicevox"

    def __init__(
        self,
        engine_url: str,
        speaker_name: str,
        style_name: str,
        required_credit: str | None = None,
        timeout: float = 30.0,
        opener: OpenUrl | None = None,
    ):
        self.engine_url = engine_url.rstrip("/")
        self.speaker_name = speaker_name.strip()
        self.style_name = style_name.strip()
        self.required_credit = required_credit.strip() if isinstance(required_credit, str) else ""
        try:
            self.timeout = float(timeout)
        except (TypeError, ValueError):
            self.timeout = 0.0
        self.opener = opener or urlopen

    def _validate_config(self) -> None:
        parsed = urlparse(self.engine_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise TTSProviderError("VOICEVOX engine_url phải là HTTP URL hợp lệ")
        if not self.speaker_name or not self.style_name:
            raise TTSProviderError("VOICEVOX speaker_name và style_name là bắt buộc")
        if self.timeout <= 0:
            raise TTSProviderError("VOICEVOX timeout phải lớn hơn 0")

    def _request_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> bytes:
        headers = {"Accept": "application/json, audio/wav"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(
            f"{self.engine_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self.opener(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as error:
            try:
                detail = error.read().decode("utf-8", errors="replace").strip()
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise TTSProviderError(
                f"VOICEVOX Engine trả về HTTP {error.code} cho {path}{suffix}"
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise TTSProviderError(
                f"Không thể kết nối VOICEVOX Engine tại {self.engine_url}. "
                f"Hãy mở VOICEVOX và kiểm tra engine_url. Chi tiết: {reason}"
            ) from error

    def _request_json(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> Any:
        raw = self._request_bytes(method, path, data, content_type)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise TTSProviderError(f"VOICEVOX trả về JSON không hợp lệ cho {path}") from error

    def check_health(self) -> str:
        self._validate_config()
        raw = self._request_bytes("GET", "/version")
        try:
            value = json.loads(raw.decode("utf-8"))
            return str(value)
        except (UnicodeDecodeError, json.JSONDecodeError):
            value = raw.decode("utf-8", errors="replace").strip()
            if not value:
                raise TTSProviderError("VOICEVOX /version trả về nội dung rỗng")
            return value

    def resolve_speaker_id(self) -> int:
        self._validate_config()
        speakers = self._request_json("GET", "/speakers")
        if not isinstance(speakers, list):
            raise TTSProviderError("VOICEVOX /speakers trả về dữ liệu không hợp lệ")
        speaker = next(
            (
                item
                for item in speakers
                if isinstance(item, dict) and item.get("name") == self.speaker_name
            ),
            None,
        )
        if speaker is None:
            available = ", ".join(
                str(item.get("name")) for item in speakers if isinstance(item, dict) and item.get("name")
            )
            raise TTSProviderError(
                f"Không tìm thấy VOICEVOX speaker '{self.speaker_name}'."
                + (f" Speaker hiện có: {available}" if available else "")
            )
        styles = speaker.get("styles", [])
        if not isinstance(styles, list):
            raise TTSProviderError(f"VOICEVOX speaker '{self.speaker_name}' có styles không hợp lệ")
        style = next(
            (
                item
                for item in styles
                if isinstance(item, dict) and item.get("name") == self.style_name
            ),
            None,
        )
        if style is None or isinstance(style.get("id"), bool) or not isinstance(style.get("id"), int):
            available = ", ".join(
                str(item.get("name")) for item in styles if isinstance(item, dict) and item.get("name")
            )
            raise TTSProviderError(
                f"Không tìm thấy style '{self.style_name}' của speaker '{self.speaker_name}'."
                + (f" Style hiện có: {available}" if available else "")
            )
        return style["id"]

    @staticmethod
    def _wav_duration(content: bytes) -> float:
        try:
            with wave.open(io.BytesIO(content), "rb") as wav:
                return wav.getnframes() / wav.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError) as error:
            raise TTSProviderError("VOICEVOX synthesis không trả về WAV hợp lệ") from error

    def generate_voice(self, request: TTSGenerationRequest) -> GeneratedAudio:
        self.check_health()
        speaker_id = self.resolve_speaker_id()
        query_path = "/audio_query?" + urlencode(
            {"text": request.jp_text, "speaker": speaker_id}
        )
        query = self._request_json(
            "POST", query_path, data=b"", content_type="application/json"
        )
        if not isinstance(query, dict):
            raise TTSProviderError("VOICEVOX audio_query trả về dữ liệu không hợp lệ")
        query["speedScale"] = request.speed
        synthesis_path = "/synthesis?" + urlencode({"speaker": speaker_id})
        wav_content = self._request_bytes(
            "POST",
            synthesis_path,
            data=json.dumps(query, ensure_ascii=False).encode("utf-8"),
            content_type="application/json; charset=utf-8",
        )
        duration = self._wav_duration(wav_content)
        metadata = {}
        if self.required_credit:
            metadata["credit"] = {
                "type": "tts",
                "provider": self.name,
                "speaker": self.speaker_name,
                "style": self.style_name,
                "text": self.required_credit,
            }
        return GeneratedAudio(content=wav_content, duration=duration, metadata=metadata)
