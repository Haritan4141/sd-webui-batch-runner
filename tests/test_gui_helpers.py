import math
import unittest

from sd_webui_batch.gui import (
    format_eta,
    format_progress_status,
    normalize_eta,
    normalize_webui_progress,
)


class GuiProgressHelperTests(unittest.TestCase):
    def test_normalizes_progress_without_allowing_invalid_values(self):
        self.assertEqual(normalize_webui_progress(-0.2), 0.0)
        self.assertEqual(normalize_webui_progress(1.2), 1.0)
        self.assertEqual(normalize_webui_progress("0.42"), 0.42)
        self.assertIsNone(normalize_webui_progress(True))
        self.assertIsNone(normalize_webui_progress(math.nan))
        self.assertIsNone(normalize_webui_progress("invalid"))

    def test_formats_eta_and_rejects_invalid_values(self):
        self.assertEqual(format_eta(151.4), "00:02:31")
        self.assertEqual(format_eta(3661), "01:01:01")
        self.assertIsNone(normalize_eta(-1))
        self.assertIsNone(format_eta(math.inf))

    def test_formats_clear_chunk_progress_status(self):
        status = format_progress_status(
            {
                "phase": "polling",
                "job_number": 2,
                "job_total": 4,
                "chunk_number": 17,
                "chunk_total": 60,
                "confirmed_images": 7600,
                "total_images": 24000,
                "webui_progress": 0.632,
                "eta_relative": 151.4,
            }
        )

        self.assertEqual(
            status,
            "ジョブ 2/4｜送信 17/60｜確定 7600/24000枚｜WebUI 63.2%｜現送信ETA 00:02:31",
        )

    def test_formats_skipped_request_as_unknown_saved_count(self):
        status = format_progress_status(
            {
                "phase": "skipped",
                "job_number": 1,
                "job_total": 1,
                "chunk_number": 2,
                "chunk_total": 3,
                "confirmed_images": 100,
                "total_images": 250,
                "webui_progress": None,
                "eta_relative": None,
            }
        )

        self.assertEqual(
            status,
            "ジョブ 1/1｜送信 2/3｜確定 100/250枚｜スキップ（保存枚数不明）",
        )


if __name__ == "__main__":
    unittest.main()
