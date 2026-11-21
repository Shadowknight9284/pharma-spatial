-- =====================================================================
-- 30_mart.sql  —  analysis cubes
--   AUTO tier    : every brand, company from CMS, class from FDA EPC
--   CURATED tier : products in ref.product, with indication allocation
-- =====================================================================

-- ---------------------------------------------------------------------
-- AUTO: prescriber x brand x year  (sales + promotion side by side)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart.npi_brand_year CASCADE;
CREATE TABLE mart.npi_brand_year AS
WITH pay AS (
  SELECT year, npi, brand_key,
         sum(amount_alloc)                                         AS pay_usd,
         count(*)                                                  AS n_payments,
         count(*) FILTER (WHERE nature = 'Food and Beverage')      AS n_meals,
         sum(amount_alloc) FILTER (WHERE nature ILIKE '%speaker%'
                                      OR nature ILIKE '%faculty%') AS speaker_usd
  FROM core.payment_fact
  WHERE npi IS NOT NULL AND brand_key IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT coalesce(r.year, p.year)           AS year,
       coalesce(r.npi, p.npi)             AS npi,
       coalesce(r.brand_key, p.brand_key) AS brand_key,
       r.trx, r.fills_30d, r.patients, r.gross_cost,
       coalesce(p.pay_usd, 0)             AS pay_usd,
       coalesce(p.n_payments, 0)          AS n_payments,
       coalesce(p.n_meals, 0)             AS n_meals,
       coalesce(p.speaker_usd, 0)         AS speaker_usd
FROM core.rx_fact r
FULL JOIN pay p USING (year, npi, brand_key);
CREATE INDEX ON mart.npi_brand_year (year, brand_key);
CREATE INDEX ON mart.npi_brand_year (npi);

-- ---------------------------------------------------------------------
-- AUTO: county x brand x year, with share within FDA class
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart.county_brand_year CASCADE;
CREATE TABLE mart.county_brand_year AS
WITH agg AS (
  SELECT n.year, p.county_fips, n.brand_key,
         count(DISTINCT n.npi) FILTER (WHERE n.trx > 0)   AS n_prescribers,
         count(DISTINCT n.npi) FILTER (WHERE n.pay_usd > 0) AS n_paid_hcps,
         sum(n.trx)         AS trx,
         sum(n.fills_30d)   AS fills_30d,
         sum(n.patients)    AS patients_min,      -- lower bound (suppression)
         sum(n.gross_cost)  AS gross_cost,
         sum(n.pay_usd)     AS pay_usd,
         sum(n.n_payments)  AS n_payments
  FROM mart.npi_brand_year n
  JOIN core.prescriber p ON p.npi = n.npi
  WHERE p.county_fips IS NOT NULL
  GROUP BY 1, 2, 3
)
SELECT a.*,
       d.brand_name, d.generic_name, d.manufacturer, d.epc_class, d.product_id, d.is_generic,
       c.pop_65p,
       1000.0 * a.fills_30d / NULLIF(c.pop_65p, 0)                         AS fills_per_1k_65p,
       a.pay_usd / NULLIF(a.n_prescribers, 0)                              AS pay_per_prescriber,
       a.fills_30d / NULLIF(sum(a.fills_30d) OVER (PARTITION BY a.year, a.county_fips, d.epc_class), 0)
                                                                           AS share_of_class
FROM agg a
JOIN core.drug d   ON d.brand_key = a.brand_key
LEFT JOIN core.county c ON c.county_fips = a.county_fips;
CREATE INDEX ON mart.county_brand_year (year, brand_key);
CREATE INDEX ON mart.county_brand_year (county_fips);

-- ---------------------------------------------------------------------
-- AUTO: company x county  (portfolio footprint)
-- Multi-manufacturer brands keep the joined string; split in analysis.
-- ---------------------------------------------------------------------
DROP VIEW IF EXISTS mart.company_county_year;
CREATE VIEW mart.company_county_year AS
SELECT year, county_fips, manufacturer,
       count(DISTINCT brand_key) AS n_brands,
       sum(trx) AS trx, sum(gross_cost) AS gross_cost, sum(pay_usd) AS pay_usd
FROM mart.county_brand_year
WHERE manufacturer IS NOT NULL AND NOT is_generic
GROUP BY 1, 2, 3;

-- ---------------------------------------------------------------------
-- CURATED: prescriber x product x indication x year
-- Indication from specialty: exact specialty row > '*' row > product default.
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart.npi_product_indication CASCADE;
CREATE TABLE mart.npi_product_indication AS
WITH np AS (   -- roll brand variants (Toujeo Solostar, Toujeo Max...) up to product
  SELECT n.year, n.npi, d.product_id,
         sum(n.trx) FILTER (WHERE n.benefit = 'D') AS trx,        -- Part D claims only
         sum(n.trx) FILTER (WHERE n.benefit = 'B') AS partb_units, -- Part B HCPCS units (not scripts!)
         sum(n.fills_30d) AS fills_30d, sum(n.patients) AS patients,
         sum(n.gross_cost) AS gross_cost                           -- $ comparable across D and B
  FROM core.rx_fact n JOIN core.drug d ON d.brand_key = n.brand_key
  WHERE d.product_id IS NOT NULL
  GROUP BY 1, 2, 3
),
pp AS (
  SELECT year, npi, product_id, sum(amount_alloc) AS pay_usd, count(*) AS n_payments
  FROM core.payment_fact WHERE npi IS NOT NULL AND product_id IS NOT NULL
  GROUP BY 1, 2, 3
),
both_ AS (
  SELECT coalesce(np.year, pp.year) AS year, coalesce(np.npi, pp.npi) AS npi,
         coalesce(np.product_id, pp.product_id) AS product_id,
         np.trx, np.partb_units, np.fills_30d, np.patients, np.gross_cost,
         coalesce(pp.pay_usd, 0) AS pay_usd, coalesce(pp.n_payments, 0) AS n_payments
  FROM np FULL JOIN pp USING (year, npi, product_id)
),
alloc AS (
  SELECT b.*, pr.specialty, pr.county_fips,
         coalesce(si.indication, sw.indication, p.default_indication) AS indication,
         coalesce(si.weight, sw.weight, 1)                           AS weight,
         CASE WHEN si.indication IS NOT NULL THEN 'specialty'
              WHEN sw.indication IS NOT NULL THEN 'wildcard'
              ELSE 'default' END                                     AS alloc_rule
  FROM both_ b
  JOIN ref.product p        ON p.product_id = b.product_id
  LEFT JOIN core.prescriber pr ON pr.npi = b.npi
  LEFT JOIN ref.specialty_indication si
         ON si.product_id = b.product_id AND si.prscrbr_type = pr.specialty
  LEFT JOIN ref.specialty_indication sw
         ON sw.product_id = b.product_id AND sw.prscrbr_type = '*'
        AND NOT EXISTS (SELECT 1 FROM ref.specialty_indication x
                        WHERE x.product_id = b.product_id AND x.prscrbr_type = pr.specialty)
)
SELECT year, npi, product_id, indication, specialty, county_fips, alloc_rule, weight,
       trx * weight AS trx, partb_units * weight AS partb_units, fills_30d * weight AS fills_30d, patients * weight AS patients,
       gross_cost * weight AS gross_cost, pay_usd * weight AS pay_usd,
       n_payments * weight AS n_payments
FROM alloc;
CREATE INDEX ON mart.npi_product_indication (year, product_id, indication);

-- ---------------------------------------------------------------------
-- CURATED: county x product x indication, with disease burden
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart.county_product_indication CASCADE;
CREATE TABLE mart.county_product_indication AS
WITH agg AS (
  SELECT year, county_fips, product_id, indication,
         count(DISTINCT npi) FILTER (WHERE trx > 0) AS n_prescribers,
         sum(trx) AS trx, sum(partb_units) AS partb_units, sum(fills_30d) AS fills_30d,
         sum(patients) AS patients_min,
         sum(gross_cost) AS gross_cost, sum(pay_usd) AS pay_usd, sum(n_payments) AS n_payments
  FROM mart.npi_product_indication
  WHERE county_fips IS NOT NULL
  GROUP BY 1, 2, 3, 4
)
SELECT a.*, p.company, p.class_id, p.brand_name,
       im.places_measure_id,
       dp.prevalence_pct,
       CASE WHEN dp.prevalence_pct IS NOT NULL
            THEN dp.prevalence_pct / 100.0 * c.pop_total
            ELSE c.pop_65p END                                   AS burden_denominator,
       CASE WHEN dp.prevalence_pct IS NOT NULL THEN 'places_prevalence' ELSE 'pop_65p' END
                                                                 AS burden_basis,
       c.svi_overall, c.median_hh_income
FROM agg a
JOIN ref.product p ON p.product_id = a.product_id
LEFT JOIN ref.indication_measure im ON im.indication = a.indication
LEFT JOIN core.disease_prev dp ON dp.county_fips = a.county_fips AND dp.measure_id = im.places_measure_id
LEFT JOIN core.county c ON c.county_fips = a.county_fips;

-- ---------------------------------------------------------------------
-- CURATED: targeting index per county x product
--   promo_share  = county's share of the product's national payments
--   sales_share  = county's share of the product's national 30-day fills
--   burden_share = county's share of national disease burden for the class
--   targeting_vs_sales  > 1 : promoted more than its sales would predict
--   targeting_vs_burden > 1 : promoted more than disease burden would predict
--   class_share  = product share of its class in the county (competition)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart.targeting_index CASCADE;
CREATE TABLE mart.targeting_index AS
WITH cp AS (
  SELECT year, county_fips, product_id, max(company) AS company, max(class_id) AS class_id,
         sum(fills_30d) AS fills_30d, sum(gross_cost) AS gross_cost, sum(pay_usd) AS pay_usd,
         sum(n_prescribers) AS n_prescribers,
         max(burden_denominator) AS burden            -- primary indication's denominator
  FROM mart.county_product_indication GROUP BY 1, 2, 3
),
tot AS (
  SELECT cp.*,
         sum(pay_usd)   OVER (PARTITION BY year, product_id) AS nat_pay,
         sum(fills_30d) OVER (PARTITION BY year, product_id) AS nat_fills,
         sum(burden)    OVER (PARTITION BY year, product_id) AS nat_burden,
         sum(fills_30d) OVER (PARTITION BY year, county_fips, class_id) AS class_fills
  FROM cp
)
SELECT year, county_fips, product_id, company, class_id,
       fills_30d, gross_cost, pay_usd, n_prescribers, burden,
       pay_usd / NULLIF(nat_pay, 0)     AS promo_share,
       fills_30d / NULLIF(nat_fills, 0) AS sales_share,
       burden / NULLIF(nat_burden, 0)   AS burden_share,
       (pay_usd / NULLIF(nat_pay, 0)) / NULLIF(fills_30d / NULLIF(nat_fills, 0), 0) AS targeting_vs_sales,
       (pay_usd / NULLIF(nat_pay, 0)) / NULLIF(burden / NULLIF(nat_burden, 0), 0)   AS targeting_vs_burden,
       fills_30d / NULLIF(class_fills, 0) AS class_share,
       pay_usd / NULLIF(fills_30d, 0)     AS promo_usd_per_fill
FROM tot;
CREATE INDEX ON mart.targeting_index (year, product_id);

-- Flat export for spatial modelling in Python / R (join geo.county_shape on county_fips)
DROP VIEW IF EXISTS mart.v_model_input;
CREATE VIEW mart.v_model_input AS
SELECT t.*, c.county_name, c.state_fips, c.pop_total, c.pop_65p,
       c.median_hh_income, c.svi_overall
FROM mart.targeting_index t
LEFT JOIN core.county c USING (county_fips);
