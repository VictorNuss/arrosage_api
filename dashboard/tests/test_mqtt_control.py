import json

import pytest

from app import mqtt_control


class _FakePublishResult:
    def wait_for_publish(self, timeout=None):
        pass


class _FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=None, retain=None):
        self.published.append({"topic": topic, "payload": payload, "qos": qos, "retain": retain})
        return _FakePublishResult()


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(mqtt_control, "_get_client", lambda: client)
    yield client


def test_open_command_uses_vanne_action_duration_contract(fake_client):
    """Régression : le firmware attend {"vanne": ..., "action": ..., "duration_s": ...},
    pas l'ancien format {"<vanne>": "open"}."""
    ok = mqtt_control.send_valve_command("jardin-1", "vanne_1", "open", 600)
    assert ok is True

    assert len(fake_client.published) == 1
    sent = fake_client.published[0]
    assert sent["topic"] == "arrosage/jardin-1/commande"
    assert json.loads(sent["payload"]) == {"vanne": "vanne_1", "action": "open", "duration_s": 600}


def test_close_command_has_no_duration_field(fake_client):
    mqtt_control.send_valve_command("jardin-1", "vanne_1", "close")
    sent = fake_client.published[0]
    assert json.loads(sent["payload"]) == {"vanne": "vanne_1", "action": "close"}


def test_open_command_without_duration_omits_the_field(fake_client):
    mqtt_control.send_valve_command("jardin-1", "vanne_1", "open", None)
    sent = fake_client.published[0]
    assert "duration_s" not in json.loads(sent["payload"])


def test_commands_are_never_retained(fake_client):
    """Régression : contrairement à une version précédente, le firmware
    n'attend jamais retain=True (sinon un redémarrage rejouerait une
    commande périmée)."""
    mqtt_control.send_valve_command("jardin-1", "vanne_1", "open", 600)
    assert fake_client.published[0]["retain"] is False


def test_commands_use_qos_1(fake_client):
    mqtt_control.send_valve_command("jardin-1", "vanne_1", "close")
    assert fake_client.published[0]["qos"] == 1


def test_stop_all_has_no_vanne_field(fake_client):
    mqtt_control.send_stop_all("jardin-1")
    sent = fake_client.published[0]
    assert json.loads(sent["payload"]) == {"action": "stop_all"}


def test_publish_failure_is_reported_not_raised(monkeypatch):
    def _broken_client():
        raise ConnectionError("mosquitto injoignable")

    monkeypatch.setattr(mqtt_control, "_get_client", _broken_client)
    ok = mqtt_control.send_valve_command("jardin-1", "vanne_1", "open", 600)
    assert ok is False
