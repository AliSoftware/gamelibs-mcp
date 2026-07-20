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

# ExternalGameCategoryEnum -> human store name, for surfacing `external_games` (which
# stores a game is available/owned on). Unknown codes fall back to their number.
_EXTERNAL_STORE_NAMES = {
    1: "steam",
    5: "gog",
    11: "microsoft",
    13: "apple",
    15: "android",
    20: "amazon_asin",
    22: "amazon_luna",
    23: "amazon_adg",
    26: "epic",
    28: "oculus",
    29: "utomik",
    30: "itch_io",
    31: "xbox_marketplace",
    32: "kartridge",
    36: "playstation_store",
    37: "focus",
    54: "xbox_game_pass_cloud",
    55: "gamejolt",
}

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


# --- persistent per-id result cache ----------------------------------------
# IGDB game metadata is effectively immutable per game id, so (like Epic's catalog cache)
# we persist resolved details to disk keyed by IGDB game id and by Steam appid. Search
# results are deliberately NOT cached (query-dependent and can shift as IGDB's DB grows).
_CACHE_FILE = "igdb_cache.json"
_cache_mem: dict[str, dict] | None = None

# GameCategoryEnum values that count as DLC/expansions (excluded unless include_dlcs).
_DLC_CATEGORY_INTS = {1, 2, 4}


def _cache_path() -> Path:
    return config.config_dir() / _CACHE_FILE


def _load_cache() -> dict[str, dict]:
    global _cache_mem
    if _cache_mem is None:
        path = _cache_path()
        if path.is_file():
            try:
                _cache_mem = json.loads(path.read_text())
            except (OSError, ValueError):
                _cache_mem = {}
        else:
            _cache_mem = {}
    return _cache_mem


def _save_cache() -> None:
    if _cache_mem is not None:
        _cache_path().write_text(json.dumps(_cache_mem))


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
# search_games filters DLC via the `where` clause, so _SUMMARY_FIELDS omits `category`.
# _DETAIL_FIELDS requests `category` because get_game_details caches full records by id and
# applies the DLC filter *after* retrieval — so one cached record serves either include_dlcs.
_SUMMARY_FIELDS = "id,name,first_release_date"
_DETAIL_FIELDS = (
    "id,name,summary,storyline,url,first_release_date,category,"
    "rating,rating_count,aggregated_rating,aggregated_rating_count,total_rating,"
    "genres.name,platforms.name,game_modes.name,themes.name,keywords.name,"
    "involved_companies.company.name,involved_companies.developer,"
    "involved_companies.publisher,similar_games.name,multiplayer_modes.*,"
    "alternative_names.name,external_games.category,external_games.uid,"
    "franchise.name,franchises.name,collections.name"
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
    """Return full text details for IGDB game ids, using the persistent per-id cache.

    Only cache-misses hit the API. DLC/expansion filtering is applied after retrieval so a
    cached record is reusable regardless of the caller's ``include_dlcs``.
    """
    if not ids:
        return []
    want = [int(i) for i in ids]
    cache = _load_cache()

    misses = [i for i in want if f"game:{i}" not in cache]
    if misses:
        id_list = ",".join(str(i) for i in misses)
        body = f"fields {_DETAIL_FIELDS}; where id = ({id_list}); limit {len(misses)};"
        rows = await _query("games", body)
        for r in rows:
            rec = _detail_dict(r)
            rec["_category"] = r.get("category")  # internal, used only for DLC filtering
            cache[f"game:{rec['id']}"] = rec
        _save_cache()

    out: list[dict] = []
    for i in want:
        rec = cache.get(f"game:{i}")
        if rec is None:
            continue  # id not found in IGDB
        if not include_dlcs and rec.get("_category") in _DLC_CATEGORY_INTS:
            continue
        out.append({k: v for k, v in rec.items() if not k.startswith("_")})
    return out


async def lookup_by_steam_appid(appids: list[int]) -> list[dict]:
    """Map Steam appids to IGDB games via /external_games (category = Steam), cached by appid.

    Only positive matches are cached; appids with no IGDB mapping are re-queried next time,
    since IGDB's database grows and a miss today may resolve later.
    """
    if not appids:
        return []
    want = [int(a) for a in appids]
    cache = _load_cache()

    misses = [a for a in want if f"steam:{a}" not in cache]
    if misses:
        uids = ",".join(f'"{a}"' for a in misses)
        body = (
            f"fields uid,game,game.name,game.first_release_date; "
            f"where category = {EXTERNAL_STEAM} & uid = ({uids}); limit {len(misses)};"
        )
        rows = await _query("external_games", body)
        for r in rows:
            uid = int(r["uid"]) if str(r.get("uid", "")).isdigit() else None
            if uid is None:
                continue
            game = r.get("game") or {}
            cache[f"steam:{uid}"] = {
                "steam_appid": uid,
                "igdb_id": game.get("id"),
                "name": game.get("name"),
            }
        _save_cache()

    return [cache[f"steam:{a}"] for a in want if f"steam:{a}" in cache]


# --- shaping ---------------------------------------------------------------

def _release_year(epoch: int | None) -> int | None:
    if not epoch:
        return None
    return time.gmtime(int(epoch)).tm_year


def _multiplayer_summary(modes: list[dict]) -> dict | None:
    """Collapse IGDB's per-platform multiplayer_modes into one compact set of flags.

    Returns None when IGDB has no multiplayer data (distinct from "not multiplayer") so the
    model can tell "unknown" apart from "single-player only". Flags are OR-ed across platforms.
    """
    if not modes:
        return None

    def _any(flag: str) -> bool:
        return any(bool(m.get(flag)) for m in modes)

    def _max(flag: str) -> int | None:
        vals = [m.get(flag) for m in modes if isinstance(m.get(flag), int) and m.get(flag)]
        return max(vals) if vals else None

    return {
        "online_coop": _any("onlinecoop"),
        "offline_coop": _any("offlinecoop"),
        "lan_coop": _any("lancoop"),
        "campaign_coop": _any("campaigncoop"),
        "splitscreen": _any("splitscreen"),
        "online_max_players": _max("onlinemax"),
        "offline_max_players": _max("offlinemax"),
    }


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
        "multiplayer": _multiplayer_summary(r.get("multiplayer_modes", []) or []),
        "themes": [t["name"] for t in r.get("themes", []) if t.get("name")],
        "keywords": [k["name"] for k in r.get("keywords", []) if k.get("name")],
        "developers": developers,
        "publishers": publishers,
        "similar_games": [s["name"] for s in r.get("similar_games", []) if s.get("name")],
        "alternative_names": [
            a["name"] for a in r.get("alternative_names", []) if a.get("name")
        ],
        "franchise": (r.get("franchise") or {}).get("name"),
        "franchises": [f["name"] for f in r.get("franchises", []) if f.get("name")],
        "collections": [c["name"] for c in r.get("collections", []) if c.get("name")],
        "external_stores": _external_stores(r.get("external_games", []) or []),
        "url": r.get("url"),
    }


def _external_stores(externals: list[dict]) -> list[dict]:
    """Which storefronts a game maps to on IGDB, as ``[{store, uid}]`` (deduplicated)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for e in externals:
        cat = e.get("category")
        store = _EXTERNAL_STORE_NAMES.get(cat, str(cat) if cat is not None else None)
        uid = e.get("uid")
        key = (store, uid)
        if store and key not in seen:
            seen.add(key)
            out.append({"store": store, "uid": uid})
    return out
