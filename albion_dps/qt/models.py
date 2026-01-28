from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    Qt,
    Property,
    Signal,
    Slot,
)

from albion_dps.meter.session_meter import SessionEntry, SessionSummary


SORT_KEY_MAP = {
    "dmg": "damage",
    "dps": "dps",
    "heal": "heal",
    "hps": "hps",
}

ROLE_COLORS = {
    "dps": "#ea5d5d",
    "heal": "#5deaa1",
    "tank": "#5da1ea",
}
FALLBACK_PALETTE = [
    "#7dd3fc",
    "#fbbf24",
    "#f472b6",
    "#34d399",
    "#a78bfa",
    "#f97316",
    "#22d3ee",
    "#e879f9",
]
FALLBACK_COLOR = "#9aa4af"

HISTORY_MAX_PLAYERS = 5
HISTORY_LABEL_LIMIT = 14


@dataclass(frozen=True)
class PlayerRow:
    name: str
    damage: float
    heal: float
    dps: float
    hps: float
    bar_ratio: float
    role: str
    color: str


@dataclass(frozen=True)
class HistoryRow:
    label: str
    totals: str
    players: str
    copy_text: str


class PlayerModel(QAbstractListModel):
    NameRole = Qt.UserRole + 1
    DamageRole = Qt.UserRole + 2
    HealRole = Qt.UserRole + 3
    DpsRole = Qt.UserRole + 4
    HpsRole = Qt.UserRole + 5
    BarRole = Qt.UserRole + 6
    RoleRole = Qt.UserRole + 7
    BarColorRole = Qt.UserRole + 8

    def __init__(self) -> None:
        super().__init__()
        self._items: list[PlayerRow] = []

    def rowCount(self, _parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._items):
            return None
        item = self._items[row]
        if role == self.NameRole:
            return item.name
        if role == self.DamageRole:
            return int(round(item.damage))
        if role == self.HealRole:
            return int(round(item.heal))
        if role == self.DpsRole:
            return float(item.dps)
        if role == self.HpsRole:
            return float(item.hps)
        if role == self.BarRole:
            return float(item.bar_ratio)
        if role == self.RoleRole:
            return item.role
        if role == self.BarColorRole:
            return item.color
        return None

    def roleNames(self) -> dict[int, bytes]:  # type: ignore[override]
        return {
            self.NameRole: b"name",
            self.DamageRole: b"damage",
            self.HealRole: b"heal",
            self.DpsRole: b"dps",
            self.HpsRole: b"hps",
            self.BarRole: b"barRatio",
            self.RoleRole: b"role",
            self.BarColorRole: b"barColor",
        }

    def set_items(self, items: list[PlayerRow]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()


class HistoryModel(QAbstractListModel):
    LabelRole = Qt.UserRole + 1
    TotalsRole = Qt.UserRole + 2
    PlayersRole = Qt.UserRole + 3
    CopyRole = Qt.UserRole + 4

    def __init__(self) -> None:
        super().__init__()
        self._items: list[HistoryRow] = []

    def rowCount(self, _parent: QModelIndex | None = None) -> int:  # type: ignore[override]
        return len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:  # type: ignore[override]
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._items):
            return None
        item = self._items[row]
        if role == self.LabelRole:
            return item.label
        if role == self.TotalsRole:
            return item.totals
        if role == self.PlayersRole:
            return item.players
        if role == self.CopyRole:
            return item.copy_text
        return None

    def roleNames(self) -> dict[int, bytes]:  # type: ignore[override]
        return {
            self.LabelRole: b"label",
            self.TotalsRole: b"totals",
            self.PlayersRole: b"players",
            self.CopyRole: b"copyText",
        }

    def set_items(self, items: list[HistoryRow]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def get_copy_text(self, index: int) -> str | None:
        if index < 0 or index >= len(self._items):
            return None
        return self._items[index].copy_text


class UiState(QObject):
    modeChanged = Signal()
    zoneChanged = Signal()
    timeChanged = Signal()
    fameChanged = Signal()
    sortChanged = Signal()

    def __init__(
        self,
        *,
        sort_key: str,
        top_n: int,
        history_limit: int,
        set_mode_callback: Callable[[str], None] | None = None,
        role_lookup: Callable[[int], str | None] | None = None,
    ) -> None:
        super().__init__()
        self._mode = "battle"
        self._zone = "-"
        self._time_text = "-"
        self._fame_text = "0"
        self._fame_per_hour_text = "0.0"
        self._sort_key = sort_key
        self._top_n = top_n
        self._history_limit = history_limit
        self._set_mode_callback = set_mode_callback
        self._players = PlayerModel()
        self._history = HistoryModel()
        self._last_snapshot = None
        self._last_names: dict[int, str] = {}
        self._last_history: list[SessionSummary] = []
        self._role_lookup = role_lookup

    @Property(str, notify=modeChanged)
    def mode(self) -> str:
        return self._mode

    @Property(str, notify=zoneChanged)
    def zone(self) -> str:
        return self._zone

    @Property(str, notify=timeChanged)
    def timeText(self) -> str:
        return self._time_text

    @Property(str, notify=fameChanged)
    def fameText(self) -> str:
        return self._fame_text

    @Property(str, notify=fameChanged)
    def famePerHourText(self) -> str:
        return self._fame_per_hour_text

    @Property(str, notify=sortChanged)
    def sortKey(self) -> str:
        return self._sort_key

    @Property(QObject, constant=True)
    def playersModel(self) -> QObject:
        return self._players

    @Property(QObject, constant=True)
    def historyModel(self) -> QObject:
        return self._history

    @Property(int, constant=True)
    def historyLimit(self) -> int:
        return self._history_limit

    @Slot(str)
    def setSortKey(self, key: str) -> None:
        if key not in SORT_KEY_MAP:
            return
        if key == self._sort_key:
            return
        self._sort_key = key
        self.sortChanged.emit()
        if self._last_snapshot is not None:
            self._players.set_items(
                _build_player_rows(
                    self._last_snapshot.totals,
                    names=self._last_names,
                    sort_key=self._sort_key,
                    top_n=self._top_n,
                    role_lookup=self._role_lookup,
                )
            )

    @Slot(str)
    def setMode(self, mode: str) -> None:
        if mode not in ("battle", "zone", "manual"):
            return
        if self._set_mode_callback is not None:
            self._set_mode_callback(mode)
        self._set_mode(mode)

    @Slot(int)
    def copyHistory(self, index: int) -> None:
        text = self._history.get_copy_text(index)
        if not text:
            return
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard:
            clipboard.setText(text)

    def update(
        self,
        snapshot,
        *,
        names: dict[int, str],
        history: list[SessionSummary],
        mode: str,
        zone: str | None,
        fame_total: int,
        fame_per_hour: float,
    ) -> None:
        self._last_snapshot = snapshot
        self._last_names = dict(names)
        self._last_history = list(history)
        self._set_mode(mode)
        self._set_zone(zone or "-")
        self._set_time(snapshot.timestamp)
        self._set_fame(fame_total, fame_per_hour)
        self._players.set_items(
            _build_player_rows(
                snapshot.totals,
                names=names,
                sort_key=self._sort_key,
                top_n=self._top_n,
                role_lookup=self._role_lookup,
            )
        )
        self._history.set_items(
            _build_history_rows(history, names=names, limit=self._history_limit)
        )

    def _set_mode(self, mode: str) -> None:
        if mode != self._mode:
            self._mode = mode
            self.modeChanged.emit()

    def _set_zone(self, zone: str) -> None:
        if zone != self._zone:
            self._zone = zone
            self.zoneChanged.emit()

    def _set_time(self, timestamp: float) -> None:
        text = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        if text != self._time_text:
            self._time_text = text
            self.timeChanged.emit()

    def _set_fame(self, total: int, per_hour: float) -> None:
        total_text = str(int(total))
        per_hour_text = f"{per_hour:.1f}"
        if total_text != self._fame_text or per_hour_text != self._fame_per_hour_text:
            self._fame_text = total_text
            self._fame_per_hour_text = per_hour_text
            self.fameChanged.emit()


def _build_player_rows(
    totals: dict[int, dict[str, float]],
    *,
    names: dict[int, str],
    sort_key: str,
    top_n: int,
    role_lookup: Callable[[int], str | None] | None = None,
) -> list[PlayerRow]:
    rows: list[PlayerRow] = []
    metric = SORT_KEY_MAP.get(sort_key, "dps")
    max_damage = max((stats.get("damage", 0.0) for stats in totals.values()), default=0.0)
    max_heal = max((stats.get("heal", 0.0) for stats in totals.values()), default=0.0)
    for source_id, stats in totals.items():
        label = names.get(source_id) or str(source_id)
        damage = float(stats.get("damage", 0.0))
        heal = float(stats.get("heal", 0.0))
        dps = float(stats.get("dps", 0.0))
        hps = float(stats.get("hps", 0.0))
        role = None
        if role_lookup is not None:
            role = role_lookup(source_id)
        if not role:
            role = _infer_role(damage, heal, max_damage=max_damage, max_heal=max_heal)
        color = _color_for_label(label, role)
        rows.append(
            PlayerRow(
                name=label,
                damage=damage,
                heal=heal,
                dps=dps,
                hps=hps,
                bar_ratio=0.0,
                role=role or "",
                color=color,
            )
        )
    rows.sort(key=lambda item: _metric_value(item, metric), reverse=True)
    rows = rows[: max(top_n, 1)]
    max_value = max((_metric_value(item, metric) for item in rows), default=0.0)
    if max_value <= 0:
        return rows
    with_ratio = []
    for item in rows:
        ratio = _metric_value(item, metric) / max_value if max_value else 0.0
        with_ratio.append(
            PlayerRow(
                name=item.name,
                damage=item.damage,
                heal=item.heal,
                dps=item.dps,
                hps=item.hps,
                bar_ratio=ratio,
                role=item.role,
                color=item.color,
            )
        )
    return with_ratio


def _metric_value(item: PlayerRow, metric: str) -> float:
    if metric == "damage":
        return item.damage
    if metric == "heal":
        return item.heal
    if metric == "hps":
        return item.hps
    return item.dps


def _infer_role(damage: float, heal: float, *, max_damage: float, max_heal: float) -> str | None:
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


def _build_history_rows(
    history: list[SessionSummary],
    *,
    names: dict[int, str],
    limit: int,
) -> list[HistoryRow]:
    rows: list[HistoryRow] = []
    for summary in history[: max(limit, 1)]:
        label = summary.mode
        if summary.mode == "zone" and summary.label:
            label = f"zone {summary.label}"
        duration = _format_duration(summary.duration)
        totals = _format_totals(summary.total_damage, summary.total_heal)
        players = _format_players(summary.entries, names=names, max_players=3)
        copy_text = _format_history_copy(summary, names=names)
        rows.append(
            HistoryRow(
                label=f"{label} {duration}",
                totals=totals,
                players=players,
                copy_text=copy_text,
            )
        )
    return rows


def _format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes:02d}:{secs:02d}"


def _format_totals(total_damage: float, total_heal: float) -> str:
    return f"total dmg {_format_int(total_damage)} heal {_format_int(total_heal)}"


def _format_players(
    entries: list[SessionEntry],
    *,
    names: dict[int, str],
    max_players: int,
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


def _format_history_copy(summary: SessionSummary, *, names: dict[int, str]) -> str:
    label = summary.mode
    if summary.mode == "zone" and summary.label:
        label = f"zone {summary.label}"
    duration = _format_duration(summary.duration)
    totals = _format_totals(summary.total_damage, summary.total_heal)
    players_text = _format_players(
        summary.entries, names=names, max_players=HISTORY_MAX_PLAYERS
    )
    return f"{label} {duration} | {totals} | {players_text}"


def _format_int(value: float) -> int:
    return int(round(value))


def _resolve_label(label: str, names: dict[int, str]) -> str:
    if label.isdigit():
        mapped = names.get(int(label))
        if mapped:
            return mapped
    return label


def _shorten_label(label: str, limit: int = HISTORY_LABEL_LIMIT) -> str:
    if len(label) <= limit:
        return label
    if limit <= 3:
        return label[:limit]
    return f"{label[: limit - 3]}..."
