from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app import formatting


@pytest.mark.parametrize(
    "metric,expected",
    [
        ("temperature_c", "Température"),
        ("water_level_cm", "Niveau d'eau"),
        ("vanne_2", "Vanne 2"),
        ("vanne_potager_nord", "Vanne potager nord"),
        ("vanne_", "vanne_"),  # pas de suffixe : affichée telle quelle
        ("pression_bar", "pression_bar"),  # clé inconnue : affichée telle quelle
    ],
)
def test_metric_label(metric, expected):
    assert formatting.metric_label(metric) == expected


@pytest.mark.parametrize(
    "metric,expected",
    [("temperature_c", "bi-thermometer-half"), ("vanne_1", "bi-droplet"), ("pression_bar", "bi-activity")],
)
def test_metric_icon(metric, expected):
    assert formatting.metric_icon(metric) == expected


@pytest.mark.parametrize(
    "age,expected",
    [
        (timedelta(seconds=10), "à l'instant"),
        (timedelta(minutes=5, seconds=10), "il y a 5 min"),
        (timedelta(hours=3, minutes=1), "il y a 3 h"),
        (timedelta(days=2, hours=1), "il y a 2 j"),
    ],
)
def test_format_last_seen(age, expected):
    assert formatting.format_last_seen(pd.Timestamp(datetime.now(timezone.utc) - age)) == expected


@pytest.mark.parametrize("ts", [None, pd.NaT])
def test_format_last_seen_never(ts):
    assert formatting.format_last_seen(ts) == "jamais"
