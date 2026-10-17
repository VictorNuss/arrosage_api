from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app import config, history_tab

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
START = NOW - timedelta(hours=24)


@pytest.fixture(autouse=True)
def _paris_tz(monkeypatch):
    monkeypatch.setattr(config, "LOCAL_TZ", "Europe/Paris")


def _valve_events(rows):
    """rows: (minutes depuis START, device_id, metric, value)."""
    return pd.DataFrame(
        [
            {"time": START + timedelta(minutes=m), "device_id": d, "metric": k, "value": v}
            for m, d, k, v in rows
        ]
    )


def _spans(intervals):
    return [
        (row.metric, int((row.start - START).total_seconds() // 60), int((row.end - START).total_seconds() // 60))
        for row in intervals.itertuples()
    ]


# --- period_bounds ---------------------------------------------------------------------

@pytest.mark.parametrize("key,hours", [("24h", 24), ("7d", 168), ("30d", 720), ("inconnue", 24)])
def test_period_bounds(key, hours):
    start, end = history_tab.period_bounds(key, now=NOW)
    assert end == NOW and end - start == timedelta(hours=hours)


# --- displayed_start ------------------------------------------------------------------------

def _times(*minutes):
    return pd.DataFrame({"time": [START + timedelta(minutes=m) for m in minutes]})


def test_displayed_start_follows_the_first_data_when_more_recent():
    assert history_tab.displayed_start(START, _times(600, 700), _times(650)) == START + timedelta(minutes=600)


def test_displayed_start_keeps_the_period_start_when_data_is_older():
    # ex. dernier état d'une vanne antérieur à la période (get_valve_history)
    assert history_tab.displayed_start(START, _times(-30, 10), _times(5)) == START


def test_displayed_start_without_data():
    assert history_tab.displayed_start(START, pd.DataFrame(), pd.DataFrame(columns=["time"])) == START


# --- watering_intervals ---------------------------------------------------------------------

def test_open_then_closed_gives_one_interval_ignoring_transitions():
    events = _valve_events(
        [
            (60, "jardin", "vanne_1", 0.5),  # transition d'ouverture : pas encore d'eau
            (61, "jardin", "vanne_1", 1.0),
            (70, "jardin", "vanne_1", 0.5),  # transition de fermeture : fin d'arrosage
            (71, "jardin", "vanne_1", 0.0),
        ]
    )
    assert _spans(history_tab.watering_intervals(events, START, NOW)) == [("vanne_1", 61, 70)]


def test_valve_already_open_before_the_period_is_clipped_to_its_start():
    events = _valve_events([(-30, "jardin", "vanne_1", 1.0), (20, "jardin", "vanne_1", 0.0)])
    assert _spans(history_tab.watering_intervals(events, START, NOW)) == [("vanne_1", 0, 20)]


def test_valve_still_open_runs_until_the_end_of_the_period():
    events = _valve_events([(1400, "jardin", "vanne_2", 1.0)])
    assert _spans(history_tab.watering_intervals(events, START, NOW)) == [("vanne_2", 1400, 1440)]


def test_closed_before_the_period_gives_nothing():
    events = _valve_events([(-120, "jardin", "vanne_1", 1.0), (-60, "jardin", "vanne_1", 0.0)])
    assert history_tab.watering_intervals(events, START, NOW).empty


def test_repeated_open_states_do_not_split_an_interval_and_valves_are_independent():
    events = _valve_events(
        [
            (10, "jardin", "vanne_1", 1.0),
            (15, "jardin", "vanne_1", 1.0),  # republication (get_status) pendant l'ouverture
            (30, "jardin", "vanne_1", 0.0),
            (10, "serre", "vanne_1", 1.0),
            (40, "serre", "vanne_1", 0.0),
        ]
    )
    intervals = history_tab.watering_intervals(events, START, NOW)
    assert sorted(zip(intervals["device_id"], _spans(intervals))) == [
        ("jardin", ("vanne_1", 10, 30)),
        ("serre", ("vanne_1", 10, 40)),
    ]


def test_no_events():
    assert history_tab.watering_intervals(pd.DataFrame(), START, NOW).empty


# --- watering_summary / format_duration ---------------------------------------------------------

def test_watering_summary_counts_and_sums_per_valve():
    events = _valve_events(
        [(0, "jardin", "vanne_1", 1.0), (10, "jardin", "vanne_1", 0.0),
         (100, "jardin", "vanne_1", 1.0), (125, "jardin", "vanne_1", 0.0)]
    )
    summary = history_tab.watering_summary(history_tab.watering_intervals(events, START, NOW))
    assert summary == [{"device_id": "jardin", "metric": "vanne_1", "count": 2, "duration_s": 35 * 60.0}]


@pytest.mark.parametrize("seconds,text", [(30, "< 1 min"), (60, "1 min"), (600, "10 min"), (3600, "1 h"), (5400, "1 h 30")])
def test_format_duration(seconds, text):
    assert history_tab.format_duration(seconds) == text


# --- figures ----------------------------------------------------------------------------------------

def test_watering_figure_has_one_row_per_valve_even_without_watering():
    keys = [("jardin", "vanne_1"), ("jardin", "vanne_2")]
    events = _valve_events([(10, "jardin", "vanne_1", 1.0), (30, "jardin", "vanne_1", 0.0)])
    fig = history_tab.build_watering_figure(
        history_tab.watering_intervals(events, START, NOW), keys, {"jardin": "Jardin"}, START, NOW
    )
    assert list(fig.layout.yaxis.categoryarray) == ["Vanne 2", "Vanne 1"]  # de haut en bas : vanne 1 d'abord
    (bars,) = fig.data
    assert list(bars.y) == ["Vanne 1"]
    assert bars.x[0] == 20 * 60 * 1000  # 20 min, en ms sur l'axe date


def test_valve_names_include_the_device_only_when_several_devices():
    keys = [("jardin", "vanne_1"), ("serre", "vanne_1")]
    fig = history_tab.build_watering_figure(pd.DataFrame(), keys, {"jardin": "Jardin", "serre": "Serre"}, START, NOW)
    assert list(fig.layout.yaxis.categoryarray) == ["Vanne 1 · Serre", "Vanne 1 · Jardin"]


def test_metric_figure_one_line_per_device_and_legend_only_if_several():
    times = [START + timedelta(hours=h) for h in range(3)]
    one = pd.DataFrame({"time": times, "device_id": "jardin", "value": [1.0, 2.0, 3.0]})
    two = pd.concat([one, one.assign(device_id="serre")])

    fig_one = history_tab.build_metric_figure(one, "°C", {}, START, NOW)
    fig_two = history_tab.build_metric_figure(two, "°C", {"serre": "Serre"}, START, NOW)

    assert len(fig_one.data) == 1 and fig_one.layout.showlegend is False
    assert [trace.name for trace in fig_two.data] == ["jardin", "Serre"] and fig_two.layout.showlegend is True
    assert fig_one.layout.yaxis.title.text == "°C"
