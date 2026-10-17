"""Simulateur de capteurs de sol Zigbee, pour tester l'intégration
Zigbee2MQTT sans dongle ni capteur.

Publie, comme le ferait Zigbee2MQTT pour un testeur de sol Tuya 5 en 1, un
message JSON par capteur sur <base_topic>/<friendly_name> :

    {"soil_moisture": 42, "temperature": 18.3, "humidity": 71,
     "illuminance": 1200, "soil_fertility": 350, "battery": 87,
     "linkquality": 110}

Le sol sèche doucement et se réhumidifie tant qu'une vanne est ouverte sur
un device du projet (abonnement à arrosage/+/etat/+), ce qui permet de voir
la condition "sol déjà humide" des programmes changer d'avis.

    docker compose --profile sim up -d --build   # sol-potager + sol-serre

En local :

    uv run --no-project --with paho-mqtt==2.1.0 python zigbee2mqtt/soil_sensor_simulator.py \\
        --mqtt-host localhost --sensors sol-potager,sol-serre --period 10
"""

import argparse
import json
import logging
import math
import os
import random
import threading
import time

import paho.mqtt.client as mqtt

log = logging.getLogger("soil_sensor_simulator")


class SimulatedSoilSensor:
    def __init__(self, name):
        self.name = name
        self.moisture = random.uniform(25, 55)
        self.battery = random.uniform(60, 100)

    def read(self, valves_open):
        self.moisture += random.uniform(-0.6, 0.2) + 1.5 * valves_open
        self.moisture = min(max(self.moisture, 5.0), 95.0)
        self.battery = max(self.battery - 0.05, 0.0)
        hour = time.localtime().tm_hour + time.localtime().tm_min / 60
        daylight = max(0.0, math.sin((hour - 6) / 14 * math.pi))
        return {
            "soil_moisture": round(self.moisture),
            "temperature": round(14 + 8 * daylight + random.uniform(-0.3, 0.3), 1),
            "humidity": round(min(max(80 - 30 * daylight + random.uniform(-2, 2), 0), 100)),
            "illuminance": round(30000 * daylight + random.uniform(0, 50)),
            "soil_fertility": round(200 + 3 * self.moisture + random.uniform(-10, 10)),
            "battery": round(self.battery),
            "battery_state": "low" if self.battery < 20 else "high",
            "linkquality": random.randint(60, 160),
        }


class Runtime:
    def __init__(self, args):
        self.args = args
        self.sensors = [SimulatedSoilSensor(name.strip()) for name in args.sensors.split(",") if name.strip()]
        self._open_valves = set()
        self._lock = threading.Lock()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"soil-sim-{os.getpid()}")
        if args.mqtt_username:
            self.client.username_pw_set(args.mqtt_username, args.mqtt_password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        log.info("Connecté au broker, %s capteur(s) simulé(s)", len(self.sensors))
        client.subscribe("arrosage/+/etat/+", qos=0)

    def _on_message(self, client, userdata, message):
        _, device_id, _, key = message.topic.split("/")
        if "vanne" not in key:
            return
        try:
            state = json.loads(message.payload.decode("utf-8")).get("state")
        except Exception:
            return
        with self._lock:
            if state == "open":
                self._open_valves.add((device_id, key))
            else:
                self._open_valves.discard((device_id, key))

    def run(self):
        while True:
            try:
                self.client.connect(self.args.mqtt_host, self.args.mqtt_port)
                break
            except OSError as exc:
                log.warning("Broker MQTT injoignable (%s), nouvelle tentative dans 5s", exc)
                time.sleep(5)
        self.client.loop_start()
        while True:
            with self._lock:
                valves_open = len(self._open_valves)
            for sensor in self.sensors:
                payload = sensor.read(valves_open)
                self.client.publish(f"{self.args.base_topic}/{sensor.name}", json.dumps(payload), qos=1)
                log.info("%s : %s", sensor.name, payload)
            time.sleep(self.args.period)


def parse_args(argv=None):
    env = os.environ.get
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sensors", default=env("SIM_SOIL_SENSORS", "sol-potager,sol-serre"))
    parser.add_argument("--base-topic", default=env("ZIGBEE2MQTT_BASE_TOPIC", "zigbee2mqtt"))
    parser.add_argument("--mqtt-host", default=env("MQTT_HOST", "localhost"))
    parser.add_argument("--mqtt-port", type=int, default=int(env("MQTT_PORT", "1883")))
    parser.add_argument("--mqtt-username", default=env("MQTT_USERNAME") or None)
    parser.add_argument("--mqtt-password", default=env("MQTT_PASSWORD") or None)
    parser.add_argument("--period", type=float, default=float(env("SIM_SOIL_PERIOD_S", "30")))
    return parser.parse_args(argv)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Runtime(parse_args()).run()
