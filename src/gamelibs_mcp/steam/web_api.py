"""Steam Web API client: owned games, playtime, profile/vanity resolution."""

from __future__ import annotations

from dataclasses import dataclass

from .. import config
from ..http import ApiError, make_client, request_with_retry
from ..util import TTLCache

BASE = "https://api.steampowered.com"

# Owned-games responses are cached briefly so pagination/filtering across successive
# tool calls doesn't re-hit the network for every page.
_owned_cache = TTLCache(ttl_seconds=300.0)


@dataclass
class OwnedGame:
    appid: int
    name: str
    playtime_forever: int  # minutes
    playtime_2weeks: int  # minutes (0 if not played recently)
    playtime_windows: int
    playtime_mac: int
    playtime_linux: int
    playtime_deck: int
    last_played: int | None  # unix seconds
    has_stats: bool


class SteamPrivateError(ApiError):
    """The target profile's game details are private (empty API response)."""


async def resolve_steam_id() -> str:
    """Resolve the configured SteamID64 from STEAM_ID or STEAM_VANITY."""
    sid = config.steam_id()
    if sid:
        return sid
    vanity = config.steam_vanity()
    if not vanity:
        raise config.ConfigError(
            "Set STEAM_ID (a SteamID64) or STEAM_VANITY (your custom URL slug)."
        )
    return await resolve_vanity(vanity)


async def resolve_vanity(vanity_or_url: str) -> str:
    """Resolve a vanity slug (or profile URL) to a SteamID64."""
    key = config.steam_api_key()
    slug = vanity_or_url.strip().rstrip("/")
    # A /profiles/<id> URL already contains the SteamID64.
    if "/profiles/" in slug:
        return slug.rsplit("/", 1)[-1]
    if "/id/" in slug:
        slug = slug.rsplit("/id/", 1)[-1].split("/")[0]

    async with make_client() as client:
        resp = await request_with_retry(
            client,
            "GET",
            f"{BASE}/ISteamUser/ResolveVanityURL/v1/",
            params={"key": key, "vanityurl": slug},
        )
    data = resp.json().get("response", {})
    if data.get("success") == 1 and data.get("steamid"):
        return data["steamid"]
    raise ApiError(
        f"Could not resolve Steam vanity '{slug}': {data.get('message', 'no match')}"
    )


async def get_owned_games(force_refresh: bool = False) -> list[OwnedGame]:
    """Fetch all owned games with playtime for the configured user (cached ~5 min)."""
    if not force_refresh:
        cached = _owned_cache.get()
        if cached is not None:
            return cached

    key = config.steam_api_key()
    steamid = await resolve_steam_id()

    async with make_client() as client:
        resp = await request_with_retry(
            client,
            "GET",
            f"{BASE}/IPlayerService/GetOwnedGames/v1/",
            params={
                "key": key,
                "steamid": steamid,
                "include_appinfo": 1,
                "include_played_free_games": 1,
                "format": "json",
            },
        )
    response = resp.json().get("response", {})
    if not response or "games" not in response:
        # Empty object == private "Game details" (or invalid steamid).
        raise SteamPrivateError(
            "Steam returned no games. The profile's 'Game details' privacy setting must be "
            "Public (and 'Always keep my total playtime private' unchecked), or the SteamID "
            "is wrong."
        )

    games = [
        OwnedGame(
            appid=int(g["appid"]),
            name=g.get("name", ""),
            playtime_forever=int(g.get("playtime_forever", 0)),
            playtime_2weeks=int(g.get("playtime_2weeks", 0)),
            playtime_windows=int(g.get("playtime_windows_forever", 0)),
            playtime_mac=int(g.get("playtime_mac_forever", 0)),
            playtime_linux=int(g.get("playtime_linux_forever", 0)),
            playtime_deck=int(g.get("playtime_deck_forever", 0)),
            last_played=int(g["rtime_last_played"]) if g.get("rtime_last_played") else None,
            has_stats=bool(g.get("has_community_visible_stats", False)),
        )
        for g in response["games"]
    ]
    _owned_cache.set(games)
    return games
