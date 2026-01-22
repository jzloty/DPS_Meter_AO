from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Callable

from albion_dps.capture.live_capture import live_capture
from albion_dps.capture.replay_pcap import replay_pcap
from albion_dps.meter.types import Meter
from albion_dps.models import CombatEvent, MeterSnapshot, PhotonMessage, RawPacket
from albion_dps.domain.fame_tracker import FameTracker
from albion_dps.domain.name_registry import NameRegistry
from albion_dps.domain.party_registry import PartyRegistry
from albion_dps.protocol.combat_mapper import CombatEventMapper
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.protocol16 import Protocol16Error, decode_event_data

EventMapper = Callable[[PhotonMessage, RawPacket], CombatEvent | list[CombatEvent] | None]

COMBAT_STATE_EVENT_CODE = 1
COMBAT_STATE_SUBTYPE_KEY = 252
COMBAT_STATE_SUBTYPE_VALUES = {257, 274}
COMBAT_STATE_ID_KEY = 0
COMBAT_STATE_ACTIVE_KEY = 1
COMBAT_STATE_PASSIVE_KEY = 2


def replay_snapshots(
    path: str | Path,
    decoder: PhotonDecoder,
    meter: Meter,
    *,
    name_registry: NameRegistry | None = None,
    party_registry: PartyRegistry | None = None,
    fame_tracker: FameTracker | None = None,
    event_mapper: EventMapper | None = None,
    snapshot_interval: float = 1.0,
) -> Iterator[MeterSnapshot]:
    return stream_snapshots(
        replay_pcap(path),
        decoder,
        meter,
        name_registry=name_registry,
        party_registry=party_registry,
        fame_tracker=fame_tracker,
        event_mapper=event_mapper,
        snapshot_interval=snapshot_interval,
    )


def live_snapshots(
    interface: str,
    decoder: PhotonDecoder,
    meter: Meter,
    *,
    bpf_filter: str = "udp and (port 5055 or port 5056 or port 5058)",
    snaplen: int = 65535,
    promisc: bool = False,
    timeout_ms: int = 1000,
    dump_raw_dir: str | Path | None = None,
    name_registry: NameRegistry | None = None,
    party_registry: PartyRegistry | None = None,
    fame_tracker: FameTracker | None = None,
    event_mapper: EventMapper | None = None,
    snapshot_interval: float = 1.0,
) -> Iterator[MeterSnapshot]:
    packets = live_capture(
        interface,
        bpf_filter=bpf_filter,
        snaplen=snaplen,
        promisc=promisc,
        timeout_ms=timeout_ms,
        dump_raw_dir=dump_raw_dir,
    )
    return stream_snapshots(
        packets,
        decoder,
        meter,
        name_registry=name_registry,
        party_registry=party_registry,
        fame_tracker=fame_tracker,
        event_mapper=event_mapper,
        snapshot_interval=snapshot_interval,
    )


def stream_snapshots(
    packets: Iterable[RawPacket],
    decoder: PhotonDecoder,
    meter: Meter,
    *,
    name_registry: NameRegistry | None = None,
    party_registry: PartyRegistry | None = None,
    fame_tracker: FameTracker | None = None,
    event_mapper: EventMapper | None = None,
    snapshot_interval: float = 1.0,
) -> Iterator[MeterSnapshot]:
    mapper = event_mapper or CombatEventMapper().map
    last_emit: float | None = None
    last_timestamp: float | None = None
    pending_events: list[CombatEvent] = []
    pending_combat_states: list[tuple[float, int, bool, bool]] = []
    pending_max_age = 120.0
    pending_max_count = 2000

    for packet in packets:
        last_timestamp = packet.timestamp
        if party_registry is not None:
            party_registry.observe_packet(packet)
        messages = decoder.decode_all(packet)
        for message in messages:
            if name_registry is not None:
                name_registry.observe(message)
            if party_registry is not None:
                party_registry.observe(message, packet)
                if name_registry is not None:
                    party_registry.sync_names(name_registry)
                    party_registry.infer_self_name_from_targets(name_registry)
                    party_registry.sync_id_names(name_registry)
                party_registry.try_resolve_self_id(name_registry)
            if fame_tracker is not None:
                fame_tracker.observe(message, packet)
        _flush_or_trim_pending(
            meter,
            packet.timestamp,
            pending_events,
            pending_combat_states,
            party_registry,
            name_registry,
            pending_max_age=pending_max_age,
            pending_max_count=pending_max_count,
        )
        for message in messages:
            event = mapper(message, packet)
            if event is None:
                continue
            if isinstance(event, list):
                for item in event:
                    if (
                        party_registry is not None
                        and party_registry.strict
                        and not party_registry.has_ids()
                    ):
                        party_registry.observe_combat_event(item)
                        party_registry.try_resolve_self_id(name_registry)
                    if _allow_event(item, party_registry, name_registry):
                        meter.push(item)
                        continue
                    if (
                        party_registry is not None
                        and party_registry.strict
                        and (
                            not party_registry.has_ids()
                            or party_registry.has_unresolved_names()
                        )
                    ):
                        pending_events.append(item)
            else:
                if (
                    party_registry is not None
                    and party_registry.strict
                    and not party_registry.has_ids()
                ):
                    party_registry.observe_combat_event(event)
                    party_registry.try_resolve_self_id(name_registry)
                if _allow_event(event, party_registry, name_registry):
                    meter.push(event)
                elif (
                    party_registry is not None
                    and party_registry.strict
                    and (
                        not party_registry.has_ids()
                        or party_registry.has_unresolved_names()
                    )
                ):
                    pending_events.append(event)

        for message in messages:
            combat_state = _decode_combat_state(message)
            if combat_state is None:
                continue
            if hasattr(meter, "observe_combat_state") and _allow_combat_state(
                combat_state[0], party_registry, name_registry
            ):
                try:
                    meter.observe_combat_state(
                        combat_state[0], combat_state[1], combat_state[2], packet.timestamp
                    )
                except TypeError:
                    pass
            elif (
                party_registry is not None
                and party_registry.strict
                and (
                    not party_registry.has_ids()
                    or party_registry.has_unresolved_names()
                )
            ):
                pending_combat_states.append(
                    (packet.timestamp, combat_state[0], combat_state[1], combat_state[2])
                )

        _flush_or_trim_pending(
            meter,
            packet.timestamp,
            pending_events,
            pending_combat_states,
            party_registry,
            name_registry,
            pending_max_age=pending_max_age,
            pending_max_count=pending_max_count,
        )

        if hasattr(meter, "observe_packet"):
            try:
                meter.observe_packet(packet)
            except TypeError:
                pass
        if name_registry is not None and hasattr(meter, "refresh_history_labels"):
            try:
                meter.refresh_history_labels()
            except TypeError:
                pass

        if last_emit is None or snapshot_interval <= 0 or packet.timestamp - last_emit >= snapshot_interval:
            snapshot = meter.snapshot()
            names = name_registry.snapshot() if name_registry is not None else None
            yield MeterSnapshot(timestamp=packet.timestamp, totals=snapshot.totals, names=names)
            last_emit = packet.timestamp

    if hasattr(meter, "finalize"):
        try:
            meter.finalize()
        except TypeError:
            pass
        fallback_ts = last_timestamp or 0.0
        snapshot = meter.snapshot()
        names = name_registry.snapshot() if name_registry is not None else None
        yield MeterSnapshot(timestamp=fallback_ts, totals=snapshot.totals, names=names)
        return

    if last_emit is None:
        fallback_ts = last_timestamp or 0.0
        names = name_registry.snapshot() if name_registry is not None else None
        yield MeterSnapshot(timestamp=fallback_ts, totals={}, names=names)


