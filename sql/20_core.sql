-- =====================================================================
-- 20_core.sql  —  typed, cleaned entities and facts
-- Re-runnable per year: facts are deleted for app.year then re-inserted.
-- =====================================================================

-- ---------------------------------------------------------------------
-- GEOGRAPHY
-- ---------------------------------------------------------------------
-- ZIP (≈ZCTA) -> single county: the county holding the most of its land.
DROP TABLE IF EXISTS core.zip_county CASCADE;
CREATE TABLE core.zip_county AS
SELECT DISTINCT ON (geoid_zcta5_20)
       geoid_zcta5_20                    AS zip5,
       geoid_county_20                   AS county_fips,
       core.num(arealand_part)           AS land_part
FROM raw.zcta_county
WHERE geoid_zcta5_20 <> '' AND geoid_county_20 <> ''
ORDER BY geoid_zcta5_20, core.num(arealand_part) DESC NULLS LAST;
ALTER TABLE core.zip_county ADD PRIMARY KEY (zip5);

DROP TABLE IF EXISTS core.county CASCADE;
CREATE TABLE core.county AS
SELECT a.county_fips,
       a.name                                   AS county_name,
       left(a.county_fips, 2)                   AS state_fips,
       core.num(a.pop_total)::bigint            AS pop_total,
       core.num(a.pop_65p)::bigint              AS pop_65p,
       NULLIF(core.num(a.median_hh_income), -666666666) AS median_hh_income,
       NULLIF(core.num(s.rpl_themes), -999)     AS svi_overall,
       NULLIF(core.num(s.rpl_theme1), -999)     AS svi_socioeconomic,
       NULLIF(core.num(s.rpl_theme2), -999)     AS svi_household,
       NULLIF(core.num(s.rpl_theme3), -999)     AS svi_minority_language,
       NULLIF(core.num(s.rpl_theme4), -999)     AS svi_housing_transport
FROM raw.acs_county a
LEFT JOIN raw.svi_county s ON lpad(s.fips, 5, '0') = a.county_fips;
ALTER TABLE core.county ADD PRIMARY KEY (county_fips);

-- Disease prevalence per county (crude prevalence, % of adults)
DROP TABLE IF EXISTS core.disease_prev CASCADE;
CREATE TABLE core.disease_prev AS
SELECT lpad(locationid, 5, '0')           AS county_fips,
       measureid                          AS measure_id,
       max(measure)                       AS measure_name,
       max(core.num(year))::int           AS data_year,
       max(core.num(data_value))          AS prevalence_pct,
       max(core.num(totalpopulation))::bigint AS total_population
FROM raw.places_county
WHERE datavaluetypeid = 'CrdPrv'
GROUP BY 1, 2;
ALTER TABLE core.disease_prev ADD PRIMARY KEY (county_fips, measure_id);

-- ---------------------------------------------------------------------
-- PRESCRIBERS (HCPs)
-- Location: NPPES practice ZIP -> county. Specialty: CMS claims specialty
-- (what Part D/B call them), falling back to NPPES primary taxonomy.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS core.prescriber CASCADE;
CREATE TABLE core.prescriber AS
WITH nppes AS (
  SELECT n.npi,
         n.entity_type_code                                              AS entity_type,
         coalesce(NULLIF(n.provider_organization_name_legal_business_name,''),
                  n.provider_last_name_legal_name || ', ' || n.provider_first_name) AS name,
         n.provider_business_practice_location_address_city_name           AS city,
         n.provider_business_practice_location_address_state_name          AS state,
         left(n.provider_business_practice_location_address_postal_code,5) AS zip5,
         CASE WHEN n.healthcare_provider_primary_taxonomy_switch_1 = 'Y' THEN n.healthcare_provider_taxonomy_code_1
              WHEN n.healthcare_provider_primary_taxonomy_switch_2 = 'Y' THEN n.healthcare_provider_taxonomy_code_2
              WHEN n.healthcare_provider_primary_taxonomy_switch_3 = 'Y' THEN n.healthcare_provider_taxonomy_code_3
              ELSE n.healthcare_provider_taxonomy_code_1 END                 AS taxonomy_code
  FROM raw.nppes n
  WHERE coalesce(n.npi_deactivation_date, '') = ''
),
partd_spec AS (   -- one specialty per NPI: the one on most claims
  SELECT DISTINCT ON (prscrbr_npi) prscrbr_npi AS npi, prscrbr_type AS specialty
  FROM raw.partd_prov_drug
  GROUP BY prscrbr_npi, prscrbr_type
  ORDER BY prscrbr_npi, sum(core.num(tot_clms)) DESC NULLS LAST
),
partb_spec AS (
  SELECT DISTINCT ON (rndrng_npi) rndrng_npi AS npi, rndrng_prvdr_type AS specialty,
         rndrng_prvdr_zip5 AS zip5, rndrng_prvdr_ruca AS ruca
  FROM raw.partb_prov_svc
  ORDER BY rndrng_npi, core.num(tot_srvcs) DESC NULLS LAST
)
SELECT n.npi,
       n.entity_type,
       n.name,
       n.city,
       n.state,
       coalesce(n.zip5, b.zip5)                               AS zip5,
       zc.county_fips,
       coalesce(d.specialty, b.specialty)                      AS specialty,
       n.taxonomy_code,
       core.num(b.ruca)                                        AS ruca
