"""Onglet Historique : arrosages effectués et évolution des mesures.

Même principe que l'onglet Météo, pour rester lisible sur téléphone :
  - aucune sélection obligatoire : tous les appareils et toutes les mesures
    s'affichent d'emblée, sur une période choisie en un clic ;
  - les vannes sont présentées comme des périodes d'arrosage (frise + durée
    totale par vanne), pas comme une suite de points 0/1 ;
  - un petit graphique par mesure, avec son unité : on ne mélange jamais
    des cm, des % et des °C sur un même axe. Une courbe par appareil.
"""

from datetime import datetime, timedelta, timezone

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, dcc, html, no_update

from . import config, queries
from .formatting import metric_icon, metric_label

# Clé -> (libellé, durée). La première est la période par défaut.
PERIODS = {"24h": ("24 h", timedelta(hours=24)), "7d": ("7 jours", timedelta(days=7)), "30d": ("30 jours", timedelta(days=30))}
DEFAULT_PERIOD = "24h"

# Mesures sans intérêt en historique : battery_v est une constante (device
# alimenté secteur, voir BatteryPlaceholderSensor côté firmware).
HIDDEN_METRICS = {"battery_v"}

# Même seuil que l'affichage des vannes (callbacks._valve_status) : seule
# une vanne pleinement ouverte compte comme un arrosage.
_VALVE_OPEN_THRESHOLD = 0.75

_DEVICE_COLORS = ["#18bc9c", "#e67e22", "#3498db", "#9b59b6", "#e74c3c", "#34495e"]
_VALVE_COLOR = "#18bc9c"
_GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


# --- Données ---------------------------------------------------------------------

def period_bounds(period_key, now=None):
    now = now or datetime.now(timezone.utc)
    _label, duration = PERIODS.get(period_key, PERIODS[DEFAULT_PERIOD])
    return now - duration, now


def watering_intervals(valve_df: pd.DataFrame, start, end) -> pd.DataFrame:
    """Périodes d'ouverture de chaque vanne, rognées à [start, end].

    `valve_df` : changements d'état (time, device_id, metric, value), y
    compris le dernier état avant `start` (voir queries.get_valve_history).
    Une vanne encore ouverte à la fin est comptée jusqu'à `end`.
    """
    columns = ["device_id", "metric", "start", "end"]
    if valve_df.empty:
        return pd.DataFrame(columns=columns)

    df = valve_df.assign(time=pd.to_datetime(valve_df["time"], utc=True)).sort_values("time")
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    intervals = []
    for (device_id, metric), group in df.groupby(["device_id", "metric"]):
        opened_at = None
        for row in group.itertuples():
            is_open = row.value >= _VALVE_OPEN_THRESHOLD
            if is_open and opened_at is None:
                opened_at = row.time
            elif not is_open and opened_at is not None:
                intervals.append((device_id, metric, opened_at, row.time))
                opened_at = None
        if opened_at is not None:
            intervals.append((device_id, metric, opened_at, end))

    result = pd.DataFrame(intervals, columns=columns)
    result["start"] = result["start"].clip(lower=start)
    result["end"] = result["end"].clip(upper=end)
    return result[result["end"] > result["start"]].reset_index(drop=True)


def watering_summary(intervals: pd.DataFrame) -> list[dict]:
    """Par vanne : nombre d'arrosages et durée totale (secondes)."""
    if intervals.empty:
        return []
    durations = (intervals["end"] - intervals["start"]).dt.total_seconds()
    grouped = intervals.assign(duration_s=durations).groupby(["device_id", "metric"])
    return [
        {"device_id": device_id, "metric": metric, "count": len(group), "duration_s": float(group["duration_s"].sum())}
        for (device_id, metric), group in grouped
    ]


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return "< 1 min"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d}" if rest else f"{hours} h"


# --- Rendu -----------------------------------------------------------------------------

def _local(times):
    return pd.to_datetime(times, utc=True).dt.tz_convert(config.LOCAL_TZ)


def _x_range(start, end):
    return [pd.Timestamp(start).tz_convert(config.LOCAL_TZ), pd.Timestamp(end).tz_convert(config.LOCAL_TZ)]


def _time_axis_format(start, end):
    return "%Hh" if (end - start) <= timedelta(days=1) else "%d/%m"


def _valve_name(device_id, metric, device_names, several_devices):
    label = metric_label(metric)
    return f"{label} · {device_names.get(device_id, device_id)}" if several_devices else label


