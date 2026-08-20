import base64
from datetime import datetime, time as time_type, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from plotly.subplots import make_subplots
import dash_bootstrap_components as dbc

from . import config, mqtt_control, ota_client, queries, valve_timers

_VALVE_OPEN_COLOR = "#28a745"
_VALVE_CLOSED_COLOR = "#dc3545"
_VALVE_TRANSITION_COLOR = "#fd7e14"

_VALVE_STATUS_COLORS = {"open": _VALVE_OPEN_COLOR, "closed": _VALVE_CLOSED_COLOR, "transition": _VALVE_TRANSITION_COLOR}
_VALVE_BADGE_COLORS = {"open": "success", "closed": "secondary", "transition": "warning"}
_TRANSITION_LABELS = {"opening": "OUVERTURE…", "closing": "FERMETURE…"}


def _valve_status(value) -> str:
    """Une vanne met ~15s à s'ouvrir/se fermer : le firmware publie un 3e
    état ("transition") pendant ce délai plutôt qu'un simple 0/1. Seuils
    plutôt qu'égalité stricte, par robustesse à l'arrondi flottant."""
    if value >= 0.75:
        return "open"
    if value <= 0.25:
        return "closed"
    return "transition"


def _valve_color(status: str) -> str:
    return _VALVE_STATUS_COLORS[status]


def _valve_label(status: str, direction=None) -> str:
    if status == "open":
        return "OUVERTE"
    if status == "closed":
        return "FERMÉE"
    return _TRANSITION_LABELS.get(direction, "EN TRANSITION…")


def _format_last_seen(ts: pd.Timestamp) -> str:
    if pd.isna(ts):
        return "jamais"
    delta = datetime.now(timezone.utc) - ts.to_pydatetime()
    if delta < timedelta(minutes=1):
        return "à l'instant"
    if delta < timedelta(hours=1):
        return f"il y a {int(delta.total_seconds() // 60)} min"
    if delta < timedelta(days=1):
        return f"il y a {int(delta.total_seconds() // 3600)} h"
    return f"il y a {delta.days} j"


_DURATION_OPTIONS = [{"label": f"{m} min" if m < 60 else "1 h", "value": m} for m in config.VALVE_DURATION_OPTIONS_MIN]


def _build_valve_badge(status, direction=None):
    return dbc.Badge(
        _valve_label(status, direction),
        color=_VALVE_BADGE_COLORS[status],
        className="mt-1 mb-1 d-block",
    )


def _build_valve_countdown(device_id, metric, status):
    remaining = valve_timers.get_remaining_seconds(device_id, metric) if status == "open" else None
    return f"Ferme dans {valve_timers.format_remaining(remaining)}" if remaining is not None else ""


def _build_valve_chip(row):
    status = _valve_status(row["value"])
    direction = row.get("direction")
    device_id = row["device_id"]
    metric = row["metric"]
    is_transitioning = status == "transition"

    return dbc.Col(
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div(metric, className="fw-bold small"),
                    html.Div(device_id, className="text-muted small"),
                    # Contenu de ces deux divs géré par update_valve_badges
                    # (rafraîchi toutes les 2s) : la structure ci-dessous
                    # (menu déroulant, boutons) n'est reconstruite que par
                    # update_valve_panel, beaucoup moins souvent, pour ne
                    # jamais perdre un clic pendant un rafraîchissement.
                    html.Div(
                        _build_valve_badge(status, direction),
                        id={"type": "valve-badge-wrap", "device": device_id, "metric": metric},
                    ),
                    html.Div(
                        _build_valve_countdown(device_id, metric, status),
                        id={"type": "valve-countdown-wrap", "device": device_id, "metric": metric},
                        className="text-muted small mb-1",
                        style={"minHeight": "1.1em"},
                    ),
                    dcc.Dropdown(
                        id={"type": "valve-duration", "device": device_id, "metric": metric},
                        options=_DURATION_OPTIONS,
                        value=config.DEFAULT_VALVE_DURATION_MIN,
                        clearable=False,
                        searchable=False,
                        className="mb-2",
                        style={"width": "110px"},
                    ),
                    dbc.ButtonGroup(
                        [
                            dbc.Button(
                                "Ouvrir",
                                id={"type": "valve-open-btn", "device": device_id, "metric": metric},
                                color="success",
                                size="sm",
                                n_clicks=0,
                                disabled=is_transitioning,
                            ),
                            dbc.Button(
                                "Fermer",
                                id={"type": "valve-close-btn", "device": device_id, "metric": metric},
                                color="secondary",
                                size="sm",
                                n_clicks=0,
                                disabled=is_transitioning,
                            ),
                        ],
                        size="sm",
                    ),
                ],
                className="text-center py-2",
            )
        ),
        width="auto",
    )


