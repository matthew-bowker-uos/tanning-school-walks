"""Tests for ``schools_sunbeds.routing`` covering buffer geometry math.

Path-from-pandana is integration-tested via notebook 08.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from schools_sunbeds import routing


def test_path_to_linestring_returns_none_below_two_nodes() -> None:
    lookup = pd.DataFrame({"x": [0.0], "y": [0.0]}, index=[1])
    lookup.index.name = "osm_id"
    assert routing._path_to_linestring([1], lookup) is None
    assert routing._path_to_linestring([], lookup) is None


def test_path_to_linestring_builds_from_lookup() -> None:
    lookup = pd.DataFrame(
        {"x": [-1.61, -1.60, -1.59], "y": [54.97, 54.97, 54.97]},
        index=[1, 2, 3],
    )
    lookup.index.name = "osm_id"
    line = routing._path_to_linestring([1, 2, 3], lookup)
    assert isinstance(line, LineString)
    assert list(line.coords) == [(-1.61, 54.97), (-1.60, 54.97), (-1.59, 54.97)]


def test_path_to_linestring_skips_missing_nodes() -> None:
    lookup = pd.DataFrame(
        {"x": [-1.61, -1.59], "y": [54.97, 54.97]},
        index=[1, 3],
    )
    lookup.index.name = "osm_id"
    # Node 2 is missing; line should still build between 1 and 3.
    line = routing._path_to_linestring([1, 2, 3], lookup)
    assert isinstance(line, LineString)
    assert len(line.coords) == 2


def test_buffer_routes_produces_polygons() -> None:
    routes = gpd.GeoDataFrame(
        {"urn": [100], "lsoa21cd": ["X"]},
        geometry=[LineString([(0, 0), (1000, 0)])],
        crs="EPSG:27700",
    )
    buffered = routing.buffer_routes(routes, buffer_m=50)
    assert buffered.geometry.iloc[0].geom_type == "Polygon"
    # Approx area of a 1000 m x 100 m rectangle = 100k sq m
    area = buffered.geometry.iloc[0].area
    assert 95_000 < area < 110_000  # allow some tolerance for caps


def test_school_euclidean_buffers_long_format() -> None:
    schools = gpd.GeoDataFrame(
        {"urn": [1, 2]},
        geometry=[Point(0, 0), Point(10_000, 0)],
        crs="EPSG:27700",
    )
    buffered = routing.school_euclidean_buffers(schools, distances_m=(400, 800))
    assert len(buffered) == 4  # 2 schools × 2 distances
    assert sorted(buffered["distance_m"].unique()) == [400, 800]
    # 400 m buffer area ~= pi*r^2
    area_400 = buffered.loc[buffered["distance_m"] == 400, "geometry"].iloc[0].area
    assert 480_000 < area_400 < 525_000  # ~3.14 * 400^2 = 502654


def test_school_euclidean_buffers_rejects_non_bng() -> None:
    schools = gpd.GeoDataFrame({"urn": [1]}, geometry=[Point(0, 0)], crs="EPSG:4326")
    try:
        routing.school_euclidean_buffers(schools)
    except ValueError as exc:
        assert "27700" in str(exc)
    else:
        raise AssertionError("expected ValueError")
