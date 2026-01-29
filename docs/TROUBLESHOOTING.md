# Troubleshooting

## `albion-dps` is not recognized
You need both:
1) an activated virtualenv
2) the project installed (so the console script exists)

Windows / PowerShell:
```
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -e ".[capture]"
albion-dps --help
```

Alternative (no install): run from the repo checkout
```
python -m albion_dps --help
python -m albion_dps live
```

## Live mode shows "no data"
Common causes:
- You are on the wrong interface: run `albion-dps live --list-interfaces` and pick the one that carries game traffic.
- No packets yet: start the game and generate traffic (zone change / combat).
- Capture dependencies missing: install `pcapy-ng` via `python -m pip install -e ".[capture]"`.
- Windows: Npcap not installed (or installed without WinPcap API compatibility).

## I see empty results while fighting
Strict "self + party only" filtering means the meter will not aggregate anything until it can resolve "self".
If you want deterministic startup, seed self:
```
albion-dps live --self-name "YourName"
```
or
```
albion-dps live --self-id 123456
```

## Too many "unknown payload" files
Unknown payloads are saved to `artifacts/unknown/` to support protocol updates.
Log lines for unknown payloads are printed only in `--debug`, but files are still written.

## Weapon colors do not match equipped weapons
Per-weapon colors require local item databases:
- `data/indexedItems.json` (required)
- `data/items.json` (recommended)
If those files are missing, the UI falls back to role/heuristic colors.
Generate them with:
```
.\tools\extract_items\run_extract_items.ps1 -GameRoot "C:\Program Files\Albion Online"
```
Alternatively set `ALBION_DPS_GAME_ROOT` and launch the GUI to be prompted.

## Zone label shows only numbers/IP
If you see `2000@x.x.x.x:5056`, the map index database is missing.
Generate it with the same extractor (creates `data/map_index.json`), or set `ALBION_DPS_MAP_INDEX`.

## Permission issues (Windows)
Npcap capture can require elevated permissions depending on configuration.
If capture fails, try running the terminal as Administrator and ensure Npcap is installed correctly.

## Pytest fails with PermissionError in TEMP (Windows)
If pytest crashes with errors like `PermissionError` under `C:\\Users\\...\\AppData\\Local\\Temp`,
set a writable temp dir before running tests:
```
$env:TEMP="$PWD\\artifacts\\tmp"
$env:TMP=$env:TEMP
python -m pytest -q -rs
```
If you still see errors, try running the terminal as Administrator.

## `python` points to Windows Store Python
If `python --version` fails or shows a Windows Store path (`WindowsApps`),
install Python from python.org and recreate the venv:
```
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

## Qt GUI fails to load (qtquick2plugin.dll missing)
This usually means Qt's DLLs are not found:
- Ensure the venv is active and PySide6 is installed: `python -m pip install -e ".[gui-qt]"`
- Restart the terminal after install so PATH updates are picked up.
- If it still fails, install the Microsoft VC++ Redistributable (x64).
