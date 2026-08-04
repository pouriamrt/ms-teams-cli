"""Path conventions and config-file IO."""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from pathlib import Path


def _home() -> Path:
    override = os.environ.get("TEAMS_CLI_HOME")
    return Path(override) if override else Path.home()


@dataclass(frozen=True)
class Paths:
    config_dir: Path
    cache_dir: Path
    credentials: Path
    access_tokens: Path
    skype_token: Path
    last_chat_listing: Path
    last_message_listing: Path
    people_cache: Path
    log_file: Path


def paths_for() -> Paths:
    home = _home()
    config_dir = home / ".config" / "teams-cli"
    cache_dir = home / ".cache" / "teams-cli"
    return Paths(
        config_dir=config_dir,
        cache_dir=cache_dir,
        credentials=config_dir / "credentials.json",
        access_tokens=cache_dir / "access_tokens.json",
        skype_token=cache_dir / "skype_token.json",
        last_chat_listing=cache_dir / "last_chat_listing.json",
        last_message_listing=cache_dir / "last_message_listing.json",
        people_cache=cache_dir / "people.json",
        log_file=cache_dir / "cli.log",
    )


def teams_origin() -> str:
    """Origin header for AAD token-endpoint requests.

    Required to satisfy AADSTS9002327 (SPA RT redemption must look cross-origin).
    The captured RT was minted at https://teams.microsoft.com/v2/.
    """
    return os.environ.get("TEAMS_CLI_ORIGIN", "https://teams.microsoft.com")


def http_verify() -> bool | ssl.SSLContext | str:
    """Resolve the TLS verification setting for outgoing HTTPS requests.

    Resolution order:
      1. ``TEAMS_CLI_INSECURE=1`` -> disable verification entirely.
      2. ``TEAMS_CLI_CA_BUNDLE`` / ``REQUESTS_CA_BUNDLE`` / ``SSL_CERT_FILE``
         -> use that file/dir as the trust store (handles corporate MITM proxies).
      3. ``truststore`` -> use the OS trust store (picks up enterprise roots
         installed in Windows/macOS cert stores).
      4. ``certifi`` -> fall back to certifi's bundled CA list.
      5. Default ``True`` -> use whatever ssl considers the system store.
    """
    if os.environ.get("TEAMS_CLI_INSECURE", "").lower() in ("1", "true", "yes"):
        return False
    for var in ("TEAMS_CLI_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        bundle = os.environ.get(var)
        if bundle:
            return bundle
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except ImportError:
        pass
    try:
        import certifi

        return certifi.where()
    except ImportError:
        return True


# Module-level conveniences for the common case.
_DEFAULT = paths_for()
CONFIG_DIR = _DEFAULT.config_dir
CACHE_DIR = _DEFAULT.cache_dir
CREDENTIALS_PATH = _DEFAULT.credentials
