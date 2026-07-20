"""Detect which Steam games are installed on *this* machine.

The Steam Web API cannot tell you what is installed locally — that information lives only
in Steam's on-disk files. This module locates the Steam installation (cross-platform),
enumerates all library folders from ``libraryfolders.vdf``, and parses each
``appmanifest_<appid>.acf`` to determine install state.

Because the user plays on Windows but may author elsewhere, all three OS layouts are
supported, and the server is expected to run on the gaming machine for real data.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import vdf

from .. import config

# StateFlags bit 2 (value 4) == "Fully Installed". A game mid-update keeps this bit set
# alongside others (e.g. 6 = 4|2), so we test the bit rather than equality.
STATE_FULLY_INSTALLED = 4


@dataclass
class InstalledApp:
    appid: int
    name: str
    install_dir: str  # full path to the game folder
    size_on_disk: int  # bytes
    state_flags: int
    last_updated: int | None
    last_played: int | None
    library_path: str

    @property
    def fully_installed(self) -> bool:
        return bool(self.state_flags & STATE_FULLY_INSTALLED)


def _steamapps_dir(library_root: Path) -> Path | None:
    """Return the ``steamapps`` subdir of a library root, matching case-insensitively."""
    if not library_root.is_dir():
        return None
    for name in ("steamapps", "SteamApps"):
        candidate = library_root / name
        if candidate.is_dir():
            return candidate
    # Fallback: scan for any case variant.
    for child in library_root.iterdir():
        if child.is_dir() and child.name.lower() == "steamapps":
            return child
    return None


def _windows_steam_path() -> Path | None:
    try:
        import winreg
    except ImportError:
        return None
    keys = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for root, subkey, value_name in keys:
        try:
            with winreg.OpenKey(root, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                path = Path(value)
                if path.is_dir():
                    return path
        except OSError:
            continue
    default = Path(r"C:\Program Files (x86)\Steam")
    return default if default.is_dir() else None


def find_steam_root() -> Path | None:
    """Locate the Steam install root for the current OS (or via STEAM_PATH override)."""
    override = config.steam_path_override()
    if override:
        path = Path(override).expanduser()
        return path if path.is_dir() else None

    if sys.platform.startswith("win"):
        return _windows_steam_path()

    home = Path.home()
    if sys.platform == "darwin":
        candidates = [home / "Library" / "Application Support" / "Steam"]
    else:  # linux and others
        candidates = [
            home / ".steam" / "steam",
            home / ".local" / "share" / "Steam",
            home / ".steam" / "root",
            home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
        ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def _library_roots(steam_root: Path) -> list[Path]:
    """All Steam library folders, from libraryfolders.vdf (+ extras + the root itself)."""
    roots: list[Path] = [steam_root]

    steamapps = _steamapps_dir(steam_root)
    if steamapps is not None:
        lf = steamapps / "libraryfolders.vdf"
        if lf.is_file():
            try:
                data = vdf.load(lf.open(encoding="utf-8"))
            except (OSError, SyntaxError):
                data = {}
            # Root key differs by Steam version: "libraryfolders" (new) / "LibraryFolders" (old).
            folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
            for key, entry in folders.items():
                if not key.isdigit():
                    continue  # skip metadata keys like contentstatsid/TimeNextStatsReport
                if isinstance(entry, dict):  # new format: object with "path"
                    path = entry.get("path")
                else:  # old format: the value *is* the path string
                    path = entry
                if path:
                    roots.append(Path(path))

    for extra in config.steam_extra_library_paths():
        roots.append(Path(extra).expanduser())

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        key = str(r).rstrip("/\\").lower()
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _parse_manifest(acf_path: Path, library_root: Path) -> InstalledApp | None:
    try:
        data = vdf.load(acf_path.open(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    app = data.get("AppState")
    if not isinstance(app, dict) or "appid" not in app:
        return None

    def _int(key: str) -> int | None:
        raw = app.get(key)
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    appid = _int("appid")
    if appid is None:
        return None

    install_dir_name = app.get("installdir", "")
    full_dir = ""
    steamapps = acf_path.parent
    if install_dir_name:
        full_dir = str(steamapps / "common" / install_dir_name)

    return InstalledApp(
        appid=appid,
        name=app.get("name", ""),
        install_dir=full_dir,
        size_on_disk=_int("SizeOnDisk") or 0,
        state_flags=_int("StateFlags") or 0,
        last_updated=_int("LastUpdated"),
        last_played=_int("LastPlayed"),
        library_path=str(library_root),
    )


def scan_installed() -> dict[int, InstalledApp]:
    """Return a map of ``appid -> InstalledApp`` for every app manifest found locally.

    Returns an empty dict when Steam is not installed on this machine (a valid state:
    e.g. querying owned games from a non-gaming box). Callers should treat "no local
    data" as "install status unknown", not "nothing installed".
    """
    steam_root = find_steam_root()
    if steam_root is None:
        return {}

    result: dict[int, InstalledApp] = {}
    for library_root in _library_roots(steam_root):
        steamapps = _steamapps_dir(library_root)
        if steamapps is None:
            continue
        for acf in steamapps.glob("appmanifest_*.acf"):
            app = _parse_manifest(acf, library_root)
            if app is not None:
                result[app.appid] = app
    return result


def steam_is_present() -> bool:
    """Whether a Steam installation was found on this machine."""
    return find_steam_root() is not None
