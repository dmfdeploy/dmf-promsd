from __future__ import annotations

from dmf_promsd.cache import PromSDCache
from tests.netbox45 import FakeNetbox, device

LANE_TAGS = ["monitoring:scrape", "monitoring:probe", "monitoring:snmp"]


def _devices() -> list[dict]:
    return [
        device("node-1", ["monitoring:scrape"], address="192.0.2.11/24", metrics_port=9100),
        device("switch-1", ["monitoring:snmp"], address="192.0.2.31/24"),
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
        assert "No lane tag is visible" in state.last_error
        assert "extras.view_tag" in state.last_error
    finally:
        client.close()


def test_no_visible_lane_tag_on_first_refresh_leaves_no_snapshot():
    client = FakeNetbox(devices=_devices(), absent_tags=LANE_TAGS).client()
    cache = PromSDCache(client=client, ttl_seconds=60)
    try:
        assert not cache.refresh()
        assert cache.get_snapshot() is None
    finally:
        client.close()


# One visible lane tag proves the token can view tags, so the others are truly missing.
def test_one_visible_lane_tag_keeps_the_missing_lane_case():
    absent = ["monitoring:probe", "monitoring:snmp"]
    client = FakeNetbox(devices=_devices(), absent_tags=absent).client()
    cache = PromSDCache(client=client, ttl_seconds=60)
    try:
        assert cache.refresh()
        snapshot = cache.get_snapshot()
        assert snapshot is not None
        assert snapshot.missing_tags == sorted(absent)
        assert cache.state().last_error is None
    finally:
        client.close()
