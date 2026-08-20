from datetime import datetime, timedelta, timezone

import pandas as pd

from app import queries


def _db_df(rows):
    """rows: liste de (device_id, metric, value, time)."""
    return pd.DataFrame(
        [
            {"device_id": d, "metric": m, "value": v, "unit": None, "time": t, "direction": None}
            for d, m, v, t in rows
        ]
    )


def _live_rows(rows):
    """rows: liste de (device_id, metric, value, time, direction)."""
    return [
        {"device_id": d, "metric": m, "value": v, "unit": None, "time": t, "direction": dir_}
        for d, m, v, t, dir_ in rows
    ]


def test_live_cache_wins_on_key_it_knows_even_if_db_timestamp_is_a_few_ms_later(monkeypatch):
    """Régression : deux abonnés MQTT indépendants (ingest, cache mémoire du
    dashboard) reçoivent le même message à quelques ms d'écart. Sans marge,
    la ligne base (qui ne connaît jamais la direction) pouvait "gagner" la
    fusion sur ce simple écart d'horloge et faire perdre le label
    ouverture/fermeture pour rien."""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        queries, "_query_db_latest_readings",
        lambda: _db_df([("jardin-1", "vanne_1", 0.5, now + timedelta(milliseconds=200))]),
    )
    monkeypatch.setattr(
        queries.live_state, "get_latest_readings",
        lambda: _live_rows([("jardin-1", "vanne_1", 0.5, now, "opening")]),
    )

    result = queries.get_latest_readings()
    row = result[(result["device_id"] == "jardin-1") & (result["metric"] == "vanne_1")].iloc[0]
    assert row["direction"] == "opening"


def test_db_wins_when_meaningfully_fresher_than_live_cache(monkeypatch):
    """Si la base a une valeur nettement plus récente que le cache mémoire
    (celui-ci a raté un message, ex: coupure MQTT côté dashboard), elle doit
    l'emporter plutôt que de garder une valeur périmée du cache."""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        queries, "_query_db_latest_readings",
        lambda: _db_df([("jardin-1", "vanne_1", 1.0, now + timedelta(seconds=30))]),
    )
    monkeypatch.setattr(
        queries.live_state, "get_latest_readings",
        lambda: _live_rows([("jardin-1", "vanne_1", 0.5, now, "opening")]),
    )

    result = queries.get_latest_readings()
    row = result[(result["device_id"] == "jardin-1") & (result["metric"] == "vanne_1")].iloc[0]
    assert row["value"] == 1.0
    assert row["direction"] is None


def test_keys_only_in_db_are_kept(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        queries, "_query_db_latest_readings",
        lambda: _db_df([("jardin-1", "temperature_c", 21.3, now)]),
    )
    monkeypatch.setattr(queries.live_state, "get_latest_readings", lambda: _live_rows([]))

    result = queries.get_latest_readings()
    assert len(result) == 1
    assert result.iloc[0]["metric"] == "temperature_c"


def test_keys_only_in_live_cache_are_kept(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        queries, "_query_db_latest_readings",
        lambda: _db_df([]),
    )
    monkeypatch.setattr(
        queries.live_state, "get_latest_readings",
        lambda: _live_rows([("jardin-1", "vanne_1", 1.0, now, None)]),
    )

    result = queries.get_latest_readings()
    assert len(result) == 1
    assert result.iloc[0]["metric"] == "vanne_1"
