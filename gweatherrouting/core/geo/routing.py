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

import gpxpy
from weatherrouting import IsoPoint

from gweatherrouting.core.geo.track import Track


class Routing(Track):
    def __init__(self, name, points=[], isochrones=[], visible=True, collection=None):
        super().__init__(name, points, visible, collection)
        self.isochrones = isochrones

        # Populated in-memory by the optional online cross-check
        # (see chartstack_routing.on_routing_crosscheck): crosscheck_margin
        # maps a point index to the multi-model wind margin ratio there,
        # crosscheck_precip to the precipitation envelope in mm/h, and
        # crosscheck_flagged holds the indexes worth warning about (wind or
        # rain). Deliberately not persisted: a snapshot of an online query,
        # not routing state.
        self.crosscheck_margin: dict = {}
        self.crosscheck_precip: dict = {}
        self.crosscheck_flagged: set = set()

    def to_gpx_object(self):
        gpx_route = gpxpy.gpx.GPXRoute()

        for x in self.points:
            gpx_route.points.append(gpxpy.gpx.GPXRoutePoint(x[0], x[1]))

        return gpx_route

    def to_json(self):
        c = super().to_json()
        c["isochrones"] = list(
            map(lambda x: list(map(lambda y: y.to_list(), x)), self.isochrones)
        )
        return c

    @staticmethod
    def from_json(j):
        d = Track.from_json(j)
        ic = list(map(lambda x: list(map(IsoPoint.from_list, x)), j["isochrones"]))
        return Routing(d.name, d.points, ic, d.visible)
