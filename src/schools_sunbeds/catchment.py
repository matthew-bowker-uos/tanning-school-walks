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


def _snap_to_network(
    network, gdf: gpd.GeoDataFrame, lon_col: str = "lon_wgs", lat_col: str = "lat_wgs"
) -> gpd.GeoDataFrame:
    """Add WGS84 lon/lat + snapped pandana node id to ``gdf`` (BNG-input)."""

    out = gdf.copy()
    wgs = out.to_crs("EPSG:4326")
    out[lon_col] = wgs.geometry.x.to_numpy()
    out[lat_col] = wgs.geometry.y.to_numpy()
    out["pandana_node"] = _nearest_node_indices(
        network, out[lon_col].to_numpy(), out[lat_col].to_numpy()
    )
    return out


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

    Pre-snaps both PWCs and schools to pandana network nodes (via the
    public ``get_node_ids`` KDTree) and computes all-pairs shortest path
    lengths per phase via ``shortest_path_lengths`` in batch. The
    snapped ``origin_node`` and ``dest_node`` are recorded on the
    assignment so Stage 5 can construct a route between exactly the same
    OD pair without re-snapping (avoids the pandana nearest_pois
    inconsistency where its internal POI snap differs from
    ``get_node_ids``).

    Both inputs must be in EPSG:27700.
    """

    if pwc_gdf.crs is None or pwc_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("pwc_gdf must be in EPSG:27700")
    if schools_gdf.crs is None or schools_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("schools_gdf must be in EPSG:27700")

    caps = dict(CATCHMENT_CAP_M)
    if cap_overrides:
        caps.update(cap_overrides)

    schools = _snap_to_network(network, schools_gdf)
    schools["phase_cat_internal"] = _phase_categories(schools, phase_col)
    pwc = _snap_to_network(network, pwc_gdf)

    rows: list[dict[str, object]] = []
    audit: dict[str, int | float] = {"n_lsoa": int(len(pwc))}

    for cat, cap_m in caps.items():
        sub = schools.loc[schools["phase_cat_internal"] == cat].reset_index(drop=True)
        if sub.empty:
            audit[f"n_schools_{cat}"] = 0
            audit[f"n_assigned_{cat}"] = 0
            continue

        n_l, n_s = len(pwc), len(sub)
        origins = np.repeat(pwc["pandana_node"].to_numpy(), n_s)
        dests = np.tile(sub["pandana_node"].to_numpy(), n_l)
        lengths = np.asarray(
            network.shortest_path_lengths(origins.astype("int64"), dests.astype("int64"))
        ).reshape(n_l, n_s).astype("float64")

        # Disconnected pairs come back as a near-uint-max wrap value;
        # mask them as unreachable.
        lengths[lengths > cap_m] = np.inf
        argmin_idx = lengths.argmin(axis=1)
        min_dist = lengths[np.arange(n_l), argmin_idx]

        for li in range(n_l):
            d = min_dist[li]
            if not np.isfinite(d):
                continue
            si = int(argmin_idx[li])
            rows.append(
                {
                    pwc_id_col: pwc.iloc[li][pwc_id_col],
                    school_id_col: sub.iloc[si][school_id_col],
                    "phase": sub.iloc[si][phase_col],
                    "phase_cat": cat,
                    "distance_m": float(d),
                    "weight": 1.0,
                    pwc_pop_col: pwc.iloc[li].get(pwc_pop_col),
                    "origin_node": int(pwc.iloc[li]["pandana_node"]),
                    "dest_node": int(sub.iloc[si]["pandana_node"]),
                }
            )

        audit[f"n_schools_{cat}"] = int(n_s)
        audit[f"n_assigned_{cat}"] = int(np.isfinite(min_dist).sum())

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

    schools = _snap_to_network(network, schools_gdf)
    schools["phase_cat_internal"] = _phase_categories(schools, phase_col)
    pwc = _snap_to_network(network, pwc_gdf)

    rows: list[dict[str, object]] = []

    for cat, cap_m in caps.items():
        sub = schools.loc[schools["phase_cat_internal"] == cat].reset_index(drop=True)
        if sub.empty:
            continue

        n_l, n_s = len(pwc), len(sub)
        origins = np.repeat(pwc["pandana_node"].to_numpy(), n_s)
        dests = np.tile(sub["pandana_node"].to_numpy(), n_l)
        lengths = np.asarray(
            network.shortest_path_lengths(origins.astype("int64"), dests.astype("int64"))
        ).reshape(n_l, n_s).astype("float64")
        lengths[lengths > cap_m] = np.inf

        # For each LSOA pick the k smallest finite distances.
        for li in range(n_l):
            row = lengths[li]
            finite = np.isfinite(row)
            if not finite.any():
                continue
            cand_idx = np.where(finite)[0]
            cand_d = row[cand_idx]
            order = np.argsort(cand_d)[:k]
            chosen_idx = cand_idx[order]
            chosen_d = cand_d[order]
            inv = 1.0 / np.maximum(chosen_d, 1.0)
            inv = inv / inv.sum()
            for si, d, w in zip(chosen_idx, chosen_d, inv):
                rows.append(
                    {
                        pwc_id_col: pwc.iloc[li][pwc_id_col],
                        school_id_col: sub.iloc[si][school_id_col],
                        "phase": sub.iloc[si][phase_col],
                        "phase_cat": cat,
                        "distance_m": float(d),
                        "weight": float(w),
                        pwc_pop_col: pwc.iloc[li].get(pwc_pop_col),
                        "origin_node": int(pwc.iloc[li]["pandana_node"]),
                        "dest_node": int(sub.iloc[si]["pandana_node"]),
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
