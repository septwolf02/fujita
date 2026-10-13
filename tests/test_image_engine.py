import tempfile
import unittest
from pathlib import Path

from image_engine import ImageGenerationEngine, ProviderRegistry
from image_providers import (
    FakeImageProvider,
    GeneratedImage,
    ImageGenerationRequest,
    ImageProvider,
    ProviderError,
)
from module2 import create_scenes_from_blocks
from storage import WorkspaceStorage


def content_block() -> dict:
    return {"block_id": "S001", "jp_text": "江戸の町です。", "vn_check": "Đây là phố Edo."}


class RecordingProvider(ImageProvider):
    name = "recording"

    def __init__(self):
        self.requests: list[ImageGenerationRequest] = []

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedImage:
        self.requests.append(request)
        return GeneratedImage(b"recording-image")


class FailOnSecondProvider(ImageProvider):
    name = "fail-second"

    def __init__(self):
        self.calls = 0

    def generate_image(self, request: ImageGenerationRequest) -> GeneratedImage:
        self.calls += 1
        if self.calls == 2:
            raise ProviderError("second variation failed")
        return GeneratedImage(b"temporary-image")


class ImageGenerationEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = WorkspaceStorage(Path(self.temporary.name) / "factory")
        self.storage.initialize()
        self._prepare_active_project()
        self.fake = FakeImageProvider()
        self.registry = ProviderRegistry()
        self.registry.register(self.fake)
        self.engine = ImageGenerationEngine(self.storage, self.registry)

    def tearDown(self):
        self.temporary.cleanup()

    def _prepare_active_project(self):
        block = content_block()
        self.storage.save_blocks([block])
        scene = create_scenes_from_blocks([block])[0]
        scene.update(
            {
                "period": "Edo",
                "year": "1700",
                "location": "Edo town street",
                "characters": ["CHAR_A"],
                "costume_profile": "merchant kimono",
                "hair_profile": "Edo merchant hair",
                "action": "walking",
                "emotion": "alert",
                "shot_type": "wide shot",
                "image_prompt": "Simple historical illustration",
            }
        )
        self.storage.save_scenes([scene])

    def test_provider_abstraction_receives_scene_library_context_and_references(self):
        characters = self.storage.load_characters()
        character = next(item for item in characters if item["character_id"] == "CHAR_A")
        character["face_reference"] = "face_references/front.png"
        self.storage.save_characters(characters)
        reference = self.storage.characters_root / "CHAR_A" / "face_references" / "front.png"
        reference.write_bytes(b"face-reference")

        provider = RecordingProvider()
        registry = ProviderRegistry()
        registry.register(provider)
        engine = ImageGenerationEngine(self.storage, registry)
        engine.generate("VS001", provider_name="recording")

        request = provider.requests[0]
        self.assertIn("merchant kimono", request.prompt)
        self.assertIn("CHAR_A", request.prompt)
        self.assertEqual(request.references, (reference,))
        self.assertEqual(request.metadata["period_kit"]["period_id"], "EDO")

    def test_fake_provider_generates_valid_png_in_active_project(self):
        result = self.engine.generate("VS001")
        relative = result["generated"][0]
        self.assertEqual(relative, "images/VS001_01.png")
        path = self.storage.active_project_root() / relative
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_generation_updates_image_assets_without_selecting_implicitly(self):
        self.engine.generate("VS001")
        scene = self.storage.load_scenes()[0]
        self.assertEqual(scene["image_assets"], ["images/VS001_01.png"])
        self.assertIsNone(scene["selected_image"])

    def test_select_image_updates_selected_image(self):
        relative = self.engine.generate("VS001")["generated"][0]
        self.engine.select_image("VS001", relative)
        self.assertEqual(self.storage.load_scenes()[0]["selected_image"], relative)

    def test_generate_variations_and_regenerate_keep_old_images(self):
        variations = self.engine.generate("VS001", count=3)["generated"]
        regenerated = self.engine.generate("VS001")["generated"]
        self.assertEqual(
            variations,
            ["images/VS001_01.png", "images/VS001_02.png", "images/VS001_03.png"],
        )
        self.assertEqual(regenerated, ["images/VS001_04.png"])
        self.assertEqual(self.storage.load_scenes()[0]["image_assets"], variations + regenerated)
        self.assertTrue(all((self.storage.active_project_root() / path).is_file() for path in variations + regenerated))

    def test_provider_failure_keeps_scene_and_files_unchanged(self):
        registry = ProviderRegistry()
        registry.register(FailOnSecondProvider())
        engine = ImageGenerationEngine(self.storage, registry)
        before = self.storage.load_scenes()
        with self.assertRaisesRegex(ProviderError, "second variation"):
            engine.generate("VS001", count=3, provider_name="fail-second")
        self.assertEqual(self.storage.load_scenes(), before)
        self.assertEqual(list((self.storage.active_project_root() / "images").iterdir()), [])

    def test_project_isolation_starts_versions_inside_each_project(self):
        first_id = self.storage.active_project()["project_id"]
        first_root = self.storage.active_project_root()
        self.engine.generate("VS001")

        self.storage.create_project("Second Video", "second-video")
        second_root = self.storage.active_project_root()
        self._prepare_active_project()
        second_result = self.engine.generate("VS001")

        self.assertEqual(second_result["generated"], ["images/VS001_01.png"])
        self.assertTrue((first_root / "images" / "VS001_01.png").is_file())
        self.assertTrue((second_root / "images" / "VS001_01.png").is_file())
        self.storage.switch_project(first_id)
        self.assertEqual(self.storage.load_scenes()[0]["image_assets"], ["images/VS001_01.png"])

    def test_delete_image_requires_explicit_action_and_clears_selection(self):
        relative = self.engine.generate("VS001")["generated"][0]
        self.engine.select_image("VS001", relative)
        self.engine.delete_image("VS001", relative)
        scene = self.storage.load_scenes()[0]
        self.assertEqual(scene["image_assets"], [])
        self.assertIsNone(scene["selected_image"])
        self.assertFalse((self.storage.active_project_root() / relative).exists())


if __name__ == "__main__":
    unittest.main()

