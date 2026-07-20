"""IGDB API v4 client.

Auth is delegated to Twitch: a ``client_credentials`` grant yields a long-lived app token
that we cache on disk. Every IGDB call is a POST whose body is an APIcalypse query string.
We request only textual fields (no cover/screenshot image fields) to keep responses lean.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .. import config
from ..http import ApiError, RateLimiter, make_client, request_with_retry

TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
IGDB_BASE = "https://api.igdb.com/v4"

# IGDB allows 4 requests/second; keep a little headroom.
_limiter = RateLimiter(rate_per_sec=4.0, burst=4)

# ExternalGameCategoryEnum: 1 == Steam.
EXTERNAL_STEAM = 1

_TOKEN_FILE = "igdb_token.json"


def _token_path() -> Path:
    return config.config_dir() / _TOKEN_FILE


def _load_cached_token() -> str | None:
    path = _token_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    # Refresh a little before the real expiry to avoid mid-call 401s.
    if data.get("access_token") and data.get("expires_at", 0) > time.time() + 60:
        return data["access_token"]
    return None


def _store_token(access_token: str, expires_in: int) -> None:
    path = _token_path()
    path.write_text(
        json.dumps({"access_token": access_token, "expires_at": time.time() + expires_in})
    )
    path.chmod(0o600)


async def _get_token() -> str:
    cached = _load_cached_token()
    if cached:
        return cached

    client_id, client_secret = config.twitch_credentials()
    async with make_client() as client:
        resp = await request_with_retry(
            client,
            "POST",
            TWITCH_TOKEN_URL,
            params={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials",
            },
        )
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise ApiError(f"Twitch token request failed: {data}")
    _store_token(token, int(data.get("expires_in", 3600)))
    return token


async def _query(endpoint: str, body: str) -> list[dict]:
    """Run one APIcalypse query against an IGDB endpoint, refreshing the token on 401."""
    client_id, _ = config.twitch_credentials()

    async def _do(token: str):
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        await _limiter.acquire()
        async with make_client(headers=headers) as client:
            return await request_with_retry(
                client, "POST", f"{IGDB_BASE}/{endpoint}", content=body.encode()
            )

    token = await _get_token()
    resp = await _do(token)
    if resp.status_code == 401:  # token rejected — force a refresh and retry once
        _token_path().unlink(missing_ok=True)
        resp = await _do(await _get_token())
    if resp.status_code >= 400:
        raise ApiError(f"IGDB {endpoint} error {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# --- public helpers --------------------------------------------------------

# Textual fields only — deliberately no cover/screenshots (see project design notes).
# We don't request `category` even though the DLC filter uses it: filtering happens in the
# `where` clause, which can reference fields that aren't in `fields`.
_SUMMARY_FIELDS = "id,name,first_release_date"
_DETAIL_FIELDS = (
    "id,name,summary,storyline,url,first_release_date,"
    "rating,rating_count,aggregated_rating,aggregated_rating_count,total_rating,"
    "genres.name,platforms.name,game_modes.name,themes.name,"
    "involved_companies.company.name,involved_companies.developer,"
    "involved_companies.publisher,similar_games.name"
)


# Non-DLC GameCategory values to keep when include_dlcs is False (excludes 1,2,4).
_KEEP_CATEGORIES = "(0,3,5,6,7,8,9,10,11,12,13,14)"


def _dlc_clause(include_dlcs: bool) -> str:
    """A `where`-fragment (no leading keyword) that drops DLC/expansions, or '' to keep all."""
    if include_dlcs:
        return ""
    return f"(category = {_KEEP_CATEGORIES} | category = null)"


async def search_games(query: str, include_dlcs: bool, limit: int, offset: int) -> list[dict]:
    escaped = query.replace('"', '\\"')
    clause = _dlc_clause(include_dlcs)
    where = f"where {clause}; " if clause else ""
    body = (
        f'search "{escaped}"; fields {_SUMMARY_FIELDS}; '
        f"{where}limit {max(1, min(limit, 500))}; offset {max(0, offset)};"
    )
    rows = await _query("games", body)
    return [_summary_dict(r) for r in rows]


async def get_game_details(ids: list[int], include_dlcs: bool) -> list[dict]:
    if not ids:
        return []
    id_list = ",".join(str(int(i)) for i in ids)
    where = f"where id = ({id_list})"
    clause = _dlc_clause(include_dlcs)
    if clause:
        where += f" & {clause}"
    body = f"fields {_DETAIL_FIELDS}; {where}; limit {len(ids)};"
    rows = await _query("games", body)
    return [_detail_dict(r) for r in rows]


async def lookup_by_steam_appid(appids: list[int]) -> list[dict]:
    """Map Steam appids to IGDB games via /external_games (category = Steam)."""
    if not appids:
        return []
    uids = ",".join(f'"{int(a)}"' for a in appids)
    body = (
        f"fields uid,game,game.name,game.first_release_date; "
        f"where category = {EXTERNAL_STEAM} & uid = ({uids}); limit {len(appids)};"
    )
    rows = await _query("external_games", body)
    out: list[dict] = []
    for r in rows:
        game = r.get("game") or {}
        out.append(
            {
                "steam_appid": int(r["uid"]) if str(r.get("uid", "")).isdigit() else r.get("uid"),
                "igdb_id": game.get("id"),
                "name": game.get("name"),
            }
        )
    return out


# --- shaping ---------------------------------------------------------------

def _release_year(epoch: int | None) -> int | None:
    if not epoch:
        return None
    return time.gmtime(int(epoch)).tm_year


def _summary_dict(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "release_year": _release_year(r.get("first_release_date")),
    }


def _detail_dict(r: dict) -> dict:
    companies = r.get("involved_companies", []) or []
    developers = [
        c["company"]["name"]
        for c in companies
        if c.get("developer") and c.get("company", {}).get("name")
    ]
    publishers = [
        c["company"]["name"]
        for c in companies
        if c.get("publisher") and c.get("company", {}).get("name")
    ]
    return {
        "id": r.get("id"),
        "name": r.get("name"),
        "summary": r.get("summary"),
        "storyline": r.get("storyline"),
        "release_year": _release_year(r.get("first_release_date")),
        "rating": r.get("rating"),
        "rating_count": r.get("rating_count"),
        "aggregated_rating": r.get("aggregated_rating"),
        "total_rating": r.get("total_rating"),
        "genres": [g["name"] for g in r.get("genres", []) if g.get("name")],
        "platforms": [p["name"] for p in r.get("platforms", []) if p.get("name")],
        "game_modes": [m["name"] for m in r.get("game_modes", []) if m.get("name")],
        "themes": [t["name"] for t in r.get("themes", []) if t.get("name")],
        "developers": developers,
        "publishers": publishers,
        "similar_games": [s["name"] for s in r.get("similar_games", []) if s.get("name")],
        "url": r.get("url"),
    }
