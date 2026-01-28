from __future__ import annotations

import queue
from collections.abc import Callable
from datetime import datetime

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from albion_dps.models import MeterSnapshot


SORT_FIELD = {"dmg": "damage", "dps": "dps", "heal": "heal", "hps": "hps"}
BAR_WIDTH = 24
ROLE_COLORS = {"tank": "#4fb3ff", "dps": "#ff7a59", "heal": "#6de38f"}
FALLBACK_PALETTE = ["#ffd166", "#06d6a0", "#118ab2", "#ef476f", "#a39dff"]


class AlbionDpsApp(App):
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("b", "mode_battle", "Battle"),
        ("z", "mode_zone", "Zone"),
        ("m", "mode_manual", "Manual"),
        ("1", "sort_dps", "Sort DPS"),
        ("2", "sort_dmg", "Sort DMG"),
        ("3", "sort_hps", "Sort HPS"),
        ("4", "sort_heal", "Sort Heal"),
    ]

    CSS = """
    Screen {
        background: #0c1116;
    }
    #header {
        color: #e6edf3;
        padding: 1 2;
        background: #111821;
        border: tall #1f2a36;
    }
    #scoreboard {
        margin: 1 2;
        border: round #1f2a36;
        height: 1fr;
    }
    #history {
        margin: 1 2 1 0;
        border: round #1f2a36;
        width: 36;
        padding: 1 2;
        color: #e6edf3;
        background: #0f151d;
    }
    """

    def __init__(
        self,
        *,
        snapshot_queue: queue.Queue[MeterSnapshot | None],
        sort_key: str,
        top_n: int,
        mode: str,
        zone_label_provider: Callable[[], str | None],
        history_provider: Callable[[int], list],
        history_limit: int,
        set_mode: Callable[[str], None],
        role_lookup: Callable[[int], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._snapshot_queue = snapshot_queue
        self._sort_key = sort_key
        self._top_n = top_n
        self._mode = mode
        self._zone_label_provider = zone_label_provider
        self._history_provider = history_provider
        self._history_limit = history_limit
        self._set_mode = set_mode
        self._role_lookup = role_lookup
        self._last_snapshot: MeterSnapshot | None = None
        self._header = Static(id="header")
        self._table = DataTable(id="scoreboard")
        self._history = Static(id="history")

    def compose(self) -> ComposeResult:
        yield Vertical(self._header, Horizontal(self._table, self._history))

    def on_mount(self) -> None:
        self._table.add_columns("source", "damage", "heal", "dps", "hps", "bar")
        self._table.zebra_stripes = True
        self.set_interval(0.2, self._drain_queue)

    def _drain_queue(self) -> None:
        updated = False
        while True:
            try:
                item = self._snapshot_queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self.exit()
                return
            self._last_snapshot = item
            updated = True
        if updated and self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)

    def _render_snapshot(self, snapshot: MeterSnapshot) -> None:
        zone_label = self._zone_label_provider() if self._zone_label_provider else None
        timestamp = datetime.fromtimestamp(snapshot.timestamp)
        time_label = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        header = "Albion DPS Meter  [GUI]"
        header += f"  [mode {self._mode}]"
        if zone_label:
            header += f"  [zone {zone_label}]"
        header += f"\nTime: {time_label}  Sort: {self._sort_key}  Top: {self._top_n}"
        self._header.update(header)

        entries = _snapshot_entries(snapshot, self._sort_key)
        if self._top_n and self._top_n > 0:
            entries = entries[: self._top_n]

        max_value = max((entry[6] for entry in entries), default=0.0)
        max_damage = max((entry[2] for entry in entries), default=0.0)
        max_heal = max((entry[3] for entry in entries), default=0.0)
        rows = []
        for source_id, label, damage, heal, dps, hps, sort_value in entries:
            role = None
            if self._role_lookup is not None:
                role = self._role_lookup(source_id)
            if not role:
                role = _infer_role(damage, heal, max_damage, max_heal)
            color = _color_for_label(label, role)
            bar = _bar(sort_value, max_value, BAR_WIDTH, color)
            rows.append(
                (
                    Text(label, style=color),
                    f"{damage:.0f}",
                    f"{heal:.0f}",
                    f"{dps:.1f}",
                    f"{hps:.1f}",
                    bar,
                )
            )

        self._table.clear()
        for row in rows:
            self._table.add_row(*row)
        self._history.update(_format_history(self._history_provider, self._history_limit))

    def action_quit(self) -> None:
        self.exit()

    def action_mode_battle(self) -> None:
        self._set_mode("battle")
        self._mode = "battle"
        self.refresh()

    def action_mode_zone(self) -> None:
        self._set_mode("zone")
        self._mode = "zone"
        self.refresh()

    def action_mode_manual(self) -> None:
        self._set_mode("manual")
        self._mode = "manual"
        self.refresh()

    def action_sort_dps(self) -> None:
        self._sort_key = "dps"
        if self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)

    def action_sort_dmg(self) -> None:
        self._sort_key = "dmg"
        if self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)

    def action_sort_hps(self) -> None:
        self._sort_key = "hps"
        if self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)

    def action_sort_heal(self) -> None:
        self._sort_key = "heal"
        if self._last_snapshot is not None:
            self._render_snapshot(self._last_snapshot)


