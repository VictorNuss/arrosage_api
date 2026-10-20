"""Simulateur de device ESP32 d'arrosage, pour tester toute la chaîne
(ingest, dashboard, scheduler, OTA) sans matériel.

Reproduit le contrat du firmware réel (repo arrosage_fw, voir README.md de
ce dossier) plutôt que d'en réinventer un :

  - MQTT état : un topic retenu par clé, arrosage/<device_id>/etat/<key>.
    À chaque connexion : ip, fw_version et l'état des vannes. Capteurs
    publiés à chaque "lecture" (toutes les --sensor-period secondes).
  - MQTT commande : arrosage/<device_id>/commande, open/close/stop_all/
    get_status. Vannes à 3 états : "transitioning" pendant
    --transition-delay secondes avant "open"/"closed", fermeture
    automatique au bout de duration_s (bornée par la durée max de la vanne,
    comme valve_logic_clamp_duration). Sur une vanne déjà ouverte (ou en
    cours d'ouverture), "open" ajoute duration_s au temps restant
    ("extra-time", total borné) sans nouvelle transition.
  - OTA : POST http://<ip>/api/ota avec le .bin brut. Seule une vraie image
    ESP-IDF est acceptée (octet magique 0xE9 + descripteur d'application) :
    sa version est lue dans le binaire, puis le device "redémarre"
    (déconnexion MQTT, vannes fermées) et republie cette nouvelle version.
    Pour tester, utiliser le build/arrosage_fw.bin du repo firmware.
  - GET /api/state : instantané JSON, pratique pour déboguer.

Lancement via Docker (recommandé : l'IP du conteneur est joignable par le
dashboard, sur le port 80 qu'il utilise toujours) :

    docker compose --profile sim up -d --build   # sim-jardin + sim-serre

En local (le dashboard dans Docker ne pourra pas joindre l'OTA) :

    uv run --no-project --with paho-mqtt==2.1.0 python esp32/device_simulator.py \\
        --mqtt-host localhost --http-port 8090 --device-id sim-local
"""

import argparse
import json
import logging
import math
import os
import random
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

log = logging.getLogger("device_simulator")

# Miroir de components/valve/valve_config.cpp : clé MQTT -> durée max (s).
VALVE_MAX_DURATION_S = {"vanne_1": 1800, "vanne_2": 900, "vanne_3": 600}

# Format d'image ESP-IDF : esp_image_header_t (24 octets) puis le premier
# segment (en-tête de 8 octets), qui commence par esp_app_desc_t.
_ESP_IMAGE_MAGIC = 0xE9
_APP_DESC_OFFSET = 24 + 8
_APP_DESC_MAGIC = 0xABCD5432
_APP_DESC_VERSION_OFFSET = _APP_DESC_OFFSET + 16  # après magic, secure_version, reserv1[2]
_APP_DESC_VERSION_SIZE = 32


def read_firmware_version(image: bytes) -> str:
    """Version embarquée dans une image ESP-IDF (esp_app_desc_t.version).
    ValueError si ce n'est pas une image ESP-IDF valide, comme le refuserait
    esp_ota_end() sur le vrai device."""
    if len(image) < _APP_DESC_VERSION_OFFSET + _APP_DESC_VERSION_SIZE or image[0] != _ESP_IMAGE_MAGIC:
        raise ValueError("pas une image ESP-IDF (octet magique 0xE9 absent)")
    (desc_magic,) = struct.unpack_from("<I", image, _APP_DESC_OFFSET)
    if desc_magic != _APP_DESC_MAGIC:
        raise ValueError("descripteur d'application ESP-IDF introuvable")
    raw = image[_APP_DESC_VERSION_OFFSET : _APP_DESC_VERSION_OFFSET + _APP_DESC_VERSION_SIZE]
    version = raw.split(b"\0", 1)[0].decode("ascii", errors="replace").strip()
    if not version:
        raise ValueError("version vide dans le descripteur d'application")
    return version


def clamp_duration(requested_s: int, max_duration_s: int) -> int:
    """Comme valve_logic_clamp_duration : 0 ou trop long -> durée max."""
    if requested_s == 0 or requested_s > max_duration_s:
        return max_duration_s
    return requested_s


def extend_duration(remaining_s: int, requested_s: int, max_duration_s: int) -> int:
    """Comme valve_logic_extend_duration ("extra-time" sur une vanne déjà
    ouverte) : la durée demandée s'ajoute au temps restant, total borné."""
    return min(remaining_s + clamp_duration(requested_s, max_duration_s), max_duration_s)


