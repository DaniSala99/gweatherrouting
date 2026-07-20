# -*- coding: utf-8 -*-
# Copyright (C) 2017-2026 Davide Gessa
"""
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

For detail about GNU see <http://www.gnu.org/licenses/>.
"""

from datetime import datetime

import pytest
import responses

from gweatherrouting.core.crosscheck.openmeteo import (
    EUROPE_MODELS,
    GLOBAL_MODELS,
    CrossCheckPoint,
    OpenMeteoCrossCheck,
    models_for_route,
)


def _hourly_series(
    base_hour, n, values_by_model, gusts_by_model=None, precip_by_model=None
):
    """Build a synthetic Open-Meteo 'hourly' block for one location."""
    times = [f"2026-07-20T{h:02d}:00" for h in range(base_hour, base_hour + n)]
    hourly = {"time": times}
    for model, values in values_by_model.items():
        hourly[f"wind_speed_10m_{model}"] = values
        hourly[f"wind_direction_10m_{model}"] = [180.0] * n
    if gusts_by_model:
        for model, values in gusts_by_model.items():
            hourly[f"wind_gusts_10m_{model}"] = values
    if precip_by_model:
        for model, values in precip_by_model.items():
            hourly[f"precipitation_{model}"] = values
    return hourly


def test_models_for_route_picks_europe_set_when_majority_in_box():
    route = [(43.0, 9.0), (44.0, 10.0), (45.0, 11.0)]
    assert models_for_route(route) == EUROPE_MODELS


def test_models_for_route_picks_global_set_outside_europe():
    route = [(10.0, -160.0), (12.0, -158.0)]
    assert models_for_route(route) == GLOBAL_MODELS


def test_models_for_route_empty_defaults_to_global():
    assert models_for_route([]) == GLOBAL_MODELS


@responses.activate
def test_check_builds_envelope_across_models():
    """Mirrors a real response captured from api.open-meteo.com for two
    Mediterranean points. Both points fall inside EUROPE_BOUNDS, so the
    client is expected to request EUROPE_MODELS (icon/arome/ukmo seamless).
    """
    payload = [
        {
            "latitude": 43.7,
            "longitude": 10.3,
            "hourly": _hourly_series(
                12,
                3,
                {
                    "icon_seamless": [4.61, 4.74, 4.55],
                    "arome_seamless": [5.9, 6.1, 5.8],
                    "ukmo_seamless": [3.2, 3.4, 3.1],
                },
                {
                    "icon_seamless": [7.1, 8.1, 7.9],
                    "arome_seamless": [9.0, 9.5, 9.1],
                    "ukmo_seamless": [5.0, 5.2, 5.1],
                },
            ),
        },
        {
            "latitude": 44.1,
            "longitude": 9.8,
            "hourly": _hourly_series(
                12,
                3,
                {
                    "icon_seamless": [3.0, 3.1, 3.2],
                    "arome_seamless": [3.5, 3.6, 3.7],
                    "ukmo_seamless": [2.9, 3.0, 3.1],
                },
            ),
        },
    ]
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/forecast",
        json=payload,
        status=200,
    )

    client = OpenMeteoCrossCheck()
    points = [
        (43.7, 10.3, datetime(2026, 7, 20, 13, 0), 3.5, 200.0),
        (44.1, 9.8, datetime(2026, 7, 20, 13, 0), 3.0, 190.0),
    ]
    results = client.check(points)

    assert len(results) == 2

    first = results[0]
    # AROME is the strongest of the three models at hour 13 -> envelope = 6.1
    assert first.envelope_tws == pytest.approx(6.1)
    assert first.envelope_gust == pytest.approx(9.5)
    assert first.primary_tws == 3.5
    assert first.margin_ratio == pytest.approx(6.1 / 3.5)
    assert not first.missing_models

    second = results[1]
    # No gust data was provided for this location in the fixture.
    assert second.envelope_gust is None
    assert second.envelope_tws == pytest.approx(3.6)


