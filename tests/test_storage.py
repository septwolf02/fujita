import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from module2 import create_scenes_from_blocks
from module2 import DEFAULT_CHARACTERS, DEFAULT_PERIOD_KITS
from portable_paths import normalize_relative_path
from storage import WorkspaceStorage, default_workspace_root


def block(block_id: str, text: str) -> dict:
    return {"block_id": block_id, "jp_text": text, "vn_check": ""}


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "Windows User" / "Video Factory"
        self.storage = WorkspaceStorage(self.root)
        self.storage.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_initializes_target_structure_and_project_metadata(self):
        project = self.storage.active_project()
        project_root = self.root / "projects" / project["project_id"]
        self.assertEqual(project["language"], "ja")
        self.assertEqual(project["status"], "draft")
        self.assertTrue(project["created_at"])
        for name in ("images", "audio", "thumbnails", "temp", "output"):
            self.assertTrue((project_root / name).is_dir())
        self.assertTrue((self.root / "library" / "styles").is_dir())
        self.assertEqual(self.storage.config()["tts_generation"]["provider"], "fake")
        self.assertEqual(self.storage.config()["tts"]["provider"], "fake")
        self.assertEqual(
            self.storage.config()["tts"]["voicevox"],
            {
                "engine_url": "http://127.0.0.1:50021",
                "speaker_name": "剣崎雌雄",
                "style_name": "ノーマル",
            },
        )
        self.assertEqual(self.storage.config()["video_render"]["ffmpeg_path"], "ffmpeg")
        self.assertTrue((project_root / "timeline.json").is_file())

    def test_creates_and_switches_projects(self):
        first_id = self.storage.active_project()["project_id"]
        second = self.storage.create_project("Edo Earthquake", "edo-earthquake")
        self.assertEqual(self.storage.active_project(), second)
        self.storage.switch_project(first_id)
        self.assertEqual(self.storage.active_project()["project_id"], first_id)

    def test_blocks_are_isolated_by_active_project(self):
        first_id = self.storage.active_project()["project_id"]
        self.storage.save_blocks([block("S001", "最初。")])
        self.storage.create_project("Second", "second")
        self.storage.save_blocks([block("S001", "二番目。")])
        self.assertEqual(self.storage.load_blocks()[0]["jp_text"], "二番目。")
        self.storage.switch_project(first_id)
        self.assertEqual(self.storage.load_blocks()[0]["jp_text"], "最初。")

    def test_visual_scenes_are_project_scoped_and_sync_block_links(self):
        blocks = [block("S001", "一。"), block("S002", "二。")]
        self.storage.save_blocks(blocks)
        scene = create_scenes_from_blocks(blocks)[0]
        scene["source_blocks"] = ["S001", "S002"]
        scene["image_assets"] = [r"images\VS001_01.png"]
        scene["selected_image"] = r"images\VS001_01.png"
        saved = self.storage.save_scenes([scene])
        self.assertEqual(saved[0]["image_assets"], ["images/VS001_01.png"])
        self.assertEqual(self.storage.load_scenes(), saved)
        self.assertEqual(self.storage.load_blocks()[0]["scene_ids"], ["VS001"])

    def test_relative_asset_paths_are_portable_and_absolute_paths_are_rejected(self):
        saved = self.storage.save_blocks([
            {**block("S001", "音声。"), "audio_file": r"audio\S001.wav"}
        ])
        self.assertEqual(saved[0]["audio_file"], "audio/S001.wav")
        self.assertEqual(normalize_relative_path(r"images\scene.png", "asset"), "images/scene.png")
        for path in (r"C:\Videos\scene.png", r"\\server\share\scene.png", "/tmp/scene.png", "../scene.png"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                normalize_relative_path(path, "asset")
        with self.assertRaisesRegex(ValueError, "audio/"):
            self.storage.save_blocks([{**block("S001", "音声。"), "audio_file": "images/wrong.wav"}])

    def test_character_library_uses_stable_directories_and_metadata(self):
        characters = self.storage.load_characters()
        character = next(item for item in characters if item["character_id"] == "CHAR_A")
        character["face_reference"] = "face_references/front.png"
        character["expression_references"] = ["expression_references/fear.png"]
        self.storage.save_characters(characters)
        root = self.root / "library" / "characters" / "CHAR_A"
        self.assertTrue((root / "face_references").is_dir())
        self.assertTrue((root / "expression_references").is_dir())
        self.assertEqual(self.storage.load_characters()[0]["face_reference"], "face_references/front.png")

    def test_period_kit_library_preserves_default_kits(self):
        kits = self.storage.load_period_kits()
        self.assertEqual({kit["period_id"] for kit in kits}, {"HEIAN", "SENGOKU", "EDO"})
        edo = next(kit for kit in kits if kit["period_id"] == "EDO")
        edo["footwear"] = "geta and zori"
        self.storage.save_period_kits(kits)
        path = self.root / "library" / "period_kits" / "EDO" / "period_kit.json"
        self.assertTrue(path.is_file())
        self.assertEqual(next(kit for kit in self.storage.load_period_kits() if kit["period_id"] == "EDO")["footwear"], "geta and zori")

    def test_environment_workspace_path_supports_spaces(self):
        configured = str(Path(self.temporary.name) / "My Video Workspace")
        with patch.dict(os.environ, {"VS_WORKSPACE_ROOT": configured}):
            self.assertEqual(default_workspace_root(Path("unused")), Path(configured))

    def test_legacy_workspace_environment_name_remains_supported(self):
        configured = str(Path(self.temporary.name) / "Legacy Workspace")
        with patch.dict(
            os.environ,
            {"FUJITA_WORKSPACE_ROOT": configured},
            clear=True,
        ):
            self.assertEqual(default_workspace_root(Path("unused")), Path(configured))


class LegacyMigrationTests(unittest.TestCase):
    def test_migrates_old_flat_data_without_deleting_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            legacy = base / "legacy-data"
            legacy.mkdir()
            old_blocks = [{"block_id": "S001", "jp_text": "江戸です。", "vn_check": "Đây là Edo."}]
            old_scenes = [{
                "scene_id": "VS001", "source_blocks": ["S001"], "period": "Edo", "year": "1700",
                "location": "Edo", "characters": ["CHAR_A"], "costume_profile": "kimono",
                "hair_profile": "Edo hair", "action": "walking", "emotion": "calm",
                "shot_type": "wide shot", "scene_note": "street", "image_prompt": "Edo street",
                "selected_image": None,
            }]
            (legacy / "content_blocks.json").write_text(json.dumps(old_blocks, ensure_ascii=False), encoding="utf-8")
            (legacy / "visual_scenes.json").write_text(json.dumps(old_scenes, ensure_ascii=False), encoding="utf-8")
            (legacy / "character_bible.json").write_text(json.dumps(DEFAULT_CHARACTERS, ensure_ascii=False), encoding="utf-8")
            (legacy / "period_kits.json").write_text(json.dumps(DEFAULT_PERIOD_KITS, ensure_ascii=False), encoding="utf-8")

            storage = WorkspaceStorage(base / "new-workspace", legacy_data_dir=legacy)
            storage.initialize()

            self.assertEqual(storage.load_blocks()[0]["audio_file"], None)
            self.assertEqual(storage.load_blocks()[0]["audio_assets"], [])
            self.assertIsNone(storage.load_blocks()[0]["duration"])
            self.assertEqual(storage.load_blocks()[0]["scene_ids"], ["VS001"])
            self.assertEqual(storage.load_scenes()[0]["image_assets"], [])
            self.assertEqual({item["character_id"] for item in storage.load_characters()}, {"CHAR_A", "CHAR_B"})
            self.assertEqual({item["period_id"] for item in storage.load_period_kits()}, {"HEIAN", "SENGOKU", "EDO"})
            self.assertTrue((legacy / "content_blocks.json").is_file())
            report = storage.config()["legacy_migration"]
            self.assertIn("content_blocks.json", report["copied"])
            self.assertIn("content_blocks.json", report["preserved_sources"])


if __name__ == "__main__":
    unittest.main()