def detect_local_ip(target_host: str, target_port: int) -> str:
    """IP de l'interface utilisée pour joindre le broker (aucun paquet n'est
    envoyé : connect() sur un socket UDP ne fait que choisir la route)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((socket.gethostbyname(target_host), target_port))
        return sock.getsockname()[0]


class SimulatedDevice:
    """État et logique du device, indépendants du transport : `publish(key,
    payload_dict)` est injecté (MQTT en vrai, une liste dans les tests)."""

    def __init__(
        self, publish, ip_address, fw_version, transition_delay_s=15.0, tank_distance_empty_cm=150.0,
        tank_distance_full_cm=25.0,
    ):
        self._publish = publish
        self._lock = threading.RLock()
        self.ip_address = ip_address
        self.fw_version = fw_version
        self.transition_delay_s = transition_delay_s
        # Comme le vrai firmware, le device ne publie que la distance brute
        # capteur -> surface (water_distance_cm) ; ces distances ne servent
        # qu'à simuler une surface qui reste dans la cuve.
        self.tank_distance_empty_cm = tank_distance_empty_cm
        self.tank_distance_full_cm = tank_distance_full_cm
        self.valves = {key: "closed" for key in VALVE_MAX_DURATION_S}
        self._transition_timers = {}
        self._auto_close_timers = {}
        self._close_deadlines = {}  # clé -> time.monotonic() de la fermeture automatique
        self._transition_targets = {}  # clé -> True (ouverture) / False (fermeture) en cours
        # Comme le firmware : un capteur jamais lu depuis le boot n'est pas publié.
        self.sensors = {}

    # --- Publication -----------------------------------------------------------

    def publish_device_info(self):
        self._publish("ip", {"value": self.ip_address})
        self._publish("fw_version", {"value": self.fw_version})

    def publish_all(self):
        """Réponse à get_status (et à chaque connexion) : vannes, derniers
        capteurs connus, infos device."""
        with self._lock:
            for key, state in self.valves.items():
                self._publish(key, {"state": state})
            for key, value in self.sensors.items():
                self._publish(key, {"value": value})
        self.publish_device_info()

    # --- Capteurs ----------------------------------------------------------------

    def read_sensors(self):
        """Une "lecture matérielle" : la cuve se vide (~0,5 cm/min par vanne
        ouverte avec la période par défaut de 10 s) et le sol s'humidifie
        tant qu'une vanne est ouverte, puis sèche lentement ; petit bruit de
        mesure sans dérive."""
        with self._lock:
            open_valves = sum(1 for state in self.valves.values() if state == "open")
            empty, full = self.tank_distance_empty_cm, self.tank_distance_full_cm
            # La cuve se vide : la surface s'éloigne du capteur.
            distance = self.sensors.get("water_distance_cm", full + 0.2 * (empty - full))
            distance += random.uniform(-0.05, 0.05) + 0.08 * open_valves
            humidity = self.sensors.get("humidity_pct", 40.0)
            humidity += random.uniform(-0.03, 0.01) + 0.05 * open_valves
            temperature = self.sensors.get("temperature_c", 18.0) + random.uniform(-0.3, 0.3)
            self.sensors = {
                "water_distance_cm": round(min(max(distance, full), empty), 1),
                "humidity_pct": round(min(max(humidity, 0.0), 100.0), 1),
                "temperature_c": round(temperature, 1),
                "battery_v": 0.0,  # alimenté secteur : constante, comme BatteryPlaceholderSensor
            }
            for key, value in self.sensors.items():
                self._publish(key, {"value": value})

    # --- Vannes --------------------------------------------------------------------

    def _set_valve_state(self, key, state):
        self.valves[key] = state
        self._publish(key, {"state": state})

    def _begin_transition(self, key, to_open):
        with self._lock:
            previous = self._transition_timers.pop(key, None)
            if previous:
                previous.cancel()  # annule une transition en cours, comme le firmware
            self._transition_targets[key] = to_open
            self._set_valve_state(key, "transitioning")
            timer = threading.Timer(self.transition_delay_s, self._end_transition, args=(key, to_open))
            timer.daemon = True
            self._transition_timers[key] = timer
            timer.start()

    def _end_transition(self, key, to_open):
        with self._lock:
            self._transition_timers.pop(key, None)
            self._transition_targets.pop(key, None)
            self._set_valve_state(key, "open" if to_open else "closed")

    def _cancel_auto_close(self, key):
        self._close_deadlines.pop(key, None)
        timer = self._auto_close_timers.pop(key, None)
        if timer:
            timer.cancel()

    def _is_opening(self, key):
        """Ouverte, ou en cours d'ouverture (GPIO déjà à 1 sur le vrai device)."""
        return self.valves[key] == "open" or (
            self.valves[key] == "transitioning" and self._transition_targets.get(key) is True
        )

    def remaining_seconds(self, key):
        """Temps restant avant la fermeture automatique (0 si aucune)."""
        with self._lock:
            deadline = self._close_deadlines.get(key)
        return max(0, math.ceil(deadline - time.monotonic())) if deadline else 0

    def open_valve(self, key, duration_s):
        with self._lock:
            max_s = VALVE_MAX_DURATION_S[key]
            already_opening = self._is_opening(key)
            if already_opening:
                # Comme le firmware : rien ne bouge, aucun état republié, seule
                # la durée restante est prolongée ("extra-time").
                effective_s = extend_duration(self.remaining_seconds(key), duration_s, max_s)
            else:
                effective_s = clamp_duration(duration_s, max_s)
            self._cancel_auto_close(key)
            timer = threading.Timer(effective_s, self.close_valve, args=(key,))
            timer.daemon = True
            self._auto_close_timers[key] = timer
            self._close_deadlines[key] = time.monotonic() + effective_s
            timer.start()
            if already_opening:
                log.info("%s : déjà ouverte, prolongée - fermeture dans %ss", key, effective_s)
            else:
                self._begin_transition(key, to_open=True)
                log.info("%s : ouverture pour %ss", key, effective_s)

    def close_valve(self, key):
        with self._lock:
            self._cancel_auto_close(key)
            self._begin_transition(key, to_open=False)
            log.info("%s : fermeture", key)

    def stop_all(self):
        with self._lock:
            for key in self.valves:
                self.close_valve(key)

    def reset_after_reboot(self, fw_version):
        """Redémarrage : timers perdus, GPIO à 0 (vannes fermées), aucun
        capteur encore relu. Rien n'est publié ici : le device est
        déconnecté, tout repart à la reconnexion."""
        with self._lock:
            for timer in [*self._transition_timers.values(), *self._auto_close_timers.values()]:
                timer.cancel()
            self._transition_timers.clear()
            self._auto_close_timers.clear()
            self._close_deadlines.clear()
            self._transition_targets.clear()
            self.valves = {key: "closed" for key in VALVE_MAX_DURATION_S}
            self.sensors = {}
            self.fw_version = fw_version

    # --- Commandes -------------------------------------------------------------------

    def handle_command(self, payload_bytes):
        """Même validation que command_parser.cpp : toute commande invalide
        est ignorée (journalisée), jamais partiellement appliquée."""
        try:
            data = json.loads(payload_bytes)
        except ValueError:
            log.warning("Commande non JSON ignorée : %r", payload_bytes)
            return
        if not isinstance(data, dict):
            log.warning("Commande ignorée (pas un objet JSON) : %r", data)
            return

        action = data.get("action")
        valve = data.get("vanne")
        if action == "open":
            duration_s = data.get("duration_s")
            valid_duration = (
                isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool) and duration_s >= 1
            )
            if valve in VALVE_MAX_DURATION_S and valid_duration:
                self.open_valve(valve, int(duration_s))
                return
        elif action == "close":
            if valve in VALVE_MAX_DURATION_S:
                self.close_valve(valve)
                return
        elif action == "stop_all":
            self.stop_all()
            return
        elif action == "get_status":
            self.publish_all()
            return
        log.warning("Commande invalide ignorée : %s", data)

    def snapshot(self):
        with self._lock:
            return {
                "ip": self.ip_address,
                "fw_version": self.fw_version,
                "valves": dict(self.valves),
                "sensors": dict(self.sensors),
            }


