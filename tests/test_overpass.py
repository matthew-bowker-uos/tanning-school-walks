"""Tests for the Overpass salon module."""

from __future__ import annotations

from schools_sunbeds import overpass


def test_build_overpass_query_includes_all_three_filters() -> None:
    q = overpass.build_overpass_query()
    assert '["leisure"="tanning_salon"]' in q
    assert '["shop"="solarium"]' in q
    assert '["shop"="beauty"]["beauty"="tanning"]' in q
    # Both nodes and ways for each
    assert q.count("node[") == 3
    assert q.count("way[") == 3
    # Bbox order is south,west,north,east per Overpass convention
    assert "out center" in q


def test_parse_overpass_response_classifies_tag_match() -> None:
    payload = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 54.97,
                "lon": -1.61,
                "tags": {"leisure": "tanning_salon", "name": "A"},
            },
            {
                "type": "node",
                "id": 2,
                "lat": 54.97,
                "lon": -1.62,
                "tags": {"shop": "solarium", "name": "B"},
            },
            {
                "type": "way",
                "id": 3,
                "center": {"lat": 54.97, "lon": -1.63},
                "tags": {"shop": "beauty", "beauty": "tanning", "name": "C"},
            },
            {
                "type": "node",
                "id": 4,
                "lat": 54.97,
                "lon": -1.64,
                "tags": {"shop": "convenience"},  # not a salon — should still appear with 'other'
            },
        ]
    }
    df = overpass.parse_overpass_response(payload)
    by_id = df.set_index("osm_id")["tag_match"]
    assert by_id[1] == "leisure=tanning_salon"
    assert by_id[2] == "shop=solarium"
    assert by_id[3] == "shop=beauty;beauty=tanning"
    assert by_id[4] == "other"


def test_parse_skips_geometryless_features() -> None:
    payload = {"elements": [{"type": "node", "id": 1, "tags": {"shop": "solarium"}}]}
    df = overpass.parse_overpass_response(payload)
    assert df.empty


def test_to_geodataframe_projects_to_bng() -> None:
    payload = {
        "elements": [
            {"type": "node", "id": 1, "lat": 54.97, "lon": -1.61, "tags": {"shop": "solarium"}}
        ]
    }
    df = overpass.parse_overpass_response(payload)
    gdf = overpass.to_geodataframe(df)
    assert gdf.crs.to_string() == "EPSG:27700"
    assert len(gdf) == 1
