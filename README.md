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
export PHARMA_DATA_DIR=./data             # ~25 GB free disk for one year

python -m pipeline.run download --year 2024   # 1–3 h, mostly NPPES + Open Payments
python -m pipeline.run load     --year 2024   # 30–90 min (COPY into raw.*)
python -m pipeline.run sql      --year 2024   # seeds + core + mart + QA
psql "$PHARMA_DSN" -f sql/examples/starter_queries.sql

# optional county polygons
psql "$PHARMA_DSN" -c "CREATE EXTENSION IF NOT EXISTS postgis"
python -m pipeline.load_geo
```

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
```

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
4. **Company parents and co-promotion.** Open Payments lists the paying legal entity
   ("Sanofi-Aventis U.S. LLC", "Regeneron Healthcare Solutions"). Group entities into
   parent companies if you want company-level payment totals.
5. **Validate a few numbers.** Compare national totals with CMS Part D Spending by Drug
   and the Open Payments company summaries before trusting any map.

## Known limits

* Medicare only (mostly 65+). There is no commercial or Medicaid prescriber-level data.
* CMS suppresses cells under 11 claims or beneficiaries, which undercounts rural HCPs and rare drugs.
* Part D has no diagnoses, so indication is inferred from specialty.
* TRx = claims. NRx/NBRx can't be observed in public data; claims per beneficiary is only a rough proxy.
* `gross_cost` is before rebates. Net revenue is often far lower.
* Open Payments covers meals, fees and travel, not sales-rep salaries, samples or DTC ads.
  When a payment names several products, the amount is split equally among them.
* Promotion is targeted at high prescribers, so these associations are not causal.
