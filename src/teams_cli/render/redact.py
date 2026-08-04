"""Redact bearer and Skype tokens before they hit log files or cassettes."""

from __future__ import annotations

import logging
import re

_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]{16,}", re.IGNORECASE)
_SKYPE = re.compile(r"skypetoken\s*=\s*[A-Za-z0-9._\-+/=]{16,}", re.IGNORECASE)
_AT_FIELD = re.compile(r'("access_token"\s*:\s*)"[^"]+"')
_RT_FIELD = re.compile(r'("refresh_token"\s*:\s*)"[^"]+"')
_ID_FIELD = re.compile(r'("id_token"\s*:\s*)"[^"]+"')
_SKYPE_FIELD = re.compile(r'("skypeToken"\s*:\s*)"[^"]+"')


def redact(text: str) -> str:
    """Replace tokens in a string with `<REDACTED>`. Safe to call on any text."""
    if not text:
        return text
    text = _BEARER.sub("Bearer <REDACTED>", text)
    text = _SKYPE.sub("skypetoken=<REDACTED>", text)
    text = _AT_FIELD.sub(r'\1"<REDACTED>"', text)
    text = _RT_FIELD.sub(r'\1"<REDACTED>"', text)
    text = _ID_FIELD.sub(r'\1"<REDACTED>"', text)
    text = _SKYPE_FIELD.sub(r'\1"<REDACTED>"', text)
    return text


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs token-like substrings from records."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (redact(v) if isinstance(v, str) else v) for k, v in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        except Exception:  # never break logging
            pass
        return True