FROM nppes n
LEFT JOIN partd_spec d ON d.npi = n.npi
LEFT JOIN partb_spec b ON b.npi = n.npi
LEFT JOIN core.zip_county zc ON zc.zip5 = coalesce(n.zip5, b.zip5);
ALTER TABLE core.prescriber ADD PRIMARY KEY (npi);
CREATE INDEX ON core.prescriber (county_fips);
CREATE INDEX ON core.prescriber (specialty);

-- ---------------------------------------------------------------------
-- DRUG DIMENSION (all products, automatic tier)
-- brand_key = normalised Part D brand name, or 'HCPCS:<code>' for Part B.
-- Manufacturer comes free from Part D Spending by Drug; class from FDA EPC.
-- product_id (curated tier) is filled where ref.product_alias matches.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS core.drug CASCADE;
CREATE TABLE core.drug AS
WITH partd AS (
  SELECT core.norm_name(brnd_name) AS brand_key,
         max(brnd_name)            AS brand_name,
         max(gnrc_name)            AS generic_name,
         bool_or(core.norm_name(brnd_name) = core.norm_name(gnrc_name)) AS is_generic
  FROM raw.partd_prov_drug GROUP BY 1
),
mfr AS (   -- per-manufacturer rows only (the file also has an 'Overall' row)
  SELECT core.norm_name(brnd_name) AS brand_key,
         string_agg(DISTINCT mftr_name, '; ' ORDER BY mftr_name) AS manufacturer,
         count(DISTINCT mftr_name) AS n_manufacturers
  FROM raw.partd_spend_drug
  WHERE mftr_name <> 'Overall'
  GROUP BY 1
),
epc AS (   -- FDA Established Pharmacologic Class by generic name
  SELECT core.norm_name(nonproprietaryname) AS generic_key,
         mode() WITHIN GROUP (ORDER BY substring(pharm_classes from '([^,]*)\[EPC\]')) AS epc_class
  FROM raw.fda_ndc_product
  WHERE pharm_classes LIKE '%[EPC]%'
  GROUP BY 1
),
partb AS (
  SELECT 'HCPCS:' || hcpcs_cd AS brand_key, max(hcpcs_desc) AS brand_name,
         max(hcpcs_desc) AS generic_name, false AS is_generic
  FROM raw.partb_prov_svc WHERE hcpcs_drug_ind = 'Y' GROUP BY hcpcs_cd
)
SELECT p.brand_key, p.brand_name, p.generic_name, p.is_generic,
       'D'::text AS benefit,
       m.manufacturer, m.n_manufacturers,
       trim(e.epc_class) AS epc_class,
       (SELECT a.product_id FROM ref.product_alias a
         WHERE a.source = 'partd_brand'
           AND (p.brand_key = a.alias OR (a.match = 'prefix' AND p.brand_key LIKE a.alias || '%'))
         ORDER BY length(a.alias) DESC LIMIT 1) AS product_id
FROM partd p
LEFT JOIN mfr m ON m.brand_key = p.brand_key
LEFT JOIN epc e ON e.generic_key = core.norm_name(p.generic_name)
UNION ALL
SELECT b.brand_key, b.brand_name, b.generic_name, b.is_generic, 'B', NULL, NULL, NULL,
       (SELECT a.product_id FROM ref.product_alias a
         WHERE a.source = 'partb_hcpcs' AND a.alias = substr(b.brand_key, 7) LIMIT 1)
