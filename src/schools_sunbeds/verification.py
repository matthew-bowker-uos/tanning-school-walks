"""Manual verification of every Google Places + OSM Overpass salon record.

Workflow:

1. After Stage 3 the audit notebook calls :func:`update_verification_csv`,
   which writes ``audit_logs/manual_verification.csv`` with one row per
   distinct place across both sources. Each row has a stable
   ``verification_id`` of the form ``google:<place_id>`` or ``osm:<osm_id>``.
2. The user opens the CSV (Excel / Numbers / VSCode) and edits four
   columns: ``status``, ``reviewer``, ``review_date_utc``, ``notes``.
3. Stage 6 (exposure measurement) calls :func:`apply_verification` to
   filter the raw enumerations to verified premises before the analysis.
   The function writes a hash + summary line to ``audit_logs/verification_apply_<utc>.json``
   every time it is run, so the manuscript can quote the exact verification
   state used for the headline numbers.

Status vocabulary:

    - ``pending``    not yet reviewed (default for fresh entries)
    - ``confirmed``  real commercial tanning salon — keep
    - ``rejected``   not a salon (e.g. Tesco, hairdresser with no sunbeds)
    - ``unsure``     could not tell — keep but flag (sensitivity-only)
    - ``duplicate``  same physical premises as another verification_id;
                     ``duplicate_of`` column points to the canonical id
    - ``closed``     real, but no longer operating

Re-running ``update_verification_csv`` is idempotent: it preserves any
human-edited rows and appends ``pending`` rows for any new ``verification_id``
seen in the latest enumeration.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)

VERIFICATION_STATUSES: tuple[str, ...] = (
    "pending",
    "confirmed",
    "rejected",
    "unsure",
    "duplicate",
    "closed",
)
DEFAULT_KEEP_STATUSES: tuple[str, ...] = ("confirmed", "unsure")

VERIFICATION_COLUMNS: tuple[str, ...] = (
    "verification_id",
    "source",
    "source_id",
    "name",
    "address",
    "lad_code",
    "imd_quintile",
    "lat",
    "lon",
    # Editable columns:
    "status",
    "reviewer",
    "review_date_utc",
    "duplicate_of",
    "notes",
)


def make_verification_id(source: str, source_id: str) -> str:
    """Stable id of the form ``<source>:<source_id>``."""
    return f"{source}:{source_id}"


# ---------------------------------------------------------------------------
# Build / update the CSV


def _gdf_to_verification_rows(
    gdf: gpd.GeoDataFrame,
    *,
    source: str,
    source_id_col: str,
    name_col: str,
    address_col: str,
) -> pd.DataFrame:
    """Project a salon GeoDataFrame to the verification schema (no status cols)."""

    if gdf.empty:
        return pd.DataFrame(columns=list(VERIFICATION_COLUMNS))
    g_wgs = gdf.to_crs("EPSG:4326")
    out = pd.DataFrame(
        {
            "verification_id": gdf[source_id_col].map(lambda x: make_verification_id(source, str(x))),
            "source": source,
            "source_id": gdf[source_id_col].astype(str),
            "name": gdf[name_col].fillna(""),
            "address": gdf[address_col].fillna("") if address_col in gdf.columns else "",
            "lad_code": gdf["lad_code"] if "lad_code" in gdf.columns else "",
            "imd_quintile": gdf["imd_quintile"] if "imd_quintile" in gdf.columns else pd.NA,
            "lat": g_wgs.geometry.y,
            "lon": g_wgs.geometry.x,
            "status": "pending",
            "reviewer": "",
            "review_date_utc": "",
            "duplicate_of": "",
            "notes": "",
        }
    )
    return out[list(VERIFICATION_COLUMNS)]


def update_verification_csv(
    csv_path: Path,
    *,
    google_gdf: gpd.GeoDataFrame | None = None,
    osm_gdf: gpd.GeoDataFrame | None = None,
) -> tuple[Path, dict[str, int]]:
    """Idempotent build/update of the verification CSV.

    Existing rows are preserved verbatim. Any place from the supplied
    GeoDataFrames whose ``verification_id`` is not already in the CSV is
    appended as a ``pending`` row. No row is ever deleted by this function.
    """

    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    new_rows: list[pd.DataFrame] = []
    if google_gdf is not None and not google_gdf.empty:
        new_rows.append(
            _gdf_to_verification_rows(
                google_gdf,
                source="google",
                source_id_col="place_id",
                name_col="name",
                address_col="address",
            )
        )
    if osm_gdf is not None and not osm_gdf.empty:
        addr_col = "addr_full" if "addr_full" in osm_gdf.columns else "address"
        new_rows.append(
            _gdf_to_verification_rows(
                osm_gdf,
                source="osm",
                source_id_col="osm_id",
                name_col="name",
                address_col=addr_col,
            )
        )
    new_df = (
        pd.concat(new_rows, ignore_index=True)
        if new_rows
        else pd.DataFrame(columns=list(VERIFICATION_COLUMNS))
    )

    if csv_path.exists() and csv_path.stat().st_size > 0:
        existing = pd.read_csv(
            csv_path,
            dtype={c: "string" for c in VERIFICATION_COLUMNS if c != "imd_quintile"},
            keep_default_na=False,
        )
        # Preserve existing rows; only append unseen verification_ids.
        seen = set(existing["verification_id"])
        to_append = new_df.loc[~new_df["verification_id"].isin(seen)]
        merged = pd.concat([existing, to_append], ignore_index=True)
    else:
        existing = pd.DataFrame(columns=list(VERIFICATION_COLUMNS))
        to_append = new_df
        merged = new_df

    # Ensure all expected columns exist (a previously-edited file might have
    # been opened in Excel and dropped a column).
    for col in VERIFICATION_COLUMNS:
        if col not in merged.columns:
            merged[col] = "" if col != "imd_quintile" else pd.NA
    merged = merged[list(VERIFICATION_COLUMNS)]
    merged = merged.sort_values(["source", "lad_code", "name"]).reset_index(drop=True)
    merged.to_csv(csv_path, index=False)

    audit = {
        "rows_total": int(len(merged)),
        "rows_existing_kept": int(len(existing)),
        "rows_new_appended": int(len(to_append)),
    }
    log.info("Verification CSV %s: %s", csv_path, audit)
    return csv_path, audit


# ---------------------------------------------------------------------------
# Apply / summarise


def load_verification(csv_path: Path) -> pd.DataFrame:
    """Read the verification CSV and validate its schema."""

    df = pd.read_csv(
        csv_path,
        dtype={c: "string" for c in VERIFICATION_COLUMNS if c != "imd_quintile"},
        keep_default_na=False,
    )
    missing = set(VERIFICATION_COLUMNS) - set(df.columns)
    if missing:
        msg = f"Verification CSV {csv_path} missing columns: {missing}"
        raise RuntimeError(msg)
    invalid_status = set(df["status"].dropna()) - set(VERIFICATION_STATUSES)
    if invalid_status:
        msg = f"Unknown verification status values in {csv_path}: {invalid_status}"
        raise RuntimeError(msg)
    return df


def verification_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Count rows by source × status."""

    return (
        df.groupby(["source", "status"]).size().unstack(fill_value=0).sort_index()
    )


