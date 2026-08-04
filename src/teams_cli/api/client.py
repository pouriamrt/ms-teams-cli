"""HTTP client wrapper for Graph (Bearer) and chatsvc (Skype token)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from teams_cli import __version__
from teams_cli.config import http_verify

log = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
CHATSVC_AMER_BASE = "https://teams.microsoft.com/api/chatsvc/ca/v1"
USER_AGENT = f"teams-cli/{__version__} httpx"

# chatsvc gates write operations on a recognized client identity. A valid
# token alone yields 200-but-no-op; these headers replicate exactly what the
# Teams web client sends so the server actually applies the mutation.
# clientVer is pinned to the captured Teams-web build (captured 2026-05-26).
# If MS rejects old builds, the failure is a silent no-op caught by the
# read-back verification (verified=false) — recapture clientVer from a fresh
# Teams-web request and bump it here. TODO: revisit if mark-* starts failing.
_CHATSVC_CLIENT_INFO = (
    "os=windows; osVer=NT 10.0; proc=x86; lcid=en-us; deviceType=1; "
    "country=us; clientName=skypeteams; clientVer=1415/26043019216; "
    "utcOffset=+00:00; timezone=UTC"
)
# Teams web sends a browser UA + a worker Referer on these writes; some chatsvc
# routes fingerprint these to distinguish a real client from a bot.
_CHATSVC_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
_CHATSVC_REFERER = "https://teams.microsoft.com/v2/"


class ApiClient:
    """Thin wrapper over httpx that injects the right credential per surface.

    - graph_*  -> Authorization: Bearer <graph-AT>
    - teams_*  -> Authorization: Bearer <teams-AT> (for teams.microsoft.com/api/* not under chatsvc)
    - chatsvc_*-> Authentication: skypetoken=<jwt>   (NOT a Bearer header)
    """

    def __init__(
        self,
        get_graph_token: Callable[[], str],
        get_teams_token: Callable[[], str],
        get_skype_token: Callable[[], str],
        get_ic3_token: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._get_graph = get_graph_token
        self._get_teams = get_teams_token
        self._get_skype = get_skype_token
        self._get_ic3 = get_ic3_token
        self._client = client or httpx.Client(timeout=30.0, verify=http_verify())

    # ----- graph -----

    def graph_get(self, path: str, *, params: dict[str, Any] | None = None) -> httpx.Response:
        return self._client.get(
            self._url(GRAPH_BASE, path),
            params=params,
            headers=self._bearer_headers(self._get_graph()),
        )

    def graph_post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return self._client.post(
            self._url(GRAPH_BASE, path),
            json=json,
            params=params,
            headers=self._bearer_headers(self._get_graph(), with_json=True),
        )

    def graph_delete(self, path: str) -> httpx.Response:
        return self._client.delete(
            self._url(GRAPH_BASE, path),
            headers=self._bearer_headers(self._get_graph()),
        )

    # ----- chatsvc -----

    def _chatsvc_headers(self, *, with_json: bool = False) -> dict[str, str]:
        h = {
            "Authentication": f"skypetoken={self._get_skype()}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if with_json:
            h["Content-Type"] = "application/json"
        return h

    def _ic3_headers(self, *, with_json: bool = False) -> dict[str, str]:
        """Headers replicating a Teams-web chatsvc request authenticated with an
        IC3 Bearer token. The clientinfo/x-ms-*/Referer/UA set is what makes
        write mutations actually apply (a request missing them is accepted with
        200 but silently ignored)."""
        if self._get_ic3 is None:
            raise RuntimeError("ic3 token getter not configured on this ApiClient.")
        h = {
            "Authorization": f"Bearer {self._get_ic3()}",
            "Accept": "application/json",
            "User-Agent": _CHATSVC_BROWSER_UA,
            "Referer": _CHATSVC_REFERER,
            "behavioroverride": "redirectAs404",
            "clientinfo": _CHATSVC_CLIENT_INFO,
            "x-ms-migration": "True",
            "x-ms-request-priority": "10",
            "x-ms-test-user": "False",
        }
        if with_json:
            h["Content-Type"] = "application/json"
        return h

    def chatsvc_get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        ic3_bearer: bool = False,
    ) -> httpx.Response:
        headers = self._ic3_headers() if ic3_bearer else self._chatsvc_headers()
        return self._client.get(
            self._url(CHATSVC_AMER_BASE, path),
            params=params,
            headers=headers,
        )

    def chatsvc_post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        return self._client.post(
            self._url(CHATSVC_AMER_BASE, path),
            json=json,
            params=params,
            headers=self._chatsvc_headers(with_json=True),
        )

    def chatsvc_put(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        ic3_bearer: bool = False,
    ) -> httpx.Response:
        """PUT against chatsvc. Defaults to skypetoken auth.

        Pass ``ic3_bearer=True`` for the newer chatsvc routes (e.g.
        ``properties?name=consumptionHorizonBookmark``) that require an AAD
        Bearer token scoped to ``https://ic3.teams.office.com`` — NOT a Skype
        JWT and NOT a ``teams.microsoft.com`` token. Captured Teams-web HAR
        shows these writes use ``Authorization: Bearer <IC3 AT>`` even though
        the URL is under ``/api/chatsvc/...``.
        """
        headers = (
            self._ic3_headers(with_json=True)
            if ic3_bearer
            else self._chatsvc_headers(with_json=True)
        )
        return self._client.put(
            self._url(CHATSVC_AMER_BASE, path),
            json=json,
            params=params,
            headers=headers,
        )

    # ----- helpers -----

    @staticmethod
    def _url(base: str, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    @staticmethod
    def _bearer_headers(token: str, *, with_json: bool = False) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if with_json:
            h["Content-Type"] = "application/json"
        return h
