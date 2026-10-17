"""Onglet Météo : résumé par jour, détail heure par heure, pluie passée.

Pensé d'abord pour le téléphone : température et pluie ne partagent jamais
un même axe (unités sans rapport), pas de légende latérale (chaque
graphique n'affiche qu'une grandeur, nommée par son axe), et les prévisions
AROME/ARPEGE sont fusionnées en une seule série (voir
queries.prefer_finest_source) plutôt que tracées côte à côte.
"""

from datetime import datetime, timedelta, timezone

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html
from plotly.subplots import make_subplots

from . import config, queries

TEMPERATURE = "temperature_c"
PRECIPITATION = "precipitation_mm"

_TEMPERATURE_COLOR = "#e67e22"
_RAIN_COLOR = "#3498db"

_RANGE_OPTIONS = [{"label": "48 h", "value": 48}, {"label": "4 jours", "value": 96}]
_DEFAULT_RANGE_HOURS = 48

_WEEKDAYS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
# En dessous, une journée de prévision est trop incomplète (fin d'horizon du
# modèle) pour donner un min/max ou un cumul représentatif.
_MIN_HOURS_FOR_A_DAY = 12

_GRAPH_CONFIG = {"displayModeBar": False, "responsive": True}


# --- Données ---------------------------------------------------------------------

def _local(times: pd.Series) -> pd.Series:
    return pd.to_datetime(times, utc=True).dt.tz_convert(config.LOCAL_TZ)


def daily_summary(forecast: pd.DataFrame, now: datetime | None = None, max_days: int = 5) -> list[dict]:
    """Un dict par jour (heure locale) à partir d'aujourd'hui : min/max de
    température et cumul de pluie. `forecast` : sortie de
    prefer_finest_source (une valeur par échéance et métrique)."""
    if forecast.empty:
        return []
    now = now or datetime.now(timezone.utc)
    today = pd.Timestamp(now).tz_convert(config.LOCAL_TZ).date()

    df = forecast.assign(day=_local(forecast["valid_time"]).dt.date)
    df = df[df["day"] >= today]

    days = []
    for day, group in df.groupby("day"):
        temperature = group[group["metric"] == TEMPERATURE]["value"]
        rain = group[group["metric"] == PRECIPITATION]["value"]
        if day != today and len(temperature) < _MIN_HOURS_FOR_A_DAY:
            continue
        days.append(
            {
                "day": day,
                "temp_min": float(temperature.min()) if not temperature.empty else None,
                "temp_max": float(temperature.max()) if not temperature.empty else None,
                "rain_mm": float(rain.sum()),
            }
        )
    return days[:max_days]


def daily_observed_rain(observed: pd.DataFrame, days: int = 7) -> pd.DataFrame:
    """Cumul de pluie observée par jour (heure locale), colonnes day/rain_mm."""
    rain = observed[observed["metric"] == PRECIPITATION]
    if rain.empty:
        return pd.DataFrame(columns=["day", "rain_mm"])
    totals = (
        rain.assign(day=_local(rain["time"]).dt.date)
        .groupby("day", as_index=False)["value"]
        .sum()
        .rename(columns={"value": "rain_mm"})
    )
    return totals.tail(days)


# --- Rendu -------------------------------------------------------------------------

def day_label(day, today) -> str:
    if day == today:
        return "Aujourd'hui"
    if day == today + timedelta(days=1):
        return "Demain"
    return f"{_WEEKDAYS[day.weekday()]} {day.day}"


def rain_icon(rain_mm: float) -> str:
    if rain_mm >= 5:
        return "bi-cloud-rain-heavy"
    if rain_mm >= 1:
        return "bi-cloud-rain"
    if rain_mm >= config.RAIN_THRESHOLD_MM:
        return "bi-cloud-drizzle"
    return "bi-sun"


def _format_temp(value) -> str:
    return "–" if value is None else f"{value:.0f}°"


