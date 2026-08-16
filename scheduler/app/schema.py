"""Définitions SQLAlchemy Core des tables (miroir de db/init/001_schema.sql).

Dupliqué à l'identique dans ingest/, dashboard/ et weather/ : chaque service
a son propre contexte de build Docker et ses propres dépendances, il n'y a
pas de paquet Python partagé entre eux dans ce projet.
"""

from sqlalchemy import (
    ARRAY,
    Boolean,
    Column,
    Double,
    ForeignKey,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
    Time,
    TIMESTAMP,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

devices = Table(
    "devices",
    metadata,
    Column("device_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("location", String),
    Column("lat", Double),
    Column("lon", Double),
    Column("ip_address", String),
    Column("created_at", TIMESTAMP(timezone=True)),
)

sensor_readings = Table(
    "sensor_readings",
    metadata,
    Column("time", TIMESTAMP(timezone=True), nullable=False),
    Column("device_id", String, nullable=False),
    Column("metric", String, nullable=False),
    Column("value", Double, nullable=False),
    Column("unit", String),
)

sensor_readings_hourly = Table(
    "sensor_readings_hourly",
    metadata,
    Column("device_id", String),
    Column("metric", String),
    Column("bucket", TIMESTAMP(timezone=True)),
    Column("avg_value", Double),
    Column("min_value", Double),
    Column("max_value", Double),
    Column("sample_count", Double),
)

weather_forecast = Table(
    "weather_forecast",
    metadata,
    Column("fetched_at", TIMESTAMP(timezone=True), nullable=False),
    Column("valid_time", TIMESTAMP(timezone=True), nullable=False),
    Column("source", String, nullable=False),
    Column("lat", Double, nullable=False),
    Column("lon", Double, nullable=False),
    Column("metric", String, nullable=False),
    Column("value", Double, nullable=False),
    PrimaryKeyConstraint("source", "metric", "valid_time"),
)

weather_observed = Table(
    "weather_observed",
    metadata,
    Column("time", TIMESTAMP(timezone=True), nullable=False),
    Column("station_id", String, nullable=False),
    Column("metric", String, nullable=False),
    Column("value", Double, nullable=False),
    PrimaryKeyConstraint("station_id", "metric", "time"),
)

watering_programs = Table(
    "watering_programs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("name", String, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("start_time", Time, nullable=False),
    Column("days_of_week", ARRAY(SmallInteger), nullable=False),
    Column("default_duration_s", Integer, nullable=False),
    Column("conditions", JSONB, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True)),
    Column("updated_at", TIMESTAMP(timezone=True)),
)

watering_program_valves = Table(
    "watering_program_valves",
    metadata,
    Column("program_id", Integer, ForeignKey("watering_programs.id"), nullable=False),
    Column("device_id", String, nullable=False),
    Column("metric", String, nullable=False),
    Column("duration_s", Integer),
    PrimaryKeyConstraint("program_id", "device_id", "metric"),
)

watering_runs = Table(
    "watering_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("program_id", Integer, ForeignKey("watering_programs.id")),
    Column("program_name", String, nullable=False),
    Column("scheduled_for", TIMESTAMP(timezone=True), nullable=False),
    Column("status", String, nullable=False),
    Column("skip_reason", String),
    Column("valves_triggered", JSONB),
    Column("executed_at", TIMESTAMP(timezone=True)),
)
