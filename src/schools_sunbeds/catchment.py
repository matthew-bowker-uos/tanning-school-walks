"""Catchment assignment: pupils-LSOA → school via shortest walking-network path.

Two implementations:

- :func:`assign_nearest_school` (DEC-009 — primary). For each LSOA21 PWC
  and each school phase, find the single nearest school within the phase
  cap (DEC-016: 2 km primary / 5 km secondary / 5 km special). Uses
  pandana's ``nearest_pois``.
- :func:`assign_knn_idw` (sensitivity per DEC-011). For each LSOA21, find
  the *k* nearest schools and weight contribution by inverse distance.
  Used to test robustness of the headline RII to allocation choice.

Both functions return a long-format DataFrame::

    lsoa21cd, urn, phase, distance_m, weight, child_n

where ``weight`` is 1.0 for hard-nearest, and the IDW weight for k-NN.
``child_n`` is copied from the LSOA-level Census 2021 school-age count,
so downstream regression can use the LSOA × school assignment as a
weighting frame directly.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from schools_sunbeds.config import CATCHMENT_CAP_M, CRS_BNG, KNN_K_SENSITIVITY

log = logging.getLogger(__name__)


# Phases recognised by the catchment functions. The mapping from GIAS
# phase strings -> "category" we use for cap selection lives here so the
# rest of the module never has to special-case middle-deemed-* etc.
PHASE_CATEGORY: dict[str, str] = {
    "Primary": "primary",
    "Middle deemed primary": "primary",
    "Secondary": "secondary",
    "Middle deemed secondary": "secondary",
    "All-through": "secondary",  # covers ages 4-18 → secondary cap is more conservative
    "Special": "special",
    "Not applicable": "special",  # phase NA in GIAS = special school per Stage 1 carve-out
}


@dataclass(frozen=True)
class CatchmentResult:
    """Output of an assignment call."""

    assignments: pd.DataFrame  # lsoa21cd, urn, phase, distance_m, weight, child_n
    audit: dict[str, int | float]


def _phase_categories(schools_gdf: gpd.GeoDataFrame, phase_col: str) -> pd.Series:
    cats = schools_gdf[phase_col].map(PHASE_CATEGORY)
    if cats.isna().any():
        unmapped = schools_gdf.loc[cats.isna(), phase_col].unique().tolist()
        msg = f"Unrecognised phase strings: {unmapped}"
        raise ValueError(msg)
    return cats


def _nearest_node_indices(
    network,
    points_x: np.ndarray,
    points_y: np.ndarray,
) -> np.ndarray:
    """Pandana wrapper that returns the network node id closest to each point."""

    return network.get_node_ids(points_x, points_y).values


def assign_nearest_school(
    network,
    pwc_gdf: gpd.GeoDataFrame,
    schools_gdf: gpd.GeoDataFrame,
    *,
    phase_col: str = "phase",
    pwc_id_col: str = "lsoa21cd",
    pwc_pop_col: str = "pop_school_age",
    school_id_col: str = "urn",
    cap_overrides: dict[str, int] | None = None,
) -> CatchmentResult:
    """Hard nearest-school per phase, capped by phase walking distance.

    ``network`` is a :class:`pandana.Network`. ``set_pois`` is called
    internally per phase, so no explicit ``precompute`` is required.

    Both inputs must be in EPSG:27700.
    """

    if pwc_gdf.crs is None or pwc_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("pwc_gdf must be in EPSG:27700")
    if schools_gdf.crs is None or schools_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("schools_gdf must be in EPSG:27700")

    caps = dict(CATCHMENT_CAP_M)
    if cap_overrides:
        caps.update(cap_overrides)

    schools = schools_gdf.copy()
    schools["phase_cat_internal"] = _phase_categories(schools, phase_col)
    # We need lon/lat for pandana — pandana stores nodes in WGS84.
    schools_wgs = schools.to_crs("EPSG:4326")
    schools["lon_wgs"] = schools_wgs.geometry.x
    schools["lat_wgs"] = schools_wgs.geometry.y

    pwc = pwc_gdf.copy()
    pwc_wgs = pwc.to_crs("EPSG:4326")
    pwc["lon_wgs"] = pwc_wgs.geometry.x
    pwc["lat_wgs"] = pwc_wgs.geometry.y

    # Snap each PWC to the nearest network node up front.
    pwc["pandana_node"] = _nearest_node_indices(
        network, pwc["lon_wgs"].to_numpy(), pwc["lat_wgs"].to_numpy()
    )

    rows: list[dict[str, object]] = []
    audit: dict[str, int | float] = {"n_lsoa": int(len(pwc))}

    for cat, cap_m in caps.items():
        sub = schools.loc[schools["phase_cat_internal"] == cat]
        if sub.empty:
            audit[f"n_schools_{cat}"] = 0
            audit[f"n_assigned_{cat}"] = 0
            continue
        # Register schools as POIs for this phase.
        network.set_pois(
            category=f"schools_{cat}",
            maxdist=cap_m,
            maxitems=1,
            x_col=sub["lon_wgs"].to_numpy(),
            y_col=sub["lat_wgs"].to_numpy(),
        )
        nearest = network.nearest_pois(
            distance=cap_m,
            category=f"schools_{cat}",
            num_pois=1,
            include_poi_ids=True,
        )
        # `nearest` is indexed by network node id with columns
        # 1 (distance) and 'poi1' (poi index, 1-based-ish per pandana).
        dist_col = 1 if 1 in nearest.columns else nearest.columns[0]
        poi_col = "poi1" if "poi1" in nearest.columns else nearest.columns[-1]

        # Map PWC -> its nearest node -> distance + poi index for THIS phase.
        per_lsoa = pwc.merge(
            nearest[[dist_col, poi_col]].rename(columns={dist_col: "distance_m", poi_col: "poi_idx"}),
            left_on="pandana_node",
            right_index=True,
            how="left",
        )
        # POI index is 1-based into ``sub`` row order.
        sub_indexed = sub.reset_index(drop=True)
        valid = (
            per_lsoa["distance_m"].notna()
            & per_lsoa["poi_idx"].notna()
            & (per_lsoa["distance_m"] <= cap_m)
        )
        for _, r in per_lsoa.loc[valid].iterrows():
            poi_i = int(r["poi_idx"]) - 1
            if poi_i < 0 or poi_i >= len(sub_indexed):
                continue
            urn_val = sub_indexed.iloc[poi_i][school_id_col]
            phase_val = sub_indexed.iloc[poi_i][phase_col]
            child_n = r.get(pwc_pop_col)
            rows.append(
                {
                    pwc_id_col: r[pwc_id_col],
                    school_id_col: urn_val,
                    "phase": phase_val,
                    "phase_cat": cat,
                    "distance_m": float(r["distance_m"]),
                    "weight": 1.0,
                    pwc_pop_col: child_n,
                }
            )
        audit[f"n_schools_{cat}"] = int(len(sub))
        audit[f"n_assigned_{cat}"] = int(valid.sum())

    df = pd.DataFrame(rows)
    audit["n_assignments"] = len(df)
    audit["n_lsoa_with_assignment"] = df[pwc_id_col].nunique() if not df.empty else 0
    return CatchmentResult(assignments=df, audit=audit)


def assign_knn_idw(
    network,
    pwc_gdf: gpd.GeoDataFrame,
    schools_gdf: gpd.GeoDataFrame,
    *,
    k: int = KNN_K_SENSITIVITY,
    phase_col: str = "phase",
    pwc_id_col: str = "lsoa21cd",
    pwc_pop_col: str = "pop_school_age",
    school_id_col: str = "urn",
    cap_overrides: dict[str, int] | None = None,
) -> CatchmentResult:
    """k-NN inverse-distance-weighted assignment (DEC-011 sensitivity).

    Returns the same long-format frame as :func:`assign_nearest_school`,
    but with up to k rows per LSOA × phase. Weights sum to 1.0 within
    each LSOA × phase.
    """

    if pwc_gdf.crs is None or pwc_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("pwc_gdf must be in EPSG:27700")
    if schools_gdf.crs is None or schools_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("schools_gdf must be in EPSG:27700")

    caps = dict(CATCHMENT_CAP_M)
    if cap_overrides:
        caps.update(cap_overrides)

    schools = schools_gdf.copy()
    schools["phase_cat_internal"] = _phase_categories(schools, phase_col)
    schools_wgs = schools.to_crs("EPSG:4326")
    schools["lon_wgs"] = schools_wgs.geometry.x
    schools["lat_wgs"] = schools_wgs.geometry.y

    pwc = pwc_gdf.copy()
    pwc_wgs = pwc.to_crs("EPSG:4326")
    pwc["lon_wgs"] = pwc_wgs.geometry.x
    pwc["lat_wgs"] = pwc_wgs.geometry.y
    pwc["pandana_node"] = _nearest_node_indices(
        network, pwc["lon_wgs"].to_numpy(), pwc["lat_wgs"].to_numpy()
    )

    rows: list[dict[str, object]] = []

    for cat, cap_m in caps.items():
        sub = schools.loc[schools["phase_cat_internal"] == cat].reset_index(drop=True)
        if sub.empty:
            continue
        network.set_pois(
            category=f"schools_{cat}_knn",
            maxdist=cap_m,
            maxitems=k,
            x_col=sub["lon_wgs"].to_numpy(),
            y_col=sub["lat_wgs"].to_numpy(),
        )
        nearest = network.nearest_pois(
            distance=cap_m,
            category=f"schools_{cat}_knn",
            num_pois=k,
            include_poi_ids=True,
        )

        # Vectorise: merge PWC rows against the nearest table on pandana_node,
        # then iterate the small per-LSOA result frame instead of itertuples.
        joined = pwc.merge(nearest, left_on="pandana_node", right_index=True, how="inner")
        for _, lsoa_row in joined.iterrows():
            dists: list[float] = []
            urns: list[object] = []
            phases: list[object] = []
            for i in range(1, k + 1):
                d = lsoa_row.get(i)
                if d is None or pd.isna(d) or d > cap_m:
                    continue
                poi_idx = lsoa_row.get(f"poi{i}")
                if pd.isna(poi_idx):
                    continue
                pi = int(poi_idx) - 1
                if pi < 0 or pi >= len(sub):
                    continue
                dists.append(float(d))
                urns.append(sub.iloc[pi][school_id_col])
                phases.append(sub.iloc[pi][phase_col])
            if not dists:
                continue
            inv = np.array([1.0 / max(d, 1.0) for d in dists])
            inv = inv / inv.sum()
            for d, u, ph, w in zip(dists, urns, phases, inv):
                rows.append(
                    {
                        pwc_id_col: lsoa_row[pwc_id_col],
                        school_id_col: u,
                        "phase": ph,
                        "phase_cat": cat,
                        "distance_m": d,
                        "weight": float(w),
                        pwc_pop_col: lsoa_row.get(pwc_pop_col),
                    }
                )

    df = pd.DataFrame(rows)
    audit: dict[str, int | float] = {
        "n_lsoa": int(len(pwc)),
        "n_assignments": int(len(df)),
        "k": int(k),
    }
    return CatchmentResult(assignments=df, audit=audit)


__all__ = [
    "PHASE_CATEGORY",
    "CatchmentResult",
    "assign_knn_idw",
    "assign_nearest_school",
]
