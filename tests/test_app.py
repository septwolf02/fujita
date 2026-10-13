import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

import app
from storage import WorkspaceStorage


class AppApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_directory = tempfile.TemporaryDirectory()
        cls.storage = WorkspaceStorage(Path(cls.temp_directory.name) / "workspace")
        cls.storage.initialize()
        cls.server = app.create_server(port=0, storage=cls.storage)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.temp_directory.cleanup()

    def request_json(self, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        with urlopen(request) as response:
            return response.status, json.load(response)

    def test_health_and_save_load_workflow(self):
        status, health = self.request_json("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health, {"status": "ok", "modules": [1, 2, 3, 4, 5]})

        _, split = self.request_json("/api/split", {"script": "江戸です。夜です！"})
        self.assertEqual(len(split["blocks"]), 2)
        split["blocks"][0]["vn_check"] = "Đây là Edo."

        _, saved = self.request_json("/api/blocks", {"blocks": split["blocks"]})
        self.assertTrue(saved["saved"])
        _, loaded = self.request_json("/api/blocks")
        self.assertEqual(loaded["blocks"], split["blocks"])

        _, providers = self.request_json("/api/tts-providers")
        self.assertEqual(providers["providers"], ["fake", "voicevox"])

    def test_image_generation_and_selection_api(self):
        blocks = [{"block_id": "S001", "jp_text": "画像です。", "vn_check": "Đây là hình ảnh."}]
        self.request_json("/api/blocks", {"blocks": blocks})
        self.request_json("/api/scenes/from-blocks", {})
        _, generated = self.request_json(
            "/api/images/generate",
            {"scene_id": "VS001", "operation": "variations", "count": 2},
        )
        self.assertEqual(generated["generated"], ["images/VS001_01.png", "images/VS001_02.png"])
        with urlopen(self.base_url + "/project-assets/images/VS001_01.png") as response:
            self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")
        _, selected = self.request_json(
            "/api/images/select",
            {"scene_id": "VS001", "path": "images/VS001_02.png"},
        )
        self.assertEqual(selected["scene"]["selected_image"], "images/VS001_02.png")

    def test_audio_generation_preview_and_selection_api(self):
        blocks = [{"block_id": "S001", "jp_text": "音声です。", "vn_check": "Đây là âm thanh."}]
        self.request_json("/api/blocks", {"blocks": blocks})
        _, first = self.request_json("/api/audio/generate", {"block_id": "S001", "operation": "generate"})
        _, second = self.request_json("/api/audio/generate", {"block_id": "S001", "operation": "regenerate"})
        self.assertEqual(first["generated"], "audio/S001_01.wav")
        self.assertEqual(second["generated"], "audio/S001_02.wav")
        with urlopen(self.base_url + "/project-assets/audio/S001_01.wav") as response:
            self.assertEqual(response.headers.get_content_type(), "audio/x-wav")
            self.assertEqual(response.read(4), b"RIFF")
        _, selected = self.request_json(
            "/api/audio/select", {"block_id": "S001", "path": "audio/S001_01.wav"}
        )
        self.assertEqual(selected["block"]["audio_file"], "audio/S001_01.wav")

    def test_timeline_and_ffmpeg_dry_run_api(self):
        blocks = [{"block_id": "S001", "jp_text": "映像です。", "vn_check": "Đây là video."}]
        self.request_json("/api/blocks", {"blocks": blocks})
        self.request_json("/api/audio/generate", {"block_id": "S001"})
        self.request_json("/api/scenes/from-blocks", {})
        _, generated = self.request_json("/api/images/generate", {"scene_id": "VS001"})
        self.request_json(
            "/api/images/select", {"scene_id": "VS001", "path": generated["generated"][0]}
        )

        _, timeline = self.request_json("/api/timeline")
        self.assertEqual(timeline["timeline"]["warnings"], [])
        self.assertGreater(timeline["timeline"]["total_duration"], 0)
        _, dry_run = self.request_json("/api/render", {"dry_run": True})
        self.assertEqual(dry_run["status"], "dry_run")
        self.assertEqual(dry_run["output"], "output/draft_001.mp4")
        self.assertIn("-filter_complex", dry_run["command"])

    def test_project_create_and_switch_api(self):
        _, initial = self.request_json("/api/workspace")
        original_id = initial["active_project"]["project_id"]
        _, created = self.request_json(
            "/api/projects", {"title": "Heian Night", "project_id": "heian-night"}
        )
        self.assertEqual(created["active_project"]["project_id"], "heian-night")
        _, empty_blocks = self.request_json("/api/blocks")
        self.assertEqual(empty_blocks["blocks"], [])
        _, switched = self.request_json("/api/projects/switch", {"project_id": original_id})
        self.assertEqual(switched["active_project"]["project_id"], original_id)

    def test_visual_scene_save_load_merge_and_split_workflow(self):
        blocks = [
            {"block_id": "S001", "jp_text": "江戸です。", "vn_check": "Đây là Edo."},
            {"block_id": "S002", "jp_text": "夜です。", "vn_check": "Trời tối."},
        ]
        self.request_json("/api/blocks", {"blocks": blocks})
        _, initialized = self.request_json("/api/scenes/from-blocks", {})
        scenes = initialized["scenes"]
        scenes[0].update({"period": "Edo", "image_prompt": "simple Edo street illustration"})

        _, saved = self.request_json("/api/scenes", {"scenes": scenes})
        self.assertTrue(saved["saved"])
        _, loaded = self.request_json("/api/scenes")
        self.assertEqual(loaded["scenes"], scenes)

        _, merged = self.request_json("/api/scenes/merge", {"scene_ids": ["VS001", "VS002"]})
        self.assertEqual(merged["scenes"][0]["source_blocks"], ["S001", "S002"])
        _, split = self.request_json("/api/scenes/split", {"scene_id": "VS001"})
        self.assertEqual([scene["source_blocks"] for scene in split["scenes"]], [["S001"], ["S002"]])

    def test_character_bible_and_period_kit_defaults_load(self):
        _, characters = self.request_json("/api/characters")
        _, periods = self.request_json("/api/period-kits")
        self.assertEqual([item["character_id"] for item in characters["characters"]], ["CHAR_A", "CHAR_B"])
        self.assertIn("EDO", [item["period_id"] for item in periods["period_kits"]])

        character_data = characters["characters"]
        character_data[0]["face_profile"] = "Stable face reference"
        self.request_json("/api/characters", {"characters": character_data})
        _, reloaded_characters = self.request_json("/api/characters")
        self.assertEqual(reloaded_characters["characters"][0]["face_profile"], "Stable face reference")

        period_data = periods["period_kits"]
        edo = next(item for item in period_data if item["period_id"] == "EDO")
        edo["footwear"] = "geta"
        self.request_json("/api/period-kits", {"period_kits": period_data})
        _, reloaded_periods = self.request_json("/api/period-kits")
        reloaded_edo = next(item for item in reloaded_periods["period_kits"] if item["period_id"] == "EDO")
        self.assertEqual(reloaded_edo["footwear"], "geta")


if __name__ == "__main__":
    unittest.main()

