"""Panneau "Humidité du sol" de la vue d'ensemble : une jauge par capteur de
sol (ESP32 ou Zigbee, tous publient "humidity_pct"), pour comparer les
zones du jardin d'un coup d'œil."""

from datetime import datetime, timedelta, timezone

import dash_bootstrap_components as dbc
import pandas as pd
from dash import html

from . import config
from .formatting import format_last_seen


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


def moisture_color(moisture_pct: float) -> str:
    if moisture_pct < config.SOIL_DRY_PCT:
        return "warning"
    if moisture_pct > config.SOIL_WET_PCT:
        return "info"
    return "success"


def is_stale(ts, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    return ts is None or pd.isna(ts) or now - pd.Timestamp(ts).to_pydatetime() > timedelta(hours=config.SOIL_STALE_HOURS)


def _build_sensor_row(row):
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

    return dbc.Col(
        [
            html.Div(
                [
                    html.Span([html.I(className="bi bi-flower1 me-2 text-primary"), row["name"], *badges]),
                    html.Span(f"{row['moisture_pct']:.0f} %", className="fw-semibold"),
                ],
                className="d-flex justify-content-between align-items-center",
                title=row["device_id"],
            ),
            dbc.Progress(
                value=max(0.0, min(row["moisture_pct"], 100.0)),
                color=moisture_color(row["moisture_pct"]),
                style={"height": "8px", "opacity": 0.5 if stale else 1},
                className="my-1",
            ),
            html.Div(footer, className="text-muted small"),
        ],
        md=6,
        lg=3,
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
            dbc.CardBody(dbc.Row([_build_sensor_row(row) for row in rows], className="gx-4"), className="py-1"),
        ],
        className="mb-3",
    )
