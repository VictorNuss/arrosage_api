"""Onglet Potager : une carte par zone (vanne + capteurs de sol + culture)
et la bibliothèque de profils de culture. Logique dans garden.py."""

from datetime import datetime, timedelta, timezone

import dash_bootstrap_components as dbc
import pandas as pd
from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate
from sqlalchemy.exc import IntegrityError

from . import config, garden, queries
from .formatting import format_last_seen, metric_label
from .history_tab import format_duration, watering_intervals

_STATUS_COLORS = {"dry": "danger", "ok": "success", "wet": "info", "unknown": "secondary"}
_WATERING_STYLES = {"frequent": "régulier, sol toujours frais", "deep": "profond et espacé"}
# Dernier arrosage recherché sur cette fenêtre (journal des vannes).
_LAST_WATERING_LOOKBACK = timedelta(days=14)


# --- Valeurs d'options -------------------------------------------------------------

def valve_value(device_id, metric):
    return f"{device_id}::{metric}" if device_id and metric else None


def parse_valve_value(value):
    return tuple(value.split("::", 1)) if value else None


def _valve_label(device_id, metric, device_names):
    return f"{metric_label(metric)} · {device_names.get(device_id, device_id)}"


# --- Rendu d'une zone -------------------------------------------------------------------

def build_moisture_gauge(moisture, crop, status):
    """Piste 0-100 % : bande verte = plage visée par la culture, repère =
    humidité actuelle de la zone."""
    children = []
    if crop is not None:
        children.append(
            html.Div(
                className="moisture-target",
                style={"left": f"{crop['moisture_min']}%", "width": f"{crop['moisture_max'] - crop['moisture_min']}%"},
            )
        )
    if moisture is not None:
        children.append(
            html.Div(className=f"moisture-marker bg-{_STATUS_COLORS[status]}",
                     style={"left": f"{min(100, max(0, moisture))}%"})
        )
    return html.Div(children, className="moisture-track")


def _metric_chip(icon, label, value):
    return html.Div([html.I(className=f"bi {icon} me-1 text-primary"), html.Span(label, className="text-muted me-1"),
                     html.Span(value, className="fw-semibold")], className="garden-chip")


def build_zone_card(zone, data):
    """data : dict préparé par collect_zone_data (dernières valeurs, noms,
    vannes, ensoleillement...)."""
    crop = zone.get("crop")
    latest = data["latest"]
    moisture = garden.zone_moisture(zone, latest)
    status = garden.moisture_status(moisture, crop)
    calibrated = zone.get("moisture_dry_ref") is not None and zone.get("moisture_wet_ref") is not None

    moisture_text = "–" if moisture is None else f"{moisture:.0f} %"
    target_text = f" (cible {crop['moisture_min']}–{crop['moisture_max']} %)" if crop else ""
    body = [
        html.Div(
            [
                html.Span([html.I(className="bi bi-moisture me-1"), "Humidité du sol "], className="text-muted"),
                html.Span(moisture_text, className="fw-bold fs-5 me-1"),
                html.Span(target_text, className="small text-muted"),
            ]
        ),
        build_moisture_gauge(moisture, crop, status),
        dbc.Badge(garden.STATUS_LABELS[status], color=_STATUS_COLORS[status], className="mb-2"),
    ]

    chips = []
    temperature = garden.zone_metric(zone, latest, garden.TEMPERATURE)
    if temperature is not None:
        chips.append(_metric_chip("bi-thermometer-half", "Temp.", f"{temperature:.1f} °C"))
    air = garden.zone_metric(zone, latest, garden.AIR_HUMIDITY)
    if air is not None:
        chips.append(_metric_chip("bi-cloud", "Air", f"{air:.0f} %"))
    sun = data["sun_hours"].get(zone["id"])
    if sun is not None:
        wanted = f" / {crop['sun_hours_min']} h" if crop else ""
        chips.append(_metric_chip("bi-sun", "Soleil auj.", f"{sun:.1f} h{wanted}"))
    fertility = garden.zone_metric(zone, latest, garden.FERTILITY)
    if fertility is not None:
        chips.append(_metric_chip("bi-flower1", "Fertilité", f"{fertility:.0f} µS/cm"))
    if chips:
        body.append(html.Div(chips, className="garden-chips"))

    body.append(html.Div(build_valve_line(zone, data), className="small mt-2"))

    alerts = garden.zone_alerts(zone, latest, data["times"], data["recent"].get(zone["id"], pd.DataFrame()),
                                device_names=data["names"])
    for color, message in alerts:
        body.append(dbc.Alert(message, color=color, className="py-1 px-2 mb-1 mt-2 small"))

    sensors = ", ".join(data["names"].get(d, d) for d in zone["sensors"]) or "aucun capteur"
    footer = [html.I(className="bi bi-broadcast me-1"), sensors]
    if zone["sensors"] and not calibrated:
        footer.append(html.Span(" · non calibrée", className="text-warning"))

    return dbc.Col(
        dbc.Card(
            [
                dbc.CardHeader(
                    [
                        html.Span([html.I(className="bi bi-flower2 me-2 text-success"), zone["name"]]),
                        html.Span(
                            [
                                dbc.Badge(crop["name"] if crop else "culture non définie",
                                          color="light", text_color="dark", className="border me-2"),
                                dbc.Button(html.I(className="bi bi-pencil"), id={"type": "garden-edit-btn", "zone_id": zone["id"]},
                                           size="sm", color="link", className="p-0", title="Modifier la zone"),
                            ]
                        ),
                    ],
                    className="fw-semibold d-flex justify-content-between align-items-center",
                ),
                dbc.CardBody(body),
                dbc.CardFooter(footer, className="small text-muted"),
            ],
            className="h-100",
        ),
        lg=6,
        className="mb-3",
    )


