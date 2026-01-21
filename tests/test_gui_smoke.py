from __future__ import annotations

import asyncio
import queue

import pytest

from albion_dps.models import MeterSnapshot


def test_gui_smoke_headless() -> None:
    try:
        from albion_dps.gui.textual_app import AlbionDpsApp
        from textual.widgets import DataTable
    except Exception:
        pytest.skip("Textual not available")

    if not hasattr(AlbionDpsApp, "run_test"):
        pytest.skip("Textual run_test not available")

    snapshot_queue: queue.Queue[MeterSnapshot | None] = queue.Queue()

    async def runner() -> None:
        app = AlbionDpsApp(
            snapshot_queue=snapshot_queue,
            sort_key="dps",
            top_n=5,
            mode="battle",
            zone_label_provider=lambda: None,
            history_provider=lambda _limit: [],
            history_limit=5,
            set_mode=lambda _mode: None,
        )
        async with app.run_test() as pilot:
            snapshot_queue.put(
                MeterSnapshot(
                    timestamp=123.456,
                    totals={
                        1: {"damage": 100.0, "heal": 0.0, "dps": 10.0, "hps": 0.0},
                        2: {"damage": 50.0, "heal": 20.0, "dps": 5.0, "hps": 2.0},
                    },
                    names={1: "Dps", 2: "Heal"},
                )
            )
            app._drain_queue()
            await pilot.pause(0.1)
            table = app.query_one(DataTable)
            assert table.row_count > 0

    asyncio.run(runner())
