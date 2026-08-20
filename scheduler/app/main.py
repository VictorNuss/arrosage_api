import json
import logging
import time
from datetime import datetime

import paho.mqtt.client as mqtt

from . import config, conditions, db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("scheduler.main")

_client = None


def _get_mqtt_client():
    global _client
    if _client is not None:
        return _client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if config.MQTT_USERNAME:
        client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
    client.connect(config.MQTT_HOST, config.MQTT_PORT)
    client.loop_start()
    _client = client
    return client


def _publish_valve_open(device_id, metric, duration_s):
    """Même contrat que dashboard.mqtt_control.send_valve_command : QoS 1,
    jamais retain."""
    payload = {"vanne": metric, "action": "open", "duration_s": duration_s}
    client = _get_mqtt_client()
    result = client.publish(f"arrosage/{device_id}/commande", json.dumps(payload), qos=1, retain=False)
    result.wait_for_publish(timeout=5)


def _is_due(program, now):
    if now.isoweekday() not in program["days_of_week"]:
        return False
    start_time = program["start_time"]
    return now.hour == start_time.hour and now.minute == start_time.minute


def run_cycle(engine):
    # datetime.now() est en heure locale du conteneur (TZ=Europe/Paris) ;
    # .astimezone() y attache l'info de fuseau. Les comparaisons avec des
    # datetime UTC (weather_forecast.valid_time) restent correctes : Python
    # normalise automatiquement deux datetime "aware" pour les comparer,
    # quel que soit leur fuseau respectif.
    now = datetime.now().astimezone()

    programs = db.get_enabled_programs(engine)
    due_programs = [p for p in programs if _is_due(p, now)]
    if not due_programs:
        return

    rain_rows = db.get_rain_forecast_rows(engine)
    tank_value = db.get_latest_tank_value(engine)

    for program in due_programs:
        scheduled_for = datetime.combine(now.date(), program["start_time"], tzinfo=now.tzinfo)
        context = {
            "now": now,
            "rain_rows": rain_rows,
            "tank_value_cm": tank_value,
            "tank_height_full_cm": config.TANK_HEIGHT_FULL_CM,
        }
        ok, reason = conditions.evaluate_conditions(program["conditions"] or [], context)

        if ok:
            valves_triggered = [
                {
                    "device_id": valve["device_id"],
                    "metric": valve["metric"],
                    "duration_s": valve["duration_s"] or program["default_duration_s"],
                }
                for valve in program["valves"]
            ]
            status, skip_reason = "executed", None
        else:
            valves_triggered = None
            status, skip_reason = "skipped", reason

        claimed = db.try_claim_run(
            engine, program["id"], program["name"], scheduled_for, status, skip_reason, valves_triggered
        )
        if not claimed:
            continue  # déjà traité par un tick précédent dans la même minute

        if status == "executed":
            for valve in valves_triggered:
                try:
                    _publish_valve_open(valve["device_id"], valve["metric"], valve["duration_s"])
                except Exception:
                    log.exception(
                        "Échec de l'envoi de commande pour %s/%s (programme '%s')",
                        valve["device_id"],
                        valve["metric"],
                        program["name"],
                    )
            log.info("Programme '%s' exécuté (%s vanne(s))", program["name"], len(valves_triggered))
        else:
            log.info("Programme '%s' ignoré: %s", program["name"], reason)


def main():
    engine = db.create_engine_with_retry()
    while True:
        try:
            run_cycle(engine)
        except Exception:
            log.exception("Erreur pendant le cycle du scheduler")
        time.sleep(config.CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
