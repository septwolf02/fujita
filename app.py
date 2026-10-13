"""Dependency-free local VS application."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from image_engine import ImageGenerationEngine, default_registry
from image_providers import ProviderError
from module1 import split_script
from module2 import create_scenes_from_blocks, merge_scenes, split_scene
from portable_paths import normalize_relative_path
from storage import WorkspaceStorage, default_workspace_root
from tts_engine import TTSEngine, default_tts_registry
from tts_providers import TTSProviderError
from timeline_engine import TimelineEngine
from video_renderer import FFmpegRenderer, RenderError


APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
LEGACY_DATA_DIR = APP_ROOT / "data"
MAX_BODY_BYTES = 2 * 1024 * 1024
_storage: WorkspaceStorage | None = None


def configure_storage(workspace_root: str | Path | None = None) -> WorkspaceStorage:
    global _storage
    root = Path(workspace_root) if workspace_root is not None else default_workspace_root(APP_ROOT)
    _storage = WorkspaceStorage(root, legacy_data_dir=LEGACY_DATA_DIR)
    _storage.initialize()
    return _storage


def get_storage() -> WorkspaceStorage:
    return _storage or configure_storage()


class AppHandler(BaseHTTPRequestHandler):
    server_version = "VS/0.6"

    @property
    def storage(self) -> WorkspaceStorage:
        return self.server.storage  # type: ignore[attr-defined]

    @property
    def image_engine(self) -> ImageGenerationEngine:
        return self.server.image_engine  # type: ignore[attr-defined]

    @property
    def tts_engine(self) -> TTSEngine:
        return self.server.tts_engine  # type: ignore[attr-defined]

    @property
    def timeline_engine(self) -> TimelineEngine:
        return self.server.timeline_engine  # type: ignore[attr-defined]

    @property
    def video_renderer(self) -> FFmpegRenderer:
        return self.server.video_renderer  # type: ignore[attr-defined]

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/workspace":
                self.send_json(
                    {
                        "workspace_root": str(self.storage.root),
                        "active_project": self.storage.active_project(),
                        "projects": self.storage.list_projects(),
                    }
                )
                return
            if path == "/api/image-providers":
                settings = self.image_engine.provider_settings()
                self.send_json(
                    {
                        "providers": self.image_engine.providers.names(),
                        "active_provider": settings["provider"],
                        "aspect_ratio": settings["aspect_ratio"],
                        "quality": settings["quality"],
                    }
                )
                return
            if path == "/api/tts-providers":
                settings = self.tts_engine.provider_settings()
                self.send_json(
                    {
                        "providers": self.tts_engine.providers.names(),
                        "active_provider": settings["provider"],
                        "voice_id": settings["voice_id"],
                        "speed": settings["speed"],
                        "style": settings["style"],
                        "engine_url": settings["engine_url"],
                    }
                )
                return
            if path == "/api/blocks":
                self.send_json({"blocks": self.storage.load_blocks()})
                return
            if path == "/api/scenes":
                self.send_json({"scenes": self.storage.load_scenes()})
                return
            if path == "/api/characters":
                self.send_json({"characters": self.storage.load_characters()})
                return
            if path == "/api/period-kits":
                self.send_json({"period_kits": self.storage.load_period_kits()})
                return
            if path == "/api/timeline":
                self.send_json({"timeline": self.timeline_engine.regenerate()})
                return
            if path == "/api/renderer":
                self.send_json(self.video_renderer.status())
                return
            if path == "/health":
                self.send_json({"status": "ok", "modules": [1, 2, 3, 4, 5]})
                return
            if path.startswith("/project-assets/"):
                self.serve_project_asset(unquote(path.removeprefix("/project-assets/")))
                return
            self.serve_static(path)
        except (RuntimeError, ValueError) as error:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/projects":
                title = payload.get("title")
                project_id = payload.get("project_id")
                if not isinstance(title, str):
                    raise ValueError("project title must be text")
                if project_id is not None and not isinstance(project_id, str):
                    raise ValueError("project_id must be text")
                project = self.storage.create_project(title, project_id)
                self.send_json({"project": project, "active_project": project, "created": True})
                return
            if path == "/api/projects/switch":
                project_id = payload.get("project_id")
                if not isinstance(project_id, str):
                    raise ValueError("project_id must be text")
                project = self.storage.switch_project(project_id)
                self.send_json({"active_project": project, "switched": True})
                return
            if path == "/api/split":
                script = payload.get("script")
                mode = payload.get("split_mode", "sentences")
                if not isinstance(script, str):
                    raise ValueError("script must be text")
                self.send_json({"blocks": split_script(script, mode)})
                return
            if path == "/api/blocks":
                blocks = self.storage.save_blocks(payload.get("blocks"))
                self.timeline_engine.regenerate()
                self.send_json({"blocks": blocks, "saved": True})
                return
            if path == "/api/scenes":
                scenes = self.storage.save_scenes(payload.get("scenes"))
                self.timeline_engine.regenerate()
                scenes = self.storage.load_scenes()
                self.send_json({"scenes": scenes, "saved": True})
                return
            if path == "/api/scenes/from-blocks":
                scenes = self.storage.save_scenes(
                    create_scenes_from_blocks(self.storage.load_blocks())
                )
                self.timeline_engine.regenerate()
                scenes = self.storage.load_scenes()
                self.send_json({"scenes": scenes, "saved": True})
                return
            if path == "/api/scenes/merge":
                scene_ids = payload.get("scene_ids")
                if not isinstance(scene_ids, list) or any(
                    not isinstance(scene_id, str) for scene_id in scene_ids
                ):
                    raise ValueError("scene_ids must be a list of IDs")
                scenes = self.storage.save_scenes(
                    merge_scenes(self.storage.load_scenes(), scene_ids)
                )
                self.timeline_engine.regenerate()
                scenes = self.storage.load_scenes()
                self.send_json({"scenes": scenes, "saved": True})
                return
            if path == "/api/scenes/split":
                scene_id = payload.get("scene_id")
                if not isinstance(scene_id, str):
                    raise ValueError("scene_id must be text")
                scenes = self.storage.save_scenes(
                    split_scene(
                        self.storage.load_scenes(),
                        scene_id,
                        payload.get("after_source_index"),
                    )
                )
                self.timeline_engine.regenerate()
                scenes = self.storage.load_scenes()
                self.send_json({"scenes": scenes, "saved": True})
                return
            if path == "/api/characters":
                characters = self.storage.save_characters(payload.get("characters"))
                self.send_json({"characters": characters, "saved": True})
                return
            if path == "/api/period-kits":
                kits = self.storage.save_period_kits(payload.get("period_kits"))
                self.send_json({"period_kits": kits, "saved": True})
                return
            if path == "/api/images/generate":
                scene_id = payload.get("scene_id")
                operation = payload.get("operation", "generate")
                provider = payload.get("provider")
                if not isinstance(scene_id, str):
                    raise ValueError("scene_id must be text")
                if operation not in {"generate", "variations", "regenerate"}:
                    raise ValueError("invalid image generation operation")
                if provider is not None and not isinstance(provider, str):
                    raise ValueError("provider must be text")
                count = payload.get("count", 4) if operation == "variations" else 1
                result = self.image_engine.generate(scene_id, count, provider)
                self.send_json({**result, "operation": operation})
                return
            if path == "/api/images/select":
                scene_id = payload.get("scene_id")
                relative_path = payload.get("path")
                if not isinstance(scene_id, str) or not isinstance(relative_path, str):
                    raise ValueError("scene_id and path must be text")
                result = self.image_engine.select_image(scene_id, relative_path)
                self.timeline_engine.regenerate()
                result["scenes"] = self.storage.load_scenes()
                self.send_json(result)
                return
            if path == "/api/images/delete":
                scene_id = payload.get("scene_id")
                relative_path = payload.get("path")
                if not isinstance(scene_id, str) or not isinstance(relative_path, str):
                    raise ValueError("scene_id and path must be text")
                result = self.image_engine.delete_image(scene_id, relative_path)
                self.timeline_engine.regenerate()
                result["scenes"] = self.storage.load_scenes()
                self.send_json(result)
                return
            if path == "/api/audio/generate":
                block_id = payload.get("block_id")
                operation = payload.get("operation", "generate")
                provider = payload.get("provider")
                if not isinstance(block_id, str):
                    raise ValueError("block_id must be text")
                if operation not in {"generate", "regenerate"}:
                    raise ValueError("invalid audio generation operation")
                if provider is not None and not isinstance(provider, str):
                    raise ValueError("provider must be text")
                result = self.tts_engine.generate(block_id, provider)
                self.timeline_engine.regenerate()
                self.send_json({**result, "operation": operation})
                return
            if path == "/api/audio/generate-all":
                provider = payload.get("provider")
                if provider is not None and not isinstance(provider, str):
                    raise ValueError("provider must be text")
                result = self.tts_engine.generate_all(provider)
                self.timeline_engine.regenerate()
                self.send_json(result)
                return
            if path == "/api/audio/select":
                block_id = payload.get("block_id")
                relative_path = payload.get("path")
                if not isinstance(block_id, str) or not isinstance(relative_path, str):
                    raise ValueError("block_id and path must be text")
                result = self.tts_engine.select_audio(block_id, relative_path)
                self.timeline_engine.regenerate()
                self.send_json(result)
                return
            if path == "/api/timeline/regenerate":
                self.send_json({"timeline": self.timeline_engine.regenerate()})
                return
            if path == "/api/render":
                dry_run = payload.get("dry_run", False)
                if not isinstance(dry_run, bool):
                    raise ValueError("dry_run must be boolean")
                self.send_json(self.video_renderer.render(dry_run=dry_run))
                return
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
        except (ValueError, json.JSONDecodeError) as error:
            self.send_error_json(HTTPStatus.BAD_REQUEST, str(error))
        except (ProviderError, TTSProviderError) as error:
            self.send_error_json(HTTPStatus.BAD_GATEWAY, str(error))
        except RenderError as error:
            self.send_error_json(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
        except (OSError, RuntimeError) as error:
            self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def read_json(self) -> dict:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as error:
            raise ValueError("invalid Content-Length") from error
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body must be between 1 byte and 2 MB")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in candidate.parents or not candidate.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        body = candidate.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_project_asset(self, relative: str) -> None:
        try:
            normalized = normalize_relative_path(relative, "project asset")
        except ValueError:
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        if not normalized or not normalized.startswith(("images/", "audio/", "output/")):
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        project_root = self.storage.active_project_root().resolve()
        candidate = (project_root / normalized).resolve()
        if project_root not in candidate.parents or not candidate.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Not found")
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_json({"error": message}, status)

    def log_message(self, message: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {message % args}")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    storage: WorkspaceStorage | None = None,
    image_engine: ImageGenerationEngine | None = None,
    tts_engine: TTSEngine | None = None,
    timeline_engine: TimelineEngine | None = None,
    video_renderer: FFmpegRenderer | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), AppHandler)
    server.storage = storage or get_storage()  # type: ignore[attr-defined]
    server.image_engine = image_engine or ImageGenerationEngine(  # type: ignore[attr-defined]
        server.storage, default_registry()  # type: ignore[attr-defined]
    )
    server.tts_engine = tts_engine or TTSEngine(  # type: ignore[attr-defined]
        server.storage, default_tts_registry(server.storage)  # type: ignore[attr-defined]
    )
    server.timeline_engine = timeline_engine or TimelineEngine(server.storage)  # type: ignore[attr-defined]
    server.video_renderer = video_renderer or FFmpegRenderer(  # type: ignore[attr-defined]
        server.storage, server.timeline_engine  # type: ignore[attr-defined]
    )
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VS locally")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--workspace-root", help="local workspace data directory")
    parser.add_argument("--open", action="store_true", help="open the app in a browser")
    args = parser.parse_args()
    storage = configure_storage(args.workspace_root)
    server = create_server(args.host, args.port, storage)
    url = f"http://{args.host}:{server.server_port}"
    print(f"VS is running at {url}")
    print(f"Workspace root: {storage.root}")
    print("Press Ctrl+C to stop.")
    if args.open:
        threading.Timer(0.4, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping the app.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
