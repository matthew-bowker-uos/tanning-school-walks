"""Google Places API (New) Text Search for sunbed-related establishments.

The new Places API uses HTTP POST to ``places.googleapis.com/v1/places:searchText``
with a JSON body and a ``X-Goog-FieldMask`` header. We use the cheapest
(Essentials) field set to keep cost low: ``places.id``, ``places.displayName``,
``places.formattedAddress``, ``places.location``, ``places.types``.

Per the SKU table at https://developers.google.com/maps/billing-and-pricing/
Text Search Essentials is billed at roughly $5 / 1000 calls.

To stay inside a sensible budget for the NE region we grid the bounding box
into ~5 km cells, run one query per (cell, keyword), and persist every raw
response under ``data/raw/google_places/<date>/`` for replay. The function
estimates and prints the projected number of API calls before issuing any.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from pyproj import Transformer
from tenacity import retry, stop_after_attempt, wait_exponential

from schools_sunbeds.config import (
    CRS_BNG,
    CRS_WGS84,
    GOOGLE_PLACES_QUERIES,
    REGION_BBOX_WGS84,
)

log = logging.getLogger(__name__)

PLACES_API_URL = "https://places.googleapis.com/v1/places:searchText"

# Essentials field mask — cheapest SKU. See
# https://developers.google.com/maps/documentation/places/web-service/text-search#field-mask
FIELD_MASK_ESSENTIALS = (
    "places.id,places.displayName,places.formattedAddress,places.location,places.types"
)

# Default per-cell radius (after gridding the bbox in BNG metres).
GRID_CELL_SIZE_M = 5_000

# Each Text Search call returns up to 20 places per page. We page until we
# either receive a short page or hit ``MAX_PAGES`` to avoid pathological
# spend on a single saturated cell.
MAX_PAGES = 3


# ---------------------------------------------------------------------------
# Grid building


@dataclass(frozen=True)
class Cell:
    """One bounding-box cell to query, identified by integer (i, j) on the grid."""

    i: int
    j: int
    bbox_bng: tuple[float, float, float, float]  # (xmin, ymin, xmax, ymax) in metres
    bbox_wgs84: tuple[float, float, float, float]  # (lon_min, lat_min, lon_max, lat_max)


def _wgs84_to_bng_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    transformer = Transformer.from_crs(CRS_WGS84, CRS_BNG, always_xy=True)
    lon_min, lat_min, lon_max, lat_max = bbox
    x_min, y_min = transformer.transform(lon_min, lat_min)
    x_max, y_max = transformer.transform(lon_max, lat_max)
    return min(x_min, x_max), min(y_min, y_max), max(x_min, x_max), max(y_min, y_max)


def _bng_to_wgs84_bbox(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    transformer = Transformer.from_crs(CRS_BNG, CRS_WGS84, always_xy=True)
    x_min, y_min, x_max, y_max = bbox
    lon_min, lat_min = transformer.transform(x_min, y_min)
    lon_max, lat_max = transformer.transform(x_max, y_max)
    return min(lon_min, lon_max), min(lat_min, lat_max), max(lon_min, lon_max), max(lat_min, lat_max)


def grid_bbox(
    bbox_wgs84: tuple[float, float, float, float] = REGION_BBOX_WGS84,
    *,
    cell_size_m: int = GRID_CELL_SIZE_M,
) -> Iterator[Cell]:
    """Yield grid cells covering ``bbox_wgs84``, with side ``cell_size_m`` in BNG."""

    x_min, y_min, x_max, y_max = _wgs84_to_bng_bbox(bbox_wgs84)
    n_cols = math.ceil((x_max - x_min) / cell_size_m)
    n_rows = math.ceil((y_max - y_min) / cell_size_m)
    for j in range(n_rows):
        for i in range(n_cols):
            cx_lo = x_min + i * cell_size_m
            cy_lo = y_min + j * cell_size_m
            cx_hi = min(cx_lo + cell_size_m, x_max)
            cy_hi = min(cy_lo + cell_size_m, y_max)
            cell_bng = (cx_lo, cy_lo, cx_hi, cy_hi)
            yield Cell(
                i=i,
                j=j,
                bbox_bng=cell_bng,
                bbox_wgs84=_bng_to_wgs84_bbox(cell_bng),
            )


def filter_cells_to_polygon(
    cells: Iterable[Cell],
    polygons: gpd.GeoDataFrame,
) -> list[Cell]:
    """Keep only cells whose BNG bbox intersects any polygon in ``polygons``.

    Used to skip empty rural cells over moorland or sea.
    """

    if polygons.empty:
        return list(cells)

    if polygons.crs is None or polygons.crs.to_string() != CRS_BNG:
        polygons = polygons.to_crs(CRS_BNG)
    poly_union = polygons.geometry.union_all()

    from shapely.geometry import box

    out: list[Cell] = []
    for c in cells:
        if box(*c.bbox_bng).intersects(poly_union):
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# API client


class PlacesAPIError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY", "")
    if not key:
        msg = (
            "GOOGLE_PLACES_API_KEY is not set. Load it from your Drive-mounted "
            ".env file (Colab) or your local .env (dev) before running the notebook."
        )
        raise PlacesAPIError(msg)
    return key


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20))
def _post_search_text(
    body: dict[str, Any],
    *,
    api_key: str,
    field_mask: str,
    timeout: int,
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }
    r = requests.post(PLACES_API_URL, headers=headers, json=body, timeout=timeout)
    if r.status_code >= 400:
        msg = f"Places API returned {r.status_code}: {r.text[:300]}"
        raise PlacesAPIError(msg)
    return r.json()


def search_text_in_cell(
    text_query: str,
    cell: Cell,
    *,
    api_key: str | None = None,
    field_mask: str = FIELD_MASK_ESSENTIALS,
    timeout: int = 30,
    max_pages: int = MAX_PAGES,
    page_throttle_s: float = 2.0,
) -> tuple[list[dict[str, Any]], int]:
    """One Text Search restricted to a cell. Returns (places, n_calls).

    Pagination: the new Places API returns ``nextPageToken`` when more
    results are available; we re-issue the same query with the token until
    the page is short or we hit ``max_pages``. Google requires a short
    delay (~2 s) between paginated calls.
    """

    api_key = api_key or _api_key()
    lon_min, lat_min, lon_max, lat_max = cell.bbox_wgs84
    base_body: dict[str, Any] = {
        "textQuery": text_query,
        "locationRestriction": {
            "rectangle": {
                "low": {"latitude": lat_min, "longitude": lon_min},
                "high": {"latitude": lat_max, "longitude": lon_max},
            }
        },
        "maxResultCount": 20,  # max per page
    }

    places: list[dict[str, Any]] = []
    n_calls = 0
    page_token: str | None = None

    for page_idx in range(max_pages):
        body = dict(base_body)
        if page_token:
            body["pageToken"] = page_token
            time.sleep(page_throttle_s)
        data = _post_search_text(body, api_key=api_key, field_mask=field_mask, timeout=timeout)
        n_calls += 1
        page = data.get("places", [])
        places.extend(page)
        page_token = data.get("nextPageToken")
        if not page_token or len(page) < 20:
            break

    return places, n_calls


# ---------------------------------------------------------------------------
# Driver — gridded enumeration


def _serialise_response(
    cell: Cell,
    text_query: str,
    places: list[dict[str, Any]],
    n_calls: int,
) -> dict[str, Any]:
    return {
        "cell_i": cell.i,
        "cell_j": cell.j,
        "bbox_wgs84": cell.bbox_wgs84,
        "text_query": text_query,
        "n_calls": n_calls,
        "places": places,
    }


def fetch_all_salons_grid(
    target_dir: Path,
    *,
    bbox_wgs84: tuple[float, float, float, float] = REGION_BBOX_WGS84,
    queries: Iterable[str] = GOOGLE_PLACES_QUERIES,
    cell_size_m: int = GRID_CELL_SIZE_M,
    polygons: gpd.GeoDataFrame | None = None,
    api_key: str | None = None,
    field_mask: str = FIELD_MASK_ESSENTIALS,
    progress: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Run gridded Text Search across NE for all queries, persist raw JSONL.

    ``polygons``: optional GeoDataFrame; cells are filtered to those that
    intersect any polygon. Use the LSOA21 NE layer to skip rural empties.

    Returns (path_to_jsonl, audit_dict). The JSONL file has one JSON object
    per (cell, query). The audit dict records cell counts, total API calls,
    and an estimated cost.
    """

    api_key = api_key or _api_key()
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "google_places_raw.jsonl"

    cells = list(grid_bbox(bbox_wgs84, cell_size_m=cell_size_m))
    if polygons is not None:
        cells = filter_cells_to_polygon(cells, polygons)
    queries = list(queries)
    log.info(
        "Gridded Text Search plan: %d cells × %d queries = %d cell-queries",
        len(cells),
        len(queries),
        len(cells) * len(queries),
    )

    n_calls_total = 0
    n_places_total = 0

    with out.open("w") as fh:
        for q_idx, text_query in enumerate(queries):
            for c_idx, cell in enumerate(cells):
                places, n_calls = search_text_in_cell(
                    text_query, cell, api_key=api_key, field_mask=field_mask
                )
                fh.write(json.dumps(_serialise_response(cell, text_query, places, n_calls)) + "\n")
                n_calls_total += n_calls
                n_places_total += len(places)
                if progress and (c_idx + 1) % 50 == 0:
                    log.info(
                        "[%s] cell %d/%d — %d places so far (calls so far: %d)",
                        text_query,
                        c_idx + 1,
                        len(cells),
                        n_places_total,
                        n_calls_total,
                    )

    audit: dict[str, Any] = {
        "n_cells": len(cells),
        "n_queries": len(queries),
        "n_calls_total": n_calls_total,
        "n_places_returned": n_places_total,
        "estimated_cost_usd": round(n_calls_total * 5 / 1000, 2),
        "cell_size_m": cell_size_m,
        "field_mask": field_mask,
    }
    return out, audit


