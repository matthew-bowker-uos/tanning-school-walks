"""Synthetic-fixture tests for ``schools_sunbeds.schools``.

The fixture is a small in-memory CSV that mimics the GIAS edubasealldata
column layout for half a dozen rows covering every filter outcome we care
about.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from schools_sunbeds import schools

# ---------------------------------------------------------------------------
# Fixture: 8 rows, hand-constructed for filter-coverage.
#
# Expected after filter_in_scope:
#   - urn 100001 (Newcastle, Primary, LA-maintained, Open)               KEPT
#   - urn 100002 (County Durham, Secondary, Academy, Open)               KEPT
#   - urn 100003 (Sunderland, Special, Special schools, Open)            KEPT
#   - urn 100004 (Newcastle, Primary, LA-maintained, *Closed*)           dropped
#   - urn 100005 (Newcastle, Primary, *Independent*, Open)               dropped
#   - urn 100006 (Manchester, Primary, LA-maintained, Open) — outside NE  dropped
#   - urn 100007 (Newcastle, *Sixth-form*, Academy, Open)                dropped
#   - urn 100008 (Sunderland, *Not applicable*, Special schools, Open)   KEPT


_RAW_HEADER = (
    "URN,EstablishmentName,LA (code),LA (name),"
    "EstablishmentTypeGroup (name),TypeOfEstablishment (name),"
    "PhaseOfEducation (name),EstablishmentStatus (name),"
    "OpenDate,CloseDate,Easting,Northing,Postcode,"
    "NumberOfPupils,StatutoryLowAge,StatutoryHighAge,Gender (name)"
)

_RAW_ROWS = (
    '100001,"Walker Primary",391,"Newcastle upon Tyne",'
    '"Local authority maintained schools","Community school",'
    '"Primary","Open",,,427000,565000,"NE6 1AB",250,4,11,"Mixed"',
    '100002,"Durham Academy",840,"County Durham",'
    '"Academies","Academy converter mainstream",'
    '"Secondary","Open",,,425000,545000,"DH1 1AB",1200,11,18,"Mixed"',
    '100003,"Sunderland Special",394,"Sunderland",'
    '"Special schools","Community special school",'
    '"Special","Open",,,438000,558000,"SR2 1AB",80,4,18,"Mixed"',
    '100004,"Walker Closed",391,"Newcastle upon Tyne",'
    '"Local authority maintained schools","Community school",'
    '"Primary","Closed",,,428000,565500,"NE6 2AB",0,4,11,"Mixed"',
    '100005,"Newcastle Independent",391,"Newcastle upon Tyne",'
    '"Independent schools","Other independent school",'
    '"Primary","Open",,,425500,564500,"NE2 1AB",100,4,11,"Mixed"',
    '100006,"Manchester Primary",352,"Manchester",'
    '"Local authority maintained schools","Community school",'
    '"Primary","Open",,,384000,398000,"M1 1AB",300,4,11,"Mixed"',
    '100007,"Newcastle Sixth Form",391,"Newcastle upon Tyne",'
    '"Academies","Academy 16-19 converter",'
    '"16 plus","Open",,,427500,564000,"NE1 1AB",450,16,19,"Mixed"',
    '100008,"Sunderland Special — phase NA",394,"Sunderland",'
    '"Special schools","Community special school",'
    '"Not applicable","Open",,,438500,558500,"SR3 1AB",60,4,18,"Mixed"',
)


@pytest.fixture
def gias_df() -> pd.DataFrame:
    csv = "\n".join((_RAW_HEADER, *_RAW_ROWS, ""))
    raw = pd.read_csv(io.StringIO(csv), dtype={"URN": "Int64"})
    return raw.rename(columns={old: new for old, new in schools._GIAS_COLUMN_RENAMES.items() if old in raw.columns})


def test_filter_keeps_only_scoped_rows(gias_df: pd.DataFrame) -> None:
    kept, audit = schools.filter_in_scope(gias_df)
    assert audit["initial_n"] == 8
    assert audit["after_la_n"] == 7  # Manchester dropped
    # phase OR type-exempt: 16-plus dropped, but NA-phase Special school survives
    assert audit["after_phase_or_exempt_n"] == 6
    assert audit["after_type_n"] == 5  # Independent dropped
    assert audit["after_status_n"] == 4  # Closed dropped
    assert sorted(kept["urn"].tolist()) == [100001, 100002, 100003, 100008]


def test_filter_assigns_ons_la_codes(gias_df: pd.DataFrame) -> None:
    kept, _ = schools.filter_in_scope(gias_df)
    expected = {
        100001: "E08000021",  # Newcastle
        100002: "E06000047",  # County Durham
        100003: "E08000024",  # Sunderland
        100008: "E08000024",  # Sunderland special, phase NA
    }
    for urn, ons in expected.items():
        assert kept.loc[kept["urn"] == urn, "la_code_ons"].iloc[0] == ons


def test_sensitivity_layer_holds_excluded_ne_rows(gias_df: pd.DataFrame) -> None:
    sens = schools.filter_sensitivity_layer(gias_df)
    assert set(sens["urn"]) == {100005, 100007}  # Independent + 16-plus
    assert 100006 not in set(sens["urn"])  # Manchester is not NE
    assert 100008 not in set(sens["urn"])  # Special school w/ phase NA is primary cohort


def test_to_geodataframe_drops_missing_coords(gias_df: pd.DataFrame) -> None:
    kept, _ = schools.filter_in_scope(gias_df)
    n_before = len(kept)
    kept.loc[kept["urn"] == 100002, "northing"] = pd.NA
    gdf = schools.to_geodataframe(kept)
    assert len(gdf) == n_before - 1
    assert gdf.crs.to_string() == "EPSG:27700"


def test_audit_basic_flags_duplicates_and_outliers(gias_df: pd.DataFrame) -> None:
    kept, _ = schools.filter_in_scope(gias_df)
    duplicated = pd.concat([kept, kept.iloc[[0]]], ignore_index=True)
    duplicated.loc[duplicated.index[-1], "easting"] = 100  # outside NE bbox
    gdf = schools.to_geodataframe(duplicated)
    audit = schools.audit_basic(gdf)
    assert 100001 in audit["duplicate_urns"]
    assert audit["n_outside_ne_bbox"] == 1
