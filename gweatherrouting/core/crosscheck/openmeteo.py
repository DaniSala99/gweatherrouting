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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("gweatherrouting")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Model slugs verified live against the API (wind + precipitation for each).
# "_seamless" variants blend a national/regional high-res model with a coarser
# global one outside their native coverage, but a model with no global
# fallback (eg. arome_seamless) is simply omitted from the response for
# points outside its region - the client records it as missing, see
# test_crosscheck_openmeteo.py.
AVAILABLE_MODELS = {
    "icon_seamless": "DWD ICON",
    "gfs_seamless": "NOAA GFS",
    "ecmwf_ifs025": "ECMWF IFS",
    "arome_seamless": "Météo-France AROME",
    "ukmo_seamless": "UK Met Office",
    "gem_seamless": "Canadian GEM",
    "jma_seamless": "Japanese JMA",
}

# Default independent-core sets used when the caller doesn't pick models.
EUROPE_MODELS = ["icon_seamless", "arome_seamless", "ukmo_seamless"]
GLOBAL_MODELS = ["gfs_seamless", "icon_seamless", "gem_seamless"]

# Rough box covering the Atlantic approaches, the Med and the Baltic - only
# used to pick which model set to request, not for anything geometric.
EUROPE_BOUNDS = (34.0, 72.0, -25.0, 45.0)  # lat_min, lat_max, lon_min, lon_max

FORECAST_DAYS_MAX = 16

# A point is flagged in the UI when BOTH hold: the envelope exceeds the GRIB
# estimate by this ratio AND by this many m/s. The absolute floor matters in
# light air, where a huge ratio on top of 0.5 m/s is meaningless - observed
# on a real Elba-Corsica test route, where ratio alone flagged 9/10 points.
# Not a safety threshold, just a default for "worth a second look".
DEFAULT_WARNING_RATIO = 1.15
DEFAULT_WARNING_MIN_EXCESS_MS = 2.0

# Precipitation envelope (mm/h) at or above which a point is flagged. The
# GRIB files the router uses carry wind only, so there is no "primary" to
# compare against - this is an absolute heads-up threshold, roughly the
# WMO boundary between light and moderate rain.
DEFAULT_WARNING_PRECIP_MM = 2.0


def _is_europe(lat: float, lon: float) -> bool:
    lat_min, lat_max, lon_min, lon_max = EUROPE_BOUNDS
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max


def models_for_route(points: List[Tuple[float, float]]) -> List[str]:
    """Pick one model set for the whole route, based on where most of its
    points fall. Deliberately not decided per-point: switching model sets
    mid-route would make the envelope jump for reasons unrelated to the
    weather itself.
    """
    if not points:
        return GLOBAL_MODELS
    in_europe = sum(1 for lat, lon in points if _is_europe(lat, lon))
    return EUROPE_MODELS if in_europe >= len(points) / 2 else GLOBAL_MODELS


@dataclass
class CrossCheckPoint:
    """Multi-model cross-check for a single already-routed point.

    All wind speeds are m/s, directions are degrees - the same convention as
    ``weatherrouting.Grib.get_wind_at``. ``primary_tws``/``primary_twd`` are
    what the GRIB-based router actually used at this point; ``envelope_tws``
    is the max across the queried models, ie. a cautionary spread margin, not
    a statistical worst case.
    """

    lat: float
    lon: float
    time: datetime
    primary_tws: float
    primary_twd: float
    envelope_tws: Optional[float] = None
    envelope_gust: Optional[float] = None
    envelope_precip: Optional[float] = None
    per_model_tws: Dict[str, float] = field(default_factory=dict)
    per_model_precip: Dict[str, float] = field(default_factory=dict)
    missing_models: List[str] = field(default_factory=list)

    @property
    def margin_ratio(self) -> Optional[float]:
        """envelope_tws / primary_tws, or None if either side is unusable."""
        if self.envelope_tws is None or self.primary_tws <= 0:
            return None
        return self.envelope_tws / self.primary_tws

    @property
    def exceeds_warning(self) -> bool:
        """True when the envelope departs from the GRIB estimate enough to be
        worth flagging: above the relative threshold AND by an absolute
        margin that matters (see DEFAULT_WARNING_MIN_EXCESS_MS).
        """
        ratio = self.margin_ratio
        if ratio is None or self.envelope_tws is None:
            return False
        excess = self.envelope_tws - self.primary_tws
        return (
            ratio >= DEFAULT_WARNING_RATIO and excess >= DEFAULT_WARNING_MIN_EXCESS_MS
        )

    @property
    def precip_warning(self) -> bool:
        """True when at least one model expects meaningful rain here."""
        if self.envelope_precip is None:
            return False
        return self.envelope_precip >= DEFAULT_WARNING_PRECIP_MM


