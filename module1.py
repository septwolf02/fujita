"""Core domain logic for Module 1.

Japanese is the source of truth. Vietnamese checks are deliberately created as
empty strings so a human can enter a close, meaning-focused translation.
"""

from __future__ import annotations

import re
from typing import Any

from portable_paths import normalize_project_asset_path


SENTENCE_END = re.compile(r"(?<=[。！？!?])\s*")
BLOCK_ID = re.compile(r"^S\d{3,}$")


def split_script(script: str, mode: str = "sentences") -> list[dict[str, Any]]:
    """Split Japanese script into editable content blocks.

    ``paragraphs`` uses blank lines as boundaries. ``sentences`` first finds
    paragraphs and then splits each paragraph after Japanese/ASCII end marks.
    """
    normalized = script.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if mode not in {"sentences", "paragraphs"}:
        raise ValueError("split_mode must be 'sentences' or 'paragraphs'")

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", normalized) if part.strip()]
    pieces: list[str] = []
    for paragraph in paragraphs:
        compact = re.sub(r"[ \t]*\n[ \t]*", "\n", paragraph).strip()
        if mode == "paragraphs":
            pieces.append(compact)
            continue
        pieces.extend(part.strip() for part in SENTENCE_END.split(compact) if part.strip())

    return [
        {
            "block_id": f"S{index:03d}",
            "jp_text": text,
            "vn_check": "",
            "scene_ids": [],
            "audio_assets": [],
            "audio_file": None,
            "duration": None,
        }
        for index, text in enumerate(pieces, start=1)
    ]


def validate_blocks(value: Any) -> list[dict[str, Any]]:
    """Validate and normalize blocks received from the browser."""
    if not isinstance(value, list):
        raise ValueError("blocks must be a list")
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for position, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"block {position} must be an object")
        block_id = item.get("block_id")
        jp_text = item.get("jp_text")
        vn_check = item.get("vn_check")
        scene_ids = item.get("scene_ids", [])
        audio_assets = item.get("audio_assets", [])
        audio_file = item.get("audio_file")
        duration = item.get("duration")
        if not isinstance(block_id, str) or not BLOCK_ID.fullmatch(block_id):
            raise ValueError(f"block {position} has an invalid block_id")
        if block_id in seen_ids:
            raise ValueError(f"duplicate block_id: {block_id}")
        if not isinstance(jp_text, str) or not jp_text.strip():
            raise ValueError(f"{block_id} requires jp_text")
        if not isinstance(vn_check, str):
            raise ValueError(f"{block_id} has an invalid vn_check")
        if not isinstance(scene_ids, list) or any(
            not isinstance(scene_id, str) or not re.fullmatch(r"VS\d{3,}", scene_id)
            for scene_id in scene_ids
        ):
            raise ValueError(f"{block_id} has invalid scene_ids")
        if not isinstance(audio_assets, list) or any(
            not isinstance(asset, str) for asset in audio_assets
        ):
            raise ValueError(f"{block_id} has invalid audio_assets")
        normalized_assets = [
            normalize_project_asset_path(asset, f"{block_id}.audio_assets", "audio")
            for asset in audio_assets
        ]
        normalized_assets = list(dict.fromkeys(asset for asset in normalized_assets if asset))
        normalized_audio = normalize_project_asset_path(
            audio_file, f"{block_id}.audio_file", "audio"
        )
        if normalized_audio and normalized_audio not in normalized_assets:
            normalized_assets.append(normalized_audio)
        if duration is not None and (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or duration < 0
        ):
            raise ValueError(f"{block_id} has invalid duration")
        seen_ids.add(block_id)
        result.append(
            {
                "block_id": block_id,
                "jp_text": jp_text.strip(),
                "vn_check": vn_check.strip(),
                "scene_ids": list(dict.fromkeys(scene_ids)),
                "audio_assets": normalized_assets,
                "audio_file": normalized_audio,
                "duration": round(float(duration), 3) if duration is not None else None,
            }
        )
    return result

