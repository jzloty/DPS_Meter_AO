from __future__ import annotations

import argparse
import logging
import os
import queue
import threading
from collections.abc import Iterable
from pathlib import Path

from albion_dps.capture import auto_detect_interface, list_interfaces
from albion_dps.domain import FameTracker, NameRegistry, PartyRegistry, load_item_resolver
from albion_dps.meter.session_meter import SessionMeter
from albion_dps.models import MeterSnapshot
from albion_dps.pipeline import live_snapshots, replay_snapshots
from albion_dps.protocol.combat_mapper import CombatEventMapper
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.registry import default_registry


SnapshotQueue = queue.Queue[MeterSnapshot | None]


def run_qt(args: argparse.Namespace) -> int:
    if args.qt_command == "live" and args.list_interfaces:
        for interface in list_interfaces():
            print(interface)
        return 0
    _ensure_pyside6_paths()
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine
    except Exception:  # pragma: no cover - optional dependency
        logging.getLogger(__name__).exception(
            "PySide6 is not available. Install GUI deps with: pip install -e \".[gui-qt]\""
        )
        return 1

    from albion_dps.qt.models import UiState

    names, party, fame, meter, decoder, mapper = _build_runtime(args)
    item_resolver = load_item_resolver(logger=logging.getLogger(__name__))

    def role_lookup(entity_id: int) -> str | None:
        return item_resolver.role_for_items(names.items_for(entity_id))
    snapshots = _build_snapshot_stream(args, names, party, fame, meter, decoder, mapper)
    if snapshots is None:
        return 1

    qml_path = Path(__file__).resolve().parent / "ui" / "Main.qml"
    if not qml_path.exists():
        logging.getLogger(__name__).error("QML not found: %s", qml_path)
        return 1

    snapshot_queue: SnapshotQueue = queue.Queue()
    stop_event = threading.Event()
    producer = threading.Thread(
        target=_produce_snapshots,
        args=(snapshots, snapshot_queue, stop_event),
        daemon=True,
    )
    producer.start()

    app = QGuiApplication([])
    engine = QQmlApplicationEngine()
    warnings: list = []

    def handle_warnings(messages) -> None:
        warnings.extend(messages)
        for message in messages:
            logging.getLogger(__name__).error("QML: %s", message.toString())

    engine.warnings.connect(handle_warnings)
    state = UiState(
        sort_key=args.sort,
        top_n=args.top,
        history_limit=max(args.history, 1),
        set_mode_callback=meter.set_mode,
        role_lookup=role_lookup,
    )
    engine.rootContext().setContextProperty("uiState", state)
    engine.load(str(qml_path))
    if not engine.rootObjects():
        logging.getLogger(__name__).error(
            "Failed to load QML UI. If QtQuick plugin is missing, reinstall PySide6 and restart the shell."
        )
        stop_event.set()
        return 1

    def drain_queue() -> None:
        _drain_snapshots(
            snapshot_queue,
            state,
            meter=meter,
            fame=fame,
            stop_event=stop_event,
        )

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(drain_queue)
    timer.start()
    app.aboutToQuit.connect(stop_event.set)
    app.exec()
    return 0


def _build_snapshot_stream(
    args: argparse.Namespace,
    names: NameRegistry,
    party: PartyRegistry,
    fame: FameTracker,
    meter: SessionMeter,
    decoder: PhotonDecoder,
    mapper: CombatEventMapper,
) -> Iterable[MeterSnapshot] | None:
    if args.qt_command == "replay":
        return replay_snapshots(
            args.pcap,
            decoder,
            meter,
            name_registry=names,
            party_registry=party,
            fame_tracker=fame,
            event_mapper=mapper.map,
            snapshot_interval=1.0,
        )

    if args.qt_command == "live":
        if args.list_interfaces:
            for interface in list_interfaces():
                print(interface)
            return None
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
                    return None
                logging.getLogger(__name__).warning(
                    "Auto-detect found no traffic; using fallback interface: %s",
                    interface,
                )
            else:
                logging.getLogger(__name__).info("Auto-detected interface: %s", interface)

        dump_raw_dir = args.dump_raw
        if args.debug and dump_raw_dir is None:
            dump_raw_dir = "artifacts/raw"

        return live_snapshots(
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

    logging.getLogger(__name__).error("Unknown qt command")
    return None


def _ensure_pyside6_paths() -> None:
    try:
        import PySide6  # type: ignore
    except Exception:
        return
    base = Path(PySide6.__file__).resolve().parent
    bin_path = base / "bin"
    qml_path = base / "qml"
    plugins_path = base / "plugins"
    if os.name == "nt":
        path_entries = [str(base)]
        if bin_path.exists():
            path_entries.insert(0, str(bin_path))
        os.environ["PATH"] = f"{os.pathsep.join(path_entries)}{os.pathsep}{os.environ.get('PATH', '')}"
        if bin_path.exists():
            try:
                os.add_dll_directory(str(bin_path))
            except Exception:
                pass
        try:
            os.add_dll_directory(str(base))
        except Exception:
            pass
    os.environ.setdefault("QML2_IMPORT_PATH", str(qml_path))
    os.environ.setdefault("QT_PLUGIN_PATH", str(plugins_path))
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


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


def _drain_snapshots(
    snapshot_queue: SnapshotQueue,
    state,
    *,
    meter: SessionMeter,
    fame: FameTracker,
    stop_event: threading.Event,
) -> None:
    while True:
        try:
            snapshot = snapshot_queue.get_nowait()
        except queue.Empty:
            return
        if snapshot is None:
            stop_event.set()
            return
        names = snapshot.names or {}
        state.update(
            snapshot,
            names=names,
            history=meter.history(limit=state.historyLimit),
            mode=meter.mode,
            zone=meter.zone_label(),
            fame_total=fame.total(),
            fame_per_hour=fame.per_hour(),
        )


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
