"""Tests for ``schools_sunbeds.exposure``."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from schools_sunbeds import exposure


def _salons() -> gpd.GeoDataFrame:
    # Three salons within a small box; one further out.
    return gpd.GeoDataFrame(
        {"place_id": ["a", "b", "c", "d"]},
        geometry=[
            Point(425_000, 565_000),
            Point(425_100, 565_050),
            Point(425_200, 565_010),
            Point(430_000, 565_000),  # far away
        ],
        crs="EPSG:27700",
    )


def _school_buffers_long() -> gpd.GeoDataFrame:
    sch = gpd.GeoDataFrame(
        {"urn": [1, 2]},
        geometry=[Point(425_050, 565_020), Point(450_000, 600_000)],
        crs="EPSG:27700",
    )
    rows = []
    for d in (200, 500):
        sub = sch.copy()
        sub["distance_m"] = d
        sub["geometry"] = sub.geometry.buffer(d)
        rows.append(sub)
    return gpd.GeoDataFrame(pd.concat(rows, ignore_index=True), geometry="geometry", crs="EPSG:27700")


def _route_buffers() -> gpd.GeoDataFrame:
    # Two routes ending at school 1: one straight through the salon cluster,
    # one missing it.
    return gpd.GeoDataFrame(
        {
            "urn": [1, 1],
            "lsoa21cd": ["L_HOT", "L_NEAR"],
            "pop_school_age": [200, 80],
        },
        geometry=[
            LineString([(424_900, 565_020), (425_300, 565_020)]).buffer(50),
            LineString([(425_500, 565_020), (425_800, 565_020)]).buffer(50),
        ],
        crs="EPSG:27700",
    )


def test_count_points_in_polygons_counts_per_polygon() -> None:
    buf = _school_buffers_long()
    s = exposure._count_points_in_polygons(_salons(), buf, group_col="urn")
    # Multiple buffer rows share urn=1 (one per distance); the helper counts
    # all matches across both buffers — that's why the wrapper buffer_exposure
    # filters by distance first. Here we just check school 1 sees salons.
    assert s.loc[1] >= 3


def test_buffer_exposure_columns_per_distance() -> None:
    panel = exposure.buffer_exposure(_school_buffers_long(), _salons())
    assert set(panel.columns) >= {"urn", "n_buffer_200m", "n_buffer_500m"}
    school1 = panel.loc[panel["urn"] == 1].iloc[0]
    school2 = panel.loc[panel["urn"] == 2].iloc[0]
    # 200m buffer around school1 captures all 3 nearby salons
    assert school1["n_buffer_200m"] == 3
    assert school1["n_buffer_500m"] == 3  # 4th salon is at 5km, way outside
    # School 2 is far from any salon
    assert school2["n_buffer_200m"] == 0
    assert school2["n_buffer_500m"] == 0


def test_route_exposure_uses_pop_weighting() -> None:
    rt = exposure.route_exposure(_route_buffers(), _salons(), width_label="50m")
    row = rt.loc[rt["urn"] == 1].iloc[0]
    assert row["n_routes_50m"] == 2
    assert row["sum_pupil_50m"] == 280  # 200 + 80
    # Hot route sees all 3 nearby salons (200 children); near route sees 0 (80 children)
    # Sum = 200 * 3 + 80 * 0 = 600
    assert row["sum_route_50m"] == 600
    # Mean per pupil = 600 / 280 ~= 2.14
    assert abs(row["mean_per_pupil_route_50m"] - 600 / 280) < 1e-6
    assert row["max_route_50m"] == 3


def test_build_exposure_panel_handles_no_routes() -> None:
    schools = gpd.GeoDataFrame(
        {
            "urn": [1, 2],
            "establishment_name": ["A", "B"],
            "phase": ["Primary", "Primary"],
        },
        geometry=[Point(425_050, 565_020), Point(450_000, 600_000)],
        crs="EPSG:27700",
    )
    panel = exposure.build_exposure_panel(
        schools,
        _salons(),
        _school_buffers_long(),
        _route_buffers(),
        route_buffers_100m_gdf=None,
    )
    s2 = panel.loc[panel["urn"] == 2].iloc[0]
    # School 2 has no routes — count cols are 0, sum cols are 0
    assert s2["n_routes_50m"] == 0
    assert s2["sum_route_50m"] == 0.0
    assert pd.isna(s2["mean_per_pupil_route_50m"])