def build_valve_line(zone, data):
    if not zone.get("valve_device_id"):
        return html.Span("Aucune vanne associée.", className="text-muted")
    key = (zone["valve_device_id"], zone["valve_metric"])
    label = _valve_label(*key, data["names"])
    state = data["valve_states"].get(key)
    state_text = {"open": "ouverte", "closed": "fermée"}.get(state, "en transition" if state else "état inconnu")
    last = data["last_watering"].get(key)
    if last is None:
        last_text = "pas d'arrosage depuis 14 jours"
    else:
        start, duration_s = last
        last_text = f"dernier arrosage {format_last_seen(start)} ({format_duration(duration_s)})"
    return [html.I(className="bi bi-droplet me-1 text-primary"), html.Span(label, className="fw-semibold"),
            f" · {state_text} · {last_text}"]


def build_crops_table(profiles):
    header = html.Thead(html.Tr([html.Th("Culture"), html.Th("Humidité visée"), html.Th("Arrosage"),
                                 html.Th("Soleil"), html.Th("Sensible à"), html.Th("Conseil")]))
    rows = []
    for profile in profiles:
        sensitivities = [label for flag, label in [(profile["frost_sensitive"], "gel"),
                                                    (profile["mildew_sensitive"], "mildiou")] if flag]
        rows.append(
            html.Tr(
                [
                    html.Td(profile["name"], className="fw-semibold"),
                    html.Td(f"{profile['moisture_min']}–{profile['moisture_max']} %"),
                    html.Td(_WATERING_STYLES.get(profile["watering_style"], profile["watering_style"])),
                    html.Td(f"≥ {profile['sun_hours_min']} h"),
                    html.Td(", ".join(sensitivities) or "–"),
                    html.Td(profile.get("notes") or "", className="small text-muted"),
                ]
            )
        )
    return dbc.Table([header, html.Tbody(rows)], hover=True, responsive=True, size="sm", className="mb-0 align-middle")


# --- Données ------------------------------------------------------------------------------

