"""Filtres de fraîcheur de la vue d'ensemble : ce qu'elle montre doit
refléter l'installation actuelle, pas tout ce qui a publié un jour (device
débranché, capteur retiré ou désactivé dans le firmware dont la dernière
valeur traîne en base)."""

from datetime import datetime, timedelta, timezone

import pandas as pd

from . import config, queries


def latest_per_key(readings_df: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par (device, métrique), la plus récente : la base et le
    cache live peuvent tous deux en fournir une pour la même clé."""
    latest = readings_df.copy()
    latest["time"] = pd.to_datetime(latest["time"], utc=True)
    return latest.sort_values("time").drop_duplicates(subset=["device_id", "metric"], keep="last")


def drop_abandoned_metrics(readings_df: pd.DataFrame, now=None) -> pd.DataFrame:
    """Retire les mesures sans nouvelle valeur depuis
    OVERVIEW_METRIC_MAX_AGE_HOURS ; les vannes sont toujours gardées."""
    if readings_df.empty:
        return readings_df
    now = now or datetime.now(timezone.utc)
    latest = latest_per_key(readings_df)
    cutoff = pd.Timestamp(now - timedelta(hours=config.OVERVIEW_METRIC_MAX_AGE_HOURS))
    keep = latest["metric"].map(queries.is_valve_metric) | (latest["time"] >= cutoff)
    return latest[keep]


def active_device_ids(readings_df: pd.DataFrame, now=None) -> set[str]:
    """Appareils ayant publié quoi que ce soit (vanne comprise) depuis
    OVERVIEW_DEVICE_MAX_AGE_HOURS."""
    if readings_df.empty:
        return set()
    now = now or datetime.now(timezone.utc)
    times = pd.to_datetime(readings_df["time"], utc=True)
    cutoff = pd.Timestamp(now - timedelta(hours=config.OVERVIEW_DEVICE_MAX_AGE_HOURS))
    return set(readings_df.loc[times >= cutoff, "device_id"])


# Mesures déjà affichées ailleurs sur la vue d'ensemble, donc absentes des
# cartes des appareils : vannes (panneau des vannes), cuve (jauge),
# humidité du sol (panneau par zone, valeur calibrée) ; battery_v est une
# constante (device alimenté secteur).
CARD_HIDDEN_METRICS = {
    config.TANK_LEVEL_METRIC,
    "water_distance_cm",
    config.SOIL_MOISTURE_METRIC,
    "battery_v",
}


def card_metrics(readings_df: pd.DataFrame) -> pd.DataFrame:
    """Lignes à montrer sur les cartes des appareils."""
    if readings_df.empty:
        return readings_df
    hidden = readings_df["metric"].isin(CARD_HIDDEN_METRICS) | readings_df["metric"].map(queries.is_valve_metric).astype(bool)
    return readings_df[~hidden]
