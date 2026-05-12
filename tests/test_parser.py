import unittest

from sd_webui_batch.cli import sanitize_subdirectory
from sd_webui_batch.parser import parse_prompt_note


class ParsePromptNoteTests(unittest.TestCase):
    def test_parses_variable_prompt_start_lines(self):
        text = """
・タイトル1：ノーマル
AAAA,
masterpiece, best quality, amazing quality,


・タイトル2：ヌルテカ
BBBB,
masterpiece, best quality, amazing quality,


・タイトル3：ヌルテカ
CCCC,
masterpiece, best quality, amazing quality,
"""

        jobs = parse_prompt_note(text)

        self.assertEqual([job.title for job in jobs], ["タイトル1：ノーマル", "タイトル2：ヌルテカ", "タイトル3：ヌルテカ"])
        self.assertEqual(jobs[0].prompt, "AAAA,\nmasterpiece, best quality, amazing quality,")
        self.assertEqual(jobs[1].prompt, "BBBB,\nmasterpiece, best quality, amazing quality,")
        self.assertEqual(jobs[2].subdirectory, "タイトル3：ヌルテカ")

    def test_keeps_tilde_lines_as_prompt_text(self):
        text = """
・タイトル1
AAAA,
~~~~~~
still prompt text
"""

        jobs = parse_prompt_note(text)

        self.assertEqual(jobs[0].prompt, "AAAA,\n~~~~~~\nstill prompt text")

    def test_sanitizes_only_filesystem_invalid_characters(self):
        self.assertEqual(sanitize_subdirectory("title:normal/01"), "title_normal_01")
        self.assertEqual(sanitize_subdirectory("タイトル1：ノーマル"), "タイトル1：ノーマル")


if __name__ == "__main__":
    unittest.main()
