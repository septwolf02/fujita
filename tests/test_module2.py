import unittest

from module2 import (
    create_scenes_from_blocks,
    merge_scenes,
    split_scene,
    validate_scenes,
)


BLOCKS = [
    {"block_id": "S001", "jp_text": "一。", "vn_check": "Một."},
    {"block_id": "S002", "jp_text": "二。", "vn_check": "Hai."},
    {"block_id": "S003", "jp_text": "三。", "vn_check": "Ba."},
]


class VisualSceneTests(unittest.TestCase):
    def test_create_scenes_keeps_content_as_references(self):
        scenes = create_scenes_from_blocks(BLOCKS)
        self.assertEqual(scenes[0]["scene_id"], "VS001")
        self.assertEqual(scenes[0]["source_blocks"], ["S001"])
        self.assertNotIn("jp_text", scenes[0])
        self.assertEqual(scenes[0]["image_assets"], [])
        self.assertIsNone(scenes[0]["selected_image"])
        self.assertEqual(scenes[0]["duration_mode"], "auto")
        self.assertEqual(scenes[0]["duration_weight"], 1.0)
        self.assertIsNone(scenes[0]["duration_override"])
        self.assertEqual(scenes[0]["motion"], "hold")
        self.assertEqual(scenes[0]["transition"], "cut")

    def test_merge_consecutive_scenes_combines_source_blocks(self):
        scenes = create_scenes_from_blocks(BLOCKS)
        scenes[0]["location"] = "Edo"
        scenes[0]["selected_image"] = "old.png"
        merged = merge_scenes(scenes, ["VS001", "VS002"])
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["scene_id"], "VS001")
        self.assertEqual(merged[0]["source_blocks"], ["S001", "S002"])
        self.assertEqual(merged[0]["location"], "Edo")
        self.assertIsNone(merged[0]["selected_image"])

    def test_merge_rejects_nonconsecutive_scenes(self):
        with self.assertRaisesRegex(ValueError, "consecutive"):
            merge_scenes(create_scenes_from_blocks(BLOCKS), ["VS001", "VS003"])

    def test_split_multi_block_scene_divides_sources(self):
        scene = create_scenes_from_blocks(BLOCKS[:1])[0]
        scene["source_blocks"] = ["S001", "S002", "S003"]
        result = split_scene([scene], "VS001", after_source_index=2)
        self.assertEqual(result[0]["source_blocks"], ["S001", "S002"])
        self.assertEqual(result[1]["source_blocks"], ["S003"])
        self.assertEqual(result[1]["scene_id"], "VS002")

    def test_split_single_block_reuses_source_for_two_visuals(self):
        scene = create_scenes_from_blocks(BLOCKS[:1])[0]
        result = split_scene([scene], "VS001")
        self.assertEqual(result[0]["source_blocks"], ["S001"])
        self.assertEqual(result[1]["source_blocks"], ["S001"])

    def test_validate_scene_rejects_missing_block_link(self):
        scenes = create_scenes_from_blocks(BLOCKS[:1])
        with self.assertRaisesRegex(ValueError, "missing blocks"):
            validate_scenes(scenes, {"S999"})


if __name__ == "__main__":
    unittest.main()

