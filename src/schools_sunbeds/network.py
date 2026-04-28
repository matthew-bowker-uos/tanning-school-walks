"""Walking-network construction for the NE region.

Downloads the Geofabrik North East England OSM PBF (small, ~50 MB), parses
walkable highways with pyosmium, computes geodesic edge lengths, and builds
a :class:`pandana.Network` ready for batch shortest-path queries.

DEC-006 made pandana the primary engine because contraction-hierarchy
shortest paths over ~10k–50k OD pairs run in seconds. DEC-008 calls for
pinning the OSM extract by snapshot date + SHA256, which is enforced via
the standard ``audit.register_raw_file`` flow once the PBF lands on disk.

The walkable-highway tag set is the conservative one used in spec §6 / the
Burgoine fast-food work: footway, pedestrian, residential, tertiary, etc.
Motorways and motorway links are excluded (no walking).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import osmium
import pandas as pd
import requests
from pyproj import Geod
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# Geofabrik does not publish a standalone NE-England subregion PBF, and on
# 2026-04-28 the england-latest.osm.pbf URL was returning a cached HTML
# error from Geofabrik's CDN proxy. We use the Great Britain PBF instead
# (~2.1 GB, served correctly), then bbox-filter ways during parsing so
# only NE walking edges land in memory.
GEOFABRIK_GB_PBF_URL = (
    "https://download.geofabrik.de/europe/great-britain-latest.osm.pbf"
)

# Walkable highway types. Anything not in this set is dropped from the
# walking graph. Motorways / motorway_link / construction / proposed are
# excluded explicitly.
WALK_HIGHWAYS: frozenset[str] = frozenset(
    {
        "footway",
        "pedestrian",
        "path",
        "steps",
        "living_street",
        "residential",
        "service",
        "unclassified",
        "tertiary",
        "tertiary_link",
        "secondary",
        "secondary_link",
        "primary",
        "primary_link",
        "trunk",
        "trunk_link",
        "cycleway",
        "track",
    }
)

# Highways where ``foot=no`` would override our default inclusion.
FOOT_OVERRIDE_NEGATIVE: frozenset[str] = frozenset({"no", "private"})


# ---------------------------------------------------------------------------
# Download


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
def _http_get_streaming(url: str, target: Path, timeout: int = 600) -> None:
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with target.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=1 << 20):
                fh.write(chunk)


def fetch_geofabrik_gb_pbf(
    target_dir: Path, *, url: str = GEOFABRIK_GB_PBF_URL
) -> Path:
    """Download the Geofabrik Great Britain OSM PBF if not already present.

    File is ~2.1 GB; downloaded once per snapshot. The PBF is read-only
    after first download; ``audit.register_raw_file`` will chmod 444.
    """

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "great-britain-latest.osm.pbf"
    if out.exists() and out.stat().st_size > 100_000:  # any non-trivial size
        log.info("Using existing PBF at %s (%d bytes)", out, out.stat().st_size)
        return out
    log.info("Downloading %s (~1.5 GB) ...", url)
    _http_get_streaming(url, out)
    log.info("Downloaded %s (%d bytes)", out, out.stat().st_size)
    return out


# ---------------------------------------------------------------------------
# Parser


@dataclass
class WalkingNetworkData:
    """Parsed nodes and edges for a walking network."""

    nodes: pd.DataFrame  # osm_id, x (lon), y (lat)
    edges: pd.DataFrame  # from_id, to_id, length_m


class _WalkingHandler(osmium.SimpleHandler):
    """SimpleHandler that captures walkable highway ways and their nodes.

    PBF order is nodes-then-ways, so we record node coords as they stream
    past, then for each way we emit edges only when both endpoint nodes
    are inside ``bbox_wgs84``. This keeps memory bounded when parsing the
    full England PBF.
    """

    def __init__(
        self,
        bbox_wgs84: tuple[float, float, float, float] | None = None,
    ) -> None:
        super().__init__()
        self._node_coords: dict[int, tuple[float, float]] = {}
        self._raw_edges: list[tuple[int, int]] = []
        self._bbox = bbox_wgs84

    def _in_bbox(self, lon: float, lat: float) -> bool:
        if self._bbox is None:
            return True
        west, south, east, north = self._bbox
        return west <= lon <= east and south <= lat <= north

    def node(self, n) -> None:  # type: ignore[no-untyped-def]
        if not n.location.valid():
            return
        lon, lat = n.location.lon, n.location.lat
        if self._in_bbox(lon, lat):
            self._node_coords[n.id] = (lon, lat)

    def way(self, w) -> None:  # type: ignore[no-untyped-def]
        tags = {t.k: t.v for t in w.tags}
        highway = tags.get("highway")
        if highway not in WALK_HIGHWAYS:
            return
        if tags.get("foot") in FOOT_OVERRIDE_NEGATIVE:
            return
        if tags.get("access") in {"private", "no"}:
            return
        ids = [n.ref for n in w.nodes]
        # Drop ways whose endpoints we did not see (i.e. fully outside bbox).
        # Edges can still be emitted if at least the endpoints used were
        # captured during the node pass.
        for a, b in zip(ids, ids[1:]):
            if a in self._node_coords and b in self._node_coords:
                self._raw_edges.append((a, b))

    def to_data(self) -> WalkingNetworkData:
        used_node_ids = {a for a, _ in self._raw_edges} | {b for _, b in self._raw_edges}
        coords = {nid: self._node_coords[nid] for nid in used_node_ids if nid in self._node_coords}

        nodes_df = pd.DataFrame(
            [(nid, lon, lat) for nid, (lon, lat) in coords.items()],
            columns=["osm_id", "x", "y"],
        ).set_index("osm_id")

        # Drop edges where either endpoint is missing coordinates
        edges = [
            (a, b)
            for a, b in self._raw_edges
            if a in coords and b in coords
        ]
        edges_df = pd.DataFrame(edges, columns=["from_id", "to_id"])

        # Compute edge length in metres on the WGS84 ellipsoid
        geod = Geod(ellps="WGS84")
        a_x = coords_arr_x = np.fromiter((coords[a][0] for a, _ in edges), dtype=float, count=len(edges))
        a_y = np.fromiter((coords[a][1] for a, _ in edges), dtype=float, count=len(edges))
        b_x = np.fromiter((coords[b][0] for _, b in edges), dtype=float, count=len(edges))
        b_y = np.fromiter((coords[b][1] for _, b in edges), dtype=float, count=len(edges))
        _, _, length_m = geod.inv(a_x, a_y, b_x, b_y)
        edges_df["length_m"] = length_m
        # Drop pathological zero-length edges and self-loops
        edges_df = edges_df.loc[
            (edges_df["from_id"] != edges_df["to_id"]) & (edges_df["length_m"] > 0)
        ].reset_index(drop=True)

        return WalkingNetworkData(nodes=nodes_df, edges=edges_df)


def clip_walking_network_to_polygon(
    data: WalkingNetworkData,
    polygon_gdf,
    *,
    polygon_bng: bool = True,
    buffer_m: float = 2_000.0,
) -> WalkingNetworkData:
    """Restrict ``data`` to nodes whose lat/lon falls inside ``polygon_gdf``.

    The polygon is buffered by ``buffer_m`` first (in BNG metres) so that
    walking routes near the polygon boundary aren't truncated. Edges with
    either endpoint outside the buffered polygon are dropped.

    ``polygon_bng`` defaults True, meaning the input polygon is in
    EPSG:27700 — typical for our LAD layer.
    """

    import geopandas as gpd
    from shapely.geometry import Point

    if polygon_bng:
        polys = polygon_gdf.to_crs("EPSG:27700") if polygon_gdf.crs is None or polygon_gdf.crs.to_epsg() != 27700 else polygon_gdf
    else:
        polys = polygon_gdf.to_crs("EPSG:27700")
    union = polys.geometry.union_all().buffer(buffer_m)

    # Project nodes to BNG and check containment
    nodes_wgs = gpd.GeoDataFrame(
        data.nodes.reset_index(),
        geometry=gpd.points_from_xy(data.nodes["x"], data.nodes["y"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:27700")
    inside = nodes_wgs.geometry.within(union)
    keep_node_ids = set(nodes_wgs.loc[inside, "osm_id"])

    nodes_kept = data.nodes.loc[data.nodes.index.isin(keep_node_ids)]
    edges_kept = data.edges.loc[
        data.edges["from_id"].isin(keep_node_ids)
        & data.edges["to_id"].isin(keep_node_ids)
    ].reset_index(drop=True)

    log.info(
        "Polygon-clip: %d -> %d nodes (%.1f%%), %d -> %d edges (%.1f%%)",
        len(data.nodes),
        len(nodes_kept),
        100 * len(nodes_kept) / max(len(data.nodes), 1),
        len(data.edges),
        len(edges_kept),
        100 * len(edges_kept) / max(len(data.edges), 1),
    )
    return WalkingNetworkData(nodes=nodes_kept, edges=edges_kept)


def parse_walking_network(
    pbf_path: Path,
    *,
    bbox_wgs84: tuple[float, float, float, float] | None = None,
) -> WalkingNetworkData:
    """Parse a PBF and return tidy nodes + edges DataFrames for a walking graph.

    If ``bbox_wgs84`` is supplied, only nodes inside the box (and edges
    whose endpoints are both inside) are kept — useful when feeding a
    nationwide PBF and only needing a region.
    """

    handler = _WalkingHandler(bbox_wgs84=bbox_wgs84)
    handler.apply_file(str(pbf_path), locations=False)
    data = handler.to_data()
    log.info(
        "Walking network: %d nodes, %d edges (median length %.1f m)",
        len(data.nodes),
        len(data.edges),
        float(data.edges["length_m"].median()),
    )
    return data


# ---------------------------------------------------------------------------
# Pandana Network construction


def build_pandana_network(data: WalkingNetworkData):
    """Build a :class:`pandana.Network` from parsed walking data.

    Pandana expects integer-indexed nodes; OSM ids are already integers, so
    we pass them through. The returned object can be used directly for
    nearest-POI queries and shortest-path lengths.
    """

    import pandana

    net = pandana.Network(
        data.nodes["x"].astype("float64"),
        data.nodes["y"].astype("float64"),
        data.edges["from_id"].astype("int64"),
        data.edges["to_id"].astype("int64"),
        data.edges[["length_m"]].astype("float64"),
    )
    return net


def save_walking_network(data: WalkingNetworkData, target_dir: Path) -> tuple[Path, Path]:
    """Save the parsed nodes + edges as parquet for fast reload."""

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = target_dir / "walk_nodes.parquet"
    edges_path = target_dir / "walk_edges.parquet"
    data.nodes.reset_index().to_parquet(nodes_path, index=False)
    data.edges.to_parquet(edges_path, index=False)
    return nodes_path, edges_path


def load_walking_network(target_dir: Path) -> WalkingNetworkData:
    target_dir = Path(target_dir)
    nodes = pd.read_parquet(target_dir / "walk_nodes.parquet").set_index("osm_id")
    edges = pd.read_parquet(target_dir / "walk_edges.parquet")
    return WalkingNetworkData(nodes=nodes, edges=edges)


__all__ = [
    "GEOFABRIK_GB_PBF_URL",
    "WALK_HIGHWAYS",
    "WalkingNetworkData",
    "build_pandana_network",
    "clip_walking_network_to_polygon",
    "fetch_geofabrik_gb_pbf",
    "load_walking_network",
    "parse_walking_network",
    "save_walking_network",
]
