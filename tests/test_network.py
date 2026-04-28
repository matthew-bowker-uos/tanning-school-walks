"""Tests for the network module that don't need a live PBF.

Live-data verification is via notebook 06.
"""

from __future__ import annotations

import pandas as pd

from schools_sunbeds import network as nw


def test_walk_highways_excludes_motorway() -> None:
    assert "motorway" not in nw.WALK_HIGHWAYS
    assert "motorway_link" not in nw.WALK_HIGHWAYS


def test_walk_highways_includes_pedestrian_and_residential() -> None:
    for must_have in ("footway", "pedestrian", "path", "residential", "cycleway"):
        assert must_have in nw.WALK_HIGHWAYS, must_have


def test_walking_network_data_holds_dataframes() -> None:
    nodes = pd.DataFrame({"x": [0.0, 1.0], "y": [0.0, 1.0]}, index=[1, 2])
    nodes.index.name = "osm_id"
    edges = pd.DataFrame({"from_id": [1], "to_id": [2], "length_m": [100.0]})
    data = nw.WalkingNetworkData(nodes=nodes, edges=edges)
    assert len(data.nodes) == 2
    assert len(data.edges) == 1
    assert data.edges.iloc[0]["length_m"] == 100.0
