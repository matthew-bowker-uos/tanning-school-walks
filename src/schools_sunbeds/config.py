"""Project-wide constants and path resolution.

Resolution rules for ``DATA_ROOT``:
    1. The ``DATA_ROOT`` environment variable, if set.
    2. ``/content/drive/MyDrive/schools-sunbeds-data`` if running on Colab
       with Drive mounted.
    3. ``<repo>/data`` for local development.

Every other path constant in this module is derived from ``DATA_ROOT`` so
notebooks and ``src/`` modules never hard-code locations.
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Coordinate reference systems

CRS_BNG = "EPSG:27700"  # British National Grid; metres; analytic CRS
CRS_WGS84 = "EPSG:4326"  # used only at ingest/export boundaries

# ---------------------------------------------------------------------------
# Region scope

REGION_NAME = "North East England"
REGION_ITL1 = "TLC"

# ONS LAD22/LAD23 codes for the 12 upper-tier authorities listed in spec §4.
LA_CODES_NE: tuple[str, ...] = (
    "E06000047",  # County Durham
    "E06000005",  # Darlington
    "E08000037",  # Gateshead
    "E06000001",  # Hartlepool
    "E06000002",  # Middlesbrough
    "E08000021",  # Newcastle upon Tyne
    "E08000022",  # North Tyneside
    "E06000057",  # Northumberland
    "E06000003",  # Redcar and Cleveland
    "E08000023",  # South Tyneside
    "E06000004",  # Stockton-on-Tees
    "E08000024",  # Sunderland
)

LA_NAMES_NE: dict[str, str] = {
    "E06000047": "County Durham",
    "E06000005": "Darlington",
    "E08000037": "Gateshead",
    "E06000001": "Hartlepool",
    "E06000002": "Middlesbrough",
    "E08000021": "Newcastle upon Tyne",
    "E08000022": "North Tyneside",
    "E06000057": "Northumberland",
    "E06000003": "Redcar and Cleveland",
    "E08000023": "South Tyneside",
    "E06000004": "Stockton-on-Tees",
    "E08000024": "Sunderland",
}

# GIAS exposes LA via its own ``LA (name)`` column, which mostly matches the
# ONS LAD22/LAD23 name verbatim. The two differences are recorded here so
# string matching is reliable. Used by ``schools.filter_ne`` to map GIAS LA
# names to the ONS codes carried in :data:`LA_CODES_NE`.
GIAS_LA_NAME_TO_ONS: dict[str, str] = {
    "County Durham": "E06000047",
    "Durham": "E06000047",  # historical GIAS spelling
    "Darlington": "E06000005",
    "Gateshead": "E08000037",
    "Hartlepool": "E06000001",
    "Middlesbrough": "E06000002",
    "Newcastle upon Tyne": "E08000021",
    "North Tyneside": "E08000022",
    "Northumberland": "E06000057",
    "Redcar and Cleveland": "E06000003",
    "Redcar & Cleveland": "E06000003",  # alternate punctuation
    "South Tyneside": "E08000023",
    "Stockton-on-Tees": "E06000004",
    "Sunderland": "E08000024",
}

# Bounding box (approx) for the NE region in WGS84 (lon_min, lat_min, lon_max, lat_max).
# Used to clip OSM extracts and to grid the Google Places query plan.
REGION_BBOX_WGS84: tuple[float, float, float, float] = (-2.69, 54.40, -0.30, 55.85)

# ---------------------------------------------------------------------------
# Distance constants (metres)

# Spec §6.2 buffer distances.
BUFFER_DISTANCES_M: tuple[int, ...] = (400, 800, 1600)

# Sensitivity distances (DEC sensitivity #2).
BUFFER_DISTANCES_SENSITIVITY_M: tuple[int, ...] = (250, 400, 800, 1600)

# Route buffer widths.
ROUTE_BUFFER_PRIMARY_M: int = 50
ROUTE_BUFFER_SENSITIVITY_M: int = 100

# Catchment distance caps by phase (DEC-010, superseded by DEC-016).
# Walking-distance is capped at 5 km for every phase: anything longer
# would not realistically be walked and would inflate route lengths in
# rural Northumberland with little additional exposure signal.
CATCHMENT_CAP_M: dict[str, int] = {
    "primary": 2000,
    "secondary": 5000,
    "special": 5000,
}

# k-NN parameter for the IDW catchment sensitivity (DEC-011).
KNN_K_SENSITIVITY: int = 3

# ---------------------------------------------------------------------------
# Salon enumeration

GOOGLE_PLACES_QUERIES: tuple[str, ...] = ("tanning salon", "sunbed", "solarium")
GOOGLE_PLACES_GRID_CELL_KM: float = 2.0

OVERPASS_TAG_FILTERS: tuple[str, ...] = (
    'leisure="tanning_salon"',
    'shop="solarium"',
    'shop="beauty"][beauty="tanning"',
)

# ---------------------------------------------------------------------------
# Statistics

BOOTSTRAP_N: int = 1000
BOOTSTRAP_SEED: int = 20260428  # date file was authored; pinned in HYPOTHESES.md
RANDOM_SEED: int = 20260428
ALPHA: float = 0.05
BONFERRONI_K: int = 3  # H1, H2, H3

# ---------------------------------------------------------------------------
# Paths


def _detect_data_root() -> Path:
    """Resolve DATA_ROOT per the rules in this module's docstring."""

    env_root = os.environ.get("DATA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    colab_drive = Path("/content/drive/MyDrive/schools-sunbeds-data")
    if Path("/content/drive").exists():
        return colab_drive

    return REPO_ROOT / "data"


REPO_ROOT: Path = Path(__file__).resolve().parents[2]
DATA_ROOT: Path = _detect_data_root()

DATA_RAW: Path = DATA_ROOT / "raw"
DATA_INTERIM: Path = DATA_ROOT / "interim"
DATA_PROCESSED: Path = DATA_ROOT / "processed"
DATA_MANIFEST: Path = DATA_ROOT / "manifest.csv"

OUTPUTS_ROOT: Path = REPO_ROOT / "outputs"
OUTPUTS_TABLES: Path = OUTPUTS_ROOT / "tables"
OUTPUTS_FIGURES: Path = OUTPUTS_ROOT / "figures"

AUDIT_LOGS: Path = REPO_ROOT / "audit_logs"


def ensure_dirs() -> None:
    """Create every project directory that downstream code expects to exist."""

    for d in (
        DATA_RAW,
        DATA_INTERIM,
        DATA_PROCESSED,
        OUTPUTS_TABLES,
        OUTPUTS_FIGURES,
        AUDIT_LOGS,
    ):
        d.mkdir(parents=True, exist_ok=True)


def is_colab() -> bool:
    """True if currently running on Google Colab with Drive mounted."""

    return Path("/content/drive").exists()


__all__ = [
    "ALPHA",
    "AUDIT_LOGS",
    "BONFERRONI_K",
    "BOOTSTRAP_N",
    "BOOTSTRAP_SEED",
    "BUFFER_DISTANCES_M",
    "BUFFER_DISTANCES_SENSITIVITY_M",
    "CATCHMENT_CAP_M",
    "CRS_BNG",
    "CRS_WGS84",
    "GIAS_LA_NAME_TO_ONS",
    "DATA_INTERIM",
    "DATA_MANIFEST",
    "DATA_PROCESSED",
    "DATA_RAW",
    "DATA_ROOT",
    "GOOGLE_PLACES_GRID_CELL_KM",
    "GOOGLE_PLACES_QUERIES",
    "KNN_K_SENSITIVITY",
    "LA_CODES_NE",
    "LA_NAMES_NE",
    "OUTPUTS_FIGURES",
    "OUTPUTS_ROOT",
    "OUTPUTS_TABLES",
    "OVERPASS_TAG_FILTERS",
    "RANDOM_SEED",
    "REGION_BBOX_WGS84",
    "REGION_ITL1",
    "REGION_NAME",
    "REPO_ROOT",
    "ROUTE_BUFFER_PRIMARY_M",
    "ROUTE_BUFFER_SENSITIVITY_M",
    "ensure_dirs",
    "is_colab",
]