# ---------------------------------------------------------------------------
# Loader — collapse the raw JSONL into a deduplicated GeoDataFrame


def parse_jsonl_to_dataframe(jsonl_path: Path) -> pd.DataFrame:
    """Flatten the gridded raw JSONL into one row per (place_id, query, cell)."""

    rows: list[dict[str, Any]] = []
    with Path(jsonl_path).open() as fh:
        for line in fh:
            envelope = json.loads(line)
            for place in envelope.get("places", []):
                loc = place.get("location") or {}
                rows.append(
                    {
                        "place_id": place.get("id", ""),
                        "name": (place.get("displayName") or {}).get("text", ""),
                        "address": place.get("formattedAddress", ""),
                        "lat": loc.get("latitude"),
                        "lon": loc.get("longitude"),
                        "types": ";".join(place.get("types") or []),
                        "query_term": envelope["text_query"],
                        "cell_i": envelope["cell_i"],
                        "cell_j": envelope["cell_j"],
                    }
                )
    return pd.DataFrame(rows)


def deduplicate_places(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Reduce to one row per ``place_id`` and project to BNG.

    The ``query_term`` and ``types`` columns are retained as semicolon-
    delimited concatenations of every distinct value seen across the
    raw JSONL — useful provenance for which keywords surfaced each place.
    """

    if df.empty:
        return gpd.GeoDataFrame(df, geometry=[], crs=CRS_BNG)

    grouped = (
        df.sort_values("place_id")
        .groupby("place_id", as_index=False)
        .agg(
            name=("name", "first"),
            address=("address", "first"),
            lat=("lat", "first"),
            lon=("lon", "first"),
            types=("types", lambda s: ";".join(sorted(set("".join(s).split(";")) - {""}))),
            query_terms=("query_term", lambda s: ";".join(sorted(set(s)))),
        )
    )
    grouped = grouped.dropna(subset=["lat", "lon"])
    return gpd.GeoDataFrame(
        grouped,
        geometry=gpd.points_from_xy(grouped["lon"], grouped["lat"]),
        crs=CRS_WGS84,
    ).to_crs(CRS_BNG)


__all__ = [
    "Cell",
    "FIELD_MASK_ESSENTIALS",
    "GRID_CELL_SIZE_M",
    "PLACES_API_URL",
    "PlacesAPIError",
    "deduplicate_places",
    "fetch_all_salons_grid",
    "filter_cells_to_polygon",
    "grid_bbox",
    "parse_jsonl_to_dataframe",
    "search_text_in_cell",
]
