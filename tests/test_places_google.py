"""Tests for ``places_google`` covering grid construction and deduplication.

The HTTP client is integration-tested via the notebook; here we cover the
non-network logic.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from schools_sunbeds import places_google as pg


def test_grid_bbox_yields_cells_covering_region() -> None:
    bbox = (-1.65, 54.97, -1.55, 55.02)  # ~5–7 km wide
    cells = list(pg.grid_bbox(bbox, cell_size_m=2_000))
    assert len(cells) >= 4
    for c in cells:
        x_min, y_min, x_max, y_max = c.bbox_bng
        assert x_max > x_min and y_max > y_min


def test_filter_cells_to_polygon() -> None:
    cells = list(pg.grid_bbox((-1.62, 54.98, -1.58, 55.00), cell_size_m=1_000))
    poly = gpd.GeoDataFrame(
        {"geometry": [box(*cells[0].bbox_bng)]},
        crs="EPSG:27700",
    )
    kept = pg.filter_cells_to_polygon(cells, poly)
    assert kept and all(
        box(*c.bbox_bng).intersects(poly.iloc[0].geometry) for c in kept
    )


def test_parse_jsonl_to_dataframe(tmp_path: Path) -> None:
    jsonl = tmp_path / "raw.jsonl"
    jsonl.write_text(
        "\n".join(
            json.dumps(env)
            for env in [
                {
                    "cell_i": 0,
                    "cell_j": 0,
                    "bbox_wgs84": [-1.6, 54.97, -1.5, 55.02],
                    "text_query": "tanning salon",
                    "n_calls": 1,
                    "places": [
                        {
                            "id": "abc",
                            "displayName": {"text": "Salon A"},
                            "formattedAddress": "1 High St, Newcastle",
                            "location": {"latitude": 54.97, "longitude": -1.61},
                            "types": ["tanning_studio"],
                        }
                    ],
                },
                {
                    "cell_i": 0,
                    "cell_j": 1,
                    "bbox_wgs84": [-1.6, 55.02, -1.5, 55.07],
                    "text_query": "sunbed",
                    "n_calls": 1,
                    "places": [
                        {
                            "id": "abc",  # same place id, different cell + query
                            "displayName": {"text": "Salon A"},
                            "formattedAddress": "1 High St, Newcastle",
                            "location": {"latitude": 54.97, "longitude": -1.61},
                            "types": ["beauty_salon"],
                        },
                        {
                            "id": "def",
                            "displayName": {"text": "Salon B"},
                            "formattedAddress": "5 Park Ln",
                            "location": {"latitude": 54.99, "longitude": -1.59},
                            "types": ["tanning_studio"],
                        },
                    ],
                },
            ]
        )
    )
    df = pg.parse_jsonl_to_dataframe(jsonl)
    assert len(df) == 3  # two abc rows (different envelopes) + one def row

    deduped = pg.deduplicate_places(df)
    assert len(deduped) == 2
    assert deduped.crs.to_string() == "EPSG:27700"
    abc_row = deduped.loc[deduped["place_id"] == "abc"].iloc[0]
    # query terms collapsed into semicolon-delimited string
    assert "tanning salon" in abc_row["query_terms"]
    assert "sunbed" in abc_row["query_terms"]
    # types union
    assert "tanning_studio" in abc_row["types"]
    assert "beauty_salon" in abc_row["types"]
