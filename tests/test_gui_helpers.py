import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sd_webui_batch.gui import (
    calculate_job_plan_counts,
    format_eta,
    format_progress_status,
    normalize_eta,
    normalize_webui_progress,
)
from sd_webui_batch.library_gui import (
    DEFAULT_PROMPTSET_DISTRIBUTION_DIR,
    DEFAULT_REQUESTSET_EXPORT_DIR,
    PromptLibraryWindow,
    PROMPT_STATUS_LABELS,
    PROMPT_STATUS_VALUES,
    REQUEST_FILTER_CHOICES,
    REQUEST_STATUS_LABELS,
    REQUEST_STATUS_VALUES,
    prompt_records_for_display,
    ready_job_ids_for_display,
    request_status_matches_filter,
)
from sd_webui_batch.prompt_library import REQUEST_STATUSES


class GuiProgressHelperTests(unittest.TestCase):
    def test_requestset_export_uses_every_visible_request_without_selection(self):
        window = PromptLibraryWindow.__new__(PromptLibraryWindow)
        window.request_tree = Mock()
        window.request_tree.get_children.return_value = ("3", "1", "2")
        window.current_request_id = None
        window.library = Mock()
        window.library.path = Path("library.sqlite3")
        window.window = Mock()
        window.reload = Mock()

        with patch(
            "sd_webui_batch.library_gui.filedialog.asksaveasfilename",
            return_value="RequestSet.json",
        ), patch("sd_webui_batch.library_gui.messagebox.showinfo"):
            window.export_visible_request_set()

        window.library.export_request_set.assert_called_once_with(
            "RequestSet.json", (3, 1, 2)
        )

    def test_requestset_export_folder_uses_generator_project(self):
        self.assertEqual(
            DEFAULT_REQUESTSET_EXPORT_DIR,
            Path.home() / "Documents" / "sd-webui-prompt-codex-generate",
        )

    def test_promptset_distribution_folder_is_inside_the_project(self):
        project_root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            DEFAULT_PROMPTSET_DISTRIBUTION_DIR,
            project_root / "SD-PromptSets",
        )

    def test_ready_batch_action_uses_only_visible_prompt_rows(self):
        jobs = {
            1: SimpleNamespace(enabled=True, status="ready"),
            2: SimpleNamespace(enabled=True, status="draft"),
            3: SimpleNamespace(enabled=True, status="ready"),
            4: SimpleNamespace(enabled=False, status="ready"),
        }

        self.assertEqual(
            ready_job_ids_for_display(jobs, (1, 2, 4)),
            (1,),
        )

    def test_prompt_display_can_limit_rows_to_the_open_collection(self):
        records = [
            SimpleNamespace(id=1, collection_id=10),
            SimpleNamespace(id=2, collection_id=10),
            SimpleNamespace(id=3, collection_id=20),
        ]

        current = prompt_records_for_display(
            records,
            current_collection_id=10,
            current_only=True,
        )
        all_records = prompt_records_for_display(
            records,
            current_collection_id=10,
            current_only=False,
        )

        self.assertEqual([record.id for record in current], [1, 2])
        self.assertEqual([record.id for record in all_records], [1, 2, 3])

    def test_library_statuses_have_reversible_japanese_labels(self):
        self.assertEqual(set(REQUEST_STATUS_LABELS), set(REQUEST_STATUSES))
        self.assertEqual(
            {
                REQUEST_STATUS_VALUES[label]
                for label in REQUEST_STATUS_LABELS.values()
            },
            set(REQUEST_STATUSES),
        )
        self.assertEqual(PROMPT_STATUS_VALUES["下書き"], "draft")
        self.assertEqual(PROMPT_STATUS_VALUES["生成準備済み"], "ready")
        self.assertEqual(PROMPT_STATUS_VALUES["生成済み"], "generated")
        self.assertEqual(set(PROMPT_STATUS_LABELS), {"draft", "ready", "generated"})

    def test_request_status_filter_hides_completed_by_default(self):
        self.assertEqual(REQUEST_FILTER_CHOICES[0], "未完了")
        self.assertTrue(request_status_matches_filter("received", "未完了"))
        self.assertTrue(
            request_status_matches_filter("prompt_generated", "未完了")
        )
        self.assertFalse(request_status_matches_filter("done", "未完了"))
        self.assertTrue(request_status_matches_filter("done", "完了"))
        self.assertFalse(request_status_matches_filter("received", "完了"))
        self.assertTrue(request_status_matches_filter("done", "すべて"))

    def test_dynamic_preview_counts_each_output_as_a_b1_request(self):
        self.assertEqual(
            calculate_job_plan_counts(1, 2, dynamic_prompts=True),
            (2, 2),
        )
        self.assertEqual(
            calculate_job_plan_counts(3, 4, dynamic_prompts=True),
            (12, 12),
        )

    def test_regular_preview_keeps_gpu_batching(self):
        self.assertEqual(
            calculate_job_plan_counts(50, 2, dynamic_prompts=False),
            (100, 1),
        )

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