FROM partb b;
ALTER TABLE core.drug ADD PRIMARY KEY (brand_key);
CREATE INDEX ON core.drug (product_id);
CREATE INDEX ON core.drug (brand_key text_pattern_ops);   -- prefix LIKE lookups

-- ---------------------------------------------------------------------
-- RX FACT: one row per year x prescriber x drug
--   trx         = Part D claims (fills incl. refills)  | Part B: services (units!)
--   fills_30d   = 30-day-equivalent fills (normalises 90-day mail order)
--   patients    = beneficiaries (NULL when CMS suppresses <11)
--   gross_cost  = Part D Tot_Drug_Cst (pre-rebate) | Part B allowed amount
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.rx_fact (
  year        int     NOT NULL,
  npi         text    NOT NULL,
  brand_key   text    NOT NULL,
  benefit     text    NOT NULL,
  trx         numeric,
  fills_30d   numeric,
  day_supply  numeric,
  patients    numeric,
  gross_cost  numeric,
  PRIMARY KEY (year, npi, brand_key)
);
DELETE FROM core.rx_fact WHERE year = core.cur_year();

INSERT INTO core.rx_fact
SELECT core.cur_year(), prscrbr_npi, core.norm_name(brnd_name), 'D',
       sum(core.num(tot_clms)), sum(core.num(tot_30day_fills)),
       sum(core.num(tot_day_suply)), sum(core.num(tot_benes)),
       sum(core.num(tot_drug_cst))
FROM raw.partd_prov_drug
GROUP BY prscrbr_npi, core.norm_name(brnd_name);

INSERT INTO core.rx_fact
SELECT core.cur_year(), rndrng_npi, 'HCPCS:' || hcpcs_cd, 'B',
       sum(core.num(tot_srvcs)), NULL, NULL,
       max(core.num(tot_benes)),          -- benes can't be summed across place of service
       sum(core.num(tot_srvcs) * core.num(avg_mdcr_alowd_amt))
FROM raw.partb_prov_svc
WHERE hcpcs_drug_ind = 'Y'
GROUP BY rndrng_npi, hcpcs_cd;
CREATE INDEX IF NOT EXISTS rx_fact_brand_idx ON core.rx_fact (year, brand_key);

-- ---------------------------------------------------------------------
-- PAYMENT FACT: one row per Open Payments record x product listed.
-- A payment can name up to 5 products; the amount is split equally
-- (documented assumption). Unattributed payments keep product = NULL.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS core.payment_fact (
  year            int     NOT NULL,
  record_id       text    NOT NULL,
  product_slot    int     NOT NULL,       -- 1..5, 0 = no product named
  npi             text,
  recipient_type  text,
  op_specialty    text,
  manufacturer    text,
  nature          text,
  payment_date    date,
  amount_total    numeric,
  amount_alloc    numeric,                -- amount_total / n products named
  product_name    text,                   -- normalised as reported
  ndc9            text,
  brand_key       text,                   -- matched to core.drug (auto tier)
  product_id      text,                   -- matched to ref.product (curated tier)
  PRIMARY KEY (year, record_id, product_slot)
);
DELETE FROM core.payment_fact WHERE year = core.cur_year();

