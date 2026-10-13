import io
import json
import tempfile
import unittest
import wave
from pathlib import Path
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from storage import WorkspaceStorage
from tts_engine import TTSEngine, TTSProviderRegistry, default_tts_registry
from tts_providers import TTSGenerationRequest, TTSProviderError, VoicevoxTTSProvider


def wav_bytes(duration: float = 1.25, sample_rate: int = 16_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\x00\x00" * round(duration * sample_rate))
    return output.getvalue()


class FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.content


class VoicevoxOpener:
    def __init__(self, audio: bytes | None = None):
        self.audio = audio or wav_bytes()
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        path = urlparse(request.full_url).path
        if path == "/version":
            return FakeResponse(json.dumps("0.22.0").encode())
        if path == "/speakers":
            return FakeResponse(
                json.dumps(
                    [
                        {
                            "name": "剣崎雌雄",
                            "speaker_uuid": "test-speaker",
                            "styles": [
                                {"name": "ノーマル", "id": 53},
                                {"name": "ツンツン", "id": 54},
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ).encode("utf-8")
            )
        if path == "/audio_query":
            return FakeResponse(json.dumps({"speedScale": 1.0, "intonationScale": 1.0}).encode())
        if path == "/synthesis":
            return FakeResponse(self.audio)
        raise AssertionError(f"unexpected VOICEVOX path: {path}")


def provider(opener, required_credit=None) -> VoicevoxTTSProvider:
    return VoicevoxTTSProvider(
        "http://127.0.0.1:50021",
        "剣崎雌雄",
        "ノーマル",
        required_credit=required_credit,
        opener=opener,
    )


class VoicevoxProviderTests(unittest.TestCase):
    def test_workspace_config_and_default_registry_include_voicevox(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = WorkspaceStorage(Path(directory) / "factory")
            storage.initialize()
            config = storage.config()["tts"]
            self.assertEqual(config["provider"], "fake")
            self.assertEqual(config["voicevox"]["engine_url"], "http://127.0.0.1:50021")
            self.assertEqual(config["voicevox"]["speaker_name"], "剣崎雌雄")
            self.assertEqual(config["voicevox"]["style_name"], "ノーマル")
            self.assertEqual(default_tts_registry(storage).names(), ["fake", "voicevox"])

    def test_required_credit_is_loaded_from_config(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = WorkspaceStorage(Path(directory) / "factory")
            storage.initialize()
            config = storage.config()
            config["tts"]["voicevox"]["required_credit"] = "VOICEVOX:剣崎雌雄"
            storage._save_config(config)

            configured = default_tts_registry(storage).get("voicevox")
            self.assertEqual(configured.required_credit, "VOICEVOX:剣崎雌雄")

    def test_resolves_style_id_from_speaker_and_style_names(self):
        opener = VoicevoxOpener()
        self.assertEqual(provider(opener).resolve_speaker_id(), 53)
        request = opener.requests[0][0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(urlparse(request.full_url).path, "/speakers")

    def test_health_query_and_synthesis_request_flow(self):
        opener = VoicevoxOpener(audio=wav_bytes(1.5))
        result = provider(opener).generate_voice(
            TTSGenerationRequest(
                block_id="S001",
                jp_text="江戸の朝です。",
                voice_id="剣崎雌雄",
                style="ノーマル",
                speed=1.2,
            )
        )
        paths = [urlparse(request.full_url).path for request, _ in opener.requests]
        self.assertEqual(paths, ["/version", "/speakers", "/audio_query", "/synthesis"])
        query_request = opener.requests[2][0]
        query_params = parse_qs(urlparse(query_request.full_url).query)
        self.assertEqual(query_request.get_method(), "POST")
        self.assertEqual(query_params["text"], ["江戸の朝です。"])
        self.assertEqual(query_params["speaker"], ["53"])
        synthesis_request = opener.requests[3][0]
        synthesis_body = json.loads(synthesis_request.data.decode("utf-8"))
        self.assertEqual(synthesis_body["speedScale"], 1.2)
        self.assertEqual(result.content[:4], b"RIFF")
        self.assertAlmostEqual(result.duration, 1.5, places=3)

    def test_engine_unavailable_has_clear_error(self):
        def unavailable(*_, **__):
            raise URLError("connection refused")

        with self.assertRaisesRegex(
            TTSProviderError, "Không thể kết nối VOICEVOX Engine.*mở VOICEVOX"
        ):
            provider(unavailable).check_health()

    def test_invalid_speaker_has_clear_error(self):
        opener = VoicevoxOpener()
        missing = VoicevoxTTSProvider(
            "http://127.0.0.1:50021", "Không tồn tại", "ノーマル", opener=opener
        )
        with self.assertRaisesRegex(TTSProviderError, "Không tìm thấy VOICEVOX speaker"):
            missing.resolve_speaker_id()

    def test_invalid_style_has_clear_error(self):
        opener = VoicevoxOpener()
        missing = VoicevoxTTSProvider(
            "http://127.0.0.1:50021", "剣崎雌雄", "Không tồn tại", opener=opener
        )
        with self.assertRaisesRegex(TTSProviderError, "Không tìm thấy style"):
            missing.resolve_speaker_id()

    def test_engine_saves_voicevox_wav_and_real_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = WorkspaceStorage(Path(directory) / "factory")
            storage.initialize()
            storage.save_blocks(
                [{"block_id": "S001", "jp_text": "江戸です。", "vn_check": ""}]
            )
            config = storage.config()
            config["tts"]["provider"] = "voicevox"
            storage._save_config(config)
            opener = VoicevoxOpener(audio=wav_bytes(2.25))
            registry = TTSProviderRegistry()
            registry.register(provider(opener))
            result = TTSEngine(storage, registry).generate("S001")
            block = storage.load_blocks()[0]
            self.assertEqual(result["generated"], "audio/S001_01.wav")
            self.assertEqual(block["audio_assets"], ["audio/S001_01.wav"])
            self.assertEqual(block["audio_file"], "audio/S001_01.wav")
            self.assertEqual(block["duration"], 2.25)
            self.assertTrue((storage.active_project_root() / block["audio_file"]).is_file())

    def test_generate_all_snapshots_configured_credit_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = WorkspaceStorage(Path(directory) / "factory")
            storage.initialize()
            storage.save_blocks(
                [
                    {"block_id": "S001", "jp_text": "江戸です。", "vn_check": ""},
                    {"block_id": "S002", "jp_text": "朝です。", "vn_check": ""},
                ]
            )
            registry = TTSProviderRegistry()
            registry.register(provider(VoicevoxOpener(), "VOICEVOX:剣崎雌雄"))
            engine = TTSEngine(storage, registry)

            engine.generate_all("voicevox")
            engine.generate("S001", "voicevox")

            self.assertEqual(
                storage.load_project_credits(),
                [
                    {
                        "type": "tts",
                        "provider": "voicevox",
                        "speaker": "剣崎雌雄",
                        "style": "ノーマル",
                        "text": "VOICEVOX:剣崎雌雄",
                    }
                ],
            )

    def test_voicevox_generation_is_isolated_by_project(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = WorkspaceStorage(Path(directory) / "factory")
            storage.initialize()
            registry = TTSProviderRegistry()
            registry.register(provider(VoicevoxOpener()))
            engine = TTSEngine(storage, registry)
            content = [{"block_id": "S001", "jp_text": "江戸です。", "vn_check": ""}]
            storage.save_blocks(content)
            first_root = storage.active_project_root()
            engine.generate("S001", "voicevox")

            storage.create_project("Second", "second")
            storage.save_blocks(content)
            second_root = storage.active_project_root()
            result = engine.generate("S001", "voicevox")

            self.assertEqual(result["generated"], "audio/S001_01.wav")
            self.assertTrue((first_root / "audio/S001_01.wav").is_file())
            self.assertTrue((second_root / "audio/S001_01.wav").is_file())


if __name__ == "__main__":
    unittest.main()
