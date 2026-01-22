from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque

from albion_dps.meter.aggregate import RollingMeter
from albion_dps.models import CombatEvent, MeterSnapshot, RawPacket

ZONE_PORTS = {5056, 5058}
COMBAT_END_GRACE_SECONDS = 0.25


@dataclass(frozen=True)
class SessionEntry:
    label: str
    damage: float
    heal: float
    dps: float
    hps: float


@dataclass(frozen=True)
class SessionSummary:
    mode: str
    start_ts: float
    end_ts: float
    duration: float
    label: str | None
    entries: list[SessionEntry]
    total_damage: float
    total_heal: float
    reason: str


@dataclass
class SessionMeter:
    window_seconds: float = 10.0
    battle_timeout_seconds: float = 20.0
    history_limit: int = 10
    mode: str = "battle"
    name_lookup: Callable[[int], str | None] | None = None
    _history: dict[str, Deque[SessionSummary]] = field(
        default_factory=lambda: {
            "battle": deque(maxlen=10),
            "zone": deque(maxlen=10),
            "manual": deque(maxlen=10),
        }
    )
    _meter: RollingMeter = field(init=False)
    _session_start: float | None = None
    _last_event_ts: float | None = None
    _last_seen_ts: float | None = None
    _active: bool = False
    _manual_active: bool = False
    _zone_key: tuple[str, int] | None = None
    _zone_label: str | None = None
    _combatants: set[int] = field(default_factory=set)
    _seen_sources: set[int] = field(default_factory=set)
    _combat_end_ts: float | None = None
    _last_combat_event_ts: float | None = None
    _saw_combat_state: bool = False

    def __post_init__(self) -> None:
        self._meter = RollingMeter(window_seconds=self.window_seconds, session_timeout_seconds=None)
        for key, entries in self._history.items():
            if entries.maxlen != self.history_limit:
                self._history[key] = deque(entries, maxlen=self.history_limit)

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        self._end_session(self._last_seen_ts or self._last_event_ts or 0.0, "mode_change")
        self.mode = mode
        self._manual_active = False
        self._combatants.clear()
        self._combat_end_ts = None
        self._last_combat_event_ts = None
        self._saw_combat_state = False
        if self.mode == "zone" and self._zone_key is not None:
            self._start_session(self._last_seen_ts or 0.0)

    def toggle_manual(self) -> bool:
        if self.mode != "manual":
            return False
        if self._manual_active:
            self._manual_active = False
            self._end_session(self._last_seen_ts or self._last_event_ts or 0.0, "manual_stop")
            return False
        self._manual_active = True
        self._start_session(self._last_seen_ts or 0.0)
        return True

    def end_session(self) -> None:
        self._end_session(self._last_seen_ts or self._last_event_ts or 0.0, "manual_end")

    def finalize(self) -> None:
        if not self._active:
            return
        end_ts = self._last_seen_ts or self._last_event_ts or 0.0
        if self.mode == "battle":
            if self._combat_end_ts is not None:
                if self._last_event_ts is not None and self._last_event_ts > self._combat_end_ts:
                    end_ts = self._last_event_ts
                else:
                    end_ts = self._combat_end_ts
                self._end_session(end_ts, "combat_state")
                return
            if (
                self._last_combat_event_ts is not None
                and end_ts - self._last_combat_event_ts >= self.battle_timeout_seconds
            ):
                self._end_session(end_ts, "idle")
                return
        self._end_session(end_ts, "stream_end")

    def observe_packet(self, packet: RawPacket) -> None:
        self._last_seen_ts = packet.timestamp
        zone_key = _infer_zone_key(packet)
        if zone_key is not None:
            if self._zone_key is None:
                self._zone_key = zone_key
                self._zone_label = f"{zone_key[0]}:{zone_key[1]}"
                if self.mode == "zone":
                    self._start_session(packet.timestamp)
            elif zone_key != self._zone_key:
                previous_label = self._zone_label
                self._zone_key = zone_key
                self._zone_label = f"{zone_key[0]}:{zone_key[1]}"
                if self.mode == "zone":
                    if self._active:
                        self._end_session(
                            packet.timestamp, "zone_change", label_override=previous_label
                        )
                    self._start_session(packet.timestamp)
            elif zone_key != self._zone_key:
                self._zone_key = zone_key
                self._zone_label = f"{zone_key[0]}:{zone_key[1]}"

        last_activity_ts = self._last_combat_event_ts
        if (
            self.mode == "battle"
            and self._active
            and last_activity_ts is not None
            and packet.timestamp - last_activity_ts >= self.battle_timeout_seconds
        ):
            self._end_session(packet.timestamp, "idle")

        if (
            self.mode == "battle"
            and self._active
            and self._combat_end_ts is not None
            and packet.timestamp - self._combat_end_ts >= COMBAT_END_GRACE_SECONDS
        ):
            end_ts = self._combat_end_ts
            if self._last_event_ts is not None and self._last_event_ts > end_ts:
                end_ts = self._last_event_ts
            self._end_session(end_ts, "combat_state")

        if self._active:
            self._meter.touch(packet.timestamp)

    def push(self, event: CombatEvent) -> None:
        if self.mode == "manual" and not self._manual_active:
            return
        if not self._active:
            self._start_session(event.timestamp)
        if self._last_event_ts is None or event.timestamp > self._last_event_ts:
            self._last_event_ts = event.timestamp
        if self._last_seen_ts is None or event.timestamp > self._last_seen_ts:
            self._last_seen_ts = event.timestamp
        if self._combat_end_ts is not None:
            if event.timestamp - self._combat_end_ts > COMBAT_END_GRACE_SECONDS:
                self._combat_end_ts = None
        self._seen_sources.add(event.source_id)
        if self._saw_combat_state:
            if event.kind == "damage" or (
                event.kind == "heal" and event.source_id != event.target_id
            ):
                if (
                    self._last_combat_event_ts is None
                    or event.timestamp > self._last_combat_event_ts
                ):
                    self._last_combat_event_ts = event.timestamp
        else:
            if (
                self._last_combat_event_ts is None
                or event.timestamp > self._last_combat_event_ts
            ):
                self._last_combat_event_ts = event.timestamp
        self._meter.push(event)

    def observe_combat_state(
        self, entity_id: int, in_active: bool, in_passive: bool, timestamp: float
    ) -> None:
        if self.mode != "battle":
            return
        if not self._seen_sources or entity_id not in self._seen_sources:
            return
        self._saw_combat_state = True
        if self._last_seen_ts is None or timestamp > self._last_seen_ts:
            self._last_seen_ts = timestamp
        in_combat = in_active or in_passive
        if in_combat:
            self._combatants.add(entity_id)
            self._combat_end_ts = None
            if not self._active:
                self._start_session(timestamp)
            return
        if entity_id in self._combatants:
            self._combatants.remove(entity_id)
        if not self._combatants:
            self._combat_end_ts = timestamp

    def snapshot(self) -> MeterSnapshot:
        if not self._active:
            return MeterSnapshot(timestamp=self._last_seen_ts or 0.0, totals={})
        now = self._last_seen_ts or self._last_event_ts
        return self._meter.snapshot(now=now)

    def history(self, limit: int | None = None) -> list[SessionSummary]:
        entries = list(reversed(self._history.get(self.mode, deque())))
        if limit is None or limit <= 0:
            return entries
        return entries[:limit]

    def merge_event_into_history(self, event: CombatEvent) -> bool:
        history = self._history.get(self.mode)
        if not history:
            return False
        label = self.name_lookup(event.source_id) if self.name_lookup else None
        if not label:
            label = str(event.source_id)
        for idx in range(len(history) - 1, -1, -1):
            summary = history[idx]
            if event.timestamp < summary.start_ts or event.timestamp > summary.end_ts:
                continue
            grouped: dict[str, tuple[float, float]] = {
                entry.label: (entry.damage, entry.heal) for entry in summary.entries
            }
            damage, heal = grouped.get(label, (0.0, 0.0))
            if event.kind == "damage":
                damage += event.amount
            else:
                heal += event.amount
            grouped[label] = (damage, heal)
            entries = _build_entries_from_grouped(grouped, summary.duration)
            total_damage = sum(entry.damage for entry in entries)
            total_heal = sum(entry.heal for entry in entries)
            history[idx] = SessionSummary(
                mode=summary.mode,
                start_ts=summary.start_ts,
                end_ts=summary.end_ts,
                duration=summary.duration,
                label=summary.label,
                entries=entries,
                total_damage=total_damage,
                total_heal=total_heal,
                reason=summary.reason,
            )
            return True
        return False

    def refresh_history_labels(self) -> bool:
        if self.name_lookup is None:
            return False
        history = self._history.get(self.mode)
        if not history:
            return False
        changed = False
        for idx in range(len(history)):
            summary = history[idx]
            grouped: dict[str, tuple[float, float]] = {}
            changed_local = False
            for entry in summary.entries:
                label = entry.label
                if label.isdigit():
                    mapped = self.name_lookup(int(label))
                    if mapped:
                        label = mapped
                        changed_local = True
                if label in grouped:
                    changed_local = True
                damage, heal = grouped.get(label, (0.0, 0.0))
                grouped[label] = (damage + entry.damage, heal + entry.heal)
            entries = _build_entries_from_grouped(grouped, summary.duration)
            total_damage = sum(entry.damage for entry in entries)
            total_heal = sum(entry.heal for entry in entries)
            if changed_local:
                changed = True
            history[idx] = SessionSummary(
                mode=summary.mode,
                start_ts=summary.start_ts,
                end_ts=summary.end_ts,
                duration=summary.duration,
                label=summary.label,
                entries=entries,
                total_damage=total_damage,
                total_heal=total_heal,
                reason=summary.reason,
            )
        return changed

    def manual_active(self) -> bool:
        return self._manual_active

    def zone_label(self) -> str | None:
        return self._zone_label

    def _start_session(self, timestamp: float) -> None:
        self._meter = RollingMeter(window_seconds=self.window_seconds, session_timeout_seconds=None)
        self._session_start = timestamp
        self._last_event_ts = None
        self._active = True
        self._combat_end_ts = None
        self._last_combat_event_ts = None
        self._seen_sources.clear()

    def _end_session(
        self,
        timestamp: float,
        reason: str,
        *,
        label_override: str | None = None,
    ) -> None:
        if not self._active:
            return
        start_ts = self._session_start or timestamp
        end_ts = timestamp
        duration = max(end_ts - start_ts, 0.0)
        snapshot = self._meter.snapshot(now=end_ts)
        entries = _build_entries(snapshot, duration, self.name_lookup)
        if not entries:
            self._meter = RollingMeter(window_seconds=self.window_seconds, session_timeout_seconds=None)
            self._session_start = None
            self._last_event_ts = None
            self._active = False
            return
        total_damage = sum(entry.damage for entry in entries)
        total_heal = sum(entry.heal for entry in entries)
        summary = SessionSummary(
            mode=self.mode,
            start_ts=start_ts,
            end_ts=end_ts,
            duration=duration,
            label=(label_override if label_override is not None else self._zone_label)
            if self.mode == "zone"
            else None,
            entries=entries,
            total_damage=total_damage,
            total_heal=total_heal,
            reason=reason,
        )
        self._history.setdefault(self.mode, deque(maxlen=self.history_limit)).append(summary)
        self._meter = RollingMeter(window_seconds=self.window_seconds, session_timeout_seconds=None)
        self._session_start = None
        self._last_event_ts = None
        self._active = False
        self._combat_end_ts = None
        self._combatants.clear()
        self._last_combat_event_ts = None
        self._seen_sources.clear()


