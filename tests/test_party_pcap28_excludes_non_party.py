from __future__ import annotations

from pathlib import Path

import pytest

from albion_dps.domain import NameRegistry, PartyRegistry
from albion_dps.meter.session_meter import SessionMeter
from albion_dps.pipeline import replay_snapshots
from albion_dps.protocol.combat_mapper import CombatEventMapper
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.registry import default_registry


def test_pcap28_excludes_non_party_name() -> None:
    pcap_path = Path("albion_dps/artifacts/pcaps/albion_combat_28_party.pcap")
    if not pcap_path.exists():
        pytest.skip(f"Missing PCAP fixture: {pcap_path}")

    decoder = PhotonDecoder(registry=default_registry())
    mapper = CombatEventMapper(clamp_overkill=True)
    names = NameRegistry()
    party = PartyRegistry()
    meter = SessionMeter(mode="battle", history_limit=20, name_lookup=names.lookup)

    for _snap in replay_snapshots(
        pcap_path,
        decoder,
        meter,
        name_registry=names,
        party_registry=party,
        event_mapper=mapper.map,
        snapshot_interval=0.0,
    ):
        pass

    name_map = names.snapshot()
    draxier_id = next((eid for eid, name in name_map.items() if name == "Draxier"), None)
    if draxier_id is None:
        pytest.skip("Draxier not present in PCAP names")

    assert draxier_id not in party.snapshot_ids()

    history = meter.history(limit=10)
    for summary in history:
        for entry in summary.entries:
            assert entry.label not in ("Draxier", str(draxier_id))
