"""IMD2025 + IDACI loader.

MHCLG publishes the Indices of Deprivation 2025 as a set of xlsx files at
https://www.gov.uk/government/statistics/english-indices-of-deprivation-2025
. We use:

- **File 1** ("Index of Multiple Deprivation") for IMD rank, decile, and the
  LSOA→LAD2024 join.
- **File 3** ("Supplementary Indices: IDACI and IDAOPI") for the
  Income Deprivation Affecting Children Index sub-domain.

Files are downloaded with retry into ``data/raw/imd2025/<date>/``, hashed,
and registered in the manifest. Loaders rename to a small canonical schema:

    lsoa21cd, imd_rank, imd_decile         (load_imd2025_main)
    lsoa21cd, idaci_rank, idaci_decile     (load_imd2025_idaci)

Sub-domain ranks vary in column naming across MHCLG releases; the loaders
auto-detect by matching column headers against known patterns and raise
clearly if the spreadsheet structure has changed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)

# Direct asset URLs scraped from the gov.uk landing page on 2026-04-28. They
# point at hashed paths under assets.publishing.service.gov.uk and are
# stable for the lifetime of the publication.
IMD2025_FILE1_URL = (
    "https://assets.publishing.service.gov.uk/media/"
    "691dece32c6b98ecdbc500d5/File_1_IoD2025_Index_of_Multiple_Deprivation.xlsx"
)
IMD2025_FILE3_URL = (
    "https://assets.publishing.service.gov.uk/media/"
    "691ded0a21ef5aaa6543efe9/File_3_IoD2025_Supplementary_Indices_IDACI_and_IDAOPI.xlsx"
)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _http_get(url: str, timeout: int = 120) -> bytes:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def fetch_imd2025_xlsx(target_dir: Path, *, url: str, filename: str) -> Path:
    """Download a single IMD2025 xlsx from gov.uk into ``target_dir``."""

    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    out = target_dir / filename
    if out.exists() and out.stat().st_size > 0:
        log.info("Using existing IMD2025 file at %s", out)
        return out

    out.write_bytes(_http_get(url))
    log.info("Downloaded %s -> %s", url, out)
    return out


def fetch_imd2025_file1(target_dir: Path, *, url: str = IMD2025_FILE1_URL) -> Path:
    return fetch_imd2025_xlsx(
        target_dir, url=url, filename="File_1_IoD2025_Index_of_Multiple_Deprivation.xlsx"
    )


def fetch_imd2025_file3(target_dir: Path, *, url: str = IMD2025_FILE3_URL) -> Path:
    return fetch_imd2025_xlsx(
        target_dir,
        url=url,
        filename="File_3_IoD2025_Supplementary_Indices_IDACI_and_IDAOPI.xlsx",
    )


# ---------------------------------------------------------------------------
# Loaders


_LSOA_CODE_PATTERNS = (
    re.compile(r"lsoa.*code.*2021", re.IGNORECASE),
    re.compile(r"lsoa.*2021.*code", re.IGNORECASE),
)
_IMD_RANK_PATTERNS = (
    re.compile(r"index of multiple deprivation.*rank", re.IGNORECASE),
    re.compile(r"^imd.*rank", re.IGNORECASE),
)
_IMD_DECILE_PATTERNS = (
    re.compile(r"index of multiple deprivation.*decile", re.IGNORECASE),
    re.compile(r"^imd.*decile", re.IGNORECASE),
)
_IDACI_RANK_PATTERNS = (
    re.compile(r"idaci.*rank", re.IGNORECASE),
    re.compile(r"income deprivation affecting children.*rank", re.IGNORECASE),
)
_IDACI_DECILE_PATTERNS = (
    re.compile(r"idaci.*decile", re.IGNORECASE),
    re.compile(r"income deprivation affecting children.*decile", re.IGNORECASE),
)


def _match_first(columns: pd.Index, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    for col in columns:
        for pat in patterns:
            if pat.search(str(col)):
                return col
    return None


def _read_data_sheet(path: Path) -> pd.DataFrame:
    """Read the first non-'Notes' sheet of an MHCLG IMD xlsx.

    MHCLG bundles its IMD spreadsheets with a Notes sheet first; the actual
    data lives on the second sheet (e.g. ``IMD25`` or
    ``IoD2025 IDACI & IDAOPI``). Skipping anything called "Notes" picks the
    data sheet without depending on its specific name.
    """

    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for name in wb.sheetnames:
        if name.strip().lower() == "notes":
            continue
        return pd.read_excel(path, sheet_name=name, engine="openpyxl")
    return pd.read_excel(path, sheet_name=0, engine="openpyxl")


def load_imd2025_main(path: Path) -> pd.DataFrame:
    """Read File 1 and return a 3-column tidy frame.

    Returns columns: ``lsoa21cd``, ``imd_rank``, ``imd_decile`` — one row
    per LSOA21.
    """

    df = _read_data_sheet(path)
    lsoa = _match_first(df.columns, _LSOA_CODE_PATTERNS)
    rank = _match_first(df.columns, _IMD_RANK_PATTERNS)
    decile = _match_first(df.columns, _IMD_DECILE_PATTERNS)
    if not all((lsoa, rank, decile)):
        msg = (
            f"IMD2025 File 1 column auto-detect failed (got lsoa={lsoa!r}, "
            f"rank={rank!r}, decile={decile!r}); columns were: {list(df.columns)}"
        )
        raise RuntimeError(msg)

    out = df[[lsoa, rank, decile]].rename(
        columns={lsoa: "lsoa21cd", rank: "imd_rank", decile: "imd_decile"}
    )
    out["imd_decile"] = pd.to_numeric(out["imd_decile"], errors="coerce").astype("Int8")
    out["imd_rank"] = pd.to_numeric(out["imd_rank"], errors="coerce").astype("Int32")
    out["imd_quintile"] = out["imd_decile"].map(
        lambda d: pd.NA if pd.isna(d) else (int(d) + 1) // 2
    ).astype("Int8")
    return out


def load_imd2025_idaci(path: Path) -> pd.DataFrame:
    """Read File 3 (Supplementary Indices) and return IDACI columns.

    Returns columns: ``lsoa21cd``, ``idaci_rank``, ``idaci_decile``.
    """

    df = _read_data_sheet(path)
    lsoa = _match_first(df.columns, _LSOA_CODE_PATTERNS)
    rank = _match_first(df.columns, _IDACI_RANK_PATTERNS)
    decile = _match_first(df.columns, _IDACI_DECILE_PATTERNS)
    if not all((lsoa, rank, decile)):
        msg = (
            f"IMD2025 File 3 IDACI column auto-detect failed (got lsoa={lsoa!r}, "
            f"rank={rank!r}, decile={decile!r}); columns were: {list(df.columns)}"
        )
        raise RuntimeError(msg)

    out = df[[lsoa, rank, decile]].rename(
        columns={lsoa: "lsoa21cd", rank: "idaci_rank", decile: "idaci_decile"}
    )
    out["idaci_decile"] = pd.to_numeric(out["idaci_decile"], errors="coerce").astype("Int8")
    out["idaci_rank"] = pd.to_numeric(out["idaci_rank"], errors="coerce").astype("Int32")
    out["idaci_quintile"] = out["idaci_decile"].map(
        lambda d: pd.NA if pd.isna(d) else (int(d) + 1) // 2
    ).astype("Int8")
    return out


__all__ = [
    "IMD2025_FILE1_URL",
    "IMD2025_FILE3_URL",
    "fetch_imd2025_file1",
    "fetch_imd2025_file3",
    "fetch_imd2025_xlsx",
    "load_imd2025_idaci",
    "load_imd2025_main",
]
