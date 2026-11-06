from __future__ import annotations

from dmf_promsd.cache import PromSDCache
from tests.netbox45 import FakeNetbox, device

LANE_TAGS = ["monitoring:scrape", "monitoring:probe", "monitoring:snmp"]


def _devices() -> list[dict]:
    return [
        device("node-1", ["monitoring:scrape"], address="192.0.2.11/24", metrics_port=9100),
        device("switch-1", ["monitoring:snmp"], address="192.0.2.31/24"),
    ]


def _devices_for_every_lane() -> list[dict]:
    return [
        *_devices(),
        device("encoder-1", ["monitoring:probe"], address="192.0.2.21/24", probe_module="icmp"),
    ]


# A token without tag-view access sees an empty tag list, the same as a NetBox with no
# lane tags. The refresh fails so the lanes don't go empty without a signal.
def test_no_visible_lane_tag_fails_the_refresh_and_keeps_the_last_snapshot():
    netbox = FakeNetbox(devices=_devices())
    client = netbox.client()
    cache = PromSDCache(client=client, ttl_seconds=60)
    try:
        assert cache.refresh()
        good = cache.get_snapshot()
        assert good is not None

        for name in LANE_TAGS:
            netbox.remove_tag(name)
        assert not cache.refresh()

        state = cache.state()
        assert state.snapshot is good
        assert state.last_error is not None
        for name in LANE_TAGS:
            assert name in state.last_error
        assert "extras.view_tag" in state.last_error
    finally:
        client.close()


# A token limited to some lane tags can't tell a hidden tag from a missing one, so any
# unseen lane tag fails the refresh and the earlier lanes stay.
def test_a_partly_visible_lane_tag_set_fails_the_refresh_and_keeps_every_lane():
    netbox = FakeNetbox(devices=_devices_for_every_lane())
    client = netbox.client()
    cache = PromSDCache(client=client, ttl_seconds=60)
    try:
        assert cache.refresh()
        good = cache.get_snapshot()
        assert good is not None
        assert good.lanes["probe"] and good.lanes["snmp"]

        netbox.remove_tag("monitoring:probe")
        netbox.remove_tag("monitoring:snmp")
        assert not cache.refresh()

        state = cache.state()
        assert state.snapshot is good
        assert cache.lane_payload("probe") == good.lanes["probe"]
        assert cache.lane_payload("snmp") == good.lanes["snmp"]
        assert state.last_error is not None
        assert "monitoring:probe, monitoring:snmp" in state.last_error
        assert "monitoring:scrape" not in state.last_error
        assert "extras.view_tag" in state.last_error
    finally:
        client.close()


def test_an_unseen_lane_tag_on_the_first_refresh_leaves_no_snapshot():
    client = FakeNetbox(devices=_devices(), absent_tags=["monitoring:probe"]).client()
    cache = PromSDCache(client=client, ttl_seconds=60)
    try:
        assert not cache.refresh()
        assert cache.get_snapshot() is None
        assert cache.state().last_error is not None
    finally:
        client.close()
