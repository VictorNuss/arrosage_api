from datetime import datetime, timedelta, timezone

import pandas as pd

from app import queries


def _forecast_df(rows):
    """rows: liste de (source, metric, offset_hours, value)."""
    now = datetime.now(timezone.utc)
    return pd.DataFrame(
        [
            {
                "valid_time": now + timedelta(hours=offset_hours),
                "source": source,
                "metric": metric,
                "value": value,
            }
            for source, metric, offset_hours, value in rows
        ]
    )


def test_no_forecast_returns_none(monkeypatch):
    monkeypatch.setattr(queries, "get_weather_forecast", lambda: pd.DataFrame(columns=["valid_time", "source", "metric", "value"]))
    assert queries.get_rain_outlook() is None


def test_sums_hourly_precipitation_within_each_window(monkeypatch):
    df = _forecast_df(
        [
            ("AROME", "precipitation_mm", 1, 0.5),
            ("AROME", "precipitation_mm", 2, 1.0),
            ("AROME", "precipitation_mm", 10, 3.0),  # hors fenêtre 3h, dans la fenêtre 48h
            ("AROME", "precipitation_mm", 47, 2.0),
        ]
    )
    monkeypatch.setattr(queries, "get_weather_forecast", lambda: df)

    outlook = queries.get_rain_outlook()
    assert outlook["rain_3h_mm"] == 1.5
    assert outlook["rain_48h_mm"] == 6.5


def test_prefers_arome_over_arpege_on_overlapping_hours(monkeypatch):
    """Régression : AROME et ARPEGE couvrent tous les deux le court terme.
    Sans dédoublonnage, la même pluie serait comptée deux fois."""
    df = _forecast_df(
        [
            ("AROME", "precipitation_mm", 1, 1.0),
            ("ARPEGE", "precipitation_mm", 1, 4.0),  # même échéance, doit être ignorée
        ]
    )
    monkeypatch.setattr(queries, "get_weather_forecast", lambda: df)

    outlook = queries.get_rain_outlook()
    assert outlook["rain_3h_mm"] == 1.0


def test_uses_arpege_when_arome_has_no_coverage(monkeypatch):
    df = _forecast_df([("ARPEGE", "precipitation_mm", 47, 2.5)])
    monkeypatch.setattr(queries, "get_weather_forecast", lambda: df)

    outlook = queries.get_rain_outlook()
    assert outlook["rain_48h_mm"] == 2.5


def test_ignores_past_and_non_precipitation_rows(monkeypatch):
    df = _forecast_df(
        [
            ("AROME", "precipitation_mm", -1, 5.0),  # dans le passé, ignoré
            ("AROME", "temperature_c", 1, 21.3),  # pas de la pluie
            ("AROME", "precipitation_mm", 1, 0.3),
        ]
    )
    monkeypatch.setattr(queries, "get_weather_forecast", lambda: df)

    outlook = queries.get_rain_outlook()
    assert outlook["rain_3h_mm"] == 0.3
