from __future__ import annotations

from pathlib import Path

import pytest

from albion_dps.domain import NameRegistry, PartyRegistry
from albion_dps.meter.session_meter import SessionMeter
from albion_dps.pipeline import replay_snapshots
from albion_dps.protocol.combat_mapper import CombatEventMapper
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.registry import default_registry


def test_pcap34_self_name_is_mapped() -> None:
    pcap_path = Path("albion_dps/artifacts/pcaps/albion_combat_34_party.pcap")
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

    self_ids = party.snapshot_self_ids()
    assert self_ids
    assert party._self_name
    for self_id in self_ids:
        assert names.lookup(self_id) == party._self_name

    history = meter.history(limit=10)
    assert history
    for summary in history:
        labels = {entry.label for entry in summary.entries}
        for self_id in self_ids:
            assert str(self_id) not in labels
