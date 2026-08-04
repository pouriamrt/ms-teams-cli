"""Shared pytest fixtures and VCR config."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def load_fixture() -> Callable[[str], dict[str, Any]]:
    def _load(name: str) -> dict[str, Any]:
        data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        return data

    return _load


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    def scrub_response(response: dict[str, Any]) -> dict[str, Any]:
        # Only scrub textual bodies; skip binary responses to avoid corruption.
        headers = response.get("headers", {})
        content_type_values = headers.get("Content-Type") or headers.get("content-type") or [""]
        content_type = content_type_values[0].lower() if content_type_values else ""
        if "application/json" not in content_type and not content_type.startswith("text/"):
            return response

        body = response.get("body", {}).get("string")
        if isinstance(body, bytes):
            text = body.decode("utf-8", errors="replace")
        elif isinstance(body, str):
            text = body
        else:
            return response
        text = re.sub(r'("access_token"\s*:\s*")[^"]+', r"\1<REDACTED>", text)
        text = re.sub(r'("refresh_token"\s*:\s*")[^"]+', r"\1<REDACTED>", text)
        text = re.sub(r'("skypeToken"\s*:\s*")[^"]+', r"\1<REDACTED>", text)
        text = re.sub(r'("id_token"\s*:\s*")[^"]+', r"\1<REDACTED>", text)
        response["body"]["string"] = text.encode("utf-8")
        return response

    return {
        "filter_headers": [
            ("authorization", "Bearer <REDACTED>"),
            ("authentication", "skypetoken=<REDACTED>"),
            ("cookie", "<REDACTED>"),
            ("set-cookie", "<REDACTED>"),
        ],
        "before_record_response": scrub_response,
        "cassette_library_dir": "tests/integration/cassettes",
        "record_mode": "once",
    }
