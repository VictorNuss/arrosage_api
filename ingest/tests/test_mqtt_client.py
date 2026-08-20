import json
from datetime import datetime, timezone

import pytest

from app import mqtt_client


# --- parse_topic ------------------------------------------------------------

def test_parse_topic_extracts_device_id_and_key():
    assert mqtt_client.parse_topic("arrosage/jardin-1/etat/temperature_c") == ("jardin-1", "temperature_c")
    assert mqtt_client.parse_topic("arrosage/jardin-1/etat/vanne_1") == ("jardin-1", "vanne_1")


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
    assert mqtt_client.parse_topic(topic) is None


# --- is_valve_metric ----------------------------------------------------------

@pytest.mark.parametrize("metric", ["vanne_1", "vanne_arrosage_potager", "VANNE_2"])
def test_is_valve_metric_true_for_valve_names(metric):
    assert mqtt_client.is_valve_metric(metric) is True


@pytest.mark.parametrize("metric", ["water_level_cm", "temperature_c", "battery_v"])
def test_is_valve_metric_false_for_sensor_names(metric):
    assert mqtt_client.is_valve_metric(metric) is False


# --- _infer_unit ---------------------------------------------------------------

@pytest.mark.parametrize(
    "metric,expected_unit",
    [("water_level_cm", "cm"), ("humidity_pct", "%"), ("temperature_c", "°C"), ("battery_v", "V"), ("vanne_1", None)],
)
def test_infer_unit_from_suffix(metric, expected_unit):
    assert mqtt_client._infer_unit(metric) == expected_unit


# --- parse_payload : capteur (champ "value") -----------------------------------

def test_parse_payload_sensor_reads_value_field():
    rows = mqtt_client.parse_payload("jardin-1", "temperature_c", b'{"value": 21.3}')
    assert len(rows) == 1
    row = rows[0]
    assert row["device_id"] == "jardin-1"
    assert row["metric"] == "temperature_c"
    assert row["value"] == 21.3
    assert row["unit"] == "°C"


def test_parse_payload_sensor_uses_reception_time_not_a_ts_field():
    """Le nouveau contrat n'a pas de champ ts : un ts fourni est ignoré, seul
    l'horodatage de réception fait foi."""
    before = datetime.now(timezone.utc)
    rows = mqtt_client.parse_payload("jardin-1", "temperature_c", b'{"value": 21.3, "ts": "2020-01-01T00:00:00Z"}')
    assert rows[0]["time"] >= before


def test_parse_payload_sensor_missing_value_field_raises():
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", "temperature_c", b"{}")


def test_parse_payload_sensor_non_numeric_value_raises():
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", "temperature_c", b'{"value": "n/a"}')


def test_parse_payload_sensor_boolean_value_raises():
    """bool est une sous-classe d'int en Python : à exclure explicitement."""
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", "temperature_c", b'{"value": true}')


# --- parse_payload : vanne (champ "state") -------------------------------------

@pytest.mark.parametrize(
    "state_raw,expected_value",
    [("open", 1.0), ("closed", 0.0), ("on", 1.0), ("off", 0.0), ("ouvert", 1.0)],
)
def test_parse_payload_valve_reads_state_field(state_raw, expected_value):
    payload = json.dumps({"state": state_raw}).encode("utf-8")
    rows = mqtt_client.parse_payload("jardin-1", "vanne_1", payload)
    assert rows[0]["value"] == expected_value
    assert rows[0]["unit"] is None


@pytest.mark.parametrize("state_raw", ["transitioning", "transition", "moving", "opening", "closing"])
def test_parse_payload_valve_reads_transition_state(state_raw):
    """Une électrovanne motorisée met un temps variable à s'ouvrir/se
    fermer (les deux sens) : le firmware publie "transitioning" comme état
    intermédiaire stable pendant ce délai."""
    payload = json.dumps({"state": state_raw}).encode("utf-8")
    rows = mqtt_client.parse_payload("jardin-1", "vanne_1", payload)
    assert rows[0]["value"] == 0.5


def test_parse_payload_valve_missing_state_field_raises():
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", "vanne_1", b'{"value": 1}')


# --- is_valid_ipv4 / parse_ip_payload ("ip", clé publiée à chaque connexion) --

@pytest.mark.parametrize("value", ["192.168.1.50", "0.0.0.0", "255.255.255.255", "10.0.0.1"])
def test_is_valid_ipv4_accepts_well_formed_addresses(value):
    assert mqtt_client.is_valid_ipv4(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "256.1.1.1",  # octet hors plage
        "192.168.1",  # pas assez de segments
        "192.168.1.1.5",  # trop de segments
        "not-an-ip",
        "",
        None,
        192,
    ],
)
def test_is_valid_ipv4_rejects_malformed_values(value):
    assert mqtt_client.is_valid_ipv4(value) is False


def test_parse_ip_payload_extracts_the_address():
    payload = json.dumps({"value": "192.168.1.50"}).encode("utf-8")
    assert mqtt_client.parse_ip_payload(payload) == "192.168.1.50"


def test_parse_ip_payload_rejects_invalid_address():
    payload = json.dumps({"value": "not-an-ip"}).encode("utf-8")
    with pytest.raises(ValueError):
        mqtt_client.parse_ip_payload(payload)


def test_parse_ip_payload_missing_value_field_raises():
    with pytest.raises(ValueError):
        mqtt_client.parse_ip_payload(b'{"unexpected": 1}')


def test_parse_payload_valve_unrecognized_state_raises():
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", "vanne_1", b'{"state": "maybe"}')


def test_parse_payload_rejects_non_object_json():
    with pytest.raises(ValueError):
        mqtt_client.parse_payload("jardin-1", "temperature_c", b"[1, 2, 3]")
