from datetime import datetime, timedelta, timezone

import pandas as pd

from app import overview

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _readings(rows):
    return pd.DataFrame(rows, columns=["device_id", "metric", "value", "unit", "time", "direction"])


def _ago(**delta):
    return NOW - timedelta(**delta)


def test_active_devices_are_those_seen_within_the_hour():
    readings = _readings(
        [
            ("arrosage-01", "water_distance_cm", 45.0, "cm", _ago(minutes=2), None),
            ("arrosage-01", "humidity_pct", 100.0, "%", _ago(days=10), None),
            ("vieux-device", "temperature_c", 18.0, "°C", _ago(hours=2), None),
            ("vanne-seule", "vanne_1", 1.0, None, _ago(minutes=30), None),
        ]
    )
    assert overview.active_device_ids(readings, NOW) == {"arrosage-01", "vanne-seule"}


def test_no_readings_no_active_device():
    assert overview.active_device_ids(_readings([]), NOW) == set()


def test_abandoned_metrics_are_dropped_but_valves_kept():
    readings = _readings(
        [
            ("arrosage-01", "water_distance_cm", 45.0, "cm", _ago(minutes=2), None),
            ("arrosage-01", "humidity_pct", 100.0, "%", _ago(days=10), None),
            ("arrosage-01", "vanne_1", 0.0, None, _ago(days=10), None),
            ("sol-potager", "humidity_pct", 40.0, "%", _ago(hours=20), None),
        ]
    )
    kept = overview.drop_abandoned_metrics(readings, NOW)
    assert set(zip(kept["device_id"], kept["metric"])) == {
        ("arrosage-01", "water_distance_cm"),
        ("arrosage-01", "vanne_1"),
        ("sol-potager", "humidity_pct"),
    }


def test_latest_row_wins_when_db_and_live_cache_both_have_the_key():
    readings = _readings(
        [
            ("arrosage-01", "humidity_pct", 100.0, "%", _ago(days=10), None),
            ("arrosage-01", "humidity_pct", 42.0, "%", _ago(minutes=1), None),
        ]
    )
    kept = overview.drop_abandoned_metrics(readings, NOW)
    assert kept["value"].tolist() == [42.0]


def test_device_cards_skip_what_the_overview_already_shows():
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    readings = pd.DataFrame(
        [
            ("jardin", "vanne_1", 0.0, None, now),
            ("jardin", "water_level_cm", 80.0, "cm", now),
            ("jardin", "water_distance_cm", 70.0, "cm", now),
            ("jardin", "humidity_pct", 40.0, "%", now),
            ("jardin", "battery_v", 0.0, "V", now),
            ("jardin", "temperature_c", 18.0, "°C", now),
        ],
        columns=["device_id", "metric", "value", "unit", "time"],
    )
    assert overview.card_metrics(readings)["metric"].tolist() == ["temperature_c"]
