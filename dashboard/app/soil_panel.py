"""Panneau "Humidité du sol" de la vue d'ensemble : une ligne par zone du
Potager, avec la même valeur calibrée et la même cible de culture que
l'onglet Potager (garden.py), pour ne jamais montrer deux chiffres pour une
même planche. Un capteur de sol rattaché à aucune zone garde sa ligne, en
valeur brute."""

from datetime import datetime, timedelta, timezone

import dash_bootstrap_components as dbc
import pandas as pd
from dash import html

from . import config, garden
from .formatting import format_last_seen
from .garden_tab import STATUS_COLORS, build_moisture_gauge


def soil_sensor_rows(devices_df: pd.DataFrame, readings_df: pd.DataFrame) -> list[dict]:
    """Une entrée par appareil ayant une humidité du sol connue :
    {device_id, name, moisture_pct, battery_pct (ou None), time}, triées par
    nom. readings_df : dernières valeurs (queries.get_latest_readings())."""
    if readings_df.empty:
        return []
    names = {}
    if not devices_df.empty:
        names = {row["device_id"]: row.get("name") or row["device_id"] for _, row in devices_df.iterrows()}

    latest = readings_df.copy()
    latest["time"] = pd.to_datetime(latest["time"], utc=True)
    # Le cache live et la base peuvent tous deux fournir une ligne pour la
    # même clé : on garde la plus récente.
    latest = latest.sort_values("time").drop_duplicates(subset=["device_id", "metric"], keep="last")
    by_key = {(row.device_id, row.metric): row for row in latest.itertuples()}

    rows = []
    for (device_id, metric), reading in by_key.items():
        if metric != config.SOIL_MOISTURE_METRIC:
            continue
        battery = by_key.get((device_id, config.SOIL_BATTERY_METRIC))
        rows.append(
            {
                "device_id": device_id,
                "name": names.get(device_id, device_id),
                "moisture_pct": float(reading.value),
                "battery_pct": float(battery.value) if battery is not None else None,
                "time": reading.time,
            }
        )
    return sorted(rows, key=lambda r: (r["name"], r["device_id"]))


def soil_rows(zones: list[dict], devices_df: pd.DataFrame, readings_df: pd.DataFrame) -> list[dict]:
    """Zones ayant au moins un capteur, puis capteurs de sol hors zone.
    Chaque entrée : {kind ("zone"/"sensor"), key, name, crop, moisture_pct,
    status, battery_pct (la plus faible, ou None), time (mesure d'humidité la
    plus récente, ou None)}."""
    sensors = soil_sensor_rows(devices_df, readings_df)
    by_device = {r["device_id"]: r for r in sensors}
    latest, _times = garden.latest_by_device(readings_df)

    rows = []
    zoned = set()
    for zone in zones:
        if not zone["sensors"]:
            continue
        zoned.update(zone["sensors"])
        own = [by_device[d] for d in zone["sensors"] if d in by_device]
        moisture = garden.zone_moisture(zone, latest)
        batteries = [r["battery_pct"] for r in own if r["battery_pct"] is not None]
        rows.append(
            {
                "kind": "zone",
                "key": f"zone-{zone['id']}",
                "name": zone["name"],
                "crop": zone.get("crop"),
                "moisture_pct": moisture,
                "status": garden.moisture_status(moisture, zone.get("crop")),
                "battery_pct": min(batteries) if batteries else None,
                "time": max((r["time"] for r in own), default=None),
            }
        )
    for r in sensors:
        if r["device_id"] in zoned:
            continue
        rows.append(
            {
                "kind": "sensor",
                "key": r["device_id"],
                "name": r["name"],
                "crop": None,
                "moisture_pct": r["moisture_pct"],
                "status": None,
                "battery_pct": r["battery_pct"],
                "time": r["time"],
            }
        )
    return rows


def moisture_color(moisture_pct: float) -> str:
    if moisture_pct < config.SOIL_DRY_PCT:
        return "warning"
    if moisture_pct > config.SOIL_WET_PCT:
        return "info"
    return "success"


def is_stale(ts, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    return ts is None or pd.isna(ts) or now - pd.Timestamp(ts).to_pydatetime() > timedelta(hours=config.SOIL_STALE_HOURS)


def _build_row(row):
    stale = is_stale(row["time"])
    footer = [html.I(className="bi bi-clock me-1"), f"Vu {format_last_seen(row['time'])}"]
    if stale:
        footer = [html.I(className="bi bi-exclamation-triangle me-1 text-warning"), *footer[1:]]

    badges = []
    battery = row["battery_pct"]
    if battery is not None and battery < config.LOW_BATTERY_PCT:
        badges.append(
            dbc.Badge([html.I(className="bi bi-battery me-1"), f"{battery:.0f} %"], color="danger", className="ms-2")
        )

    moisture = row["moisture_pct"]
    value = "–" if moisture is None else f"{moisture:.0f} %"
    if row["kind"] == "zone":
        icon = "bi-flower2 text-success"
        crop = row["crop"]
        status = row["status"]
        gauge = build_moisture_gauge(moisture, crop, status)
        detail = [dbc.Badge(garden.STATUS_LABELS[status], color=STATUS_COLORS[status], className="me-2")]
        if crop:
            detail.append(html.Span(f"{crop['name']} {crop['moisture_min']}–{crop['moisture_max']} %", className="me-2"))
    else:
        icon = "bi-moisture text-primary"
        gauge = dbc.Progress(
            value=max(0.0, min(moisture, 100.0)), color=moisture_color(moisture), style={"height": "8px"}, className="my-1"
        )
        detail = [html.Span("capteur hors zone, valeur brute", className="me-2")]

    return dbc.Col(
        [
            html.Div(
                [
                    html.Span([html.I(className=f"bi {icon} me-2"), row["name"], *badges]),
                    html.Span(value, className="fw-semibold"),
                ],
                className="d-flex justify-content-between align-items-center",
                title=row["key"],
            ),
            html.Div(gauge, style={"opacity": 0.5 if stale else 1}),
            html.Div([*detail, html.Span(footer)], className="text-muted small"),
        ],
        md=6,
        lg=4,
        className="py-2",
    )


def build_soil_panel(rows: list[dict]):
    """Rien si aucun capteur de sol n'a encore publié : le panneau n'encombre
    pas la vue d'ensemble d'une installation qui n'en a pas."""
    if not rows:
        return None
    return dbc.Card(
        [
            dbc.CardHeader([html.I(className="bi bi-moisture me-2"), "Humidité du sol"], className="fw-semibold"),
            dbc.CardBody(dbc.Row([_build_row(row) for row in rows], className="gx-4"), className="py-1"),
        ],
        className="mb-3",
    )