def build_day_cards(days, today):
    cards = []
    for summary in days:
        rainy = summary["rain_mm"] >= config.RAIN_THRESHOLD_MM
        cards.append(
            html.Div(
                [
                    html.Div(day_label(summary["day"], today), className="weather-day-name"),
                    html.I(className=f"bi {rain_icon(summary['rain_mm'])} weather-day-icon"
                           f"{' text-primary' if rainy else ' text-warning'}"),
                    html.Div(
                        [
                            html.Span(_format_temp(summary["temp_max"]), className="fw-bold"),
                            html.Span(f" / {_format_temp(summary['temp_min'])}", className="text-muted"),
                        ]
                    ),
                    html.Div(
                        f"{summary['rain_mm']:.1f} mm" if rainy else "sec",
                        className=f"small {'text-primary fw-semibold' if rainy else 'text-muted'}",
                    ),
                ],
                className="weather-day card",
            )
        )
    return html.Div(cards, className="weather-days")


def build_hourly_figure(forecast: pd.DataFrame, hours: int, now: datetime | None = None) -> go.Figure:
    """Température (courbe) au-dessus, pluie horaire (barres) en dessous,
    axe du temps partagé : lisible même sur un écran étroit."""
    now = now or datetime.now(timezone.utc)
    window = forecast[
        (forecast["valid_time"] >= now - timedelta(hours=1)) & (forecast["valid_time"] <= now + timedelta(hours=hours))
    ]
    temperature = window[window["metric"] == TEMPERATURE]
    rain = window[window["metric"] == PRECIPITATION]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.62, 0.38], vertical_spacing=0.06)
    fig.add_trace(
        go.Scatter(
            x=_local(temperature["valid_time"]),
            y=temperature["value"],
            mode="lines",
            line=dict(color=_TEMPERATURE_COLOR, width=2.5, shape="spline"),
            fill="tozeroy",
            fillcolor="rgba(230, 126, 34, 0.08)",
            name="Température",
            hovertemplate="%{y:.1f} °C<extra></extra>",
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=_local(rain["valid_time"]),
            y=rain["value"],
            marker_color=_RAIN_COLOR,
            name="Pluie",
            hovertemplate="%{y:.1f} mm<extra></extra>",
        ),
        row=2,
        col=1,
    )

    if not temperature.empty:
        low, high = temperature["value"].min(), temperature["value"].max()
        fig.update_yaxes(range=[low - 2, high + 2], row=1, col=1)
    # Toujours au moins 2 mm d'échelle : sinon 0,1 mm de bruit remplit tout
    # le graphique et ressemble à une averse.
    fig.update_yaxes(range=[0, max(2.0, float(rain["value"].max()) * 1.15 if not rain.empty else 0)], row=2, col=1)
    fig.update_yaxes(title_text="°C", ticksuffix="°", row=1, col=1)
    fig.update_yaxes(title_text="mm", row=2, col=1)
    fig.update_xaxes(tickformat="%Hh\n%d/%m", row=2, col=1)
    fig.add_vline(x=pd.Timestamp(now).tz_convert(config.LOCAL_TZ), line=dict(color="#95a5a6", width=1, dash="dot"))

    fig.update_layout(
        height=360,
        showlegend=False,
        hovermode="x unified",
        bargap=0.15,
        margin=dict(l=48, r=12, t=10, b=36),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def build_observed_rain_figure(daily_rain: pd.DataFrame) -> go.Figure:
    labels = [f"{_WEEKDAYS[day.weekday()]}<br>{day.day}" for day in daily_rain["day"]]
    fig = go.Figure(
        go.Bar(
            x=labels,
            y=daily_rain["rain_mm"],
            marker_color=_RAIN_COLOR,
            text=[f"{v:.1f}" if v >= 0.1 else "" for v in daily_rain["rain_mm"]],
            textposition="outside",
            hovertemplate="%{y:.1f} mm<extra></extra>",
        )
    )
    top = float(daily_rain["rain_mm"].max()) if not daily_rain.empty else 0.0
    if top < 0.1:
        fig.add_annotation(
            text="Pas de pluie sur la période", x=0.5, y=0.5, xref="paper", yref="paper",
            showarrow=False, font=dict(color="#7b8a8b"),
        )
    fig.update_yaxes(title_text="mm", range=[0, max(2.0, top * 1.25)])
    fig.update_xaxes(tickangle=0)
    fig.update_layout(
        height=240, showlegend=False, margin=dict(l=48, r=12, t=10, b=30), paper_bgcolor="rgba(0,0,0,0)"
    )
    return fig


def _windy_radar_url():
    return (
        "https://embed.windy.com/embed2.html"
        f"?lat={config.WEATHER_LAT}&lon={config.WEATHER_LON}"
        f"&detailLat={config.WEATHER_LAT}&detailLon={config.WEATHER_LON}"
        "&width=650&height=450&zoom=8&level=surface"
        "&overlay=radar&product=radar"
        "&menu=&message=true&marker=true&calendar=now&pressure="
        "&type=map&location=coordinates&detail="
        "&metricWind=default&metricTemp=default&radarRange=-1"
    )


def _card(icon, title, body, header_extra=None):
    header = [html.Span([html.I(className=f"bi {icon} me-2"), title])]
    if header_extra is not None:
        header.append(header_extra)
    return dbc.Card(
        [
            dbc.CardHeader(header, className="fw-semibold d-flex justify-content-between align-items-center"),
            dbc.CardBody(body, className="p-2"),
        ],
        className="mb-3",
    )


def build_weather_tab():
    return html.Div(
        [
            dcc.Interval(id="weather-interval", interval=5 * 60_000),
            html.Div([html.I(className="bi bi-calendar3 me-1"), "Prochains jours"], className="section-title"),
            html.Div(id="weather-days", className="mb-3"),
            _card(
                "bi-clock",
                "Heure par heure",
                html.Div(id="weather-hourly"),
                header_extra=dbc.RadioItems(
                    id="weather-range",
                    options=_RANGE_OPTIONS,
                    value=_DEFAULT_RANGE_HOURS,
                    inline=True,
                    className="btn-group btn-group-sm segmented",
                    inputClassName="btn-check",
                    labelClassName="btn btn-outline-primary",
                    labelCheckedClassName="active",
                ),
            ),
            html.Div(
                "Prévisions AROME (≈2 jours, maille fine) puis ARPEGE, via Open-Meteo.",
                className="small text-muted mt-n2 mb-3",
            ),
            _card("bi-cloud-rain", "Pluie des 7 derniers jours", html.Div(id="weather-observed")),
            _card(
                "bi-broadcast",
                "Radar (Windy)",
                html.Iframe(src=_windy_radar_url(), className="weather-radar"),
            ),
        ],
        className="pt-3",
    )


def register_callbacks(app):
    @app.callback(
        Output("weather-days", "children"),
        Output("weather-hourly", "children"),
        Output("weather-observed", "children"),
        Input("weather-interval", "n_intervals"),
        Input("weather-range", "value"),
    )
    def update_weather(_n, range_hours):
        now = datetime.now(timezone.utc)
        today = pd.Timestamp(now).tz_convert(config.LOCAL_TZ).date()
        forecast = queries.prefer_finest_source(queries.get_weather_forecast())
        observed = queries.get_weather_observed(days=8)

        if forecast.empty:
            no_forecast = dbc.Alert(
                "Pas encore de prévisions (le service météo se met à jour toutes les 3h).",
                color="secondary",
                className="mb-0",
            )
            days, hourly = no_forecast, no_forecast
        else:
            days = build_day_cards(daily_summary(forecast, now), today)
            hourly = dcc.Graph(
                figure=build_hourly_figure(forecast, range_hours or _DEFAULT_RANGE_HOURS, now),
                config=_GRAPH_CONFIG,
            )

        daily_rain = daily_observed_rain(observed)
        if daily_rain.empty:
            past = dbc.Alert("Pas encore d'observations.", color="secondary", className="mb-0")
        else:
            past = dcc.Graph(figure=build_observed_rain_figure(daily_rain), config=_GRAPH_CONFIG)

        return days, hourly, past
