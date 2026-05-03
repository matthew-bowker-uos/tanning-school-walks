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

**SUPERSEDED by DEC-016**: 10 km is unrealistic for a *walking* analysis. The cap is the maximum network distance a route is allowed to traverse before we say "this LSOA cannot reach this school on foot"; for special-school pupils above a 5 km walk it is mechanistically a non-walking journey. Cap is now 5 km for all phases.

## DEC-011 — k-NN (k=3) inverse-distance-weighted as sensitivity; no Huff/gravity
**Date:** 2026-04-28
**Reason:** k=3 IDW is the right sensitivity to bound allocation-choice impact on the headline RII ratio. Huff/gravity models import parameters that cannot be calibrated without admissions data, which we do not have, and would invite reviewer challenge for no analytic gain.

## DEC-012 — Use ONS-published total-population PWCs for LSOA21 origin geometry; weight exposure counts by Census 2021 *child* population
**Date:** 2026-04-28
**Reason:** Two separate concerns that the original draft of this entry conflated. (a) The geographic origin used to seed walking routes is the ONS-published LSOA21 population-weighted centroid — these are total-population weighted, the standard published anchor used in UK food-environment GIS, and reproducible without re-deriving them from OA-level data. (b) The exposure count for each LSOA is then weighted by the LSOA's Census 2021 child population (ages 4–18) when aggregating to school-level outcomes and as the regression offset, so the analysis is anchored to the population at risk. A child-population-weighted LSOA centroid (recomputed from OA21 child-pop and OA21 PWCs) is reserved as a sensitivity check only — DEC-011's k-NN sensitivity captures most of the same uncertainty.

**SUPERSEDES** the earlier draft of this entry which proposed self-computed child-PWCs as the primary geometry; that version conflicted with the plan's stated preference for ONS-published PWCs.

## DEC-013 — OSF pre-registration **skipped** (user choice); replaced by `HYPOTHESES.md` git-tag before salon collection + medRxiv pre-print at submission
**Date:** 2026-04-28
**Reason:** User has chosen not to pre-register on OSF. Spec §8 identified OSF pre-registration as the principal mitigation for priority risk vs the active Lorigan/Manchester group. As a lighter-weight substitute, `HYPOTHESES.md` is committed and `git tag hypotheses-locked-YYYYMMDD` pushed to a public GitHub repo before salon enumeration; the Stage 3 notebook asserts the tag exists. The methods section will acknowledge this is weaker than full OSF pre-registration.

## DEC-014 — Do not republish raw Google Places records
**Date:** 2026-04-28
**Reason:** Google Places ToS prohibits redistribution of raw place records. The OSM-only replication path is fully sufficient for independent verification by reviewers; aggregated salon counts and grid summaries are publishable.

## DEC-017 — Most-rigorous-on-public-data analysis: origin-IMD + age-cohort-weighted + empirical walking caps + phase-stratified
**Date:** 2026-04-28
**Reason:** The earliest LSOA-primary numbers attributed route exposure to the SCHOOL'S LSOA-IMD and weighted every route by the OA's total 5-19 population, conflating cohorts. The methodologically rigorous version on data we can access without DfE Schools-Census micro-data:

