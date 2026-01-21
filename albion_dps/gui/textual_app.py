from __future__ import annotations

import queue
from collections.abc import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from albion_dps.models import MeterSnapshot


SORT_FIELD = {"dmg": "damage", "dps": "dps", "heal": "heal", "hps": "hps"}
BAR_WIDTH = 24


class AlbionDpsApp(App):
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
    """

    def __init__(
        self,
        *,
        snapshot_queue: queue.Queue[MeterSnapshot | None],
        sort_key: str,
        top_n: int,
        mode: str,
        zone_label_provider: Callable[[], str | None],
    ) -> None:
        super().__init__()
        self._snapshot_queue = snapshot_queue
        self._sort_key = sort_key
        self._top_n = top_n
        self._mode = mode
        self._zone_label_provider = zone_label_provider
        self._last_snapshot: MeterSnapshot | None = None
        self._header = Static(id="header")
        self._table = DataTable(id="scoreboard")

    def compose(self) -> ComposeResult:
        yield Vertical(self._header, self._table)

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
        header = "Albion DPS Meter  [GUI]"
        header += f"  [mode {self._mode}]"
        if zone_label:
            header += f"  [zone {zone_label}]"
        header += f"\nTimestamp: {snapshot.timestamp:.3f}  Sort: {self._sort_key}  Top: {self._top_n}"
        self._header.update(header)

        entries = _snapshot_entries(snapshot, self._sort_key)
        if self._top_n and self._top_n > 0:
            entries = entries[: self._top_n]

        max_value = max((entry[5] for entry in entries), default=0.0)
        rows = []
        for label, damage, heal, dps, hps, sort_value in entries:
            bar = _bar(sort_value, max_value, BAR_WIDTH)
            rows.append(
                (
                    label,
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


def _snapshot_entries(
    snapshot: MeterSnapshot, sort_key: str
) -> list[tuple[str, float, float, float, float, float]]:
    field = SORT_FIELD.get(sort_key, "dps")
    entries: list[tuple[str, float, float, float, float, float]] = []
    names = snapshot.names or {}
    for source_id, stats in snapshot.totals.items():
        label = names.get(source_id) or str(source_id)
        damage = float(stats.get("damage", 0.0))
        heal = float(stats.get("heal", 0.0))
        dps = float(stats.get("dps", 0.0))
        hps = float(stats.get("hps", 0.0))
        sort_value = float(stats.get(field, 0.0))
        entries.append((label, damage, heal, dps, hps, sort_value))
    entries.sort(key=lambda item: item[5], reverse=True)
    return entries


def _bar(value: float, max_value: float, width: int) -> Text:
    if max_value <= 0:
        fill = 0
    else:
        fill = int(round((value / max_value) * width))
    fill = max(0, min(width, fill))
    bar = "#" * fill + "." * (width - fill)
    return Text(bar, style="bold #57a6ff")
