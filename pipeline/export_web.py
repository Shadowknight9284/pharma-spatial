"""
Export small, aggregated JSON for the static website in web/.

    python -m pipeline.export_web --year 2024

All shaping happens in SQL (json_build_object / json_agg); this script only
writes the results. Output is county- and company-level only: no NPIs or
prescriber names leave the database.

    web/data/meta.json              build info, QA checks, product list
    web/data/counties.geojson       simplified county polygons (PostGIS)
    web/data/states.geojson         state outlines (ST_Union of counties)
    web/data/county_base.json       population, SVI, income, PLACES prevalence
    web/data/product/<ID>.json      county targeting metrics for one product
    web/data/indications.json       product x indication national totals
    web/data/companies.json         parent company: sales vs promotion
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "data"
DSN = os.environ.get("PHARMA_DSN", "postgresql://pharma:pharma@localhost:5432/pharma")

# Columnar (one array per field) keeps the files small and fast to parse.
SQL = {
    "meta": """
        SELECT json_build_object(
          'year', %(year)s,
          'built_at', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI"Z"'),
          'qa', (SELECT json_agg(json_build_object('check', check_name, 'value', value) ORDER BY check_name)
                 FROM qa.checks),
          'products', (
            SELECT json_agg(json_build_object(
                     'id', p.product_id, 'brand', p.brand_name, 'generic', p.generic_name,
                     'company', p.company, 'class', p.class_id, 'class_name', c.class_name,
                     'benefit', p.benefit,
                     'fills_30d', round(t.fills), 'gross_cost', round(t.cost), 'pay_usd', round(t.pay),
                     'n_counties', t.n)
                   ORDER BY p.class_id, t.cost DESC NULLS LAST)
            FROM ref.product p
            JOIN ref.drug_class c USING (class_id)
            LEFT JOIN (SELECT product_id, sum(fills_30d) fills, sum(gross_cost) cost,
                              sum(pay_usd) pay, count(*) n
                       FROM mart.targeting_index WHERE year = %(year)s GROUP BY 1) t USING (product_id)))
    """,
    "counties": """
        SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(
                 json_build_object('type', 'Feature', 'id', g.county_fips,
                   'properties', json_build_object('name', g.county_name, 'state', g.state),
                   'geometry', ST_AsGeoJSON(
                       ST_SimplifyPreserveTopology(g.geometry, 0.008), 3)::json)))
        FROM geo.county_shape g
    """,
    "states": """
        SELECT json_build_object('type', 'FeatureCollection', 'features', json_agg(
                 json_build_object('type', 'Feature', 'id', st,
                   'geometry', ST_AsGeoJSON(ST_SimplifyPreserveTopology(g, 0.01), 3)::json)))
        FROM (SELECT left(county_fips, 2) st, ST_Union(geometry) g FROM geo.county_shape GROUP BY 1) x
    """,
    "county_base": """
        WITH prev AS (
          SELECT county_fips,
                 max(prevalence_pct) FILTER (WHERE measure_id = 'DIABETES') AS diabetes,
                 max(prevalence_pct) FILTER (WHERE measure_id = 'CASTHMA')  AS asthma,
                 max(prevalence_pct) FILTER (WHERE measure_id = 'COPD')     AS copd,
                 max(prevalence_pct) FILTER (WHERE measure_id = 'CHD')      AS chd
          FROM core.disease_prev GROUP BY 1
        )
        SELECT json_build_object(
          'fips',     json_agg(c.county_fips ORDER BY c.county_fips),
          'name',     json_agg(c.county_name ORDER BY c.county_fips),
          'pop',      json_agg(c.pop_total ORDER BY c.county_fips),
          'pop65',    json_agg(c.pop_65p ORDER BY c.county_fips),
          'income',   json_agg(c.median_hh_income ORDER BY c.county_fips),
          'svi',      json_agg(round(c.svi_overall, 3) ORDER BY c.county_fips),
          'diabetes', json_agg(p.diabetes ORDER BY c.county_fips),
          'asthma',   json_agg(p.asthma ORDER BY c.county_fips),
          'copd',     json_agg(p.copd ORDER BY c.county_fips),
          'chd',      json_agg(p.chd ORDER BY c.county_fips))
        FROM core.county c LEFT JOIN prev p USING (county_fips)
    """,
    "product": """
        SELECT json_build_object(
          'id', %(pid)s::text,
          'fips',      json_agg(t.county_fips ORDER BY t.county_fips),
          'fills',     json_agg(round(t.fills_30d) ORDER BY t.county_fips),
          'cost',      json_agg(round(t.gross_cost) ORDER BY t.county_fips),
          'pay',       json_agg(round(t.pay_usd) ORDER BY t.county_fips),
          'n_pay',     json_agg(round(t.n_payments) ORDER BY t.county_fips),
          'n_hcp',     json_agg(t.n_prescribers ORDER BY t.county_fips),
          'per1k65',   json_agg(round(1000.0 * t.fills_30d / NULLIF(c.pop_65p, 0), 2) ORDER BY t.county_fips),
          'tvs',       json_agg(round(t.targeting_vs_sales_sm, 3) ORDER BY t.county_fips),
          'tvb',       json_agg(round(t.targeting_vs_burden, 3) ORDER BY t.county_fips),
          'share',     json_agg(round(t.class_share, 4) ORDER BY t.county_fips),
          'ok',        json_agg(t.enough_evidence ORDER BY t.county_fips))
        FROM mart.targeting_index t
        LEFT JOIN core.county c USING (county_fips)
        WHERE t.year = %(year)s AND t.product_id = %(pid)s
    """,
    "indications": """
        SELECT json_agg(json_build_object(
                 'product', product_id, 'indication', indication,
                 'fills_30d', round(f), 'gross_cost', round(g), 'pay_usd', round(p))
               ORDER BY product_id, f DESC NULLS LAST)
        FROM (SELECT product_id, indication, sum(fills_30d) f, sum(gross_cost) g, sum(pay_usd) p
              FROM mart.county_product_indication WHERE year = %(year)s GROUP BY 1, 2) x
    """,
    "companies": """
        SELECT json_agg(json_build_object(
                 'parent', parent_company, 'n_brands', n_brands,
                 'gross_cost', round(gross_cost), 'partd_claims', round(partd_claims),
                 'promo_drug', round(promo_drug_usd), 'promo_all', round(promo_all_usd),
                 'n_entities', n_paying_entities, 'n_hcps', n_paid_hcps)
               ORDER BY coalesce(gross_cost, 0) + coalesce(promo_all_usd, 0) * 50 DESC)
        FROM (SELECT * FROM mart.parent_company_year
              WHERE year = %(year)s AND parent_company NOT LIKE '%%;%%'
                AND (gross_cost > 2e8 OR promo_all_usd > 5e6)) x
    """,
}


def q(cur, name, **params):
    cur.execute(SQL[name], params)
    return cur.fetchone()[0]


def write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=float))
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size / 1e3:,.0f} KB")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    a = ap.parse_args(argv)
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        meta = q(cur, "meta", year=a.year)
        write(OUT / "meta.json", meta)
        write(OUT / "counties.geojson", q(cur, "counties"))
        write(OUT / "states.geojson", q(cur, "states"))
        write(OUT / "county_base.json", q(cur, "county_base"))
        write(OUT / "indications.json", q(cur, "indications", year=a.year))
        write(OUT / "companies.json", q(cur, "companies", year=a.year))
        for p in meta["products"]:
            write(OUT / "product" / f"{p['id']}.json", q(cur, "product", year=a.year, pid=p["id"]))


if __name__ == "__main__":
    main()
