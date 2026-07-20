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

import os
import sys


def _bootstrap_msys2_gdal_env():
    """On Windows, GDAL/PROJ can't find their data directories when running
    under the MSYS2 mingw64 Python (unlike the official GDAL wheels, which
    bundle their own data dir) - this crashes any OSM-based chart layer with
    "Cannot find osmconf.ini". The packaged Windows build already works
    around this in its launcher script (see windows.yaml), but running from
    source had no equivalent, so set it here based on the interpreter's own
    location. Never overrides an explicitly configured environment, and is a
    no-op unless the MSYS2 layout is actually detected.
    """
    if sys.platform != "win32":
        return
    if "GDAL_DATA" in os.environ and "PROJ_LIB" in os.environ:
        return

    mingw_share = os.path.join(
        os.path.dirname(os.path.dirname(sys.executable)), "share"
    )

    if "GDAL_DATA" not in os.environ:
        gdal_data = os.path.join(mingw_share, "gdal")
        if os.path.isdir(gdal_data):
            os.environ["GDAL_DATA"] = gdal_data

    if "PROJ_LIB" not in os.environ:
        proj_lib = os.path.join(mingw_share, "proj")
        if os.path.isdir(proj_lib):
            os.environ["PROJ_LIB"] = proj_lib


_bootstrap_msys2_gdal_env()

# isort:skip_file
from . import core  # noqa: F401

__version__ = "0.3.6"
