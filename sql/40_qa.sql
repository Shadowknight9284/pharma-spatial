-- =====================================================================
-- 40_qa.sql  —  data-quality checks. Read qa.checks after every load;
-- low match rates tell you where seeds/product_alias.csv needs work.
-- =====================================================================
DROP TABLE IF EXISTS qa.checks;
CREATE TABLE qa.checks AS
SELECT now() AS run_at, core.cur_year() AS year, *
FROM (VALUES
  ('raw.partd_prov_drug rows',      (SELECT count(*) FROM raw.partd_prov_drug)::numeric),
  ('raw.op_general rows',           (SELECT count(*) FROM raw.op_general)),
  ('core.prescriber rows',          (SELECT count(*) FROM core.prescriber)),
  ('pct prescribers with county',   (SELECT round(100.0 * avg((county_fips IS NOT NULL)::int), 2) FROM core.prescriber)),
  ('pct Part D cost w/ manufacturer',
     (SELECT round(100.0 * sum(r.gross_cost) FILTER (WHERE d.manufacturer IS NOT NULL) / NULLIF(sum(r.gross_cost),0), 2)
      FROM core.rx_fact r JOIN core.drug d USING (brand_key)
      WHERE r.year = core.cur_year() AND r.benefit = 'D' AND NOT d.is_generic)),
  ('pct Part D brand cost w/ EPC class',
     (SELECT round(100.0 * sum(r.gross_cost) FILTER (WHERE d.epc_class IS NOT NULL) / NULLIF(sum(r.gross_cost),0), 2)
      FROM core.rx_fact r JOIN core.drug d USING (brand_key)
      WHERE r.year = core.cur_year() AND r.benefit = 'D' AND NOT d.is_generic)),
  ('pct OP drug $ matched to brand',
     (SELECT round(100.0 * sum(amount_alloc) FILTER (WHERE brand_key IS NOT NULL) / NULLIF(sum(amount_alloc),0), 2)
      FROM core.payment_fact WHERE year = core.cur_year() AND product_slot > 0)),
  ('pct OP $ to an NPI',
     (SELECT round(100.0 * sum(amount_alloc) FILTER (WHERE npi IS NOT NULL) / NULLIF(sum(amount_alloc),0), 2)
      FROM core.payment_fact WHERE year = core.cur_year())),
  ('curated products with zero Part D/B rows',
     (SELECT count(*) FROM ref.product p
      WHERE NOT EXISTS (SELECT 1 FROM core.drug d WHERE d.product_id = p.product_id))),
  ('pct curated fills allocated by specialty',
     (SELECT round(100.0 * sum(fills_30d) FILTER (WHERE alloc_rule = 'specialty') / NULLIF(sum(fills_30d),0), 2)
      FROM mart.npi_product_indication WHERE year = core.cur_year())),
  ('pct OP $ from entities mapped to a parent',
     (SELECT round(100.0 * sum(f.amount_alloc) FILTER (WHERE cm.mapped) / NULLIF(sum(f.amount_alloc),0), 2)
      FROM core.payment_fact f JOIN core.company_map cm ON cm.source = 'op' AND cm.name = f.manufacturer
      WHERE f.year = core.cur_year())),
  ('pct branded Part D cost w/ mapped parent',
     (SELECT round(100.0 * sum(r.gross_cost) FILTER (WHERE d.parent_company IN (SELECT parent FROM ref.company_parent))
                  / NULLIF(sum(r.gross_cost),0), 2)
      FROM core.rx_fact r JOIN core.drug d USING (brand_key)
      WHERE r.year = core.cur_year() AND r.benefit = 'D' AND NOT d.is_generic)),
  ('specialty weights not summing to 1',
     (SELECT count(*) FROM (SELECT product_id, prscrbr_type FROM ref.specialty_indication
                            GROUP BY 1,2 HAVING abs(sum(weight) - 1) > 0.001) x))
) AS t(check_name, value);

SELECT check_name, value FROM qa.checks ORDER BY check_name;

-- Worklists for the manual mapping step ------------------------------
-- 1) Top Open Payments product names with no brand match
DROP VIEW IF EXISTS qa.v_unmatched_op_products;
CREATE VIEW qa.v_unmatched_op_products AS
SELECT product_name, manufacturer, round(sum(amount_alloc)) AS usd, count(*) AS n
FROM core.payment_fact
WHERE product_slot > 0 AND brand_key IS NULL AND product_id IS NULL
GROUP BY 1, 2 ORDER BY usd DESC;

-- 2) Top-cost Part D brands not yet curated (candidates for ref.product)
DROP VIEW IF EXISTS qa.v_uncurated_top_brands;
CREATE VIEW qa.v_uncurated_top_brands AS
SELECT d.brand_key, d.generic_name, d.manufacturer, d.epc_class,
       round(sum(r.gross_cost)) AS gross_cost
FROM core.rx_fact r JOIN core.drug d USING (brand_key)
WHERE d.product_id IS NULL AND NOT d.is_generic
GROUP BY 1, 2, 3, 4 ORDER BY gross_cost DESC NULLS LAST;

-- 3) Specialties prescribing curated products with no indication rule
DROP VIEW IF EXISTS qa.v_unmapped_specialties;
CREATE VIEW qa.v_unmapped_specialties AS
SELECT product_id, specialty, round(sum(fills_30d)) AS fills_30d
FROM mart.npi_product_indication
WHERE alloc_rule <> 'specialty'
GROUP BY 1, 2 ORDER BY fills_30d DESC NULLS LAST;

-- 4) Biggest paying entities / manufacturers with no parent rule
DROP VIEW IF EXISTS qa.v_unmapped_companies;
CREATE VIEW qa.v_unmapped_companies AS
SELECT 'op' AS source, f.manufacturer AS name, round(sum(f.amount_alloc)) AS usd
FROM core.payment_fact f JOIN core.company_map cm ON cm.source = 'op' AND cm.name = f.manufacturer
WHERE NOT cm.mapped GROUP BY 1, 2
UNION ALL
SELECT 'partd', d.manufacturer, round(sum(r.gross_cost))
FROM core.rx_fact r JOIN core.drug d USING (brand_key)
JOIN core.company_map cm ON cm.source = 'partd' AND cm.name = d.manufacturer
WHERE NOT cm.mapped AND NOT d.is_generic GROUP BY 1, 2
ORDER BY usd DESC NULLS LAST;
