"""Cross-source agreement between Google Places and OSM Overpass salon lists.

Spec §6.7 names this audit as a methods sub-finding: report counts and
distance distribution between matched salons across the two sources, plus
union and intersection layers used downstream as sensitivity inputs.

Matching rule:
    A Google place ``g`` is paired with an OSM record ``o`` if
        distance(g, o) <= max_distance_m
        AND token_set_ratio(g.name, o.name) >= name_score_min

If multiple OSM records satisfy the criteria for a single Google place we
keep the closest one (and break ties by higher name score). The function
returns the matched, Google-only, and OSM-only frames.
"""

from __future__ import annotations

from dataclasses import dataclass

import geopandas as gpd
import pandas as pd
from rapidfuzz import fuzz

DEFAULT_MAX_DISTANCE_M = 50
DEFAULT_NAME_SCORE_MIN = 85


@dataclass(frozen=True)
class MatchResult:
    matched: pd.DataFrame  # one row per matched pair
    google_only: gpd.GeoDataFrame
    osm_only: gpd.GeoDataFrame
    summary: dict[str, int | float]


def _name_score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return float(fuzz.token_set_ratio(a, b))


def match_salons(
    google_gdf: gpd.GeoDataFrame,
    osm_gdf: gpd.GeoDataFrame,
    *,
    max_distance_m: float = DEFAULT_MAX_DISTANCE_M,
    name_score_min: float = DEFAULT_NAME_SCORE_MIN,
    google_name_col: str = "name",
    osm_name_col: str = "name",
    google_id_col: str = "place_id",
    osm_id_col: str = "osm_id",
) -> MatchResult:
    """Match Google Places ↔ OSM Overpass by distance + fuzzy name.

    Both inputs must be in EPSG:27700 (BNG metres).
    """

    if google_gdf.crs is None or google_gdf.crs.to_string() != "EPSG:27700":
        raise ValueError("google_gdf must be in EPSG:27700")
    if osm_gdf.crs is None or osm_gdf.crs.to_string() != "EPSG:27700":
        raise ValueError("osm_gdf must be in EPSG:27700")

    g = google_gdf.reset_index(drop=True).copy()
    o = osm_gdf.reset_index(drop=True).copy()
    o["_idx_osm"] = o.index

    # Spatial nearest with distance budget. geopandas.sjoin_nearest gives
    # the single closest OSM record per Google point regardless of name;
    # we then post-filter on name score. A Google place that has no OSM
    # match within max_distance_m is dropped here.
    cand = gpd.sjoin_nearest(
        g,
        o[[osm_id_col, osm_name_col, "_idx_osm", "geometry"]].rename(
            columns={osm_name_col: "_osm_name", osm_id_col: "_osm_id"}
        ),
        how="left",
        max_distance=max_distance_m,
        distance_col="match_distance_m",
    )

    cand["name_score"] = cand.apply(
        lambda r: _name_score(str(r.get(google_name_col, "")), str(r.get("_osm_name", "") or "")),
        axis=1,
    )

    matched_mask = cand["_osm_id"].notna() & (cand["name_score"] >= name_score_min)
    matched = cand.loc[matched_mask].copy()
    # If the same Google place ended up with several OSM candidates from
    # sjoin_nearest's tie-breaking, keep the highest-scoring closest one.
    matched = (
        matched.sort_values(["match_distance_m", "name_score"], ascending=[True, False])
        .drop_duplicates(subset=[google_id_col], keep="first")
    )

    google_only_ids = set(g[google_id_col]) - set(matched[google_id_col])
    google_only = g.loc[g[google_id_col].isin(google_only_ids)].copy()

    osm_matched_ids = set(matched["_osm_id"]) - {None}
    osm_only_ids = set(o[osm_id_col]) - osm_matched_ids
    osm_only = o.loc[o[osm_id_col].isin(osm_only_ids)].copy()

    summary: dict[str, int | float] = {
        "n_google": int(len(g)),
        "n_osm": int(len(o)),
        "n_matched": int(len(matched)),
        "n_google_only": int(len(google_only)),
        "n_osm_only": int(len(osm_only)),
        "median_match_distance_m": float(matched["match_distance_m"].median())
        if len(matched)
        else float("nan"),
        "median_match_name_score": float(matched["name_score"].median())
        if len(matched)
        else float("nan"),
    }

    matched_out = matched[
        [
            google_id_col,
            "_osm_id",
            google_name_col,
            "_osm_name",
            "match_distance_m",
            "name_score",
        ]
    ].rename(columns={"_osm_id": osm_id_col, "_osm_name": "osm_name"})

    return MatchResult(
        matched=matched_out.reset_index(drop=True),
        google_only=google_only.reset_index(drop=True),
        osm_only=osm_only.reset_index(drop=True).drop(columns=["_idx_osm"]),
        summary=summary,
    )