def _snapshot_entries(
    snapshot: MeterSnapshot, sort_key: str
) -> list[tuple[int, str, float, float, float, float, float]]:
    field = SORT_FIELD.get(sort_key, "dps")
    entries: list[tuple[int, str, float, float, float, float, float]] = []
    names = snapshot.names or {}
    for source_id, stats in snapshot.totals.items():
        label = names.get(source_id) or str(source_id)
        damage = float(stats.get("damage", 0.0))
        heal = float(stats.get("heal", 0.0))
        dps = float(stats.get("dps", 0.0))
        hps = float(stats.get("hps", 0.0))
        sort_value = float({"damage": damage, "heal": heal, "dps": dps, "hps": hps}[field])
        entries.append((source_id, label, damage, heal, dps, hps, sort_value))
    entries.sort(key=lambda item: item[6], reverse=True)
    return entries


def _bar(value: float, max_value: float, width: int, color: str) -> Text:
    if max_value <= 0:
        fill = 0
    else:
        fill = int(round((value / max_value) * width))
    fill = max(0, min(width, fill))
    bar = "#" * fill + "." * (width - fill)
    return Text(bar, style=f"bold {color}")


def _infer_role(damage: float, heal: float, max_damage: float, max_heal: float) -> str | None:
    if heal > 0.0:
        if damage <= 0.0 or heal >= damage * 0.7 or (max_heal > 0.0 and heal >= max_heal * 0.5):
            return "heal"
    if max_damage > 0.0 and damage >= max_damage * 0.6:
        return "dps"
    if damage > 0.0 or heal > 0.0:
        return "tank"
    return None


def _color_for_label(label: str, role: str | None) -> str:
    if role and role in ROLE_COLORS:
        return ROLE_COLORS[role]
    if not label:
        return FALLBACK_PALETTE[0]
    idx = sum(ord(ch) for ch in label) % len(FALLBACK_PALETTE)
    return FALLBACK_PALETTE[idx]


def _format_history(
    history_provider: Callable[[int], list], limit: int
) -> Text:
    history = history_provider(max(limit, 1))
    if not history:
        text = Text("History:\n(empty)\n\nLegend:\n")
        _append_legend(text)
        return text
    text = Text()
    text.append("History:\n")
    for idx, summary in enumerate(history, start=1):
        top = summary.entries[0] if summary.entries else None
        label = _shorten_label(top.label if top else "-", 14)
        dps = top.dps if top else 0.0
        text.append(f"[{idx}] {summary.mode} {_format_duration(summary.duration)}\n")
        text.append(f" {label} dmg {summary.total_damage:.0f} dps {dps:.1f}\n")
        if summary.entries and len(summary.entries) > 1:
            text.append(f" +{len(summary.entries) - 1} others\n")
    text.append("\nLegend:\n")
    _append_legend(text)
    return text


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    return f"{minutes:02d}:{secs:02d}"


def _append_legend(text: Text) -> None:
    text.append("DPS", style=ROLE_COLORS["dps"])
    text.append("  ")
    text.append("Tank", style=ROLE_COLORS["tank"])
    text.append("  ")
    text.append("Heal", style=ROLE_COLORS["heal"])


def _shorten_label(label: str, limit: int) -> str:
    if len(label) <= limit:
        return label
    if limit <= 1:
        return label[:limit]
    trimmed = label[: max(limit - 3, 0)]
    return f"{trimmed}..."
