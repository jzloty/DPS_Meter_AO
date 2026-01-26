from __future__ import annotations

import argparse
import logging
import os
import time
from albion_dps.capture import auto_detect_interface, list_interfaces
from albion_dps.cli_clipboard import copy_to_clipboard
from albion_dps.cli_ui import format_dashboard, format_history_lines, render_loop
from albion_dps.domain import FameTracker, NameRegistry, PartyRegistry
from albion_dps.gui.runner import run_gui
from albion_dps.qt.runner import run_qt
from albion_dps.logging_config import configure_logging
from albion_dps.meter.session_meter import SessionEntry, SessionMeter, SessionSummary
from albion_dps.pipeline import live_snapshots, replay_snapshots
from albion_dps.protocol.combat_mapper import CombatEventMapper
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.registry import default_registry


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="albion-dps")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--version", action="version", version="0.1.0")

    subparsers = parser.add_subparsers(dest="command")

    live = subparsers.add_parser("live")
    replay = subparsers.add_parser("replay")
    gui = subparsers.add_parser("gui")
    gui_subparsers = gui.add_subparsers(dest="gui_command")
    gui_live = gui_subparsers.add_parser("live")
    gui_replay = gui_subparsers.add_parser("replay")
    qt = subparsers.add_parser("qt")
    qt_subparsers = qt.add_subparsers(dest="qt_command")
    qt_live = qt_subparsers.add_parser("live")
    qt_replay = qt_subparsers.add_parser("replay")

    for sub in (live, replay, gui_live, gui_replay, qt_live, qt_replay):
        sub.add_argument("--sort", choices=["dmg", "dps", "heal", "hps"], default="dps")
        sub.add_argument("--top", type=int, default=10)
        sub.add_argument("--snapshot")
        sub.add_argument("--self-name")
        sub.add_argument("--self-id", type=int)
        sub.add_argument("--debug", action="store_true")
        sub.add_argument(
            "--mode",
            choices=["battle", "zone", "manual"],
            default="battle",
        )
        sub.add_argument("--history", type=int, default=5)
        sub.add_argument("--battle-timeout", type=float, default=20.0)

    live.add_argument("--interface")
    live.add_argument("--list-interfaces", action="store_true")
    live.add_argument("--bpf", default="udp and (port 5055 or port 5056 or port 5058)")
    live.add_argument("--promisc", action="store_true")
    live.add_argument("--snaplen", type=int, default=65535)
    live.add_argument("--timeout-ms", type=int, default=1000)
    live.add_argument("--dump-raw")
    replay.add_argument("pcap")
    gui_live.add_argument("--interface")
    gui_live.add_argument("--list-interfaces", action="store_true")
    gui_live.add_argument("--bpf", default="udp and (port 5055 or port 5056 or port 5058)")
    gui_live.add_argument("--promisc", action="store_true")
    gui_live.add_argument("--snaplen", type=int, default=65535)
    gui_live.add_argument("--timeout-ms", type=int, default=1000)
    gui_live.add_argument("--dump-raw")
    gui_replay.add_argument("pcap")
    qt_live.add_argument("--interface")
    qt_live.add_argument("--list-interfaces", action="store_true")
    qt_live.add_argument("--bpf", default="udp and (port 5055 or port 5056 or port 5058)")
    qt_live.add_argument("--promisc", action="store_true")
    qt_live.add_argument("--snaplen", type=int, default=65535)
    qt_live.add_argument("--timeout-ms", type=int, default=1000)
    qt_live.add_argument("--dump-raw")
    qt_replay.add_argument("pcap")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    self_name, self_id = _resolve_self(args)
    log_level = "DEBUG" if args.debug else args.log_level
    configure_logging(log_level)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "replay":
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
        if self_name:
            party.set_self_name(self_name, confirmed=True)
        if self_id is not None:
            party.seed_self_ids([self_id])
        view_builder = _build_view(
            meter,
            fame,
            party,
            args.sort,
            args.top,
            args.history,
        )
        key_handler = _build_key_handler(meter, fame, names.lookup)
        key_handler.set_view(view_builder)
        render_loop(
            replay_snapshots(
                args.pcap,
                decoder,
                meter,
                name_registry=names,
                party_registry=party,
                fame_tracker=fame,
                event_mapper=mapper.map,
                snapshot_interval=1.0,
            ),
            sort_key=args.sort,
            top_n=args.top,
            snapshot_path=args.snapshot,
            refresh_seconds=0.0,
            view_builder=view_builder,
            key_handler=key_handler,
        )
        return 0

    if args.command == "gui":
        if not args.gui_command:
            parser.parse_args(["gui", "--help"])
            return 0
        return run_gui(args)

    if args.command == "qt":
        if not args.qt_command:
            parser.parse_args(["qt", "--help"])
            return 0
        return run_qt(args)

    if args.command == "live":
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
                    parser.error("No capture interfaces available")
                logging.getLogger(__name__).warning(
                    "Auto-detect found no traffic; using fallback interface: %s",
                    interface,
                )
            else:
                logging.getLogger(__name__).info(
                    "Auto-detected interface: %s", interface
                )

        dump_raw_dir = args.dump_raw
        if args.debug and dump_raw_dir is None:
            dump_raw_dir = "artifacts/raw"

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
        if self_name:
            party.set_self_name(self_name, confirmed=True)
        if self_id is not None:
            party.seed_self_ids([self_id])
        view_builder = _build_view(
            meter,
            fame,
            party,
            args.sort,
            args.top,
            args.history,
        )
        key_handler = _build_key_handler(meter, fame, names.lookup)
        key_handler.set_view(view_builder)
        render_loop(
            live_snapshots(
                interface,
                decoder=PhotonDecoder(
                    registry=default_registry(), debug=args.debug, dump_unknowns=True
                ),
                meter=meter,
                bpf_filter=args.bpf,
                snaplen=args.snaplen,
                promisc=args.promisc,
                timeout_ms=args.timeout_ms,
                dump_raw_dir=dump_raw_dir,
                name_registry=names,
                party_registry=party,
                fame_tracker=fame,
                event_mapper=CombatEventMapper(dump_unknowns=True, clamp_overkill=True).map,
                snapshot_interval=1.0,
            ),
            sort_key=args.sort,
            top_n=args.top,
            snapshot_path=args.snapshot,
            refresh_seconds=0.0,
            view_builder=view_builder,
            key_handler=key_handler,
        )
        return 0

    return 0


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