1. **Origin-IMD attribution.** A route's salon count is a property of where the pupil lives, not where the school sits. Each route is attributed by its origin OA's IMD (inherited from origin's LSOA).
2. **Age-cohort weighting.** Each (origin × school) route is weighted only by the OA's children whose age matches the school's phase:
   - Primary (5-10): pop_5_9 + 0.2 × pop_10_14
   - Secondary (11-16): 0.8 × pop_10_14 + 0.4 × pop_15_19
   These linear-interpolation cohort estimates assume uniform distribution within 5-year bands (the only public Census granularity for OAs).
3. **Empirical walking caps** from DfT NTS 2024: 1.6 km primary, 3.2 km secondary. Modal-walking thresholds. `config.CATCHMENT_CAP_M` updated 2026-05-03 to match (was 2000/5000 per DEC-016; superseded here).
4. **Phase-stratified RIIs** so primary and secondary children's exposures are not pooled. The pooled regression controls for phase as a factor.
5. **Routes with zero relevant cohort dropped** (no primary-aged kids at OA X means no primary route from there).

**Limitations made explicit in manuscript:**
- Nearest-school assumption: ~70% of primary, ~50% secondary actually attend their nearest school. The 30-50% who travel further are disproportionately wealthier (faith / grammar / academy choice). Bias is toward under-counting longer commutes by less-deprived pupils.
- Mode-share not modelled: NTS shows walking is similar across IMD for short trips (<1 mile), so this does not bias the relative inequality much; but absolute level is conditional on walking.
- Pupil-school real pairings not available without DfE Schools Census DPA registration. The nearest-school within walking distance is the published-GIS convention (Burgoine et al., Nahar et al.).

## DEC-016 — Cap walking catchment at 5 km for all school phases (supersedes DEC-010 special cap)
**Date:** 2026-04-28
**Reason:** A 10 km network distance for special schools (DEC-010) is incompatible with a *walking-route* exposure framing — pupils above ~5 km walk are not walking, they are bussed, taxied, or driven. Including them inflates the per-pupil route length and dilutes the urban deprivation signal we are trying to measure. The cap is now uniformly 5 km. The ±25 % sensitivity (Sensitivity #5 in HYPOTHESES.md) becomes a 4 km / 6 km test.

## DEC-015 — Add OA21 origin-geometry sensitivity (HYPOTHESES.md amendment A-01)
**Date:** 2026-04-28
**Reason:** Real-data check on Stage 2 outputs showed urban LSOAs in NE have a median equivalent radius of 360 m (smaller than the 400 m exposure buffer), and rural LSOAs are larger but contribute little exposure. LSOA-level origin geometry is therefore appropriate as the primary unit. To pre-empt a reviewer concern about within-LSOA averaging masking variation, an OA21-level origin sensitivity is added: rerun route exposure with the ~10,000 OA21 PWCs in NE (≈ 6× finer than LSOA21), keeping IMD/IDACI joined at LSOA21 level via the ONS OA21→LSOA21 lookup. Pre-data amendment (no salon collection done yet); recorded in HYPOTHESES.md amendment log A-01 for transparency.

## DEC-018 — Cross-regional extension: replicate density + RII analysis in 5 additional regions
**Date:** 2026-05-03
**Reason:** Author began with NE England as a tractable single-region pilot (HYPOTHESES.md and the original spec target NE only). After completing the rigorous NE analysis (DEC-017) we found a strong route-RII gradient. To test whether the Lorigan/published England-wide pattern of salon clustering in deprivation generalises, and to test whether the H2 finding (route-RII steeper than buffer-RII) is region-specific, we extend salon enumeration and route-exposure analysis to five further regions: London (capital), SW England (rural-dominant), Greater Manchester (post-industrial NW), West Midlands (Birmingham-dominant), Yorkshire (mixed urban). Total Google Places spend ~$10 (within free trial credits). Same methodology as DEC-017 throughout: LSOA-origin centroids, age-cohort-weighted offsets, empirical 1.6/3.2 km walking caps, origin-IMD attribution, NB GLM with cluster-bootstrap by LAD. Per-region RII point estimates with 95% bootstrap CIs reported as a forest plot. The extension is a *replication test* rather than a separate hypothesis test — same H2/H3 framing, evaluated independently in each region.

**How to apply:** Manuscript should report NE as the primary regression (the pre-registered geography in HYPOTHESES.md), with the 5-region extension as a robustness/generalisability check. The fact that the gradient direction *varies between regions* (NE strongly positive vs. London flat vs. WM inverted) is itself a substantive finding to report — it speaks to spatial heterogeneity in commercial-tanning geography, not a single population-wide gradient.

## DEC-019 — Cross-regional bbox correction (data-quality fix)
**Date:** 2026-05-03
**Reason:** Initial cross-regional fetches (regions_run_v2.py) used hand-specified WGS84 bboxes that did not fully enclose the actual LAD polygons of three of the four target regions: West Midlands eastern edge cut off at -1.55° (missing ~80% of Coventry, including 157 of 196 Coventry LSOAs); Yorkshire western edge at -2.10° missed western Calderdale; SW England eastern/northern edges missed parts of Wiltshire/Gloucestershire. The bbox is used both for the Google Places gridded fetch (so cells weren't generated for missing area) AND for the LSOA21 boundary download from ONS (so the in-region polygon itself was incomplete). The original Q1/Q5 density ratios were computed against the *partial* in-region populations — internally consistent but representing only the area inside the bbox.

Resolution: extended each truncated bbox to fully enclose its LAD polygons (with 5 km margin), re-fetched LSOA21 boundaries by the new bbox, re-fetched the Google Places cells whose centres lay outside the old bbox (delta-fetch, 79 cells, $0.40), merged old + delta JSONL, re-deduplicated, re-spatial-joined. Density ratios essentially unchanged after correction (WM 0.71→0.71; Yorks 1.31→1.31; SW 2.00→2.01) — the missing fringe cells were sparsely populated. But LSOA coverage rose ~10% in WM (1554→1718) so the route pipeline must use the corrected layer.

**How to apply:** Going forward, region bboxes for any new geography MUST be derived from the actual LAD polygon `total_bounds` (with margin), not hand-specified. The pipeline now logs a coverage check at start (LAD polygon bbox vs. config bbox) and warns on truncation. Existing NE bbox is checked and confirmed adequate.

## DEC-020 — Verification CSV for cross-regional salon enumeration (parallel to NE manual_verification.csv)
**Date:** 2026-05-03
**Reason:** NE salon enumeration was manually classified in `audit_logs/manual_verification.csv` (730 rows, 100% of Google Places hits in NE) to flag false-positives like beauty salons, spray-tan-only businesses, etc. For methodological parity, the 5 cross-regional fetches (~3,560 salons total) need a comparable verification step. Full classification (3,560 rows) is impractical for one researcher; instead, a **stratified random sample** (~100/region, stratified by LAD × IMD-quintile, total n=616) is generated at `audit_logs/google_classification_sample_other_regions_<DATE>.csv`. This gives per-region, per-quintile false-positive rate estimates, which are then applied as adjustment factors to the regional salon counts in a sensitivity analysis. The schema mirrors `google_classification_sample_<DATE>.csv` (the simpler `is_true_salon` Y/N format) rather than the richer NE schema.

**How to apply:** Once classified, the false-positive rate per region × IMD-quintile is computed and applied either as an exclusion (drop rejected place_ids from the verified set) or as a per-cell precision factor on counts. The sensitivity is reported in the manuscript supplement; primary results use the unadjusted counts to keep them comparable to the published Lorigan finding (which also used unverified Google data).
