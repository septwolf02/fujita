"""Visual Scene Planner domain model and transformations.

Visual scenes are intentionally independent from content blocks. The
``source_blocks`` list provides a many-to-many link without copying script text.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from portable_paths import normalize_project_asset_path, normalize_relative_path


SCENE_ID = re.compile(r"^VS\d{3,}$")
CHARACTER_ID = re.compile(r"^CHAR_[A-Z0-9_]+$")
PERIOD_ID = re.compile(r"^[A-Z][A-Z0-9_]*$")
MOTIONS = {"hold", "zoom_in", "zoom_out", "pan_left", "pan_right"}
TRANSITIONS = {"cut", "fade"}

SCENE_TEXT_FIELDS = (
    "period",
    "year",
    "location",
    "costume_profile",
    "hair_profile",
    "action",
    "emotion",
    "shot_type",
    "scene_note",
    "image_prompt",
)

DEFAULT_CHARACTERS = [
    {
        "character_id": "CHAR_A",
        "name": "POV chính",
        "role": "Nhân vật nhập vai xuyên suốt kênh",
        "face_profile": "Gương mặt trẻ, đường nét Nhật Bản rõ ràng, biểu cảm dễ đọc",
        "body_profile": "Dáng người cân đối, chiều cao trung bình",
        "reference_images": [],
    },
    {
        "character_id": "CHAR_B",
        "name": "Người dẫn giải",
        "role": "Nhân vật am hiểu bối cảnh lịch sử",
        "face_profile": "Gương mặt trưởng thành, điềm tĩnh, đường nét Nhật Bản",
        "body_profile": "Dáng người mảnh, tư thế tự tin",
        "reference_images": [],
    },
]

DEFAULT_PERIOD_KITS = [
    {
        "period_id": "HEIAN",
        "name": "Heian",
        "year_range": "794–1185",
        "costume_profile": "Trang phục Heian đúng địa vị xã hội; nhiều lớp với giới quý tộc",
        "hair_profile": "Tóc dài tự nhiên hoặc kiểu tóc đúng giới tính và địa vị thời Heian",
        "accessories": "Quạt, mũ quan và vật dụng theo địa vị",
        "footwear": "Giày hoặc dép phù hợp tầng lớp thời Heian",
        "environment_profile": "Kiến trúc gỗ Heian, rèm sudare, ánh sáng tự nhiên dịu",
    },
    {
        "period_id": "SENGOKU",
        "name": "Sengoku",
        "year_range": "1467–1615",
        "costume_profile": "Kosode hoặc giáp chiến phù hợp vai trò và địa vị",
        "hair_profile": "Kiểu tóc thực dụng thời chiến, samurai dùng kiểu tóc đúng thời kỳ",
        "accessories": "Vũ khí, túi và công cụ theo vai trò; tránh vật dụng hiện đại",
        "footwear": "Waraji hoặc dép đúng bối cảnh",
        "environment_profile": "Thành lũy, làng gỗ và đường đất Nhật Bản thời chiến quốc",
    },
    {
        "period_id": "EDO",
        "name": "Edo",
        "year_range": "1603–1868",
        "costume_profile": "Kimono hoặc kosode đúng nghề nghiệp và tầng lớp thời Edo",
        "hair_profile": "Kiểu tóc Edo phù hợp giới tính, tuổi và địa vị",
        "accessories": "Obi, túi, dụng cụ nghề nghiệp; vũ khí chỉ khi phù hợp vai trò",
        "footwear": "Geta, zori hoặc waraji theo nhân vật và địa điểm",
        "environment_profile": "Nhà gỗ machiya, phố đất, biển hiệu và nội thất thời Edo",
    },
]


def _text(item: dict[str, Any], field: str, label: str) -> str:
    value = item.get(field, "")
    if not isinstance(value, str):
        raise ValueError(f"{label} has an invalid {field}")
    return value.strip()


def create_scenes_from_blocks(blocks: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "scene_id": f"VS{index:03d}",
            "source_blocks": [block["block_id"]],
            "period": "",
            "year": "",
            "location": "",
            "characters": [],
            "costume_profile": "",
            "hair_profile": "",
            "action": "",
            "emotion": "",
            "shot_type": "",
            "scene_note": "",
            "image_prompt": "",
            "image_assets": [],
            "selected_image": None,
            "duration_mode": "auto",
            "duration_weight": 1.0,
            "duration_override": None,
            "timeline_start": None,
            "timeline_end": None,
            "motion": "hold",
            "transition": "cut",
        }
        for index, block in enumerate(blocks, start=1)
    ]


def validate_scenes(value: Any, valid_block_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("scenes must be a list")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"scene {position} must be an object")
        scene_id = item.get("scene_id")
        if not isinstance(scene_id, str) or not SCENE_ID.fullmatch(scene_id):
            raise ValueError(f"scene {position} has an invalid scene_id")
        if scene_id in seen_ids:
            raise ValueError(f"duplicate scene_id: {scene_id}")

        sources = item.get("source_blocks")
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"{scene_id} requires at least one source block")
        if any(not isinstance(source, str) for source in sources):
            raise ValueError(f"{scene_id} has invalid source_blocks")
        unique_sources = list(dict.fromkeys(sources))
        missing = [source for source in unique_sources if source not in valid_block_ids]
        if missing:
            raise ValueError(f"{scene_id} references missing blocks: {', '.join(missing)}")

        characters = item.get("characters", [])
        if not isinstance(characters, list) or any(
            not isinstance(character, str) or not CHARACTER_ID.fullmatch(character)
            for character in characters
        ):
            raise ValueError(f"{scene_id} has invalid characters")

        selected_image = item.get("selected_image")
        image_assets = item.get("image_assets", [])
        if not isinstance(image_assets, list) or any(not isinstance(path, str) for path in image_assets):
            raise ValueError(f"{scene_id} has invalid image_assets")

        normalized: dict[str, Any] = {
            "scene_id": scene_id,
            "source_blocks": unique_sources,
        }
        normalized.update({field: _text(item, field, scene_id) for field in SCENE_TEXT_FIELDS})
        normalized["characters"] = list(dict.fromkeys(characters))
        normalized["image_assets"] = list(
            dict.fromkeys(
                normalize_project_asset_path(path, f"{scene_id}.image_assets", "images")
                for path in image_assets
                if path.strip()
            )
        )
        normalized["selected_image"] = normalize_project_asset_path(
            selected_image, f"{scene_id}.selected_image", "images"
        )
        if normalized["selected_image"] and normalized["selected_image"] not in normalized["image_assets"]:
            normalized["image_assets"].append(normalized["selected_image"])

        duration_mode = item.get("duration_mode", "auto")
        if duration_mode not in {"auto", "manual"}:
            raise ValueError(f"{scene_id} has invalid duration_mode")
        duration_weight = item.get("duration_weight", 1.0)
        if (
            isinstance(duration_weight, bool)
            or not isinstance(duration_weight, (int, float))
            or duration_weight <= 0
        ):
            raise ValueError(f"{scene_id} has invalid duration_weight")
        duration_override = item.get("duration_override")
        if duration_override is not None and (
            isinstance(duration_override, bool)
            or not isinstance(duration_override, (int, float))
            or duration_override <= 0
        ):
            raise ValueError(f"{scene_id} has invalid duration_override")
        timeline_start = item.get("timeline_start")
        timeline_end = item.get("timeline_end")
        for field, number in (("timeline_start", timeline_start), ("timeline_end", timeline_end)):
            if number is not None and (
                isinstance(number, bool) or not isinstance(number, (int, float)) or number < 0
            ):
                raise ValueError(f"{scene_id} has invalid {field}")
        motion = item.get("motion", "hold")
        if motion not in MOTIONS:
            raise ValueError(f"{scene_id} has invalid motion")
        transition = item.get("transition", "cut")
        if transition not in TRANSITIONS:
            raise ValueError(f"{scene_id} has invalid transition")
        normalized.update(
            {
                "duration_mode": duration_mode,
                "duration_weight": float(duration_weight),
                "duration_override": (
                    round(float(duration_override), 3) if duration_override is not None else None
                ),
                "timeline_start": (
                    round(float(timeline_start), 3) if timeline_start is not None else None
                ),
                "timeline_end": (
                    round(float(timeline_end), 3) if timeline_end is not None else None
                ),
                "motion": motion,
                "transition": transition,
            }
        )
        seen_ids.add(scene_id)
        result.append(normalized)
    return result


def merge_scenes(scenes: list[dict[str, Any]], scene_ids: list[str]) -> list[dict[str, Any]]:
    """Merge two or more consecutive scenes, retaining the first scene ID."""
    if len(scene_ids) < 2 or len(set(scene_ids)) != len(scene_ids):
        raise ValueError("select at least two different scenes to merge")
    indices = [index for index, scene in enumerate(scenes) if scene["scene_id"] in scene_ids]
    if len(indices) != len(scene_ids):
        raise ValueError("one or more scenes do not exist")
    if indices != list(range(indices[0], indices[-1] + 1)):
        raise ValueError("only consecutive scenes can be merged")

    merged = deepcopy(scenes[indices[0]])
    merged["source_blocks"] = list(
        dict.fromkeys(
            source
            for index in indices
            for source in scenes[index]["source_blocks"]
        )
    )
    merged["selected_image"] = None
    selected = set(scene_ids)
    return [
        merged if index == indices[0] else deepcopy(scene)
        for index, scene in enumerate(scenes)
        if index == indices[0] or scene["scene_id"] not in selected
    ]


def _next_scene_id(scenes: list[dict[str, Any]]) -> str:
    maximum = max((int(scene["scene_id"][2:]) for scene in scenes), default=0)
    return f"VS{maximum + 1:03d}"


def split_scene(
    scenes: list[dict[str, Any]], scene_id: str, after_source_index: int | None = None
) -> list[dict[str, Any]]:
    """Split a scene, reusing a sole block or dividing multiple source blocks."""
    index = next((i for i, scene in enumerate(scenes) if scene["scene_id"] == scene_id), None)
    if index is None:
        raise ValueError(f"scene does not exist: {scene_id}")
    original = deepcopy(scenes[index])
    sources = original["source_blocks"]
    if len(sources) == 1:
        left_sources = right_sources = sources.copy()
    else:
        split_at = after_source_index if after_source_index is not None else len(sources) // 2
        if not isinstance(split_at, int) or split_at < 1 or split_at >= len(sources):
            raise ValueError("split position must leave blocks on both scenes")
        left_sources, right_sources = sources[:split_at], sources[split_at:]

    left = deepcopy(original)
    right = deepcopy(original)
    left["source_blocks"] = left_sources
    right["scene_id"] = _next_scene_id(scenes)
    right["source_blocks"] = right_sources
    left["selected_image"] = None
    right["selected_image"] = None
    return [*deepcopy(scenes[:index]), left, right, *deepcopy(scenes[index + 1 :])]


def validate_characters(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("characters must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"character {position} must be an object")
        character_id = item.get("character_id")
        if not isinstance(character_id, str) or not CHARACTER_ID.fullmatch(character_id):
            raise ValueError(f"character {position} has an invalid character_id")
        if character_id in seen:
            raise ValueError(f"duplicate character_id: {character_id}")
        references = item.get("reference_images", [])
        if not isinstance(references, list) or any(not isinstance(ref, str) for ref in references):
            raise ValueError(f"{character_id} has invalid reference_images")
        face_reference = normalize_relative_path(
            item.get("face_reference"), f"{character_id}.face_reference"
        )
        expression_references = item.get("expression_references", [])
        if not isinstance(expression_references, list) or any(
            not isinstance(ref, str) for ref in expression_references
        ):
            raise ValueError(f"{character_id} has invalid expression_references")
        result.append(
            {
                "character_id": character_id,
                "name": _text(item, "name", character_id),
                "role": _text(item, "role", character_id),
                "face_profile": _text(item, "face_profile", character_id),
                "body_profile": _text(item, "body_profile", character_id),
                "reference_images": [
                    normalize_relative_path(ref, f"{character_id}.reference_images")
                    for ref in references
                    if ref.strip()
                ],
                "face_reference": face_reference,
                "expression_references": [
                    normalize_relative_path(ref, f"{character_id}.expression_references")
                    for ref in expression_references
                    if ref.strip()
                ],
            }
        )
        seen.add(character_id)
    return result


def validate_period_kits(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("period_kits must be a list")
    fields = (
        "name",
        "year_range",
        "costume_profile",
        "hair_profile",
        "accessories",
        "footwear",
        "environment_profile",
    )
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for position, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"period kit {position} must be an object")
        period_id = item.get("period_id")
        if not isinstance(period_id, str) or not PERIOD_ID.fullmatch(period_id):
            raise ValueError(f"period kit {position} has an invalid period_id")
        if period_id in seen:
            raise ValueError(f"duplicate period_id: {period_id}")
        result.append(
            {"period_id": period_id, **{field: _text(item, field, period_id) for field in fields}}
        )
        seen.add(period_id)
    return result

