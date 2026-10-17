import json

import pytest

from app import main, zigbee


class _FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, json.loads(payload), qos, retain))


# --- parse_topic ------------------------------------------------------------

def test_parse_topic_extracts_friendly_name():
    assert zigbee.parse_topic("zigbee2mqtt/sol-potager") == "sol-potager"
    assert zigbee.parse_topic("zigbee2mqtt/0xa4c1380000000000") == "0xa4c1380000000000"


@pytest.mark.parametrize(
    "topic",
    [
        "zigbee2mqtt/bridge",
        "zigbee2mqtt/bridge/state",
        "zigbee2mqtt/sol-potager/availability",
        "zigbee2mqtt/sol-potager/set",
        "zigbee2mqtt/",
        "zigbee2mqtt",
        "arrosage/jardin-1/etat/temperature_c",
        "autre/sol-potager",
    ],
)
def test_parse_topic_rejects_non_state_topics(topic):
    assert zigbee.parse_topic(topic) is None


def test_parse_topic_disabled_when_base_topic_empty(monkeypatch):
    monkeypatch.setattr(zigbee.config, "ZIGBEE2MQTT_BASE_TOPIC", "")
    assert zigbee.parse_topic("zigbee2mqtt/sol-potager") is None


# --- translate_payload --------------------------------------------------------

def _translate(payload):
    return dict(zigbee.translate_payload(json.dumps(payload).encode("utf-8")))


def test_translate_maps_tuya_soil_sensor_fields():
    payload = {
        "soil_moisture": 42,
        "temperature": 18.3,
        "humidity": 71,
        "illuminance": 1200,
        "soil_fertility": 350,
        "battery": 87,
        "battery_state": "high",
        "linkquality": 110,
    }
    assert _translate(payload) == {
        "humidity_pct": 42.0,
        "temperature_c": 18.3,
        "air_humidity_pct_rh": 71.0,
        "illuminance_lux": 1200.0,
        "fertility_us_cm": 350.0,
        "battery_pct": 87.0,
        "zigbee_lqi": 110.0,
    }


def test_translate_prefers_first_alias_for_same_metric():
    assert _translate({"illuminance_lux": 800, "illuminance": 21000}) == {"illuminance_lux": 800.0}


def test_translate_skips_null_and_non_numeric_values():
    """Zigbee2MQTT envoie null pour une mesure pas encore reçue."""
    assert _translate({"soil_moisture": None, "temperature": "18", "battery": True, "linkquality": 90}) == {
        "zigbee_lqi": 90.0
    }


def test_translate_rejects_non_object_payload():
    with pytest.raises(ValueError):
        zigbee.translate_payload(b"[1, 2]")


# --- _on_message ----------------------------------------------------------------

def test_zigbee_message_is_republished_in_device_contract():
    client = _FakeClient()
    main._on_message(client, None, _FakeMessage("zigbee2mqtt/sol-potager", {"soil_moisture": 42, "battery": 87}))
    assert client.published == [
        ("arrosage/sol-potager/etat/humidity_pct", {"value": 42.0}, 1, True),
        ("arrosage/sol-potager/etat/battery_pct", {"value": 87.0}, 1, True),
    ]
    # Rien n'est stocké directement : ce sont les messages republiés qui le
    # seront, par l'abonnement habituel d'ingest.
    assert main._pending_rows.empty()


def test_invalid_zigbee_payload_is_dropped():
    client = _FakeClient()
    main._on_message(client, None, _FakeMessage("zigbee2mqtt/sol-potager", b"pas du json"))
    assert client.published == []


def test_republished_message_is_stored_with_unit():
    main._on_message(None, None, _FakeMessage("arrosage/sol-potager/etat/fertility_us_cm", {"value": 350}))
    device_id, rows = main._pending_rows.get_nowait()
    assert device_id == "sol-potager"
    assert rows[0]["metric"] == "fertility_us_cm" and rows[0]["unit"] == "µS/cm"