class OpenMeteoCrossCheck:
    """Optional, opt-in, online safety-margin check on top of an
    already-computed route.

    Not a new weather data source and not a change to the routing engine:
    the GRIB-based router runs exactly as today. This only queries Open-Meteo
    for the finite set of (lat, lon, time) points a route already passes
    through, once, after routing has completed, and reports how far the
    multi-model spread departs from the GRIB estimate the router used.

    All ``time`` values passed in and returned are expected to be naive UTC
    datetimes (Open-Meteo's hourly timestamps are UTC by default). Converting
    from whatever timezone convention the rest of the app uses is the
    caller's responsibility.
    """

    def __init__(
        self, session: Optional[requests.Session] = None, timeout: float = 20.0
    ):
        self._session = session or requests.Session()
        self.timeout = timeout

    def check(
        self,
        points: List[Tuple[float, float, datetime, float, float]],
        models: Optional[List[str]] = None,
    ) -> List[CrossCheckPoint]:
        """
        points: (lat, lon, time, primary_tws_ms, primary_twd_deg) tuples,
        typically thinned down from a RoutingResult.path (one entry per
        route leg is enough - there is no need to query every isopoint).
        models: Open-Meteo model slugs to query (see AVAILABLE_MODELS);
        defaults to an independent-core set picked from the route location.
        """
        if not points:
            return []

        if not models:
            models = models_for_route([(p[0], p[1]) for p in points])
        response = self._fetch(points, models)

        if len(response) != len(points):
            raise ValueError(
                f"Open-Meteo returned {len(response)} locations for "
                f"{len(points)} requested points"
            )

        return self._build_envelope(points, models, response)

    def _fetch(self, points, models) -> List[dict]:
        lats = ",".join(f"{p[0]:.4f}" for p in points)
        lons = ",".join(f"{p[1]:.4f}" for p in points)

        params: Dict[str, str] = {
            "latitude": lats,
            "longitude": lons,
            "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,precipitation",
            "models": ",".join(models),
            "wind_speed_unit": "ms",
            "timezone": "UTC",
            "forecast_days": str(self._forecast_days_needed(points)),
        }
        r = self._session.get(OPEN_METEO_URL, params=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()

        # A single-point request gets back a bare object, not a list.
        if isinstance(data, dict):
            data = [data]
        return data

    @staticmethod
    def _forecast_days_needed(points) -> int:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        latest = max((p[2] for p in points), default=now)
        days_ahead = max((latest - now).days + 2, 1)
        return min(days_ahead, FORECAST_DAYS_MAX)

    def _build_envelope(self, points, models, response) -> List[CrossCheckPoint]:
        results = []

        for (lat, lon, t, primary_tws, primary_twd), location in zip(points, response):
            cp = CrossCheckPoint(
                lat=lat,
                lon=lon,
                time=t,
                primary_tws=primary_tws,
                primary_twd=primary_twd,
            )
            hourly = location.get("hourly", {})
            times = hourly.get("time", [])
            idx = self._nearest_hour_index(times, t)

            tws_values = []
            gust_values = []
            precip_values = []

            for model in models:
                speed_key = f"wind_speed_10m_{model}"
                gust_key = f"wind_gusts_10m_{model}"
                precip_key = f"precipitation_{model}"

                if idx is None or speed_key not in hourly:
                    cp.missing_models.append(model)
                    continue

                speed = hourly[speed_key][idx]
                gust = hourly.get(gust_key, [None] * len(times))[idx]
                precip = hourly.get(precip_key, [None] * len(times))[idx]

                if speed is None:
                    cp.missing_models.append(model)
                    continue

                cp.per_model_tws[model] = speed
                tws_values.append(speed)
                if gust is not None:
                    gust_values.append(gust)
                if precip is not None:
                    cp.per_model_precip[model] = precip
                    precip_values.append(precip)

            if tws_values:
                cp.envelope_tws = max(tws_values)
            if gust_values:
                cp.envelope_gust = max(gust_values)
            if precip_values:
                cp.envelope_precip = max(precip_values)

            results.append(cp)

        return results

    @staticmethod
    def _nearest_hour_index(
        times: List[str], t: datetime, tolerance_hours: float = 3.0
    ) -> Optional[int]:
        if not times:
            return None

        target = t.replace(minute=0, second=0, microsecond=0)
        target_iso = target.strftime("%Y-%m-%dT%H:%M")

        try:
            return times.index(target_iso)
        except ValueError:
            pass

        parsed = [datetime.fromisoformat(x) for x in times]
        diffs = [abs((p - target).total_seconds()) for p in parsed]
        best = min(range(len(diffs)), key=lambda i: diffs[i])

        if diffs[best] <= tolerance_hours * 3600:
            return best
        return None
