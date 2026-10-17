"""Lancement : uv run --no-project --python 3.12 --with paho-mqtt==2.1.0 --with pytest pytest esp32 -q"""

import json
import struct
import time

import pytest

import device_simulator as sim


def _esp_image(version=b"v1.3.0", desc_magic=0xABCD5432, image_magic=0xE9):
    header = bytes([image_magic]) + bytes(31)  # esp_image_header_t + en-tête du 1er segment
    desc = struct.pack("<IIII", desc_magic, 0, 0, 0) + version.ljust(32, b"\0") + b"arrosage_fw".ljust(32, b"\0")
    return header + desc + bytes(64)


class _Recorder:
    def __init__(self):
        self.messages = []

    def __call__(self, key, payload):
        self.messages.append((key, payload))

    def states_of(self, key):
        return [payload["state"] for k, payload in self.messages if k == key]


@pytest.fixture
def published():
    return _Recorder()


@pytest.fixture
def device(published):
    return sim.SimulatedDevice(published, "172.18.0.50", "v1.0.0", transition_delay_s=0.05)


def _wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# --- read_firmware_version ----------------------------------------------------------

def test_reads_version_from_esp_idf_image():
    assert sim.read_firmware_version(_esp_image(b"v1.3.0-2-g32e9aaa")) == "v1.3.0-2-g32e9aaa"


@pytest.mark.parametrize(
    "image",
    [
        b"",
        b"FAKEFIRMWARE" * 20,  # pas d'octet magique 0xE9
        _esp_image(desc_magic=0x12345678),  # descripteur absent
        _esp_image(version=b""),
        _esp_image()[:60],  # tronquée
    ],
)
def test_rejects_non_esp_idf_images(image):
    with pytest.raises(ValueError):
        sim.read_firmware_version(image)


# --- clamp_duration (miroir de valve_logic_clamp_duration) ---------------------------

@pytest.mark.parametrize("requested,expected", [(0, 900), (60, 60), (900, 900), (5000, 900)])
def test_clamp_duration(requested, expected):
    assert sim.clamp_duration(requested, 900) == expected


# --- vannes ----------------------------------------------------------------------------

def test_open_goes_through_transitioning_then_auto_closes(device, published):
    device.handle_command(json.dumps({"vanne": "vanne_2", "action": "open", "duration_s": 1}))
    assert published.states_of("vanne_2") == ["transitioning"]
    assert _wait_for(lambda: device.valves["vanne_2"] == "open")
    # Fermeture automatique après duration_s, elle aussi via "transitioning".
    assert _wait_for(lambda: published.states_of("vanne_2")[-1] == "closed", timeout=3)
    assert published.states_of("vanne_2") == ["transitioning", "open", "transitioning", "closed"]


def test_close_cancels_auto_close(device, published):
    device.handle_command(json.dumps({"vanne": "vanne_1", "action": "open", "duration_s": 600}))
    device.handle_command(json.dumps({"vanne": "vanne_1", "action": "close"}))
    assert _wait_for(lambda: device.valves["vanne_1"] == "closed")
    assert not device._auto_close_timers


def test_stop_all_closes_every_valve(device):
    device.handle_command(json.dumps({"vanne": "vanne_1", "action": "open", "duration_s": 600}))
    device.handle_command(json.dumps({"action": "stop_all"}))
    assert _wait_for(lambda: set(device.valves.values()) == {"closed"})


@pytest.mark.parametrize(
    "payload",
    [
        b"pas du json",
        b"[1, 2]",
        json.dumps({"vanne": "vanne_9", "action": "open", "duration_s": 60}).encode(),
        json.dumps({"vanne": "vanne_1", "action": "open"}).encode(),  # duration_s obligatoire
        json.dumps({"vanne": "vanne_1", "action": "open", "duration_s": 0}).encode(),
        json.dumps({"vanne": "vanne_1", "action": "open", "duration_s": True}).encode(),
        json.dumps({"action": "reboot"}).encode(),
    ],
)
def test_invalid_commands_are_ignored(device, published, payload):
    device.handle_command(payload)
    assert published.messages == []


# --- get_status / capteurs / redémarrage ---------------------------------------------------

def test_get_status_republishes_valves_known_sensors_and_device_info(device, published):
    device.handle_command(json.dumps({"action": "get_status"}))
    keys = [key for key, _ in published.messages]
    # Aucun capteur lu depuis le boot : aucun n'est publié (pas de valeur bidon).
    assert keys == ["vanne_1", "vanne_2", "vanne_3", "ip", "fw_version"]

    device.read_sensors()
    published.messages.clear()
    device.handle_command(json.dumps({"action": "get_status"}))
    assert {"water_level_cm", "humidity_pct", "temperature_c", "battery_v"} <= {k for k, _ in published.messages}
    assert ("ip", {"value": "172.18.0.50"}) in published.messages


def test_water_level_drops_while_a_valve_is_open(device):
    device.sensors = {"water_level_cm": 100.0, "humidity_pct": 40.0, "temperature_c": 18.0}
    device.valves["vanne_1"] = "open"
    device.read_sensors()
    assert device.sensors["water_level_cm"] < 100.0


def test_reset_after_reboot(device):
    device.handle_command(json.dumps({"vanne": "vanne_1", "action": "open", "duration_s": 600}))
    device.read_sensors()
    device.reset_after_reboot("v1.3.0")
    assert set(device.valves.values()) == {"closed"}
    assert device.sensors == {}
    assert device.fw_version == "v1.3.0"
    assert not device._auto_close_timers and not device._transition_timers
