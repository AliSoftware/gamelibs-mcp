"""FastMCP server exposing Steam, Epic, and IGDB game-library tools.

Design principles (see plan): thin-list + fat-detail, pagination + server-side filtering
everywhere, no image URLs, DLCs excluded by default. Every tool degrades to a clear
``{"error": ...}`` rather than raising, so one misconfigured integration never breaks the rest.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.fastmcp import FastMCP

# NOTE: absolute imports (not `from . import ...`) so that `mcp dev`/`mcp run`, which load
# this file by path as a top-level module (no parent package), can import it successfully.
from gamelibs_mcp import config
from gamelibs_mcp.epic import api as epic_api
from gamelibs_mcp.epic import auth as epic_auth
from gamelibs_mcp.http import ApiError
from gamelibs_mcp.igdb import client as igdb
from gamelibs_mcp.steam import local as steam_local
from gamelibs_mcp.steam import web_api as steam_web
from gamelibs_mcp.util import MAX_DETAIL_IDS, epoch_to_iso, paginate

mcp = FastMCP("gamelibs")


def tool_errors(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Convert known config/API errors into a structured result instead of an exception."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except (config.ConfigError, ApiError) as exc:
            return {"error": str(exc)}

    return wrapper


def _cap_ids(ids: list, kind: str) -> dict | None:
    if len(ids) > MAX_DETAIL_IDS:
        return {
            "error": f"Too many {kind} ({len(ids)}); max {MAX_DETAIL_IDS} per call. "
            "Split into multiple calls."
        }
    return None


# ============================ Steam ========================================

@mcp.tool()
@tool_errors
async def steam_list_games(
    installed_only: bool = False,
    search: str | None = None,
    sort: str = "playtime",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List owned Steam games (thin: appid, name, playtime, last-played, installed).

    Args:
        installed_only: Only games installed on THIS machine (requires local Steam).
        search: Case-insensitive substring match on the game name.
        sort: One of "playtime" (desc), "name" (asc), "last_played" (desc).
        limit: Page size (1-500).
        offset: Pagination offset.
    """
    owned = await steam_web.get_owned_games()
    installed_map = steam_local.scan_installed()
    steam_present = bool(installed_map) or steam_local.steam_is_present()

    rows = []
    for g in owned:
        is_installed = g.appid in installed_map
        if installed_only and not is_installed:
            continue
        if search and search.lower() not in g.name.lower():
            continue
        rows.append(
            {
                "appid": g.appid,
                "name": g.name,
                "playtime_hours": round(g.playtime_forever / 60, 1),
                "last_played": epoch_to_iso(g.last_played),
                "installed": is_installed if steam_present else None,
            }
        )

    if sort == "name":
        rows.sort(key=lambda r: r["name"].lower())
    elif sort == "last_played":
        rows.sort(key=lambda r: r["last_played"] or "", reverse=True)
    else:  # playtime
        rows.sort(key=lambda r: r["playtime_hours"], reverse=True)

    result = paginate(rows, limit, offset)
    result["install_detection"] = "available" if steam_present else "unavailable-on-this-machine"
    return result


@mcp.tool()
@tool_errors
async def steam_get_game_details(appids: list[int], include_dlcs: bool = False) -> dict:
    """Full details for specific Steam games: playtime breakdown + local install info.

    Args:
        appids: Steam appids to fetch (max 50 per call).
        include_dlcs: Reserved; Steam DLC ownership is not separately derivable here.
    """
    cap = _cap_ids(appids, "appids")
    if cap:
        return cap

    owned = {g.appid: g for g in await steam_web.get_owned_games()}
    installed_map = steam_local.scan_installed()

    games = []
    for appid in appids:
        g = owned.get(appid)
        inst = installed_map.get(appid)
        entry: dict[str, Any] = {"appid": appid}
        if g is None and inst is None:
            entry["error"] = "not owned by / not installed for this user"
            games.append(entry)
            continue
        if g is not None:
            entry.update(
                {
                    "name": g.name,
                    "playtime_hours": round(g.playtime_forever / 60, 1),
                    "playtime_minutes": g.playtime_forever,
                    "playtime_2weeks_minutes": g.playtime_2weeks,
                    "playtime_windows_minutes": g.playtime_windows,
                    "playtime_mac_minutes": g.playtime_mac,
                    "playtime_linux_minutes": g.playtime_linux,
                    "playtime_deck_minutes": g.playtime_deck,
                    "last_played": epoch_to_iso(g.last_played),
                    "has_stats": g.has_stats,
                }
            )
        entry["installed"] = inst is not None
        if inst is not None:
            entry.update(
                {
                    "name": entry.get("name") or inst.name,
                    "install_dir": inst.install_dir,
                    "size_on_disk_bytes": inst.size_on_disk,
                    "library_path": inst.library_path,
                    "state_flags": inst.state_flags,
                    "fully_installed": inst.fully_installed,
                    "last_updated": epoch_to_iso(inst.last_updated),
                }
            )
        games.append(entry)
    return {"count": len(games), "games": games}


