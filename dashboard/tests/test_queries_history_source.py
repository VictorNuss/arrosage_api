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
