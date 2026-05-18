"""
network_executor.py
-------------------
HTTP client for applying network changes in the Mininet simulator pod.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict


class NetworkExecutor:
    def __init__(self, api_url: str, timeout_seconds: int = 12) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def health(self) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(
                f"{self.api_url}/health", timeout=self.timeout_seconds
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # pragma: no cover
            return {"status": "error", "error": str(e)}

    def topology(self) -> Dict[str, Any]:
        try:
            with urllib.request.urlopen(
                f"{self.api_url}/topology", timeout=self.timeout_seconds
            ) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # pragma: no cover
            return {"status": "error", "error": str(e)}

    def implement(self, request_text: str) -> Dict[str, Any]:
        payload = json.dumps({"request": request_text}).encode("utf-8")
        req = urllib.request.Request(
            url=f"{self.api_url}/implement",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:  # pragma: no cover
            body = e.read().decode("utf-8", errors="replace")
            return {
                "implemented": False,
                "error": f"HTTP {e.code}",
                "details": body,
            }
        except Exception as e:  # pragma: no cover
            return {
                "implemented": False,
                "error": "network_executor_unavailable",
                "details": str(e),
            }