def build_watering_figure(intervals: pd.DataFrame, valve_keys, device_names, start, end) -> go.Figure:
    """Frise : une ligne par vanne, une barre par période d'ouverture."""
    several_devices = len({device_id for device_id, _ in valve_keys}) > 1
    names = {key: _valve_name(*key, device_names, several_devices) for key in valve_keys}
    fig = go.Figure()
    if not intervals.empty:
        starts, ends = _local(intervals["start"]), _local(intervals["end"])
        durations = (intervals["end"] - intervals["start"]).dt.total_seconds()
        fig.add_trace(
            go.Bar(
                base=starts,
                x=(ends - starts).dt.total_seconds() * 1000,  # axe date : longueur en ms
                y=[names[(d, m)] for d, m in zip(intervals["device_id"], intervals["metric"])],
                orientation="h",
                marker_color=_VALVE_COLOR,
                customdata=[format_duration(s) for s in durations],
                hovertemplate="%{base|%d/%m %Hh%M} · %{customdata}<extra></extra>",
            )
        )
    fig.update_yaxes(
        type="category",
        categoryorder="array",
        categoryarray=list(reversed([names[key] for key in valve_keys])),
        range=[-0.5, len(valve_keys) - 0.5],
        fixedrange=True,
    )
    fig.update_xaxes(type="date", range=_x_range(start, end), tickformat=_time_axis_format(start, end))
    fig.update_layout(
        height=50 + 36 * max(1, len(valve_keys)),
        showlegend=False,
        bargap=0.35,
        margin=dict(l=10, r=12, t=6, b=28),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_watering_section(intervals, valve_keys, device_names, start, end):
    if not valve_keys:
        return dbc.Alert("Aucune vanne connue.", color="secondary", className="mb-0")
    summary = {(s["device_id"], s["metric"]): s for s in watering_summary(intervals)}
    several_devices = len({device_id for device_id, _ in valve_keys}) > 1
    chips = []
    for key in valve_keys:
        stats = summary.get(key)
        text = (
            f"{stats['count']} arrosage{'s' if stats['count'] > 1 else ''} · {format_duration(stats['duration_s'])}"
            if stats
            else "pas d'arrosage"
        )
        chips.append(
            html.Div(
                [
                    html.Div([html.I(className="bi bi-droplet me-1 text-primary"),
                              _valve_name(*key, device_names, several_devices)], className="fw-semibold small"),
                    html.Div(text, className="small text-muted" if not stats else "small"),
                ],
                className="history-chip",
            )
        )
    return [
        html.Div(chips, className="history-chips"),
        dcc.Graph(figure=build_watering_figure(intervals, valve_keys, device_names, start, end), config=_GRAPH_CONFIG),
    ]


def build_metric_figure(metric_df: pd.DataFrame, unit, device_names, start, end) -> go.Figure:
    """Une courbe par appareil ; légende seulement s'il y en a plusieurs."""
    fig = go.Figure()
    device_ids = sorted(metric_df["device_id"].unique())
    for index, device_id in enumerate(device_ids):
        group = metric_df[metric_df["device_id"] == device_id].sort_values("time")
        fig.add_trace(
            go.Scatter(
                x=_local(group["time"]),
                y=group["value"],
                mode="lines",
                line=dict(color=_DEVICE_COLORS[index % len(_DEVICE_COLORS)], width=2),
                name=device_names.get(device_id, device_id),
                hovertemplate=f"%{{y:.1f}} {unit or ''}<extra>%{{fullData.name}}</extra>",
            )
        )
    several = len(device_ids) > 1
    fig.update_xaxes(range=_x_range(start, end), tickformat=_time_axis_format(start, end))
    fig.update_yaxes(title_text=unit or "")
    fig.update_layout(
        height=230 if several else 200,
        showlegend=several,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        hovermode="x unified",
        margin=dict(l=48, r=12, t=28 if several else 8, b=28),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_metric_card(metric, metric_df, unit, device_names, start, end):
    values = metric_df["value"]
    suffix = f" {unit}" if unit else ""
    stats = f"min {values.min():.1f} · max {values.max():.1f}{suffix}"
    return dbc.Col(
        dbc.Card(
            [
                dbc.CardHeader(
                    [
                        html.Span([html.I(className=f"bi {metric_icon(metric)} me-2 text-primary"), metric_label(metric)],
                                  title=metric),
                        html.Span(stats, className="small text-muted fw-normal"),
                    ],
                    className="fw-semibold d-flex justify-content-between align-items-center flex-wrap gap-1",
                ),
                dbc.CardBody(
                    dcc.Graph(figure=build_metric_figure(metric_df, unit, device_names, start, end), config=_GRAPH_CONFIG),
                    className="p-1",
                ),
            ],
            className="h-100",
        ),
        lg=6,
        className="mb-3",
    )


def build_history_tab():
    return html.Div(
        [
            dbc.Card(
                dbc.CardBody(
                    dbc.Row(
                        [
                            dbc.Col(
                                dcc.Dropdown(
                                    id="history-devices",
                                    multi=True,
                                    placeholder="Tous les appareils",
                                ),
                                md=7,
                            ),
                            dbc.Col(
                                dbc.RadioItems(
                                    id="history-period",
                                    options=[{"label": label, "value": key} for key, (label, _) in PERIODS.items()],
                                    value=DEFAULT_PERIOD,
                                    inline=True,
                                    className="btn-group btn-group-sm segmented",
                                    inputClassName="btn-check",
                                    labelClassName="btn btn-outline-primary",
                                    labelCheckedClassName="active",
                                ),
                                md=5,
                                className="d-flex justify-content-md-end",
                            ),
                        ],
                        className="g-2 align-items-center",
                    ),
                    className="p-2",
                ),
                className="mb-3",
            ),
            html.Div([html.I(className="bi bi-droplet-half me-1"), "Arrosages"], className="section-title"),
            dbc.Card(dbc.CardBody(dcc.Loading(html.Div(id="history-watering")), className="p-2"), className="mb-3"),
            html.Div([html.I(className="bi bi-graph-up me-1"), "Mesures"], className="section-title"),
            dcc.Loading(html.Div(id="history-metrics")),
        ],
        className="pt-3",
    )


def register_callbacks(app):
    @app.callback(
        Output("history-devices", "options"),
        Output("history-devices", "value"),
        Input("tabs", "active_tab"),
        State("history-devices", "value"),
    )
    def populate_devices(active_tab, current):
        if active_tab != "history":
            return no_update, no_update
        devices = queries.get_devices()
        options = [{"label": row.get("name") or row["device_id"], "value": row["device_id"]} for _, row in devices.iterrows()]
        known = {option["value"] for option in options}
        kept = [device_id for device_id in (current or []) if device_id in known]
        return options, kept

    @app.callback(
        Output("history-watering", "children"),
        Output("history-metrics", "children"),
        Input("history-devices", "value"),
        Input("history-period", "value"),
        Input("tabs", "active_tab"),
    )
    def update_history(selected_devices, period, active_tab):
        if active_tab != "history":
            return no_update, no_update
        devices = queries.get_devices()
        device_names = {row["device_id"]: row.get("name") or row["device_id"] for _, row in devices.iterrows()}
        # Aucune sélection = tous les appareils (pas d'écran vide par défaut).
        device_ids = selected_devices or list(device_names)
        start, end = period_bounds(period)

        known_metrics = [m for m in queries.get_known_metrics() if m not in HIDDEN_METRICS]
        valve_metrics = {m for m in known_metrics if queries.is_valve_metric(m)}
        sensor_metrics = [m for m in known_metrics if m not in valve_metrics]

        valve_df = queries.get_valve_history(device_ids, start, end)
        latest = queries.get_latest_readings()
        valve_keys = sorted(
            {(row.device_id, row.metric) for row in latest.itertuples()
             if row.device_id in device_ids and row.metric in valve_metrics}
        )
        intervals = watering_intervals(valve_df, start, end)
        watering = build_watering_section(intervals, valve_keys, device_names, start, end)

        history = queries.get_history(device_ids, sensor_metrics, start, end)
        if history.empty:
            return watering, dbc.Alert("Aucune mesure sur cette période.", color="secondary")
        units = latest.dropna(subset=["unit"]).groupby("metric")["unit"].first().to_dict() if not latest.empty else {}
        cards = [
            build_metric_card(metric, history[history["metric"] == metric], units.get(metric), device_names, start, end)
            for metric in sensor_metrics
            if (history["metric"] == metric).any()
        ]
        return watering, dbc.Row(cards, className="g-3")
