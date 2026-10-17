import json
import queue

import pytest

from app import main


class _FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


def _drain(q):
    items = []
    try:
        while True:
            items.append(q.get_nowait())
    except queue.Empty:
        return items


@pytest.fixture(autouse=True)
def _empty_queues():
    _drain(main._pending_rows)
    _drain(main._pending_device_info)
    yield
    _drain(main._pending_rows)
    _drain(main._pending_device_info)


@pytest.mark.parametrize(
    "key,payload,expected",
    [
        ("ip", {"value": "192.168.1.50"}, ("jardin-1", "ip_address", "192.168.1.50")),
        ("fw_version", {"value": "v1.2.0"}, ("jardin-1", "fw_version", "v1.2.0")),
    ],
)
def test_device_info_keys_go_to_devices_not_readings(key, payload, expected):
    main._on_message(None, None, _FakeMessage(f"arrosage/jardin-1/etat/{key}", payload))
    assert _drain(main._pending_device_info) == [expected]
    assert _drain(main._pending_rows) == []


def test_invalid_device_info_payload_is_dropped():
    main._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/fw_version", {"value": ""}))
    assert _drain(main._pending_device_info) == []
    assert _drain(main._pending_rows) == []


def test_sensor_reading_still_goes_to_readings():
    main._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/temperature_c", {"value": 21.5}))
    rows = _drain(main._pending_rows)
    assert len(rows) == 1 and rows[0][0] == "jardin-1"
    assert _drain(main._pending_device_info) == []
