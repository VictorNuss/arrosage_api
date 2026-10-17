from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app import garden, garden_tab

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
TOMATO = {"name": "Tomate", "moisture_min": 40, "moisture_max": 75, "watering_style": "deep",
          "sun_hours_min": 6, "frost_sensitive": True, "mildew_sensitive": True, "notes": None}
SALAD = {**TOMATO, "name": "Salade", "moisture_min": 55, "moisture_max": 85, "frost_sensitive": False,
         "mildew_sensitive": False}


def _zone(sensors=("sol-1",), crop=TOMATO, dry=None, wet=None):
    return {"id": 1, "name": "Zone 1", "sensors": list(sensors), "crop": crop,
            "moisture_dry_ref": dry, "moisture_wet_ref": wet, "valve_device_id": None, "valve_metric": None}


# --- humidité calibrée ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw,dry,wet,expected",
    [
        (30, None, None, 30.0),  # pas de calibration : valeur brute
        (30, 20, 60, 25.0),
        (10, 20, 60, 0.0),  # plus sec que la référence : borné
        (80, 20, 60, 100.0),
        (30, 60, 20, 30.0),  # calibration incohérente ignorée
        (None, 20, 60, None),
    ],
)
def test_calibrated_moisture(raw, dry, wet, expected):
    assert garden.calibrated_moisture(raw, dry, wet) == expected


def test_zone_moisture_averages_calibrated_sensors_with_a_value():
    latest = {"sol-1": {"humidity_pct": 40}, "sol-2": {"humidity_pct": 60}, "sol-3": {}}
    zone = _zone(sensors=("sol-1", "sol-2", "sol-3"), dry=20, wet=60)
    assert garden.zone_moisture(zone, latest) == pytest.approx((50 + 100) / 2)
    assert garden.zone_moisture(_zone(sensors=()), latest) is None


def test_raw_moisture_for_calibration():
    latest = {"sol-1": {"humidity_pct": 31.0}, "sol-2": {"humidity_pct": 33.0}}
    assert garden.raw_moisture(["sol-1", "sol-2"], latest) == 32.0
    assert garden.raw_moisture(["inconnu"], latest) is None


@pytest.mark.parametrize(
    "moisture,crop,status",
    [(None, TOMATO, "unknown"), (30, TOMATO, "dry"), (60, TOMATO, "ok"), (80, TOMATO, "wet"), (80, None, "ok")],
)
def test_moisture_status(moisture, crop, status):
    assert garden.moisture_status(moisture, crop) == status


# --- soleil / mildiou --------------------------------------------------------------------

def test_sun_hours_counts_minutes_above_threshold_averaging_sensors():
    minutes = [NOW + timedelta(minutes=m) for m in range(120)]
    lux = pd.DataFrame(
        [{"time": t, "device_id": "sol-1", "value": 50_000 if i < 90 else 5_000} for i, t in enumerate(minutes)]
        + [{"time": t, "device_id": "sol-2", "value": 30_000} for t in minutes]
    )
    # moyenne 40 000 lx pendant 90 min, 17 500 lx ensuite
    assert garden.sun_hours(lux) == pytest.approx(1.5)
    assert garden.sun_hours(pd.DataFrame(columns=["time", "device_id", "value"])) is None


def _recent(air, temp, hours_ago=1):
    t = NOW - timedelta(hours=hours_ago)
    return pd.DataFrame([{"time": t, "metric": "air_humidity_pct_rh", "value": air},
                         {"time": t, "metric": "temperature_c", "value": temp}])


@pytest.mark.parametrize(
    "air,temp,hours_ago,risk",
    [(92, 18, 1, True), (70, 18, 1, False), (92, 30, 1, False), (92, 18, 10, False)],
)
def test_mildew_risk(air, temp, hours_ago, risk):
    assert garden.mildew_risk(_recent(air, temp, hours_ago), now=NOW) is risk


# --- alertes ---------------------------------------------------------------------------------