def _build_entries(
    snapshot: MeterSnapshot,
    duration: float,
    name_lookup: Callable[[int], str | None] | None,
) -> list[SessionEntry]:
    grouped: dict[str, tuple[float, float]] = {}
    for source_id, stats in snapshot.totals.items():
        damage = float(stats.get("damage", 0.0))
        heal = float(stats.get("heal", 0.0))
        label = name_lookup(source_id) if name_lookup else None
        if not label:
            label = str(source_id)
        current_damage, current_heal = grouped.get(label, (0.0, 0.0))
        grouped[label] = (current_damage + damage, current_heal + heal)

    return _build_entries_from_grouped(grouped, duration)


def _build_entries_from_grouped(
    grouped: dict[str, tuple[float, float]], duration: float
) -> list[SessionEntry]:
    entries: list[SessionEntry] = []
    for label, (damage, heal) in grouped.items():
        if duration > 0:
            dps = damage / duration
            hps = heal / duration
        else:
            dps = 0.0
            hps = 0.0
        entries.append(
            SessionEntry(
                label=label,
                damage=damage,
                heal=heal,
                dps=dps,
                hps=hps,
            )
        )
    entries.sort(key=lambda item: item.damage, reverse=True)
    return entries


def _infer_zone_key(packet: RawPacket) -> tuple[str, int] | None:
    if packet.src_port in ZONE_PORTS:
        return packet.src_ip, packet.src_port
    if packet.dst_port in ZONE_PORTS:
        return packet.dst_ip, packet.dst_port
    return None
