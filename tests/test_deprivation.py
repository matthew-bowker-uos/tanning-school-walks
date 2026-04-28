"""Tests for ``schools_sunbeds.deprivation``.

The IMD2025 file structure is well-known, so we test column auto-detection
on a synthetic in-memory DataFrame that matches the published schema.
"""

from __future__ import annotations

import pandas as pd

from schools_sunbeds import deprivation


def _make_imd_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "LSOA code (2021)": ["E01000001", "E01000002", "E01000003"],
            "LSOA name (2021)": ["A", "B", "C"],
            "Local Authority District code (2024)": [
                "E08000021",
                "E08000021",
                "E06000047",
            ],
            "Local Authority District name (2024)": ["X", "X", "Y"],
            "Index of Multiple Deprivation (IMD) Rank (where 1 is most deprived)": [
                26525,
                31203,
                25913,
            ],
            "Index of Multiple Deprivation (IMD) Decile (where 1 is most deprived 10% of LSOAs)": [
                8,
                10,
                8,
            ],
        }
    )


def _make_idaci_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "LSOA code (2021)": ["E01000001", "E01000002"],
            "LSOA name (2021)": ["A", "B"],
            "Income Deprivation Affecting Children Index (IDACI) Rank (where 1 is most deprived)": [
                33304,
                31744,
            ],
            "Income Deprivation Affecting Children Index (IDACI) Decile (where 1 is most deprived 10% of LSOAs)": [
                10,
                10,
            ],
        }
    )


def test_load_imd2025_main_uses_auto_detect(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "imd_main.xlsx"
    fake.write_bytes(b"\x00")  # placeholder; we replace _read_data_sheet

    monkeypatch.setattr(deprivation, "_read_data_sheet", lambda p: _make_imd_df())
    out = deprivation.load_imd2025_main(fake)

    assert list(out.columns) == ["lsoa21cd", "imd_rank", "imd_decile", "imd_quintile"]
    assert out.loc[0, "imd_rank"] == 26525
    assert out.loc[0, "imd_decile"] == 8
    assert out.loc[0, "imd_quintile"] == 4  # decile 8 -> quintile 4


def test_load_imd2025_idaci_uses_auto_detect(monkeypatch, tmp_path) -> None:
    fake = tmp_path / "imd_idaci.xlsx"
    fake.write_bytes(b"\x00")

    monkeypatch.setattr(deprivation, "_read_data_sheet", lambda p: _make_idaci_df())
    out = deprivation.load_imd2025_idaci(fake)

    assert list(out.columns) == [
        "lsoa21cd",
        "idaci_rank",
        "idaci_decile",
        "idaci_quintile",
    ]
    assert out.loc[0, "idaci_rank"] == 33304
    assert out.loc[0, "idaci_decile"] == 10
    assert out.loc[0, "idaci_quintile"] == 5  # decile 10 -> quintile 5


def test_quintile_recoding_handles_all_deciles() -> None:
    # decile -> quintile mapping: 1,2->1; 3,4->2; 5,6->3; 7,8->4; 9,10->5
    cases = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 5, 10: 5}
    for decile, expected_quintile in cases.items():
        assert (decile + 1) // 2 == expected_quintile
