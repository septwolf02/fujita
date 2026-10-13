"""Project-scoped Content Block TTS orchestration."""

from __future__ import annotations

import io
import re
import threading
import wave
from copy import deepcopy
from pathlib import Path
from typing import Any

from portable_paths import normalize_project_asset_path
from storage import WorkspaceStorage
from tts_providers import (
    FakeTTSProvider,
    GeneratedAudio,
    TTSGenerationRequest,
    TTSProvider,
    TTSProviderError,
    VoicevoxTTSProvider,
)


class TTSProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, TTSProvider] = {}

    def register(self, provider: TTSProvider) -> None:
        if not provider.name or not re.fullmatch(r"[a-z0-9_-]+", provider.name):
            raise ValueError("provider name must use lowercase letters, numbers, hyphens, or underscores")
        self._providers[provider.name] = provider

    def get(self, name: str) -> TTSProvider:
        try:
            return self._providers[name]
        except KeyError as error:
            raise ValueError(f"unknown TTS provider: {name}") from error

    def names(self) -> list[str]:
        return sorted(self._providers)


def default_tts_registry(storage: WorkspaceStorage | None = None) -> TTSProviderRegistry:
    registry = TTSProviderRegistry()
    registry.register(FakeTTSProvider())
    config = storage.config().get("tts", {}) if storage else {}
    voicevox = config.get("voicevox", {}) if isinstance(config, dict) else {}
    if not isinstance(voicevox, dict):
        voicevox = {}
    registry.register(
        VoicevoxTTSProvider(
            engine_url=str(voicevox.get("engine_url", "http://127.0.0.1:50021")),
            speaker_name=str(voicevox.get("speaker_name", "剣崎雌雄")),
            style_name=str(voicevox.get("style_name", "ノーマル")),
            required_credit=voicevox.get("required_credit"),
            timeout=voicevox.get("timeout", 30.0),
        )
    )
    return registry


