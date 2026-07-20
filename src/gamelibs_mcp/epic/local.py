"""Read local Legendary / Heroic state: auth bootstrap + installed games.

Standalone Legendary keeps its data in ``~/.config/legendary`` (overridable). Heroic bundles
Legendary and stores it under its own app folder (``.../heroic/legendaryConfig/legendary``).
We check both so "installed" status and a bootstrap auth token can be discovered wherever the
user's launcher of choice put them.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import config


def _legendary_dirs() -> list[Path]:
    """Candidate Legendary config directories, most-specific first."""
    dirs: list[Path] = []

    override = config.legendary_config_override()
    if override:
        dirs.append(Path(override).expanduser())

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        dirs.append(Path(xdg) / "legendary")

    dirs.append(Path.home() / ".config" / "legendary")

    # Legendary bundled inside Heroic.
    for heroic in _heroic_dirs():
        dirs.append(heroic / "legendaryConfig" / "legendary")

    # De-dup preserving order.
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d).lower()
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def _heroic_dirs() -> list[Path]:
    override = config.heroic_config_override()
    if override:
        return [Path(override).expanduser()]

    home = Path.home()
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "heroic"]
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return [base / "heroic"]
    return [home / ".config" / "heroic"]


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def read_legendary_user() -> dict | None:
    """Return the first valid Legendary ``user.json`` (OAuth data) found, or None."""
    for d in _legendary_dirs():
        data = _read_json(d / "user.json")
        if data and data.get("access_token"):
            return data
    return None


def read_legendary_installed() -> dict[str, dict]:
    """Return a map of ``app_name -> InstalledGame dict`` from Legendary/Heroic installed.json.

    Covers both standalone Legendary and Heroic's bundled Legendary (see ``_legendary_dirs``),
    which share the same on-disk format. Empty when neither is installed here (install status
    simply unknown, not "none"). For the official Epic launcher, see ``read_official_installed``.
    """
    merged: dict[str, dict] = {}
    for d in _legendary_dirs():
        for fname in ("installed.json", "third-party-installed.json"):
            data = _read_json(d / fname)
            if isinstance(data, dict):
                # Later (less-specific) dirs must not overwrite earlier hits.
                for app_name, info in data.items():
                    merged.setdefault(app_name, info)
    return merged


def _official_manifest_dirs() -> list[Path]:
    """Candidate dirs for the OFFICIAL Epic Games Launcher's *.item manifests."""
    override = config.epic_manifests_override()
    if override:
        return [Path(override).expanduser()]

    if sys.platform.startswith("win"):
        programdata = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [Path(programdata) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"]
    if sys.platform == "darwin":
        return [
            Path.home()
            / "Library" / "Application Support"
            / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests",
            Path("/Users/Shared/Epic/EpicGamesLauncher/Data/Manifests"),
        ]
    return []  # official launcher isn't native on Linux


def read_official_installed() -> dict[str, dict]:
    """Installed games from the official Epic Games Launcher (*.item JSON manifests).

    Keyed by ``AppName`` (the same identifier the library/assets API returns), so it merges
    cleanly with owned-game records. Incomplete/in-progress installs are treated as not-installed.
    """
    out: dict[str, dict] = {}
    for d in _official_manifest_dirs():
        if not d.is_dir():
            continue
        for item in d.glob("*.item"):
            data = _read_json(item)
            if not isinstance(data, dict):
                continue
            app_name = data.get("AppName")
            if not app_name or data.get("bIsIncompleteInstall"):
                continue
            out[app_name] = {
                "installed": True,
                "install_path": data.get("InstallLocation"),
                "install_size": data.get("InstallSize"),
                "version": data.get("AppVersionString"),
                "platform": "Windows" if sys.platform.startswith("win") else "Mac",
                "title": data.get("DisplayName"),
                "source": "epic-launcher",
            }
    return out


def installed_summary() -> dict[str, dict]:
    """Compact per-game install info keyed by app_name, merging every local source.

    Sources: Legendary/Heroic ``installed.json`` and the official Epic Launcher manifests.
    The official launcher wins on conflict, since that's the mainstream client.
    """
    out: dict[str, dict] = {}
    for app_name, info in read_legendary_installed().items():
        out[app_name] = {
            "installed": True,
            "install_path": info.get("install_path"),
            "install_size": info.get("install_size"),
            "version": info.get("version"),
            "platform": info.get("platform"),
            "title": info.get("title"),
            "source": "legendary/heroic",
        }
    # Official Epic Games Launcher manifests take precedence.
    out.update(read_official_installed())
    return out
