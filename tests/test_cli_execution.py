import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from sd_webui_batch.cli import main
from sd_webui_batch.client import SdWebuiApiError, SdWebuiTransportError


class CliExecutionTests(unittest.TestCase):
    def _prompt_file(self, directory, job_count=1):
        path = Path(directory) / "prompts.txt"
        text = "\n\n".join(
            f"・job-{index}\nprompt-{index}" for index in range(1, job_count + 1)
        )
        path.write_text(text + "\n", encoding="utf-8")
        return path

    def _run_main(self, args, client):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("sd_webui_batch.cli.SdWebuiClient", return_value=client):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = main(args)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_sends_250_images_as_three_grid_free_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt_path = self._prompt_file(directory)
            client = Mock()
            client.txt2img.side_effect = [{}, {}, {}]

            result, stdout, _stderr = self._run_main(
                [str(prompt_path), "--batch-count", "250"],
                client,
            )

        payloads = [call.args[0] for call in client.txt2img.call_args_list]
        self.assertEqual(result, 0)
        self.assertEqual([payload["n_iter"] for payload in payloads], [100, 100, 50])
        self.assertTrue(
            all(not payload["override_settings"]["return_grid"] for payload in payloads)
        )
        self.assertTrue(
            all(not payload["override_settings"]["grid_save"] for payload in payloads)
        )
        self.assertIn("3 request(s)", stdout)

    def test_http_error_skips_remaining_chunks_and_runs_next_job(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt_path = self._prompt_file(directory, job_count=2)
            client = Mock()
            client.txt2img.side_effect = [SdWebuiApiError("HTTP 500"), {}, {}, {}]

            result, _stdout, stderr = self._run_main(
                [str(prompt_path), "--batch-count", "250"],
                client,
            )

        self.assertEqual(result, 1)
        self.assertEqual(client.txt2img.call_count, 4)
        self.assertIn("Skipping the remaining requests", stderr)

    def test_transport_error_stops_before_later_requests(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt_path = self._prompt_file(directory, job_count=2)
            client = Mock()
            client.txt2img.side_effect = SdWebuiTransportError("connection lost")

            result, _stdout, stderr = self._run_main(
                [str(prompt_path), "--batch-count", "250"],
                client,
            )

        self.assertEqual(result, 1)
        self.assertEqual(client.txt2img.call_count, 1)
        self.assertIn("may still be processing", stderr)


if __name__ == "__main__":
    unittest.main()
