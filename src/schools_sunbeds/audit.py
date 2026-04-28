"""Audit, hashing, and provenance utilities.

Two responsibilities:

1. **Raw-data manifest.** ``register_raw_file`` appends a row to
   ``data/manifest.csv`` recording the file path, SHA256, source URL,
   retrieval timestamp, and licence. ``verify_manifest`` re-hashes every
   listed file and raises if anything has drifted.
2. **Processed-data provenance sidecars.** ``write_provenance_sidecar``
   writes ``<output>.meta.yaml`` next to a processed artefact, recording the
   inputs that produced it, the code git SHA, and the library versions.

These helpers are called from every analysis notebook, both for defensibility
and to make pipeline drift loud and immediate.
"""

from __future__ import annotations

import csv
import hashlib
import os
import platform
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from schools_sunbeds.config import DATA_MANIFEST, DATA_RAW, REPO_ROOT

MANIFEST_FIELDS: tuple[str, ...] = (
    "path",
    "sha256",
    "size_bytes",
    "source_url",
    "licence",
    "retrieved_utc",
    "notes",
)

CHUNK_BYTES: int = 1 << 20  # 1 MiB


# ---------------------------------------------------------------------------
# Hashing


def sha256_file(path: Path) -> str:
    """SHA256 hex digest of a file, streamed in 1 MiB chunks."""

    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK_BYTES):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Manifest IO


def _ensure_manifest_exists() -> None:
    if not DATA_MANIFEST.exists():
        DATA_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        with DATA_MANIFEST.open("w", newline="") as fh:
            csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS).writeheader()


def _read_manifest() -> list[dict[str, str]]:
    _ensure_manifest_exists()
    with DATA_MANIFEST.open("r", newline="") as fh:
        return list(csv.DictReader(fh))


def _write_manifest(rows: Iterable[Mapping[str, Any]]) -> None:
    with DATA_MANIFEST.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in MANIFEST_FIELDS})


def register_raw_file(
    path: Path,
    *,
    source_url: str,
    licence: str,
    notes: str = "",
    set_readonly: bool = True,
) -> dict[str, str]:
    """Hash a raw file, append it to the manifest, and chmod 444.

    The manifest path is stored as a POSIX path relative to ``DATA_RAW`` when
    the file lives under the raw tree, else relative to ``REPO_ROOT``.
    """

    path = Path(path).resolve()
    if not path.exists():
        msg = f"raw file does not exist: {path}"
        raise FileNotFoundError(msg)

    if path.is_relative_to(DATA_RAW):
        rel = path.relative_to(DATA_RAW.resolve())
        rel_str = f"raw/{rel.as_posix()}"
    else:
        rel_str = path.relative_to(REPO_ROOT).as_posix()

    row: dict[str, str] = {
        "path": rel_str,
        "sha256": sha256_file(path),
        "size_bytes": str(path.stat().st_size),
        "source_url": source_url,
        "licence": licence,
        "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "notes": notes,
    }

    rows = [r for r in _read_manifest() if r.get("path") != rel_str]
    rows.append(row)
    _write_manifest(rows)

    if set_readonly:
        os.chmod(path, 0o444)

    return row


def verify_manifest(strict: bool = True) -> list[str]:
    """Re-hash every listed file and return the list of mismatches.

    If ``strict`` (default) any mismatch raises :class:`RuntimeError`. The
    typical pattern at the top of an analysis notebook is::

        verify_manifest()  # strict

    so that pipeline drift fails loudly before any analytic work is done.
    """

    failures: list[str] = []
    for row in _read_manifest():
        rel = row["path"]
        path = (
            DATA_RAW / rel.removeprefix("raw/") if rel.startswith("raw/") else REPO_ROOT / rel
        )
        if not path.exists():
            failures.append(f"missing: {rel}")
            continue
        actual = sha256_file(path)
        if actual != row["sha256"]:
            failures.append(f"hash drift: {rel} (expected {row['sha256'][:12]}, got {actual[:12]})")

    if failures and strict:
        msg = "manifest verification failed:\n  - " + "\n  - ".join(failures)
        raise RuntimeError(msg)
    return failures


# ---------------------------------------------------------------------------
# Provenance sidecars


@dataclass(frozen=True)
class Provenance:
    """In-memory representation of a processed-artefact sidecar.

    Inputs are referenced by their manifest path strings (so a sidecar links
    back to the manifest), or by file paths that get hashed at write time.
    """

    output_path: Path
    inputs: tuple[Path, ...] = ()
    notes: str = ""
    random_seed: int | None = None


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "no-git"


def _library_versions() -> dict[str, str]:
    """Versions of the libraries the analysis is most sensitive to."""

    packages = (
        "geopandas",
        "shapely",
        "pyproj",
        "pandas",
        "numpy",
        "pandana",
        "osmnx",
        "networkx",
        "statsmodels",
        "scipy",
        "rapidfuzz",
    )
    versions: dict[str, str] = {}
    for pkg in packages:
        try:
            from importlib.metadata import version

            versions[pkg] = version(pkg)
        except Exception:  # noqa: BLE001 — best-effort metadata
            versions[pkg] = "unknown"
    return versions


def write_provenance_sidecar(prov: Provenance) -> Path:
    """Write ``<output>.meta.yaml`` next to ``prov.output_path``."""

    sidecar = prov.output_path.with_suffix(prov.output_path.suffix + ".meta.yaml")
    payload: dict[str, Any] = {
        "output": prov.output_path.name,
        "output_sha256": sha256_file(prov.output_path) if prov.output_path.exists() else None,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.machine()}",
        "library_versions": _library_versions(),
        "random_seed": prov.random_seed,
        "notes": prov.notes,
        "inputs": [
            {
                "path": str(p),
                "sha256": sha256_file(p) if Path(p).exists() else None,
            }
            for p in prov.inputs
        ],
    }
    sidecar.write_text(yaml.safe_dump(payload, sort_keys=False))
    return sidecar


__all__ = [
    "MANIFEST_FIELDS",
    "Provenance",
    "register_raw_file",
    "sha256_file",
    "verify_manifest",
    "write_provenance_sidecar",
]
