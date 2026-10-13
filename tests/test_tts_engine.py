import tempfile
import threading
import unittest
import wave
from pathlib import Path

from storage import WorkspaceStorage
from tts_engine import TTSEngine, TTSProviderRegistry
from tts_providers import FakeTTSProvider, TTSProviderError


def blocks() -> list[dict]:
    return [
        {"block_id": "S001", "jp_text": "江戸の町です。", "vn_check": "Phố Edo."},
        {"block_id": "S002", "jp_text": "夜になりました。", "vn_check": "Trời đã tối."},
    ]


class RecordingProvider(FakeTTSProvider):
    name = "recording"


class FailOnSecondProvider(FakeTTSProvider):
    name = "fail-second"

    def generate_voice(self, request):
        if len(self.requests) == 1:
            raise TTSProviderError("second block failed")
        return super().generate_voice(request)


class BlockingProvider(FakeTTSProvider):
    name = "blocking"

    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def generate_voice(self, request):
        self.started.set()
        if not self.release.wait(timeout=2):
            raise TTSProviderError("test provider timed out")
        return super().generate_voice(request)


class TTSEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = WorkspaceStorage(Path(self.temporary.name) / "factory")
        self.storage.initialize()
        self.storage.save_blocks(blocks())
        self.fake = FakeTTSProvider()
        self.registry = TTSProviderRegistry()
        self.registry.register(self.fake)
        self.engine = TTSEngine(self.storage, self.registry)

    def tearDown(self):
        self.temporary.cleanup()

    def test_provider_abstraction_receives_japanese_text_and_runtime_settings(self):
        provider = RecordingProvider()
        registry = TTSProviderRegistry()
        registry.register(provider)
        TTSEngine(self.storage, registry).generate("S001", "recording")

        request = provider.requests[0]
        self.assertEqual(request.jp_text, "江戸の町です。")
        self.assertEqual(request.voice_id, "fake-ja")
        self.assertEqual(request.metadata["language"], "ja")
        self.assertEqual(request.version, 1)

    def test_fake_generation_saves_valid_wav_in_active_project(self):
        result = self.engine.generate("S001")
        relative = result["generated"]
        self.assertEqual(relative, "audio/S001_01.wav")
        path = self.storage.active_project_root() / relative
        with wave.open(str(path), "rb") as audio:
            self.assertEqual(audio.getnchannels(), 1)
            self.assertEqual(audio.getframerate(), 16_000)
            self.assertGreater(audio.getnframes(), 0)

    def test_generation_updates_assets_selected_file_and_duration(self):
        self.engine.generate("S001")
        block = self.storage.load_blocks()[0]
        self.assertEqual(block["audio_assets"], ["audio/S001_01.wav"])
        self.assertEqual(block["audio_file"], "audio/S001_01.wav")
        self.assertGreater(block["duration"], 0)

    def test_regenerate_versions_and_keeps_old_audio(self):
        first = self.engine.generate("S001")["generated"]
        second = self.engine.generate("S001")["generated"]
        block = self.storage.load_blocks()[0]
        self.assertEqual([first, second], ["audio/S001_01.wav", "audio/S001_02.wav"])
        self.assertEqual(block["audio_assets"], [first, second])
        self.assertEqual(block["audio_file"], second)
        self.assertTrue(all((self.storage.active_project_root() / path).is_file() for path in [first, second]))

    def test_select_audio_changes_active_version(self):
        first = self.engine.generate("S001")["generated"]
        self.engine.generate("S001")
        result = self.engine.select_audio("S001", first)
        self.assertEqual(result["block"]["audio_file"], first)
        self.assertGreater(result["block"]["duration"], 0)

    def test_provider_failure_rolls_back_block_and_files(self):
        registry = TTSProviderRegistry()
        registry.register(FakeTTSProvider(fail=True))
        before = self.storage.load_blocks()
        with self.assertRaisesRegex(TTSProviderError, "failure"):
            TTSEngine(self.storage, registry).generate("S001")
        self.assertEqual(self.storage.load_blocks(), before)
        self.assertEqual(list((self.storage.active_project_root() / "audio").iterdir()), [])

    def test_generate_all_is_atomic_when_provider_fails(self):
        registry = TTSProviderRegistry()
        registry.register(FailOnSecondProvider())
        before = self.storage.load_blocks()
        with self.assertRaisesRegex(TTSProviderError, "second block"):
            TTSEngine(self.storage, registry).generate_all("fail-second")
        self.assertEqual(self.storage.load_blocks(), before)
        self.assertEqual(list((self.storage.active_project_root() / "audio").iterdir()), [])

    def test_generate_all_updates_every_block(self):
        result = self.engine.generate_all()
        self.assertEqual(result["generated"], ["audio/S001_01.wav", "audio/S002_01.wav"])
        self.assertTrue(all(block["audio_file"] for block in self.storage.load_blocks()))

    def test_project_isolation_starts_versions_per_project(self):
        first_id = self.storage.active_project()["project_id"]
        first_root = self.storage.active_project_root()
        self.engine.generate("S001")

        self.storage.create_project("Second", "second")
        second_root = self.storage.active_project_root()
        self.storage.save_blocks(blocks())
        result = self.engine.generate("S001")

        self.assertEqual(result["generated"], "audio/S001_01.wav")
        self.assertTrue((first_root / "audio/S001_01.wav").is_file())
        self.assertTrue((second_root / "audio/S001_01.wav").is_file())
        self.storage.switch_project(first_id)
        self.assertEqual(self.storage.load_blocks()[0]["audio_assets"], ["audio/S001_01.wav"])

    def test_project_switch_waits_until_generation_finishes(self):
        current_root = self.storage.active_project_root()
        self.storage.create_project("Destination", "destination")
        self.storage.switch_project("project-001")
        provider = BlockingProvider()
        registry = TTSProviderRegistry()
        registry.register(provider)
        engine = TTSEngine(self.storage, registry)
        switch_finished = threading.Event()
        errors = []

        generation = threading.Thread(
            target=lambda: self._capture(errors, engine.generate, "S001", "blocking")
        )
        switching = threading.Thread(
            target=lambda: self._capture(errors, self._switch_and_signal, switch_finished)
        )
        generation.start()
        self.assertTrue(provider.started.wait(timeout=1))
        switching.start()
        self.assertFalse(switch_finished.wait(timeout=0.05))
        provider.release.set()
        generation.join(timeout=2)
        switching.join(timeout=2)

        self.assertEqual(errors, [])
        self.assertTrue((current_root / "audio/S001_01.wav").is_file())
        self.assertEqual(self.storage.active_project()["project_id"], "destination")

    @staticmethod
    def _capture(errors, function, *args):
        try:
            function(*args)
        except Exception as error:  # pragma: no cover - reported through assertion
            errors.append(error)

    def _switch_and_signal(self, event):
        self.storage.switch_project("destination")
        event.set()


if __name__ == "__main__":
    unittest.main()
