"""FFmpeg command builder and safe project-local renderer for Module 5."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from storage import WorkspaceStorage, utc_now
from timeline_engine import TimelineEngine


class RenderError(RuntimeError):
    """Video rendering could not complete safely."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


class FFmpegRenderer:
    def __init__(
        self,
        storage: WorkspaceStorage,
        timeline_engine: TimelineEngine | None = None,
        runner: Runner | None = None,
    ):
        self.storage = storage
        self.timeline_engine = timeline_engine or TimelineEngine(storage)
        self.runner = runner or subprocess.run

    def settings(self) -> dict[str, Any]:
        config = self.storage.config().get("video_render", {})
        settings = {
            "renderer": str(config.get("renderer", "ffmpeg")),
            "ffmpeg_path": str(config.get("ffmpeg_path", "ffmpeg")),
            "width": int(config.get("width", 1920)),
            "height": int(config.get("height", 1080)),
            "fps": int(config.get("fps", 30)),
            "video_codec": str(config.get("video_codec", "libx264")),
            "audio_codec": str(config.get("audio_codec", "aac")),
        }
        if settings["width"] <= 0 or settings["height"] <= 0 or settings["fps"] <= 0:
            raise ValueError("video width, height, and fps must be positive")
        return settings

    def ffmpeg_available(self) -> bool:
        executable = self.settings()["ffmpeg_path"]
        return bool(shutil.which(executable) or Path(executable).is_file())

    def status(self) -> dict[str, Any]:
        settings = self.settings()
        return {**settings, "available": self.ffmpeg_available()}

    def _next_output(self, project_root: Path) -> tuple[str, Path, Path]:
        output_root = project_root / "output"
        versions = []
        for path in output_root.glob("draft_*.mp4"):
            suffix = path.stem.removeprefix("draft_")
            if suffix.isdigit():
                versions.append(int(suffix))
        version = max(versions, default=0) + 1
        relative = f"output/draft_{version:03d}.mp4"
        destination = project_root / relative
        temporary = output_root / f"draft_{version:03d}.rendering.mp4"
        return relative, destination, temporary

    @staticmethod
    def _motion_filter(item: dict[str, Any], width: int, height: int, fps: int) -> str:
        duration = max(float(item["duration"]), 0.001)
        base = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"
        motion = item["motion"]
        if motion == "zoom_in":
            base += f",zoompan=z='min(zoom+0.0007,1.12)':d=1:s={width}x{height}:fps={fps}"
        elif motion == "zoom_out":
            base += (
                f",zoompan=z='if(eq(on,1),1.12,max(zoom-0.0007,1.0))'"
                f":d=1:s={width}x{height}:fps={fps}"
            )
        elif motion in {"pan_left", "pan_right"}:
            large_width = round(width * 1.1)
            direction = "(iw-ow)*(1-t/{:.6f})" if motion == "pan_left" else "(iw-ow)*t/{:.6f}"
            base = (
                f"scale={large_width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height}:x='{direction.format(duration)}':y=0"
            )
        base += f",fps={fps},trim=duration={duration:.6f},setpts=PTS-STARTPTS"
        if item.get("transition") == "fade":
            fade_duration = min(0.5, duration / 2)
            fade_out_start = max(0.0, duration - fade_duration)
            base += (
                f",fade=t=in:st=0:d={fade_duration:.6f},"
                f"fade=t=out:st={fade_out_start:.6f}:d={fade_duration:.6f}"
            )
        return base

    def build_command(
        self,
        timeline: dict[str, Any],
        blocks: list[dict[str, Any]],
        project_root: Path,
        output_path: Path,
    ) -> list[str]:
        settings = self.settings()
        items = timeline.get("items", [])
        if not items:
            raise RenderError("Timeline không có scene để render.")
        command = [settings["ffmpeg_path"], "-y", "-hide_banner"]
        for item in items:
            command.extend(
                [
                    "-loop",
                    "1",
                    "-t",
                    f"{float(item['duration']):.6f}",
                    "-i",
                    str(project_root / item["selected_image"]),
                ]
            )
        audio_blocks = [block for block in blocks if block.get("audio_file")]
        for block in audio_blocks:
            command.extend(["-i", str(project_root / block["audio_file"])])

        filters: list[str] = []
        for index, item in enumerate(items):
            filters.append(
                f"[{index}:v]{self._motion_filter(item, settings['width'], settings['height'], settings['fps'])}[v{index}]"
            )
        video_inputs = "".join(f"[v{index}]" for index in range(len(items)))
        filters.append(f"{video_inputs}concat=n={len(items)}:v=1:a=0[vout]")
        audio_start = len(items)
        for index in range(len(audio_blocks)):
            filters.append(
                f"[{audio_start + index}:a]aresample=48000,asetpts=PTS-STARTPTS[a{index}]"
            )
        audio_inputs = "".join(f"[a{index}]" for index in range(len(audio_blocks)))
        if len(audio_blocks) == 1:
            filters.append("[a0]anull[aout]")
        else:
            filters.append(f"{audio_inputs}concat=n={len(audio_blocks)}:v=0:a=1[aout]")

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-map",
                "[aout]",
                "-c:v",
                settings["video_codec"],
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(settings["fps"]),
                "-c:a",
                settings["audio_codec"],
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        return command

    @staticmethod
    def _warning_text(warnings: list[dict[str, Any]]) -> str:
        return "\n".join(f"- [{warning['code']}] {warning['message']}" for warning in warnings)

    @staticmethod
    def _write_log(path: Path, heading: str, details: str) -> None:
        path.write_text(f"{heading}\n{details}\n", encoding="utf-8")

    def render(self, dry_run: bool = False) -> dict[str, Any]:
        with self.storage.project_transaction() as project_root:
            timeline = self.timeline_engine.regenerate()
            relative, destination, temporary = self._next_output(project_root)
            log_path = destination.with_suffix(".log")
            warnings = timeline.get("warnings", [])
            if warnings:
                message = "Timeline chưa sẵn sàng:\n" + self._warning_text(warnings)
                self._write_log(log_path, f"Render validation failed at {utc_now()}", message)
                raise RenderError(message)
            blocks = self.storage.load_blocks()
            command = self.build_command(timeline, blocks, project_root, temporary)
            if dry_run:
                return {
                    "status": "dry_run",
                    "output": relative,
                    "command": command,
                    "log": None,
                }
            if not self.ffmpeg_available():
                raise RenderError(
                    f"Không tìm thấy FFmpeg tại '{self.settings()['ffmpeg_path']}'. Hãy cấu hình video_render.ffmpeg_path."
                )
            try:
                completed = self.runner(
                    command,
                    cwd=project_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if completed.returncode != 0 or not temporary.is_file():
                    details = (completed.stderr or completed.stdout or "FFmpeg did not create output").strip()
                    self._write_log(log_path, f"FFmpeg render failed at {utc_now()}", details)
                    temporary.unlink(missing_ok=True)
                    raise RenderError(f"FFmpeg render thất bại. Xem log: {log_path.name}")
                temporary.replace(destination)
                self._write_log(log_path, f"FFmpeg render completed at {utc_now()}", "Success")
            except RenderError:
                raise
            except Exception as error:
                temporary.unlink(missing_ok=True)
                self._write_log(log_path, f"FFmpeg render failed at {utc_now()}", str(error))
                raise RenderError(f"Không thể chạy FFmpeg: {error}") from error
            return {
                "status": "completed",
                "output": relative,
                "command": command,
                "log": f"output/{log_path.name}",
            }
