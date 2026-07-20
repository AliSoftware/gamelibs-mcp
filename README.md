# gamelibs-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes your **Steam**, **Epic Games
Store**, and **IGDB** game data as tools an MCP client (Claude Code / Desktop) can call.

- **Steam** — owned games, playtime, and *installed-on-this-machine* status.
- **Epic** — owned library + install status, via the reverse-engineered endpoints used by the
  open-source [Legendary](https://github.com/derrod/legendary) CLI / [Heroic](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher).
- **IGDB** — a games metadata database, for enriching/looking up game info.

## Design notes

- **Lean responses.** Tools split into thin *list* tools (ids/titles only) and fat *detail*
  tools that take a sublist of ids. Everything is paginated (`limit`/`offset`) and filterable
  server-side (`search`, `installed_only`, `sort`). No image/cover URLs are ever returned, and
  DLCs are excluded unless you pass `include_dlcs=True`. This keeps large libraries from
  blowing up the model's context.
- **Per-machine install detection.** "Installed" data lives only in local files: Steam's `.acf`
  manifests, the official Epic Games Launcher's `*.item` manifests, and/or Legendary/Heroic's
  `installed.json`. Run this server on the machine you actually game on to get real install
  status. Owned/playtime data works from anywhere. Windows, macOS, and Linux layouts are
  supported (the official Epic launcher is Windows/macOS only).

## Install

Requires Python 3.11+. [`uv`](https://docs.astral.sh/uv/) recommended:

```bash
uv sync                     # or: pip install -e ".[dev]"
```

## Configure

Copy `.env.example` and fill in credentials (or set them in your MCP client's `env`):

| Variable | Needed for | How to get it |
|---|---|---|
| `STEAM_API_KEY` | Steam | https://steamcommunity.com/dev/apikey |
| `STEAM_ID` or `STEAM_VANITY` | Steam | your SteamID64, or your `/id/<slug>` vanity name |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | IGDB | https://dev.twitch.tv/console/apps |

Epic needs no static secret. If you already use Legendary/Heroic on this machine, Epic auth is
bootstrapped from their local `user.json`. Otherwise: call `epic_get_login_url`, sign in, then
`epic_login(<authorizationCode>)`.

> Steam note: `GetOwnedGames` only returns data if your profile's **Game details** privacy is
> set to **Public** (and "Always keep my total playtime private" is off).

## Run

```bash
# Interactive dev/testing with the MCP Inspector:
uv run mcp dev src/gamelibs_mcp/server.py

# Register in Claude Code (see .mcp.json.example for the config-file form):
claude mcp add gamelibs -- uv run --directory "$PWD" python -m gamelibs_mcp
```

## Tools

**Steam** — `steam_list_games`, `steam_get_game_details`, `steam_resolve_user`
**Epic** — `epic_auth_status`, `epic_get_login_url`, `epic_login`, `epic_list_games`, `epic_get_game_details`
**IGDB** — `igdb_search_games`, `igdb_get_game_details`, `igdb_lookup_by_steam_appid`
**Cache** — `refresh_cache` (flush cached data so the next call re-fetches; the `igdb_*`
tools also take `force_refresh` for refreshing specific games)

## Test

```bash
uv run pytest        # offline unit tests (file-format parsing, install detection)
```

## Disclaimer

The Epic integration talks to undocumented, reverse-engineered endpoints (as Legendary/Heroic
do). It may break if Epic changes them, and is for personal use with your own account.
