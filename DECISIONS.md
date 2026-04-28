# Methodological decision log

Append-only. Each entry has an ID, a date, the decision, and the reason. Notebooks reference IDs in cells (e.g. "see DEC-009").

When a decision is later revised, add a *new* entry that supersedes the old; do not edit the original. Mark the superseded entry with `**SUPERSEDED by DEC-NNN**` at the top of its body.

---

## DEC-001 — pip + a single `requirements.txt` for both local dev and Colab; `requirements-dev.txt` layered on top for tooling
**Date:** 2026-04-28
**Reason:** Colab is the primary execution target, and Colab is pip-native. A single shared `requirements.txt` avoids drift between environments without needing a separate lock format. Tooling (pytest, ruff, black, pre-commit) lives in `requirements-dev.txt` so Colab does not pull it. The same dependency list is mirrored into `pyproject.toml` so that `pip install -e .` continues to work standalone.

**SUPERSEDES** an earlier draft of this entry that proposed uv + lockfile; the user preferred a pip-only flow.

## DEC-002 — Hash-manifest for raw data; no DVC, no git-LFS
**Date:** 2026-04-28
**Reason:** Single researcher, total raw inputs <5 GB. A `data/manifest.csv` with SHA256 + source URL + retrieval-UTC + licence per file, combined with `chmod 444` on the raw directory, gives a sufficient and auditable immutability story without the operational burden of DVC or the repo pollution of git-LFS.

## DEC-003 — Native `.ipynb` notebooks (not Quarto)
**Date:** 2026-04-28
**Reason:** Colab compatibility is mandatory; Quarto's principal advantage (manuscript render) is not in scope for this repo since manuscript prose lives elsewhere.

## DEC-004 — Data on Google Drive, mounted at fixed path; `config.py` switches Colab/local
**Date:** 2026-04-28
**Reason:** Colab sessions are ephemeral; Drive provides persistent storage without re-uploading. `config.DATA_ROOT` resolves to `/content/drive/MyDrive/schools-sunbeds-data` if `/content/drive` exists, else `<repo>/data`.

## DEC-005 — EPSG:27700 (British National Grid) throughout
**Date:** 2026-04-28
**Reason:** UK-native, metres for buffer/distance arithmetic, matches OS Open Roads and Boundary-Line natively. EPSG:4326 is used only at ingest/export boundaries.

## DEC-006 — pandana as primary routing engine
**Date:** 2026-04-28
**Reason:** ~10k–50k OD pairs over a regional walking graph run in minutes after contraction-hierarchy precompute. Pandana also natively supports the network-buffer POI aggregation needed for the buffer-based exposure measure, so one library serves both buffer and route work.

## DEC-007 — osmnx Dijkstra (and optionally OS Open Roads) on a 10–15 % sample as Colab-runnable cross-check
**Date:** 2026-04-28
**Reason:** Pandana's contraction hierarchy is correct in the limit but the implementation should be cross-checked. osmnx + networkx Dijkstra runs comfortably in Colab on a sub-sample and gives algorithm independence on the same OSM extract. OS Open Roads as a second network layer (where licence permits) gives data-source independence as well.

## DEC-008 — Pin OSM extract by Geofabrik snapshot date + SHA256 of the `.osm.pbf`
**Date:** 2026-04-28
**Reason:** Routing graph reproducibility. The Geofabrik daily extracts change; without pinning the input, the graph is not reproducible across runs.

## DEC-009 — Hard nearest-school allocation as primary catchment proxy
**Date:** 2026-04-28
**Reason:** The route-based exposure outcome is a count along route geometry, not an admissions prediction. Nearby LSOAs route over largely overlapping street segments, so a misallocated centroid mostly traverses the same corridors; the count is therefore robust to allocation noise. Defensible at area level and matches DfE/ONS conventions.

## DEC-010 — Distance caps: 2 km primary, 5 km secondary, 10 km special
**Date:** 2026-04-28
**Reason:** Anchored to DfE statutory walking-distance limits (2 mi for under-8s, 3 mi for over-8s) and median actual home–school distances in England (~1.1 km primary, ~3.4 km secondary). Special schools have much wider catchments by design. The ±25 % sensitivity tests robustness to these caps.

## DEC-011 — k-NN (k=3) inverse-distance-weighted as sensitivity; no Huff/gravity
**Date:** 2026-04-28
**Reason:** k=3 IDW is the right sensitivity to bound allocation-choice impact on the headline RII ratio. Huff/gravity models import parameters that cannot be calibrated without admissions data, which we do not have, and would invite reviewer challenge for no analytic gain.

## DEC-012 — Weight LSOA centroids by Census 2021 *child* population, not total
**Date:** 2026-04-28
**Reason:** The proxy should align mechanically with the population whose exposure is being estimated. Total-population weighting would over-weight LSOAs with few children but many adults (e.g. retirement-age areas).

## DEC-013 — OSF pre-registration **skipped** (user choice); replaced by `HYPOTHESES.md` git-tag before salon collection + medRxiv pre-print at submission
**Date:** 2026-04-28
**Reason:** User has chosen not to pre-register on OSF. Spec §8 identified OSF pre-registration as the principal mitigation for priority risk vs the active Lorigan/Manchester group. As a lighter-weight substitute, `HYPOTHESES.md` is committed and `git tag hypotheses-locked-YYYYMMDD` pushed to a public GitHub repo before salon enumeration; the Stage 3 notebook asserts the tag exists. The methods section will acknowledge this is weaker than full OSF pre-registration.

## DEC-014 — Do not republish raw Google Places records
**Date:** 2026-04-28
**Reason:** Google Places ToS prohibits redistribution of raw place records. The OSM-only replication path is fully sufficient for independent verification by reviewers; aggregated salon counts and grid summaries are publishable.