def _resolve_self(args: argparse.Namespace) -> tuple[str | None, int | None]:
    name = args.self_name or os.environ.get("ALBION_DPS_SELF_NAME")
    raw_id = args.self_id
    if raw_id is None:
        env_id = os.environ.get("ALBION_DPS_SELF_ID")
        if env_id:
            try:
                raw_id = int(env_id)
            except ValueError:
                raw_id = None
    return name, raw_id


def _build_view(
    meter: SessionMeter,
    fame: FameTracker,
    party: PartyRegistry | None,
    sort_key: str,
    top_n: int,
    history_limit: int,
):
    status = {"text": None, "until": 0.0}

    def set_status(text: str, duration: float = 2.0) -> None:
        status["text"] = text
        status["until"] = time.time() + duration

    def view(snapshot):
        history = meter.history(limit=history_limit)
        names = snapshot.names or {}
        history_lines = [
            _format_history_line(summary, max_players=3, names=names) for summary in history
        ]
        status_line = status["text"] if status["text"] and time.time() <= status["until"] else None
        if (
            status_line is None
            and party is not None
            and party.strict
            and not party.has_ids()
            and meter.mode == "battle"
        ):
            status_line = "Waiting for self detection (target lock / combat start)..."
        return format_dashboard(
            snapshot,
            mode=meter.mode,
            zone_label=meter.zone_label(),
            manual_active=meter.manual_active(),
            fame_total=fame.total(),
            fame_per_hour=fame.per_hour(),
            history_lines=format_history_lines(history_lines, limit=history_limit),
            sort_key=sort_key,
            top_n=top_n,
            status_line=status_line,
        )

    view.set_status = set_status
    return view