def _null_event_mapper(_message: PhotonMessage, _packet: RawPacket) -> CombatEvent | None:
    return None


def _allow_event(
    event: CombatEvent,
    party_registry: PartyRegistry | None,
    name_registry: NameRegistry | None,
) -> bool:
    if party_registry is None:
        return True
    return party_registry.allows(event.source_id, name_registry)


def _allow_combat_state(
    entity_id: int,
    party_registry: PartyRegistry | None,
    name_registry: NameRegistry | None,
) -> bool:
    if party_registry is None:
        return True
    return party_registry.allows(entity_id, name_registry)


def _decode_combat_state(
    message: PhotonMessage,
) -> tuple[int, bool, bool] | None:
    if message.event_code is None or message.event_code != COMBAT_STATE_EVENT_CODE:
        return None
    try:
        event = decode_event_data(message.payload)
    except Protocol16Error:
        return None
    if event.parameters.get(COMBAT_STATE_SUBTYPE_KEY) not in COMBAT_STATE_SUBTYPE_VALUES:
        return None
    entity_id = event.parameters.get(COMBAT_STATE_ID_KEY)
    if not isinstance(entity_id, int):
        return None
    in_active = bool(event.parameters.get(COMBAT_STATE_ACTIVE_KEY))
    in_passive = bool(event.parameters.get(COMBAT_STATE_PASSIVE_KEY))
    return entity_id, in_active, in_passive


def _flush_or_trim_pending(
    meter: Meter,
    now_ts: float,
    pending_events: list[CombatEvent],
    pending_combat_states: list[tuple[float, int, bool, bool]],
    party_registry: PartyRegistry | None,
    name_registry: NameRegistry | None,
    *,
    pending_max_age: float,
    pending_max_count: int,
) -> None:
    if party_registry is None:
        return
    if not (pending_events or pending_combat_states):
        return
    cutoff = now_ts - pending_max_age
    if cutoff > 0:
        pending_events[:] = [item for item in pending_events if item.timestamp >= cutoff]
        pending_combat_states[:] = [item for item in pending_combat_states if item[0] >= cutoff]
    if len(pending_events) > pending_max_count:
        pending_events[:] = pending_events[-pending_max_count:]
    if len(pending_combat_states) > pending_max_count:
        pending_combat_states[:] = pending_combat_states[-pending_max_count:]
    if not (pending_events or pending_combat_states):
        return
    if party_registry.has_ids():
        retain_unresolved = party_registry.has_unresolved_names()
        if pending_events:
            remaining: list[CombatEvent] = []
            for item in pending_events:
                if _allow_event(item, party_registry, name_registry):
                    if hasattr(meter, "merge_event_into_history") and meter.merge_event_into_history(item):
                        continue
                    meter.push(item)
                elif retain_unresolved:
                    remaining.append(item)
            pending_events[:] = remaining
        if pending_combat_states and hasattr(meter, "observe_combat_state"):
            remaining_states: list[tuple[float, int, bool, bool]] = []
            for ts, entity_id, in_active, in_passive in sorted(pending_combat_states):
                if _allow_combat_state(entity_id, party_registry, name_registry):
                    try:
                        meter.observe_combat_state(entity_id, in_active, in_passive, ts)
                    except TypeError:
                        pass
                elif retain_unresolved:
                    remaining_states.append((ts, entity_id, in_active, in_passive))
            pending_combat_states[:] = remaining_states
        return
