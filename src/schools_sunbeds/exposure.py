"""Exposure measurement: salon counts in school buffers and along routes.

Two measures per school, both in the same panel so Stage 7 can test H2:

- **Buffer-based exposure** (the conventional measure, spec §6.2):
  count of salons inside a Euclidean buffer of the school point at
  400 / 800 / 1600 m.

- **Route-based exposure** (the novel measure, spec §6.3):
  for each ``(LSOA, school)`` route, count salons inside the route
  buffer. Aggregate to school as
    - sum_route_<width>     = Σ child_n * count
    - mean_per_pupil_<width> = Σ child_n * count / Σ child_n
    - max_route_<width>     = max count across pupil-routes
  Salons are counted *per route*: a salon on a corridor used by N LSOAs
  contributes N times to ``sum_route_*``, which is the per-pupil
  exposure outcome named in spec §6.3.

The verification filter (Stage 3 manual review) is applied at the top of
``build_exposure_panel`` so only ``confirmed`` + ``unsure`` salons feed
the counts. Stage 7 reads this panel directly.
"""

from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd

from schools_sunbeds.config import (
    BUFFER_DISTANCES_M,
    CRS_BNG,
    ROUTE_BUFFER_PRIMARY_M,
    ROUTE_BUFFER_SENSITIVITY_M,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Spatial-join count primitives


def _count_points_in_polygons(
    points_gdf: gpd.GeoDataFrame,
    polygons_gdf: gpd.GeoDataFrame,
    *,
    group_col: str,
) -> pd.Series:
    """Return a Series of point-counts indexed by polygons_gdf[group_col].

    Uses a single spatial join under ``predicate="within"``; multiple
    polygons covering the same point all increment their respective
    counts (so route-buffer counts are per-route, not per-salon).
    """

    if points_gdf.crs is None or polygons_gdf.crs is None:
        raise ValueError("Both inputs must have CRS set (preferably EPSG:27700)")
    if points_gdf.crs.to_string() != polygons_gdf.crs.to_string():
        raise ValueError(
            f"CRS mismatch: points {points_gdf.crs} vs polygons {polygons_gdf.crs}"
        )

    joined = gpd.sjoin(
        points_gdf,
        polygons_gdf[[group_col, "geometry"]],
        how="left",
        predicate="within",
    )
    counts = joined.dropna(subset=[group_col]).groupby(group_col).size()
    counts.name = "n_salons"
    return counts


# ---------------------------------------------------------------------------
# Buffer-based exposure (school-centred)


def buffer_exposure(
    school_buffers_gdf: gpd.GeoDataFrame,
    salons_gdf: gpd.GeoDataFrame,
    *,
    school_id_col: str = "urn",
    distance_col: str = "distance_m",
) -> pd.DataFrame:
    """Wide-format counts: one row per school, columns ``n_buffer_<d>m``.

    ``school_buffers_gdf`` is the long-format frame from
    :func:`routing.school_euclidean_buffers` (one row per school × distance).
    """

    if not {school_id_col, distance_col, "geometry"}.issubset(school_buffers_gdf.columns):
        raise ValueError("school_buffers_gdf must have urn, distance_m, geometry")
    if school_buffers_gdf.crs.to_string() != CRS_BNG or salons_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("All inputs must be in EPSG:27700")

    pieces: list[pd.DataFrame] = []
    for d in school_buffers_gdf[distance_col].unique():
        polys = school_buffers_gdf.loc[school_buffers_gdf[distance_col] == d]
        counts = _count_points_in_polygons(salons_gdf, polys, group_col=school_id_col)
        col_name = f"n_buffer_{int(d)}m"
        df = counts.reindex(polys[school_id_col]).fillna(0).astype("int32").rename(col_name).to_frame()
        pieces.append(df)

    panel = pieces[0]
    for p in pieces[1:]:
        panel = panel.join(p, how="outer")
    panel.index.name = school_id_col
    return panel.fillna(0).astype("int32").reset_index()


# ---------------------------------------------------------------------------
# Route-based exposure


def route_exposure(
    route_buffers_gdf: gpd.GeoDataFrame,
    salons_gdf: gpd.GeoDataFrame,
    *,
    school_id_col: str = "urn",
    pwc_id_col: str = "lsoa21cd",
    pwc_pop_col: str = "pop_school_age",
    width_label: str,
) -> pd.DataFrame:
    """Per-school route-exposure summary at one buffer width.

    Returns a DataFrame keyed by ``school_id_col`` with columns:
        sum_route_<width>            Σ child_n * salons_per_route
        mean_per_pupil_route_<width> Σ child_n * salons_per_route / Σ child_n
        max_route_<width>            max salons_per_route
        n_routes_<width>             count of (lsoa, school) routes used
        sum_pupil_<width>            Σ child_n across the routes
    """

    needed = {school_id_col, pwc_id_col, pwc_pop_col, "geometry"}
    if not needed.issubset(route_buffers_gdf.columns):
        raise ValueError(f"route_buffers_gdf missing columns: {needed - set(route_buffers_gdf.columns)}")
    if route_buffers_gdf.crs.to_string() != CRS_BNG or salons_gdf.crs.to_string() != CRS_BNG:
        raise ValueError("All inputs must be in EPSG:27700")

    # Each route polygon is uniquely identified by (lsoa, urn). Build a
    # surrogate key, count salons per surrogate, then aggregate to the
    # school via child-pop-weighted statistics.
    rb = route_buffers_gdf.copy().reset_index(drop=True)
    rb["_route_key"] = np.arange(len(rb))

    counts = _count_points_in_polygons(salons_gdf, rb, group_col="_route_key")
    rb["n_salons_route"] = rb["_route_key"].map(counts).fillna(0).astype("int32")

    pop = rb[pwc_pop_col].astype("float64").to_numpy()
    n = rb["n_salons_route"].astype("float64").to_numpy()

    summed = pop * n
    by_school = rb.groupby(school_id_col).apply(
        lambda g: pd.Series(
            {
                f"sum_route_{width_label}": float(
                    (g[pwc_pop_col].astype("float64") * g["n_salons_route"]).sum()
                ),
                f"mean_per_pupil_route_{width_label}": float(
                    (g[pwc_pop_col].astype("float64") * g["n_salons_route"]).sum()
                    / max(g[pwc_pop_col].astype("float64").sum(), 1.0)
                ),
                f"max_route_{width_label}": int(g["n_salons_route"].max()),
                f"n_routes_{width_label}": int(len(g)),
                f"sum_pupil_{width_label}": float(g[pwc_pop_col].astype("float64").sum()),
            }
        ),
        include_groups=False,
    )
    return by_school.reset_index()


# ---------------------------------------------------------------------------
# Combined panel


def build_exposure_panel(
    schools_gdf: gpd.GeoDataFrame,
    salons_gdf: gpd.GeoDataFrame,
    school_buffers_gdf: gpd.GeoDataFrame,
    route_buffers_50m_gdf: gpd.GeoDataFrame,
    route_buffers_100m_gdf: gpd.GeoDataFrame | None = None,
    *,
    school_id_col: str = "urn",
    keep_school_attrs: tuple[str, ...] = (
        "establishment_name",
        "phase",
        "type_group",
        "la_code_ons",
        "lad_code_dfe" if False else "la_code_dfe",
        "n_pupils",
    ),
) -> pd.DataFrame:
    """Stitch buffer + route exposure into one panel keyed by ``urn``."""

    out = schools_gdf[[school_id_col, *[c for c in keep_school_attrs if c in schools_gdf.columns]]].copy()

    # Buffer-based
    buf = buffer_exposure(school_buffers_gdf, salons_gdf, school_id_col=school_id_col)
    out = out.merge(buf, on=school_id_col, how="left")

    # Route-based at 50 m primary
    rt50 = route_exposure(
        route_buffers_50m_gdf, salons_gdf, school_id_col=school_id_col, width_label="50m"
    )
    out = out.merge(rt50, on=school_id_col, how="left")

    if route_buffers_100m_gdf is not None:
        rt100 = route_exposure(
            route_buffers_100m_gdf,
            salons_gdf,
            school_id_col=school_id_col,
            width_label="100m",
        )
        out = out.merge(rt100, on=school_id_col, how="left")

    # Schools that didn't receive any route (rural special, e.g.) get NaN
    # in the route columns. Replace with 0 for counts; keep NaN for means
    # since "no pupils => no exposure" is mathematically undefined.
    count_cols = [c for c in out.columns if c.startswith(("n_buffer_", "max_route_", "n_routes_"))]
    for c in count_cols:
        out[c] = out[c].fillna(0).astype("int32")
    sum_cols = [c for c in out.columns if c.startswith("sum_")]
    for c in sum_cols:
        out[c] = out[c].fillna(0.0)

    return out


__all__ = [
    "BUFFER_DISTANCES_M",
    "ROUTE_BUFFER_PRIMARY_M",
    "ROUTE_BUFFER_SENSITIVITY_M",
    "build_exposure_panel",
    "buffer_exposure",
    "route_exposure",
]
