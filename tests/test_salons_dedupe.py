"""Tests for ``salons_dedupe``."""

from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Point

from schools_sunbeds import salons_dedupe


def _gp(name: str, x: float, y: float, place_id: str = "abc") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"place_id": [place_id], "name": [name]},
        geometry=[Point(x, y)],
        crs="EPSG:27700",
    )


def _op(name: str, x: float, y: float, osm_id: int = 1) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {"osm_id": [osm_id], "name": [name]},
        geometry=[Point(x, y)],
        crs="EPSG:27700",
    )


def test_match_salons_pairs_close_with_matching_name() -> None:
    google = _gp("Indigo Sun Tanning", 425_000, 565_000, "g1")
    osm = _op("Indigo Sun Tan", 425_010, 565_010, 1001)
    out = salons_dedupe.match_salons(google, osm)
    assert out.summary["n_matched"] == 1
    assert out.summary["n_google_only"] == 0
    assert out.summary["n_osm_only"] == 0


def test_match_salons_rejects_far_pair() -> None:
    google = _gp("Indigo Sun Tanning", 425_000, 565_000, "g1")
    osm = _op("Indigo Sun Tan", 425_500, 565_000, 1001)  # 500 m away
    out = salons_dedupe.match_salons(google, osm)
    assert out.summary["n_matched"] == 0
    assert out.summary["n_google_only"] == 1
    assert out.summary["n_osm_only"] == 1


def test_match_salons_rejects_close_but_different_names() -> None:
    google = _gp("Smith's Bakery", 425_000, 565_000, "g1")
    osm = _op("Sunny Beauty", 425_010, 565_010, 1001)
    out = salons_dedupe.match_salons(google, osm)
    assert out.summary["n_matched"] == 0
