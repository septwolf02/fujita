"""Project-scoped image generation orchestration."""

from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from image_providers import (
    FakeImageProvider,
    GeneratedImage,
    ImageGenerationRequest,
    ImageProvider,
    ProviderError,
)
from portable_paths import normalize_project_asset_path
from storage import WorkspaceStorage


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, ImageProvider] = {}

    def register(self, provider: ImageProvider) -> None:
        if not provider.name or not re.fullmatch(r"[a-z0-9_-]+", provider.name):
            raise ValueError("provider name must use lowercase letters, numbers, hyphens, or underscores")
        self._providers[provider.name] = provider

    def get(self, name: str) -> ImageProvider:
        try:
            return self._providers[name]
        except KeyError as error:
            raise ValueError(f"unknown image provider: {name}") from error

    def names(self) -> list[str]:
        return sorted(self._providers)


def default_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(FakeImageProvider())
    return registry


class ImageGenerationEngine:
    def __init__(self, storage: WorkspaceStorage, providers: ProviderRegistry | None = None):
        self.storage = storage
        self.providers = providers or default_registry()
        self._lock = threading.RLock()

    def provider_settings(self) -> dict[str, str]:
        settings = self.storage.config().get("image_generation", {})
        return {
            "provider": str(settings.get("provider", "fake")),
            "aspect_ratio": str(settings.get("aspect_ratio", "16:9")),
            "quality": str(settings.get("quality", "standard")),
        }

    def _find_scene(self, scene_id: str) -> tuple[list[dict[str, Any]], int]:
        scenes = self.storage.load_scenes()
        index = next((index for index, scene in enumerate(scenes) if scene["scene_id"] == scene_id), None)
        if index is None:
            raise ValueError(f"scene does not exist: {scene_id}")
        return scenes, index

    def _character_context(self, character_ids: list[str]) -> tuple[list[dict[str, Any]], tuple[Path, ...]]:
        by_id = {character["character_id"]: character for character in self.storage.load_characters()}
        context: list[dict[str, Any]] = []
        references: list[Path] = []
        for character_id in character_ids:
            character = by_id.get(character_id)
            if not character:
                continue
            context.append(
                {
                    "character_id": character_id,
                    "name": character["name"],
                    "face_profile": character["face_profile"],
                    "body_profile": character["body_profile"],
                }
            )
            root = self.storage.characters_root / character_id
            paths = [character.get("face_reference"), *character.get("reference_images", [])]
            paths.extend(character.get("expression_references", []))
            for relative in paths:
                if not relative:
                    continue
                path = root / relative
                if path.is_file():
                    references.append(path)
        return context, tuple(dict.fromkeys(references))

    def _period_context(self, period: str) -> dict[str, Any] | None:
        key = period.strip().lower()
        if not key:
            return None
        for kit in self.storage.load_period_kits():
            if key in {kit["period_id"].lower(), kit["name"].lower()}:
                return kit
        return None

    def build_request(self, scene: dict[str, Any], variation_index: int = 1) -> ImageGenerationRequest:
        characters, references = self._character_context(scene["characters"])
        period_kit = self._period_context(scene["period"])
        prompt_parts = [scene["image_prompt"].strip()]
        labelled = (
            ("Period", scene["period"]),
            ("Year", scene["year"]),
            ("Location", scene["location"]),
            ("Costume", scene["costume_profile"]),
            ("Hair", scene["hair_profile"]),
            ("Action", scene["action"]),
            ("Emotion", scene["emotion"]),
            ("Shot", scene["shot_type"]),
        )
        prompt_parts.extend(f"{label}: {value}" for label, value in labelled if value)
        for character in characters:
            prompt_parts.append(
                f"Character {character['character_id']} ({character['name']}): "
                f"{character['face_profile']}; {character['body_profile']}"
            )
        if period_kit:
            prompt_parts.append(
                "Period kit: "
                + "; ".join(
                    value
                    for value in (
                        period_kit["accessories"],
                        period_kit["footwear"],
                        period_kit["environment_profile"],
                    )
                    if value
                )
            )
        settings = self.provider_settings()
        return ImageGenerationRequest(
            scene_id=scene["scene_id"],
            prompt="\n".join(part for part in prompt_parts if part),
            references=references,
            aspect_ratio=settings["aspect_ratio"],
            quality=settings["quality"],
            variation_index=variation_index,
            metadata={"scene": scene.copy(), "characters": characters, "period_kit": period_kit},
        )

    def _next_version(self, scene: dict[str, Any]) -> int:
        expression = re.compile(rf"^{re.escape(scene['scene_id'])}_(\d+)\.[A-Za-z0-9]+$")
        versions: list[int] = []
        for relative in scene["image_assets"]:
            match = expression.fullmatch(Path(relative).name)
            if match:
                versions.append(int(match.group(1)))
        images_root = self.storage.active_project_root() / "images"
        for path in images_root.glob(f"{scene['scene_id']}_*.*"):
            match = expression.fullmatch(path.name)
            if match:
                versions.append(int(match.group(1)))
        return max(versions, default=0) + 1

    @staticmethod
    def _validate_result(result: GeneratedImage) -> str:
        if not isinstance(result, GeneratedImage) or not result.content:
            raise ProviderError("Image provider returned no image data")
        extension = result.extension.lower().lstrip(".")
        if extension not in {"png", "jpg", "jpeg", "webp"}:
            raise ProviderError(f"Unsupported generated image extension: {extension}")
        return extension

    def generate(
        self,
        scene_id: str,
        count: int = 1,
        provider_name: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            with self.storage.project_transaction():
                return self._generate(scene_id, count, provider_name)

    def _generate(
        self,
        scene_id: str,
        count: int,
        provider_name: str | None,
    ) -> dict[str, Any]:
        if not isinstance(count, int) or count < 1 or count > 8:
            raise ValueError("image count must be between 1 and 8")
        scenes, index = self._find_scene(scene_id)
        original_scene = scenes[index]
        settings = self.provider_settings()
        provider = self.providers.get(provider_name or settings["provider"])
        first_version = self._next_version(original_scene)

        generated: list[tuple[str, bytes]] = []
        for offset in range(count):
            request = self.build_request(original_scene, variation_index=first_version + offset)
            try:
                result = provider.generate_image(request)
            except ProviderError:
                raise
            except Exception as error:
                raise ProviderError(f"Image provider failed: {error}") from error
            extension = self._validate_result(result)
            filename = f"{scene_id}_{first_version + offset:02d}.{extension}"
            generated.append((f"images/{filename}", result.content))

        project_root = self.storage.active_project_root()
        written: list[Path] = []
        temporary_paths: list[Path] = []
        try:
            for relative, content in generated:
                destination = project_root / relative
                temporary = destination.with_suffix(destination.suffix + ".tmp")
                temporary_paths.append(temporary)
                temporary.write_bytes(content)
                temporary.replace(destination)
                written.append(destination)
            updated = original_scene.copy()
            updated["image_assets"] = [*original_scene["image_assets"], *(path for path, _ in generated)]
            scenes[index] = updated
            self.storage.save_scenes(scenes)
        except Exception:
            for path in written:
                path.unlink(missing_ok=True)
            for path in temporary_paths:
                path.unlink(missing_ok=True)
            raise
        return {"scene": updated, "scenes": scenes, "generated": [path for path, _ in generated]}

    def select_image(self, scene_id: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            with self.storage.project_transaction():
                return self._select_image(scene_id, relative_path)

    def _select_image(self, scene_id: str, relative_path: str) -> dict[str, Any]:
        normalized = normalize_project_asset_path(relative_path, "selected_image", "images")
        scenes, index = self._find_scene(scene_id)
        scene = scenes[index].copy()
        if normalized not in scene["image_assets"]:
            raise ValueError("selected image must belong to the scene")
        if not (self.storage.active_project_root() / normalized).is_file():
            raise ValueError("selected image file does not exist")
        scene["selected_image"] = normalized
        scenes[index] = scene
        self.storage.save_scenes(scenes)
        return {"scene": scene, "scenes": scenes}

    def delete_image(self, scene_id: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            with self.storage.project_transaction():
                return self._delete_image(scene_id, relative_path)

    def _delete_image(self, scene_id: str, relative_path: str) -> dict[str, Any]:
        normalized = normalize_project_asset_path(relative_path, "image asset", "images")
        scenes, index = self._find_scene(scene_id)
        scene = scenes[index].copy()
        if normalized not in scene["image_assets"]:
            raise ValueError("image does not belong to the scene")
        scene["image_assets"] = [path for path in scene["image_assets"] if path != normalized]
        if scene["selected_image"] == normalized:
            scene["selected_image"] = None
        scenes[index] = scene
        self.storage.save_scenes(scenes)
        (self.storage.active_project_root() / normalized).unlink(missing_ok=True)
        return {"scene": scene, "scenes": scenes}

