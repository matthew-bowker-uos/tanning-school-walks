# Schools and sunbeds — North East England

Cross-sectional GIS study of commercial tanning-salon exposure along modelled walking routes between pupil residences and schools across the 12 upper-tier local authorities of North East England, stratified by Index of Multiple Deprivation 2025.

The novel contribution is **H2**: walking-route exposure shows a steeper deprivation gradient than school-centred buffer exposure. The headline outcome is the ratio `RII_route / RII_buffer`.

Spec: [spec.md](spec.md). Plan: `~/.claude/plans/here-is-my-spec-mighty-eich.md`.

---

## Repo layout

```
data/                 # raw / interim / processed (gitignored except manifest.csv)
notebooks/            # one .ipynb per analysis stage; Colab-runnable end-to-end
src/schools_sunbeds/  # importable Python package; pure functions, unit-tested
tests/                # pytest fixtures = a toy LA
outputs/              # final tables (CSV/HTML) and figures (PNG/PDF)
audit_logs/           # timestamped run artefacts
DECISIONS.md          # ADR-lite, append-only methodological log
HYPOTHESES.md         # H1/H2/H3 statement, git-tagged before salon collection
```

## Reproducing the pipeline

### Local (development)

Requires Python 3.11 or 3.12.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
pip install -e .
pre-commit install
pytest
```

### Google Colab (high-RAM runtime — execution target)

1. Open `notebooks/00_setup_environment.ipynb` in Colab.
2. Mount Google Drive when prompted; the notebook expects to find a folder `MyDrive/schools-sunbeds-data` (created by the notebook on first run).
3. Place a populated `.env` file at `MyDrive/schools-sunbeds-data/.env` containing your `GOOGLE_PLACES_API_KEY` and the `GITHUB_OWNER` / `GITHUB_REPO` for hypothesis-lock verification. See `.env.example` for the schema.
4. Run all cells. The notebook installs `requirements.txt`, clones this repo into `/content/repo`, performs `pip install -e /content/repo`, and verifies `import schools_sunbeds` works.
5. Then run notebooks 01..14 in order. Each notebook re-imports `schools_sunbeds` and asserts the data manifest is intact before doing any analytic work.

## Manual verification of salons

After Stage 3, every Google Places + OSM record lands in `audit_logs/manual_verification.csv` with status `pending`. Open the file and edit four columns per row:

- `status` — one of `pending` (default), `confirmed`, `rejected`, `unsure`, `duplicate`, `closed`
- `reviewer` — your initials
- `review_date_utc` — ISO date
- `notes` — optional free text

Stage 6 (exposure measurement) calls `verification.apply_verification` which keeps only `confirmed` + `unsure` rows by default and writes a per-run audit JSON to `audit_logs/verification_apply_*.json`. The verification CSV is committed to git so progress is durable; running notebook 05a is idempotent (existing edits preserved, new places appended as `pending`).

See `notebooks/05a_manual_verification.ipynb` for the bootstrap / preview / summary helper.

## Reproducibility & audit

- **Hypothesis lock.** `HYPOTHESES.md` is committed and `git tag hypotheses-locked-YYYYMMDD` is pushed *before* salon enumeration begins. The Stage 3 notebook fails closed if the tag is missing.
- **Raw data immutability.** Files in `data/raw/<source>/<YYYY-MM-DD>/` are set to read-only after retrieval (`chmod 444`) and never edited.
- **Hash manifest.** Every raw file is registered in `data/manifest.csv` with SHA256, source URL, retrieval-UTC, and licence. `schools_sunbeds.audit.verify_manifest()` is called at the top of every analysis notebook and fails loudly on drift.
- **Provenance sidecars.** Every processed `.gpkg` / `.parquet` ships with a `<file>.meta.yaml` recording input hashes, code git SHA, library versions, random seed, and generation timestamp.
- **Decision log.** `DECISIONS.md` records every analytic choice as a dated ADR-lite entry; notebooks reference IDs (e.g. "see DEC-009").
- **Determinism.** All notebooks set numpy/random seeds; bootstrap CIs use a fixed seed published in the manuscript methods.
