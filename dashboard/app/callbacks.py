import base64
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from plotly.subplots import make_subplots
import dash_bootstrap_components as dbc

from . import config, mqtt_control, ota_client, queries

_VALVE_OPEN_COLOR = "#28a745"
_VALVE_CLOSED_COLOR = "#dc3545"


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


def _build_valve_chip(row):
    is_open = row["value"] >= 0.5
    device_id = row["device_id"]
    metric = row["metric"]
    return dbc.Col(
        dbc.Card(
            dbc.CardBody(
                [
                    html.Div(metric, className="fw-bold small"),
                    html.Div(device_id, className="text-muted small"),
                    dbc.Badge(
                        "OUVERTE" if is_open else "FERMÉE",
                        color="success" if is_open else "secondary",
                        className="mt-1 mb-2 d-block",
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
                            ),
                            dbc.Button(
                                "Fermer",
                                id={"type": "valve-close-btn", "device": device_id, "metric": metric},
                                color="secondary",
                                size="sm",
                                n_clicks=0,
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
            colors = [_VALVE_OPEN_COLOR if v >= 0.5 else _VALVE_CLOSED_COLOR for v in group["value"]]
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
            is_open = reading["value"] >= 0.5
            metric_items.append(
                dbc.ListGroupItem(
                    [
                        html.Span(reading["metric"], className="me-2"),
                        dbc.Badge(
                            "ouverte" if is_open else "fermée",
                            color="success" if is_open else "secondary",
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
            message = (
                f"Ouverture de {metric} ({device_id}) pour {duration_min} min envoyée."
                if ok
                else f"Échec de l'envoi de la commande d'ouverture pour {metric} ({device_id})."
            )
        else:
            ok = mqtt_control.send_valve_command(device_id, metric, "close")
            message = (
                f"Fermeture de {metric} ({device_id}) envoyée."
                if ok
                else f"Échec de l'envoi de la commande de fermeture pour {metric} ({device_id})."
            )

        return dbc.Alert(
            message, color="success" if ok else "danger", dismissable=True, duration=6000, className="py-2"
        )

    @app.callback(Output("valve-panel", "children"), Input("global-interval", "n_intervals"))
    def update_valve_panel(_n):
        valves = queries.get_valve_states()
        if valves.empty:
            return dbc.Alert(
                "Aucune vanne détectée pour le moment.", color="secondary", className="mb-0 py-2"
            )
        return dbc.Row([_build_valve_chip(row) for _, row in valves.iterrows()], className="g-2")

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
                "Service météo non configuré ou pas encore de données "
                "(vérifiez METEOFRANCE_API_KEY dans .env).",
                color="warning",
            )

        children = []

        if not forecast.empty:
            fig = go.Figure()
            for metric, group in forecast.groupby("metric"):
                fig.add_trace(
                    go.Scatter(x=group["valid_time"], y=group["value"], mode="lines", name=metric)
                )
            fig.update_layout(
                title="Prévisions (AROME / ARPEGE)", margin=dict(l=40, r=20, t=40, b=40)
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

    app.clientside_callback(
        "function(n_clicks) { return n_clicks ? true : window.dash_clientside.no_update; }",
        Output("ota-confirm-btn", "disabled", allow_duplicate=True),
        Input("ota-confirm-btn", "n_clicks"),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("ota-feedback", "children", allow_duplicate=True),
        Output("ota-confirm-modal", "is_open", allow_duplicate=True),
        Output("ota-confirm-btn", "disabled", allow_duplicate=True),
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