def collect_zone_data(zones, now=None):
    """Toutes les données nécessaires aux cartes, en un minimum de requêtes."""
    now = now or datetime.now(timezone.utc)
    latest_df = queries.get_latest_readings()
    latest, times = garden.latest_by_device(latest_df)
    names = {row["device_id"]: row.get("name") or row["device_id"] for _, row in queries.get_devices().iterrows()}

    valve_states = {}
    for row in queries.get_valve_states().itertuples():
        value = row.value
        valve_states[(row.device_id, row.metric)] = "open" if value >= 0.75 else "closed" if value <= 0.25 else "transition"

    valve_devices = sorted({z["valve_device_id"] for z in zones if z.get("valve_device_id")})
    last_watering = {}
    if valve_devices:
        start = now - _LAST_WATERING_LOOKBACK
        intervals = watering_intervals(queries.get_valve_history(valve_devices, start, now), start, now)
        for row in intervals.sort_values("start").itertuples():
            last_watering[(row.device_id, row.metric)] = (row.start, (row.end - row.start).total_seconds())

    sensor_ids = sorted({d for z in zones for d in z["sensors"]})
    sun_hours, recent = {}, {}
    if sensor_ids:
        midnight = pd.Timestamp(now).tz_convert(config.LOCAL_TZ).normalize().tz_convert("UTC").to_pydatetime()
        lux = queries.get_history(sensor_ids, [garden.ILLUMINANCE], midnight, now)
        history = queries.get_history(sensor_ids, [garden.AIR_HUMIDITY, garden.TEMPERATURE],
                                      now - garden.MILDEW_WINDOW, now)
        for zone in zones:
            zone_lux = lux[lux["device_id"].isin(zone["sensors"])]
            sun_hours[zone["id"]] = garden.sun_hours(zone_lux)
            recent[zone["id"]] = history[history["device_id"].isin(zone["sensors"])]

    return {"latest": latest, "times": times, "names": names, "valve_states": valve_states,
            "last_watering": last_watering, "sun_hours": sun_hours, "recent": recent}


# --- Mise en page ---------------------------------------------------------------------------

def build_garden_tab():
    return html.Div(
        [
            dcc.Store(id="garden-version", data=0),
            dcc.Store(id="garden-editing-id"),
            dcc.Interval(id="garden-interval", interval=60_000),
            html.Div(id="garden-feedback"),
            html.Div(
                [
                    html.Div([html.I(className="bi bi-flower2 me-1"), "Zones"], className="section-title mb-0"),
                    dbc.Button([html.I(className="bi bi-plus-lg me-1"), "Nouvelle zone"], id="garden-new-btn",
                               color="primary", size="sm", n_clicks=0),
                ],
                className="d-flex justify-content-between align-items-center mb-2",
            ),
            dcc.Loading(html.Div(id="garden-zones")),
            html.Div([html.I(className="bi bi-book me-1"), "Bibliothèque de cultures"], className="section-title mt-2"),
            dbc.Card(html.Div(id="garden-crops"), className="mb-3"),
            build_zone_modal(),
        ],
        className="pt-3",
    )


def build_zone_modal():
    return dbc.Modal(
        [
            dbc.ModalHeader(id="garden-modal-title"),
            dbc.ModalBody(
                [
                    html.Div(id="garden-form-error"),
                    dbc.Label("Nom de la zone"),
                    dbc.Input(id="garden-form-name", placeholder="Ex : Zone 1 - Tomates"),
                    dbc.Label("Vanne qui l'arrose", className="mt-3"),
                    dcc.Dropdown(id="garden-form-valve", placeholder="Choisir une vanne…"),
                    dbc.Label("Capteurs de sol", className="mt-3"),
                    dcc.Dropdown(id="garden-form-sensors", multi=True, placeholder="Capteurs plantés dans la zone…"),
                    dbc.Label("Culture", className="mt-3"),
                    dcc.Dropdown(id="garden-form-crop", placeholder="Choisir une culture…"),
                    html.Div(id="garden-form-crop-help", className="form-text"),
                    html.Hr(),
                    html.Div("Calibration de l'humidité (facultatif)", className="fw-bold"),
                    html.Div(
                        "Relever la valeur du capteur un jour où le sol est sec au toucher, puis ~1 h après un "
                        "gros arrosage : l'humidité de la zone s'affiche ensuite sur cette échelle (0 = sec, "
                        "100 = gorgé d'eau), celle des profils de culture.",
                        className="form-text mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(_calibration_input("Sol sec (valeur brute)", "garden-form-dry", "garden-dry-now"), md=6),
                            dbc.Col(_calibration_input("Sol gorgé d'eau (valeur brute)", "garden-form-wet", "garden-wet-now"), md=6),
                        ],
                        className="g-3",
                    ),
                    dbc.Label("Notes", className="mt-3"),
                    dbc.Textarea(id="garden-form-notes", placeholder="Variétés, date de plantation…"),
                ]
            ),
            dbc.ModalFooter(
                [
                    dbc.Button("Supprimer", id="garden-delete-btn", color="danger", outline=True, className="me-auto"),
                    dbc.Button("Annuler", id="garden-cancel-btn", color="secondary"),
                    dbc.Button("Enregistrer", id="garden-save-btn", color="primary"),
                ]
            ),
        ],
        id="garden-modal",
        is_open=False,
        size="lg",
    )


