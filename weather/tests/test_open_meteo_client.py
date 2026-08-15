from datetime import datetime, timezone

import pytest

from app import open_meteo_client


class _FakeResponse:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data


# --- _extract_series ---------------------------------------------------------

def test_extract_series_reads_model_suffixed_fields():
    data = {
        "hourly": {
            "time": ["2026-07-16T10:00", "2026-07-16T11:00"],
            "temperature_2m_meteofrance_arome_seamless": [21.3, 22.0],
            "precipitation_meteofrance_arome_seamless": [0.0, 0.5],
        }
    }
    rows = open_meteo_client._extract_series(data, "AROME", "meteofrance_arome_seamless")

    by_metric_time = {(r["metric"], r["time"]): r["value"] for r in rows}
    assert by_metric_time[("temperature_c", datetime(2026, 7, 16, 10, tzinfo=timezone.utc))] == 21.3
    assert by_metric_time[("precipitation_mm", datetime(2026, 7, 16, 11, tzinfo=timezone.utc))] == 0.5
    assert all(r["source"] == "AROME" for r in rows)


def test_extract_series_falls_back_to_unsuffixed_key():
    """Robustesse si un seul modèle est demandé et qu'Open-Meteo ne suffixe pas."""
    data = {"hourly": {"time": ["2026-07-16T10:00"], "temperature_2m": [21.3]}}
    rows = open_meteo_client._extract_series(data, "AROME", "meteofrance_arome_seamless")
    assert rows[0]["value"] == 21.3


def test_extract_series_skips_null_values():
    data = {
        "hourly": {
            "time": ["2026-07-16T10:00", "2026-07-16T11:00"],
            "temperature_2m_meteofrance_arome_seamless": [21.3, None],
        }
    }
    rows = open_meteo_client._extract_series(data, "AROME", "meteofrance_arome_seamless")
    assert len(rows) == 1


def test_extract_series_skips_missing_variable_entirely():
    data = {"hourly": {"time": ["2026-07-16T10:00"]}}
    rows = open_meteo_client._extract_series(data, "AROME", "meteofrance_arome_seamless")
    assert rows == []


# --- fetch_series (mock HTTP) --------------------------------------------------

def test_fetch_series_combines_arome_and_arpege(mocker):
    fake_json = {
        "hourly": {
            "time": ["2026-07-16T10:00"],
            "temperature_2m_meteofrance_arome_seamless": [21.3],
            "precipitation_meteofrance_arome_seamless": [0.0],
            "temperature_2m_meteofrance_arpege_europe": [20.9],
            "precipitation_meteofrance_arpege_europe": [0.1],
        }
    }
    mocker.patch("app.open_meteo_client.requests.get", return_value=_FakeResponse(200, fake_json))

    rows = open_meteo_client.fetch_series(45.19, 5.72, past_days=1, forecast_days=1)
    sources = {r["source"] for r in rows}
    assert sources == {"AROME", "ARPEGE"}
    assert len(rows) == 4  # 2 metrics x 2 sources x 1 timestamp


def test_fetch_series_raises_on_http_error(mocker):
    mocker.patch("app.open_meteo_client.requests.get", return_value=_FakeResponse(500, text="internal error"))
    with pytest.raises(open_meteo_client.OpenMeteoApiError):
        open_meteo_client.fetch_series(45.19, 5.72, past_days=1, forecast_days=1)


def test_fetch_series_passes_expected_query_params(mocker):
    fake_json = {"hourly": {"time": []}}
    get = mocker.patch("app.open_meteo_client.requests.get", return_value=_FakeResponse(200, fake_json))

    open_meteo_client.fetch_series(45.19, 5.72, past_days=7, forecast_days=4)

    _, kwargs = get.call_args
    params = kwargs["params"]
    assert params["latitude"] == 45.19
    assert params["longitude"] == 5.72
    assert params["past_days"] == 7
    assert params["forecast_days"] == 4
    assert "meteofrance_arome_seamless" in params["models"]
    assert "meteofrance_arpege_europe" in params["models"]
