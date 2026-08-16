from datetime import datetime, timedelta, timezone

import pytest

from app import conditions

NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def _rain_row(hours_from_now, value, source="AROME"):
    return {"valid_time": NOW + timedelta(hours=hours_from_now), "source": source, "value": value}


# --- sum_rain_forecast_mm -----------------------------------------------------

def test_sum_rain_forecast_sums_values_within_window():
    rows = [_rain_row(1, 1.0), _rain_row(2, 2.0), _rain_row(4, 5.0)]  # hors fenêtre de 3h
    assert conditions.sum_rain_forecast_mm(rows, NOW, window_hours=3) == 3.0


def test_sum_rain_forecast_ignores_past_values():
    rows = [_rain_row(-1, 10.0), _rain_row(1, 1.0)]
    assert conditions.sum_rain_forecast_mm(rows, NOW, window_hours=3) == 1.0


def test_sum_rain_forecast_prefers_arome_over_arpege_for_same_valid_time():
    """Régression : AROME et ARPEGE se chevauchent, il ne faut pas compter
    la même pluie deux fois."""
    rows = [_rain_row(1, 1.0, source="ARPEGE"), _rain_row(1, 9.0, source="AROME")]
    assert conditions.sum_rain_forecast_mm(rows, NOW, window_hours=3) == 9.0


def test_sum_rain_forecast_empty_rows_is_zero():
    assert conditions.sum_rain_forecast_mm([], NOW, window_hours=3) == 0


# --- evaluate_no_rain_forecast --------------------------------------------------

def test_no_rain_forecast_passes_when_below_threshold():
    rows = [_rain_row(1, 0.05)]
    ok, reason = conditions.evaluate_no_rain_forecast(rows, NOW, {"window_hours": 3, "threshold_mm": 0.2})
    assert ok is True
    assert reason is None


def test_no_rain_forecast_fails_when_at_or_above_threshold():
    rows = [_rain_row(1, 0.2)]
    ok, reason = conditions.evaluate_no_rain_forecast(rows, NOW, {"window_hours": 3, "threshold_mm": 0.2})
    assert ok is False
    assert "pluie" in reason


def test_no_rain_forecast_uses_default_params_when_missing():
    rows = [_rain_row(1, 5.0)]
    ok, _ = conditions.evaluate_no_rain_forecast(rows, NOW, {})
    assert ok is False


# --- evaluate_avoid_time_window --------------------------------------------------

@pytest.mark.parametrize("hour,expected_ok", [(9, True), (10, False), (14, False), (17, False), (18, True), (20, True)])
def test_avoid_time_window_blocks_inside_range(hour, expected_ok):
    now = NOW.replace(hour=hour, minute=0)
    ok, reason = conditions.evaluate_avoid_time_window(now, {"start": "10:00", "end": "18:00"})
    assert ok is expected_ok
    if not expected_ok:
        assert "heure interdite" in reason


# --- evaluate_min_tank_pct --------------------------------------------------------

def test_min_tank_pct_fails_when_below_threshold():
    ok, reason = conditions.evaluate_min_tank_pct(10.0, 150.0, {"min_pct": 10})
    assert ok is False
    assert "cuve à" in reason


def test_min_tank_pct_passes_when_above_threshold():
    ok, reason = conditions.evaluate_min_tank_pct(80.0, 150.0, {"min_pct": 10})
    assert ok is True
    assert reason is None


def test_min_tank_pct_blocks_when_no_reading_known():
    """Régression : pas de lecture connue -> on bloque par sécurité plutôt
    que de supposer la cuve pleine."""
    ok, reason = conditions.evaluate_min_tank_pct(None, 150.0, {"min_pct": 10})
    assert ok is False
    assert "inconnu" in reason


def test_min_tank_pct_exact_boundary_passes():
    ok, _ = conditions.evaluate_min_tank_pct(15.0, 150.0, {"min_pct": 10})  # exactement 10%
    assert ok is True


# --- evaluate_conditions (dispatcher) --------------------------------------------

def test_evaluate_conditions_passes_when_all_pass():
    ctx = {"now": NOW, "rain_rows": [], "tank_value_cm": 100.0, "tank_height_full_cm": 150.0}
    ok, reason = conditions.evaluate_conditions(
        [{"type": "no_rain_forecast"}, {"type": "min_tank_pct", "min_pct": 10}], ctx
    )
    assert ok is True
    assert reason is None


def test_evaluate_conditions_stops_at_first_failure():
    ctx = {"now": NOW, "rain_rows": [_rain_row(1, 5.0)], "tank_value_cm": None, "tank_height_full_cm": 150.0}
    ok, reason = conditions.evaluate_conditions(
        [{"type": "no_rain_forecast"}, {"type": "min_tank_pct", "min_pct": 10}], ctx
    )
    assert ok is False
    assert "pluie" in reason  # la 1ère condition en échec, pas la cuve


def test_evaluate_conditions_empty_list_always_passes():
    ctx = {"now": NOW, "rain_rows": [], "tank_value_cm": None, "tank_height_full_cm": 150.0}
    ok, reason = conditions.evaluate_conditions([], ctx)
    assert ok is True
    assert reason is None


def test_evaluate_conditions_ignores_unknown_condition_type():
    ctx = {"now": NOW, "rain_rows": [], "tank_value_cm": 100.0, "tank_height_full_cm": 150.0}
    ok, reason = conditions.evaluate_conditions([{"type": "max_wind_kmh", "max": 30}], ctx)
    assert ok is True
    assert reason is None
