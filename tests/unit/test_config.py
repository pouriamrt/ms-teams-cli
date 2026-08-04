from pathlib import Path

import pytest

from teams_cli.config import CACHE_DIR, CONFIG_DIR, CREDENTIALS_PATH, paths_for


def test_default_paths_are_under_home() -> None:
    assert Path.home() / ".config" / "teams-cli" == CONFIG_DIR
    assert Path.home() / ".cache" / "teams-cli" == CACHE_DIR
    assert CREDENTIALS_PATH == CONFIG_DIR / "credentials.json"


def test_paths_for_returns_per_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEAMS_CLI_HOME", str(tmp_path))
    p = paths_for()
    assert p.config_dir == tmp_path / ".config" / "teams-cli"
    assert p.credentials == tmp_path / ".config" / "teams-cli" / "credentials.json"
    assert p.cache_dir == tmp_path / ".cache" / "teams-cli"


def test_http_verify_insecure_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from teams_cli.config import http_verify

    monkeypatch.setenv("TEAMS_CLI_INSECURE", "1")
    assert http_verify() is False


def test_http_verify_ca_bundle_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from teams_cli.config import http_verify

    bundle = tmp_path / "bundle.pem"
    bundle.write_text("placeholder")
    monkeypatch.setenv("TEAMS_CLI_INSECURE", "")
    monkeypatch.setenv("TEAMS_CLI_CA_BUNDLE", str(bundle))
    assert http_verify() == str(bundle)


def test_http_verify_uses_truststore_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    from teams_cli.config import http_verify

    monkeypatch.delenv("TEAMS_CLI_INSECURE", raising=False)
    monkeypatch.delenv("TEAMS_CLI_CA_BUNDLE", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    result = http_verify()
    # truststore is in our deps; should return an SSLContext.
    assert isinstance(result, ssl.SSLContext)
