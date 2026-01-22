from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from collections.abc import Iterable

from albion_dps.domain.name_registry import NameRegistry
from albion_dps.models import CombatEvent, PhotonMessage, RawPacket
from albion_dps.protocol.protocol16 import (
    Protocol16Error,
    decode_event_data,
    decode_operation_request,
)

PARTY_EVENT_CODE = 1
PARTY_SUBTYPE_KEY = 252
PARTY_SUBTYPE_NAME_KEYS = {
    227: 13,
    229: 6,
}
PARTY_SUBTYPE_ID_KEYS = {
    209: 0,
    210: 0,
}
SELF_SUBTYPE_NAME_KEYS = {
    228: 1,
    238: 0,
}
COMBAT_TARGET_SUBTYPE = 21
COMBAT_TARGET_A_KEY = 0
COMBAT_TARGET_B_KEY = 1
SERVER_PORTS = {5055, 5056, 5058}
ZONE_PORTS = {5056, 5058}
TARGET_REQUEST_OPCODE = 1
TARGET_REQUEST_ID_KEY = 5
TARGET_SELF_NAME_MIN_COUNT = 5
TARGET_SELF_NAME_MIN_RATIO = 2.0
TARGET_SELF_NAME_WINDOW_SECONDS = 60.0
TARGET_SELF_NAME_CONFIRM_COUNT = 20
SELF_ID_CANDIDATE_TTL_SECONDS = 15.0
SELF_ID_CORRELATION_WINDOW_SECONDS = 0.75
SELF_ID_MIN_SCORE = 1.0
SELF_ID_MIN_SCORE_GAP = 1.0
TARGET_LINK_WINDOW_SECONDS = 2.0
TARGET_LINK_REORDER_SECONDS = 0.15