def _alerts(zone, latest, times=None, recent=None):
    times = times if times is not None else {d: NOW for d in zone["sensors"]}
    recent = recent if recent is not None else pd.DataFrame(columns=["time", "metric", "value"])
    return [message for _color, message in garden.zone_alerts(zone, latest, times, recent, now=NOW,
                                                              device_names={"sol-1": "Sol potager"})]


def test_frost_alert_only_for_frost_sensitive_crops():
    latest = {"sol-1": {"temperature_c": 1.0}}
    assert any("gel" in a for a in _alerts(_zone(crop=TOMATO), latest))
    assert not any("gel" in a for a in _alerts(_zone(crop=SALAD), latest))


def test_heat_mildew_battery_and_stale_alerts():
    latest = {"sol-1": {"temperature_c": 34.0, "battery_pct": 12}}
    alerts = _alerts(_zone(), latest, times={"sol-1": NOW - timedelta(hours=10)}, recent=_recent(92, 18))
    assert any("chaleur" in a for a in alerts)
    assert any("Pile faible sur Sol potager" in a for a in alerts)
    assert any("Pas de mesure récente de Sol potager" in a for a in alerts)
    # mildiou : conditions récentes réunies (moyenne 18 °C / 92 %), culture sensible
    assert any("mildiou" in a for a in alerts)


def test_zone_without_sensor_says_so():
    assert _alerts(_zone(sensors=()), {}) == ["Aucun capteur de sol associé à cette zone."]


def test_latest_by_device_keeps_the_most_recent_value():
    readings = pd.DataFrame(
        [
            {"device_id": "sol-1", "metric": "humidity_pct", "value": 30.0, "time": NOW - timedelta(minutes=5)},
            {"device_id": "sol-1", "metric": "humidity_pct", "value": 35.0, "time": NOW},
        ]
    )
    values, times = garden.latest_by_device(readings)
    assert values == {"sol-1": {"humidity_pct": 35.0}}
    assert times["sol-1"] == pd.Timestamp(NOW)


# --- formulaire / rendu ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "name,dry,wet,ok",
    [("Zone 1", None, None, True), ("Zone 1", 20.0, 60.0, True), ("", None, None, False),
     ("Zone 1", 20.0, None, False), ("Zone 1", 60.0, 20.0, False)],
)
def test_validate_zone_form(name, dry, wet, ok):
    assert (garden_tab.validate_zone_form(name, dry, wet) is None) is ok


def test_valve_value_round_trip():
    assert garden_tab.parse_valve_value(garden_tab.valve_value("jardin", "vanne_1")) == ("jardin", "vanne_1")
    assert garden_tab.valve_value(None, None) is None and garden_tab.parse_valve_value(None) is None


def test_moisture_gauge_shows_target_band_and_marker():
    gauge = garden_tab.build_moisture_gauge(60, TOMATO, "ok")
    band, marker = gauge.children
    assert band.style == {"left": "40%", "width": "35%"}
    assert marker.style == {"left": "60%"} and "bg-success" in marker.className


def test_zone_card_renders_with_minimal_data():
    data = {"latest": {"sol-1": {"humidity_pct": 30.0}}, "times": {"sol-1": NOW}, "names": {},
            "valve_states": {}, "last_watering": {}, "sun_hours": {1: 2.5}, "recent": {}}
    card = garden_tab.build_zone_card(_zone(), data)
    assert card is not None


# --- programmes : condition "selon la culture de chaque zone" ---------------------

def test_program_summary_and_run_detail_for_zone_condition():
    from app import callbacks

    assert callbacks._format_conditions_summary([{"type": "zone_crop_moisture"}]) == ["selon la culture de chaque zone"]
    partial = {"status": "executed", "skip_reason": "Tomates déjà assez humide pour Tomate (60% ≥ 40%)",
               "valves_triggered": [{"metric": "vanne_2", "duration_s": 300}]}
    assert callbacks._format_run_detail(partial) == (
        "vanne_2 (5min) · ignorées : Tomates déjà assez humide pour Tomate (60% ≥ 40%)"
    )
    assert callbacks._format_run_detail({"status": "skipped", "skip_reason": "pluie", "valves_triggered": None}) == "pluie"
