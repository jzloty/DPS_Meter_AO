from __future__ import annotations

import argparse
import logging
import queue
import threading
from collections.abc import Iterable, Callable

from albion_dps.capture import auto_detect_interface, list_interfaces
from albion_dps.domain import FameTracker, NameRegistry, PartyRegistry, load_item_resolver
from albion_dps.meter.session_meter import SessionMeter
from albion_dps.models import MeterSnapshot
from albion_dps.pipeline import live_snapshots, replay_snapshots
from albion_dps.protocol.combat_mapper import CombatEventMapper
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.registry import default_registry


SnapshotQueue = queue.Queue[MeterSnapshot | None]


def run_gui_stub(_args: argparse.Namespace) -> int:
    logging.getLogger(__name__).warning("GUI not implemented yet on this branch.")
    return 0


def run_gui(args: argparse.Namespace) -> int:
    try:
        from albion_dps.gui.textual_app import AlbionDpsApp
    except Exception:  # pragma: no cover - optional dependency
        logging.getLogger(__name__).exception(
            "Textual is not available. Install GUI deps with: pip install -e \".[gui]\""
        )
        return 1

    names, party, fame, meter, decoder, mapper = _build_runtime(args)
    item_resolver = load_item_resolver(logger=logging.getLogger(__name__))

    def role_lookup(entity_id: int) -> str | None:
        return item_resolver.role_for_items(names.items_for(entity_id))

    if args.gui_command == "live":
        if args.list_interfaces:
            for interface in list_interfaces():
                print(interface)
            return 0
        interface = args.interface
        if not interface:
            interface = auto_detect_interface(
                bpf_filter=args.bpf,
                snaplen=args.snaplen,
                promisc=args.promisc,
                timeout_ms=args.timeout_ms,
            )
            if interface is None:
                interface = _fallback_interface()
                if interface is None:
                    logging.getLogger(__name__).error("No capture interfaces available")
                    return 1
                logging.getLogger(__name__).warning(
                    "Auto-detect found no traffic; using fallback interface: %s",
                    interface,
                )
            else:
                logging.getLogger(__name__).info("Auto-detected interface: %s", interface)

        dump_raw_dir = args.dump_raw
        if args.debug and dump_raw_dir is None:
            dump_raw_dir = "artifacts/raw"

        snapshots = live_snapshots(
            interface,
            decoder=decoder,
            meter=meter,
            bpf_filter=args.bpf,
            snaplen=args.snaplen,
            promisc=args.promisc,
            timeout_ms=args.timeout_ms,
            dump_raw_dir=dump_raw_dir,
            name_registry=names,
            party_registry=party,
            fame_tracker=fame,
            event_mapper=mapper.map,
            snapshot_interval=1.0,
        )
    return _run_textual_app(
        AlbionDpsApp,
        snapshots,
        sort_key=args.sort,
        top_n=args.top,
        mode=args.mode,
        zone_label_provider=meter.zone_label,
        history_provider=meter.history,
        history_limit=max(args.history, 1),
        set_mode=meter.set_mode,
        role_lookup=role_lookup,
    )

    if args.gui_command == "replay":
        snapshots = replay_snapshots(
            args.pcap,
            decoder,
            meter,
            name_registry=names,
            party_registry=party,
            fame_tracker=fame,
            event_mapper=mapper.map,
            snapshot_interval=1.0,
        )
    return _run_textual_app(
        AlbionDpsApp,
        snapshots,
        sort_key=args.sort,
        top_n=args.top,
        mode=args.mode,
        zone_label_provider=meter.zone_label,
        history_provider=meter.history,
        history_limit=max(args.history, 1),
        set_mode=meter.set_mode,
        role_lookup=role_lookup,
    )

    logging.getLogger(__name__).error("Unknown gui command")
    return 1


def _build_runtime(
    args: argparse.Namespace,
) -> tuple[
    NameRegistry,
    PartyRegistry,
    FameTracker,
    SessionMeter,
    PhotonDecoder,
    CombatEventMapper,
]:
    decoder = PhotonDecoder(
        registry=default_registry(), debug=args.debug, dump_unknowns=True
    )
    mapper = CombatEventMapper(dump_unknowns=True, clamp_overkill=True)
    names = NameRegistry()
    party = PartyRegistry()
    fame = FameTracker()
    meter = SessionMeter(
        window_seconds=10.0,
        battle_timeout_seconds=args.battle_timeout,
        history_limit=max(args.history, 1),
        mode=args.mode,
        name_lookup=names.lookup,
    )
    if args.self_name:
        party.set_self_name(args.self_name, confirmed=True)
    if args.self_id is not None:
        party.seed_self_ids([args.self_id])
    return names, party, fame, meter, decoder, mapper


def _run_textual_app(
    app_cls: type,
    snapshots: Iterable[MeterSnapshot],
    *,
    sort_key: str,
    top_n: int,
    mode: str,
    zone_label_provider: Callable[[], str | None],
    history_provider: Callable[[int], list],
    history_limit: int,
    set_mode: Callable[[str], None],
    role_lookup: Callable[[int], str | None] | None = None,
) -> int:
    snapshot_queue: SnapshotQueue = queue.Queue()
    stop_event = threading.Event()
    producer = threading.Thread(
        target=_produce_snapshots,
        args=(snapshots, snapshot_queue, stop_event),
        daemon=True,
    )
    producer.start()
    app = app_cls(
        snapshot_queue=snapshot_queue,
        sort_key=sort_key,
        top_n=top_n,
        mode=mode,
        zone_label_provider=zone_label_provider,
        history_provider=history_provider,
        history_limit=history_limit,
        set_mode=set_mode,
        role_lookup=role_lookup,
    )
    try:
        app.run()
    finally:
        stop_event.set()
    return 0


def _produce_snapshots(
    snapshots: Iterable[MeterSnapshot],
    snapshot_queue: SnapshotQueue,
    stop_event: threading.Event,
) -> None:
    try:
        for snapshot in snapshots:
            if stop_event.is_set():
                break
            snapshot_queue.put(snapshot)
    finally:
        snapshot_queue.put(None)


def _fallback_interface() -> str | None:
    try:
        interfaces = list_interfaces()
    except RuntimeError:
        return None
    if not interfaces:
        return None
    for candidate in interfaces:
        lowered = candidate.lower()
        if "loopback" in lowered or "npf_loopback" in lowered:
            continue
        return candidate
    return interfaces[0]
