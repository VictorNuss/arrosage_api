import pytest
import requests

from app import ota_client


# --- is_valid_ip --------------------------------------------------------------

@pytest.mark.parametrize(
    "ip_address",
    ["192.168.1.50", "10.0.0.1", "255.255.255.255", "0.0.0.0"],
)
def test_is_valid_ip_accepts_well_formed_ipv4(ip_address):
    assert ota_client.is_valid_ip(ip_address) is True


@pytest.mark.parametrize(
    "ip_address",
    ["", None, "pas-une-ip", "999.1.1.1", "192.168.1", "192.168.1.1.1", "192.168.1.-1"],
)
def test_is_valid_ip_rejects_malformed_input(ip_address):
    assert ota_client.is_valid_ip(ip_address) is False


# --- send_firmware ------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text


def test_send_firmware_success(mocker):
    mocker.patch("app.ota_client.requests.post", return_value=_FakeResponse(200, "OK, redemarrage en cours..."))
    ok, message = ota_client.send_firmware("jardin-1", "192.168.1.50", b"firmware-bytes")
    assert ok is True
    assert "redemarrage" in message


def test_send_firmware_device_rejects_image(mocker):
    mocker.patch("app.ota_client.requests.post", return_value=_FakeResponse(400, "Image invalide"))
    ok, message = ota_client.send_firmware("jardin-1", "192.168.1.50", b"firmware-bytes")
    assert ok is False
    assert "400" in message


def test_send_firmware_network_error_does_not_raise(mocker):
    mocker.patch("app.ota_client.requests.post", side_effect=requests.RequestException("timeout"))
    ok, message = ota_client.send_firmware("jardin-1", "192.168.1.50", b"firmware-bytes")
    assert ok is False
    assert "timeout" in message


def test_send_firmware_refuses_concurrent_upload_to_same_device(mocker):
    """Régression : deux envois vers le même device ne doivent jamais se
    chevaucher (deux requêtes HTTP simultanées vers le port OTA de l'ESP32)."""
    ota_client._uploads_in_progress.add("jardin-1")
    try:
        post = mocker.patch("app.ota_client.requests.post")
        ok, message = ota_client.send_firmware("jardin-1", "192.168.1.50", b"firmware-bytes")
        assert ok is False
        assert "déjà en cours" in message
        post.assert_not_called()
    finally:
        ota_client._uploads_in_progress.discard("jardin-1")


def test_send_firmware_allows_concurrent_upload_to_different_devices(mocker):
    ota_client._uploads_in_progress.add("jardin-1")
    try:
        mocker.patch("app.ota_client.requests.post", return_value=_FakeResponse(200, "OK"))
        ok, _ = ota_client.send_firmware("jardin-2", "192.168.1.51", b"firmware-bytes")
        assert ok is True
    finally:
        ota_client._uploads_in_progress.discard("jardin-1")


def test_send_firmware_releases_the_lock_after_completion(mocker):
    mocker.patch("app.ota_client.requests.post", return_value=_FakeResponse(200, "OK"))
    ota_client.send_firmware("jardin-1", "192.168.1.50", b"firmware-bytes")
    assert "jardin-1" not in ota_client._uploads_in_progress
