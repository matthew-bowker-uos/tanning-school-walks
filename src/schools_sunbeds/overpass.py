"""OSM tanning-salon enumeration via the Overpass API.

We issue a single Overpass query covering the NE bounding box with the three
tag combinations from spec §5.2:

    leisure = tanning_salon
    shop    = solarium
    shop    = beauty AND beauty = tanning

Each is queried for both nodes and ways; ``out center`` gives a single
representative point per way, which we use as the salon location. The raw
response is persisted unmodified for replay; the loader returns a tidy
GeoDataFrame in EPSG:27700 with a stable schema.
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

from schools_sunbeds.config import CRS_BNG, REGION_BBOX_WGS84

log = logging.getLogger(__name__)

OVERPASS_URL_PRIMARY = "https://overpass-api.de/api/interpreter"
OVERPASS_URL_FALLBACK = "https://overpass.kumi.systems/api/interpreter"

# Tag combinations to enumerate. Each entry is a list of ``["k","v"]``
# pairs that must all match on a single feature. We map this into the
# Overpass ``[k="v"]`` syntax in :func:`build_overpass_query`.
SALON_TAG_FILTERS: tuple[tuple[tuple[str, str], ...], ...] = (
    (("leisure", "tanning_salon"),),
    (("shop", "solarium"),),
    (("shop", "beauty"), ("beauty", "tanning")),
)


def _bbox_wgs84_for_overpass(bbox_wgs84: tuple[float, float, float, float]) -> str:
    """Overpass uses (south, west, north, east) order, comma-separated."""
    west, south, east, north = bbox_wgs84
    return f"{south},{west},{north},{east}"


def build_overpass_query(
    bbox_wgs84: tuple[float, float, float, float] = REGION_BBOX_WGS84,
    *,
    tag_filters: Iterable[Iterable[tuple[str, str]]] = SALON_TAG_FILTERS,
    timeout_s: int = 90,
) -> str:
    """Assemble the Overpass QL query string."""

    bbox_str = _bbox_wgs84_for_overpass(bbox_wgs84)
    statements: list[str] = []
    for filt in tag_filters:
        tag_clause = "".join(f'["{k}"="{v}"]' for k, v in filt)
        statements.append(f"  node{tag_clause}({bbox_str});")
        statements.append(f"  way{tag_clause}({bbox_str});")
    body = "\n".join(statements)
    return f"[out:json][timeout:{timeout_s}];\n(\n{body}\n);\nout center;\n"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
def _post_overpass(url: str, query: str, timeout: int) -> dict[str, Any]:
    r = requests.post(
        url,
        data={"data": query},
        timeout=timeout,
        headers={"User-Agent": "schools-sunbeds research/0.1 (matthew.j.bowker@gmail.com)"},
    )
    r.raise_for_status()
    return r.json()


def fetch_overpass_salons(
    target_dir: Path,
    *,
    bbox_wgs84: tuple[float, float, float, float] = REGION_BBOX_WGS84,
    overpass_url: str = OVERPASS_URL_PRIMARY,
    overpass_url_fallback: str = OVERPASS_URL_FALLBACK,
    timeout: int = 120,
) -> Path:
    """Run the salon Overpass query and persist the raw JSON response."""

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "osm_overpass_salons_ne.json"
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing Overpass response at %s", out)
        return out

    query = build_overpass_query(bbox_wgs84)
    try:
        data = _post_overpass(overpass_url, query, timeout=timeout)
    except requests.HTTPError as exc:
        log.info("Primary Overpass failed (%s); trying fallback %s", exc, overpass_url_fallback)
        data = _post_overpass(overpass_url_fallback, query, timeout=timeout)

    out.write_text(json.dumps(data))
    log.info("Saved %d Overpass elements to %s", len(data.get("elements", [])), out)
    return out


# ---------------------------------------------------------------------------
# Loaders


def _classify_tags(tags: dict[str, str]) -> str:
    """Which of the SALON_TAG_FILTERS matched? Returns a short label.

    A single feature can satisfy more than one filter (e.g. "shop=beauty +
    beauty=tanning" plus a stray ``leisure=tanning_salon``). We label by
    the most specific match: tanning_salon > solarium > beauty+tanning.
    """

    if tags.get("leisure") == "tanning_salon":
        return "leisure=tanning_salon"
    if tags.get("shop") == "solarium":
        return "shop=solarium"
    if tags.get("shop") == "beauty" and tags.get("beauty") == "tanning":
        return "shop=beauty;beauty=tanning"
    return "other"


def parse_overpass_response(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert an Overpass JSON response into a flat DataFrame.

    Columns:
        osm_type ("node" / "way") — feature type
        osm_id   — OSM identifier within type
        name     — ``tags["name"]`` if present
        addr_full— concatenated ``addr:*`` fields
        tag_match— which SALON_TAG_FILTERS entry matched
        lat, lon — point coords (centre for ways, geometry for nodes)
        tags     — full original tag dict (for provenance)
    """

    rows: list[dict[str, Any]] = []
    for el in payload.get("elements", []):
        osm_type = el.get("type")
        if osm_type not in {"node", "way"}:
            continue
        osm_id = el.get("id")
        tags = el.get("tags", {}) or {}
        if osm_type == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            centre = el.get("center", {}) or {}
            lat, lon = centre.get("lat"), centre.get("lon")
        if lat is None or lon is None:
            continue
        addr_parts = [
            tags.get(k, "") for k in ("addr:housenumber", "addr:street", "addr:city", "addr:postcode")
        ]
        addr_full = ", ".join(p for p in addr_parts if p)

        rows.append(
            {
                "osm_type": osm_type,
                "osm_id": osm_id,
                "name": tags.get("name", ""),
                "addr_full": addr_full,
                "tag_match": _classify_tags(tags),
                "lat": float(lat),
                "lon": float(lon),
                "tags": tags,
            }
        )
    return pd.DataFrame(rows)


def to_geodataframe(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Project a parsed Overpass DataFrame to BNG points."""

    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs=CRS_BNG)
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["lon"], df["lat"]),
        crs="EPSG:4326",
    ).to_crs(CRS_BNG)
    return gdf


__all__ = [
    "OVERPASS_URL_FALLBACK",
    "OVERPASS_URL_PRIMARY",
    "SALON_TAG_FILTERS",
    "build_overpass_query",
    "fetch_overpass_salons",
    "parse_overpass_response",
    "to_geodataframe",
]
