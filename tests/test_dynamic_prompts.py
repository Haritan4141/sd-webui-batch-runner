import json
import random
import tempfile
import unittest
from pathlib import Path

from sd_webui_batch.dynamic_prompts import (
    DynamicPromptError,
    DynamicPromptExpander,
    plan_dynamic_prompt_chunks,
    write_dynamic_manifest,
)


class DynamicPromptTests(unittest.TestCase):
    def test_expands_inline_nested_wildcard_and_lora_text(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "character.txt").write_text(
                "alice, <lora:alice:0.8>\nbob, <lora:bob:0.6>\n",
                encoding="utf-8",
            )
            expander = DynamicPromptExpander([root])

            result = expander.expand("{red|blue} hair, __character__", random.Random(7))

        self.assertNotIn("{", result.text)
        self.assertNotIn("__", result.text)
        self.assertIn("<lora:", result.text)
        self.assertEqual(len(result.choices), 2)

    def test_nested_wildcard_paths_are_supported(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "people"
            nested.mkdir()
            (nested / "character.txt").write_text("alice\n", encoding="utf-8")
            expander = DynamicPromptExpander([root])

            result = expander.expand("__people/character__", random.Random(1))

        self.assertEqual(result.text, "alice")

    def test_missing_wildcard_is_an_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            expander = DynamicPromptExpander([temp_dir])
            with self.assertRaises(DynamicPromptError):
                expander.expand("__missing__", random.Random(1))

    def test_planning_converts_batch_to_one_deterministic_request_per_image(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "pose.txt").write_text("standing\nsitting\n", encoding="utf-8")
            expander = DynamicPromptExpander([root])
            payload = {
                "prompt": "__pose__, {red|blue} hair",
                "negative_prompt": "{bad|worse}",
                "n_iter": 2,
                "batch_size": 2,
                "seed": 100,
            }

            chunks, records = plan_dynamic_prompt_chunks(
                payload,
                expander,
                job_index=1,
                job_title="test",
            )

        self.assertEqual(len(chunks), 4)
        self.assertEqual([chunk.payload["seed"] for chunk in chunks], [100, 101, 102, 103])
        self.assertTrue(all(chunk.payload["n_iter"] == 1 for chunk in chunks))
        self.assertTrue(all(chunk.payload["batch_size"] == 1 for chunk in chunks))
        self.assertEqual(len(records), 4)

    def test_manifest_contains_resolved_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_dynamic_manifest(
                temp_dir,
                [{"resolved_prompt": "alice"}],
                metadata={"url": "http://127.0.0.1:7861"},
            )
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["records"][0]["resolved_prompt"], "alice")
        self.assertEqual(document["metadata"]["url"], "http://127.0.0.1:7861")


if __name__ == "__main__":
    unittest.main()
