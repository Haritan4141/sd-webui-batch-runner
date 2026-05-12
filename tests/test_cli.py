import json
import tempfile
import unittest
from pathlib import Path

from sd_webui_batch.cli import build_arg_parser, build_payload, load_payload_json
from sd_webui_batch.parser import PromptJob


class CliPayloadTests(unittest.TestCase):
    def test_uses_payload_n_iter_when_batch_count_is_omitted(self):
        args = build_arg_parser().parse_args(["prompts.txt"])
        job = PromptJob(index=1, title="タイトル", prompt="AAAA,", line_number=1)

        payload = build_payload(job, args, {"n_iter": 7})

        self.assertEqual(payload["n_iter"], 7)

    def test_defaults_n_iter_to_one(self):
        args = build_arg_parser().parse_args(["prompts.txt"])
        job = PromptJob(index=1, title="タイトル", prompt="AAAA,", line_number=1)

        payload = build_payload(job, args, {})

        self.assertEqual(payload["n_iter"], 1)

    def test_cli_batch_count_overrides_payload_n_iter(self):
        args = build_arg_parser().parse_args(["prompts.txt", "--batch-count", "3"])
        job = PromptJob(index=1, title="タイトル", prompt="AAAA,", line_number=1)

        payload = build_payload(job, args, {"n_iter": 7})

        self.assertEqual(payload["n_iter"], 3)

    def test_comment_fields_are_removed_from_payload_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "payload.json"
            path.write_text(
                json.dumps(
                    {
                        "n_iter": 1,
                        "_comment_n_iter": "生成枚数",
                        "override_settings": {
                            "CLIP_stop_at_last_layers": 2,
                            "_comment_clip_skip": "Clip Skip",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            payload = load_payload_json(path)

        self.assertEqual(payload["n_iter"], 1)
        self.assertNotIn("_comment_n_iter", payload)
        self.assertNotIn("_comment_clip_skip", payload["override_settings"])

    def test_hires_compatibility_defaults_are_added(self):
        args = build_arg_parser().parse_args(["prompts.txt"])
        job = PromptJob(index=1, title="タイトル", prompt="AAAA,", line_number=1)

        payload = build_payload(job, args, {"enable_hr": True, "cfg_scale": 5.5})

        self.assertEqual(payload["hr_cfg_scale"], 5.5)
        self.assertEqual(payload["hr_rescale_cfg"], 0.0)


if __name__ == "__main__":
    unittest.main()
