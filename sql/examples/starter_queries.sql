-- =====================================================================
-- 50_examples.sql  —  starter questions (run manually, not by the pipeline;
-- the file name sorts after the build steps but every statement is a SELECT)
-- =====================================================================

-- Q1. Dupixent by indication nationally: sales, promotion, $ per fill
SELECT indication,
       round(sum(fills_30d))                        AS fills_30d,
       round(sum(gross_cost) / 1e6, 1)              AS gross_cost_musd,
       round(sum(pay_usd) / 1e3, 1)                 AS promo_kusd,
       round(sum(pay_usd) / NULLIF(sum(fills_30d), 0), 2) AS promo_usd_per_fill
FROM mart.county_product_indication
WHERE product_id = 'DUPIXENT'
GROUP BY indication ORDER BY fills_30d DESC NULLS LAST;

-- Q2. Head-to-head share in NJ/NY/PA counties: Eliquis vs Xarelto
SELECT t.county_fips, c.county_name,
       round(100 * max(t.class_share) FILTER (WHERE t.product_id = 'ELIQUIS'), 1) AS eliquis_pct,
       round(100 * max(t.class_share) FILTER (WHERE t.product_id = 'XARELTO'), 1) AS xarelto_pct,
       round(max(t.pay_usd) FILTER (WHERE t.product_id = 'ELIQUIS'))              AS eliquis_promo,
       round(max(t.pay_usd) FILTER (WHERE t.product_id = 'XARELTO'))              AS xarelto_promo
FROM mart.targeting_index t JOIN core.county c USING (county_fips)
WHERE t.class_id = 'DOAC' AND left(t.county_fips, 2) IN ('34', '36', '42')
GROUP BY 1, 2 ORDER BY xarelto_pct DESC NULLS LAST;

-- Q3. Most over-targeted counties per company (promo share >> sales share)
SELECT company, product_id, county_fips,
       round(targeting_vs_sales, 2) AS targeting_vs_sales, round(pay_usd) AS pay_usd
FROM (
  SELECT t.*, row_number() OVER (PARTITION BY product_id ORDER BY targeting_vs_sales DESC) AS rk
  FROM mart.targeting_index t
  WHERE n_prescribers >= 5 AND pay_usd > 1000
) x WHERE rk <= 5 ORDER BY company, product_id, rk;

-- Q4. Does promotion follow social vulnerability? (correlation by product)
SELECT product_id,
       round(corr(targeting_vs_burden, svi_overall)::numeric, 3) AS r_promo_svi,
       round(corr(class_share, svi_overall)::numeric, 3)         AS r_share_svi,
       count(*) AS n_counties
FROM mart.v_model_input
WHERE burden > 0
GROUP BY product_id ORDER BY product_id;

-- Q5. Company footprint (auto tier, all brands): top manufacturers by county count
SELECT manufacturer, count(DISTINCT county_fips) AS counties,
       round(sum(gross_cost) / 1e9, 2) AS gross_cost_busd, round(sum(pay_usd) / 1e6, 2) AS promo_musd
FROM mart.company_county_year
GROUP BY 1 ORDER BY gross_cost_busd DESC LIMIT 25;

-- Q6. HCP-level: payments vs prescribing for one product (input to a response model)
-- Part D lists brand variants separately ('DUPIXENT PEN', 'DUPIXENT SYRINGE'),
-- so roll up through the curated product_id rather than a single brand_key.
SELECT n.npi, p.specialty, p.county_fips,
       sum(n.trx) AS trx, sum(n.fills_30d) AS fills_30d,
       sum(n.pay_usd) AS pay_usd, sum(n.n_meals) AS n_meals
FROM mart.npi_brand_year n
JOIN core.drug d USING (brand_key)
JOIN core.prescriber p USING (npi)
WHERE d.product_id = 'DUPIXENT'
GROUP BY 1, 2, 3
ORDER BY pay_usd DESC LIMIT 50;
