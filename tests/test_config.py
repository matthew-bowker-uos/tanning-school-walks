"""Sanity checks on the project-wide config module."""

from __future__ import annotations

from schools_sunbeds import config


def test_la_codes_are_complete_and_unique() -> None:
    assert len(config.LA_CODES_NE) == 12
    assert len(set(config.LA_CODES_NE)) == 12
    assert set(config.LA_CODES_NE) == set(config.LA_NAMES_NE)


def test_buffer_distances_are_in_metres_and_ascending() -> None:
    assert config.BUFFER_DISTANCES_M == (400, 800, 1600)
    sens = config.BUFFER_DISTANCES_SENSITIVITY_M
    assert list(sens) == sorted(sens)
    assert len(set(sens)) == len(sens)


def test_catchment_caps_match_dec_010() -> None:
    assert config.CATCHMENT_CAP_M["primary"] == 2000
    assert config.CATCHMENT_CAP_M["secondary"] == 5000
    assert config.CATCHMENT_CAP_M["special"] == 10000


def test_crs_constants() -> None:
    assert config.CRS_BNG == "EPSG:27700"
    assert config.CRS_WGS84 == "EPSG:4326"
