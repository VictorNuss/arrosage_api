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
    rows = result[(result["device_id"] == "jardin-1") & (result["metric"] == "vanne_1")]
    # Régression : la ligne périmée du cache restait aussi, et la vanne
    # apparaissait deux fois dans le panneau des vannes.
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["value"] == 1.0
    assert row["direction"] is None


def test_one_row_per_key_when_db_wins_on_some_valves_only(monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        queries, "_query_db_latest_readings",
        lambda: _db_df([
            ("jardin-1", "vanne_1", 0.0, now + timedelta(seconds=30)),
            ("jardin-1", "vanne_2", 0.0, now),
            ("jardin-1", "temperature_c", 21.0, now),
        ]),
    )
    monkeypatch.setattr(
        queries.live_state, "get_latest_readings",
        lambda: _live_rows([
            ("jardin-1", "vanne_1", 1.0, now, None),
            ("jardin-1", "vanne_2", 1.0, now, None),
        ]),
    )

    valves = queries.get_valve_states()
    assert valves[["metric", "value"]].values.tolist() == [["vanne_1", 0.0], ["vanne_2", 1.0]]


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


def test_valve_state_comes_from_permanent_journal_when_raw_is_purged():
    """L'état d'une vanne inutilisée depuis plus longtemps que la
    conservation du brut vient de valve_events (le cache live ignore les
    messages retenus) ; une vanne présente dans les deux n'apparaît qu'une
    fois."""
    now = datetime.now(timezone.utc)
    readings = pd.DataFrame(
        [
            {"device_id": "jardin-1", "metric": "temperature_c", "value": 20.0, "unit": "°C", "time": now},
            {"device_id": "jardin-1", "metric": "vanne_2", "value": 1.0, "unit": None, "time": now},
        ]
    )
    valves = pd.DataFrame(
        [
            {"device_id": "jardin-1", "metric": "vanne_1", "value": 0.0, "time": now - timedelta(days=40)},
            {"device_id": "jardin-1", "metric": "vanne_2", "value": 1.0, "time": now},
        ]
    )
    merged = queries.merge_valve_journal(readings, valves)
    assert list(zip(merged["metric"], merged["value"])) == [
        ("temperature_c", 20.0), ("vanne_1", 0.0), ("vanne_2", 1.0)
    ]
    assert merged["direction"].isna().all()


def test_merge_valve_journal_without_valves_keeps_readings():
    readings = pd.DataFrame(
        [{"device_id": "a", "metric": "temperature_c", "value": 1.0, "unit": "°C", "time": datetime.now(timezone.utc)}]
    )
    empty = pd.DataFrame(columns=["device_id", "metric", "value", "time"])
    merged = queries.merge_valve_journal(readings, empty)
    assert len(merged) == 1 and merged["direction"].isna().all()


def test_db_snapshot_is_shared_by_callbacks_of_the_same_tick(monkeypatch):
    """Vannes, cuve, sol, cartes : un seul aller-retour base par tick."""
    calls = []
    monkeypatch.setattr(queries, "_latest_cache", None)
    monkeypatch.setattr(queries, "_fetch_db_latest_readings", lambda: calls.append(1) or _db_df([]))
    queries._query_db_latest_readings()
    queries._query_db_latest_readings()
    assert len(calls) == 1
    monkeypatch.setattr(queries.config, "LATEST_READINGS_CACHE_S", 0)
    queries._query_db_latest_readings()
    assert len(calls) == 2
