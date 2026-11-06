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


def _captured_history_sql(monkeypatch, metrics):
    from datetime import datetime, timezone

    import pandas as pd
    from sqlalchemy.dialects import postgresql

    captured = []
    monkeypatch.setattr(queries.pd, "read_sql_query", lambda stmt, conn: captured.append(stmt) or pd.DataFrame())
    monkeypatch.setattr(queries, "engine", __import__("unittest.mock").mock.MagicMock())
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    queries.get_history(["jardin"], metrics, end - timedelta(days=30), end)
    return str(captured[0].compile(dialect=postgresql.dialect())) if captured else None


def test_history_without_metric_list_reads_every_metric_of_the_aggregate(monkeypatch):
    """Régression : un capteur retiré depuis plus de 15 jours (purgé du brut)
    doit rester visible sur 30 jours / 1 an, d'après l'agrégat seul."""
    sql = _captured_history_sql(monkeypatch, None)
    assert "sensor_readings_hourly.metric IN" not in sql
    assert "FROM sensor_readings_hourly" in sql


def test_history_with_empty_metric_list_returns_nothing(monkeypatch):
    assert _captured_history_sql(monkeypatch, []) is None