class DeviceRuntime:
    """Branche un SimulatedDevice sur MQTT et sur un serveur HTTP (OTA)."""

    def __init__(self, args):
        self.args = args
        self.device_id = args.device_id
        self.topic_prefix = f"arrosage/{self.device_id}"
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=self.device_id)
        if args.mqtt_username:
            self.client.username_pw_set(args.mqtt_username, args.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self._connected = threading.Event()
        self._ota_lock = threading.Lock()

        ip_address = args.ip or detect_local_ip(args.mqtt_host, args.mqtt_port)
        self.device = SimulatedDevice(
            self._publish, ip_address, args.fw_version, args.transition_delay, args.tank_distance_empty,
            args.tank_distance_full,
        )

    def _publish(self, key, payload):
        if not self._connected.is_set():
            return
        self.client.publish(f"{self.topic_prefix}/etat/{key}", json.dumps(payload), qos=1, retain=True)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code.is_failure:
            log.error("Connexion MQTT refusée : %s", reason_code)
            return
        log.info("MQTT connecté (%s, IP %s, firmware %s)", self.device_id, self.device.ip_address, self.device.fw_version)
        client.subscribe(f"{self.topic_prefix}/commande", qos=1)
        self._connected.set()
        self.device.publish_all()

    def _on_message(self, client, userdata, message):
        self.device.handle_command(message.payload)

    def _mqtt_connect(self):
        self.client.connect(self.args.mqtt_host, self.args.mqtt_port)
        self.client.loop_start()

    def reboot(self, new_version):
        log.info("Redémarrage sur le firmware %s...", new_version)
        self._connected.clear()
        self.client.disconnect()
        self.client.loop_stop()
        self.device.reset_after_reboot(new_version)
        time.sleep(self.args.reboot_delay)
        self._mqtt_connect()

    def apply_ota(self, image):
        """(code HTTP, message) comme le endpoint du firmware ; en cas de
        succès, redémarre après avoir laissé partir la réponse."""
        if not self._ota_lock.acquire(blocking=False):
            return 409, "Mise a jour deja en cours"
        try:
            new_version = read_firmware_version(image)
        except ValueError as exc:
            self._ota_lock.release()
            log.warning("OTA rejetée : %s", exc)
            return 400, f"Image invalide : {exc}"
        log.info("OTA acceptée : %s octets, version %s", len(image), new_version)

        def _reboot_then_unlock():
            try:
                time.sleep(0.5)
                self.reboot(new_version)
            finally:
                self._ota_lock.release()

        threading.Thread(target=_reboot_then_unlock, daemon=True).start()
        return 200, "OK, redemarrage en cours..."

    def _make_http_handler(self):
        runtime = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, status, body, content_type="text/plain; charset=utf-8"):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path != "/api/ota":
                    self._reply(404, "Not found")
                    return
                length = int(self.headers.get("Content-Length", 0))
                status, message = runtime.apply_ota(self.rfile.read(length))
                self._reply(status, message)

            def do_GET(self):
                if self.path != "/api/state":
                    self._reply(404, "Not found")
                    return
                self._reply(200, json.dumps(runtime.device.snapshot()), "application/json")

            def log_message(self, format, *args):
                log.debug("HTTP %s", format % args)

        return Handler

    def run(self):
        server = ThreadingHTTPServer(("0.0.0.0", self.args.http_port), self._make_http_handler())
        threading.Thread(target=server.serve_forever, daemon=True).start()
        log.info("Endpoint OTA : http://%s:%s/api/ota", self.device.ip_address, self.args.http_port)

        while True:
            try:
                self._mqtt_connect()
                break
            except OSError as exc:
                log.warning("Broker MQTT injoignable (%s), nouvelle tentative dans 5s", exc)
                time.sleep(5)

        while True:
            time.sleep(self.args.sensor_period)
            if self._connected.is_set():
                self.device.read_sensors()


