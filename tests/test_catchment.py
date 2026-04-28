"""Tests for the catchment module.

Live pandana-based assignment is integration-tested via notebook 07; here
we cover the pure-Python pieces (phase mapping, error paths, dataclass).
"""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import Point

from schools_sunbeds import catchment as ct


def test_phase_category_mapping_covers_all_in_scope_phases() -> None:
    # Every PhaseOfEducation string we keep in Stage 1 must map to a cap key.
    for phase in (
        "Primary",
        "Middle deemed primary",
        "Secondary",
        "Middle deemed secondary",
        "All-through",
        "Special",
        "Not applicable",  # Stage 1 carve-out
    ):
        assert phase in ct.PHASE_CATEGORY, phase
        assert ct.PHASE_CATEGORY[phase] in {"primary", "secondary", "special"}


def test__phase_categories_raises_on_unmapped() -> None:
    schools = gpd.GeoDataFrame(
        {
            "phase": ["Primary", "Postgraduate"],  # 2nd is not mapped
            "geometry": [Point(0, 0), Point(0, 1)],
        },
        crs="EPSG:27700",
    )
    with pytest.raises(ValueError, match="Unrecognised phase"):
        ct._phase_categories(schools, phase_col="phase")


def test_catchment_result_dataclass_holds_pieces() -> None:
    import pandas as pd

    df = pd.DataFrame(columns=["lsoa21cd", "urn", "phase", "distance_m", "weight"])
    result = ct.CatchmentResult(assignments=df, audit={"n_lsoa": 0})
    assert result.audit["n_lsoa"] == 0
    assert list(result.assignments.columns) == [
        "lsoa21cd",
        "urn",
        "phase",
        "distance_m",
        "weight",
    ]
