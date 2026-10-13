import subprocess
import tempfile
import unittest
from pathlib import Path

from module2 import create_scenes_from_blocks
from storage import WorkspaceStorage
from timeline_engine import TimelineEngine
from video_renderer import FFmpegRenderer, RenderError


class FakeRunner:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.returncode == 0:
            Path(command[-1]).write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(command, self.returncode, "", "render error")


class VideoRendererTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = WorkspaceStorage(Path(self.temporary.name) / "factory")
        self.storage.initialize()
        self.prepare_project()

    def tearDown(self):
        self.temporary.cleanup()

    def prepare_project(self):
        blocks = [
            {
                "block_id": "S001", "jp_text": "一。", "vn_check": "",
                "audio_file": "audio/S001_01.wav", "audio_assets": ["audio/S001_01.wav"],
                "duration": 2.0,
            },
            {
                "block_id": "S002", "jp_text": "二。", "vn_check": "",
                "audio_file": "audio/S002_01.wav", "audio_assets": ["audio/S002_01.wav"],
                "duration": 3.0,
            },
        ]
        self.storage.save_blocks(blocks)
        scenes = create_scenes_from_blocks(blocks)
        root = self.storage.active_project_root()
        for block in blocks:
            (root / block["audio_file"]).write_bytes(b"audio")
        for index, scene in enumerate(scenes):
            scene["selected_image"] = f"images/{scene['scene_id']}_01.png"
            scene["image_assets"] = [scene["selected_image"]]
            scene["motion"] = "zoom_in" if index == 0 else "pan_right"
            scene["transition"] = "fade" if index == 0 else "cut"
            (root / scene["selected_image"]).write_bytes(b"image")
        self.storage.save_scenes(scenes)

    def test_ffmpeg_command_generation(self):
        timeline = TimelineEngine(self.storage).regenerate()
        renderer = FFmpegRenderer(self.storage)
        output = self.storage.active_project_root() / "output/draft_001.mp4"
        command = renderer.build_command(
            timeline, self.storage.load_blocks(), self.storage.active_project_root(), output
        )
        joined = " ".join(command)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("scale=1920:1080", joined)
        self.assertIn("zoompan", joined)
        self.assertIn("concat=n=2:v=0:a=1", joined)
        self.assertIn("libx264", command)
        self.assertIn("aac", command)
        self.assertEqual(command[-1], str(output))

    def test_render_uses_versioned_project_output_and_keeps_old_files(self):
        runner = FakeRunner()
        renderer = FFmpegRenderer(self.storage, runner=runner)
        first = renderer.render()
        second = renderer.render()
        self.assertEqual(first["output"], "output/draft_001.mp4")
        self.assertEqual(second["output"], "output/draft_002.mp4")
        root = self.storage.active_project_root()
        self.assertTrue((root / first["output"]).is_file())
        self.assertTrue((root / second["output"]).is_file())

    def test_dry_run_returns_command_without_creating_video(self):
        result = FFmpegRenderer(self.storage).render(dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["output"], "output/draft_001.mp4")
        self.assertFalse((self.storage.active_project_root() / result["output"]).exists())

    def test_render_failure_keeps_assets_and_writes_readable_log(self):
        image = self.storage.active_project_root() / "images/VS001_01.png"
        renderer = FFmpegRenderer(self.storage, runner=FakeRunner(returncode=1))
        with self.assertRaisesRegex(RenderError, "Xem log"):
            renderer.render()
        self.assertTrue(image.is_file())
        log = self.storage.active_project_root() / "output/draft_001.log"
        self.assertIn("render error", log.read_text(encoding="utf-8"))

    def test_project_isolation_starts_output_versions_independently(self):
        renderer = FFmpegRenderer(self.storage, runner=FakeRunner())
        first_root = self.storage.active_project_root()
        renderer.render()

        self.storage.create_project("Second", "second")
        self.prepare_project()
        second_root = self.storage.active_project_root()
        result = renderer.render()

        self.assertEqual(result["output"], "output/draft_001.mp4")
        self.assertTrue((first_root / "output/draft_001.mp4").is_file())
        self.assertTrue((second_root / "output/draft_001.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
