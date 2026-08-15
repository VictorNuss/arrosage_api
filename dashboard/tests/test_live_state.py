import json
from datetime import datetime, timezone

import pytest

from app import live_state


class _FakeMessage:
    def __init__(self, topic, payload_dict):
        self.topic = topic
        self.payload = json.dumps(payload_dict).encode("utf-8")


# --- _parse_topic -----------------------------------------------------------

def test_parse_topic_extracts_device_id_and_key():
    assert live_state._parse_topic("arrosage/jardin-1/etat/temperature_c") == ("jardin-1", "temperature_c")
    assert live_state._parse_topic("arrosage/jardin-1/etat/vanne_1") == ("jardin-1", "vanne_1")


@pytest.mark.parametrize(
    "topic",
    [
        "arrosage/jardin-1/etat",  # ancien contrat (JSON combiné), plus supporté
        "arrosage/jardin-1/commande",
        "autre/jardin-1/etat/temperature_c",
        "arrosage/etat/temperature_c",
        "arrosage/jardin-1/etat/temperature_c/extra",
    ],
)
def test_parse_topic_rejects_unexpected_shapes(topic):
    assert live_state._parse_topic(topic) is None


# --- _is_valve_metric / _infer_unit --------------------------------------------

def test_is_valve_metric():
    assert live_state._is_valve_metric("vanne_1") is True
    assert live_state._is_valve_metric("temperature_c") is False


@pytest.mark.parametrize(
    "metric,expected_unit",
    [("water_level_cm", "cm"), ("battery_v", "V"), ("temperature_c", "°C"), ("humidity_pct", "%"), ("vanne_1", None)],
)
def test_infer_unit_from_suffix(metric, expected_unit):
    assert live_state._infer_unit(metric) == expected_unit


# --- _coerce_sensor_value / _coerce_valve_state --------------------------------

@pytest.mark.parametrize("raw_value,expected", [(21.3, 21.3), (5, 5.0)])
def test_coerce_sensor_value_accepts_numbers(raw_value, expected):
    assert live_state._coerce_sensor_value(raw_value) == expected


@pytest.mark.parametrize("raw_value", [True, False, "21.3", None])
def test_coerce_sensor_value_rejects_non_numeric(raw_value):
    with pytest.raises(ValueError):
        live_state._coerce_sensor_value(raw_value)


@pytest.mark.parametrize(
    "raw_value,expected",
    [("open", 1.0), ("closed", 0.0), (True, 1.0), (False, 0.0), ("on", 1.0), ("off", 0.0)],
)
def test_coerce_valve_state_normalizes_known_shapes(raw_value, expected):
    assert live_state._coerce_valve_state(raw_value) == expected


def test_coerce_valve_state_rejects_unrecognized_string():
    with pytest.raises(ValueError):
        live_state._coerce_valve_state("maybe")


# --- _on_message / get_latest_readings -----------------------------------------

def test_on_message_updates_cache_for_a_sensor():
    message = _FakeMessage("arrosage/jardin-1/etat/temperature_c", {"value": 21.3})
    live_state._on_message(None, None, message)

    readings = {(r["device_id"], r["metric"]): r for r in live_state.get_latest_readings()}
    assert readings[("jardin-1", "temperature_c")]["value"] == 21.3
    assert readings[("jardin-1", "temperature_c")]["unit"] == "°C"


def test_on_message_updates_cache_for_a_valve():
    message = _FakeMessage("arrosage/jardin-1/etat/vanne_1", {"state": "open"})
    live_state._on_message(None, None, message)

    readings = {(r["device_id"], r["metric"]): r for r in live_state.get_latest_readings()}
    assert readings[("jardin-1", "vanne_1")]["value"] == 1.0


def test_on_message_only_touches_the_published_key():
    """Régression : contrairement à l'ancien contrat (JSON combiné), un
    message ne doit mettre à jour QUE sa propre clé, pas en effacer d'autres."""
    live_state._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/temperature_c", {"value": 21.3}))
    live_state._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/vanne_1", {"state": "open"}))

    readings = {(r["device_id"], r["metric"]) for r in live_state.get_latest_readings()}
    assert ("jardin-1", "temperature_c") in readings
    assert ("jardin-1", "vanne_1") in readings


def test_on_message_ignores_sensor_payload_missing_value_field():
    live_state._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/temperature_c", {"unexpected": 1}))
    assert live_state.get_latest_readings() == []


def test_on_message_ignores_valve_payload_missing_state_field():
    live_state._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/vanne_1", {"value": 1}))
    assert live_state.get_latest_readings() == []


def test_on_message_ignores_malformed_json():
    message = type("M", (), {"topic": "arrosage/jardin-1/etat/temperature_c", "payload": b"{not json"})()
    live_state._on_message(None, None, message)
    assert live_state.get_latest_readings() == []


def test_on_message_ignores_unexpected_topic_shape():
    message = _FakeMessage("arrosage/jardin-1/etat", {"temperature_c": 21.3})
    live_state._on_message(None, None, message)
    assert live_state.get_latest_readings() == []


def test_on_message_records_a_recent_timestamp():
    before = datetime.now(timezone.utc)
    live_state._on_message(None, None, _FakeMessage("arrosage/jardin-1/etat/temperature_c", {"value": 21.3}))

    readings = {(r["device_id"], r["metric"]): r for r in live_state.get_latest_readings()}
    assert readings[("jardin-1", "temperature_c")]["time"] >= before