def _calibration_input(label, input_id, button_id):
    return [
        dbc.Label(label, size="sm"),
        dbc.InputGroup(
            [
                dbc.Input(id=input_id, type="number", step=0.1, size="sm"),
                dbc.Button("Valeur actuelle", id=button_id, color="secondary", outline=True, size="sm", n_clicks=0),
            ],
            size="sm",
        ),
    ]


def _parse_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_zone_form(name, dry, wet):
    """Message d'erreur, ou None si le formulaire est valide."""
    if not (name or "").strip():
        return "Donnez un nom à la zone."
    if (dry is None) != (wet is None):
        return "Renseignez les deux valeurs de calibration (sol sec et sol gorgé d'eau), ou aucune."
    if dry is not None and dry >= wet:
        return "La valeur « sol sec » doit être inférieure à la valeur « sol gorgé d'eau »."
    return None


# --- Callbacks --------------------------------------------------------------------------------

def register_callbacks(app):
    @app.callback(
        Output("garden-zones", "children"),
        Output("garden-crops", "children"),
        Input("tabs", "active_tab"),
        Input("garden-interval", "n_intervals"),
        Input("garden-version", "data"),
    )
    def update_garden(active_tab, _n, _version):
        if active_tab != "garden":
            return no_update, no_update
        zones = queries.get_zones()
        crops = build_crops_table(queries.get_crop_profiles())
        if not zones:
            return dbc.Alert(
                "Aucune zone pour l'instant : créez-en une par vanne (« Nouvelle zone »), associez-lui ses "
                "capteurs de sol et sa culture.",
                color="info",
            ), crops
        data = collect_zone_data(zones)
        return dbc.Row([build_zone_card(zone, data) for zone in zones], className="g-3"), crops

    @app.callback(
        Output("garden-modal", "is_open", allow_duplicate=True),
        Output("garden-modal-title", "children"),
        Output("garden-editing-id", "data"),
        Output("garden-form-name", "value"),
        Output("garden-form-valve", "options"),
        Output("garden-form-valve", "value"),
        Output("garden-form-sensors", "options"),
        Output("garden-form-sensors", "value"),
        Output("garden-form-crop", "options"),
        Output("garden-form-crop", "value"),
        Output("garden-form-dry", "value"),
        Output("garden-form-wet", "value"),
        Output("garden-form-notes", "value"),
        Output("garden-delete-btn", "style"),
        Output("garden-form-error", "children", allow_duplicate=True),
        Input("garden-new-btn", "n_clicks"),
        Input({"type": "garden-edit-btn", "zone_id": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def open_zone_modal(_new, _edit):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            raise PreventUpdate
        names = {row["device_id"]: row.get("name") or row["device_id"] for _, row in queries.get_devices().iterrows()}
        valve_options = [{"label": _valve_label(v["device_id"], v["metric"], names),
                          "value": valve_value(v["device_id"], v["metric"])} for v in queries.get_known_valves()]
        sensor_options = [{"label": s["name"], "value": s["device_id"]} for s in queries.get_soil_sensors()]
        crop_options = [{"label": p["name"], "value": p["id"]} for p in queries.get_crop_profiles()]

        if ctx.triggered_id == "garden-new-btn":
            return (True, "Nouvelle zone", None, "", valve_options, None, sensor_options, [], crop_options, None,
                    None, None, "", {"display": "none"}, None)

        zone = queries.get_zone(ctx.triggered_id["zone_id"])
        if zone is None:
            raise PreventUpdate
        return (True, f"Modifier « {zone['name']} »", zone["id"], zone["name"],
                valve_options, valve_value(zone["valve_device_id"], zone["valve_metric"]),
                sensor_options, zone["sensors"], crop_options, zone["crop_profile_id"],
                zone["moisture_dry_ref"], zone["moisture_wet_ref"], zone["notes"] or "", {}, None)

    @app.callback(
        Output("garden-form-crop-help", "children"),
        Input("garden-form-crop", "value"),
    )
    def show_crop_help(crop_id):
        profile = next((p for p in queries.get_crop_profiles() if p["id"] == crop_id), None) if crop_id else None
        if profile is None:
            return None
        return (f"Humidité visée {profile['moisture_min']}–{profile['moisture_max']} %, arrosage "
                f"{_WATERING_STYLES.get(profile['watering_style'], '')}, ≥ {profile['sun_hours_min']} h de soleil. "
                f"{profile.get('notes') or ''}")

    @app.callback(
        Output("garden-form-dry", "value", allow_duplicate=True),
        Output("garden-form-wet", "value", allow_duplicate=True),
        Output("garden-form-error", "children", allow_duplicate=True),
        Input("garden-dry-now", "n_clicks"),
        Input("garden-wet-now", "n_clicks"),
        State("garden-form-sensors", "value"),
        prevent_initial_call=True,
    )
    def calibrate_now(_dry, _wet, sensors):
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            raise PreventUpdate
        latest, _times = garden.latest_by_device(queries.get_latest_readings())
        value = garden.raw_moisture(sensors or [], latest)
        if value is None:
            return no_update, no_update, dbc.Alert(
                "Aucune humidité actuelle pour les capteurs choisis.", color="warning", className="py-2")
        if ctx.triggered_id == "garden-dry-now":
            return value, no_update, None
        return no_update, value, None

    @app.callback(
        Output("garden-modal", "is_open", allow_duplicate=True),
        Input("garden-cancel-btn", "n_clicks"),
        prevent_initial_call=True,
    )
    def cancel_zone_modal(_n):
        return False

    @app.callback(
        Output("garden-modal", "is_open", allow_duplicate=True),
        Output("garden-version", "data", allow_duplicate=True),
        Output("garden-form-error", "children", allow_duplicate=True),
        Output("garden-feedback", "children", allow_duplicate=True),
        Input("garden-save-btn", "n_clicks"),
        State("garden-editing-id", "data"),
        State("garden-form-name", "value"),
        State("garden-form-valve", "value"),
        State("garden-form-sensors", "value"),
        State("garden-form-crop", "value"),
        State("garden-form-dry", "value"),
        State("garden-form-wet", "value"),
        State("garden-form-notes", "value"),
        State("garden-version", "data"),
        prevent_initial_call=True,
    )
    def save_zone(_n, zone_id, name, valve, sensors, crop_id, dry, wet, notes, version):
        dry, wet = _parse_float(dry), _parse_float(wet)
        error = validate_zone_form(name, dry, wet)
        if error:
            return no_update, no_update, dbc.Alert(error, color="danger", className="py-2"), no_update
        try:
            queries.save_zone(zone_id, name.strip(), parse_valve_value(valve), sensors or [], crop_id, dry, wet,
                              (notes or "").strip() or None)
        except IntegrityError:
            return no_update, no_update, dbc.Alert(
                "Cette vanne arrose déjà une autre zone.", color="danger", className="py-2"), no_update
        feedback = dbc.Alert(f"Zone « {name.strip()} » enregistrée.", color="success", dismissable=True, duration=4000)
        return False, (version or 0) + 1, None, feedback

    @app.callback(
        Output("garden-modal", "is_open", allow_duplicate=True),
        Output("garden-version", "data", allow_duplicate=True),
        Output("garden-feedback", "children", allow_duplicate=True),
        Input("garden-delete-btn", "n_clicks"),
        State("garden-editing-id", "data"),
        State("garden-version", "data"),
        prevent_initial_call=True,
    )
    def delete_zone(_n, zone_id, version):
        if zone_id is None:
            raise PreventUpdate
        queries.delete_zone(zone_id)
        feedback = dbc.Alert("Zone supprimée.", color="secondary", dismissable=True, duration=4000)
        return False, (version or 0) + 1, feedback
