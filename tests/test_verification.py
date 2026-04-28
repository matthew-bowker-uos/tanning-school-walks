"""Tests for ``schools_sunbeds.verification``."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from schools_sunbeds import verification as v


def _google_fixture() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "place_id": ["g1", "g2"],
            "name": ["Salon A", "Tesco Express"],
            "address": ["1 High St", "Tesco, Queen St"],
            "lad_code": ["E08000021", "E06000057"],
            "imd_quintile": [1, 3],
        },
        geometry=[Point(425_000, 565_000), Point(420_000, 600_000)],
        crs="EPSG:27700",
    )


def _osm_fixture() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "osm_id": [1001, 1002],
            "name": ["Salon A", "Sunny Beauty"],
            "addr_full": ["1 High St", "9 Main St"],
            "lad_code": ["E08000021", "E08000037"],
            "imd_quintile": [1, 2],
        },
        geometry=[Point(425_010, 565_010), Point(424_000, 555_000)],
        crs="EPSG:27700",
    )


def test_make_verification_id_is_stable() -> None:
    assert v.make_verification_id("google", "abc") == "google:abc"
    assert v.make_verification_id("osm", "1001") == "osm:1001"


def test_update_verification_csv_creates_file_with_all_rows(tmp_path: Path) -> None:
    out = tmp_path / "manual_verification.csv"
    path, audit = v.update_verification_csv(
        out,
        google_gdf=_google_fixture(),
        osm_gdf=_osm_fixture(),
    )
    assert path.exists()
    df = pd.read_csv(path)
    assert len(df) == 4
    assert (df["status"] == "pending").all()
    assert audit["rows_new_appended"] == 4
    assert audit["rows_existing_kept"] == 0


def test_update_verification_csv_is_idempotent(tmp_path: Path) -> None:
    out = tmp_path / "v.csv"
    v.update_verification_csv(out, google_gdf=_google_fixture(), osm_gdf=_osm_fixture())
    # Hand-edit one row using load_verification so dtypes are correct.
    df = v.load_verification(out)
    df.loc[df["verification_id"] == "google:g1", "status"] = "confirmed"
    df.loc[df["verification_id"] == "google:g1", "reviewer"] = "MB"
    df.to_csv(out, index=False)

    # Re-run: no new places, the edited row should survive
    path, audit = v.update_verification_csv(out, google_gdf=_google_fixture(), osm_gdf=_osm_fixture())
    df2 = v.load_verification(path)
    g1 = df2.loc[df2["verification_id"] == "google:g1"].iloc[0]
    assert g1["status"] == "confirmed"
    assert g1["reviewer"] == "MB"
    assert audit["rows_new_appended"] == 0


def test_update_verification_appends_new_places(tmp_path: Path) -> None:
    out = tmp_path / "v.csv"
    v.update_verification_csv(out, google_gdf=_google_fixture())

    google_v2 = pd.concat(
        [
            _google_fixture(),
            gpd.GeoDataFrame(
                {
                    "place_id": ["g3"],
                    "name": ["NewBie Tan"],
                    "address": ["44 Park Rd"],
                    "lad_code": ["E08000022"],
                    "imd_quintile": [2],
                },
                geometry=[Point(430_000, 568_000)],
                crs="EPSG:27700",
            ),
        ],
        ignore_index=True,
    )
    google_v2 = gpd.GeoDataFrame(google_v2, geometry="geometry", crs="EPSG:27700")
    _, audit = v.update_verification_csv(out, google_gdf=google_v2)
    assert audit["rows_new_appended"] == 1
    df = pd.read_csv(out)
    assert (df["verification_id"] == "google:g3").any()
    assert (df.loc[df["verification_id"] == "google:g3", "status"] == "pending").all()


def test_load_verification_rejects_unknown_status(tmp_path: Path) -> None:
    out = tmp_path / "v.csv"
    v.update_verification_csv(out, google_gdf=_google_fixture())
    df = pd.read_csv(out)
    df.loc[0, "status"] = "garbage"
    df.to_csv(out, index=False)
    try:
        v.load_verification(out)
    except RuntimeError as exc:
        assert "Unknown verification status" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_apply_verification_filters_to_keep_statuses(tmp_path: Path) -> None:
    out = tmp_path / "v.csv"
    v.update_verification_csv(out, google_gdf=_google_fixture())
    df = pd.read_csv(out)
    df.loc[df["source_id"] == "g1", "status"] = "confirmed"
    df.loc[df["source_id"] == "g2", "status"] = "rejected"
    df.to_csv(out, index=False)

    ver = v.load_verification(out)
    kept, audit = v.apply_verification(
        _google_fixture(),
        ver,
        source="google",
        source_id_col="place_id",
        audit_dir=tmp_path / "logs",
    )
    assert kept["place_id"].tolist() == ["g1"]
    assert audit["n_kept"] == 1
    # Audit json should have been written
    logs = list((tmp_path / "logs").glob("verification_apply_google_*.json"))
    assert len(logs) == 1