def parse_args(argv=None):
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--device-id", default=env("SIM_DEVICE_ID") or f"sim-{socket.gethostname()}"[:32])
    parser.add_argument("--mqtt-host", default=env("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(env("MQTT_PORT", "1883")))
    parser.add_argument("--mqtt-username", default=env("MQTT_USERNAME") or None)
    parser.add_argument("--mqtt-password", default=env("MQTT_PASSWORD") or None)
    parser.add_argument("--http-port", type=int, default=int(env("SIM_HTTP_PORT", "80")))
    parser.add_argument("--ip", default=env("SIM_IP") or None, help="IP publiée (défaut : détectée)")
    parser.add_argument("--fw-version", default=env("SIM_FW_VERSION", "v0.0.0-sim"))
    parser.add_argument("--transition-delay", type=float, default=float(env("SIM_TRANSITION_DELAY_S", "15")))
    parser.add_argument("--sensor-period", type=float, default=float(env("SIM_SENSOR_PERIOD_S", "10")))
    parser.add_argument("--reboot-delay", type=float, default=float(env("SIM_REBOOT_DELAY_S", "3")))
    parser.add_argument(
        "--tank-distance-empty", type=float, default=float(env("SIM_TANK_DISTANCE_EMPTY_CM") or "150")
    )
    parser.add_argument("--tank-distance-full", type=float, default=float(env("SIM_TANK_DISTANCE_FULL_CM") or "25"))
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    DeviceRuntime(parse_args()).run()
