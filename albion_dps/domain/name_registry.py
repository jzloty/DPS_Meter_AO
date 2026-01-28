from __future__ import annotations

from dataclasses import dataclass, field

from albion_dps.models import PhotonMessage
from albion_dps.protocol.protocol16 import Protocol16Error, decode_event_data

NAME_EVENT_CODE = 1
NAME_ID_KEY = 0
NAME_VALUE_KEY = 1
NAME_SUBTYPE_KEY = 252
NAME_SUBTYPE_ID_NAME = 275
NAME_SUBTYPE_NAME_KEY = 2
NAME_SUBTYPE_ENTITY_NAME = 166
NAME_SUBTYPE_ENTITY_ID_KEY = 0
NAME_SUBTYPE_ENTITY_ALT_ID_KEY = 4
NAME_SUBTYPE_ENTITY_NAME_KEY = 5
NAME_SUBTYPE_UNIT_INFO = 29
NAME_SUBTYPE_UNIT_NAME_KEY = 1
NAME_SUBTYPE_CHARACTER_INFO = 30
NAME_SUBTYPE_CHARACTER_NAME_KEY = 5
NAME_SUBTYPE_EQUIPMENT = 90
NAME_EQUIPMENT_ENTITY_ID_KEY = 0
NAME_EQUIPMENT_ITEM_LIST_KEY = 2
NAME_EQUIPMENT_MIN_MATCHES = 3
NAME_EQUIPMENT_MIN_RATIO = 2.0


