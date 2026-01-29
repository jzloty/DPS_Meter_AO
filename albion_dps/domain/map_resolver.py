from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ENV_MAP_INDEX = "ALBION_DPS_MAP_INDEX"
DEFAULT_MAP_PATHS = (
    Path("data/map_index.json"),
    Path("map_index.json"),
)

SPECIAL_MAP_TYPES = {
    "ISLAND": "Island",
    "HIDEOUT": "Hideout",
    "RANDOMDUNGEON": "Dungeon",
    "CORRUPTEDDUNGEON": "Corrupted Dungeon",
    "HELLCLUSTER": "Hellgate",
    "MISTSDUNGEON": "Mists Dungeon",
    "MISTS": "Mists",
    "HELLDUNGEON": "Abyssal Depths",
    "EXPEDITION": "Expedition",
    "ARENA": "Arena",
}


@dataclass
class MapResolver:
    index_to_name: dict[str, str] = field(default_factory=dict)

    def name_for_index(self, index: str | None) -> str | None:
        if not index:
            return None
        if index in self.index_to_name:
            return self.index_to_name[index]
        if "@" in index:
            tokens = [token for token in index.split("@") if token]
            for token in tokens:
                upper = token.upper()
                if upper in SPECIAL_MAP_TYPES:
                    return SPECIAL_MAP_TYPES[upper]
        return None


def load_map_resolver(
    *,
    path: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> MapResolver:
    logger = logger or logging.getLogger(__name__)
    resolved = _resolve_path(path)
    if not resolved:
        return MapResolver()
    data = _load_json(resolved, logger=logger)
    mapping = _coerce_mapping(data)
    if not mapping:
        logger.warning("Map index file loaded but produced no entries: %s", resolved)
    return MapResolver(index_to_name=mapping)


def _resolve_path(provided: str | Path | None) -> Path | None:
    if provided:
        path = Path(provided)
        return path if path.exists() else None
    env_val = os.environ.get(ENV_MAP_INDEX)
    if env_val:
        path = Path(env_val)
        return path if path.exists() else None
    for candidate in DEFAULT_MAP_PATHS:
        if candidate.exists():
            return candidate
    return None


def _load_json(path: Path, *, logger: logging.Logger) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load JSON: %s", path)
        return {}


def _coerce_mapping(data: Any) -> dict[str, str]:
    if isinstance(data, dict):
        return {str(k): str(v) for k, v in data.items() if v}
    if isinstance(data, list):
        mapping: dict[str, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            idx = item.get("index") or item.get("Index") or item.get("@id") or item.get("id")
            name = item.get("name") or item.get("Name") or item.get("@displayname") or item.get("displayname")
            if idx and name:
                mapping[str(idx)] = str(name)
        return mapping
    return {}