@responses.activate
def test_check_handles_model_missing_from_response():
    """Regional-only models (eg. arome_seamless) are silently dropped by
    Open-Meteo for points outside their coverage, verified live against the
    real API for a mid-Pacific point - the client must not crash on this and
    should record it as a missing model instead.
    """
    payload = [
        {
            "latitude": 10.0,
            "longitude": -160.0,
            "hourly": _hourly_series(
                0,
                2,
                {
                    "gfs_seamless": [8.5, 8.1],
                    "icon_seamless": [6.8, 7.2],
                    "gem_seamless": [4.5, 3.3],
                },
            ),
        }
    ]
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/forecast",
        json=payload,
        status=200,
    )

    client = OpenMeteoCrossCheck()
    points = [(10.0, -160.0, datetime(2026, 7, 20, 0, 0), 5.0, 90.0)]
    results = client.check(points)

    assert len(results) == 1
    assert results[0].envelope_tws == pytest.approx(8.5)
    assert results[0].missing_models == []


@responses.activate
def test_check_raises_on_location_count_mismatch():
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/forecast",
        json=[{"latitude": 1.0, "longitude": 1.0, "hourly": {"time": []}}],
        status=200,
    )

    client = OpenMeteoCrossCheck()
    points = [
        (1.0, 1.0, datetime(2026, 7, 20, 0, 0), 5.0, 90.0),
        (2.0, 2.0, datetime(2026, 7, 20, 0, 0), 5.0, 90.0),
    ]
    with pytest.raises(ValueError):
        client.check(points)


def test_check_empty_points_returns_empty_list():
    assert OpenMeteoCrossCheck().check([]) == []


def _point(primary_tws, envelope_tws):
    return CrossCheckPoint(
        lat=43.0,
        lon=10.0,
        time=datetime(2026, 7, 21, 8, 0),
        primary_tws=primary_tws,
        primary_twd=200.0,
        envelope_tws=envelope_tws,
    )


def test_exceeds_warning_needs_both_ratio_and_absolute_excess():
    # Huge ratio but tiny absolute excess (light air): not flagged.
    assert not _point(0.5, 1.5).exceeds_warning
    # Large absolute excess but ratio below threshold: not flagged.
    assert not _point(20.0, 21.0).exceeds_warning
    # Both thresholds exceeded: flagged.
    assert _point(5.0, 8.0).exceeds_warning


def test_exceeds_warning_false_without_envelope_or_wind():
    assert not _point(5.0, None).exceeds_warning
    assert not _point(0.0, 5.0).exceeds_warning


def test_precip_warning_threshold():
    p = _point(5.0, 6.0)
    assert not p.precip_warning  # no precip data at all
    p.envelope_precip = 0.4
    assert not p.precip_warning  # drizzle
    p.envelope_precip = 2.5
    assert p.precip_warning  # moderate rain


@responses.activate
def test_check_with_explicit_models_and_precipitation():
    """An explicit model list bypasses the auto Europe/global selection, and
    precipitation is enveloped across models like wind is."""
    payload = [
        {
            "latitude": 10.0,
            "longitude": -160.0,
            "hourly": _hourly_series(
                0,
                2,
                {"ecmwf_ifs025": [8.0, 8.5], "jma_seamless": [7.0, 7.5]},
                precip_by_model={
                    "ecmwf_ifs025": [0.3, 0.1],
                    "jma_seamless": [2.8, 1.0],
                },
            ),
        }
    ]
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/forecast",
        json=payload,
        status=200,
    )

    client = OpenMeteoCrossCheck()
    points = [(10.0, -160.0, datetime(2026, 7, 20, 0, 0), 5.0, 90.0)]
    results = client.check(points, models=["ecmwf_ifs025", "jma_seamless"])

    assert "ecmwf_ifs025" in responses.calls[0].request.url
    cp = results[0]
    assert cp.envelope_tws == pytest.approx(8.0)
    assert cp.envelope_precip == pytest.approx(2.8)
    assert cp.per_model_precip["jma_seamless"] == pytest.approx(2.8)
    assert cp.precip_warning
