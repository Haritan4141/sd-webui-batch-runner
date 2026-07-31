import unittest
from unittest.mock import patch
from urllib.error import URLError

from sd_webui_batch.client import (
    SdWebuiClient,
    SdWebuiTimeoutError,
    SdWebuiTransportError,
)


class ClientErrorTests(unittest.TestCase):
    @patch("sd_webui_batch.client.urlopen", side_effect=TimeoutError("timed out"))
    def test_wraps_direct_socket_timeout(self, _urlopen):
        client = SdWebuiClient(timeout=0.01)

        with self.assertRaises(SdWebuiTimeoutError):
            client.txt2img({})

    @patch(
        "sd_webui_batch.client.urlopen",
        side_effect=URLError(TimeoutError("timed out")),
    )
    def test_wraps_urlerror_timeout(self, _urlopen):
        client = SdWebuiClient(timeout=0.01)

        with self.assertRaises(SdWebuiTimeoutError):
            client.txt2img({})

    @patch(
        "sd_webui_batch.client.urlopen",
        side_effect=ConnectionResetError("connection reset"),
    )
    def test_wraps_interrupted_connection_as_unknown_state(self, _urlopen):
        client = SdWebuiClient()

        with self.assertRaises(SdWebuiTransportError):
            client.txt2img({})


if __name__ == "__main__":
    unittest.main()
