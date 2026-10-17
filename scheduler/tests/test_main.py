from datetime import datetime, time as time_type, timezone

from app.main import _is_due


def _program(days_of_week, hour, minute):
    return {"days_of_week": days_of_week, "start_time": time_type(hour, minute)}


def test_is_due_matches_day_and_exact_minute():
    # 2026-08-20 est un jeudi (isoweekday=4)
    now = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)
    assert _is_due(_program([4], 6, 30), now) is True


def test_is_due_false_when_wrong_day():
    now = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)  # jeudi
    assert _is_due(_program([1, 2, 3, 5, 6, 7], 6, 30), now) is False  # tous les jours sauf jeudi


def test_is_due_false_when_wrong_minute():
    now = datetime(2026, 8, 20, 6, 31, tzinfo=timezone.utc)
    assert _is_due(_program([4], 6, 30), now) is False


def test_is_due_true_every_day_of_week():
    now = datetime(2026, 8, 20, 6, 30, tzinfo=timezone.utc)
    assert _is_due(_program([1, 2, 3, 4, 5, 6, 7], 6, 30), now) is True


# --- run_cycle : condition "seuil de la culture" évaluée vanne par vanne -------

from datetime import timedelta  # noqa: E402

from app import main  # noqa: E402


def _run_cycle_with(mocker, moisture_by_sensor):
    now = datetime.now().astimezone().replace(second=0, microsecond=0)
    program = {
        "id": 7, "name": "Matin", "start_time": time_type(now.hour, now.minute),
        "days_of_week": [now.isoweekday()], "default_duration_s": 600,
        "conditions": [{"type": "zone_crop_moisture", "max_age_hours": 6}],
        "valves": [{"device_id": "jardin", "metric": "vanne_1", "duration_s": None},
                   {"device_id": "jardin", "metric": "vanne_2", "duration_s": 300}],
    }
    zones = {
        ("jardin", "vanne_1"): {"name": "Tomates", "crop_name": "Tomate", "moisture_min": 40,
                                "dry_ref": None, "wet_ref": None, "sensors": ["sol-1"]},
        ("jardin", "vanne_2"): {"name": "Salades", "crop_name": "Salade", "moisture_min": 55,
                                "dry_ref": None, "wet_ref": None, "sensors": ["sol-2"]},
    }
    readings = {d: {"value": v, "time": now - timedelta(minutes=30)} for d, v in moisture_by_sensor.items()}
    mocker.patch.object(main.db, "get_enabled_programs", return_value=[program])
    mocker.patch.object(main.db, "get_rain_forecast_rows", return_value=[])
    mocker.patch.object(main.db, "get_latest_tank_value", return_value=None)
    mocker.patch.object(main.db, "get_zones_by_valve", return_value=zones)
    mocker.patch.object(main.db, "get_latest_soil_moisture", return_value=readings)
    claim = mocker.patch.object(main.db, "try_claim_run", return_value=True)
    publish = mocker.patch.object(main, "_publish_valve_open")
    main.run_cycle(engine=None)
    _engine, _pid, _name, _scheduled, status, skip_reason, valves = claim.call_args.args
    return status, skip_reason, valves, [c.args for c in publish.call_args_list]


def test_only_dry_zones_are_watered(mocker):
    status, reason, valves, published = _run_cycle_with(mocker, {"sol-1": 60, "sol-2": 30})
    assert status == "executed"
    assert published == [("jardin", "vanne_2", 300)]
    assert [v["metric"] for v in valves] == ["vanne_2"]
    assert reason == "Tomates déjà assez humide pour Tomate (60% ≥ 40%)"


def test_program_skipped_when_every_zone_is_moist_enough(mocker):
    status, reason, valves, published = _run_cycle_with(mocker, {"sol-1": 60, "sol-2": 70})
    assert status == "skipped" and valves is None and published == []
    assert "Tomates" in reason and "Salades" in reason
