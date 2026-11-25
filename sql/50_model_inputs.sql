-- =====================================================================
-- 50_model_inputs.sql  —  analysis-ready inputs for analysis/models.py
-- =====================================================================

-- ---------------------------------------------------------------------
-- Prescriber x curated product, within competitive class (Part D).
-- One row per (NPI active in the class) x (every product in that class),
-- so non-prescribers of a product appear with share = 0.
--   share         = product fills / NPI's class fills
--   pay_usd etc.  = Open Payments naming THIS product, to this NPI
--   rival_pay_usd = payments naming the other products in the class
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS mart.npi_class_product CASCADE;
CREATE TABLE mart.npi_class_product AS
WITH rx AS (
  SELECT r.year, r.npi, d.product_id, sum(r.fills_30d) AS fills
  FROM core.rx_fact r JOIN core.drug d USING (brand_key)
  WHERE d.product_id IS NOT NULL AND r.benefit = 'D'
  GROUP BY 1, 2, 3
),
pay AS (
  SELECT year, npi, product_id,
         sum(amount_alloc)                                         AS pay_usd,
         count(*)                                                  AS n_payments,
         count(*) FILTER (WHERE nature = 'Food and Beverage')      AS n_meals,
         coalesce(sum(amount_alloc) FILTER (WHERE nature ILIKE '%speaker%'
                                               OR nature ILIKE '%faculty%'), 0) AS speaker_usd
  FROM core.payment_fact
  WHERE npi IS NOT NULL AND product_id IS NOT NULL
  GROUP BY 1, 2, 3
),
cls AS (
  SELECT rx.year, rx.npi, p.class_id, sum(rx.fills) AS class_fills
  FROM rx JOIN ref.product p USING (product_id)
  GROUP BY 1, 2, 3
),
grid AS (
  SELECT c.year, c.npi, c.class_id, c.class_fills, p.product_id,
         coalesce(rx.fills, 0)          AS fills,
         coalesce(pay.pay_usd, 0)       AS pay_usd,
         coalesce(pay.n_payments, 0)    AS n_payments,
         coalesce(pay.n_meals, 0)       AS n_meals,
         coalesce(pay.speaker_usd, 0)   AS speaker_usd
  FROM cls c
  JOIN ref.product p ON p.class_id = c.class_id
  LEFT JOIN rx  ON rx.year  = c.year AND rx.npi  = c.npi AND rx.product_id  = p.product_id
  LEFT JOIN pay ON pay.year = c.year AND pay.npi = c.npi AND pay.product_id = p.product_id
  WHERE c.class_fills > 0
)
SELECT g.*,
       g.fills / g.class_fills                                                       AS share,
       sum(g.pay_usd) OVER (PARTITION BY g.year, g.npi, g.class_id) - g.pay_usd       AS rival_pay_usd,
       pr.specialty, pr.county_fips, pr.entity_type
FROM grid g
LEFT JOIN core.prescriber pr ON pr.npi = g.npi;
CREATE INDEX ON mart.npi_class_product (year, product_id);
ANALYZE mart.npi_class_product;
