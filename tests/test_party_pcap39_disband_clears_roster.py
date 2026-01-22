from __future__ import annotations

from pathlib import Path

import pytest

from albion_dps.capture.replay_pcap import replay_pcap
from albion_dps.domain import NameRegistry, PartyRegistry
from albion_dps.protocol.photon_decode import PhotonDecoder
from albion_dps.protocol.registry import default_registry


def test_pcap39_party_disband_clears_roster() -> None:
    pcap_path = Path("albion_dps/artifacts/pcaps/albion_combat_39_party_withD4dits.pcap")
    if not pcap_path.exists():
        pytest.skip(f"Missing PCAP fixture: {pcap_path}")

    decoder = PhotonDecoder(registry=default_registry())
    names = NameRegistry()
    party = PartyRegistry()

    for packet in replay_pcap(pcap_path):
        party.observe_packet(packet)
        for message in decoder.decode_all(packet):
            names.observe(message)
            party.observe(message, packet)
            party.sync_names(names)
            party.sync_self_name(names)
            party.sync_id_names(names)

    assert not party.snapshot_names()
    assert party.snapshot_ids().issubset(party.snapshot_self_ids())
