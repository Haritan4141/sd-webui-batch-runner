from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


class SdWebuiApiError(RuntimeError):
    """Raised when Stable Diffusion WebUI API returns an error."""


@dataclass
class SdWebuiClient:
    base_url: str = "http://127.0.0.1:7860"
    timeout: float | None = 86400
    username: str | None = None
    password: str | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/") + "/"

    def get_progress(self, skip_current_image: bool = True) -> dict[str, Any]:
        query = "?skip_current_image=true" if skip_current_image else ""
        return self._request_json("GET", f"sdapi/v1/progress{query}")

    def txt2img(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json("POST", "sdapi/v1/txt2img", payload)

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = urljoin(self.base_url, path)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}

        if body is not None:
            headers["Content-Type"] = "application/json"

        if self.username is not None or self.password is not None:
            token = f"{self.username or ''}:{self.password or ''}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")

        request = Request(url, data=body, headers=headers, method=method)

        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise SdWebuiApiError(f"HTTP {error.code} from {url}: {detail}") from error
        except URLError as error:
            raise SdWebuiApiError(f"Could not connect to {url}: {error.reason}") from error

        if not raw:
            return {}

        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise SdWebuiApiError(f"Invalid JSON response from {url}: {raw[:500]}") from error
