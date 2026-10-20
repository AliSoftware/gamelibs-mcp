"""Environment-based configuration and cross-platform paths.

Nothing here raises at import time. Each accessor either returns a value or raises
:class:`ConfigError` only when a feature that needs it is actually invoked, so a server
configured for only one integration still starts and serves that integration.

Credentials come from the environment. On import we also load a local ``.env`` file
(searched from the launch directory upward) so tokens can live in a file next to the
project — but variables already set in the real environment (e.g. passed by the MCP
client's ``env`` block) take precedence and are never overwritten.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs
from dotenv import find_dotenv, load_dotenv

APP_NAME = "gamelibs-mcp"

# Load .env if one is found from the current working directory upward. `override=False`
# (the default) means real environment variables win over .env entries.
_dotenv_path = find_dotenv(usecwd=True)
if _dotenv_path:
    load_dotenv(_dotenv_path)


class ConfigError(RuntimeError):
    """Raised when a required credential or setting is missing/invalid."""


def config_dir() -> Path:
    """Cross-platform per-user config dir for our own cached tokens."""
    path = Path(platformdirs.user_config_dir(APP_NAME))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _require(name: str, hint: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(
            f"Missing required environment variable {name}. {hint}"
        )
    return value


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


# --- Steam -----------------------------------------------------------------
def steam_api_key() -> str:
    return _require(
        "STEAM_API_KEY",
        "Get one at https://steamcommunity.com/dev/apikey and set STEAM_API_KEY.",
    )


def steam_id() -> str | None:
    """A pre-resolved SteamID64, if the user set one directly."""
    return _optional("STEAM_ID")


def steam_vanity() -> str | None:
    """A Steam vanity URL slug, resolved to a SteamID64 on demand."""
    return _optional("STEAM_VANITY")


def steam_path_override() -> str | None:
    return _optional("STEAM_PATH")


def steam_extra_library_paths() -> list[str]:
    """Extra Steam library folders (e.g. mounted network shares)."""
    raw = _optional("STEAM_EXTRA_LIBRARY_PATHS")
    if not raw:
        return []
    # Accept either ':' (POSIX) or ';' (Windows) separators.
    sep = ";" if ";" in raw else os.pathsep
    return [p.strip() for p in raw.split(sep) if p.strip()]


# --- IGDB / Twitch ---------------------------------------------------------
def twitch_credentials() -> tuple[str, str]:
    client_id = _require(
        "TWITCH_CLIENT_ID",
        "Register an app at https://dev.twitch.tv/console/apps for IGDB access.",
    )
    client_secret = _require(
        "TWITCH_CLIENT_SECRET",
        "Register an app at https://dev.twitch.tv/console/apps for IGDB access.",
    )
    return client_id, client_secret


# --- Epic ------------------------------------------------------------------
def legendary_config_override() -> str | None:
    return _optional("LEGENDARY_CONFIG_PATH")


def heroic_config_override() -> str | None:
    return _optional("HEROIC_CONFIG_PATH")


def epic_manifests_override() -> str | None:
    """Override the official Epic Games Launcher manifests dir (the *.item folder)."""
    return _optional("EPIC_MANIFESTS_PATH")


# --- GOG -------------------------------------------------------------------
def gog_database_override() -> str | None:
    """Explicit path to the GOG Galaxy ``galaxy-2.0.db`` database."""
    return _optional("GOG_DB_PATH")


def gog_user_id() -> str | None:
    """GOG Galaxy profile ID, required only when the database has multiple profiles."""
    return _optional("GOG_USER_ID")