def apply_verification(
    places_gdf: gpd.GeoDataFrame,
    verification_df: pd.DataFrame,
    *,
    source: str,
    source_id_col: str,
    keep_statuses: Iterable[str] = DEFAULT_KEEP_STATUSES,
    audit_dir: Path | None = None,
) -> tuple[gpd.GeoDataFrame, dict[str, int]]:
    """Filter ``places_gdf`` to rows whose verification status is in ``keep_statuses``.

    Rows whose ``verification_id`` is not in the CSV (e.g. a place added to
    Google after the verification file was last updated) are dropped from
    the kept set so the analysis only ever runs on reviewed records — set
    ``keep_statuses=(*VERIFICATION_STATUSES,)`` to disable that effect.

    If ``audit_dir`` is supplied, an audit JSON is written there so the
    manuscript can quote the exact verification state used.
    """

    keep = set(keep_statuses)
    sub = verification_df.loc[verification_df["source"] == source].copy()
    keep_ids = set(sub.loc[sub["status"].isin(keep), "source_id"])

    mask = places_gdf[source_id_col].astype(str).isin(keep_ids)
    out = places_gdf.loc[mask].copy()

    audit = {
        "n_input": int(len(places_gdf)),
        "n_in_verification_csv": int(places_gdf[source_id_col].astype(str).isin(set(sub["source_id"])).sum()),
        "n_kept": int(len(out)),
        "n_excluded_by_status": int(len(places_gdf) - len(out)),
        "keep_statuses": sorted(keep),
        "source": source,
    }

    if audit_dir is not None:
        audit_dir = Path(audit_dir)
        audit_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = audit_dir / f"verification_apply_{source}_{ts}.json"
        path.write_text(json.dumps(audit, indent=2))
        log.info("Wrote verification apply audit to %s", path)

    return out, audit


__all__ = [
    "DEFAULT_KEEP_STATUSES",
    "VERIFICATION_COLUMNS",
    "VERIFICATION_STATUSES",
    "apply_verification",
    "load_verification",
    "make_verification_id",
    "update_verification_csv",
    "verification_summary",
]