@dataclass
class PartyRegistry:
    strict: bool = True
    _party_names: set[str] = field(default_factory=set)
    _party_ids: set[int] = field(default_factory=set)
    _resolved_party_names: set[str] = field(default_factory=set)
    _party_roster_candidates: set[int] = field(default_factory=set)
    _party_roster_self_seen: bool = False
    _combat_ids_seen: set[int] = field(default_factory=set)
    _target_ids: set[int] = field(default_factory=set)
    _self_ids: set[int] = field(default_factory=set)
    _primary_self_id: int | None = None
    _self_name: str | None = None
    _self_name_confirmed: bool = False
    _recent_target_ids: deque[tuple[float, int]] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    _recent_outbound_ts: deque[float] = field(default_factory=lambda: deque(maxlen=500))
    _target_request_ts: dict[int, float] = field(default_factory=dict)
    _self_candidate_scores: dict[int, float] = field(default_factory=dict)
    _self_candidate_last_ts: dict[int, float] = field(default_factory=dict)
    _self_candidate_link_hits: dict[int, int] = field(default_factory=dict)
    _self_candidate_combat_hits: dict[int, int] = field(default_factory=dict)
    _recent_target_links: deque[tuple[float, int, int]] = field(
        default_factory=lambda: deque(maxlen=500)
    )
    _last_packet_fingerprint: tuple[float, str, int, str, int, int] | None = None
    _zone_key: tuple[str, int] | None = None

    def observe(self, message: PhotonMessage, packet: RawPacket | None = None) -> None:
        if packet is not None:
            self._observe_packet_once(packet)
            self._apply_target_request(message, packet)
        if message.event_code is None or message.event_code != PARTY_EVENT_CODE:
            return
        try:
            event = decode_event_data(message.payload)
        except Protocol16Error:
            return

        subtype = event.parameters.get(PARTY_SUBTYPE_KEY)
        if not isinstance(subtype, int):
            return
        if subtype == COMBAT_TARGET_SUBTYPE:
            self._apply_target_link(event.parameters, packet)
            return
        id_key = PARTY_SUBTYPE_ID_KEYS.get(subtype)
        if id_key is not None:
            if self._party_names:
                return
            entity_id = event.parameters.get(id_key)
            if isinstance(entity_id, int):
                self._party_roster_candidates.add(entity_id)
                if entity_id in self._self_ids:
                    self._party_roster_self_seen = True
                self._promote_roster_candidates()
            return
        name_key = PARTY_SUBTYPE_NAME_KEYS.get(subtype)
        if name_key is None:
            name_key = SELF_SUBTYPE_NAME_KEYS.get(subtype)
        if name_key is None:
            return
        names = _coerce_names(event.parameters.get(name_key))
        if not names:
            return
        if subtype in SELF_SUBTYPE_NAME_KEYS:
            self.set_self_name(names[0], confirmed=True)
            return
        self._party_names.update(names)
        self._resolved_party_names.clear()
        self._party_roster_candidates.clear()
        self._party_roster_self_seen = False
        if self._self_ids:
            self._party_ids.intersection_update(self._self_ids)
        else:
            self._party_ids.clear()

    def observe_packet(self, packet: RawPacket) -> None:
        self._last_packet_fingerprint = (
            packet.timestamp,
            packet.src_ip,
            packet.src_port,
            packet.dst_ip,
            packet.dst_port,
            len(packet.payload),
        )
        self._update_zone_key(packet)
        if packet.dst_port in ZONE_PORTS and packet.src_port not in SERVER_PORTS:
            self._recent_outbound_ts.append(packet.timestamp)
        _prune_deque(self._recent_outbound_ts, packet.timestamp, SELF_ID_CANDIDATE_TTL_SECONDS)
        _prune_deque_pairs(self._recent_target_ids, packet.timestamp, TARGET_SELF_NAME_WINDOW_SECONDS)
        _prune_deque_triples(self._recent_target_links, packet.timestamp, TARGET_LINK_WINDOW_SECONDS)
        self._prune_candidate_scores(packet.timestamp)
        cutoff = packet.timestamp - SELF_ID_CANDIDATE_TTL_SECONDS
        for target_id, ts in list(self._target_request_ts.items()):
            if ts < cutoff:
                self._target_request_ts.pop(target_id, None)

    def observe_combat_event(self, event: CombatEvent) -> None:
        if self._primary_self_id is not None:
            return
        if not isinstance(event.target_id, int) or not isinstance(event.source_id, int):
            return
        requested_ts = self._target_request_ts.get(event.target_id)
        if requested_ts is None:
            return
        if not _has_outbound_correlation(self._recent_outbound_ts, event.timestamp):
            return
        self._add_self_candidate_score(event.source_id, event.timestamp, weight=1.0)
        self._self_candidate_combat_hits[event.source_id] = (
            self._self_candidate_combat_hits.get(event.source_id, 0) + 1
        )

    def try_resolve_self_id(self, name_registry: NameRegistry | None = None) -> None:
        if self._primary_self_id is not None:
            return
        self._prune_candidate_scores(None)
        if not self._self_candidate_scores:
            return

        if (
            name_registry is not None
            and self._self_name_confirmed
            and self._self_name
        ):
            matches = [
                entity_id
                for entity_id in self._self_candidate_scores.keys()
                if name_registry.lookup(entity_id) == self._self_name
            ]
            if len(matches) == 1:
                if (
                    self._self_candidate_link_hits.get(matches[0], 0) > 0
                    and self._self_candidate_combat_hits.get(matches[0], 0) > 0
                ):
                    self._accept_self_id_candidate(matches[0])
                return

        best_id, best_score = max(self._self_candidate_scores.items(), key=lambda item: item[1])
        second_score = max(
            (score for entity_id, score in self._self_candidate_scores.items() if entity_id != best_id),
            default=0.0,
        )
        if best_score >= SELF_ID_MIN_SCORE and (best_score - second_score) >= SELF_ID_MIN_SCORE_GAP:
            if self._self_candidate_combat_hits.get(best_id, 0) <= 0:
                return
            self._accept_self_id_candidate(best_id)

    def seed_names(self, names: Iterable[str]) -> None:
        for name in names:
            if isinstance(name, str) and name:
                self._party_names.add(name)

    def seed_ids(self, ids: Iterable[int]) -> None:
        for entity_id in ids:
            if isinstance(entity_id, int):
                self._party_ids.add(entity_id)

    def seed_self_ids(self, ids: Iterable[int]) -> None:
        for entity_id in ids:
            if isinstance(entity_id, int):
                self._party_ids.add(entity_id)
                self._self_ids.add(entity_id)
                if self._primary_self_id is None:
                    self._primary_self_id = entity_id
        self._promote_roster_candidates()

    def set_self_name(self, name: str, *, confirmed: bool = False) -> None:
        if not isinstance(name, str) or not name:
            return
        if confirmed:
            self._self_name = name
            self._self_name_confirmed = True
            return
        if self._self_name_confirmed:
            return
        if self._self_name is None:
            self._self_name = name

    def snapshot_names(self) -> set[str]:
        return set(self._party_names)

    def snapshot_ids(self) -> set[int]:
        return set(self._party_ids)

    def snapshot_self_ids(self) -> set[int]:
        return set(self._self_ids)

    def has_ids(self) -> bool:
        if self.strict:
            return bool(self._self_ids)
        return bool(self._party_ids)

    def has_unresolved_names(self) -> bool:
        if not self._party_names:
            return False
        return bool(self._party_names.difference(self._resolved_party_names))

    def sync_names(self, name_registry: NameRegistry) -> None:
        if not self._party_names:
            return
        snapshot = name_registry.snapshot()
        mapped_ids: set[int] = set()
        for entity_id, name in snapshot.items():
            if name not in self._party_names:
                continue
            if not isinstance(entity_id, int) or entity_id <= 0:
                continue
            if entity_id not in self._combat_ids_seen and entity_id not in self._self_ids:
                continue
            mapped_ids.add(entity_id)
            self._resolved_party_names.add(name)
        if mapped_ids:
            self._party_ids.update(mapped_ids)

    def infer_self_name_from_targets(self, name_registry: NameRegistry) -> None:
        if self._self_name_confirmed:
            return
        if not self._recent_target_ids:
            return

        last_ts = self._recent_target_ids[-1][0]
        cutoff = last_ts - TARGET_SELF_NAME_WINDOW_SECONDS
        counts: dict[str, int] = {}
        distinct_ids: dict[str, set[int]] = {}
        for ts, entity_id in self._recent_target_ids:
            if ts < cutoff:
                continue
            name = name_registry.lookup(entity_id)
            if not name or name == "SYSTEM":
                continue
            counts[name] = counts.get(name, 0) + 1
            distinct_ids.setdefault(name, set()).add(entity_id)
        if not counts:
            return

        sorted_counts = sorted(counts.items(), key=lambda item: item[1], reverse=True)
        best_name, best_count = sorted_counts[0]
        second_count = sorted_counts[1][1] if len(sorted_counts) > 1 else 0
        if best_count < TARGET_SELF_NAME_MIN_COUNT:
            return
        if second_count > 0 and (best_count / float(second_count)) < TARGET_SELF_NAME_MIN_RATIO:
            return
        confirm = best_count >= TARGET_SELF_NAME_CONFIRM_COUNT or len(distinct_ids.get(best_name, set())) >= 2
        if self._self_name is not None and self._self_name != best_name:
            return
        self.set_self_name(best_name, confirmed=confirm)

    def sync_id_names(self, name_registry: NameRegistry) -> None:
        if not self._self_ids:
            return
        if not self._self_name or not self._self_name_confirmed:
            return
        for entity_id in self._self_ids:
            current = name_registry.lookup(entity_id)
            if current is not None and current != self._self_name:
                continue
            if hasattr(name_registry, "record_weak"):
                name_registry.record_weak(entity_id, self._self_name)
            else:
                name_registry.record(entity_id, self._self_name)

    def allows(self, source_id: int, name_registry: NameRegistry | None = None) -> bool:
        if not isinstance(source_id, int):
            return False
        self._combat_ids_seen.add(source_id)
        if self.strict:
            if not self._self_ids:
                return False
            return source_id in self._party_ids or source_id in self._self_ids
        if self._party_ids:
            return source_id in self._party_ids
        if not self._party_names or name_registry is None:
            return True
        name = name_registry.lookup(source_id)
        return name is not None and name in self._party_names

    def _apply_target_request(self, message: PhotonMessage, packet: RawPacket) -> None:
        if message.event_code is not None:
            return
        if packet.dst_port not in ZONE_PORTS:
            return
        try:
            request = decode_operation_request(message.payload)
        except Protocol16Error:
            return
        if request.code != TARGET_REQUEST_OPCODE:
            return
        entity_id = request.parameters.get(TARGET_REQUEST_ID_KEY)
        if isinstance(entity_id, int):
            self._target_ids.add(entity_id)
            self._recent_target_ids.append((packet.timestamp, entity_id))
            self._target_request_ts[entity_id] = packet.timestamp
            self._apply_target_link_hint_from_recent_links(entity_id, packet.timestamp)

    def _apply_target_link(self, parameters: dict[int, object], packet: RawPacket | None) -> None:
        first = parameters.get(COMBAT_TARGET_A_KEY)
        second = parameters.get(COMBAT_TARGET_B_KEY)
        if not isinstance(first, int) or not isinstance(second, int):
            return
        ts = packet.timestamp if packet is not None else 0.0
        self._recent_target_links.append((ts, first, second))
        if not self._target_ids:
            return
        self._apply_target_link_hint(first, second, ts)

    def _apply_target_link_hint_from_recent_links(self, target_id: int, ts: float) -> None:
        for link_ts, first, second in reversed(self._recent_target_links):
            if (ts - link_ts) > TARGET_LINK_WINDOW_SECONDS:
                break
            if (ts - link_ts) > TARGET_LINK_REORDER_SECONDS:
                continue
            if first == target_id and second != target_id:
                self._apply_target_link_hint(first, second, ts)
            elif second == target_id and first != target_id:
                self._apply_target_link_hint(first, second, ts)

    def _apply_target_link_hint(self, first: int, second: int, ts: float) -> None:
        if first in self._target_ids and second not in self._target_ids:
            candidate = second
        elif second in self._target_ids and first not in self._target_ids:
            candidate = first
        else:
            return
        self._add_self_candidate_score(candidate, ts, weight=0.5)
        self._self_candidate_link_hits[candidate] = self._self_candidate_link_hits.get(candidate, 0) + 1

    def _accept_self_id_candidate(self, candidate_id: int) -> None:
        if not isinstance(candidate_id, int):
            return
        if self._primary_self_id is None:
            self._primary_self_id = candidate_id
            self._self_ids.add(candidate_id)
            self._party_ids.add(candidate_id)
            if candidate_id in self._party_roster_candidates:
                self._party_roster_self_seen = True
            self._promote_roster_candidates()
            return
        if candidate_id != self._primary_self_id:
            return
        self._self_ids.add(candidate_id)
        self._party_ids.add(candidate_id)
        if candidate_id in self._party_roster_candidates:
            self._party_roster_self_seen = True
        self._promote_roster_candidates()

    def _promote_roster_candidates(self) -> None:
        if not self._party_roster_candidates:
            return
        if not self._party_roster_self_seen and self._self_ids:
            if any(entity_id in self._self_ids for entity_id in self._party_roster_candidates):
                self._party_roster_self_seen = True
        if not self._party_roster_self_seen:
            return
        self._party_ids.update(self._party_roster_candidates)

    def _add_self_candidate_score(self, candidate_id: int, ts: float, *, weight: float) -> None:
        if not isinstance(candidate_id, int):
            return
        current = float(self._self_candidate_scores.get(candidate_id, 0.0))
        self._self_candidate_scores[candidate_id] = current + float(weight)
        self._self_candidate_last_ts[candidate_id] = float(ts)

    def _prune_candidate_scores(self, now: float | None) -> None:
        if now is None:
            if self._self_candidate_last_ts:
                now = max(self._self_candidate_last_ts.values())
            else:
                return
        cutoff = now - SELF_ID_CANDIDATE_TTL_SECONDS
        for entity_id, ts in list(self._self_candidate_last_ts.items()):
            if ts < cutoff:
                self._self_candidate_last_ts.pop(entity_id, None)
                self._self_candidate_scores.pop(entity_id, None)
                self._self_candidate_link_hits.pop(entity_id, None)
                self._self_candidate_combat_hits.pop(entity_id, None)

    def _observe_packet_once(self, packet: RawPacket) -> None:
        fingerprint = (
            packet.timestamp,
            packet.src_ip,
            packet.src_port,
            packet.dst_ip,
            packet.dst_port,
            len(packet.payload),
        )
        if fingerprint == self._last_packet_fingerprint:
            return
        self._last_packet_fingerprint = fingerprint
        self.observe_packet(packet)

    def _update_zone_key(self, packet: RawPacket) -> None:
        zone_key = _infer_zone_key(packet)
        if zone_key is None:
            return
        if self._zone_key is None:
            self._zone_key = zone_key
            return
        if zone_key != self._zone_key:
            self._zone_key = zone_key
            self._target_ids.clear()
            self._recent_target_ids.clear()
            self._recent_outbound_ts.clear()
            self._target_request_ts.clear()
            self._self_candidate_scores.clear()
            self._self_candidate_last_ts.clear()
            self._self_candidate_link_hits.clear()
            self._self_candidate_combat_hits.clear()
            self._party_ids.difference_update(self._self_ids)
            self._self_ids.clear()
            self._primary_self_id = None
            self._party_roster_candidates.clear()
            self._party_roster_self_seen = False
            self._combat_ids_seen.clear()


def _infer_zone_key(packet: RawPacket) -> tuple[str, int] | None:
    if packet.src_port in ZONE_PORTS:
        return packet.src_ip, packet.src_port
    if packet.dst_port in ZONE_PORTS:
        return packet.dst_ip, packet.dst_port
    return None


def _coerce_names(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _prune_deque(values: deque[float], now: float, window_seconds: float) -> None:
    cutoff = now - window_seconds
    while values and values[0] < cutoff:
        values.popleft()


def _prune_deque_pairs(values: deque[tuple[float, int]], now: float, window_seconds: float) -> None:
    cutoff = now - window_seconds
    while values and values[0][0] < cutoff:
        values.popleft()


def _prune_deque_triples(values: deque[tuple[float, int, int]], now: float, window_seconds: float) -> None:
    cutoff = now - window_seconds
    while values and values[0][0] < cutoff:
        values.popleft()


def _has_outbound_correlation(outbound_ts: deque[float], event_ts: float) -> bool:
    for ts in reversed(outbound_ts):
        if ts > event_ts:
            continue
        if (event_ts - ts) <= SELF_ID_CORRELATION_WINDOW_SECONDS:
            return True
        break
    return False
