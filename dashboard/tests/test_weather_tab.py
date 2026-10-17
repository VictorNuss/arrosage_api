from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from app import config, queries, weather_tab

# 17/10/2026 08:00 UTC = 10:00 à Paris (heure d'été) : une journée locale
# entière tient entre 22:00 UTC la veille et 22:00 UTC le jour même.
NOW = datetime(2026, 10, 17, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _paris_tz(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_TZ", "Europe/Paris")


def _hourly(start, hours, metric, value_fn, source="AROME"):
    return [
        {"valid_time": start + timedelta(hours=h), "source": source, "metric": metric, "value": value_fn(h)}
        for h in range(hours)
    ]


def _forecast(rows):
    return pd.DataFrame(rows, columns=["valid_time", "source", "metric", "value"])


# --- prefer_finest_source -------------------------------------------------------------

def test_prefer_finest_source_keeps_arome_where_both_models_overlap():
    t0 = NOW
    df = _forecast(
        [
            {"valid_time": t0, "source": "ARPEGE", "metric": "temperature_c", "value": 10.0},
            {"valid_time": t0, "source": "AROME", "metric": "temperature_c", "value": 11.0},
            {"valid_time": t0, "source": "ARPEGE", "metric": "precipitation_mm", "value": 0.5},
            {"valid_time": t0 + timedelta(days=3), "source": "ARPEGE", "metric": "temperature_c", "value": 9.0},
        ]
    )
    merged = queries.prefer_finest_source(df)
    assert len(merged) == 3
    at_t0 = merged[(merged["valid_time"] == t0) & (merged["metric"] == "temperature_c")]
    assert at_t0["source"].tolist() == ["AROME"]
    # Sans AROME pour cette échéance/métrique, ARPEGE reste utilisé.
    assert "ARPEGE" in merged["source"].tolist()


def test_prefer_finest_source_on_empty_frame():
    assert queries.prefer_finest_source(_forecast([])).empty


# --- daily_summary ------------------------------------------------------------------------

def test_daily_summary_min_max_and_rain_per_local_day():
    start = NOW  # 10:00 locale -> 14 h restantes aujourd'hui, puis 2 jours complets
    rows = _hourly(start, 62, "temperature_c", lambda h: 10 + (h % 24) / 2)
    rows += _hourly(start, 62, "precipitation_mm", lambda h: 0.5 if 20 <= h < 24 else 0.0)
    days = weather_tab.daily_summary(_forecast(rows), now=NOW)

    assert [d["day"] for d in days] == [date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)]
    today = days[0]
    assert today["temp_min"] == 10.0 and today["temp_max"] == 16.5  # h = 0..13
    assert today["rain_mm"] == 0.0
    # h = 20..23 -> 06:00-09:00 le 18 (heure de Paris)
    assert days[1]["rain_mm"] == pytest.approx(2.0)


def test_daily_summary_drops_incomplete_days_except_today():
    start = NOW
    # 14 h aujourd'hui + 24 h demain + 5 h après-demain (fin d'horizon du modèle)
    rows = _hourly(start, 14 + 24 + 5, "temperature_c", lambda h: 12.0)
    days = weather_tab.daily_summary(_forecast(rows), now=NOW)
    assert [d["day"] for d in days] == [date(2026, 10, 17), date(2026, 10, 18)]


def test_daily_summary_ignores_past_days_and_empty_forecast():
    rows = _hourly(NOW - timedelta(days=2), 24, "temperature_c", lambda h: 12.0)
    assert weather_tab.daily_summary(_forecast(rows), now=NOW) == []
    assert weather_tab.daily_summary(_forecast([]), now=NOW) == []


# --- daily_observed_rain --------------------------------------------------------------------

def test_daily_observed_rain_sums_per_local_day():
    observed = pd.DataFrame(
        [
            # 23:30 UTC le 15 = 01:30 le 16 à Paris
            {"time": datetime(2026, 10, 15, 23, 30, tzinfo=timezone.utc), "metric": "precipitation_mm", "value": 1.0},
            {"time": datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc), "metric": "precipitation_mm", "value": 0.5},
            {"time": datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc), "metric": "temperature_c", "value": 15.0},
        ]
    )
    totals = weather_tab.daily_observed_rain(observed)
    assert totals.to_dict("records") == [{"day": date(2026, 10, 16), "rain_mm": 1.5}]


def test_daily_observed_rain_without_rain_data():
    observed = pd.DataFrame(columns=["time", "metric", "value"])
    assert weather_tab.daily_observed_rain(observed).empty


# --- libellés / icônes / figures -----------------------------------------------------------

def test_day_label():
    today = date(2026, 10, 17)  # samedi
    assert weather_tab.day_label(today, today) == "Aujourd'hui"
    assert weather_tab.day_label(date(2026, 10, 18), today) == "Demain"
    assert weather_tab.day_label(date(2026, 10, 19), today) == "Lun 19"


@pytest.mark.parametrize(
    "rain_mm,icon",
    [(0.0, "bi-sun"), (0.3, "bi-cloud-drizzle"), (2.0, "bi-cloud-rain"), (12.0, "bi-cloud-rain-heavy")],
)
def test_rain_icon(rain_mm, icon):
    assert weather_tab.rain_icon(rain_mm) == icon


def test_observed_rain_figure_says_so_when_it_did_not_rain():
    dry = pd.DataFrame({"day": [date(2026, 10, 16), date(2026, 10, 17)], "rain_mm": [0.0, 0.0]})
    wet = pd.DataFrame({"day": [date(2026, 10, 16)], "rain_mm": [3.0]})
    assert len(weather_tab.build_observed_rain_figure(dry).layout.annotations) == 1
    assert len(weather_tab.build_observed_rain_figure(wet).layout.annotations) == 0


def test_hourly_figure_splits_temperature_and_rain_without_legend():
    rows = _hourly(NOW, 96, "temperature_c", lambda h: 12.0) + _hourly(NOW, 96, "precipitation_mm", lambda h: 0.2)
    fig = weather_tab.build_hourly_figure(_forecast(rows), hours=48, now=NOW)

    assert fig.layout.showlegend is False
    temperature, rain = fig.data
    assert temperature.type == "scatter" and temperature.yaxis == "y"
    assert rain.type == "bar" and rain.yaxis == "y2"  # graphique séparé, pas d'axe partagé
    assert len(temperature.x) == 49  # fenêtre de 48 h (bornes incluses)
    assert fig.layout.yaxis2.range[1] == 2.0  # échelle de pluie minimale
