import base64
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.parse import urlparse

from image_engine import ImageGenerationEngine, ProviderRegistry, default_registry
from image_providers import AlibabaQwenImageProvider, ImageGenerationRequest, ProviderError
from image_providers.fake import _solid_png
from module2 import create_scenes_from_blocks
from storage import WorkspaceStorage


PNG = _solid_png(16, 9, (30, 60, 90))


class Response:
    def __init__(self, value):
        self.value = value if isinstance(value, bytes) else json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return self.value


class Opener:
    def __init__(self, fail_at=None):
        self.requests = []
        self.fail_at = fail_at

    def __call__(self, request, timeout):
        self.requests.append(request)
        if len(self.requests) == self.fail_at:
            raise URLError("outage")
        return Response({"output": {"results": [{"b64_json": base64.b64encode(PNG).decode()}]}})


def provider(opener, key="test-only-key"):
    return AlibabaQwenImageProvider("https://dashscope-intl.aliyuncs.com",
                                   api_key=key, opener=opener, sleeper=lambda _: None,
                                   timeout_seconds=2)


class AlibabaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = WorkspaceStorage(Path(self.temp.name) / "workspace")
        self.storage.initialize()
        self.prepare()

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self):
        block = {"block_id": "S001", "jp_text": "江戸です。", "vn_check": ""}
        self.storage.save_blocks([block])
        scene = create_scenes_from_blocks([block])[0]
        scene["image_prompt"] = "Historical Edo street"
        self.storage.save_scenes([scene])

    def engine(self, opener):
        registry = ProviderRegistry()
        registry.register(provider(opener))
        return ImageGenerationEngine(self.storage, registry)

    def test_config_registry_and_key_precedence(self):
        config = self.storage.config()
        config["image_generation"]["alibaba_qwen"]["base_url"] = "https://example.test"
        self.storage._save_config(config)
        self.assertEqual(default_registry(self.storage).get("alibaba_qwen").base_url,
                         "https://example.test")
        opener = Opener()
        with patch.dict(os.environ, {"VS_ALIBABA_API_KEY": "environment-key"}):
            provider(opener).generate_image(ImageGenerationRequest("VS001", "Main prompt", variation_index=3))
        sent = opener.requests[0]
        payload = json.loads(sent.data)
        self.assertEqual(sent.headers["Authorization"], "Bearer environment-key")
        self.assertEqual(payload["model"], "qwen-mt-image-2.0")
        self.assertEqual(payload["parameters"]["seed"], 3)
        self.assertEqual(payload["input"]["messages"][0]["content"][0]["text"], "Main prompt")

    def test_variations_regenerate_select_and_project_isolation(self):
        opener = Opener()
        engine = self.engine(opener)
        first = engine.generate("VS001", count=4, provider_name="alibaba_qwen")["generated"]
        next_image = engine.generate("VS001", provider_name="alibaba_qwen")["generated"][0]
        engine.select_image("VS001", next_image)
        self.assertEqual(first, [f"images/VS001_{i:02d}.png" for i in range(1, 5)])
        self.assertEqual(next_image, "images/VS001_05.png")
        self.assertEqual(self.storage.load_scenes()[0]["selected_image"], next_image)
        self.assertTrue((self.storage.active_project_root() / next_image).read_bytes().startswith(b"\x89PNG"))
        self.storage.create_project("Second", "second")
        self.prepare()
        self.assertEqual(engine.generate("VS001", provider_name="alibaba_qwen")["generated"],
                         ["images/VS001_01.png"])

    def test_failure_preserves_scene_and_files(self):
        before = self.storage.load_scenes()
        with self.assertRaises(ProviderError):
            self.engine(Opener(fail_at=2)).generate("VS001", count=4, provider_name="alibaba_qwen")
        self.assertEqual(self.storage.load_scenes(), before)
        self.assertEqual(list((self.storage.active_project_root() / "images").iterdir()), [])

    def test_async_url_and_missing_key(self):
        calls = []
        def opener(request, timeout):
            calls.append(request)
            path = urlparse(request.full_url).path
            if path.endswith("/generation"):
                return Response({"output": {"task_id": "task-1", "task_status": "PENDING"}})
            if path == "/api/v1/tasks/task-1":
                return Response({"output": {"results": [{"url": "https://cdn.test/p.png"}]}})
            return Response(PNG)
        self.assertEqual(provider(opener).generate_image(ImageGenerationRequest("VS001", "prompt")).content, PNG)
        self.assertNotIn("Authorization", calls[-1].headers)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ProviderError, "VS_ALIBABA_API_KEY"):
            provider(Opener(), key="").generate_image(ImageGenerationRequest("VS001", "prompt"))


if __name__ == "__main__":
    unittest.main()
