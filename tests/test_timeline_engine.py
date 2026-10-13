import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from module2 import create_scenes_from_blocks
from storage import WorkspaceStorage
from timeline_engine import TimelineEngine


def block(block_id: str, duration: float, audio: bool = True) -> dict:
    relative = f"audio/{block_id}_01.wav" if audio else None
    return {
        "block_id": block_id,
        "jp_text": f"{block_id} の本文。",
        "vn_check": "",
        "audio_file": relative,
        "audio_assets": [relative] if relative else [],
        "duration": duration if audio else None,
    }


class TimelineEngineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = WorkspaceStorage(Path(self.temporary.name) / "factory")
        self.storage.initialize()
        self.engine = TimelineEngine(self.storage)

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, blocks, scenes):
        self.storage.save_blocks(blocks)
        root = self.storage.active_project_root()
        for item in blocks:
            if item.get("audio_file"):
                (root / item["audio_file"]).write_bytes(b"audio")
        for scene in scenes:
            scene["selected_image"] = scene.get("selected_image") or f"images/{scene['scene_id']}_01.png"
            scene["image_assets"] = [scene["selected_image"]]
            (root / scene["selected_image"]).write_bytes(b"image")
        self.storage.save_scenes(scenes)

    def two_scenes_for_one_block(self, duration=8.0):
        blocks = [block("S001", duration)]
        first = create_scenes_from_blocks(blocks)[0]
        second = deepcopy(first)
        second["scene_id"] = "VS002"
        return blocks, [first, second]

    def test_timeline_uses_real_audio_duration(self):
        blocks = [block("S001", 8.27)]
        scenes = create_scenes_from_blocks(blocks)
        self.prepare(blocks, scenes)
        timeline = self.engine.regenerate()
        self.assertEqual(timeline["total_duration"], 8.27)
        self.assertEqual(timeline["items"][0]["start"], 0.0)
        self.assertEqual(timeline["items"][0]["end"], 8.27)
        self.assertEqual(timeline["warnings"], [])

    def test_scene_covering_multiple_blocks_gets_sum_of_audio(self):
        blocks = [block("S001", 4.1), block("S002", 5.3)]
        scene = create_scenes_from_blocks(blocks)[0]
        scene["source_blocks"] = ["S001", "S002"]
        self.prepare(blocks, [scene])
        item = self.engine.regenerate()["items"][0]
        self.assertEqual(item["duration"], 9.4)
        self.assertEqual(item["audio_blocks"], ["S001", "S002"])

    def test_one_block_is_split_evenly_between_scenes(self):
        blocks, scenes = self.two_scenes_for_one_block(8.27)
        self.prepare(blocks, scenes)
        items = self.engine.regenerate()["items"]
        self.assertEqual([item["duration"] for item in items], [4.135, 4.135])
        self.assertEqual(items[0]["end"], items[1]["start"])
        self.assertEqual(items[-1]["end"], 8.27)

    def test_duration_weight_controls_auto_split(self):
        blocks, scenes = self.two_scenes_for_one_block(8.0)
        scenes[0]["duration_weight"] = 1
        scenes[1]["duration_weight"] = 3
        self.prepare(blocks, scenes)
        self.assertEqual(
            [item["duration"] for item in self.engine.regenerate()["items"]], [2.0, 6.0]
        )

    def test_rounded_scene_durations_still_sum_to_audio_clock(self):
        blocks = [block("S001", 1.0)]
        scenes = []
        for number in range(1, 4):
            scene = create_scenes_from_blocks(blocks)[0]
            scene["scene_id"] = f"VS{number:03d}"
            scenes.append(scene)
        self.prepare(blocks, scenes)
        timeline = self.engine.regenerate()
        self.assertEqual(sum(item["duration"] for item in timeline["items"]), 1.0)
        self.assertEqual(timeline["items"][-1]["end"], 1.0)

    def test_manual_override_reserves_time_and_auto_scene_uses_remainder(self):
        blocks, scenes = self.two_scenes_for_one_block(8.0)
        scenes[0].update({"duration_mode": "manual", "duration_override": 3.0})
        self.prepare(blocks, scenes)
        timeline = self.engine.regenerate()
        self.assertEqual([item["duration"] for item in timeline["items"]], [3.0, 5.0])
        self.assertEqual(timeline["warnings"], [])

    def test_missing_image_and_audio_are_reported_with_ids(self):
        blocks = [block("S001", 0, audio=False)]
        scenes = create_scenes_from_blocks(blocks)
        self.storage.save_blocks(blocks)
        self.storage.save_scenes(scenes)
        warnings = self.engine.regenerate()["warnings"]
        codes = {warning["code"] for warning in warnings}
        self.assertIn("missing_audio", codes)
        self.assertIn("missing_audio_duration", codes)
        self.assertIn("missing_image", codes)
        self.assertTrue(any(warning.get("block_id") == "S001" for warning in warnings))
        self.assertTrue(any(warning.get("scene_id") == "VS001" for warning in warnings))

    def test_scene_list_order_is_preserved(self):
        blocks, scenes = self.two_scenes_for_one_block(6.0)
        scenes.reverse()
        self.prepare(blocks, scenes)
        timeline = self.engine.regenerate()
        self.assertEqual([item["scene_id"] for item in timeline["items"]], ["VS002", "VS001"])

    def test_timeline_is_saved_in_active_project(self):
        blocks = [block("S001", 2.0)]
        scenes = create_scenes_from_blocks(blocks)
        self.prepare(blocks, scenes)
        timeline = self.engine.regenerate()
        self.assertEqual(self.storage.load_timeline(), timeline)
        self.assertTrue((self.storage.active_project_root() / "timeline.json").is_file())


if __name__ == "__main__":
    unittest.main()
