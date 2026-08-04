from __future__ import annotations

import io
import logging

import pytest

from teams_cli.render.redact import RedactingFilter, redact


def test_redacts_bearer_jwt_in_string() -> None:
    s = "header Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.aaa.bbb here"
    out = redact(s)
    assert "Bearer <REDACTED>" in out
    assert "eyJ0eXAi" not in out


def test_redacts_skypetoken() -> None:
    s = "Authentication: skypetoken=eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.aaa.bbb done"
    out = redact(s)
    assert "skypetoken=<REDACTED>" in out
    assert "eyJ0eXAi" not in out


def test_redacts_refresh_token_field_in_json() -> None:
    s = '{"refresh_token": "1.AXYA-SECRET-RT-VALUE", "access_token": "AT"}'
    out = redact(s)
    assert "<REDACTED>" in out
    assert "AXYA-SECRET" not in out


def test_redacts_access_token_field() -> None:
    s = '"access_token":"abcdefghijklmnopqrstuvwxyz1234567890"'
    out = redact(s)
    assert "abcdef" not in out


def test_handles_unicode_and_empty() -> None:
    assert redact("") == ""
    assert redact("héllo wörld") == "héllo wörld"


def test_filter_redacts_log_records(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test_redact_logger")
    logger.handlers = []
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("Sending request with Bearer eyJabc.def.ghi.jklmnopqrstuv")
    out = stream.getvalue()
    assert "Bearer <REDACTED>" in out
    assert "eyJabc" not in out


def test_filter_redacts_args_in_record(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("test_redact_logger_args")
    logger.handlers = []
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("token=%s", "skypetoken=eyJabc.def.ghi.jklmnopqrstuv")
    out = stream.getvalue()
    assert "skypetoken=<REDACTED>" in out
