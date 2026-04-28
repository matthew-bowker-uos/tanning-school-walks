# Tanning salon exposure on walking routes to schools in North East England

A research specification.

---

## 1. Background and rationale

Sunbeds are a Class 1 carcinogen (IARC, 2009). Commercial sunbed use by under-18s has been banned in England since the Sunbeds (Regulation) Act 2010, but enforcement is devolved to local authority environmental health teams and is patchy. A 2025 Melanoma Focus survey found 34% of UK 16–17 year-olds still use sunbeds. A 2024 survey of 18–25 year-olds found 43% use them, half of those at least weekly.

A BMJ Analysis paper by Lorigan et al. (October 2025), using websites and social media data from January 2024, identified 4,231 sunbed outlets in England and 232 in Wales. Density per 100,000 population was highest in the North East and North West, in the most deprived areas, and correlated with melanoma rates among 25–49 year-olds. That work establishes outlet density at area level. It does not address proximity to schools or exposure on routes children actually take.

The closest school-focused work is Nahar et al. (2018), who used ArcGIS network analysis in Worcester County, Massachusetts, and found 39% of schools within one mile of a tanning salon, with urban schools (53%) far above rural/suburban (18%). They used 1-mile network buffers around schools, did not stratify by deprivation, and were not in the UK.

