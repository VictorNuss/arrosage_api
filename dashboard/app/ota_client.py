"""Relais de mise à jour firmware (OTA) vers un device, en HTTP local.

Contrat côté device (déjà implémenté dans le firmware, repo arrosage_fw) :

    POST http://<ip_fixe_du_device>/api/ota
    Content-Type: application/octet-stream
    Corps : le .bin brut

    200 OK "OK, redemarrage en cours..." -> image acceptée, le device redémarre.
    400/500 + message -> image rejetée, l'ancien firmware reste actif.

Pas d'authentification côté device (réseau local de confiance uniquement) :
le dashboard se contente de relayer le fichier tel quel.
"""

import logging
import re
import threading

import requests

log = logging.getLogger("dashboard.ota_client")

_IPV4_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

_uploads_in_progress: set[str] = set()
_uploads_lock = threading.Lock()

UPLOAD_TIMEOUT_SECONDS = 60


def is_valid_ip(ip_address: str) -> bool:
    if not ip_address or not _IPV4_RE.match(ip_address):
        return False
    return all(0 <= int(part) <= 255 for part in ip_address.split("."))


def send_firmware(device_id: str, ip_address: str, data: bytes) -> tuple[bool, str]:
    """Renvoie (succès, message à afficher). Refuse tout envoi concurrent
    vers le même device plutôt que de laisser deux requêtes HTTP se
    chevaucher sur le port OTA de l'ESP32."""
    with _uploads_lock:
        if device_id in _uploads_in_progress:
            return False, "Un envoi est déjà en cours pour cet appareil, veuillez attendre."
        _uploads_in_progress.add(device_id)

    try:
        url = f"http://{ip_address}/api/ota"
        try:
            resp = requests.post(
                url,
                data=data,
                headers={"Content-Type": "application/octet-stream"},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            log.exception("Échec de l'envoi OTA vers %s (%s)", device_id, ip_address)
            return False, f"Erreur réseau vers {ip_address} : {exc}"

        if resp.status_code == 200:
            log.info("OTA envoyé avec succès à %s (%s)", device_id, ip_address)
            return True, resp.text or "OK"

        log.warning("OTA refusé par %s (%s): %s %s", device_id, ip_address, resp.status_code, resp.text)
        return False, f"Refusé par le device (HTTP {resp.status_code}) : {resp.text}"
    finally:
        with _uploads_lock:
            _uploads_in_progress.discard(device_id)
