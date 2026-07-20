"""Epic OAuth token management.

Modeled on the open-source Legendary CLI. Uses Epic's public "launcher" OAuth client
(the same credentials the real launcher ships with) for Basic auth on the token endpoint;
all subsequent API calls use a Bearer access token.

Tokens are persisted under our config dir. On first use we also try to *bootstrap* from a
local Legendary/Heroic ``user.json`` (see :mod:`gamelibs_mcp.epic.local`), so a user who
already runs those tools may never need the interactive browser login.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .. import config
from ..http import ApiError, make_client, request_with_retry

OAUTH_HOST = "https://account-public-service-prod03.ol.epicgames.com"
TOKEN_URL = f"{OAUTH_HOST}/account/api/oauth/token"

# Epic's launcherAppClient2 credentials (public; shipped with the real launcher).
CLIENT_ID = "34a02cf8f4414e29b15921876da36f9a"
CLIENT_SECRET = "daafbccc737745039dffe53d94fc76cf"

USER_AGENT = (
    "UELauncher/11.0.1-14907503+++Portal+Release-Live "
    "Windows/10.0.19041.1.256.64bit"
)

# The endpoint that mints the authorizationCode — but only for an authenticated session.
REDIRECT_URL = (
    "https://www.epicgames.com/id/api/redirect?"
    f"clientId={CLIENT_ID}&responseType=code"
)
# Login-first URL: Epic authenticates you, THEN bounces to REDIRECT_URL (which now has a
# live session and returns a real authorizationCode). Opening REDIRECT_URL directly while
# logged out yields all-null fields.
LOGIN_URL = (
    "https://www.epicgames.com/id/login?redirectUrl="
    + quote(REDIRECT_URL, safe="")
)

_TOKEN_FILE = "epic_auth.json"
# Refresh when fewer than this many seconds remain on the access token.
_REFRESH_MARGIN = 600


class EpicAuthError(ApiError):
    """Auth is missing or expired and re-login is required."""


def _basic_auth_header() -> str:
    raw = f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    return "basic " + base64.b64encode(raw).decode()


def _token_path() -> Path:
    return config.config_dir() / _TOKEN_FILE


def _load() -> dict | None:
    path = _token_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _store(data: dict) -> None:
    path = _token_path()
    path.write_text(json.dumps(data))
    path.chmod(0o600)


def _expires_at(data: dict) -> float:
    """Access-token expiry as a unix timestamp (from expires_at ISO or expires_in)."""
    iso = data.get("expires_at")
    if iso:
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return float(data.get("_expires_epoch", 0))


async def _post_token(form: dict[str, str]) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Authorization": _basic_auth_header(),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with make_client(headers=headers) as client:
        resp = await request_with_retry(client, "POST", TOKEN_URL, data=form)
    data = resp.json()
    if resp.status_code >= 400 or "access_token" not in data:
        raise EpicAuthError(
            f"Epic token request failed ({resp.status_code}): "
            f"{data.get('errorMessage', resp.text[:200])}"
        )
    # Normalize an absolute expiry for our own bookkeeping.
    if "expires_in" in data and "expires_at" not in data:
        data["_expires_epoch"] = time.time() + int(data["expires_in"])
    return data


async def login_with_authorization_code(code: str) -> dict:
    """Exchange an authorizationCode (from the browser login) for tokens; persist them."""
    data = await _post_token(
        {"grant_type": "authorization_code", "code": code.strip(), "token_type": "eg1"}
    )
    _store(data)
    return data


async def _refresh(refresh_token: str) -> dict:
    data = await _post_token(
        {"grant_type": "refresh_token", "refresh_token": refresh_token, "token_type": "eg1"}
    )
    _store(data)
    return data


async def get_access_token() -> str:
    """Return a valid access token, refreshing or bootstrapping as needed.

    Resolution order: our stored token → refresh via stored refresh_token → bootstrap from
    a local Legendary/Heroic ``user.json``. Raises :class:`EpicAuthError` if none work.
    """
    data = _load()

    # Bootstrap from Legendary/Heroic on first run.
    if data is None:
        from .local import read_legendary_user

        data = read_legendary_user()
        if data is not None:
            _store(data)

    if data is None:
        raise EpicAuthError(
            "Not logged in to Epic. Call epic_get_login_url, sign in, then epic_login(code)."
        )

    # Still valid?
    if data.get("access_token") and _expires_at(data) > time.time() + _REFRESH_MARGIN:
        return data["access_token"]

    # Try to refresh.
    if data.get("refresh_token"):
        try:
            data = await _refresh(data["refresh_token"])
            return data["access_token"]
        except EpicAuthError:
            pass

    raise EpicAuthError(
        "Epic session expired and could not be refreshed. Re-run epic_get_login_url + epic_login."
    )


def auth_status() -> dict:
    """Report current auth state without performing any network calls."""
    data = _load()
    source = "stored"
    if data is None:
        from .local import read_legendary_user

        data = read_legendary_user()
        source = "legendary/heroic" if data else "none"
    if data is None:
        return {"authenticated": False, "source": "none"}
    valid = bool(data.get("access_token")) and _expires_at(data) > time.time()
    return {
        "authenticated": valid,
        "source": source,
        "account_id": data.get("account_id"),
        "display_name": data.get("displayName"),
        "expires_at": data.get("expires_at"),
        "can_refresh": bool(data.get("refresh_token")),
    }