@dataclass
class NameRegistry:
    _names: dict[int, str] = field(default_factory=dict)
    _guid_names: dict[bytes, str] = field(default_factory=dict)
    _id_guids: dict[int, bytes] = field(default_factory=dict)
    _strong_name_ids: dict[str, set[int]] = field(default_factory=dict)
    _weak_name_ids: dict[str, set[int]] = field(default_factory=dict)
    _strong_id_names: dict[int, str] = field(default_factory=dict)
    _item_names: dict[int, set[str]] = field(default_factory=dict)
    _entity_items: dict[int, list[int]] = field(default_factory=dict)

    def observe(self, message: PhotonMessage) -> None:
        if message.event_code is None:
            return
        if message.event_code != NAME_EVENT_CODE:
            return
        try:
            event = decode_event_data(message.payload)
        except Protocol16Error:
            return

        self._apply_event(event.parameters)

    def snapshot(self) -> dict[int, str]:
        merged = dict(self._names)
        for entity_id, guid in self._id_guids.items():
            if entity_id in merged:
                continue
            name = self._guid_names.get(guid)
            if name:
                merged[entity_id] = name
        return merged

    def lookup(self, entity_id: int) -> str | None:
        name = self._names.get(entity_id)
        if name is not None:
            return name
        guid = self._id_guids.get(entity_id)
        if guid is None:
            return None
        return self._guid_names.get(guid)

    def record(self, entity_id: int, name: str) -> None:
        self._store(entity_id, name)

    def record_weak(self, entity_id: int, name: str) -> None:
        self._store(entity_id, name, weak=True)

    def snapshot_guid_names(self) -> dict[bytes, str]:
        return dict(self._guid_names)

    def snapshot_id_guids(self) -> dict[int, bytes]:
        return dict(self._id_guids)

    def items_for(self, entity_id: int) -> list[int]:
        items = self._entity_items.get(entity_id)
        if not items:
            return []
        return list(items)

    def _apply_event(self, parameters: dict[int, object]) -> None:
        self._apply_party_roster(parameters)
        self._apply_guid_link(parameters)
        subtype = parameters.get(NAME_SUBTYPE_KEY)
        if subtype == NAME_SUBTYPE_ENTITY_NAME:
            name = parameters.get(NAME_SUBTYPE_ENTITY_NAME_KEY)
            if isinstance(name, str) and name:
                self._store(parameters.get(NAME_SUBTYPE_ENTITY_ID_KEY), name)
                self._store(parameters.get(NAME_SUBTYPE_ENTITY_ALT_ID_KEY), name)
        if subtype == NAME_SUBTYPE_UNIT_INFO:
            name = parameters.get(NAME_SUBTYPE_UNIT_NAME_KEY)
            if isinstance(name, str) and name:
                self._store(parameters.get(NAME_SUBTYPE_ENTITY_ID_KEY), name)
        if subtype == NAME_SUBTYPE_CHARACTER_INFO:
            name = parameters.get(NAME_SUBTYPE_CHARACTER_NAME_KEY)
            if isinstance(name, str) and name:
                entity_id = parameters.get(NAME_SUBTYPE_ENTITY_ID_KEY)
                self._store(entity_id, name)
                item_id = parameters.get(1)
                if isinstance(item_id, int):
                    self._item_names.setdefault(item_id, set()).add(name)
                    if isinstance(entity_id, int):
                        self._infer_name_from_items(entity_id)
                    if self._entity_items:
                        for target_id, items in list(self._entity_items.items()):
                            if item_id in items:
                                self._infer_name_from_items(target_id)
        if subtype == NAME_SUBTYPE_EQUIPMENT:
            entity_id = parameters.get(NAME_EQUIPMENT_ENTITY_ID_KEY)
            items = parameters.get(NAME_EQUIPMENT_ITEM_LIST_KEY)
            if isinstance(entity_id, int) and isinstance(items, list):
                filtered = [item for item in items if isinstance(item, int) and item > 0]
                if filtered:
                    self._entity_items[entity_id] = filtered
                    self._infer_name_from_items(entity_id)
        if subtype == NAME_SUBTYPE_ID_NAME:
            self._store(parameters.get(NAME_ID_KEY), parameters.get(NAME_SUBTYPE_NAME_KEY), weak=True)
        raw_id = parameters.get(NAME_ID_KEY)
        raw_name = parameters.get(NAME_VALUE_KEY)

        if isinstance(raw_id, list) and isinstance(raw_name, list):
            for entity_id, name in zip(raw_id, raw_name):
                self._store(entity_id, name)
            return

        self._store(raw_id, raw_name)

    def _store(self, entity_id: object, name: object, *, weak: bool = False) -> None:
        if isinstance(entity_id, int) and isinstance(name, str) and name:
            if weak:
                strong_name = self._strong_id_names.get(entity_id)
                if strong_name is not None and strong_name != name:
                    return
                strong_ids = self._strong_name_ids.get(name, set())
                if strong_ids and entity_id not in strong_ids:
                    return
                self._weak_name_ids.setdefault(name, set()).add(entity_id)
            else:
                strong_ids = self._strong_name_ids.setdefault(name, set())
                strong_ids.add(entity_id)
                self._strong_id_names[entity_id] = name
                weak_ids = self._weak_name_ids.get(name)
                if weak_ids:
                    for weak_id in list(weak_ids):
                        if weak_id in strong_ids:
                            continue
                        if self._names.get(weak_id) == name:
                            self._names.pop(weak_id, None)
                    weak_ids.intersection_update(strong_ids)
            self._names[entity_id] = name
            return
        if isinstance(entity_id, int) and _is_guid(name):
            self._id_guids[entity_id] = bytes(name)
            return
        if _is_guid(entity_id) and isinstance(name, str) and name:
            self._guid_names[bytes(entity_id)] = name

    def _apply_guid_link(self, parameters: dict[int, object]) -> None:
        guid = parameters.get(3)
        entity_id = parameters.get(1)
        if not _is_guid(guid):
            return
        if not isinstance(entity_id, int):
            return
        self._id_guids[entity_id] = bytes(guid)

    def _apply_party_roster(self, parameters: dict[int, object]) -> None:
        subtype = parameters.get(252)
        if subtype == 229:
            guids = parameters.get(5)
            names = parameters.get(6)
        elif subtype == 227:
            guids = parameters.get(12)
            names = parameters.get(13)
        else:
            return

        if not isinstance(guids, list) or not isinstance(names, list):
            return
        for guid, name in zip(guids, names):
            if _is_guid(guid) and isinstance(name, str) and name:
                self._guid_names[bytes(guid)] = name

    def _infer_name_from_items(self, entity_id: int) -> None:
        items = self._entity_items.get(entity_id)
        if not items:
            return
        counts: dict[str, int] = {}
        for item_id in items:
            for name in self._item_names.get(item_id, set()):
                if not name:
                    continue
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            return
        sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        best_name, best_count = sorted_counts[0]
        second_count = sorted_counts[1][1] if len(sorted_counts) > 1 else 0
        if best_count < NAME_EQUIPMENT_MIN_MATCHES:
            return
        if second_count > 0 and (best_count / float(second_count)) < NAME_EQUIPMENT_MIN_RATIO:
            return
        current_strong = self._strong_id_names.get(entity_id)
        if current_strong is not None and current_strong != best_name:
            return
        self._store(entity_id, best_name)


def _is_guid(value: object) -> bool:
    if isinstance(value, (bytes, bytearray)) and len(value) == 16:
        return True
    return False