INSERT INTO core.payment_fact
WITH op AS (
  SELECT * FROM raw.op_general
  WHERE coalesce(dispute_status_for_publication, '') <> 'Yes'
),
slots AS (
  SELECT op.record_id, s.slot, s.kind, core.norm_name(s.name) AS product_name, core.ndc9(s.ndc) AS ndc9
  FROM op
  CROSS JOIN LATERAL (VALUES
    (1, indicate_drug_or_biological_or_device_or_medical_supply_1, name_of_drug_or_biological_or_device_or_medical_supply_1, associated_drug_or_biological_ndc_1),
    (2, indicate_drug_or_biological_or_device_or_medical_supply_2, name_of_drug_or_biological_or_device_or_medical_supply_2, associated_drug_or_biological_ndc_2),
    (3, indicate_drug_or_biological_or_device_or_medical_supply_3, name_of_drug_or_biological_or_device_or_medical_supply_3, associated_drug_or_biological_ndc_3),
    (4, indicate_drug_or_biological_or_device_or_medical_supply_4, name_of_drug_or_biological_or_device_or_medical_supply_4, associated_drug_or_biological_ndc_4),
    (5, indicate_drug_or_biological_or_device_or_medical_supply_5, name_of_drug_or_biological_or_device_or_medical_supply_5, associated_drug_or_biological_ndc_5)
  ) AS s(slot, kind, name, ndc)
  WHERE core.norm_name(s.name) IS NOT NULL
    AND coalesce(s.kind, '') IN ('Drug', 'Biological', '')
),
nslots AS (SELECT record_id, count(*) AS n FROM slots GROUP BY record_id),
-- FDA NDC (labeler+product) -> brand name, to rescue payments with odd product names
ndc_brand AS (
  SELECT DISTINCT ON (core.ndc9(productndc)) core.ndc9(productndc) AS ndc9,
         core.norm_name(proprietaryname) AS brand_key
  FROM raw.fda_ndc_product WHERE productndc <> ''
  ORDER BY core.ndc9(productndc), proprietaryname
),
-- Resolve each DISTINCT (name, ndc) once (thousands) instead of per payment (millions):
--   1) exact brand match   2) brand variant by prefix ('TOUJEO' -> 'TOUJEO SOLOSTAR')
--   3) FDA NDC -> brand    4) curated alias for product_id
names AS (
  SELECT DISTINCT product_name, ndc9 FROM slots
),
name_map AS (
  SELECT nm.product_name, nm.ndc9,
         coalesce(d1.brand_key, d2.brand_key, d3.brand_key) AS brand_key,
         coalesce(
           (SELECT a.product_id FROM ref.product_alias a
             WHERE a.source = 'op_name'
               AND (nm.product_name = a.alias OR (a.match = 'prefix' AND nm.product_name LIKE a.alias || '%'))
             ORDER BY length(a.alias) DESC LIMIT 1),
           d1.product_id, d2.product_id, d3.product_id)     AS product_id
  FROM names nm
  LEFT JOIN core.drug d1 ON d1.brand_key = nm.product_name
  LEFT JOIN LATERAL (
    SELECT d.brand_key, d.product_id FROM core.drug d
    WHERE d1.brand_key IS NULL AND d.benefit = 'D'
      AND d.brand_key LIKE nm.product_name || ' %'
    ORDER BY length(d.brand_key) LIMIT 1) d2 ON true
  LEFT JOIN ndc_brand nb ON nb.ndc9 = nm.ndc9 AND d1.brand_key IS NULL AND d2.brand_key IS NULL
  LEFT JOIN core.drug d3 ON d3.brand_key = nb.brand_key
),
expanded AS (
  SELECT o.*, s.slot, s.product_name, s.ndc9, coalesce(ns.n, 0) AS n_products,
         m.brand_key, m.product_id
  FROM op o
  LEFT JOIN slots s  ON s.record_id = o.record_id
  LEFT JOIN nslots ns ON ns.record_id = o.record_id
  LEFT JOIN name_map m ON m.product_name = s.product_name
                      AND m.ndc9 IS NOT DISTINCT FROM s.ndc9
)
SELECT core.cur_year(),
       e.record_id,
       coalesce(e.slot, 0),
       NULLIF(e.covered_recipient_npi, ''),
       e.covered_recipient_type,
       e.covered_recipient_specialty_1,
       e.applicable_manufacturer_or_applicable_gpo_making_payment_name,
       e.nature_of_payment_or_transfer_of_value,
       to_date(NULLIF(e.date_of_payment, ''), 'MM/DD/YYYY'),
       core.num(e.total_amount_of_payment_usdollars),
       core.num(e.total_amount_of_payment_usdollars) / GREATEST(e.n_products, 1),
       e.product_name,
       e.ndc9,
       e.brand_key,
       e.product_id
FROM expanded e;

CREATE INDEX IF NOT EXISTS pay_npi_idx   ON core.payment_fact (year, npi);
CREATE INDEX IF NOT EXISTS pay_brand_idx ON core.payment_fact (year, brand_key);
CREATE INDEX IF NOT EXISTS pay_prod_idx  ON core.payment_fact (year, product_id);

ANALYZE core.rx_fact; ANALYZE core.payment_fact; ANALYZE core.prescriber;
