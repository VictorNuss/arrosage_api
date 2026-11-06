from datetime import timedelta

import pytest

from app import queries


@pytest.mark.parametrize(
    "span,table",
    [
        (timedelta(hours=24), "sensor_readings_1min"),
        (timedelta(days=7), "sensor_readings_1min"),
        (timedelta(days=30), "sensor_readings_hourly"),
        (timedelta(days=365), "sensor_readings_daily"),
    ],
)
def test_history_source_picks_the_resolution_tier(span, table):
    assert queries.history_source(span).name == table


@pytest.mark.parametrize(
    "span,step",
    [
        (timedelta(hours=6), None),  # 360 minutes : rien à regrouper
        (timedelta(hours=24), timedelta(minutes=2)),
        (timedelta(days=7), timedelta(minutes=15)),
        (timedelta(days=30), None),  # 720 heures
        (timedelta(days=90), timedelta(hours=3)),
        (timedelta(days=365), None),  # journalier : jamais regroupé
    ],
)
def test_resample_step_keeps_under_max_points(span, step):
    assert queries.resample_step(span, 1000) == step


def test_resample_step_disabled_without_max_points():
    assert queries.resample_step(timedelta(days=7), None) is None
