from datetime import datetime, timezone

import pytest

from app import mqtt_client


# --- parse_topic ----------------------------------------------------------

def test_parse_topic_extracts_device_id():
    assert mqtt_client.parse_topic("arrosage/jardin-1/etat") == "jardin-1"


@pytest.mark.parametrize(
    "topic",
    ["arrosage/jardin-1/commande", "autre/jardin-1/etat", "arrosage/etat", "arrosage/jardin-1/etat/extra"],
)
def test_parse_topic_rejects_unexpected_shapes(topic):
    assert mqtt_client.parse_topic(topic) is None


# --- _infer_unit -----------------------------------------------------------

@pytest.mark.parametrize(
    "metric,expected_unit",
    [
        ("water_level_cm", "cm"),
        ("rain_mm", "mm"),
        ("humidity_pct", "%"),
        ("temperature_c", "°C"),
        ("battery_v", "V"),
        ("vanne_1", None),
    ],
)
def test_infer_unit_from_suffix(metric, expected_unit):
    assert mqtt_client._infer_unit(metric) == expected_unit


# --- _coerce_value -----------------------------------------------------------

@pytest.mark.parametrize(
    "raw_value,expected",
    [
        (True, 1.0),
        (False, 0.0),
        (21.3, 21.3),
        (5, 5.0),
        ("open", 1.0),
        ("closed", 0.0),
        ("ouvert", 1.0),
        ("fermee", 0.0),
        ("1", 1.0),
        ("34.5", 34.5),
    ],
)
def test_coerce_value_normalizes_known_shapes(raw_value, expected):
    assert mqtt_client._coerce_value("vanne_1", raw_value) == expected


def test_coerce_value_rejects_unparseable_string():
    with pytest.raises(ValueError):
        mqtt_client._coerce_value("temperature_c", "pas-un-nombre")


def test_coerce_value_rejects_unsupported_type():
    with pytest.raises(ValueError):
        mqtt_client._coerce_value("temperature_c", {"nested": "object"})


# --- parse_payload ------------------------------------------------------------

def test_parse_payload_flattens_every_field_into_a_row():
    payload = b'{"water_level_cm": 34.5, "vanne_1": "open"}'
    rows = mqtt_client.parse_payload("jardin-1", payload)

    by_metric = {r["metric"]: r for r in rows}
    assert by_metric["water_level_cm"]["value"] == 34.5
    assert by_metric["water_level_cm"]["unit"] == "cm"
    assert by_metric["vanne_1"]["value"] == 1.0
    assert all(r["device_id"] == "jardin-1" for r in rows)


def test_parse_payload_uses_explicit_ts_when_present():
    payload = b'{"ts": "2026-07-16T10:00:00Z", "temperature_c": 21.3}'
    rows = mqtt_client.parse_payload("jardin-1", payload)
    assert rows[0]["time"] == datetime(2026, 7, 16, 10, 0, 0, tzinfo=timezone.utc)


def test_parse_payload_uses_now_when_ts_is_absent():
    before = datetime.now(timezone.utc)
    rows = mqtt_client.parse_payload("jardin-1", b'{"temperature_c": 21.3}')
    assert rows[0]["time"] >= before


def test_parse_payload_never_emits_ts_as_a_metric():
    rows = mqtt_client.parse_payload("jardin-1", b'{"ts": "2026-07-16T10:00:00Z", "temperature_c": 21.3}')
    assert all(r["metric"] != "ts" for r in rows)


def test_parse_payload_skips_bad_metric_but_keeps_the_rest():
    """Régression : un seul champ invalide ne doit pas faire perdre tout le message."""
    rows = mqtt_client.parse_payload("jardin-1", b'{"temperature_c": "n/a", "vanne_1": "open"}')
    metrics = {r["metric"] for r in rows}
    assert metrics == {"vanne_1"}


def test_parse_payload_rejects_non_object_json():
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", b"[1, 2, 3]")