def _build_tank_gauge_figure(tank):
    pct = min(100.0, max(0.0, (tank["value"] / config.TANK_HEIGHT_FULL_CM) * 100))
    is_full = pct >= config.TANK_FULL_THRESHOLD_PCT
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=pct,
            number={"suffix": "%"},
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": "#0d6efd"},
                "steps": [
                    {"range": [0, 50], "color": "#f8d7da"},
                    {"range": [50, 95], "color": "#fff3cd"},
                    {"range": [95, 100], "color": "#d1e7dd"},
                ],
            },
            title={"text": "Cuve pleine ✅" if is_full else "Niveau de la cuve"},
        )
    )
    fig.update_layout(height=220, margin=dict(l=20, r=20, t=40, b=10))
    return fig


def _rain_tile(label, mm):
    will_rain = mm >= config.RAIN_THRESHOLD_MM
    return dbc.Col(
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div(label, className="fw-bold"),
                    html.Div(
                        f"🌧️ {mm:.1f} mm attendus" if will_rain else "☀️ Pas de pluie prévue",
                        className="" if will_rain else "text-muted",
                    ),
                ]
            ),
            color="info" if will_rain else "light",
            outline=not will_rain,
            className="h-100",
        ),
        md=6,
    )


def _build_history_figure(df: pd.DataFrame) -> go.Figure:
    """Courbes classiques pour les capteurs ; vannes affichées à part, en
    carrés rouge (fermée) / vert (ouverte), sur une piste dédiée synchronisée
    sur le même axe temporel."""
    is_valve = df["metric"].apply(queries.is_valve_metric)
    sensor_df = df[~is_valve]
    valve_df = df[is_valve]

    has_sensors = not sensor_df.empty
    has_valves = not valve_df.empty

    if has_sensors and has_valves:
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.06)
        sensor_row, valve_row = 1, 2
    else:
        fig = make_subplots(rows=1, cols=1)
        sensor_row = valve_row = 1

    if has_sensors:
        for (device_id, metric), group in sensor_df.groupby(["device_id", "metric"]):
            fig.add_trace(
                go.Scatter(
                    x=group["time"], y=group["value"], mode="lines", name=f"{device_id} · {metric}"
                ),
                row=sensor_row,
                col=1,
            )

    if has_valves:
        for (device_id, metric), group in valve_df.sort_values("time").groupby(["device_id", "metric"]):
            label = f"{device_id} · {metric}"
            colors = [_valve_color(_valve_status(v)) for v in group["value"]]
            fig.add_trace(
                go.Scatter(
                    x=group["time"],
                    y=[label] * len(group),
                    mode="markers",
                    marker=dict(symbol="square", size=14, color=colors, line=dict(width=0)),
                    name=label,
                    showlegend=False,
                ),
                row=valve_row,
                col=1,
            )
        fig.update_yaxes(fixedrange=True, row=valve_row, col=1)

    if not has_sensors and not has_valves:
        fig.update_layout(title="Aucune donnée sur cette période")

    fig.update_layout(margin=dict(l=40, r=20, t=30, b=40), legend=dict(orientation="h"))
    return fig


def _build_device_card(device_row, readings_for_device: pd.DataFrame):
    metric_items = []
    for _, reading in readings_for_device.sort_values("metric").iterrows():
        if queries.is_valve_metric(reading["metric"]):
            status = _valve_status(reading["value"])
            metric_items.append(
                dbc.ListGroupItem(
                    [
                        html.Span(reading["metric"], className="me-2"),
                        dbc.Badge(
                            _valve_label(status, reading.get("direction")).lower(),
                            color=_VALVE_BADGE_COLORS[status],
                        ),
                    ]
                )
            )
        else:
            unit = f" {reading['unit']}" if reading["unit"] else ""
            metric_items.append(
                dbc.ListGroupItem(f"{reading['metric']}: {reading['value']:.1f}{unit}")
            )

    last_seen = readings_for_device["time"].max() if not readings_for_device.empty else pd.NaT

    return dbc.Col(
        dbc.Card(
            [
                dbc.CardHeader(device_row.get("name") or device_row["device_id"]),
                dbc.ListGroup(metric_items, flush=True),
                dbc.CardFooter(f"Vu {_format_last_seen(last_seen)}", className="text-muted small"),
            ],
            className="h-100",
        ),
        md=4,
        className="pb-3",
    )


