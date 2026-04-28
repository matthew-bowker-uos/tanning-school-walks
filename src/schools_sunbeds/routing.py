"""Walking-route geometry and buffer construction.

Reads the catchment assignments produced by Stage 4b, asks the pandana
network for the shortest-path node sequence per (LSOA, school) pair,
materialises the path as a BNG LineString, and buffers it.

Two buffers are produced for each route:

- 50 m (the primary route-buffer width per spec §6.3)
- 100 m (the sensitivity width, listed as #6 in HYPOTHESES.md)

This module also produces conventional school-centred Euclidean buffers
at 400 / 800 / 1600 m for the H2 comparator. Network service-area
school buffers are NOT implemented here — for the H2 contrast the
Euclidean buffer is the conventional measure and is the more defensible
comparator. Adding network-distance school buffers is a future sensitivity
that would require a per-school Dijkstra over the 1.5 M-node graph.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString

from schools_sunbeds.config import (
    BUFFER_DISTANCES_M,
    BUFFER_DISTANCES_SENSITIVITY_M,
    CRS_BNG,
    CRS_WGS84,
    ROUTE_BUFFER_PRIMARY_M,
    ROUTE_BUFFER_SENSITIVITY_M,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routes


def _path_to_linestring(
    node_seq: list[int],
    node_lookup: pd.DataFrame,
) -> LineString | None:
    """Convert a sequence of pandana node IDs to a WGS84 LineString.

    ``node_lookup`` must be indexed by ``osm_id`` with ``x`` (lon) and
    ``y`` (lat) columns. Sequences shorter than 2 nodes return ``None``.
    """

    if len(node_seq) < 2:
        return None
    try:
        coords = node_lookup.loc[node_seq, ["x", "y"]].to_numpy()
    except KeyError:
        # Some path nodes not in the lookup (clipped network); drop the
        # missing ones and try again.
        seq = [n for n in node_seq if n in node_lookup.index]
        if len(seq) < 2:
            return None
        coords = node_lookup.loc[seq, ["x", "y"]].to_numpy()
    return LineString(coords)


def compute_routes(
    network,
    assignments_df: pd.DataFrame,
    pwc_gdf: gpd.GeoDataFrame,
    schools_gdf: gpd.GeoDataFrame,
    *,
    pwc_id_col: str = "lsoa21cd",
    school_id_col: str = "urn",
    walk_nodes: pd.DataFrame | None = None,
    max_length_m: float = 6_000.0,
) -> gpd.GeoDataFrame:
    """Compute walking-route LineStrings for each row in ``assignments_df``.

    ``network`` is a :class:`pandana.Network`. ``walk_nodes`` is the parsed
    nodes table (osm_id index, x/y in WGS84) used to look up coordinates;
    if not given we fall back to ``network.nodes_df``.

    Routes whose recomputed network length exceeds ``max_length_m`` are
    dropped: pandana returns very large (likely uint-underflow) lengths
    for OD pairs that span disconnected sub-graphs, even though
    ``nearest_pois`` matched them. The default 6 km absorbs the 5 km
    DEC-016 cap with slack.

    Returns a GeoDataFrame in EPSG:27700 with one row per assignment that
    produced a non-trivial path. Columns mirror ``assignments_df`` plus
    ``length_m`` (network distance) and ``geometry``.
    """

    if walk_nodes is None:
        walk_nodes = network.nodes_df  # pandana exposes nodes_df

    if pwc_gdf.crs is None or pwc_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("pwc_gdf must be in EPSG:27700")
    if schools_gdf.crs is None or schools_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("schools_gdf must be in EPSG:27700")

    df = assignments_df.copy()

    if "origin_node" in df.columns and "dest_node" in df.columns:
        # Stage 4 already snapped — use the stored nodes verbatim. Avoids
        # the inconsistency where re-snapping schools picks different
        # network nodes than catchment did, breaking the OD pair.
        log.info("Using origin/dest node ids stored on assignments_df")
    else:
        # Fallback: snap from lat/lon. Used by tests and any caller that
        # didn't go through Stage 4b.
        pwc_wgs = pwc_gdf.to_crs(CRS_WGS84)
        pwc_node = pd.Series(
            network.get_node_ids(
                pwc_wgs.geometry.x.to_numpy(), pwc_wgs.geometry.y.to_numpy()
            ).values,
            index=pwc_gdf[pwc_id_col].values,
            name="origin_node",
        )
        schools_wgs = schools_gdf.to_crs(CRS_WGS84)
        school_node = pd.Series(
            network.get_node_ids(
                schools_wgs.geometry.x.to_numpy(), schools_wgs.geometry.y.to_numpy()
            ).values,
            index=schools_gdf[school_id_col].values,
            name="dest_node",
        )
        df["origin_node"] = df[pwc_id_col].map(pwc_node)
        df["dest_node"] = df[school_id_col].map(school_node)

    valid = df["origin_node"].notna() & df["dest_node"].notna()
    df = df.loc[valid].reset_index(drop=True)

    if df.empty:
        log.warning("No valid origin/dest node pairs after snapping")
        return gpd.GeoDataFrame(df, geometry=[], crs=CRS_BNG)

    # Pandana shortest_paths returns the node sequence; shortest_path_lengths
    # returns the network distance. For the OD pairs that span disconnected
    # sub-graphs pandana returns a wrap-around length (very large value), so
    # we apply ``max_length_m`` as a sanity filter and drop them.
    log.info("Computing %d shortest paths via pandana...", len(df))
    paths = network.shortest_paths(
        df["origin_node"].astype("int64").to_numpy(),
        df["dest_node"].astype("int64").to_numpy(),
    )
    lengths = network.shortest_path_lengths(
        df["origin_node"].astype("int64").to_numpy(),
        df["dest_node"].astype("int64").to_numpy(),
    )

    geoms: list[LineString | None] = []
    for path in paths:
        geoms.append(_path_to_linestring(list(path), walk_nodes))

    df["length_m"] = lengths
    df["geometry"] = geoms

    out = gpd.GeoDataFrame(df, geometry="geometry", crs=CRS_WGS84)
    out = out.dropna(subset=["geometry"])

    n_before = len(out)
    out = out.loc[out["length_m"] <= max_length_m].reset_index(drop=True)
    n_dropped = n_before - len(out)
    if n_dropped:
        log.info(
            "Dropped %d routes whose pandana length exceeded %.0f m "
            "(disconnected sub-graphs)",
            n_dropped,
            max_length_m,
        )

    out = out.to_crs(CRS_BNG)
    log.info(
        "Materialised %d route LineStrings (mean length %.0f m)",
        len(out),
        float(out["length_m"].mean()) if len(out) else float("nan"),
    )
    return out


# ---------------------------------------------------------------------------
# Buffers


def buffer_routes(
    routes_gdf: gpd.GeoDataFrame,
    *,
    buffer_m: float = ROUTE_BUFFER_PRIMARY_M,
) -> gpd.GeoDataFrame:
    """Buffer route LineStrings by ``buffer_m`` metres (BNG).

    Output is a GeoDataFrame with the same attributes as ``routes_gdf``
    and a new buffered polygon ``geometry``. The original ``geometry``
    column is dropped to keep the schema clean for downstream spatial joins.
    """

    if routes_gdf.crs is None or routes_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("routes_gdf must be in EPSG:27700")

    out = routes_gdf.copy()
    out["geometry"] = out.geometry.buffer(buffer_m, cap_style=2, join_style=2)
    return out


def school_euclidean_buffers(
    schools_gdf: gpd.GeoDataFrame,
    *,
    distances_m: Iterable[int] = BUFFER_DISTANCES_M,
    school_id_col: str = "urn",
) -> gpd.GeoDataFrame:
    """Long-format Euclidean school-centred buffers at multiple distances.

    Returns one row per ``(urn, distance_m)`` with a polygon ``geometry``
    and the original school attributes (minus geometry). Distances default
    to spec §6.2 (400, 800, 1600 m).
    """

    if schools_gdf.crs is None or schools_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("schools_gdf must be in EPSG:27700")

    distances_m = list(distances_m)
    frames: list[gpd.GeoDataFrame] = []
    for d in distances_m:
        sub = schools_gdf.copy()
        sub["distance_m"] = d
        sub["geometry"] = sub.geometry.buffer(d)
        frames.append(sub)
    out = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=CRS_BNG
    )
    return out


__all__ = [
    "BUFFER_DISTANCES_M",
    "BUFFER_DISTANCES_SENSITIVITY_M",
    "ROUTE_BUFFER_PRIMARY_M",
    "ROUTE_BUFFER_SENSITIVITY_M",
    "buffer_routes",
    "compute_routes",
    "school_euclidean_buffers",
]
