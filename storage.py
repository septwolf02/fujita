"""Portable, project-aware storage for the local production workspace."""

from __future__ import annotations

import json
import os
import re
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from module1 import validate_blocks
from module2 import (
    DEFAULT_CHARACTERS,
    DEFAULT_PERIOD_KITS,
    validate_characters,
    validate_period_kits,
    validate_scenes,
)
PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SCHEMA_VERSION = 6
PROJECT_DIRECTORIES = ("images", "audio", "thumbnails", "temp", "output")
CREDIT_FIELDS = ("type", "provider", "speaker", "style", "text")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_workspace_root(app_root: Path) -> Path:
    configured = os.environ.get("VS_WORKSPACE_ROOT") or os.environ.get("FUJITA_WORKSPACE_ROOT")
    return Path(configured).expanduser() if configured else app_root / "workspace_data"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {path}: {error}") from error


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _slugify(title: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return value[:48] or "project"


def validate_project_credits(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("project credits must be a list")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for position, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"project credit {position} must be an object")
        normalized: dict[str, str] = {}
        for field in CREDIT_FIELDS:
            field_value = item.get(field)
            if not isinstance(field_value, str) or not field_value.strip():
                raise ValueError(f"project credit {position} has invalid {field}")
            normalized[field] = field_value.strip()
        key = tuple(normalized[field] for field in CREDIT_FIELDS)
        if key not in seen:
            result.append(normalized)
            seen.add(key)
    return result


class WorkspaceStorage:
    def __init__(self, workspace_root: str | os.PathLike[str], legacy_data_dir: Path | None = None):
        self.root = Path(workspace_root).expanduser()
        self.legacy_data_dir = Path(legacy_data_dir) if legacy_data_dir else None
        self.config_path = self.root / "config.json"
        self.library_root = self.root / "library"
        self.projects_root = self.root / "projects"
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.library_root / "characters").mkdir(parents=True, exist_ok=True)
            (self.library_root / "period_kits").mkdir(parents=True, exist_ok=True)
            (self.library_root / "styles").mkdir(parents=True, exist_ok=True)
            self.projects_root.mkdir(parents=True, exist_ok=True)

            config = _read_json(self.config_path, {})
            if not isinstance(config, dict):
                raise RuntimeError(f"Invalid workspace config: {self.config_path}")
            config["schema_version"] = SCHEMA_VERSION
            config.setdefault("active_project_id", None)
            config.setdefault(
                "image_generation",
                {"provider": "fake", "aspect_ratio": "16:9", "quality": "standard"},
            )
            config.setdefault(
                "tts_generation",
                {"provider": "fake", "voice_id": "fake-ja", "speed": 1.0, "style": "neutral"},
            )
            legacy_tts = config["tts_generation"]
            if not isinstance(legacy_tts, dict):
                legacy_tts = {}
            config.setdefault(
                "tts",
                {
                    "provider": legacy_tts.get("provider", "fake"),
                    "fake": {
                        "voice_id": legacy_tts.get("voice_id", "fake-ja"),
                        "speed": legacy_tts.get("speed", 1.0),
                        "style": legacy_tts.get("style", "neutral"),
                    },
                    "voicevox": {
                        "engine_url": "http://127.0.0.1:50021",
                        "speaker_name": "剣崎雌雄",
                        "style_name": "ノーマル",
                    },
                },
            )
            config.setdefault(
                "video_render",
                {
                    "renderer": "ffmpeg",
                    "ffmpeg_path": os.environ.get("VS_FFMPEG_PATH")
                    or os.environ.get("FUJITA_FFMPEG_PATH", "ffmpeg"),
                    "width": 1920,
                    "height": 1080,
                    "fps": 30,
                    "video_codec": "libx264",
                    "audio_codec": "aac",
                },
            )
            if not self.list_projects():
                legacy_names = ("content_blocks.json", "visual_scenes.json")
                has_legacy_project = bool(
                    self.legacy_data_dir
                    and any((self.legacy_data_dir / name).is_file() for name in legacy_names)
                )
                project = self._create_project(
                    "Imported Project" if has_legacy_project else "Untitled Project",
                    "legacy-import" if has_legacy_project else "project-001",
                )
                config["active_project_id"] = project["project_id"]
            elif not config["active_project_id"]:
                config["active_project_id"] = self.list_projects()[0]["project_id"]
            _write_json(self.config_path, config)
            self._migrate_legacy_if_needed()
            self._initialize_libraries()

    def config(self) -> dict[str, Any]:
        value = _read_json(self.config_path, {})
        if not isinstance(value, dict):
            raise RuntimeError(f"Invalid workspace config: {self.config_path}")
        return value

    @contextmanager
    def project_transaction(self):
        """Pin the active project while a multi-step filesystem action runs."""
        with self._lock:
            yield self.active_project_root()

    def _save_config(self, config: dict[str, Any]) -> None:
        config["schema_version"] = SCHEMA_VERSION
        _write_json(self.config_path, config)

    def list_projects(self) -> list[dict[str, Any]]:
        if not self.projects_root.exists():
            return []
        projects: list[dict[str, Any]] = []
        for metadata_path in sorted(self.projects_root.glob("*/project.json")):
            metadata = _read_json(metadata_path, {})
            if isinstance(metadata, dict) and PROJECT_ID.fullmatch(str(metadata.get("project_id", ""))):
                metadata["credits"] = validate_project_credits(metadata.get("credits", []))
                projects.append(metadata)
        return projects

    def _unique_project_id(self, title: str) -> str:
        base = _slugify(title)
        candidate = base
        counter = 2
        while (self.projects_root / candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def _create_project(self, title: str, project_id: str | None = None) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("project title is required")
        identifier = project_id or self._unique_project_id(clean_title)
        if not PROJECT_ID.fullmatch(identifier):
            raise ValueError("project_id must use lowercase letters, numbers, hyphens, or underscores")
        project_root = self.projects_root / identifier
        if project_root.exists():
            raise ValueError(f"project already exists: {identifier}")
        project_root.mkdir(parents=True)
        for directory in PROJECT_DIRECTORIES:
            (project_root / directory).mkdir()
        metadata = {
            "project_id": identifier,
            "title": clean_title,
            "created_at": utc_now(),
            "language": "ja",
            "status": "draft",
            "credits": [],
        }
        _write_json(project_root / "project.json", metadata)
        _write_json(project_root / "content_blocks.json", [])
        _write_json(project_root / "visual_scenes.json", [])
        _write_json(
            project_root / "timeline.json",
            {"project_id": identifier, "total_duration": 0.0, "items": [], "warnings": []},
        )
        return metadata

    def create_project(self, title: str, project_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            project = self._create_project(title, project_id)
            config = self.config()
            config["active_project_id"] = project["project_id"]
            self._save_config(config)
            return project

    def switch_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            project = self.get_project(project_id)
            config = self.config()
            config["active_project_id"] = project_id
            self._save_config(config)
            return project

    def get_project(self, project_id: str) -> dict[str, Any]:
        if not PROJECT_ID.fullmatch(project_id):
            raise ValueError("invalid project_id")
        path = self.projects_root / project_id / "project.json"
        if not path.is_file():
            raise ValueError(f"project does not exist: {project_id}")
        metadata = _read_json(path, {})
        if not isinstance(metadata, dict):
            raise RuntimeError(f"Invalid project metadata: {path}")
        metadata["credits"] = validate_project_credits(metadata.get("credits", []))
        return metadata

    def active_project(self) -> dict[str, Any]:
        project_id = self.config().get("active_project_id")
        if not isinstance(project_id, str):
            raise RuntimeError("workspace has no active project")
        return self.get_project(project_id)

    def active_project_root(self) -> Path:
        return self.projects_root / self.active_project()["project_id"]

    def load_project_credits(self, project_id: str | None = None) -> list[dict[str, str]]:
        project = self.get_project(project_id) if project_id else self.active_project()
        return deepcopy(project["credits"])

    def add_project_credits(self, credits: Any) -> list[dict[str, str]]:
        additions = validate_project_credits(credits)
        with self._lock:
            project = self.active_project()
            merged = validate_project_credits([*project["credits"], *additions])
            project["credits"] = merged
            _write_json(self.active_project_root() / "project.json", project)
            return deepcopy(merged)

    def load_blocks(self) -> list[dict[str, Any]]:
        return validate_blocks(_read_json(self.active_project_root() / "content_blocks.json", []))

    def save_blocks(self, blocks: Any) -> list[dict[str, Any]]:
        with self._lock:
            normalized = validate_blocks(blocks)
            valid_ids = {block["block_id"] for block in normalized}
            scenes = validate_scenes(self.load_scenes_raw(), valid_ids)
            scene_ids_by_block = {block_id: [] for block_id in valid_ids}
            for scene in scenes:
                for block_id in scene["source_blocks"]:
                    scene_ids_by_block[block_id].append(scene["scene_id"])
            for block in normalized:
                block["scene_ids"] = scene_ids_by_block[block["block_id"]]
            _write_json(self.active_project_root() / "content_blocks.json", normalized)
            return normalized

    def load_scenes_raw(self) -> list[dict[str, Any]]:
        value = _read_json(self.active_project_root() / "visual_scenes.json", [])
        if not isinstance(value, list):
            raise ValueError("scenes must be a list")
        return value

    def load_scenes(self) -> list[dict[str, Any]]:
        block_ids = {block["block_id"] for block in self.load_blocks()}
        return validate_scenes(self.load_scenes_raw(), block_ids)

    def save_scenes(self, scenes: Any) -> list[dict[str, Any]]:
        with self._lock:
            blocks = self.load_blocks()
            normalized = validate_scenes(scenes, {block["block_id"] for block in blocks})
            _write_json(self.active_project_root() / "visual_scenes.json", normalized)
            scene_ids_by_block = {block["block_id"]: [] for block in blocks}
            for scene in normalized:
                for block_id in scene["source_blocks"]:
                    scene_ids_by_block[block_id].append(scene["scene_id"])
            for block in blocks:
                block["scene_ids"] = scene_ids_by_block[block["block_id"]]
            _write_json(self.active_project_root() / "content_blocks.json", validate_blocks(blocks))
            return normalized

    def load_timeline(self) -> dict[str, Any]:
        value = _read_json(
            self.active_project_root() / "timeline.json",
            {
                "project_id": self.active_project()["project_id"],
                "total_duration": 0.0,
                "items": [],
                "warnings": [],
            },
        )
        if not isinstance(value, dict):
            raise ValueError("timeline must be an object")
        return value

    def save_timeline(self, timeline: Any) -> dict[str, Any]:
        if not isinstance(timeline, dict):
            raise ValueError("timeline must be an object")
        with self._lock:
            _write_json(self.active_project_root() / "timeline.json", timeline)
        return timeline

    @property
    def characters_root(self) -> Path:
        return self.library_root / "characters"

    @property
    def period_kits_root(self) -> Path:
        return self.library_root / "period_kits"

    def _initialize_libraries(self) -> None:
        if not (self.characters_root / "index.json").exists():
            self.save_characters(DEFAULT_CHARACTERS)
        if not (self.period_kits_root / "index.json").exists():
            self.save_period_kits(DEFAULT_PERIOD_KITS)

    def load_characters(self) -> list[dict[str, Any]]:
        index = _read_json(self.characters_root / "index.json", {"character_ids": []})
        ids = index.get("character_ids", []) if isinstance(index, dict) else []
        values = [_read_json(self.characters_root / character_id / "character.json", {}) for character_id in ids]
        return validate_characters(values)

    def save_characters(self, characters: Any) -> list[dict[str, Any]]:
        with self._lock:
            normalized = validate_characters(characters)
            for character in normalized:
                root = self.characters_root / character["character_id"]
                (root / "face_references").mkdir(parents=True, exist_ok=True)
                (root / "expression_references").mkdir(parents=True, exist_ok=True)
                _write_json(root / "character.json", character)
            _write_json(
                self.characters_root / "index.json",
                {"character_ids": [character["character_id"] for character in normalized]},
            )
            return normalized

    def load_period_kits(self) -> list[dict[str, Any]]:
        index = _read_json(self.period_kits_root / "index.json", {"period_ids": []})
        ids = index.get("period_ids", []) if isinstance(index, dict) else []
        values = [_read_json(self.period_kits_root / period_id / "period_kit.json", {}) for period_id in ids]
        return validate_period_kits(values)

    def save_period_kits(self, period_kits: Any) -> list[dict[str, Any]]:
        with self._lock:
            normalized = validate_period_kits(period_kits)
            for kit in normalized:
                root = self.period_kits_root / kit["period_id"]
                root.mkdir(parents=True, exist_ok=True)
                _write_json(root / "period_kit.json", kit)
            _write_json(
                self.period_kits_root / "index.json",
                {"period_ids": [kit["period_id"] for kit in normalized]},
            )
            return normalized

    def _migrate_legacy_if_needed(self) -> None:
        config = self.config()
        if config.get("legacy_migration", {}).get("version") == SCHEMA_VERSION:
            return
        report: dict[str, Any] = {"version": SCHEMA_VERSION, "completed_at": utc_now(), "copied": [], "preserved_sources": []}
        legacy = self.legacy_data_dir
        project_root = self.active_project_root()
        if legacy and legacy.is_dir():
            mappings = {
                "content_blocks.json": project_root / "content_blocks.json",
                "visual_scenes.json": project_root / "visual_scenes.json",
            }
            for source_name, destination in mappings.items():
                source = legacy / source_name
                if source.is_file():
                    report["preserved_sources"].append(source_name)
                    existing = _read_json(destination, [])
                    if not existing:
                        _write_json(destination, _read_json(source, []))
                        report["copied"].append(source_name)

            legacy_characters = legacy / "character_bible.json"
            if legacy_characters.is_file() and not (self.characters_root / "index.json").exists():
                self.save_characters(_read_json(legacy_characters, DEFAULT_CHARACTERS))
                report["copied"].append("character_bible.json")
                report["preserved_sources"].append("character_bible.json")
            legacy_periods = legacy / "period_kits.json"
            if legacy_periods.is_file() and not (self.period_kits_root / "index.json").exists():
                self.save_period_kits(_read_json(legacy_periods, DEFAULT_PERIOD_KITS))
                report["copied"].append("period_kits.json")
                report["preserved_sources"].append("period_kits.json")

        # Validate and upgrade the migrated schemas without changing their meaning.
        blocks = validate_blocks(_read_json(project_root / "content_blocks.json", []))
        _write_json(project_root / "content_blocks.json", blocks)
        scenes = validate_scenes(
            _read_json(project_root / "visual_scenes.json", []),
            {block["block_id"] for block in blocks},
        )
        self.save_scenes(scenes)
        config["legacy_migration"] = report
        self._save_config(config)

