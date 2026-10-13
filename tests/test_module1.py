import unittest

from module1 import split_script, validate_blocks


class SplitScriptTests(unittest.TestCase):
    def test_splits_japanese_sentences_and_assigns_stable_ids(self):
        blocks = split_script("江戸の町が揺れました。 人々は逃げます！\n\n夜が来ました。")
        self.assertEqual([block["block_id"] for block in blocks], ["S001", "S002", "S003"])
        self.assertEqual(blocks[0]["jp_text"], "江戸の町が揺れました。")
        self.assertEqual(blocks[1]["jp_text"], "人々は逃げます！")
        self.assertTrue(all(block["vn_check"] == "" for block in blocks))
        self.assertTrue(all(block["scene_ids"] == [] for block in blocks))
        self.assertTrue(all(block["audio_file"] is None for block in blocks))
        self.assertTrue(all(block["audio_assets"] == [] for block in blocks))
        self.assertTrue(all(block["duration"] is None for block in blocks))

    def test_paragraph_mode_preserves_sentences_together(self):
        blocks = split_script("最初です。次です。\n\n最後です。", "paragraphs")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]["jp_text"], "最初です。次です。")

    def test_empty_script_returns_no_blocks(self):
        self.assertEqual(split_script(" \n "), [])


class ValidateBlockTests(unittest.TestCase):
    def test_normalizes_valid_blocks(self):
        blocks = validate_blocks([{"block_id": "S001", "jp_text": " 日本語 ", "vn_check": " nghĩa "}])
        self.assertEqual(blocks[0]["jp_text"], "日本語")
        self.assertEqual(blocks[0]["vn_check"], "nghĩa")

    def test_old_schema_receives_new_storage_fields(self):
        blocks = validate_blocks([{"block_id": "S001", "jp_text": "日本語", "vn_check": ""}])
        self.assertEqual(blocks[0]["scene_ids"], [])
        self.assertIsNone(blocks[0]["audio_file"])
        self.assertEqual(blocks[0]["audio_assets"], [])
        self.assertIsNone(blocks[0]["duration"])

    def test_normalizes_audio_fields_and_migrates_selected_file_into_assets(self):
        block = validate_blocks([{
            "block_id": "S001", "jp_text": "日本語", "vn_check": "",
            "audio_file": "audio/S001_01.wav", "duration": 1.23456,
        }])[0]
        self.assertEqual(block["audio_assets"], ["audio/S001_01.wav"])
        self.assertEqual(block["duration"], 1.235)

    def test_rejects_audio_outside_project_audio_directory(self):
        with self.assertRaisesRegex(ValueError, "audio"):
            validate_blocks([{
                "block_id": "S001", "jp_text": "日本語", "vn_check": "",
                "audio_assets": ["../secret.wav"],
            }])

    def test_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_blocks([
                {"block_id": "S001", "jp_text": "一。", "vn_check": ""},
                {"block_id": "S001", "jp_text": "二。", "vn_check": ""},
            ])

    def test_rejects_blank_japanese_source(self):
        with self.assertRaisesRegex(ValueError, "requires jp_text"):
            validate_blocks([{"block_id": "S001", "jp_text": " ", "vn_check": ""}])


if __name__ == "__main__":
    unittest.main()