class TTSEngine:
    def __init__(self, storage: WorkspaceStorage, providers: TTSProviderRegistry | None = None):
        self.storage = storage
        self.providers = providers or default_tts_registry(storage)
        self._lock = threading.RLock()

    def provider_settings(self) -> dict[str, Any]:
        config = self.storage.config()
        legacy = config.get("tts_generation", {})
        if not isinstance(legacy, dict):
            legacy = {}
        tts = config.get("tts", {})
        if not isinstance(tts, dict):
            raise ValueError("tts config must be an object")
        provider = str(tts.get("provider", legacy.get("provider", "fake")))
        provider_config = tts.get(provider, {})
        if not isinstance(provider_config, dict):
            raise ValueError(f"tts.{provider} config must be an object")
        try:
            speed = float(provider_config.get("speed", legacy.get("speed", 1.0)))
        except (TypeError, ValueError) as error:
            raise ValueError("TTS speed must be a number") from error
        if speed <= 0:
            raise ValueError("TTS speed must be greater than zero")
        return {
            "provider": provider,
            "voice_id": str(
                provider_config.get(
                    "speaker_name", provider_config.get("voice_id", legacy.get("voice_id", "fake-ja"))
                )
            ),
            "speed": speed,
            "style": str(
                provider_config.get("style_name", provider_config.get("style", legacy.get("style", "neutral")))
            ),
            "engine_url": provider_config.get("engine_url"),
        }

    def _find_block(self, block_id: str) -> tuple[list[dict[str, Any]], int]:
        blocks = self.storage.load_blocks()
        index = next((index for index, block in enumerate(blocks) if block["block_id"] == block_id), None)
        if index is None:
            raise ValueError(f"content block does not exist: {block_id}")
        return blocks, index

    def build_request(self, block: dict[str, Any], version: int) -> TTSGenerationRequest:
        settings = self.provider_settings()
        return TTSGenerationRequest(
            block_id=block["block_id"],
            jp_text=block["jp_text"],
            voice_id=settings["voice_id"],
            speed=settings["speed"],
            style=settings["style"],
            version=version,
            metadata={"block": block.copy(), "language": "ja"},
        )

    def _next_version(self, block: dict[str, Any]) -> int:
        expression = re.compile(rf"^{re.escape(block['block_id'])}_(\d+)\.[A-Za-z0-9]+$")
        versions: list[int] = []
        for relative in block["audio_assets"]:
            match = expression.fullmatch(Path(relative).name)
            if match:
                versions.append(int(match.group(1)))
        audio_root = self.storage.active_project_root() / "audio"
        for path in audio_root.glob(f"{block['block_id']}_*.*"):
            match = expression.fullmatch(path.name)
            if match:
                versions.append(int(match.group(1)))
        return max(versions, default=0) + 1

    @staticmethod
    def _validate_result(result: GeneratedAudio) -> tuple[str, float]:
        if not isinstance(result, GeneratedAudio) or not result.content:
            raise TTSProviderError("TTS provider returned no audio data")
        extension = result.extension.lower().lstrip(".")
        if extension != "wav":
            raise TTSProviderError("Module 4 currently requires WAV output")
        if not isinstance(result.duration, (int, float)) or result.duration <= 0:
            raise TTSProviderError("TTS provider returned an invalid duration")
        try:
            with wave.open(io.BytesIO(result.content), "rb") as wav:
                actual_duration = wav.getnframes() / wav.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError) as error:
            raise TTSProviderError("TTS provider returned an invalid WAV file") from error
        return extension, round(actual_duration, 3)

    def generate(self, block_id: str, provider_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            with self.storage.project_transaction():
                return self._generate(block_id, provider_name)

    def _generate(self, block_id: str, provider_name: str | None) -> dict[str, Any]:
        blocks, index = self._find_block(block_id)
        original_blocks = deepcopy(blocks)
        block = blocks[index]
        version = self._next_version(block)
        provider = self.providers.get(provider_name or self.provider_settings()["provider"])
        request = self.build_request(block, version)
        try:
            result = provider.generate_voice(request)
        except TTSProviderError:
            raise
        except Exception as error:
            raise TTSProviderError(f"TTS provider failed: {error}") from error
        extension, duration = self._validate_result(result)
        relative = f"audio/{block_id}_{version:02d}.{extension}"
        destination = self.storage.active_project_root() / relative
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        try:
            temporary.write_bytes(result.content)
            temporary.replace(destination)
            updated = block.copy()
            updated["audio_assets"] = [*block["audio_assets"], relative]
            updated["audio_file"] = relative
            updated["duration"] = duration
            blocks[index] = updated
            blocks = self.storage.save_blocks(blocks)
            updated = blocks[index]
            credit = result.metadata.get("credit") if isinstance(result.metadata, dict) else None
            if credit is not None:
                self.storage.add_project_credits([credit])
        except Exception:
            temporary.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            try:
                self.storage.save_blocks(original_blocks)
            except Exception:
                pass
            raise
        return {"block": updated, "blocks": blocks, "generated": relative}

    def generate_all(self, provider_name: str | None = None) -> dict[str, Any]:
        with self._lock:
            with self.storage.project_transaction():
                blocks = self.storage.load_blocks()
                original_blocks = deepcopy(blocks)
                provider = self.providers.get(provider_name or self.provider_settings()["provider"])
                pending: list[tuple[int, str, bytes, float, dict[str, Any] | None]] = []
                for index, block in enumerate(blocks):
                    version = self._next_version(block)
                    request = self.build_request(block, version)
                    try:
                        result = provider.generate_voice(request)
                    except TTSProviderError:
                        raise
                    except Exception as error:
                        raise TTSProviderError(f"TTS provider failed: {error}") from error
                    extension, duration = self._validate_result(result)
                    credit = result.metadata.get("credit") if isinstance(result.metadata, dict) else None
                    pending.append(
                        (
                            index,
                            f"audio/{block['block_id']}_{version:02d}.{extension}",
                            result.content,
                            duration,
                            credit,
                        )
                    )

                written: list[Path] = []
                temporary_paths: list[Path] = []
                try:
                    for index, relative, content, duration, _credit in pending:
                        destination = self.storage.active_project_root() / relative
                        temporary = destination.with_suffix(destination.suffix + ".tmp")
                        temporary_paths.append(temporary)
                        temporary.write_bytes(content)
                        temporary.replace(destination)
                        written.append(destination)
                        updated = blocks[index].copy()
                        updated["audio_assets"] = [*updated["audio_assets"], relative]
                        updated["audio_file"] = relative
                        updated["duration"] = duration
                        blocks[index] = updated
                    blocks = self.storage.save_blocks(blocks)
                    self.storage.add_project_credits(
                        [credit for _, _, _, _, credit in pending if credit is not None]
                    )
                except Exception:
                    for path in written:
                        path.unlink(missing_ok=True)
                    for path in temporary_paths:
                        path.unlink(missing_ok=True)
                    try:
                        self.storage.save_blocks(original_blocks)
                    except Exception:
                        pass
                    raise
                return {
                    "blocks": blocks,
                    "generated": [relative for _, relative, _, _, _ in pending],
                }

    @staticmethod
    def _wav_duration(path: Path) -> float:
        try:
            with wave.open(str(path), "rb") as wav:
                return round(wav.getnframes() / wav.getframerate(), 3)
        except (wave.Error, EOFError, ZeroDivisionError) as error:
            raise ValueError("selected audio is not a valid WAV file") from error

    def select_audio(self, block_id: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            with self.storage.project_transaction():
                normalized = normalize_project_asset_path(relative_path, "audio_file", "audio")
                blocks, index = self._find_block(block_id)
                block = blocks[index].copy()
                if normalized not in block["audio_assets"]:
                    raise ValueError("selected audio must belong to the content block")
                path = self.storage.active_project_root() / normalized
                if not path.is_file():
                    raise ValueError("selected audio file does not exist")
                block["audio_file"] = normalized
                block["duration"] = self._wav_duration(path)
                blocks[index] = block
                blocks = self.storage.save_blocks(blocks)
                return {"block": blocks[index], "blocks": blocks}

