from datetime import datetime, timedelta, timezone

import pandas as pd

from app import firmware_tab


def _device(device_id="jardin-1", ip_address="192.168.1.50", fw_version="v1.2.0", name=None):
    return {
        "device_id": device_id,
        "name": name or device_id,
        "ip_address": ip_address,
        "fw_version": fw_version,
        "last_seen": None,
    }


def _texts(component):
    """Tous les textes (chaînes) contenus dans un arbre de composants Dash."""
    if isinstance(component, str):
        return [component]
    if isinstance(component, (list, tuple)):
        return [text for child in component for text in _texts(child)]
    children = getattr(component, "children", None)
    return _texts(children) if children is not None else []


# --- load_devices ----------------------------------------------------------------

def test_load_devices_attaches_last_seen_from_latest_readings():
    now = datetime.now(timezone.utc)
    devices_df = pd.DataFrame(
        [
            {"device_id": "jardin-1", "name": "Jardin", "ip_address": "192.168.1.50", "fw_version": "v1.2.0"},
            {"device_id": "serre", "name": None, "ip_address": None, "fw_version": None},
        ]
    )
    readings_df = pd.DataFrame(
        [
            {"device_id": "jardin-1", "metric": "temperature_c", "time": now - timedelta(minutes=10)},
            {"device_id": "jardin-1", "metric": "vanne_1", "time": now},
        ]
    )

    devices = firmware_tab.load_devices(devices_df, readings_df)

    assert devices[0]["name"] == "Jardin"
    assert pd.Timestamp(devices[0]["last_seen"]) == pd.Timestamp(now)
    # Sans nom, on retombe sur l'identifiant ; sans mesure, pas de last_seen.
    assert devices[1] == {
        "device_id": "serre",
        "name": "serre",
        "ip_address": None,
        "fw_version": None,
        "last_seen": None,
    }


def test_load_devices_without_any_reading():
    devices_df = pd.DataFrame([{"device_id": "jardin-1", "name": "Jardin", "ip_address": None, "fw_version": None}])
    assert firmware_tab.load_devices(devices_df, pd.DataFrame())[0]["last_seen"] is None


# --- pick_selected_device ----------------------------------------------------------

def test_pick_selected_device_keeps_current_selection_when_still_present():
    devices = [_device("a"), _device("b")]
    assert firmware_tab.pick_selected_device(devices, "b") == "b"


def test_pick_selected_device_prefers_first_device_with_a_known_ip():
    devices = [_device("a", ip_address=None), _device("b")]
    assert firmware_tab.pick_selected_device(devices, None) == "b"
    assert firmware_tab.pick_selected_device(devices, "disparu") == "b"


def test_pick_selected_device_falls_back_to_first_device_without_any_ip():
    devices = [_device("a", ip_address=None), _device("b", ip_address=None)]
    assert firmware_tab.pick_selected_device(devices, None) == "a"


def test_pick_selected_device_with_no_device():
    assert firmware_tab.pick_selected_device([], None) is None


# --- build_device_option -------------------------------------------------------------

def test_device_option_shows_ip_and_version():
    option = firmware_tab.build_device_option(_device(name="Serre nord"))
    assert option["value"] == "jardin-1"
    assert option["search"].startswith("Serre nord jardin-1 192.168.1.50")
    texts = " ".join(_texts(option["label"]))
    assert "Serre nord" in texts and "192.168.1.50" in texts and "v1.2.0" in texts


def test_device_option_flags_unknown_ip_and_version():
    option = firmware_tab.build_device_option(_device(ip_address=None, fw_version=None))
    texts = " ".join(_texts(option["label"]))
    assert "IP inconnue" in texts and "version inconnue" in texts


# --- build_device_details --------------------------------------------------------------

def test_device_details_warns_only_when_ip_unknown():
    def warnings(device):
        details = firmware_tab.build_device_details(device)
        return [c for c in details if type(c).__name__ == "Alert"]

    assert len(warnings(_device(ip_address=None))) == 1
    assert warnings(_device()) == []


def test_device_details_without_device():
    assert type(firmware_tab.build_device_details(None)).__name__ == "Alert"


# --- has_valid_ip / find_device -----------------------------------------------------------

def test_has_valid_ip():
    assert firmware_tab.has_valid_ip(_device()) is True
    assert firmware_tab.has_valid_ip(_device(ip_address=None)) is False
    assert firmware_tab.has_valid_ip(_device(ip_address="pas-une-ip")) is False
    assert firmware_tab.has_valid_ip(None) is False


def test_find_device():
    devices = [_device("a"), _device("b")]
    assert firmware_tab.find_device(devices, "b")["device_id"] == "b"
    assert firmware_tab.find_device(devices, "c") is None
    assert firmware_tab.find_device(None, "a") is None


# --- build_devices_table ---------------------------------------------------------------------

def test_devices_table_has_one_row_per_device():
    table = firmware_tab.build_devices_table([_device("a"), _device("b", ip_address=None, fw_version=None)])
    tbody = table.children[1]
    assert len(tbody.children) == 2


def test_devices_table_empty():
    assert firmware_tab.build_devices_table([]) is None
