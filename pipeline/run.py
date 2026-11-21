"""
One entry point.

    python -m pipeline.run download --year 2024
    python -m pipeline.run load     --year 2024
    python -m pipeline.run seeds                    # load seeds/*.csv into ref.*
    python -m pipeline.run sql                      # run sql/*.sql in order
    python -m pipeline.run all      --year 2024
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import psycopg

from . import download, load_raw

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PHARMA_DSN", "postgresql://pharma:pharma@localhost:5432/pharma")

# seed file -> (table, columns) ; DDL lives in sql/10_ref.sql
SEEDS = {   # parent tables first (foreign keys)
    "drug_class.csv": "ref.drug_class",
    "product.csv": "ref.product",
    "product_alias.csv": "ref.product_alias",
    "specialty_indication.csv": "ref.specialty_indication",
    "indication_measure.csv": "ref.indication_measure",
}


def run_sql(files: list[Path], year: int):
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute(f"SET app.year = '{int(year)}'")
        for f in files:
            t0 = time.time()
            print(f"[sql] {f.name}", flush=True)
            conn.execute(f.read_text())
            print(f"      {time.time()-t0:.1f}s")


def load_seeds():
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        # children first for truncation
        for fname in reversed(list(SEEDS)):
            cur.execute(f"TRUNCATE {SEEDS[fname]} CASCADE")
        for fname, table in SEEDS.items():
            path = ROOT / "seeds" / fname
            header = path.read_text().splitlines()[0]
            with cur.copy(f"COPY {table} ({header}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
                cp.write(path.read_bytes())
            print(f"[seed] {table}")
        conn.commit()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["download", "load", "seeds", "sql", "all"])
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--from-file", help="start sql at this file name (e.g. 30_mart.sql)")
    a = ap.parse_args(argv)
    sql_files = sorted((ROOT / "sql").glob("[0-9]*.sql"))
    if a.from_file:
        sql_files = [f for f in sql_files if f.name >= a.from_file]

    if a.step in ("download", "all"):
        download.main(["--year", str(a.year)])
    if a.step in ("load", "all"):
        load_raw.main(["--year", str(a.year)])
    if a.step in ("sql", "all", "seeds"):
        ddl = [f for f in sql_files if f.name < "20"]
        run_sql(ddl, a.year)          # schemas + ref tables must exist before seeds
        load_seeds()
        if a.step != "seeds":
            run_sql([f for f in sql_files if f.name >= "20"], a.year)


if __name__ == "__main__":
    main()