@mcp.tool()
@tool_errors
async def steam_resolve_user(vanity_or_url: str) -> dict:
    """Resolve a Steam vanity slug or profile URL to a SteamID64."""
    steamid = await steam_web.resolve_vanity(vanity_or_url)
    return {"steamid": steamid}


# ============================ Epic =========================================

@mcp.tool()
@tool_errors
async def epic_auth_status() -> dict:
    """Report Epic auth state (does a valid/refreshable token exist, and from where)."""
    return epic_auth.auth_status()


@mcp.tool()
@tool_errors
async def epic_get_login_url() -> dict:
    """Get the Epic login URL. Sign in there, then copy the `authorizationCode` for epic_login."""
    return {
        "login_url": epic_auth.LOGIN_URL,
        "instructions": (
            "1) Open login_url in a browser and sign in to Epic (it redirects to a JSON page). "
            "2) Copy the value of \"authorizationCode\" from that JSON. "
            "3) Call epic_login with that code (valid ~5 minutes). "
            "If authorizationCode is null, you were not signed in — sign in first (a fresh "
            "private/incognito window is the most reliable), then reopen login_url."
        ),
        "redirect_url": epic_auth.REDIRECT_URL,
        "note": (
            "redirect_url is the endpoint that mints the code; it only works when you already "
            "have an active Epic web session. Prefer login_url, which signs you in first."
        ),
    }


@mcp.tool()
@tool_errors
async def epic_login(authorization_code: str) -> dict:
    """Exchange an Epic authorizationCode (from epic_get_login_url) for a stored session."""
    data = await epic_auth.login_with_authorization_code(authorization_code)
    return {
        "authenticated": True,
        "account_id": data.get("account_id"),
        "display_name": data.get("displayName"),
    }


@mcp.tool()
@tool_errors
async def epic_list_games(
    installed_only: bool = False,
    search: str | None = None,
    include_dlcs: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List owned Epic games (thin: app_name, catalog_item_id, namespace, title, installed).

    The first call warms a persistent catalog-metadata cache (titles + game/DLC categories),
    so it may be slow on a large library; subsequent calls are fast. Only a page is returned.

    Args:
        installed_only: Only games installed here (via local Legendary/Heroic state).
        search: Case-insensitive substring match on the title.
        include_dlcs: Include DLC/add-on entries (excluded by default).
        limit: Page size (1-500). Use pagination for large libraries.
        offset: Pagination offset.
    """
    games = await epic_api.list_games(installed_only, search, include_dlcs)
    return paginate(games, limit, offset)


@mcp.tool()
@tool_errors
async def epic_get_game_details(app_names: list[str], include_dlcs: bool = False) -> dict:
    """Full text details for specific Epic games (title, description, developer, install info).

    Args:
        app_names: Epic app_name identifiers (from epic_list_games), max 50 per call.
        include_dlcs: Include entries that are DLC (excluded by default).
    """
    cap = _cap_ids(app_names, "app_names")
    if cap:
        return cap
    games = await epic_api.get_game_details(app_names, include_dlcs)
    return {"count": len(games), "games": games}


# ============================ IGDB =========================================

@mcp.tool()
@tool_errors
async def igdb_search_games(
    query: str, include_dlcs: bool = False, limit: int = 10, offset: int = 0
) -> dict:
    """Search IGDB by name (thin: id, name, release_year). Use igdb_get_game_details for more.

    Args:
        query: Free-text game name to search for.
        include_dlcs: Include DLC/expansion results (excluded by default).
        limit: Page size (1-500, IGDB default 10).
        offset: Pagination offset.
    """
    rows = await igdb.search_games(query, include_dlcs, limit, offset)
    return {"count": len(rows), "limit": limit, "offset": offset, "games": rows}


@mcp.tool()
@tool_errors
async def igdb_get_game_details(ids: list[int], include_dlcs: bool = False) -> dict:
    """Full text metadata for specific IGDB game ids (summary, ratings, genres, companies...).

    Args:
        ids: IGDB game ids (from igdb_search_games / igdb_lookup_by_steam_appid), max 50 per call.
        include_dlcs: Include ids that are DLC/expansions (excluded by default).
    """
    cap = _cap_ids(ids, "ids")
    if cap:
        return cap
    rows = await igdb.get_game_details(ids, include_dlcs)
    return {"count": len(rows), "games": rows}


@mcp.tool()
@tool_errors
async def igdb_lookup_by_steam_appid(appids: list[int]) -> dict:
    """Map Steam appids to IGDB games (id + name) — bridges a Steam library to IGDB metadata.

    Args:
        appids: Steam appids (max 50 per call).
    """
    cap = _cap_ids(appids, "appids")
    if cap:
        return cap
    rows = await igdb.lookup_by_steam_appid(appids)
    return {"count": len(rows), "matches": rows}


def main() -> None:
    """Console-script / module entry point: run the server over stdio."""
    mcp.run()