def _build_firmware_row(device_row):
    device_id = device_row["device_id"]
    ip_address = device_row.get("ip_address") or ""
    return dbc.Card(
        dbc.CardBody(
            [
                html.Div(device_row.get("name") or device_id, className="fw-bold mb-2"),
                dbc.Row(
                    [
                        dbc.Col(
                            dbc.InputGroup(
                                [
                                    dbc.InputGroupText("IP"),
                                    dbc.Input(
                                        id={"type": "device-ip-input", "device": device_id},
                                        value=ip_address,
                                        placeholder="192.168.1.50",
                                    ),
                                    dbc.Button(
                                        "Enregistrer",
                                        id={"type": "device-ip-save-btn", "device": device_id},
                                        color="secondary",
                                        n_clicks=0,
                                    ),
                                ]
                            ),
                            md=4,
                        ),
                        dbc.Col(
                            dcc.Upload(
                                id={"type": "ota-upload", "device": device_id},
                                children=html.Div("Glisser un .bin ou cliquer pour choisir", className="small"),
                                accept=".bin",
                                className="border rounded p-2 text-center",
                            ),
                            md=5,
                        ),
                        dbc.Col(
                            dbc.Button(
                                "Envoyer",
                                id={"type": "ota-send-btn", "device": device_id},
                                color="warning",
                                n_clicks=0,
                                className="w-100",
                            ),
                            md=3,
                        ),
                    ],
                    className="g-2 align-items-center",
                ),
            ]
        ),
        className="mb-3",
    )


_DAY_LABELS = {1: "Lun", 2: "Mar", 3: "Mer", 4: "Jeu", 5: "Ven", 6: "Sam", 7: "Dim"}


def _format_days(days_of_week):
    days_sorted = sorted(days_of_week or [])
    if days_sorted == list(range(1, 8)):
        return "Tous les jours"
    return ", ".join(_DAY_LABELS.get(d, "?") for d in days_sorted)


def _format_conditions_summary(conditions_list):
    labels = []
    for condition in conditions_list or []:
        condition_type = condition.get("type")
        if condition_type == "no_rain_forecast":
            labels.append(f"pas de pluie ({condition.get('window_hours', 3)}h)")
        elif condition_type == "min_tank_pct":
            labels.append(f"cuve ≥ {condition.get('min_pct')}%")
    return labels


def _valve_option_value(device_id, metric):
    return f"{device_id}::{metric}"


def _build_valve_options():
    return [
        {"label": f"{v['metric']} ({v['device_id']})", "value": _valve_option_value(v["device_id"], v["metric"])}
        for v in queries.get_known_valves()
    ]


def _build_program_card(program):
    program_id = program["id"]
    valve_labels = (
        ", ".join(f"{v['metric']} ({v['device_id']})" for v in program["valves"]) or "aucune vanne sélectionnée"
    )
    condition_badges = [
        dbc.Badge(label, color="info", className="me-1")
        for label in _format_conditions_summary(program["conditions"])
    ]
    duration_min = program["default_duration_s"] // 60
    schedule_summary = f"{_format_days(program['days_of_week'])} à {program['start_time'].strftime('%H:%M')} — {duration_min} min"

    return dbc.Card(
        dbc.CardBody(
            dbc.Row(
                [
                    dbc.Col(
                        [
                            html.Div(program["name"], className="fw-bold"),
                            html.Div(schedule_summary, className="small text-muted"),
                            html.Div(valve_labels, className="small"),
                            html.Div(condition_badges, className="mt-1"),
                        ],
                        md=8,
                    ),
                    dbc.Col(
                        [
                            dbc.Switch(
                                id={"type": "program-enabled-toggle", "program_id": program_id},
                                value=program["enabled"],
                                label="Actif",
                                className="mb-2",
                            ),
                            dbc.ButtonGroup(
                                [
                                    dbc.Button(
                                        "Modifier",
                                        id={"type": "program-edit-btn", "program_id": program_id},
                                        size="sm",
                                        color="secondary",
                                    ),
                                    dbc.Button(
                                        "Supprimer",
                                        id={"type": "program-delete-btn", "program_id": program_id},
                                        size="sm",
                                        color="danger",
                                        outline=True,
                                    ),
                                ]
                            ),
                        ],
                        md=4,
                        className="text-md-end",
                    ),
                ]
            )
        ),
        className="mb-2",
    )