def _build_key_handler(
    meter: SessionMeter,
    fame: FameTracker,
    name_lookup: Callable[[int], str | None] | None = None,
):
    view = None

    def set_view(handle):
        nonlocal view
        view = handle

    def handler(key: str) -> None:
        if key in ("b", "z", "m"):
            mode = {"b": "battle", "z": "zone", "m": "manual"}[key]
            meter.set_mode(mode)
            _set_status(view, f"Mode: {mode}")
            return
        if key == " ":
            if meter.mode == "manual":
                active = meter.toggle_manual()
                _set_status(view, "Manual: on" if active else "Manual: off")
            return
        if key == "n":
            meter.end_session()
            _set_status(view, "Session archived")
            return
        if key == "r":
            fame.reset()
            _set_status(view, "Fame reset")
            return
        if key.isdigit() and key != "0":
            index = int(key)
            history = meter.history(limit=index)
            if len(history) < index:
                return
            summary = history[index - 1]
            text = _format_history_copy(summary, max_players=3, name_lookup=name_lookup)
            copied = copy_to_clipboard(text)
            if copied:
                _set_status(view, f"Copied: {text}")
            else:
                _set_status(view, "Copy failed")
            return

    handler.set_view = set_view
    return handler


def _set_status(view, text: str) -> None:
    if view is None:
        return
    if hasattr(view, "set_status"):
        view.set_status(text)


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


HISTORY_LINE_MAX_PLAYERS = 3
HISTORY_COPY_MAX_PLAYERS = 5
HISTORY_LABEL_LIMIT = 14


def _format_history_line(
    summary: SessionSummary,
    max_players: int = HISTORY_LINE_MAX_PLAYERS,
    *,
    names: dict[int, str] | None = None,
) -> str:
    label = summary.mode
    if summary.mode == "zone" and summary.label:
        label = f"zone {summary.label}"
    duration = _format_duration(summary.duration)
    names = names or {}
    players_text = _format_players_summary(
        summary.entries, max_players=max_players, names=names
    )
    totals = _format_totals(summary.total_damage, summary.total_heal)
    return f"{label} {duration} | {totals} | {players_text}"


def _format_history_copy(
    summary: SessionSummary,
    max_players: int = HISTORY_COPY_MAX_PLAYERS,
    *,
    name_lookup: Callable[[int], str | None] | None = None,
) -> str:
    label = summary.mode
    if summary.mode == "zone" and summary.label:
        label = f"zone {summary.label}"
    duration = _format_duration(summary.duration)
    players_text = _format_players_copy(
        summary.entries, max_players=max_players, name_lookup=name_lookup
    )
    totals = _format_totals(summary.total_damage, summary.total_heal)
    return f"{label} {duration} | {totals} | {players_text}"


def _format_int(value: float) -> int:
    return int(round(value))


def _resolve_label(label: str, names: dict[int, str]) -> str:
    if label.isdigit():
        mapped = names.get(int(label))
        if mapped:
            return mapped
    return label


def _resolve_label_lookup(
    label: str, name_lookup: Callable[[int], str | None] | None
) -> str:
    if name_lookup is None:
        return label
    if label.isdigit():
        mapped = name_lookup(int(label))
        if mapped:
            return mapped
    return label


def _format_totals(total_damage: float, total_heal: float) -> str:
    return f"total dmg {_format_int(total_damage)} heal {_format_int(total_heal)}"


def _format_players_summary(
    entries: list[SessionEntry], max_players: int, names: dict[int, str]
) -> str:
    players = entries[:max_players]
    parts = [
        f"{_shorten_label(_resolve_label(entry.label, names))} dmg {_format_int(entry.damage)} dps {entry.dps:.1f}"
        for entry in players
    ]
    extra = len(entries) - len(players)
    if extra > 0:
        parts.append(f"+{extra} others")
    return ", ".join(parts) if parts else "(no data)"


def _format_players_copy(
    entries: list[SessionEntry],
    max_players: int,
    name_lookup: Callable[[int], str | None] | None,
) -> str:
    players = entries[:max_players]
    parts = [
        f"{_shorten_label(_resolve_label_lookup(entry.label, name_lookup))} dmg {_format_int(entry.damage)} dps {entry.dps:.1f}"
        for entry in players
    ]
    extra = len(entries) - len(players)
    if extra > 0:
        parts.append(f"+{extra} others")
    return ", ".join(parts) if parts else "(no data)"


def _shorten_label(label: str, limit: int = HISTORY_LABEL_LIMIT) -> str:
    if len(label) <= limit:
        return label
    if limit <= 3:
        return label[:limit]
    return f"{label[: limit - 3]}..."



if __name__ == "__main__":
    raise SystemExit(main())
