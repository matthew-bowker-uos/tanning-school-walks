"""Tests for ``schools_sunbeds.geography`` covering the audit-critical
spatial-join helpers (``assign_lsoa_to_lad``, ``validate_schools_in_la``).

Network-dependent fetchers are not unit-tested here; they are exercised by
the Stage 2 notebook smoke run.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon

from schools_sunbeds import geography


def _square(x: float, y: float, size: float) -> Polygon:
    return Polygon(
        [
            (x, y),
            (x + size, y),
            (x + size, y + size),
            (x, y + size),
        ]
    )


def _lad_fixture() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "lad_code": ["E08000021", "E08000037"],
            "lad_name": ["Newcastle upon Tyne", "Gateshead"],
            "geometry": [
                _square(420_000, 560_000, 10_000),  # Newcastle
                _square(420_000, 550_000, 10_000),  # Gateshead, due south
            ],
        },
        crs="EPSG:27700",
    )


def _lsoa_fixture() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "lsoa21cd": ["NEWC1", "GATE1", "STRAY1"],
            "geometry": [
                _square(421_000, 561_000, 1_000),  # inside Newcastle
                _square(421_000, 551_000, 1_000),  # inside Gateshead
                _square(450_000, 555_000, 1_000),  # outside both
            ],
        },
        crs="EPSG:27700",
    )


def test_assign_lsoa_to_lad_attributes_via_centroid() -> None:
    lsoa = _lsoa_fixture()
    lad = _lad_fixture()
    out = geography.assign_lsoa_to_lad(lsoa, lad)
    by_code = out.set_index("lsoa21cd")["lad_code"]
    assert by_code["NEWC1"] == "E08000021"
    assert by_code["GATE1"] == "E08000037"
    assert pd.isna(by_code["STRAY1"])


def test_validate_schools_in_la_clean_agreement() -> None:
    schools = gpd.GeoDataFrame(
        {
            "urn": [1, 2],
            "la_code_ons": ["E08000021", "E08000037"],
            "geometry": [Point(425_000, 565_000), Point(425_000, 555_000)],
        },
        crs="EPSG:27700",
    )
    pip = geography.validate_schools_in_la(schools, _lad_fixture())
    assert pip["agree"].all()
    assert (pip["distance_m_to_declared"] == 0).all()


def test_validate_schools_in_la_flags_misattribution() -> None:
    # Newcastle URN that actually sits in Gateshead
    schools = gpd.GeoDataFrame(
        {
            "urn": [99],
            "la_code_ons": ["E08000021"],  # claims Newcastle
            "geometry": [Point(425_000, 555_000)],  # but is in Gateshead
        },
        crs="EPSG:27700",
    )
    pip = geography.validate_schools_in_la(schools, _lad_fixture())
    assert not pip.iloc[0]["agree"]
    assert pip.iloc[0]["polygon_la"] == "E08000037"
    # Distance from Gateshead point to Newcastle polygon should be > 0
    assert pip.iloc[0]["distance_m_to_declared"] > 0
