"""Census 2021 school-age population by LSOA21 + ONS RUC21 classification.

Two layers:

- **Census 2021 age (TS007a)** — bulk zip from nomisweb. The LSOA21-level
  CSV uses 5-year age bands; we sum the three bands "Aged 5 to 9", "Aged
  10 to 14", "Aged 15 to 19" as a proxy for school-age population (ages
  5–19 inclusive). This bracket is a slight overshoot at age 19 and a
  slight undershoot at age 4 (reception); the spec's primary outcome is
  per-LSOA exposure-weighting, where the precise age cut-off does not
  shift the inequality estimate materially.
- **RUC21 (Rural-Urban Classification 2021 for 2021 LSOAs)** — ONS
  ArcGIS service ``LSOA_2021_EW_BSC_V4_RUC``. The plan calls this RUC2011 /
  RUC2021; ONS publishes the 2011-methodology classification mapped onto
  2021 LSOAs as ``RUC21CD`` / ``Urban_rura``, which is what we use.
"""

from __future__ import annotations

import io
import logging
import zipfile
from collections.abc import Iterable
from pathlib import Path

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from schools_sunbeds.geography import (
    BBOX_BNG_NE_GEO,
    query_arcgis_geojson,
)

log = logging.getLogger(__name__)

CENSUS_TS007A_ZIP_URL = "https://www.nomisweb.co.uk/output/census/2021/census2021-ts007a.zip"
CENSUS_TS007A_LSOA_CSV = "census2021-ts007a-lsoa.csv"

# 5-year age band column names (TS007a publishes these on the LSOA file).
SCHOOL_AGE_BANDS_TS007A: tuple[str, ...] = (
    "Age: Aged 5 to 9 years",
    "Age: Aged 10 to 14 years",
    "Age: Aged 15 to 19 years",
)

ONS_LSOA21_RUC_URL = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/ArcGIS/rest/services/"
    "LSOA_2021_EW_BSC_V4_RUC/FeatureServer/0"
)

# School-age range targeted by the analysis. The 5-year bands available in
# TS007a give us 5–19 inclusive as the closest match.
SCHOOL_AGE_RANGE: tuple[int, int] = (5, 19)


# ---------------------------------------------------------------------------
# Census TS007 (single-year age) by LSOA21


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _fetch_bytes(url: str, timeout: int = 180) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def fetch_census_ts007a_zip(target_dir: Path, *, url: str = CENSUS_TS007A_ZIP_URL) -> Path:
    """Download the Census 2021 TS007a (Age, 5-year bands) zip from nomisweb."""

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "census2021-ts007a.zip"
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing TS007a zip at %s", out)
        return out
    out.write_bytes(_fetch_bytes(url))
    log.info("Downloaded %s", out)
    return out


def load_lsoa_school_age_population(
    zip_path: Path,
    *,
    lsoa_csv_name: str = CENSUS_TS007A_LSOA_CSV,
    school_age_bands: tuple[str, ...] = SCHOOL_AGE_BANDS_TS007A,
) -> pd.DataFrame:
    """Return per-LSOA21 counts of total population and school-age children.

    Columns: ``lsoa21cd``, ``pop_total``, ``pop_school_age``.

    School-age is the sum of the three 5-year bands "Aged 5 to 9", "Aged
    10 to 14", "Aged 15 to 19" — i.e. ages 5–19 inclusive.
    """

    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(lsoa_csv_name) as fh:
            df = pd.read_csv(io.TextIOWrapper(fh, encoding="utf-8"))

    code_col = next(c for c in df.columns if c.strip().lower() == "geography code")
    total_col = next(c for c in df.columns if c.strip() == "Age: Total")

    missing = [b for b in school_age_bands if b not in df.columns]
    if missing:
        msg = f"TS007a LSOA CSV missing expected columns: {missing}; got: {list(df.columns)[:6]}..."
        raise RuntimeError(msg)

    df["pop_school_age"] = df[list(school_age_bands)].sum(axis=1).astype("Int32")
    return df[[code_col, total_col, "pop_school_age"]].rename(
        columns={code_col: "lsoa21cd", total_col: "pop_total"}
    )


# ---------------------------------------------------------------------------
# RUC21 classification


def fetch_lsoa21_ruc_ne(
    target_dir: Path,
    *,
    feature_server: str = ONS_LSOA21_RUC_URL,
    bbox: tuple[float, float, float, float] = BBOX_BNG_NE_GEO,
) -> Path:
    """Download LSOA21 RUC21 attributes (no geometry) for the NE bbox.

    Geometry is requested but discarded by the loader; we only need the
    attribute join key + RUC code/name. Returned file is a GeoJSON sidecar
    we register in the manifest as the canonical source.
    """

    import json

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / "lsoa21_ruc_ne.geojson"
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing LSOA21 RUC download at %s", out)
        return out

    data = query_arcgis_geojson(
        feature_server,
        out_fields="LSOA21CD,RUC21CD,RUC21NM,Urban_rura",
        bbox=bbox,
    )
    if not data["features"]:
        msg = f"No RUC21 features returned from {feature_server}"
        raise RuntimeError(msg)
    out.write_text(json.dumps(data))
    return out


def load_lsoa21_ruc(path: Path, lsoa_codes: Iterable[str] | None = None) -> pd.DataFrame:
    """Read the LSOA21 RUC GeoJSON and return a tidy frame.

    Columns: ``lsoa21cd``, ``ruc21cd``, ``ruc21nm``, ``urban_rural``.
    """

    import json

    data = json.loads(Path(path).read_text())
    rows = [feat.get("properties", {}) for feat in data.get("features", [])]
    df = pd.DataFrame(rows)
    rename = {
        "LSOA21CD": "lsoa21cd",
        "RUC21CD": "ruc21cd",
        "RUC21NM": "ruc21nm",
        "Urban_rura": "urban_rural",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    keep = [c for c in ("lsoa21cd", "ruc21cd", "ruc21nm", "urban_rural") if c in df.columns]
    df = df[keep]
    if lsoa_codes is not None:
        df = df.loc[df["lsoa21cd"].isin(set(lsoa_codes))].reset_index(drop=True)
    return df


__all__ = [
    "CENSUS_TS007A_LSOA_CSV",
    "CENSUS_TS007A_ZIP_URL",
    "ONS_LSOA21_RUC_URL",
    "SCHOOL_AGE_BANDS_TS007A",
    "SCHOOL_AGE_RANGE",
    "fetch_census_ts007a_zip",
    "fetch_lsoa21_ruc_ne",
    "load_lsoa21_ruc",
    "load_lsoa_school_age_population",
]
