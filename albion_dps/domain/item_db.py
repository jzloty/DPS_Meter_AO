from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable


GAME_ROOT_ENV = "ALBION_DPS_GAME_ROOT"
PROMPT_DISABLE_ENV = "ALBION_DPS_DISABLE_GAME_ROOT_PROMPT"
DEFAULT_GAME_ROOT_FILE = Path("data/game_root.txt")
DEFAULT_INDEXED_PATHS = (
    Path("data/indexedItems.json"),
    Path("data/indexed_items.json"),
    Path("indexedItems.json"),
)


def ensure_item_databases(*, logger, interactive: bool = True) -> bool:
    if _has_indexed_items():
        return True
    if not interactive:
        return False
    if os.environ.get(PROMPT_DISABLE_ENV):
        return False

    game_root = _resolve_game_root(logger)
    if game_root is None:
        game_root = _prompt_game_root(logger)
        if game_root is None:
            return False
        _persist_game_root(game_root, logger)

    if not _is_valid_game_root(game_root):
        logger.warning("Selected game root is invalid: %s", game_root)
        return False

    return _run_extractor(game_root, logger=logger)


def _has_indexed_items() -> bool:
    for path in DEFAULT_INDEXED_PATHS:
        if path.exists():
            return True
    env_val = os.environ.get("ALBION_DPS_INDEXED_ITEMS")
    if env_val and Path(env_val).exists():
        return True
    return False


def _resolve_game_root(logger) -> Path | None:
    env_val = os.environ.get(GAME_ROOT_ENV)
    if env_val:
        path = Path(env_val)
        if _is_valid_game_root(path):
            return path
        logger.warning("ALBION_DPS_GAME_ROOT is invalid: %s", path)
    if DEFAULT_GAME_ROOT_FILE.exists():
        stored = DEFAULT_GAME_ROOT_FILE.read_text(encoding="utf-8").strip()
        if stored:
            path = Path(stored)
            if _is_valid_game_root(path):
                return path
            logger.warning("Stored game root is invalid: %s", path)
    return None


def _prompt_game_root(logger) -> Path | None:
    if sys.platform != "win32":
        logger.info("Game root prompt is only available on Windows.")
        return None
    if not sys.stdin.isatty():
        return None
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        logger.exception("Failed to load tkinter for folder picker.")
        return None

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory(title="Select Albion Online folder")
    root.destroy()
    if not folder:
        return None
    return Path(folder)


def _persist_game_root(path: Path, logger) -> None:
    try:
        DEFAULT_GAME_ROOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_GAME_ROOT_FILE.write_text(str(path), encoding="utf-8")
    except Exception:
        logger.exception("Failed to persist game root: %s", path)


def _is_valid_game_root(path: Path) -> bool:
    items_bin = path / "game" / "Albion-Online_Data" / "StreamingAssets" / "GameData" / "items.bin"
    localization_bin = path / "game" / "Albion-Online_Data" / "StreamingAssets" / "GameData" / "localization.bin"
    return items_bin.exists() and localization_bin.exists()


def _run_extractor(game_root: Path, *, logger) -> bool:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "tools" / "extract_items" / "run_extract_items.ps1"
    if not script.exists():
        logger.error("Extractor script missing: %s", script)
        return False

    env = os.environ.copy()
    env["DOTNET_CLI_HOME"] = str(repo_root / "artifacts" / "dotnet")
    env["DOTNET_SKIP_FIRST_TIME_EXPERIENCE"] = "1"
    env["NUGET_PACKAGES"] = str(repo_root / "artifacts" / "nuget")

    cmd = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-GameRoot",
        str(game_root),
        "-OutputDir",
        str(repo_root / "data"),
    ]
    result = subprocess.run(cmd, env=env, cwd=repo_root)
    if result.returncode != 0:
        logger.error("Extractor failed with code %s", result.returncode)
        return False
    return _has_indexed_items()
