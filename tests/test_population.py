"""Tests for ``schools_sunbeds.population``."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

from schools_sunbeds import population


def _build_ts007a_zip(tmp_path: Path) -> Path:
    csv = (
        "date,geography,geography code,Age: Total,Age: Aged 4 years and under,"
        "Age: Aged 5 to 9 years,Age: Aged 10 to 14 years,Age: Aged 15 to 19 years,"
        "Age: Aged 20 to 24 years\n"
        "2021,Foo 001A,E01000001,1473,52,34,32,23,90\n"
        "2021,Foo 001B,E01000002,1384,40,30,28,20,80\n"
    )
    zpath = tmp_path / "census2021-ts007a.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(population.CENSUS_TS007A_LSOA_CSV, csv)
    return zpath


def test_load_lsoa_school_age_population(tmp_path: Path) -> None:
    zpath = _build_ts007a_zip(tmp_path)
    out = population.load_lsoa_school_age_population(zpath)
    assert list(out.columns) == ["lsoa21cd", "pop_total", "pop_school_age"]
    # Row 1: ages 5-19 = 34 + 32 + 23 = 89
    assert out.loc[0, "pop_school_age"] == 89
    assert out.loc[0, "pop_total"] == 1473
    # Row 2: 30 + 28 + 20 = 78
    assert out.loc[1, "pop_school_age"] == 78


def test_load_lsoa_school_age_raises_on_missing_band(tmp_path: Path) -> None:
    csv = (
        "date,geography,geography code,Age: Total,Age: Aged 5 to 9 years,Age: Aged 10 to 14 years\n"
        "2021,Foo,E01000001,1000,30,30\n"
    )
    zpath = tmp_path / "missing.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr(population.CENSUS_TS007A_LSOA_CSV, csv)
    try:
        population.load_lsoa_school_age_population(zpath)
    except RuntimeError as exc:
        assert "missing expected columns" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_load_lsoa21_ruc(tmp_path: Path) -> None:
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "LSOA21CD": "E01000001",
                    "RUC21CD": "UN1",
                    "RUC21NM": "Urban: Nearer to a major town or city",
                    "Urban_rura": "Urban",
                },
                "geometry": None,
            },
            {
                "type": "Feature",
                "properties": {
                    "LSOA21CD": "E01000002",
                    "RUC21CD": "RR1",
                    "RUC21NM": "Larger rural",
                    "Urban_rura": "Rural",
                },
                "geometry": None,
            },
        ],
    }
    import json

    fpath = tmp_path / "ruc.geojson"
    fpath.write_text(json.dumps(payload))
    out = population.load_lsoa21_ruc(fpath)
    assert list(out.columns) == ["lsoa21cd", "ruc21cd", "ruc21nm", "urban_rural"]
    assert out["urban_rural"].tolist() == ["Urban", "Rural"]

    # filter case
    filtered = population.load_lsoa21_ruc(fpath, lsoa_codes=["E01000002"])
    assert len(filtered) == 1
    assert filtered.iloc[0]["urban_rural"] == "Rural"
