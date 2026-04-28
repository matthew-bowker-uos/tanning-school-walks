"""GIAS schools acquisition, filtering, and basic spatial validation.

Stage 1 (per the project plan). Strict point-in-polygon validation against
LA boundaries is deferred to Stage 2 once the boundary layer is loaded; the
audits here are everything that can be done with GIAS alone.

Design notes:

- We filter on GIAS's own ``LA (name)`` column rather than its DfE LA code so
  we do not depend on a DfE→ONS mapping that drifts between LGR re-orgs.
  ``GIAS_LA_NAME_TO_ONS`` in :mod:`schools_sunbeds.config` records the small
  set of name variants we have to absorb.
- Phase / type / status filters follow spec §4 and DEC-009 (no Huff/gravity);
  FE, sixth-form, and independent schools are *retained but flagged* in a
  separate table for sensitivity analysis (DEC-010 family).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from schools_sunbeds.config import (
    CRS_BNG,
    GIAS_LA_NAME_TO_ONS,
    LA_CODES_NE,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Inclusion criteria (spec §4)

# In-scope phases include the two "middle deemed" categories so a school
# whose LEA classifies it as middle-deemed-primary still counts as primary
# for our purposes.
PHASES_IN_SCOPE: frozenset[str] = frozenset(
    {
        "Primary",
        "Middle deemed primary",
        "Secondary",
        "Middle deemed secondary",
        "All-through",
        "Special",
    }
)

# State-funded establishment type groups we keep. Excludes Independent,
# Other, and FE/HE.
TYPE_GROUPS_IN_SCOPE: frozenset[str] = frozenset(
    {
        "Local authority maintained schools",
        "Academies",
        "Free Schools",
        "Special schools",
    }
)

# A school is "currently operating" if its status is one of these.
STATUSES_IN_SCOPE: frozenset[str] = frozenset(
    {
        "Open",
        "Open, but proposed to close",
    }
)

# Type group(s) that we keep regardless of their PhaseOfEducation. GIAS
# records most special schools with phase = "Not applicable", so a strict
# phase filter would drop them. The carve-out below keeps any row whose
# type_group is in this set even if its phase is not in PHASES_IN_SCOPE.
TYPE_GROUPS_PHASE_EXEMPT: frozenset[str] = frozenset({"Special schools"})

# Loose bbox sanity check (BNG metres) — used only to flag impossible coords.
# NE England fits inside this box with margin; anything outside is corrupt.
# North Northumberland (Berwick) sits at roughly northing 655 km, so the
# upper edge is set to 670 km to leave a small margin.
BBOX_BNG_NE: tuple[float, float, float, float] = (350_000, 480_000, 480_000, 670_000)

# GIAS canonical column names we read. Column rename to snake_case happens in
# :func:`load_gias_csv` so downstream code does not have to keep quoting.
_GIAS_COLUMN_RENAMES: dict[str, str] = {
    "URN": "urn",
    "EstablishmentName": "establishment_name",
    "LA (code)": "la_code_dfe",
    "LA (name)": "la_name_gias",
    "EstablishmentTypeGroup (name)": "type_group",
    "TypeOfEstablishment (name)": "type_of_establishment",
    "PhaseOfEducation (name)": "phase",
    "EstablishmentStatus (name)": "status",
    "OpenDate": "open_date",
    "CloseDate": "close_date",
    "Easting": "easting",
    "Northing": "northing",
    "Postcode": "postcode",
    "NumberOfPupils": "n_pupils",
    "StatutoryLowAge": "stat_low_age",
    "StatutoryHighAge": "stat_high_age",
    "Gender (name)": "gender",
}


# ---------------------------------------------------------------------------
# Download


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _http_get_bytes(url: str, timeout: int = 120) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def fetch_gias_snapshot(target_dir: Path, date_str: str | None = None) -> Path:
    """Download today's GIAS ``edubasealldata`` snapshot, or a specific date.

    GIAS publishes a daily CSV at a predictable URL. If the auto-download
    fails (URL pattern changes, today's snapshot not yet published, etc.) we
    raise with a clear instruction telling the user where to download
    manually and where to put the file. The hash is identical either way.
    """

    if date_str is None:
        date_str = datetime.now(UTC).strftime("%Y%m%d")

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"edubasealldata{date_str}.csv"

    if target_path.exists() and target_path.stat().st_size > 0:
        log.info("Using existing GIAS snapshot at %s", target_path)
        return target_path

    candidate_urls = (
        f"https://ea-edubase-api-prod.azurewebsites.net/edubase/downloads/public/edubasealldata{date_str}.csv",
        f"https://www.get-information-schools.service.gov.uk/Downloads/edubasealldata{date_str}.csv",
    )

    last_status: int | None = None
    for url in candidate_urls:
        try:
            content = _http_get_bytes(url)
            target_path.write_bytes(content)
            log.info("Downloaded GIAS snapshot from %s -> %s", url, target_path)
            return target_path
        except requests.HTTPError as exc:
            last_status = exc.response.status_code
            log.info("GIAS URL %s returned %s; trying next candidate", url, last_status)

    msg = (
        "GIAS auto-download failed for date "
        f"{date_str} (last HTTP status {last_status}). "
        "Visit https://get-information-schools.service.gov.uk/Downloads, "
        "select 'Establishment fields CSV — All establishment data', save the "
        f"resulting file as {target_path}, and re-run."
    )
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Load and filter


def load_gias_csv(path: Path) -> pd.DataFrame:
    """Read the raw GIAS CSV and rename the columns we use to snake_case.

    GIAS publishes the CSV in Windows-1252; we set the encoding explicitly
    so non-ASCII school names round-trip cleanly.
    """

    df = pd.read_csv(
        path,
        encoding="cp1252",
        low_memory=False,
        dtype={"URN": "Int64"},
    )
    available = {old: new for old, new in _GIAS_COLUMN_RENAMES.items() if old in df.columns}
    missing = set(_GIAS_COLUMN_RENAMES) - set(available)
    if missing:
        log.warning("GIAS CSV missing expected columns: %s", sorted(missing))
    df = df.rename(columns=available)
    return df


def filter_in_scope(
    df: pd.DataFrame,
    *,
    la_name_to_ons: dict[str, str] = GIAS_LA_NAME_TO_ONS,
    la_codes_ne: Iterable[str] = LA_CODES_NE,
    phases: Iterable[str] = PHASES_IN_SCOPE,
    type_groups: Iterable[str] = TYPE_GROUPS_IN_SCOPE,
    type_groups_phase_exempt: Iterable[str] = TYPE_GROUPS_PHASE_EXEMPT,
    statuses: Iterable[str] = STATUSES_IN_SCOPE,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Return (in-scope df, audit dict).

    Filter logic:
        in_scope ⇔ ONS LA in NE
                  ∧ status ∈ STATUSES_IN_SCOPE
                  ∧ type_group ∈ TYPE_GROUPS_IN_SCOPE
                  ∧ (phase ∈ PHASES_IN_SCOPE
                     ∨ type_group ∈ TYPE_GROUPS_PHASE_EXEMPT)

    The phase carve-out keeps special schools (which GIAS records with
    ``phase = "Not applicable"``) in scope.

    Audit dict keys:
        - ``initial_n``                   rows in the input
        - ``after_la_n``                  after restricting to NE LAs
        - ``after_phase_or_exempt_n``     after phase OR type-exempt filter
        - ``after_type_n``                after establishment-type-group filter
        - ``after_status_n``              after status filter (= retained_n)
        - ``unmatched_la_names``          LA names in NE that did not map
        - ``per_la_phase``                retained counts by ONS LA × phase
    """

    audit: dict[str, Any] = {"initial_n": len(df)}

    df = df.copy()
    df["la_code_ons"] = df["la_name_gias"].map(la_name_to_ons)
    la_codes_set = set(la_codes_ne)

    in_ne = df["la_code_ons"].isin(la_codes_set)
    audit["unmatched_la_names"] = sorted(
        set(df.loc[df["la_name_gias"].isin(la_name_to_ons.keys()) & ~in_ne, "la_name_gias"])
    )
    df = df.loc[in_ne].copy()
    audit["after_la_n"] = len(df)

    phases_set = set(phases)
    exempt_set = set(type_groups_phase_exempt)
    keep_phase = df["phase"].isin(phases_set) | df["type_group"].isin(exempt_set)
    df = df.loc[keep_phase]
    audit["after_phase_or_exempt_n"] = len(df)

    type_groups_set = set(type_groups)
    df = df.loc[df["type_group"].isin(type_groups_set)]
    audit["after_type_n"] = len(df)

    statuses_set = set(statuses)
    df = df.loc[df["status"].isin(statuses_set)]
    audit["after_status_n"] = len(df)

    audit["per_la_phase"] = (
        df.groupby(["la_code_ons", "phase"]).size().unstack(fill_value=0).sort_index()
    )
    return df, audit


def filter_sensitivity_layer(df: pd.DataFrame) -> pd.DataFrame:
    """Return the FE / sixth-form / independent rows in the NE for sensitivity.

    A row is in the sensitivity layer if it sits in an NE LA but does not
    meet the primary-cohort criteria — i.e. its type group is excluded
    (e.g. independent schools, FE/HE), or its phase is excluded *and* it is
    not a phase-exempt type (so the GIAS phase = "Not applicable" rows for
    Special schools stay in the primary cohort, not here).
    """

    df = df.copy()
    df["la_code_ons"] = df["la_name_gias"].map(GIAS_LA_NAME_TO_ONS)
    in_ne = df["la_code_ons"].isin(set(LA_CODES_NE))
    fails_type = ~df["type_group"].isin(TYPE_GROUPS_IN_SCOPE)
    fails_phase = ~df["phase"].isin(PHASES_IN_SCOPE) & ~df["type_group"].isin(
        TYPE_GROUPS_PHASE_EXEMPT
    )
    return df.loc[in_ne & (fails_type | fails_phase)].copy()


# ---------------------------------------------------------------------------
# Spatialise and audit


def to_geodataframe(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Build a GeoDataFrame in EPSG:27700 from GIAS Easting/Northing.

    Rows with missing coords are dropped; the count is logged.
    """

    has_coords = df["easting"].notna() & df["northing"].notna()
    n_dropped = (~has_coords).sum()
    if n_dropped:
        log.info("Dropping %d rows with missing easting/northing", n_dropped)
    df = df.loc[has_coords].copy()
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df["easting"], df["northing"]),
        crs=CRS_BNG,
    )


def audit_basic(gdf: gpd.GeoDataFrame) -> dict[str, Any]:
    """URN uniqueness, missing coords, and bbox-sanity check.

    Returned audit dict:
        - ``n_rows``
        - ``n_unique_urn``
        - ``duplicate_urns``  (list of URNs appearing more than once)
        - ``n_missing_coords`` (always 0 here because to_geodataframe drops them)
        - ``n_outside_ne_bbox``
        - ``per_la_phase`` count table by ONS LA × phase
    """

    duplicates = gdf.loc[gdf["urn"].duplicated(keep=False), "urn"].tolist()

    minx, miny, maxx, maxy = BBOX_BNG_NE
    inside = (
        (gdf.geometry.x.between(minx, maxx))
        & (gdf.geometry.y.between(miny, maxy))
    )
    n_outside = int((~inside).sum())

    per_la_phase = (
        gdf.groupby(["la_code_ons", "phase"]).size().unstack(fill_value=0).sort_index()
    )

    return {
        "n_rows": int(len(gdf)),
        "n_unique_urn": int(gdf["urn"].nunique()),
        "duplicate_urns": duplicates,
        "n_missing_coords": 0,
        "n_outside_ne_bbox": n_outside,
        "per_la_phase": per_la_phase,
    }


__all__ = [
    "BBOX_BNG_NE",
    "PHASES_IN_SCOPE",
    "STATUSES_IN_SCOPE",
    "TYPE_GROUPS_IN_SCOPE",
    "TYPE_GROUPS_PHASE_EXEMPT",
    "audit_basic",
    "fetch_gias_snapshot",
    "filter_in_scope",
    "filter_sensitivity_layer",
    "load_gias_csv",
    "to_geodataframe",
]
