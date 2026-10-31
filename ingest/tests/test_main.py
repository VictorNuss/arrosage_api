import json
import queue

import pytest

from app import main


class _FakeMessage:
    def __init__(self, topic, payload, retain=False):
        self.topic = topic
        self.retain = retain
        self.payload = json.dumps(payload).encode("utf-8")


def _drain(q):
    items = []
    try:
        while True:
            items.append(q.get_nowait())
    except queue.Empty:
        return items


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append(topic)


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


@pytest.mark.parametrize(
    "key,payload",
    [("humidity_pct", {"value": 100}), ("vanne_1", {"state": "closed"}), ("water_distance_cm", {"value": 40})],
)
def test_retained_reading_replayed_on_subscribe_is_not_stored(key, payload):
    """Régression : la dernière valeur retenue d'un appareil éteint était
    restockée avec l'heure du redémarrage d'ingest, le faisant passer pour
    vivant (et une vanne fermée ajoutait un faux évènement au journal)."""
    client = _FakeClient()
    main._on_message(client, None, _FakeMessage(f"arrosage/arrosage-01/etat/{key}", payload, retain=True))
    assert _drain(main._pending_rows) == []
    assert client.published == []


def test_retained_device_info_is_still_applied():
    """IP et version firmware ne sont pas des mesures horodatées : la valeur
    retenue reste la bonne information à garder en base."""
    message = _FakeMessage("arrosage/jardin-1/etat/fw_version", {"value": "v1.2.0"}, retain=True)
    main._on_message(None, None, message)
    assert _drain(main._pending_device_info) == [("jardin-1", "fw_version", "v1.2.0")]


# --- db.valve_event_rows : journal permanent des vannes -----------------------

def test_valve_event_rows_keeps_only_valves_without_unit():
    from app import db

    rows = [
        {"time": 1, "device_id": "jardin-1", "metric": "vanne_1", "value": 1.0, "unit": None},
        {"time": 1, "device_id": "jardin-1", "metric": "temperature_c", "value": 21.5, "unit": "°C"},
    ]
    assert db.valve_event_rows(rows) == [{"time": 1, "device_id": "jardin-1", "metric": "vanne_1", "value": 1.0}]
