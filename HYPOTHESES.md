# Hypotheses (commit-locked)

This file is the lightweight priority anchor that replaces an OSF pre-registration. It is committed to a public GitHub repo and tagged `hypotheses-locked-YYYYMMDD` **before any salon data collection begins**. The Stage 3 notebook (`03_salons_google_places.ipynb`) asserts the tag exists via the GitHub API before running.

The trade-off versus full OSF pre-registration is acknowledged in DEC-013 and will be acknowledged in the manuscript limitations section.

**Authored:** 2026-04-28
**Author:** Matthew Bowker

---

## Setting

Cross-sectional analysis of state-funded primary, secondary, and special schools in the 12 upper-tier local authorities of North East England (ITL1 region TLC). Exposure measure: counts of commercial tanning-salon premises along modelled walking routes between population-weighted LSOA21 child-population centroids and assigned schools, using a 50 m route buffer (primary) on a pandana walking-network shortest path. Comparator: counts within 400 m / 800 m / 1600 m Euclidean and network buffers around each school.

## Primary outcome

`RII_route / RII_buffer` — the ratio of the Relative Index of Inequality (RII) for walking-route exposure to the RII for school-centred buffer exposure, both across IMD2025 quintiles, child-population-weighted, with bootstrap 95 % confidence intervals (n = 1000).

## Hypotheses

**H1.** Tanning-salon density along walking routes to schools is positively associated with area deprivation; the most-deprived IMD2025 quintile shows the highest exposure.

- **Test.** Negative-binomial regression of route-based salon count on IMD2025 quintile, with `offset(log(child_n))`. Cluster-robust SE by local authority. Vuong test against ZINB.
- **Decision rule.** H1 supported if the exposure incidence-rate ratio for IMD quintile 1 (most deprived) vs quintile 5 is >1 with a 95 % CI excluding 1.

**H2 (primary novelty).** The deprivation gradient is *steeper* when measured along walking routes than when measured by static school-centred buffers.

- **Test.** Stacked panel (school × measure type) negative-binomial regression with school random intercept and a `measure × IMD2025 quintile` interaction term. Bootstrap 95 % CI on the interaction coefficient (n = 1000). Equivalent specification: separate NB models for buffer and route, slopes compared via paired bootstrap.
- **Decision rule.** H2 supported if the headline ratio `RII_route / RII_buffer` is >1 with a 95 % CI excluding 1.

**H3.** Exposure is higher for secondary-school pupils than primary-school pupils, both because secondary catchments are larger / routes longer and because secondary-aged pupils are the policy-relevant under-18 group.

- **Test.** Negative-binomial regression of route exposure with a `phase × IMD2025 quintile` interaction.
- **Decision rule.** H3 supported if the secondary-vs-primary main effect on route exposure is positive with a 95 % CI excluding 0, controlling for IMD.

## Multiple-testing correction

Bonferroni adjustment across the three primary hypotheses (α = 0.05 / 3 = 0.0167).

## Sensitivity analyses pre-specified

The sensitivity dimensions listed below are the ones whose results will be reported regardless of how they come out:

1. Salon source: Google Places only / OSM only / union / intersection.
2. Buffer distance: 250 m / 400 m / 800 m / 1600 m.
3. Catchment proxy: hard nearest-school / k=3 IDW / straight-line radius.
4. Deprivation index: IMD2025 vs IDACI.
5. Distance caps: ±25 % around the primary 2 km / 5 km / 10 km cap.
6. Route-buffer width: 50 m vs 100 m.
7. Restrict to schools with ≥1 salon in any buffer.
8. Routing engine: pandana vs osmnx (and OS Open Roads + networkx if used) on a 10–15 % random sample.

The headline `RII_route / RII_buffer` will be reported as a forest plot across all eight sensitivity dimensions.

## Anything not pre-specified here

Anything analytic done after the git tag is set that is *not* in this document is exploratory and will be labelled as such in the manuscript.
