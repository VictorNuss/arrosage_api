import json
import queue

import pytest

from app import main, tank


class _FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8")


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), qos, retain))


@pytest.fixture(autouse=True)
def _tank_settings(monkeypatch):
    monkeypatch.setattr(tank.config, "TANK_DISTANCE_EMPTY_CM", 150.0)
    monkeypatch.setattr(tank.config, "TANK_DISTANCE_FULL_CM", 25.0)


@pytest.fixture(autouse=True)
def _empty_rows():
    yield
    try:
        while True:
            main._pending_rows.get_nowait()
    except queue.Empty:
        pass


@pytest.mark.parametrize(
    "distance,expected",
    [
        (150.0, 0.0),  # surface au fond : cuve vide
        (25.0, 125.0),  # surface au plus près : cuve pleine
        (100.0, 50.0),
        (180.0, 0.0),  # plus loin que le fond (écho parasite)
        (10.0, 125.0),  # dans la zone morte / débordement
        (87.34, 62.7),
    ],
)
def test_water_level_from_distance(distance, expected):
    assert tank.water_level_cm(distance) == expected


def test_inconsistent_settings_give_no_level(monkeypatch):
    monkeypatch.setattr(tank.config, "TANK_DISTANCE_FULL_CM", 150.0)
    assert tank.tank_capacity_cm() is None
    assert tank.water_level_cm(100.0) is None


def test_distance_is_stored_raw_and_level_republished():
    client = _FakeClient()
    main._on_message(client, None, _FakeMessage("arrosage/jardin-1/etat/water_distance_cm", {"value": 100.0}))

    device_id, rows = main._pending_rows.get_nowait()
    assert (device_id, rows[0]["metric"], rows[0]["value"], rows[0]["unit"]) == (
        "jardin-1",
        "water_distance_cm",
        100.0,
        "cm",
    )
    assert client.published == [("arrosage/jardin-1/etat/water_level_cm", {"value": 50.0}, 1, True)]


def test_republished_level_is_stored_without_new_republish():
    client = _FakeClient()
    main._on_message(client, None, _FakeMessage("arrosage/jardin-1/etat/water_level_cm", {"value": 50.0}))
    _, rows = main._pending_rows.get_nowait()
    assert rows[0]["metric"] == "water_level_cm"
    assert client.published == []
