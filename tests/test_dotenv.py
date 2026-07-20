"""Verify config loads a local .env from the launch directory (integration, subprocess)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Credential vars we strip from the child env so the child only sees the temp .env.
_CRED_VARS = ("STEAM_API_KEY", "STEAM_ID", "STEAM_VANITY")


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _CRED_VARS}


def test_dotenv_is_loaded_from_cwd(tmp_path: Path):
    (tmp_path / ".env").write_text("STEAM_API_KEY=dummy_from_dotenv\n")
    script = "import gamelibs_mcp.config as c; print(c.steam_api_key())"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=_child_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "dummy_from_dotenv" in result.stdout


def test_real_env_wins_over_dotenv(tmp_path: Path):
    # A variable already set in the environment must NOT be overwritten by .env.
    (tmp_path / ".env").write_text("STEAM_API_KEY=from_dotenv\n")
    script = "import gamelibs_mcp.config as c; print(c.steam_api_key())"
    env = _child_env()
    env["STEAM_API_KEY"] = "from_real_env"
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "from_real_env" in result.stdout
