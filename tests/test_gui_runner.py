import queue
import threading
import unittest
from unittest.mock import Mock, patch

from sd_webui_batch.batching import split_payload_into_chunks
from sd_webui_batch.client import SdWebuiApiError, SdWebuiTransportError
from sd_webui_batch.gui import BatchRunnerApp, format_dry_run_request_lines
from sd_webui_batch.parser import PromptJob


class GuiRunnerTests(unittest.TestCase):
    def _make_app(self):
        app = BatchRunnerApp.__new__(BatchRunnerApp)
        app.stop_after_current = threading.Event()
        app.interrupt_requested = threading.Event()
        app.skip_requested = threading.Event()
        app.control_in_flight = threading.Event()
        app.control_finished = threading.Event()
        app.control_finished.set()
        app.progress_poll_warning_sent = threading.Event()
        app.events = queue.Queue()
        app.generation_running = False
        app.webui_controls_enabled = False
        return app

    def _plan(self, index, n_iter):
        job = PromptJob(
            index=index,
            title=f"job-{index}",
            prompt=f"prompt-{index}",
            line_number=index,
        )
        payload = {
            "n_iter": n_iter,
            "batch_size": 1,
            "override_settings": {
                "directories_filename_pattern": job.title,
                "grid_save": False,
                "return_grid": False,
            },
        }
        return job, split_payload_into_chunks(payload)

    def _run_with_clients(self, app, plans, generation_client, **kwargs):
        progress_client = kwargs.get("progress_client") or Mock()
        with patch(
            "sd_webui_batch.gui.SdWebuiClient",
            side_effect=[generation_client, progress_client],
        ):
            app._run_jobs(
                run_id=1,
                job_plans=plans,
                client_options={"base_url": "http://example", "timeout": 10},
                stop_on_error=kwargs.get("stop_on_error", False),
                dry_run=False,
            )

        events = []
        while True:
            try:
                events.append(app.events.get_nowait())
            except queue.Empty:
                break
        done = [value for event, value in events if event == "done"][-1]
        return events, done

    def test_sends_all_chunks_and_reports_confirmed_images(self):
        app = self._make_app()
        generation_client = Mock()
        generation_client.txt2img.side_effect = [{}, {}, {}]

        events, done = self._run_with_clients(
            app,
            [self._plan(1, 250)],
            generation_client,
        )

        sent_payloads = [call.args[0] for call in generation_client.txt2img.call_args_list]
        self.assertEqual([payload["n_iter"] for payload in sent_payloads], [100, 100, 50])
        self.assertEqual(done["outcome"], "completed")
        self.assertEqual(done["confirmed_images"], 250)
        completed_updates = [
            value
            for event, value in events
            if event == "run_progress" and value["phase"] == "completed"
        ]
        self.assertEqual(
            [update["confirmed_images"] for update in completed_updates],
            [100, 200, 250],
        )

    def test_large_dry_run_request_list_is_compacted(self):
        job = PromptJob(
            index=1,
            title="large-preview",
            prompt="prompt",
            line_number=1,
        )
        chunks = split_payload_into_chunks(
            {
                "n_iter": 100,
                "batch_size": 1,
                "seed": 1,
                "override_settings": {
                    "directories_filename_pattern": job.title,
                },
            },
            max_images_per_request=1,
        )

        lines = format_dry_run_request_lines(chunks)

        self.assertEqual(len(lines), 13)
        self.assertIn("88 request(s) omitted", lines[6])
        self.assertTrue(lines[0].startswith("request 1/100"))
        self.assertTrue(lines[-1].startswith("request 100/100"))

    def test_start_worker_runs_preparation_in_background(self):
        app = self._make_app()
        app.worker = None
        app.active_run_id = 0
        app.run_preparing = False
        app._selected_jobs = Mock(
            return_value=[
                PromptJob(
                    index=1,
                    title="background-plan",
                    prompt="prompt",
                    line_number=1,
                )
            ]
        )
        app._collect_base_payload = Mock(return_value={"n_iter": 100})
        app._build_cli_args = Mock(return_value=Mock())
        app._parse_timeout = Mock(return_value=10)
        app._set_running = Mock()
        app._clear_log = Mock()
        app._append_log = Mock()
        app.progress = Mock()
        app.status_var = Mock()
        app.dynamic_prompts_var = Mock(get=Mock(return_value=False))
        app.wildcards_dir_var = Mock(get=Mock(return_value=""))
        app.manifest_dir_var = Mock(get=Mock(return_value="manifests"))
        app.url_var = Mock(get=Mock(return_value="http://example"))
        app.username_var = Mock(get=Mock(return_value=""))
        app.password_var = Mock(get=Mock(return_value=""))
        app.stop_on_error_var = Mock(get=Mock(return_value=False))
        app.prompt_path_var = Mock(get=Mock(return_value="prompts.txt"))
        app.payload_path_var = Mock(get=Mock(return_value="payload.json"))

        started = threading.Event()
        release = threading.Event()
        ran_off_main_thread = []

        def slow_preparation(*_args):
            ran_off_main_thread.append(
                threading.current_thread() is not threading.main_thread()
            )
            started.set()
            release.wait(timeout=2)

        app._prepare_and_run_jobs = slow_preparation

        app._start_worker(dry_run=True)

        self.assertTrue(started.wait(timeout=0.5))
        self.assertTrue(app.worker.is_alive())
        self.assertEqual(ran_off_main_thread, [True])
        self.assertTrue(app.run_preparing)
        release.set()
        app.worker.join(timeout=1)
        self.assertFalse(app.worker.is_alive())

    def test_http_error_skips_remaining_chunks_but_starts_next_job(self):
        app = self._make_app()
        generation_client = Mock()
        generation_client.txt2img.side_effect = [SdWebuiApiError("HTTP 500"), {}]

        _events, done = self._run_with_clients(
            app,
            [self._plan(1, 250), self._plan(2, 1)],
            generation_client,
        )

        self.assertEqual(generation_client.txt2img.call_count, 2)
        self.assertEqual(done["outcome"], "completed_with_errors")
        self.assertEqual(done["failures"], 1)
        self.assertEqual(done["confirmed_images"], 1)

    def test_transport_error_stops_without_sending_later_requests(self):
        app = self._make_app()
        generation_client = Mock()
        generation_client.txt2img.side_effect = SdWebuiTransportError("connection lost")

        _events, done = self._run_with_clients(
            app,
            [self._plan(1, 250), self._plan(2, 1)],
            generation_client,
        )

        self.assertEqual(generation_client.txt2img.call_count, 1)
        self.assertEqual(done["outcome"], "failed")
        self.assertEqual(done["confirmed_images"], 0)

    def test_stop_request_waits_for_current_chunk_then_stops_cleanly(self):
        app = self._make_app()
        generation_client = Mock()

        def complete_then_request_stop(_payload):
            app.stop_after_current.set()
            return {}

        generation_client.txt2img.side_effect = complete_then_request_stop

        _events, done = self._run_with_clients(
            app,
            [self._plan(1, 250)],
            generation_client,
        )

        self.assertEqual(generation_client.txt2img.call_count, 1)
        self.assertEqual(done["outcome"], "stopped")
        self.assertEqual(done["confirmed_images"], 100)
        self.assertFalse(done["partial_images_possible"])

    def test_skip_does_not_count_chunk_as_confirmed_and_continues(self):
        app = self._make_app()
        generation_client = Mock()

        def skip_first_request(_payload):
            if generation_client.txt2img.call_count == 1:
                app.skip_requested.set()
            return {}

        generation_client.txt2img.side_effect = skip_first_request

        events, done = self._run_with_clients(
            app,
            [self._plan(1, 250)],
            generation_client,
        )

        self.assertEqual(generation_client.txt2img.call_count, 3)
        self.assertEqual(done["outcome"], "completed_with_skips")
        self.assertEqual(done["skipped_requests"], 1)
        self.assertEqual(done["confirmed_images"], 150)
        warning_logs = [
            value
            for event, value in events
            if event == "log" and "exact count is unknown" in value
        ]
        self.assertEqual(len(warning_logs), 1)

    def test_delayed_skip_control_cannot_hit_next_chunk(self):
        class DummyButton:
            def configure(self, **_kwargs):
                pass

        app = self._make_app()
        app.start_button = DummyButton()
        app.preview_button = DummyButton()
        app.stop_button = DummyButton()
        app.interrupt_button = DummyButton()
        app.skip_button = DummyButton()
        app.url_var = Mock(get=Mock(return_value="http://example"))
        app.username_var = Mock(get=Mock(return_value=""))
        app.password_var = Mock(get=Mock(return_value=""))
        app._append_log = Mock()
        app.generation_running = True
        app.webui_controls_enabled = True

        first_started = threading.Event()
        release_first = threading.Event()
        first_finished = threading.Event()
        second_started = threading.Event()
        generation_client = Mock()

        def generate(_payload):
            if generation_client.txt2img.call_count == 1:
                first_started.set()
                release_first.wait(timeout=1)
                first_finished.set()
            else:
                second_started.set()
            return {}

        generation_client.txt2img.side_effect = generate
        progress_client = Mock()

        control_started = threading.Event()
        release_control = threading.Event()
        control_client = Mock()

        def delayed_skip():
            control_started.set()
            release_control.wait(timeout=1)

        control_client.skip.side_effect = delayed_skip

        with patch(
            "sd_webui_batch.gui.SdWebuiClient",
            side_effect=[generation_client, progress_client, control_client],
        ):
            runner = threading.Thread(
                target=app._run_jobs,
                kwargs={
                    "run_id": 1,
                    "job_plans": [self._plan(1, 250)],
                    "client_options": {"base_url": "http://example", "timeout": 10},
                    "stop_on_error": False,
                    "dry_run": False,
                },
            )
            runner.start()
            self.assertTrue(first_started.wait(timeout=1))

            app.skip_webui()
            self.assertTrue(control_started.wait(timeout=1))
            release_first.set()
            self.assertTrue(first_finished.wait(timeout=1))

            # The first txt2img has returned, but the delayed /skip must finish
            # before request 2 can be submitted.
            self.assertFalse(second_started.wait(timeout=0.05))
            release_control.set()
            self.assertTrue(second_started.wait(timeout=1))
            runner.join(timeout=1)

        self.assertFalse(runner.is_alive())
        self.assertEqual(generation_client.txt2img.call_count, 3)

    def test_control_request_blocks_new_run_until_post_finishes(self):
        control_done_queued = threading.Event()

        class ControlQueue(queue.Queue):
            def put(self, item, block=True, timeout=None):
                super().put(item, block=block, timeout=timeout)
                if item[0] == "control_done":
                    control_done_queued.set()

        class DummyButton:
            def __init__(self):
                self.state = None

            def configure(self, **kwargs):
                if "state" in kwargs:
                    self.state = kwargs["state"]

        app = self._make_app()
        app.events = ControlQueue()
        app.start_button = DummyButton()
        app.preview_button = DummyButton()
        app.stop_button = DummyButton()
        app.interrupt_button = DummyButton()
        app.skip_button = DummyButton()
        app.url_var = Mock(get=Mock(return_value="http://example"))
        app.username_var = Mock(get=Mock(return_value=""))
        app.password_var = Mock(get=Mock(return_value=""))
        app._append_log = Mock()
        app.root = Mock()
        app.worker = None

        control_started = threading.Event()
        release_control = threading.Event()
        control_finished = threading.Event()
        control_client = Mock()

        def blocking_interrupt():
            control_started.set()
            release_control.wait(timeout=1)
            control_finished.set()

        control_client.interrupt.side_effect = blocking_interrupt
        app.webui_controls_enabled = True
        app._set_running(True)

        with patch("sd_webui_batch.gui.SdWebuiClient", return_value=control_client):
            app.interrupt_webui()
            self.assertTrue(control_started.wait(timeout=1))
            self.assertTrue(app.control_in_flight.is_set())
            self.assertEqual(app.start_button.state, "disabled")
            self.assertEqual(app.interrupt_button.state, "disabled")

            # Simulate the generation worker finishing before the control POST.
            app._set_running(False)
            with patch("sd_webui_batch.gui.messagebox.showinfo") as showinfo:
                app._start_worker(dry_run=False)
            showinfo.assert_called_once()
            self.assertEqual(app.start_button.state, "disabled")

            release_control.set()
            self.assertTrue(control_finished.wait(timeout=1))
            self.assertTrue(control_done_queued.wait(timeout=1))

        app._drain_events()
        self.assertFalse(app.control_in_flight.is_set())
        self.assertEqual(app.start_button.state, "normal")
        self.assertEqual(app.interrupt_button.state, "disabled")
        self.assertEqual(app.skip_button.state, "disabled")

    def test_webui_controls_are_disabled_for_dry_run(self):
        class DummyButton:
            def __init__(self):
                self.state = None

            def configure(self, **kwargs):
                if "state" in kwargs:
                    self.state = kwargs["state"]

        app = self._make_app()
        app.start_button = DummyButton()
        app.preview_button = DummyButton()
        app.stop_button = DummyButton()
        app.interrupt_button = DummyButton()
        app.skip_button = DummyButton()
        app.webui_controls_enabled = False

        app._set_running(True)

        self.assertEqual(app.start_button.state, "disabled")
        self.assertEqual(app.stop_button.state, "normal")
        self.assertEqual(app.interrupt_button.state, "disabled")
        self.assertEqual(app.skip_button.state, "disabled")

    def test_polls_webui_progress_while_request_is_running(self):
        app = self._make_app()
        poll_attempted = threading.Event()
        generation_client = Mock()

        def wait_for_progress_poll(_payload):
            self.assertTrue(poll_attempted.wait(timeout=1))
            return {}

        generation_client.txt2img.side_effect = wait_for_progress_poll
        progress_client = Mock()

        def report_progress(**_kwargs):
            poll_attempted.set()
            return {
                "progress": 0.42,
                "eta_relative": 12.4,
            }

        progress_client.get_progress.side_effect = report_progress

        with patch("sd_webui_batch.gui.PROGRESS_POLL_INTERVAL_SECONDS", 0.005):
            events, done = self._run_with_clients(
                app,
                [self._plan(1, 100)],
                generation_client,
                progress_client=progress_client,
            )

        polling_updates = [
            value
            for event, value in events
            if event == "run_progress" and value["phase"] == "polling"
        ]
        self.assertTrue(polling_updates)
        self.assertEqual(polling_updates[-1]["webui_progress"], 0.42)
        self.assertEqual(polling_updates[-1]["eta_relative"], 12.4)
        self.assertEqual(done["outcome"], "completed")

    def test_progress_poll_failure_is_nonfatal_and_logged_once_per_run(self):
        app = self._make_app()
        poll_attempted = threading.Event()
        generation_client = Mock()

        def wait_for_failed_poll(_payload):
            self.assertTrue(poll_attempted.wait(timeout=1))
            return {}

        generation_client.txt2img.side_effect = wait_for_failed_poll
        progress_client = Mock()

        def fail_progress_poll(**_kwargs):
            poll_attempted.set()
            raise SdWebuiTransportError("poll failed")

        progress_client.get_progress.side_effect = fail_progress_poll

        with patch("sd_webui_batch.gui.PROGRESS_POLL_INTERVAL_SECONDS", 0.003):
            events, done = self._run_with_clients(
                app,
                [self._plan(1, 200)],
                generation_client,
                progress_client=progress_client,
            )

        warning_logs = [
            value
            for event, value in events
            if event == "log" and "progress polling unavailable" in value
        ]
        self.assertEqual(len(warning_logs), 1)
        self.assertEqual(done["outcome"], "completed")
        self.assertEqual(done["confirmed_images"], 200)


if __name__ == "__main__":
    unittest.main()