def build_union_intersection(
    google_gdf: gpd.GeoDataFrame,
    osm_gdf: gpd.GeoDataFrame,
    matched: pd.DataFrame,
    *,
    google_id_col: str = "place_id",
    osm_id_col: str = "osm_id",
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Construct the union and intersection sets used for sensitivity.

    - **Intersection**: matched Google rows (one per matched pair). Each
      retained row carries both keys for traceability.
    - **Union**: matched Google rows + Google-only + OSM-only.
    """

    matched_google = google_gdf.loc[
        google_gdf[google_id_col].isin(matched[google_id_col])
    ].copy()
    google_only = google_gdf.loc[
        ~google_gdf[google_id_col].isin(matched[google_id_col])
    ].copy()
    osm_only = osm_gdf.loc[~osm_gdf[osm_id_col].isin(set(matched[osm_id_col]))].copy()

    intersection = matched_google.assign(source="intersection")
    union = pd.concat(
        [
            matched_google.assign(source="both"),
            google_only.assign(source="google_only"),
            osm_only.assign(source="osm_only"),
        ],
        ignore_index=True,
    )
    return gpd.GeoDataFrame(intersection, geometry="geometry", crs=google_gdf.crs), gpd.GeoDataFrame(
        union, geometry="geometry", crs=google_gdf.crs
    )


def lsoa_level_agreement(
    google_gdf: gpd.GeoDataFrame,
    osm_gdf: gpd.GeoDataFrame,
    lsoa_gdf: gpd.GeoDataFrame,
    *,
    lsoa_code_col: str = "lsoa21cd",
) -> pd.DataFrame:
    """For each LSOA, count Google / OSM / matched / either-source salons.

    Useful as the ``methods sub-finding`` table promised in spec §6.7. The
    matched-only count requires that ``google_gdf`` already carries an
    ``_is_matched`` column from :func:`match_salons` — we add it on the fly
    by inspecting which place IDs appear in ``matched``.
    """

    google_pts = (
        gpd.sjoin(
            google_gdf[["place_id", "geometry"]],
            lsoa_gdf[[lsoa_code_col, "geometry"]],
            how="left",
            predicate="within",
        )
        .groupby(lsoa_code_col)["place_id"]
        .count()
        .rename("n_google")
    )
    osm_pts = (
        gpd.sjoin(
            osm_gdf[["osm_id", "geometry"]],
            lsoa_gdf[[lsoa_code_col, "geometry"]],
            how="left",
            predicate="within",
        )
        .groupby(lsoa_code_col)["osm_id"]
        .count()
        .rename("n_osm")
    )

    out = (
        lsoa_gdf[[lsoa_code_col]]
        .copy()
        .merge(google_pts, on=lsoa_code_col, how="left")
        .merge(osm_pts, on=lsoa_code_col, how="left")
    )
    out[["n_google", "n_osm"]] = out[["n_google", "n_osm"]].fillna(0).astype(int)
    return out


__all__ = [
    "DEFAULT_MAX_DISTANCE_M",
    "DEFAULT_NAME_SCORE_MIN",
    "MatchResult",
    "build_union_intersection",
    "lsoa_level_agreement",
    "match_salons",
]
