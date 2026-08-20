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


_EVALUATORS = {
    "no_rain_forecast": lambda condition, ctx: evaluate_no_rain_forecast(ctx["rain_rows"], ctx["now"], condition),
    "min_tank_pct": lambda condition, ctx: evaluate_min_tank_pct(
        ctx["tank_value_cm"], ctx["tank_height_full_cm"], condition
    ),
}


def evaluate_conditions(conditions, context):
    """conditions: liste de dicts avec une clé "type" (+ paramètres propres
    au type). context: dict avec "now" (datetime tz-aware), "rain_rows",
    "tank_value_cm", "tank_height_full_cm".

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
