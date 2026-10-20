"""Évaluation des conditions attachées à un programme d'arrosage.

Fonctions pures (pas d'I/O) pour rester facilement testables : les données
nécessaires (prévisions de pluie, niveau de cuve, heure courante) sont
passées en paramètre plutôt que récupérées ici.
"""

from datetime import timedelta

# AROME et ARPEGE se chevauchent sur les échéances proches : on préfère
# AROME (plus fin) pour ne pas compter la même pluie deux fois. Même
# convention que dashboard/app/queries.py::get_rain_outlook.
_AROME_SOURCE = "AROME"


def sum_rain_forecast_mm(rain_rows, now, window_hours):
    """rain_rows: liste de dicts {valid_time, source, value} (déjà filtrée
    sur metric='precipitation_mm'). Renvoie le cumul (mm) prévu entre `now`
    et `now + window_hours`."""
    by_valid_time = {}
    for row in rain_rows:
        key = row["valid_time"]
        if key not in by_valid_time or row["source"] == _AROME_SOURCE:
            by_valid_time[key] = row

    window_end = now + timedelta(hours=window_hours)
    return sum(
        row["value"] for row in by_valid_time.values() if now <= row["valid_time"] <= window_end
    )


def evaluate_no_rain_forecast(rain_rows, now, params):
    window_hours = params.get("window_hours", 3)
    threshold_mm = params.get("threshold_mm", 0.2)
    total_mm = sum_rain_forecast_mm(rain_rows, now, window_hours)
    if total_mm >= threshold_mm:
        return False, f"pluie prévue ({total_mm:.1f} mm dans les {window_hours}h)"
    return True, None


def evaluate_min_tank_pct(tank_value_cm, tank_height_full_cm, params):
    min_pct = params.get("min_pct", 0)
    if tank_value_cm is None:
        # Pas de lecture connue : on bloque par sécurité plutôt que de
        # supposer la cuve pleine.
        return False, "niveau de la cuve inconnu (aucune lecture)"
    pct = (tank_value_cm / tank_height_full_cm) * 100 if tank_height_full_cm else 0.0
    if pct < min_pct:
        return False, f"cuve à {pct:.0f}% (< {min_pct}%)"
    return True, None


def evaluate_max_soil_moisture(soil_readings, now, params):
    """Ne pas arroser si le sol est déjà humide : moyenne des dernières
    mesures récentes des capteurs choisis (params["device_ids"]) comparée à
    params["max_pct"].

    soil_readings: {device_id: {"value": float, "time": datetime}}, dernière
    humidité connue de chaque capteur de sol.

    Moyenne plutôt que "tous les capteurs" : un programme arrose souvent
    une zone couverte par plusieurs capteurs, un seul capteur mal planté ne
    doit pas décider pour toute la zone.

    Contrairement au niveau de cuve, l'absence de mesure récente (pile
    vide, capteur hors de portée...) laisse arroser : mieux vaut un
    arrosage de trop qu'un potager qui sèche à cause d'un capteur muet.
    """
    max_pct = params.get("max_pct", 60)
    max_age = timedelta(hours=params.get("max_age_hours", 6))
    fresh_values = [
        reading["value"]
        for device_id in params.get("device_ids") or []
        if (reading := soil_readings.get(device_id)) is not None and now - reading["time"] <= max_age
    ]
    if not fresh_values:
        return True, None
    mean_pct = sum(fresh_values) / len(fresh_values)
    if mean_pct >= max_pct:
        return False, f"sol déjà humide ({mean_pct:.0f}% ≥ {max_pct}%, {len(fresh_values)} capteur(s))"
    return True, None


_EVALUATORS = {
    "no_rain_forecast": lambda condition, ctx: evaluate_no_rain_forecast(ctx["rain_rows"], ctx["now"], condition),
    "min_tank_pct": lambda condition, ctx: evaluate_min_tank_pct(
        ctx["tank_value_cm"], ctx["tank_height_full_cm"], condition
    ),
    "max_soil_moisture": lambda condition, ctx: evaluate_max_soil_moisture(
        ctx.get("soil_readings", {}), ctx["now"], condition
    ),
}


def evaluate_conditions(conditions, context):
    """conditions: liste de dicts avec une clé "type" (+ paramètres propres
    au type). context: dict avec "now" (datetime tz-aware), "rain_rows",
    "tank_value_cm", "tank_height_full_cm", "soil_readings".

    Renvoie (True, None) si toutes les conditions passent, sinon
    (False, raison) dès le premier échec. Un type de condition inconnu est
    ignoré plutôt que de bloquer le programme (permet d'ajouter des types
    plus tard sans casser les programmes existants si un service n'est pas
    encore à jour)."""
    for condition in conditions:
        evaluator = _EVALUATORS.get(condition.get("type"))
        if evaluator is None:
            continue
        ok, reason = evaluator(condition, context)
        if not ok:
            return False, reason
    return True, None


# --- Condition par vanne : seuil d'humidité de la culture de la zone ------------
#
# Contrairement aux conditions ci-dessus (tout le programme arrose ou rien),
# celle-ci est évaluée vanne par vanne : chaque vanne arrose une zone du
# potager (garden_zones) qui a sa propre culture, donc son propre seuil.
ZONE_CROP_MOISTURE = "zone_crop_moisture"


def calibrated_moisture(raw, dry_ref, wet_ref):
    """Même échelle que le dashboard (dashboard/app/garden.py) : 0 = sol sec
    au toucher, 100 = gorgé d'eau ; valeur brute si la zone n'est pas
    calibrée."""
    if dry_ref is None or wet_ref is None or wet_ref <= dry_ref:
        return float(raw)
    return min(100.0, max(0.0, (raw - dry_ref) / (wet_ref - dry_ref) * 100))


def filter_valves_by_zone_moisture(valves, zones_by_valve, soil_readings, now, params):
    """Sépare les vannes à ouvrir de celles dont la zone est déjà assez
    humide pour sa culture : humidité calibrée moyenne des capteurs de la
    zone (mesures de moins de params["max_age_hours"]) >= seuil bas de la
    culture (crop_profiles.moisture_min).

    valves : [{"device_id", "metric", ...}] ; zones_by_valve : voir
    db.get_zones_by_valve ; soil_readings : {device_id: {"value", "time"}}.

    Renvoie (vannes à ouvrir, raisons des vannes ignorées). Une vanne sans
    zone, sans culture ou sans mesure récente arrose : mieux vaut un
    arrosage de trop qu'un potager qui sèche à cause d'un capteur muet.
    """
    max_age = timedelta(hours=params.get("max_age_hours", 6))
    to_water, skipped = [], []
    for valve in valves:
        zone = zones_by_valve.get((valve["device_id"], valve["metric"]))
        if zone is None or zone["moisture_min"] is None:
            to_water.append(valve)
            continue
        values = [
            calibrated_moisture(reading["value"], zone["dry_ref"], zone["wet_ref"])
            for device_id in zone["sensors"]
            if (reading := soil_readings.get(device_id)) is not None and now - reading["time"] <= max_age
        ]
        if not values:
            to_water.append(valve)
            continue
        moisture = sum(values) / len(values)
        if moisture >= zone["moisture_min"]:
            skipped.append(
                f"{zone['name']} déjà assez humide pour {zone['crop_name']} "
                f"({moisture:.0f}% ≥ {zone['moisture_min']}%)"
            )
        else:
            to_water.append(valve)
    return to_water, skipped
