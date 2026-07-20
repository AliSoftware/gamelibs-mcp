"""Offline tests for Steam local install detection (VDF/ACF parsing, StateFlags)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import vdf

from gamelibs_mcp.steam import local

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_manifest_fully_installed(tmp_path: Path):
    steamapps = tmp_path / "steamapps"
    steamapps.mkdir()
    acf = steamapps / "appmanifest_440.acf"
    shutil.copy(FIXTURES / "appmanifest_440.acf", acf)

    app = local._parse_manifest(acf, tmp_path)
    assert app is not None
    assert app.appid == 440
    assert app.name == "Team Fortress 2"
    assert app.state_flags == 4
    assert app.fully_installed is True
    assert app.size_on_disk == 24309708288
    assert app.install_dir.endswith("common/Team Fortress 2") or app.install_dir.endswith(
        "common\\Team Fortress 2"
    )


def test_parse_manifest_updating_is_not_fully_installed(tmp_path: Path):
    steamapps = tmp_path / "steamapps"
    steamapps.mkdir()
    acf = steamapps / "appmanifest_570.acf"
    shutil.copy(FIXTURES / "appmanifest_570.acf", acf)

    app = local._parse_manifest(acf, tmp_path)
    assert app is not None
    assert app.appid == 570
    # 1026 = 1024|2 -> update pending, bit 2 (value 4) not set.
    assert app.fully_installed is False


@pytest.mark.parametrize("fixture", ["libraryfolders_new.vdf", "libraryfolders_old.vdf"])
def test_libraryfolders_formats_parse(fixture: str):
    data = vdf.load((FIXTURES / fixture).open(encoding="utf-8"))
    folders = data.get("libraryfolders") or data.get("LibraryFolders") or {}
    numeric = {k: v for k, v in folders.items() if k.isdigit()}
    assert numeric, "expected at least one numbered library entry"
    for entry in numeric.values():
        path = entry.get("path") if isinstance(entry, dict) else entry
        assert path  # both formats yield a usable path


def test_scan_installed_across_libraries(tmp_path: Path, monkeypatch):
    # Build a fake Steam root with two library folders (new-format libraryfolders.vdf).
    steam_root = tmp_path / "Steam"
    lib_a = steam_root / "steamapps"
    lib_b = tmp_path / "SteamLibrary" / "steamapps"
    lib_a.mkdir(parents=True)
    lib_b.mkdir(parents=True)
    shutil.copy(FIXTURES / "appmanifest_440.acf", lib_a / "appmanifest_440.acf")
    shutil.copy(FIXTURES / "appmanifest_570.acf", lib_b / "appmanifest_570.acf")

    lf = {
        "libraryfolders": {
            "0": {"path": str(steam_root)},
            "1": {"path": str(tmp_path / "SteamLibrary")},
        }
    }
    (lib_a / "libraryfolders.vdf").write_text(vdf.dumps(lf))

    monkeypatch.setattr(local, "find_steam_root", lambda: steam_root)
    monkeypatch.setattr(local.config, "steam_extra_library_paths", lambda: [])

    installed = local.scan_installed()
    assert set(installed) == {440, 570}
    assert installed[440].fully_installed is True
    assert installed[570].fully_installed is False
    assert installed[570].library_path.endswith("SteamLibrary")


def test_scan_installed_no_steam(monkeypatch):
    monkeypatch.setattr(local, "find_steam_root", lambda: None)
    assert local.scan_installed() == {}
