"""Alibaba Cloud Model Studio image adapter (stdlib only)."""

from __future__ import annotations

import base64
import binascii
import json
import os
import struct
import time
import zlib
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from .base import GeneratedImage, ImageGenerationRequest, ImageProvider, ProviderError


ENDPOINT = "/api/v1/services/aigc/multimodal-generation/generation"
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def valid_png(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    header = False
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        end = offset + length + 12
        if end > len(data):
            return False
        kind = data[offset + 4 : offset + 8]
        chunk = data[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", data[end - 4 : end])[0]
        if zlib.crc32(kind + chunk) & 0xFFFFFFFF != crc:
            return False
        if kind == b"IHDR":
            header = length == 13
        if kind == b"IEND":
            return header and length == 0 and end == len(data)
        offset = end
    return False


class AlibabaQwenImageProvider(ImageProvider):
    name = "alibaba_qwen"

    def __init__(self, base_url: str, model: str = "qwen-mt-image-2.0",
                 api_key_env: str = "VS_ALIBABA_API_KEY", api_key: str | None = None,
                 width: int = 1280, height: int = 720, timeout_seconds: float = 180,
                 opener=None, sleeper=None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key_env = api_key_env
        self.config_api_key = api_key or ""
        self.width = width
        self.height = height
        self.timeout_seconds = timeout_seconds
        self.opener = opener or urlopen
        self.sleeper = sleeper or time.sleep

    def _configuration(self) -> str:
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ProviderError("Alibaba base_url must be an HTTP(S) URL")
        if not self.model or not isinstance(self.width, int) or not isinstance(self.height, int) or self.width <= 0 or self.height <= 0:
            raise ProviderError("Alibaba model and positive image dimensions are required")
        if not isinstance(self.timeout_seconds, (int, float)) or self.timeout_seconds <= 0:
            raise ProviderError("Alibaba timeout_seconds must be positive")
        if not self.api_key_env.isidentifier():
            raise ProviderError("Alibaba api_key_env is invalid")
        key = os.environ.get(self.api_key_env, "").strip() or self.config_api_key.strip()
        if not key:
            raise ProviderError(f"Alibaba API key is missing: set {self.api_key_env}")
        return key

    def _json_request(self, url: str, key: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers.update({"Content-Type": "application/json", "X-DashScope-Async": "enable"})
        request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")[:500]
            raise ProviderError(f"Alibaba HTTP {error.code}: {detail}") from error
        except (URLError, OSError, TimeoutError) as error:
            raise ProviderError(f"Cannot connect to Alibaba Model Studio: {error}") from error
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ProviderError("Alibaba returned invalid JSON") from error
        if not isinstance(result, dict):
            raise ProviderError("Alibaba returned an invalid response")
        return result

    @classmethod
    def _image_value(cls, obj):
        if isinstance(obj, dict):
            for field in ("b64_json", "image", "image_url", "url"):
                value = obj.get(field)
                if isinstance(value, str) and value:
                    return field, value
            for field in ("output", "results", "data", "choices", "message", "content"):
                found = cls._image_value(obj.get(field))
                if found:
                    return found
        if isinstance(obj, list):
            for item in obj:
                found = cls._image_value(item)
                if found:
                    return found
        return None

    def _image_bytes(self, result: dict) -> bytes | None:
        found = self._image_value(result)
        if not found:
            return None
        field, value = found
        if field == "b64_json" or value.startswith("data:image/"):
            encoded = value.split(",", 1)[-1]
            try:
                return base64.b64decode(encoded, validate=True)
            except binascii.Error as error:
                raise ProviderError("Alibaba returned invalid base64 image") from error
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ProviderError("Alibaba returned an invalid image URL")
        # Signed image URLs are downloaded without the API Authorization header.
        try:
            with self.opener(Request(value), timeout=self.timeout_seconds) as response:
                return response.read(MAX_IMAGE_BYTES + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            raise ProviderError(f"Cannot download Alibaba image: {error}") from error

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedImage:
        key = self._configuration()
        content = [{"text": request.prompt}]
        for reference in request.references:
            path = Path(reference)
            try:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            except OSError as error:
                raise ProviderError(f"Cannot read character reference {path.name}: {error}") from error
            content.append({"image": f"data:image/png;base64,{encoded}"})
        payload = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {"size": f"{self.width}*{self.height}", "n": 1,
                           "seed": request.variation_index},
        }
        parsed = urlparse(self.base_url)
        url = self.base_url if parsed.path not in ("", "/") else self.base_url + ENDPOINT
        result = self._json_request(url, key, payload)
        image = self._image_bytes(result)
        task = result.get("output", result)
        task_id = task.get("task_id") if isinstance(task, dict) else None
        deadline = time.monotonic() + self.timeout_seconds
        while image is None and task_id and time.monotonic() < deadline:
            status = str(task.get("task_status", "")).upper()
            if status in {"FAILED", "CANCELED", "CANCELLED"}:
                raise ProviderError(f"Alibaba generation failed: {task.get('message', status)}")
            self.sleeper(1)
            task_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/tasks/{quote(str(task_id), safe='')}"
            result = self._json_request(task_url, key)
            task = result.get("output", result)
            image = self._image_bytes(result)
        if image is None:
            raise ProviderError("Alibaba generation returned no image or timed out")
        if len(image) > MAX_IMAGE_BYTES or not valid_png(image):
            raise ProviderError("Alibaba did not return a valid PNG image")
        return GeneratedImage(content=image, extension="png", media_type="image/png")
