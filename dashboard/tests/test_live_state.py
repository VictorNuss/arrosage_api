import json
from datetime import datetime, timezone

import pytest

from app import live_state


class _FakeMessage:
    def __init__(self, topic, payload_dict):
        self.topic = topic
        self.payload = json.dumps(payload_dict).encode("utf-8")


# --- _parse_topic -----------------------------------------------------------

def test_parse_topic_extracts_device_id():
    assert live_state._parse_topic("arrosage/jardin-1/etat") == "jardin-1"


@pytest.mark.parametrize(
    "topic",
    [
        "arrosage/jardin-1/commande",  # mauvais suffixe
        "autre/jardin-1/etat",  # mauvais préfixe
        "arrosage/etat",  # topic incomplet
        "arrosage/jardin-1/etat/extra",  # trop de segments
    ],
)
def test_parse_topic_rejects_unexpected_shapes(topic):
    assert live_state._parse_topic(topic) is None


# --- _infer_unit --------------------------------------------------------------

@pytest.mark.parametrize(
    "metric,expected_unit",
    [
        ("water_level_cm", "cm"),
        ("battery_v", "V"),
        ("temperature_c", "°C"),
        ("humidity_pct", "%"),
        ("vanne_1", None),
    ],
)
def test_infer_unit_from_suffix(metric, expected_unit):
    assert live_state._infer_unit(metric) == expected_unit


# --- _coerce_value ------------------------------------------------------------

@pytest.mark.parametrize(
    "raw_value,expected",
    [
        (True, 1.0),
        (False, 0.0),
        (21.3, 21.3),
        (5, 5.0),
        ("open", 1.0),
        ("closed", 0.0),
        ("on", 1.0),
        ("off", 0.0),
        ("1", 1.0),
        ("34.5", 34.5),
    ],
)
def test_coerce_value_normalizes_known_shapes(raw_value, expected):
    assert live_state._coerce_value(raw_value) == expected


def test_coerce_value_rejects_unparseable_string():
    with pytest.raises(ValueError):
        live_state._coerce_value("pas-un-nombre")


# --- _on_message / get_latest_readings ---------------------------------------

def test_on_message_updates_cache_instantly():
    message = _FakeMessage(
        "arrosage/jardin-1/etat",
        {"ts": "2026-07-16T10:00:00Z", "vanne_1": "open", "temperature_c": 21.3},
    )
    live_state._on_message(None, None, message)

    readings = {(r["device_id"], r["metric"]): r for r in live_state.get_latest_readings()}
    assert readings[("jardin-1", "vanne_1")]["value"] == 1.0
    assert readings[("jardin-1", "temperature_c")]["value"] == 21.3
    assert readings[("jardin-1", "temperature_c")]["unit"] == "°C"
    # "ts" ne doit jamais devenir une métrique.
    assert ("jardin-1", "ts") not in readings


def test_on_message_ignores_unparseable_metric_but_keeps_the_rest():
    message = _FakeMessage("arrosage/jardin-1/etat", {"vanne_1": "open", "temperature_c": "n/a"})
    live_state._on_message(None, None, message)

    readings = {(r["device_id"], r["metric"]): r for r in live_state.get_latest_readings()}
    assert ("jardin-1", "vanne_1") in readings
    assert ("jardin-1", "temperature_c") not in readings


def test_on_message_ignores_malformed_json():
    message = type("M", (), {"topic": "arrosage/jardin-1/etat", "payload": b"{not json"})()
    live_state._on_message(None, None, message)
    assert live_state.get_latest_readings() == []


def test_on_message_records_a_recent_timestamp():
    message = _FakeMessage("arrosage/jardin-1/etat", {"temperature_c": 21.3})
    before = datetime.now(timezone.utc)
    live_state._on_message(None, None, message)

    readings = {(r["device_id"], r["metric"]): r for r in live_state.get_latest_readings()}
    recorded_time = readings[("jardin-1", "temperature_c")]["time"]
    assert recorded_time >= before
