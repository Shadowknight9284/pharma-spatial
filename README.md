# pharma-spatial

Where do pharma companies promote, where do their products sell, and how does
that line up with disease burden? Built entirely from public data:
CMS Medicare Part D / Part B prescribing, CMS Open Payments, NPPES, FDA NDC,
CDC PLACES, CDC SVI, and the Census.

Grain of the core cube: **company × product × indication × prescriber (HCP) × county × year**.

## Quick start

```bash
docker compose up -d                      # Postgres 16 + PostGIS
pip install -r requirements.txt
export PHARMA_DSN=postgresql://pharma:pharma@localhost:5432/pharma
export PHARMA_DATA_DIR=./data             # see "Resources" below

python -m pipeline.run download --year 2024   # ~15 min at 20 MB/s (~18 GB)
python -m pipeline.run load     --year 2024   # ~20 min (COPY into raw.*)
python -m pipeline.run sql      --year 2024   # ~20 min: seeds + core + mart + QA
python -m pipeline.load_geo                   # county polygons -> geo.county_shape
python -m pipeline.export_web   --year 2024   # JSON for the website in web/
psql "$PHARMA_DSN" -f sql/examples/starter_queries.sql
python -m http.server -d web 8000             # open http://localhost:8000
```

Run the long steps inside `tmux` so they survive a dropped SSH session.
`python -m pipeline.load_raw --only <source>` and `python -m pipeline.download --only <source>`
redo a single source.

### Resources (one year, measured on the 2024 files)

| | |
|---|---|
| Downloads | ~18 GB (Open Payments 9.0 GB, Part D 4.1 GB, Part B 3.3 GB, NPPES 1.1 GB zip) |
| Postgres volume | ~60 GB after `sql` (raw tables are all-TEXT) |
| RAM | Postgres peaked ~19 GB of a 31 GB VM; `shared_buffers=2GB` |
| `/dev/shm` | `docker-compose.yml` sets `shm_size: 4gb`. Docker's 64 MB default makes parallel joins fail with *could not resize shared memory segment* |

`raw.*` tables are `UNLOGGED` (fast COPY). They survive a clean shutdown but are emptied after a
crash or `SIGKILL`, hence `stop_grace_period: 5m`. Run `CHECKPOINT` before recreating the container.

### Census API key (optional)

The Census API now rejects keyless calls. Without `CENSUS_API_KEY` the pipeline reads the keyless
ACS table-based summary files from www2.census.gov instead (same output). Set the key to use the API.

Test the whole pipeline in about 10 seconds without downloading anything:

```bash
PHARMA_DATA_DIR=/tmp/pharma_test python tests/make_synthetic.py
PHARMA_DATA_DIR=/tmp/pharma_test python -m pipeline.run load --year 2024
PHARMA_DATA_DIR=/tmp/pharma_test python -m pipeline.run sql  --year 2024
```

## Layout

```
pipeline/sources.py     source registry: catalog discovery + columns kept
pipeline/download.py    resumable downloads + manifest.json (url, sha256)
pipeline/load_raw.py    streamed COPY into raw.* (schema taken from file header)
pipeline/load_geo.py    county polygons -> geo.county_shape (PostGIS)
pipeline/export_web.py  aggregated JSON for the static site (no NPIs leave the DB)
pipeline/run.py         one CLI for every step
sql/00_schemas.sql      raw/ref/core/mart/qa schemas + helpers (num, norm_name, ndc9)
sql/10_ref.sql          curated mapping tables
sql/20_core.sql         prescriber, county, drug, rx_fact, payment_fact
sql/30_mart.sql         npi_brand_year, county_brand_year, npi_product_indication,
                        county_product_indication, targeting_index, v_model_input
sql/40_qa.sql           qa.checks + worklists for the manual mapping
sql/examples/           starter analysis queries
seeds/*.csv             the hand-made mappings (start here when adding products)
tests/make_synthetic.py fake files with the real headers
web/                    static, interactive site (D3): maps, company and indication views
```

## Website

`web/` is a static site (plain HTML + D3 from a CDN, no build step) fed by
`python -m pipeline.export_web`, which writes a few MB of county- and company-level JSON to
`web/data/`. Nothing at prescriber (NPI) level is exported.

* **Map**: any curated product, coloured by fills per 1,000 aged 65+, promotion $, promotion vs
  sales, promotion vs disease burden, class share, PLACES prevalence or SVI. Click a county for
  details; the linked scatter shows promotion share against sales share for every county.
* **Companies**: parent companies, Medicare gross cost vs drug-attributed promotion.
* **Indications**: each product's fills, cost and promotion split by inferred indication.
* **About**: method, caveats and this build's `qa.checks`.

