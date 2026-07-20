# gamelibs-mcp

An [MCP](https://modelcontextprotocol.io) server that exposes your **Steam**, **Epic Games
Store**, and **IGDB** game data as tools an MCP client (Claude Code / Desktop) can call.

- **Steam** — owned games, playtime, and *installed-on-this-machine* status.
- **Epic** — owned library + install status, via the reverse-engineered endpoints used by the
  open-source [Legendary](https://github.com/derrod/legendary) CLI / [Heroic](https://github.com/Heroic-Games-Launcher/HeroicGamesLauncher).
- **[IGDB](https://www.igdb.com/)** — a games metadata database, for enriching/looking up game info.

## Exposed Tools

- **Steam** — `steam_list_games`, `steam_get_game_details`, `steam_resolve_user`
- **Epic** — `epic_auth_status`, `epic_get_login_url`, `epic_login`, `epic_list_games`, `epic_get_game_details`
- **IGDB** — `igdb_search_games`, `igdb_get_game_details`, `igdb_lookup_by_steam_appid`
- **Cache** — `refresh_cache` (flush cached data so the next call re-fetches; the `igdb_*`
tools also take `force_refresh` for refreshing specific games)

<details><summary>Design notes</summary>

- **Lean responses.** This keeps large libraries from blowing up the model's context.
   - Tools split into thin *list* tools (ids/titles only) and fat *detail* tools that take a sublist of ids.
   - Everything is paginated (`limit`/`offset`) and filterable server-side (`search`, `installed_only`, `sort`).
   - No image/cover URLs are ever returned
   - DLCs are excluded unless you pass `include_dlcs=True`. 
- **Per-machine install detection.**
   - "Installed" data lives only in local files: Steam's `.acf` manifests, the official Epic Games Launcher's `*.item` manifests, and/or Legendary/Heroic's `installed.json`.
   - Run this server on the machine you actually game on to get real install status.
   - Owned/playtime data works from anywhere.
   - Windows, macOS, and Linux layouts are supported (the official Epic launcher is Windows/macOS only).
</details>

## Download the code

Requires Python 3.11+. Download or Clone the repo, then run it locally.

[`uv`](https://docs.astral.sh/uv/) is recommended:
```bash
uv sync                     # or: pip install -e ".[dev]"
```

## Configure your API keys

Credentials are read from environment variables:

| Variable | Needed for | How to get it |
|---|---|---|
| `STEAM_API_KEY` | Steam | https://steamcommunity.com/dev/apikey |
| `STEAM_ID` or `STEAM_VANITY` | Steam | your SteamID64, or your `/id/<slug>` vanity name |
| `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET` | IGDB | https://dev.twitch.tv/console/apps |

Those will typically be provided as environment variables in your MCP client's configuration (e.g. `claude_desktop_config.json`, see "Set up in Claude" below), but can also be provided via a `.env` file (see "Contributing - Developer Tips").

> [!NOTE]
> - Epic does not need static secrets. If you already use Legendary/Heroic on this machine, Epic auth is bootstrapped from their local `user.json`. Otherwise, your MCP client is expected to call `epic_get_login_url`, have you open the returned URL in your browser to sign in to Epic like normal, then call `epic_login(<authorizationCode>)`.
> - For Steam, `GetOwnedGames` only returns data if your profile's **Game details** privacy is set to **Public** (and "Always keep my total playtime private" is off).


## Set up in Claude

For Claude Code, you can add the MCP by running the following command from the cloned working copy directory:

```bash
claude mcp add gamelibs \
  --env STEAM_API_KEY=... --env STEAM_ID=... \
  --env TWITCH_CLIENT_ID=... --env TWITCH_CLIENT_SECRET=... \
  -- uv run --directory "$PWD" python -m gamelibs_mcp
```

If you are using Claude Desktop, go to "Settings" > "Developer" > "Edit Config" then add this to the `claude_desktop_config.json` file (see also `.mcp.json.example`):

```json
  "mcpServers": {
    "gamelibs": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "<mcp path on disk>", "python", "-m", "gamelibs_mcp"],
      "env": {
        "STEAM_ID": "<see above>",
        "STEAM_API_KEY": "<see above>",
        "TWITCH_CLIENT_ID": "<see above>",
        "TWITCH_CLIENT_SECRET": "<see above>"
      }
    }
  }
```

Be sure to replace the values of the env vars with your actual credentials (see above), as well as the `<mcp path on disk>` with the actual absolute path to where you cloned the repo (If you're on Windows, don't forget to escape backslashes (e.g. `C:\\Users\\...`) in that path).

## Contributing - Developer Tips

### Store your credentials in an `.env` file

The MCP can read your credentials from env vars declared in a local `.env` file too. Simply `cp .env.example .env` and fill it in with your actual credentials.

While this is not the ideal option for integrating the MCP in Claude (where you'd usually prefer to use `claude mcp add gamlibs --env ... --env ...` or the `"env"` attribute in the JSON config, like described above), having the ability to store your credentials in a local `.env` file can be very useful when working on the MCP's code itself for debugging or contributing, to avoid having to export them globally in your shell or having to pass them to every call while you test and iterate on the MCP.

On startup the server loads `.env` files searched **from the directory it is launched in, upward**. This works for `uv run mcp dev …` (launched from the project root) and for a `claude mcp add … --directory <project>` registration (launched in the project dir). It does *not* magically work if the server is launched from an unrelated directory.

### Use the MCP Inspector to explore, debug and test the MCP

The MCP Inspector is a nice tool for developers who are working on _any_ MCP. It's not specific to this `gamelibs` MCP in particular, but I find this useful so figured I'd share.

To use it, simply run this command from the root of your repo's working copy:

```bash
uv run mcp dev src/gamelibs_mcp/server.py
```

> [!NOTE]
> This MCP Inspector requires `Node.js` / `npx` to run

### Run Tests

```bash
uv run pytest        # offline unit tests (file-format parsing, install detection)
```

## Disclaimer

The Epic integration talks to undocumented, reverse-engineered endpoints (as Legendary/Heroic
do). It may break if Epic changes them, and is for personal use with your own account.
