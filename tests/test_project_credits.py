import json
import tempfile
import unittest
from pathlib import Path

from storage import WorkspaceStorage


VOICEVOX_CREDIT = {
    "type": "tts",
    "provider": "voicevox",
    "speaker": "剣崎雌雄",
    "style": "ノーマル",
    "text": "VOICEVOX:剣崎雌雄",
}


class ProjectCreditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = WorkspaceStorage(Path(self.temporary.name) / "factory")
        self.storage.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def test_save_and_load_project_credits(self):
        saved = self.storage.add_project_credits([VOICEVOX_CREDIT])

        self.assertEqual(saved, [VOICEVOX_CREDIT])
        self.assertEqual(self.storage.load_project_credits(), [VOICEVOX_CREDIT])
        metadata = json.loads(
            (self.storage.active_project_root() / "project.json").read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["credits"], [VOICEVOX_CREDIT])

    def test_duplicate_credit_is_stored_once(self):
        self.storage.add_project_credits([VOICEVOX_CREDIT, VOICEVOX_CREDIT])
        self.storage.add_project_credits([VOICEVOX_CREDIT])

        self.assertEqual(self.storage.load_project_credits(), [VOICEVOX_CREDIT])

    def test_project_without_credits_is_backward_compatible(self):
        metadata_path = self.storage.active_project_root() / "project.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata.pop("credits")
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")

        self.assertEqual(self.storage.load_project_credits(), [])
        self.assertEqual(self.storage.list_projects()[0]["credits"], [])
        self.assertEqual(self.storage.add_project_credits([VOICEVOX_CREDIT]), [VOICEVOX_CREDIT])

    def test_multiple_provider_and_speaker_snapshots_are_preserved(self):
        other_voice = {
            "type": "tts",
            "provider": "voicevox",
            "speaker": "四国めたん",
            "style": "ノーマル",
            "text": "VOICEVOX:四国めたん",
        }
        other_provider = {
            "type": "tts",
            "provider": "future-provider",
            "speaker": "JP Voice 1",
            "style": "narration",
            "text": "Audio: Future Provider",
        }

        saved = self.storage.add_project_credits(
            [VOICEVOX_CREDIT, other_voice, other_provider, VOICEVOX_CREDIT]
        )

        self.assertEqual(saved, [VOICEVOX_CREDIT, other_voice, other_provider])

    def test_credits_are_isolated_by_project(self):
        first_id = self.storage.active_project()["project_id"]
        self.storage.add_project_credits([VOICEVOX_CREDIT])
        self.storage.create_project("Second", "second")

        self.assertEqual(self.storage.load_project_credits(), [])
        self.storage.switch_project(first_id)
        self.assertEqual(self.storage.load_project_credits(), [VOICEVOX_CREDIT])


if __name__ == "__main__":
    unittest.main()