Preview locally with `python -m http.server -d web 8000`. To publish, enable GitHub Pages
(Settings → Pages → Source: GitHub Actions), then run the `pages` workflow
(`.github/workflows/pages.yml`, manual trigger) to deploy `web/`.

## Two tiers

* **Auto tier**: every Part D brand. Company comes from CMS Part D Spending by Drug,
  class from FDA EPC, and Open Payments product names are matched by brand name,
  then by brand prefix, then by NDC. Output: `mart.county_brand_year`, `mart.company_county_year`.
* **Curated tier**: products listed in `seeds/product.csv`. Brand variants roll up to
  one product, Part B J-codes are included, and fills are split across indications
  by prescriber specialty. Output: `mart.county_product_indication`, `mart.targeting_index`.

## What you have to do by hand

1. **Product list and aliases** (`seeds/product.csv`, `seeds/product_alias.csv`).
   Pick the products and competitive classes, and list every brand string and Part B
   HCPCS code. Use `qa.v_uncurated_top_brands` and `qa.v_unmatched_op_products` as worklists.
2. **Specialty → indication rules** (`seeds/specialty_indication.csv`). The split
   weights are starting guesses. Calibrate them against label shares, published
   patient-mix data, or what you know from industry. Check `qa.v_unmapped_specialties`.
   The CMS specialty strings must match exactly (e.g. `Allergy/ Immunology` has a space).
3. **Indication → disease measure** (`seeds/indication_measure.csv`). CDC PLACES only
   covers some diseases (asthma, COPD, diabetes, CKD, CHD). Atopic dermatitis, AF,
   EoE and CRSwNP fall back to the 65+ population.
4. **Company parents** (`seeds/company_parent.csv`). Open Payments lists the paying legal
   entity (BMS pays mostly as "E.R. Squibb & Sons" and Celgene; Sanofi as Genzyme), and Part D
   gives a 15-character truncated manufacturer ("BMS PRIMARYCARE"). Regex rules map both to a
   parent *as of the data year*; unmatched names are their own parent. Worklist:
   `qa.v_unmapped_companies`. Output: `mart.parent_company_year`, `mart.company_county_year`.
   Co-promoted products (Eliquis: BMS/Pfizer) keep both names in `ref.product.company`.
5. **Validate a few numbers.** Compare national totals with CMS Part D Spending by Drug
   and the Open Payments company summaries before trusting any map.

## Validation (2024)

* Part D prescriber file vs CMS Part D Spending by Drug: Eliquis 95.7% of $20.8B, Jardiance 93.4% of
  $11.4B, Dupixent (Pen + Syringe) 83% of $2.5B. The gap is CMS small-cell suppression, which
  hits specialty drugs hardest.
* `core.payment_fact` reproduces the raw undisputed Open Payments total to the dollar ($3,423.4M):
  the equal split across up to five products loses nothing.
* 72% of drug-attributed payment dollars match a Part D/B brand. Most of the rest is for products
  with no Part D/B prescriber rows at all (Comirnaty $178M, Pluvicto $64M, hospital-only drugs);
  bridging Open Payments names to Part B HCPCS via FDA generic names would recover only ~$14M.

## Known limits

* Medicare only (mostly 65+). There is no commercial or Medicaid prescriber-level data.
* CMS suppresses cells under 11 claims or beneficiaries, which undercounts rural HCPs and rare drugs.
* Part D has no diagnoses, so indication is inferred from specialty.
* TRx = claims. NRx/NBRx can't be observed in public data; claims per beneficiary is only a rough proxy.
* `gross_cost` is before rebates. Net revenue is often far lower.
* Open Payments covers meals, fees and travel, not sales-rep salaries, samples or DTC ads.
  When a payment names several products, the amount is split equally among them.
* Promotion is targeted at high prescribers, so these associations are not causal.
* `targeting_vs_sales` is a ratio of shares and explodes when a product's national payment pool is
  small (Lantus: one $4K payment -> 1,200x). Use `targeting_vs_sales_sm` (adds 1% of the national
  pool to both sides, pulling thin cells toward 1) and
  filter on `enough_evidence` (>= 10 payments and >= 120 fills).
* Connecticut replaced its 8 counties with 9 planning regions in 2022. ZIPs are mapped to the new
  regions through 2020 towns (`zcta_cousub` + Census `ct_cousub_xwalk`) so CT joins ACS, SVI and TIGER.
* Puerto Rico and the territories are in the tables but not drawn on the website map (Albers USA).
