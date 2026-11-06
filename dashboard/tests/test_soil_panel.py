from datetime import datetime, timedelta, timezone

import pandas as pd

from app import soil_panel

NOW = datetime.now(timezone.utc)


def _readings(rows):
    return pd.DataFrame(rows, columns=["device_id", "metric", "value", "unit", "time", "direction"])


def _devices(rows):
    return pd.DataFrame(rows, columns=["device_id", "name"])


def test_one_row_per_soil_sensor_sorted_by_name_with_battery():
    readings = _readings(
        [
            ("sol-serre", "humidity_pct", 55.0, "%", NOW, None),
            ("sol-potager", "humidity_pct", 22.0, "%", NOW, None),
            ("sol-potager", "battery_pct", 15.0, "%", NOW, None),
            ("sol-potager", "temperature_c", 18.0, "°C", NOW, None),
            ("jardin-1", "vanne_1", 0.0, None, NOW, None),
        ]
    )
    devices = _devices([("sol-potager", "Potager"), ("sol-serre", "Serre"), ("jardin-1", "jardin-1")])

    rows = soil_panel.soil_sensor_rows(devices, readings)

    assert [(r["device_id"], r["name"], r["moisture_pct"], r["battery_pct"]) for r in rows] == [
        ("sol-potager", "Potager", 22.0, 15.0),
        ("sol-serre", "Serre", 55.0, None),
    ]


def test_most_recent_value_wins_when_db_and_live_cache_overlap():
    readings = _readings(
        [
            ("sol-potager", "humidity_pct", 30.0, "%", NOW - timedelta(minutes=30), None),
            ("sol-potager", "humidity_pct", 45.0, "%", NOW, None),
        ]
    )
    rows = soil_panel.soil_sensor_rows(_devices([]), readings)
    assert [r["moisture_pct"] for r in rows] == [45.0]


def test_no_soil_sensor_means_no_panel():
    readings = _readings([("jardin-1", "temperature_c", 18.0, "°C", NOW, None)])
    assert soil_panel.soil_sensor_rows(_devices([]), readings) == []
    assert soil_panel.build_soil_panel([]) is None


def test_moisture_color_thresholds():
    assert soil_panel.moisture_color(10) == "warning"
    assert soil_panel.moisture_color(50) == "success"
    assert soil_panel.moisture_color(90) == "info"


def test_is_stale():
    assert soil_panel.is_stale(NOW - timedelta(hours=7), now=NOW) is True
    assert soil_panel.is_stale(NOW - timedelta(hours=1), now=NOW) is False
    assert soil_panel.is_stale(None, now=NOW) is True


def _zone(zone_id, name, sensors, crop=None, dry=None, wet=None):
    return {"id": zone_id, "name": name, "sensors": sensors, "crop": crop,
            "moisture_dry_ref": dry, "moisture_wet_ref": wet}


def test_soil_rows_show_calibrated_zone_moisture_then_unzoned_sensors():
    """Même chiffre que l'onglet Potager : valeur calibrée par zone. Un
    capteur hors zone garde sa valeur brute."""
    readings = _readings(
        [
            ("sol-a", "humidity_pct", 30.0, "%", NOW, None),
            ("sol-b", "humidity_pct", 50.0, "%", NOW - timedelta(minutes=5), None),
            ("sol-b", "battery_pct", 12.0, "%", NOW, None),
            ("sol-libre", "humidity_pct", 70.0, "%", NOW, None),
        ]
    )
    tomate = {"name": "Tomate", "moisture_min": 40, "moisture_max": 60}
    zones = [
        _zone(1, "Tomates", ["sol-a", "sol-b"], crop=tomate, dry=20.0, wet=60.0),
        _zone(2, "Vide", []),
    ]

    rows = soil_panel.soil_rows(zones, _devices([("sol-libre", "Libre")]), readings)

    assert [(r["kind"], r["name"]) for r in rows] == [("zone", "Tomates"), ("sensor", "Libre")]
    zone = rows[0]
    # (30-20)/40*100 = 25 et (50-20)/40*100 = 75 -> 50, dans la cible 40-60.
    assert zone["moisture_pct"] == 50.0
    assert zone["status"] == "ok"
    assert zone["battery_pct"] == 12.0
    assert rows[1]["moisture_pct"] == 70.0
    assert soil_panel.build_soil_panel(rows) is not None
