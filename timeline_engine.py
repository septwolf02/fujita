"""Audio-clock timeline construction for Module 5."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from storage import WorkspaceStorage, utc_now


MILLISECOND = Decimal("0.001")


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _seconds(value: Decimal) -> float:
    return float(value.quantize(MILLISECOND, rounding=ROUND_HALF_UP))


class TimelineEngine:
    """Build a scene timeline while treating selected audio as the master clock."""

    def __init__(self, storage: WorkspaceStorage):
        self.storage = storage

    @staticmethod
    def _warning(code: str, message: str, **context: str) -> dict[str, str]:
        return {"code": code, "message": message, **context}

    def regenerate(self) -> dict[str, Any]:
        with self.storage.project_transaction() as project_root:
            blocks = self.storage.load_blocks()
            scenes = self.storage.load_scenes()
            block_by_id = {block["block_id"]: block for block in blocks}
            block_order = {block["block_id"]: index for index, block in enumerate(blocks)}
            warnings: list[dict[str, str]] = []
            durations: dict[str, Decimal] = {}

            for block in blocks:
                block_id = block["block_id"]
                duration = block.get("duration")
                audio_file = block.get("audio_file")
                if duration is None or duration <= 0:
                    durations[block_id] = Decimal("0")
                    warnings.append(
                        self._warning(
                            "missing_audio_duration",
                            f"{block_id} chưa có audio duration hợp lệ.",
                            block_id=block_id,
                        )
                    )
                else:
                    durations[block_id] = _decimal(duration)
                if not audio_file:
                    warnings.append(
                        self._warning(
                            "missing_audio",
                            f"{block_id} chưa chọn audio_file.",
                            block_id=block_id,
                        )
                    )
                elif not (project_root / audio_file).is_file():
                    warnings.append(
                        self._warning(
                            "missing_audio_file",
                            f"Không tìm thấy file {audio_file} của {block_id}.",
                            block_id=block_id,
                        )
                    )

            coverage: dict[str, list[int]] = {block_id: [] for block_id in block_by_id}
            previous_first = -1
            for scene_index, scene in enumerate(scenes):
                scene_id = scene["scene_id"]
                indices = [block_order[source] for source in scene["source_blocks"]]
                if indices != list(range(min(indices), max(indices) + 1)):
                    warnings.append(
                        self._warning(
                            "nonconsecutive_blocks",
                            f"{scene_id} phải cover các Content Block liên tiếp.",
                            scene_id=scene_id,
                        )
                    )
                if min(indices) < previous_first:
                    warnings.append(
                        self._warning(
                            "scene_order",
                            f"{scene_id} không theo thứ tự audio của Content Block.",
                            scene_id=scene_id,
                        )
                    )
                previous_first = min(indices)
                for source in scene["source_blocks"]:
                    coverage[source].append(scene_index)
                selected_image = scene.get("selected_image")
                if not selected_image:
                    warnings.append(
                        self._warning(
                            "missing_image",
                            f"{scene_id} chưa có selected_image.",
                            scene_id=scene_id,
                        )
                    )
                elif not (project_root / selected_image).is_file():
                    warnings.append(
                        self._warning(
                            "missing_image_file",
                            f"Không tìm thấy file {selected_image} của {scene_id}.",
                            scene_id=scene_id,
                        )
                    )

            for block_id, scene_indices in coverage.items():
                if not scene_indices:
                    warnings.append(
                        self._warning(
                            "uncovered_block",
                            f"{block_id} chưa được Visual Scene nào cover.",
                            block_id=block_id,
                        )
                    )

            allocations: list[dict[str, Decimal]] = [dict() for _ in scenes]
            manual_allocations: list[dict[str, Decimal]] = [dict() for _ in scenes]
            for index, scene in enumerate(scenes):
                if scene["duration_mode"] != "manual":
                    continue
                override = scene.get("duration_override")
                if override is None:
                    warnings.append(
                        self._warning(
                            "missing_duration_override",
                            f"{scene['scene_id']} ở manual mode nhưng chưa có duration_override.",
                            scene_id=scene["scene_id"],
                        )
                    )
                    continue
                source_total = sum((durations[source] for source in scene["source_blocks"]), Decimal("0"))
                for source in scene["source_blocks"]:
                    if source_total > 0:
                        share = _decimal(override) * durations[source] / source_total
                    else:
                        share = _decimal(override) / len(scene["source_blocks"])
                    manual_allocations[index][source] = share

            for block in blocks:
                block_id = block["block_id"]
                block_duration = durations[block_id]
                scene_indices = coverage[block_id]
                if not scene_indices:
                    continue
                manual_indices = [
                    index for index in scene_indices if scenes[index]["duration_mode"] == "manual"
                ]
                auto_indices = [
                    index for index in scene_indices if scenes[index]["duration_mode"] == "auto"
                ]
                fixed = sum(
                    (manual_allocations[index].get(block_id, Decimal("0")) for index in manual_indices),
                    Decimal("0"),
                )
                for index in manual_indices:
                    allocations[index][block_id] = manual_allocations[index].get(
                        block_id, Decimal("0")
                    )
                remaining = block_duration - fixed
                if remaining < 0:
                    warnings.append(
                        self._warning(
                            "duration_overflow",
                            f"Manual duration của các scene vượt duration audio {block_id}.",
                            block_id=block_id,
                        )
                    )
                    remaining = Decimal("0")
                if auto_indices:
                    weight_total = sum(
                        (_decimal(scenes[index]["duration_weight"]) for index in auto_indices),
                        Decimal("0"),
                    )
                    for index in auto_indices:
                        allocations[index][block_id] = (
                            remaining * _decimal(scenes[index]["duration_weight"]) / weight_total
                        )
                elif remaining != 0:
                    warnings.append(
                        self._warning(
                            "duration_gap",
                            f"Manual duration không lấp đầy audio duration của {block_id}.",
                            block_id=block_id,
                        )
                    )

            cursor = Decimal("0")
            items: list[dict[str, Any]] = []
            for index, scene in enumerate(scenes):
                duration = sum(allocations[index].values(), Decimal("0"))
                start = cursor
                end = start + duration
                start_seconds = _seconds(start)
                end_seconds = _seconds(end)
                item = {
                    "scene_id": scene["scene_id"],
                    "source_blocks": scene["source_blocks"],
                    "start": start_seconds,
                    "end": end_seconds,
                    "duration": round(end_seconds - start_seconds, 3),
                    "selected_image": scene.get("selected_image"),
                    "motion": scene["motion"],
                    "transition": scene["transition"],
                    "audio_blocks": scene["source_blocks"],
                }
                items.append(item)
                scene["timeline_start"] = item["start"]
                scene["timeline_end"] = item["end"]
                cursor = end

            total_audio = sum(durations.values(), Decimal("0"))
            if abs(cursor - total_audio) > MILLISECOND:
                warnings.append(
                    self._warning(
                        "timeline_mismatch",
                        "Tổng scene duration không bằng tổng audio duration.",
                    )
                )
            self.storage.save_scenes(scenes)
            timeline = {
                "project_id": self.storage.active_project()["project_id"],
                "generated_at": utc_now(),
                "total_duration": _seconds(total_audio),
                "items": items,
                "warnings": warnings,
            }
            self.storage.save_timeline(timeline)
            return timeline