Methodologically adjacent UK GIS work on **fast food** outlets and schools (e.g. Portsmouth's 400m takeaway exclusion zone, the Cambridge work on modelled vs GPS routes by Harrison/Burgoine) demonstrates that the technique transfers cleanly. A 2025 medRxiv preprint surveying UK 18–24 year-old sunbed users explicitly tested public support for "location bans (e.g. gyms, beauty salons, hotels, **near schools**)" — establishing that the policy idea exists, but no UK GIS study has put a number on the exposure it would target.

This study fills that gap for the North East.

---

## 2. Research questions

**Primary.** Among schoolchildren in North East England, what is the prevalence and intensity of exposure to commercial tanning salon premises along modelled walking routes to and from school, and how does this exposure vary by area-level deprivation?

**Secondary.**

- How does walking-route exposure compare to the conventional school-centred buffer measure?
- Does the deprivation gradient in exposure differ between primary and secondary schools?
- What proportion of NE schools have at least one tanning salon within plausible walking distance (400m / 800m / 1600m)?
- How sensitive are findings to the choice of salon data source?

---

## 3. Hypotheses

**H1.** Tanning salon density on walking routes to schools is positively associated with area deprivation, with the most deprived IMD2025 quintile showing the highest exposure.

**H2 (primary novelty).** The deprivation gradient is *steeper* when measured along walking routes than when measured by static school-centred buffers. Mechanism: commercial high streets sit on movement corridors that walking routes traverse but that point-buffers around schools may not capture; in deprived areas, schools sit closer to those corridors and home–school distances are shorter, so route-based exposure compounds the inequality already visible in outlet density. If supported, H2 implies that conventional buffer-based exposure measures *understate* the inequality of exposure that children actually experience.

**H3.** Exposure is higher for secondary-school pupils than primary-school pupils — both because secondary catchments are larger and routes longer, and because secondary-aged pupils are the policy-relevant under-18 group whose sunbed use the 2011 ban has not eliminated.

---

## 4. Setting and scope constraints

**Geography.** North East England (ITL1 region TLC), comprising the 12 upper-tier local authorities: County Durham, Darlington, Gateshead, Hartlepool, Middlesbrough, Newcastle upon Tyne, North Tyneside, Northumberland, Redcar & Cleveland, South Tyneside, Stockton-on-Tees, and Sunderland.

**Why North East specifically.**

- Lorigan et al. (2025) place the NE alongside the NW as the highest-density region for sunbed outlets nationally.
- Melanoma incidence in 25–49 year-olds is highest in the north of England.
- Under-18 prevalence is plausibly higher than the English average given regional deprivation and use patterns.
- 12 LAs is a tractable region for a single-researcher project within one research year.
- No published or in-preparation GIS work specifically on NE schools and sunbeds was identified through searches of PubMed, BMJ archives, medRxiv, WhatDoTheyKnow, and council websites (April 2026).

**Population of interest.** Pupils registered at state-funded primary, secondary, and special schools in the region.

**Exclusions.** FE / sixth-form colleges (post-16, less relevant to the under-18 ban rationale; kept as a sensitivity layer). Home-educated children. Independent schools that are not in GIAS (small numbers; kept in sensitivity analysis).

**Time frame.** Cross-sectional. All layers fixed to the most recent available snapshot, with salon data collected within a single 8–12 week window to minimise turnover bias.

---

## 5. Data sources

### 5.1 Schools

| Layer | Source | Notes |
|---|---|---|
| School locations & metadata | DfE *Get Information about Schools* (GIAS) standard CSV | Free, daily-updated. URN as unique key. Filter to NE region or to the 12 LA codes. |
| Pupil headcounts & demographics | DfE *Schools, Pupils and their Characteristics* annual statistics | Used as denominators and for school-level pupil weighting. |

### 5.2 Tanning salons (the methodological hard part)

No single authoritative source exists. The plan is to enumerate salons from two complementary internet-discoverable sources and report agreement between them as a methods contribution in itself.

| Source | Role | Notes |
|---|---|---|
| Google Places API (Text Search + relevant place types) | Primary scalable enumeration | Grid the NE into bounding boxes; query for "tanning salon", "sunbed", "solarium". Cost manageable at this geographic scale. |
| OpenStreetMap (Overpass API) | Independent open enumeration | Tags `leisure=tanning_salon`, `shop=solarium`, `shop=beauty + beauty=tanning`. Coverage is patchy but provides a fully open, reproducible counterpart that does not depend on a commercial API. |

**Sources excluded and why.**

- *FOI to local authorities.* Considered as a statutory ground-truth layer. Excluded to keep the data pipeline lean, reproducible without bureaucratic dependencies, and entirely scriptable. The honest trade-off is no statutory cross-check; this is acknowledged in §8 limitations and addressed analytically by reporting Google–OSM agreement transparently.
- *Facebook Graph API.* The `/pages/search?q=` endpoint requires the Page Public Content Access permission, which Meta has been very restrictive about granting to researchers since 2018. The older `/search?type=place` endpoint was deprecated in November 2020. Approval risk and timeline make this not viable.
- *Companies House SIC 96040 ("Physical well-being activities").* Too broad — also covers saunas, slimming salons, massage. Not isolable to tanning.
- *VOA business rates and council rates-relief lists.* Excluded to keep the pipeline lean.
- *Commercial directories (Yell, Yelp).* ToS prohibits scraping for research republication.

### 5.3 Deprivation and geography

| Layer | Source | Notes |
|---|---|---|
| Index of Multiple Deprivation 2025 (IMD2025) | MHCLG, published 30 October 2025 | Based on 2021 LSOAs and 55 indicators. Use deciles and quintiles. |
| Income Deprivation Affecting Children Index (IDACI) | MHCLG, part of IoD25 | Secondary deprivation measure better aligned to a child-focused outcome. |
| LSOA21 boundaries | ONS Open Geography Portal | Aggregation unit. |
| Local authority boundaries | OS Boundary-Line | For LA-level reporting. |
| Road / pedestrian network | OS Open Roads or OSM | Used for shortest-network and walking-route modelling. |
| Census 2021 child population by LSOA | ONS | Denominator for population-weighted exposure. |
| Urban–rural classification | ONS RUC2011 (or RUC2021 if released by analysis time) | Stratifier. |

---

## 6. Statistical plan (high-level)

The technical specification will follow separately. The intended analytic structure is:

**6.1 Descriptive layer.** Per LA and per LSOA: school counts (by phase), salon counts, salon density per 100,000 population and per 100,000 children, IMD2025 distribution of both schools and salons. Replicate the Lorigan et al. (2025) outlet-density-by-deprivation finding at the regional scale specifically for the NE.

**6.2 Buffer-based exposure (the conventional measure).** For each school, count tanning salons within Euclidean and network buffers at 400m, 800m, and 1600m. Stratify counts by IMD2025 quintile of the school's LSOA. The 400m and 800m buffers are the standard distances used in UK school-environment GIS (e.g. takeaway exclusion-zone work). 1600m approximates one mile and aligns with the Nahar et al. US comparator.

**6.3 Walking-route exposure (the primary contribution).** For each school, model walking routes between pupil residences and the school. Routes are buffered narrowly (e.g. 50m or 100m either side) and salons within the route-buffer are counted. Pupil residences are approximated using population-weighted LSOA centroids within a plausible catchment, assigned via nearest-school allocation with a phase-appropriate distance cap. Exposure is summarised per school as the mean and maximum salon count along pupil-routes.

**6.4 Comparative analysis (H2 test).** For each school, compute both buffer-based and route-based exposure. Regress exposure on IMD quintile separately for each measure (negative binomial or zero-inflated negative binomial given count outcome with likely zero-inflation in less deprived areas). Compare slopes — formally with a measure × deprivation interaction term in a stacked model. H2 is supported if the route-based slope is steeper than the buffer-based slope.

**6.5 Stratification.** Repeat the headline analysis split by school phase (primary vs secondary), urban–rural classification, and local authority. Report a forest plot of LA-specific slopes to surface within-region heterogeneity.

**6.6 Inequality summary.** Compute the Slope Index of Inequality (SII) and Relative Index of Inequality (RII) for both buffer-based and route-based exposure across IMD quintiles. The headline single number for the paper is the *ratio* of the route-based RII to the buffer-based RII — i.e. the factor by which walking-route measurement amplifies the deprivation inequality signal that buffer-based measurement already shows.

**6.7 Sensitivity analyses.**

- Salon list provenance: rerun with Google Places only, OSM only, the union, and the intersection. Report agreement statistics (count, address match, geocoded distance) between the two sources as a methods sub-finding.
- Buffer distance: rerun at 250m, 400m, 800m, 1600m.
- Catchment proxy: rerun with population-weighted LSOA centroids vs nearest-school assignment vs straight-line catchment radius, to gauge sensitivity to catchment definition.
- Deprivation index: rerun stratifying by IDACI rather than overall IMD2025.
- Restrict to schools with at least one identified salon in any buffer (sensitivity to zero-heavy distribution).

---

## 7. Outputs

- **Manuscript.** Target journals: *Journal of Epidemiology and Community Health*, *Health & Place*, *Social Science & Medicine – Population Health*, or *International Journal of Health Geographics*.
- **Policy brief.** Targeted at the North East Combined Authority and the 12 LA public health teams, framed around the existing under-18 ban and the option of licensing or planning-buffer interventions.
- **Open data and code.** Reproducible analysis pipeline in a public repository. Derived salon counts and grid summaries published openly; raw Google Places place records not republished (subject to ToS), but the OSM-based replication path lets others verify independently.
- **Pre-registered protocol** on OSF before data collection begins.

---

## 8. Considerations

**Ethics.** Aggregate area-level analysis with no individual-level pupil data does not require NHS REC review. All inputs are either openly published (GIAS, IMD2025, ONS boundaries, OSM) or accessed under a commercial API agreement (Google Places). University-level ethics review is not anticipated to be required but should be confirmed with the host institution.

**Competitive landscape.** The Lorigan / Manchester / Christie group is active in this space; Kreft, Green and Lorigan have a 2026 follow-up in *Clinical and Experimental Dermatology*. This study is intended to compete, not collaborate. Mitigations to protect priority: (a) pre-register the protocol publicly on OSF before data collection begins, with a clear date stamp on the H2 hypothesis (route-based vs buffer-based deprivation gradient), which is the novel contribution; (b) move quickly through the data-collection and pilot phases; (c) consider a pre-print on medRxiv at submission. The H2 framing — that conventional buffer-based exposure measures *understate* the inequality children actually experience — is methodologically distinct from outlet-density work and is the defensible piece of intellectual territory.

**Equity framing.** Exposure is a feature of the built environment that children do not choose. Reporting must avoid implicit blame on pupils or families and centre the structural-inequality framing.

**Industry response.** Any media coverage will draw a response from the Sunbed Association. Pre-prepare neutral framing: the under-18 ban already exists, and this work quantifies the avoidable exposure pattern that the ban does not currently address.

**Limitations to acknowledge upfront.** Cross-sectional design cannot establish behavioural outcomes (sunbed use); this is an exposure study, not a causal one. Salon enumeration relies on two internet-discoverable sources (Google Places and OSM); there is no statutory ground-truth layer in the design, so any salon operating without a web presence will be missed by both sources. The expected direction of bias is toward an undercount of the smallest, least-marketed premises, which may bias the estimated deprivation gradient downward (i.e. the true gradient may be steeper than reported). Pupil-route modelling without GPS data is an approximation; the Burgoine/Harrison comparison of modelled vs measured routes shows non-trivial differences in environmental exposure estimates.
