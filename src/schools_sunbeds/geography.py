"""Acquisition and assembly of the geography layers used downstream.

Layers built here:

- LSOA21 boundaries (ONS, BGC: Generalised Clipped) for the 12 NE LAs.
- LAD24 boundaries (ONS, BFE: Boundary Full Extent) for the 12 NE LAs.
- LSOA21 population-weighted centroids (ONS).
- Schools point-in-polygon validation against LA polygons (deferred from
  Stage 1 per the plan).

Auto-fetch uses ONS Open Geography Portal ArcGIS FeatureServer queries with
pagination. The default service URLs are the latest known stable layers as
of 2026-04-28; if ONS releases a new version, override via the function
parameter rather than editing this file. URLs that 404 raise with a clear
instruction pointing at https://geoportal.statistics.gov.uk/ .
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from schools_sunbeds.config import CRS_BNG, LA_CODES_NE

log = logging.getLogger(__name__)

# Known stable ONS Open Geography Portal feature services. Override via the
# fetch_*_ne(feature_server=...) parameter if a newer version is published.
ONS_LSOA21_BGC_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/ArcGIS/rest/services/"
    "Lower_layer_Super_Output_Areas_December_2021_Boundaries_EW_BGC_V5/"
    "FeatureServer/0"
)
ONS_LAD24_BFE_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/ArcGIS/rest/services/"
    "Local_Authority_Districts_December_2024_Boundaries_UK_BFE/FeatureServer/0"
)
ONS_LSOA21_PWC_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/ArcGIS/rest/services/"
    "LSOA_PopCentroids_EW_2021_V4/FeatureServer/0"
)


# ---------------------------------------------------------------------------
# ArcGIS FeatureServer paging


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _query_page(base_url: str, params: dict[str, str], timeout: int) -> dict[str, Any]:
    r = requests.get(f"{base_url}/query", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def query_arcgis_geojson(
    base_url: str,
    *,
    where: str = "1=1",
    out_fields: str = "*",
    out_sr: int | None = 27700,
    page_size: int = 1000,
    timeout: int = 90,
    bbox: tuple[float, float, float, float] | None = None,
    bbox_sr: int = 27700,
) -> dict[str, Any]:
    """Page an ArcGIS FeatureServer query and return a single GeoJSON dict.

    ``base_url`` should end at ``/FeatureServer/<layerId>`` (no ``/query``).
    Geometry comes back in ``out_sr`` (default EPSG:27700). The function
    keeps requesting until a page returns fewer than ``page_size`` features.

    If ``bbox`` is given as ``(xmin, ymin, xmax, ymax)`` the request adds a
    spatial filter (``esriSpatialRelIntersects``) so the server returns only
    features whose geometry intersects the envelope. ``bbox_sr`` is the SR
    of the envelope (default 27700).
    """

    features: list[dict[str, Any]] = []
    offset = 0

    while True:
        params: dict[str, str] = {
            "where": where,
            "outFields": out_fields,
            "f": "geojson",
            "resultOffset": str(offset),
            "resultRecordCount": str(page_size),
            "returnGeometry": "true",
        }
        if out_sr is not None:
            params["outSR"] = str(out_sr)
        if bbox is not None:
            xmin, ymin, xmax, ymax = bbox
            params["geometry"] = json.dumps(
                {
                    "xmin": xmin,
                    "ymin": ymin,
                    "xmax": xmax,
                    "ymax": ymax,
                    "spatialReference": {"wkid": bbox_sr},
                }
            )
            params["geometryType"] = "esriGeometryEnvelope"
            params["inSR"] = str(bbox_sr)
            params["spatialRel"] = "esriSpatialRelIntersects"

        try:
            data = _query_page(base_url, params, timeout=timeout)
        except requests.HTTPError as exc:
            msg = (
                f"ArcGIS FeatureServer query failed for {base_url} "
                f"(HTTP {exc.response.status_code}). The layer may have been "
                "renamed or versioned. Find the current service URL at "
                "https://geoportal.statistics.gov.uk/ and pass it explicitly."
            )
            raise RuntimeError(msg) from exc

        page = data.get("features", [])
        features.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    return {"type": "FeatureCollection", "features": features}


# Loose BNG bbox enclosing the 12 NE LAs, with margin. Used to spatially
# pre-filter the national LSOA21 boundary download. Same shape as
# ``schools.BBOX_BNG_NE`` but slightly wider to absorb LSOA edges that
# straddle the NE/Yorkshire boundary.
BBOX_BNG_NE_GEO: tuple[float, float, float, float] = (340_000, 470_000, 490_000, 680_000)


def _save_geojson(data: dict[str, Any], target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data))
    return target


# ---------------------------------------------------------------------------
# Layer fetchers


def _quoted_in_clause(values: Iterable[str]) -> str:
    return "(" + ",".join(f"'{v}'" for v in values) + ")"


def fetch_lsoa21_boundaries_ne(
    target_dir: Path,
    *,
    feature_server: str = ONS_LSOA21_BGC_URL,
    bbox: tuple[float, float, float, float] = BBOX_BNG_NE_GEO,
) -> Path:
    """Download LSOA21 BGC polygons intersecting the NE bounding box.

    The LSOA21 BGC service (V5) does not expose a LAD code column, so we
    cannot filter on the server by LA. Instead we send a BNG envelope as a
    spatial filter and clip downstream by polygon intersection with the LAD
    layer. Net download is roughly the LSOAs in the bounding box (~3,500
    instead of ~35k EW-wide).
    """

    target_dir = Path(target_dir)
    out = target_dir / "lsoa21_ne_bgc.geojson"
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing LSOA21 download at %s", out)
        return out

    data = query_arcgis_geojson(feature_server, bbox=bbox)
    if not data["features"]:
        msg = f"No LSOA21 features returned for NE bbox from {feature_server}"
        raise RuntimeError(msg)
    return _save_geojson(data, out)


def fetch_lad_boundaries_ne(
    target_dir: Path,
    *,
    la_codes: Iterable[str] = LA_CODES_NE,
    feature_server: str = ONS_LAD24_BFE_URL,
) -> Path:
    """Download LAD BFE polygons for the 12 NE LAs."""

    target_dir = Path(target_dir)
    out = target_dir / "lad24_ne_bfe.geojson"
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing LAD download at %s", out)
        return out

    # Newer LAD layers use the latest LADyyCD column; we try LAD24CD first,
    # fall back to LAD23CD / LAD22CD if the layer is older.
    last_err: Exception | None = None
    for col in ("LAD24CD", "LAD23CD", "LAD22CD"):
        try:
            where = f"{col} IN {_quoted_in_clause(la_codes)}"
            data = query_arcgis_geojson(feature_server, where=where)
            if data["features"]:
                return _save_geojson(data, out)
        except RuntimeError as exc:
            last_err = exc

    msg = f"LAD boundary download returned no features from {feature_server} (last error: {last_err})"
    raise RuntimeError(msg)


def fetch_lsoa21_pwc(
    target_dir: Path,
    *,
    feature_server: str = ONS_LSOA21_PWC_URL,
    bbox: tuple[float, float, float, float] = BBOX_BNG_NE_GEO,
) -> Path:
    """Download LSOA21 population-weighted centroids inside the NE bbox.

    Filtered on the server by spatial envelope so we only pull NE PWCs
    (~3,500) rather than all 35k in EW. The points are total-population
    weighted, the standard ONS publication (DEC-012).
    """

    target_dir = Path(target_dir)
    out = target_dir / "lsoa21_pwc_ne.geojson"
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing LSOA21 PWC download at %s", out)
        return out

    data = query_arcgis_geojson(feature_server, bbox=bbox)
    if not data["features"]:
        msg = f"No LSOA21 PWC features returned from {feature_server}"
        raise RuntimeError(msg)
    return _save_geojson(data, out)


# ---------------------------------------------------------------------------
# Loading and harmonising


def load_lsoa21(path: Path) -> gpd.GeoDataFrame:
    """Read the LSOA21 boundary GeoJSON and normalise column names."""

    gdf = gpd.read_file(path)
    # GeoJSON RFC 7946 forbids non-WGS84 CRS declarations, so geopandas
    # auto-tags the read GeoDataFrame as EPSG:4326 even though we asked the
    # ArcGIS service for outSR=27700 and the coordinate values are BNG. We
    # *know* they are BNG, so override.
    gdf = gdf.set_crs(CRS_BNG, allow_override=True)

    rename: dict[str, str] = {}
    for col in gdf.columns:
        if col == "LSOA21CD":
            rename[col] = "lsoa21cd"
        elif col == "LSOA21NM":
            rename[col] = "lsoa21nm"
        elif col in ("LAD22CD", "LAD23CD", "LAD24CD"):
            rename[col] = "lad_code"
        elif col in ("LAD22NM", "LAD23NM", "LAD24NM"):
            rename[col] = "lad_name"
    return gdf.rename(columns=rename)


def load_lad(path: Path) -> gpd.GeoDataFrame:
    """Read the LAD boundary GeoJSON and normalise column names."""

    gdf = gpd.read_file(path)
    # GeoJSON RFC 7946 forbids non-WGS84 CRS declarations, so geopandas
    # auto-tags the read GeoDataFrame as EPSG:4326 even though we asked the
    # ArcGIS service for outSR=27700 and the coordinate values are BNG. We
    # *know* they are BNG, so override.
    gdf = gdf.set_crs(CRS_BNG, allow_override=True)

    rename: dict[str, str] = {}
    for col in gdf.columns:
        if col in ("LAD24CD", "LAD23CD", "LAD22CD"):
            rename[col] = "lad_code"
        elif col in ("LAD24NM", "LAD23NM", "LAD22NM"):
            rename[col] = "lad_name"
    return gdf.rename(columns=rename)


def load_lsoa21_pwc(path: Path, lsoa_codes: Iterable[str] | None = None) -> gpd.GeoDataFrame:
    """Read the national LSOA21 PWC GeoJSON, optionally filter to NE LSOAs."""

    gdf = gpd.read_file(path)
    # GeoJSON RFC 7946 forbids non-WGS84 CRS declarations, so geopandas
    # auto-tags the read GeoDataFrame as EPSG:4326 even though we asked the
    # ArcGIS service for outSR=27700 and the coordinate values are BNG. We
    # *know* they are BNG, so override.
    gdf = gdf.set_crs(CRS_BNG, allow_override=True)

    rename = {c: "lsoa21cd" for c in gdf.columns if c == "LSOA21CD"}
    gdf = gdf.rename(columns=rename)
    if lsoa_codes is not None:
        gdf = gdf.loc[gdf["lsoa21cd"].isin(set(lsoa_codes))].reset_index(drop=True)
    return gdf


# ---------------------------------------------------------------------------
# LSOA → LAD attribution (LSOA21 BGC has no LAD column of its own)


def assign_lsoa_to_lad(
    lsoa_gdf: gpd.GeoDataFrame,
    lad_gdf: gpd.GeoDataFrame,
    *,
    lad_code_col: str = "lad_code",
) -> gpd.GeoDataFrame:
    """Add a ``lad_code`` column to LSOAs via centroid-in-polygon spatial join.

    The LSOA21 BGC service does not expose a LAD code column, so we attribute
    each LSOA to the LAD that contains its (geometric) centroid. This matches
    ONS's own LSOA→LAD lookup convention closely enough for our purposes; the
    PWC-based attribution would be theoretically cleaner but the geometric
    centroid is robust for the regular shapes the BGC layer publishes.
    """

    if lad_code_col not in lad_gdf.columns:
        msg = f"lad_gdf missing column: {lad_code_col}"
        raise ValueError(msg)

    centroids = lsoa_gdf[["lsoa21cd"]].copy()
    centroids["geometry"] = lsoa_gdf.geometry.centroid
    centroids = gpd.GeoDataFrame(centroids, geometry="geometry", crs=lsoa_gdf.crs)

    joined = centroids.sjoin(
        lad_gdf[[lad_code_col, "geometry"]], how="left", predicate="within"
    )
    joined = joined[["lsoa21cd", lad_code_col]].drop_duplicates("lsoa21cd")

    return lsoa_gdf.merge(joined, on="lsoa21cd", how="left")


# ---------------------------------------------------------------------------
# Schools point-in-polygon validation (deferred from Stage 1)


def validate_schools_in_la(
    schools_gdf: gpd.GeoDataFrame,
    lad_gdf: gpd.GeoDataFrame,
    *,
    school_la_col: str = "la_code_ons",
    lad_code_col: str = "lad_code",
) -> pd.DataFrame:
    """Spatial-join schools against LAD polygons and flag mismatches.

    Returns one row per school with columns:
        urn, declared_la, polygon_la, agree (bool), distance_m_to_declared

    ``distance_m_to_declared`` is 0.0 when the school is inside its declared
    LA polygon, else the (signed) distance from the school to that polygon
    boundary in metres. A small positive distance (< ~50 m) is usually a
    schools-on-coastal-boundary effect; large distances suggest a real
    geocoding or LA-attribution problem.
    """

    needed_cols_school = {"urn", school_la_col, "geometry"}
    missing_school = needed_cols_school - set(schools_gdf.columns)
    if missing_school:
        msg = f"schools_gdf missing columns: {missing_school}"
        raise ValueError(msg)
    if lad_code_col not in lad_gdf.columns:
        msg = f"lad_gdf missing column: {lad_code_col}"
        raise ValueError(msg)

    school_pts = schools_gdf[["urn", school_la_col, "geometry"]].rename(
        columns={school_la_col: "declared_la"}
    )
    lad_polys = lad_gdf[[lad_code_col, "geometry"]].rename(
        columns={lad_code_col: "polygon_la"}
    )

    joined = gpd.sjoin(school_pts, lad_polys, how="left", predicate="within")
    joined = joined.drop(columns=["index_right"], errors="ignore")
    joined["agree"] = joined["polygon_la"] == joined["declared_la"]

    distance = pd.Series(0.0, index=joined.index, dtype=float)
    mismatches = ~joined["agree"]
    if mismatches.any():
        # Distance from each mismatched school to the polygon of its
        # declared LA. Large values mean the GIAS-declared LA disagrees
        # with the polygon the point falls inside.
        declared_polys = lad_gdf.set_index(lad_code_col)["geometry"]
        for idx in joined.index[mismatches]:
            la = joined.at[idx, "declared_la"]
            if la in declared_polys.index:
                distance.at[idx] = float(
                    joined.at[idx, "geometry"].distance(declared_polys.loc[la])
                )
            else:
                distance.at[idx] = float("nan")
    joined["distance_m_to_declared"] = distance

    return pd.DataFrame(joined.drop(columns=["geometry"]))


__all__ = [
    "BBOX_BNG_NE_GEO",
    "ONS_LAD24_BFE_URL",
    "ONS_LSOA21_BGC_URL",
    "ONS_LSOA21_PWC_URL",
    "assign_lsoa_to_lad",
    "fetch_lad_boundaries_ne",
    "fetch_lsoa21_boundaries_ne",
    "fetch_lsoa21_pwc",
    "load_lad",
    "load_lsoa21",
    "load_lsoa21_pwc",
    "query_arcgis_geojson",
    "validate_schools_in_la",
]