def _format_valves_triggered(valves_triggered):
    if not valves_triggered:
        return ""
    return ", ".join(f"{v['metric']} ({v['duration_s'] // 60}min)" for v in valves_triggered)


def register_callbacks(app):
    @app.callback(
        Output("valve-command-feedback", "children"),
        Input({"type": "valve-open-btn", "device": ALL, "metric": ALL}, "n_clicks"),
        Input({"type": "valve-close-btn", "device": ALL, "metric": ALL}, "n_clicks"),
        State({"type": "valve-duration", "device": ALL, "metric": ALL}, "value"),
        prevent_initial_call=True,
    )
    def handle_valve_command(_open_clicks, _close_clicks, _durations):
        triggered_id = ctx.triggered_id
        if not triggered_id or not ctx.triggered or not ctx.triggered[0]["value"]:
            # Re-render périodique du panneau (nouveaux boutons créés) plutôt
            # qu'un vrai clic : n_clicks vaut None/0, on ignore.
            return no_update

        device_id = triggered_id["device"]
        metric = triggered_id["metric"]

        if triggered_id["type"] == "valve-open-btn":
            duration_min = None
            for state in ctx.states_list[0]:
                if state["id"]["device"] == device_id and state["id"]["metric"] == metric:
                    duration_min = state["value"]
                    break
            duration_s = int(duration_min) * 60 if duration_min else None
            ok = mqtt_control.send_valve_command(device_id, metric, "open", duration_s)
            if ok and duration_s:
                valve_timers.mark_opened(device_id, metric, duration_s)
            message = (
                f"Ouverture de {metric} ({device_id}) pour {duration_min} min envoyée."
                if ok
                else f"Échec de l'envoi de la commande d'ouverture pour {metric} ({device_id})."
            )
        else:
            ok = mqtt_control.send_valve_command(device_id, metric, "close")
            if ok:
                valve_timers.mark_closed(device_id, metric)
            message = (
                f"Fermeture de {metric} ({device_id}) envoyée."
                if ok
                else f"Échec de l'envoi de la commande de fermeture pour {metric} ({device_id})."
            )

        return dbc.Alert(
            message, color="success" if ok else "danger", dismissable=True, duration=6000, className="py-2"
        )

    @app.callback(
        Output("valve-panel", "children"),
        Input("tabs", "active_tab"),
        Input("valve-structure-interval", "n_intervals"),
    )
    def update_valve_panel(_active_tab, _n):
        valves = queries.get_valve_states()
        if valves.empty:
            return dbc.Alert(
                "Aucune vanne détectée pour le moment.", color="secondary", className="mb-0 py-2"
            )
        return dbc.Row([_build_valve_chip(row) for _, row in valves.iterrows()], className="g-2")

    @app.callback(
        Output({"type": "valve-badge-wrap", "device": ALL, "metric": ALL}, "children"),
        Output({"type": "valve-countdown-wrap", "device": ALL, "metric": ALL}, "children"),
        Output({"type": "valve-open-btn", "device": ALL, "metric": ALL}, "disabled"),
        Output({"type": "valve-close-btn", "device": ALL, "metric": ALL}, "disabled"),
        Input("global-interval", "n_intervals"),
    )
    def update_valve_badges(_n):
        valves = queries.get_valve_states()
        state_by_key = {
            (row["device_id"], row["metric"]): (row["value"], row.get("direction"))
            for _, row in valves.iterrows()
        }

        def ids_for(index):
            return [o["id"] for o in ctx.outputs_list[index]] if ctx.outputs_list[index] else []

        badge_ids = ids_for(0)
        countdown_ids = ids_for(1)
        open_btn_ids = ids_for(2)
        close_btn_ids = ids_for(3)

        def status_of(oid):
            value, direction = state_by_key.get((oid["device"], oid["metric"]), (None, None))
            status = _valve_status(value) if value is not None else "closed"
            return status, direction

        badges = []
        for oid in badge_ids:
            status, direction = status_of(oid)
            badges.append(_build_valve_badge(status, direction))

        countdowns = []
        for oid in countdown_ids:
            status, _direction = status_of(oid)
            countdowns.append(_build_valve_countdown(oid["device"], oid["metric"], status))

        # Boutons désactivés tant que la vanne est en transition (~15s) :
        # évite d'envoyer une commande contradictoire pendant qu'elle bouge
        # déjà. Ces props sont mises à jour en place (pas de reconstruction
        # des boutons), donc pas de risque de perdre un clic.
        open_disabled = [status_of(oid)[0] == "transition" for oid in open_btn_ids]
        close_disabled = [status_of(oid)[0] == "transition" for oid in close_btn_ids]

        return badges, countdowns, open_disabled, close_disabled

    @app.callback(Output("tank-gauge", "children"), Input("global-interval", "n_intervals"))
    def update_tank_gauge(_n):
        tank = queries.get_tank_level()
        if tank is None:
            return dbc.Alert(
                f"Pas de mesure de niveau d'eau ({config.TANK_LEVEL_METRIC}) reçue pour le moment.",
                color="secondary",
            )
        return dcc.Graph(figure=_build_tank_gauge_figure(tank), config={"displayModeBar": False})

    @app.callback(Output("rain-outlook", "children"), Input("global-interval", "n_intervals"))
    def update_rain_outlook(_n):
        outlook = queries.get_rain_outlook()
        if outlook is None:
            return dbc.Alert(
                "Prévisions de pluie indisponibles (service météo non configuré).", color="secondary"
            )
        return dbc.Row(
            [
                _rain_tile("Pluie dans les 3 prochaines heures", outlook["rain_3h_mm"]),
                _rain_tile("Pluie dans les 2 prochains jours", outlook["rain_48h_mm"]),
            ],
            className="g-2 h-100",
        )

    @app.callback(Output("overview-cards", "children"), Input("global-interval", "n_intervals"))
    def update_overview(_n):
        devices = queries.get_devices()
        if devices.empty:
            return dbc.Alert("Aucun appareil n'a encore envoyé de données.", color="info")

        readings = queries.get_latest_readings()
        cards = []
        for _, device_row in devices.iterrows():
            device_readings = readings[readings["device_id"] == device_row["device_id"]]
            cards.append(_build_device_card(device_row, device_readings))
        return dbc.Row(cards)

    @app.callback(
        Output("history-devices", "options"),
        Output("history-metrics", "options"),
        Output("history-date-range", "start_date"),
        Output("history-date-range", "end_date"),
        Input("tabs", "active_tab"),
    )
    def populate_history_filters(_active_tab):
        devices = queries.get_devices()
        device_options = [
            {"label": row.get("name") or row["device_id"], "value": row["device_id"]}
            for _, row in devices.iterrows()
        ]
        metric_options = [{"label": m, "value": m} for m in queries.get_known_metrics()]
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=2)
        return device_options, metric_options, start_date, end_date

    @app.callback(
        Output("history-graph", "figure"),
        Input("history-devices", "value"),
        Input("history-metrics", "value"),
        Input("history-date-range", "start_date"),
        Input("history-date-range", "end_date"),
    )
    def update_history_graph(devices, metrics, start_date, end_date):
        if not devices or not metrics or not start_date or not end_date:
            fig = go.Figure()
            fig.update_layout(title="Sélectionnez au moins un appareil et une métrique")
            return fig

        # DatePickerRange renvoie des chaînes ISO ("2026-07-14"), pas des
        # datetime : get_history fait de l'arithmétique dessus (end - start).
        start = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
        end = datetime.fromisoformat(end_date).replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        df = queries.get_history(devices, metrics, start, end)
        return _build_history_figure(df)

    @app.callback(Output("weather-content", "children"), Input("weather-interval", "n_intervals"))
    def update_weather(_n):
        forecast = queries.get_weather_forecast()
        observed = queries.get_weather_observed()

        if forecast.empty and observed.empty:
            return dbc.Alert(
                "Pas encore de données météo (le service se met à jour toutes les 3h).",
                color="warning",
            )

        children = []

        if not forecast.empty:
            fig = go.Figure()
            # Groupé par (source, métrique) : AROME et ARPEGE se chevauchent
            # sur les échéances proches, les mélanger dans une même trace
            # donnerait une courbe qui va et vient entre les deux valeurs.
            for (source, metric), group in forecast.groupby(["source", "metric"]):
                group = group.sort_values("valid_time")
                fig.add_trace(
                    go.Scatter(
                        x=group["valid_time"], y=group["value"], mode="lines", name=f"{source} · {metric}"
                    )
                )
            fig.update_layout(
                title="Prévisions (AROME / ARPEGE, via Open-Meteo)", margin=dict(l=40, r=20, t=40, b=40)
            )
            children.append(dcc.Graph(figure=fig))
        else:
            children.append(dbc.Alert("Pas encore de prévisions disponibles.", color="secondary"))

        if not observed.empty:
            rain = observed[observed["metric"] == "precipitation_mm"]
            fig2 = go.Figure()
            if not rain.empty:
                fig2.add_trace(go.Bar(x=rain["time"], y=rain["value"], name="Précipitations (mm)"))
            fig2.update_layout(title="Pluviométrie passée", margin=dict(l=40, r=20, t=40, b=40))
            children.append(dcc.Graph(figure=fig2))

        return children

    @app.callback(Output("firmware-rows", "children"), Input("tabs", "active_tab"))
    def update_firmware_rows(active_tab):
        if active_tab != "firmware":
            return no_update
        devices = queries.get_devices()
        if devices.empty:
            return dbc.Alert("Aucun appareil n'a encore envoyé de données.", color="info")
        return [_build_firmware_row(row) for _, row in devices.iterrows()]

    @app.callback(
        Output("ota-feedback", "children", allow_duplicate=True),
        Input({"type": "device-ip-save-btn", "device": ALL}, "n_clicks"),
        State({"type": "device-ip-input", "device": ALL}, "value"),
        prevent_initial_call=True,
    )
    def save_device_ip(_clicks, _values):
        triggered_id = ctx.triggered_id
        if not triggered_id or not ctx.triggered or not ctx.triggered[0]["value"]:
            return no_update

        device_id = triggered_id["device"]
        ip_value = None
        for state in ctx.states_list[0]:
            if state["id"]["device"] == device_id:
                ip_value = state["value"]
                break

        if not ota_client.is_valid_ip(ip_value or ""):
            return dbc.Alert(
                f"Adresse IP invalide pour {device_id}.", color="danger", dismissable=True, duration=6000
            )

        queries.set_device_ip(device_id, ip_value)
        return dbc.Alert(
            f"IP enregistrée pour {device_id} : {ip_value}", color="success", dismissable=True, duration=4000
        )

    @app.callback(
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Output("ota-modal-body", "children"),
        Output("ota-pending-upload", "data"),
        Output("ota-feedback", "children", allow_duplicate=True),
        Input({"type": "ota-send-btn", "device": ALL}, "n_clicks"),
        State({"type": "ota-upload", "device": ALL}, "contents"),
        State({"type": "ota-upload", "device": ALL}, "filename"),
        State({"type": "device-ip-input", "device": ALL}, "value"),
        prevent_initial_call=True,
    )
    def open_ota_confirm(_clicks, _contents_list, _filenames_list, _ips_list):
        triggered_id = ctx.triggered_id
        if not triggered_id or not ctx.triggered or not ctx.triggered[0]["value"]:
            return no_update, no_update, no_update, no_update

        device_id = triggered_id["device"]

        def _find(state_index):
            for state in ctx.states_list[state_index]:
                if state["id"]["device"] == device_id:
                    return state["value"]
            return None

        contents = _find(0)
        filename = _find(1)
        ip_address = _find(2)

        if not contents or not filename:
            return (
                no_update,
                no_update,
                no_update,
                dbc.Alert(
                    f"Sélectionnez d'abord un fichier .bin pour {device_id}.",
                    color="warning",
                    dismissable=True,
                    duration=6000,
                ),
            )
        if not ota_client.is_valid_ip(ip_address or ""):
            return (
                no_update,
                no_update,
                no_update,
                dbc.Alert(
                    f"Adresse IP manquante ou invalide pour {device_id}. Enregistrez-la d'abord.",
                    color="warning",
                    dismissable=True,
                    duration=6000,
                ),
            )

        body = html.Div(
            [
                html.P(f"Envoyer {filename} vers {device_id} ({ip_address}) ?"),
                html.P(
                    "Le device va redémarrer et appliquer le nouveau firmware. "
                    "Toute vanne actuellement ouverte sera fermée pendant le redémarrage.",
                    className="text-danger mb-0",
                ),
            ]
        )
        pending = {"device_id": device_id, "ip_address": ip_address, "filename": filename, "contents": contents}
        return True, body, pending, no_update

    @app.callback(
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Input("ota-cancel-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def cancel_ota(_n):
        return False

    @app.callback(
        Output("ota-feedback", "children", allow_duplicate=True),
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Output("ota-confirm-btn", "disabled"),
        Input("ota-confirm-btn", "n_clicks"),
        State("ota-pending-upload", "data"),
        prevent_initial_call=True,
    )
    def confirm_ota(_n, pending):
        if not pending:
            return no_update, False, False

        device_id = pending["device_id"]
        ip_address = pending["ip_address"]
        filename = pending["filename"]
        contents = pending["contents"]

        try:
            _header, b64data = contents.split(",", 1)
            data = base64.b64decode(b64data)
        except Exception:
            return (
                dbc.Alert(f"Fichier illisible pour {device_id}.", color="danger", dismissable=True, duration=6000),
                False,
                False,
            )

        ok, message = ota_client.send_firmware(device_id, ip_address, data)
        alert = dbc.Alert(
            f"{filename} → {device_id} ({ip_address}) : {message}",
            color="success" if ok else "danger",
            dismissable=True,
            duration=10000,
        )
        return alert, False, False

    @app.callback(
        Output("program-modal", "is_open", allow_duplicate=True),
        Output("program-modal-title", "children"),
        Output("program-editing-id", "data"),
        Output("program-form-name", "value"),
        Output("program-form-days", "value"),
        Output("program-form-start-time", "value"),
        Output("program-form-duration", "value"),
        Output("program-form-valves", "value"),
        Output("program-form-valves", "options"),
        Output("program-form-cond-rain-enabled", "value"),
        Output("program-form-cond-rain-hours", "value"),
        Output("program-form-cond-tank-enabled", "value"),
        Output("program-form-cond-tank-pct", "value"),
        Output("program-form-error", "children", allow_duplicate=True),
        Input("new-program-btn", "n_clicks"),
        Input({"type": "program-edit-btn", "program_id": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def open_program_modal(_new_clicks, _edit_clicks):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            raise PreventUpdate

        valve_options = _build_valve_options()
        no_valves_warning = (
            dbc.Alert(
                "Aucune vanne détectée pour l'instant : elle doit avoir déjà envoyé son état "
                "au moins une fois (sur arrosage/<device>/etat/<vanne>) pour apparaître ici.",
                color="warning",
                className="mb-3",
            )
            if not valve_options
            else None
        )
        triggered_id = ctx.triggered_id

        if triggered_id == "new-program-btn":
            return (
                True,
                "Nouveau programme",
                None,
                "",
                [1, 2, 3, 4, 5, 6, 7],
                "06:30",
                10,
                [],
                valve_options,
                True,
                3,
                True,
                10,
                no_valves_warning,
            )

        program = queries.get_program(triggered_id["program_id"])
        if program is None:
            raise PreventUpdate

        conditions_by_type = {c["type"]: c for c in (program["conditions"] or [])}
        rain = conditions_by_type.get("no_rain_forecast")
        tank = conditions_by_type.get("min_tank_pct")
        valve_values = [_valve_option_value(v["device_id"], v["metric"]) for v in program["valves"]]

        return (
            True,
            f"Modifier « {program['name']} »",
            program["id"],
            program["name"],
            program["days_of_week"],
            program["start_time"].strftime("%H:%M"),
            program["default_duration_s"] // 60,
            valve_values,
            valve_options,
            rain is not None,
            rain.get("window_hours", 3) if rain else 3,
            tank is not None,
            tank.get("min_pct", 10) if tank else 10,
            no_valves_warning,
        )

    @app.callback(
        Output("program-modal", "is_open", allow_duplicate=True),
        Input("program-cancel-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def cancel_program_modal(_n):
        return False

    @app.callback(
        Output("program-modal", "is_open", allow_duplicate=True),
        Output("programs-version", "data", allow_duplicate=True),
        Output("programs-feedback", "children", allow_duplicate=True),
        Output("program-form-error", "children", allow_duplicate=True),
        Input("program-save-btn", "n_clicks"),
        State("program-editing-id", "data"),
        State("program-form-name", "value"),
        State("program-form-days", "value"),
        State("program-form-start-time", "value"),
        State("program-form-duration", "value"),
        State("program-form-valves", "value"),
        State("program-form-cond-rain-enabled", "value"),
        State("program-form-cond-rain-hours", "value"),
        State("program-form-cond-tank-enabled", "value"),
        State("program-form-cond-tank-pct", "value"),
        State("programs-version", "data"),
        prevent_initial_call=True,
    )
    def save_program(
        n_clicks,
        program_id,
        name,
        days,
        start_time_str,
        duration_min,
        valve_values,
        rain_enabled,
        rain_hours,
        tank_enabled,
        tank_pct,
        version,
    ):
        if not n_clicks:
            raise PreventUpdate

        errors = []
        if not name or not name.strip():
            errors.append("le nom est obligatoire")
        if not days:
            errors.append("sélectionnez au moins un jour")
        if not valve_values:
            errors.append("sélectionnez au moins une vanne")
        if not start_time_str:
            errors.append("l'heure de déclenchement est obligatoire")
        if not duration_min or duration_min <= 0:
            errors.append("la durée doit être positive")

        if errors:
            # A l'intérieur de la modale (pas dans programs-feedback, qui est
            # caché derrière tant que la modale reste ouverte) : régression
            # trouvée en test, l'erreur était invisible et "Enregistrer"
            # semblait ne rien faire.
            return (
                no_update,
                no_update,
                no_update,
                dbc.Alert("Erreur : " + ", ".join(errors), color="danger", dismissable=True, className="mb-3"),
            )

        conditions_list = []
        if rain_enabled:
            conditions_list.append(
                {"type": "no_rain_forecast", "window_hours": int(rain_hours or 3), "threshold_mm": 0.2}
            )
        if tank_enabled:
            conditions_list.append({"type": "min_tank_pct", "min_pct": int(tank_pct or 0)})

        valves = [tuple(v.split("::", 1)) for v in valve_values]
        hour, minute = (int(part) for part in start_time_str.split(":")[:2])

        existing_enabled = True
        if program_id is not None:
            existing = queries.get_program(program_id)
            if existing is not None:
                existing_enabled = existing["enabled"]

        queries.save_program(
            program_id=program_id,
            name=name.strip(),
            enabled=existing_enabled,
            start_time=time_type(hour, minute),
            days_of_week=[int(d) for d in days],
            default_duration_s=int(duration_min) * 60,
            conditions_list=conditions_list,
            valves=valves,
        )

        return (
            False,
            (version or 0) + 1,
            dbc.Alert("Programme enregistré.", color="success", dismissable=True, duration=4000),
            None,
        )

    @app.callback(
        Output("programs-version", "data", allow_duplicate=True),
        Input({"type": "program-enabled-toggle", "program_id": ALL}, "value"),
        State("programs-version", "data"),
        prevent_initial_call=True,
    )
    def toggle_program_enabled(_values, version):
        triggered_id = ctx.triggered_id
        if not triggered_id or not ctx.triggered:
            raise PreventUpdate
        queries.set_program_enabled(triggered_id["program_id"], bool(ctx.triggered[0]["value"]))
        return (version or 0) + 1

    @app.callback(
        Output("programs-version", "data", allow_duplicate=True),
        Output("programs-feedback", "children", allow_duplicate=True),
        Input({"type": "program-delete-btn", "program_id": ALL}, "n_clicks"),
        State("programs-version", "data"),
        prevent_initial_call=True,
    )
    def delete_program(_clicks, version):
        triggered_id = ctx.triggered_id
        if not triggered_id or not ctx.triggered or not ctx.triggered[0]["value"]:
            raise PreventUpdate
        queries.delete_program(triggered_id["program_id"])
        return (version or 0) + 1, dbc.Alert(
            "Programme supprimé.", color="secondary", dismissable=True, duration=4000
        )

    @app.callback(
        Output("programs-list", "children"),
        Input("tabs", "active_tab"),
        Input("programs-version", "data"),
    )
    def update_programs_list(active_tab, _version):
        if active_tab != "programs":
            return no_update
        programs = queries.get_programs()
        if not programs:
            return dbc.Alert("Aucun programme pour l'instant.", color="secondary")
        return [_build_program_card(program) for program in programs]

    @app.callback(
        Output("watering-runs-history", "children"),
        Input("tabs", "active_tab"),
        Input("programs-version", "data"),
        Input("global-interval", "n_intervals"),
    )
    def update_runs_history(active_tab, _version, _n):
        if active_tab != "programs":
            return no_update
        runs = queries.get_recent_runs(limit=20)
        if runs.empty:
            return dbc.Alert("Aucune exécution pour l'instant.", color="secondary")

        rows = []
        for _, run in runs.iterrows():
            executed = run["status"] == "executed"
            status_badge = dbc.Badge(
                "Exécuté" if executed else "Ignoré", color="success" if executed else "secondary"
            )
            detail = _format_valves_triggered(run["valves_triggered"]) if executed else (run["skip_reason"] or "")
            rows.append(
                html.Tr(
                    [
                        html.Td(run["scheduled_for"].strftime("%d/%m %H:%M")),
                        html.Td(run["program_name"]),
                        html.Td(status_badge),
                        html.Td(detail),
                    ]
                )
            )

        return dbc.Table(
            [
                html.Thead(html.Tr([html.Th("Prévu"), html.Th("Programme"), html.Th("Statut"), html.Th("Détail")])),
                html.Tbody(rows),
            ],
            size="sm",
            hover=True,
        )
