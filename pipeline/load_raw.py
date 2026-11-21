"""
Stream downloaded files into the `raw` schema of Postgres.

Raw tables are created from each file's header (all TEXT, snake_case),
so a column reorder by CMS never breaks the load. Typing and cleaning
happen in SQL (sql/20_core.sql).

    export PHARMA_DSN=postgresql://pharma:pharma@localhost:5432/pharma
    python -m pipeline.load_raw --year 2024
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import zipfile
from pathlib import Path

import pandas as pd
import psycopg

from .sources import ACS_VARS, SOURCES

DATA = Path(os.environ.get("PHARMA_DATA_DIR", "data"))
DSN = os.environ.get("PHARMA_DSN", "postgresql://pharma:pharma@localhost:5432/pharma")
CHUNK = 250_000


def snake(col: str) -> str:
    c = re.sub(r"[^0-9a-zA-Z]+", "_", col.strip()).strip("_").lower()
    return c[:63]  # Postgres identifier limit


def open_member(path: Path, member_re: str | None):
    """Return a binary file handle: the file itself, or a zip member."""
    if path.suffix.lower() != ".zip":
        return path.open("rb")
    z = zipfile.ZipFile(path)
    names = [n for n in z.namelist() if not n.endswith("/")]
    if member_re:
        names = [n for n in names if re.search(member_re, n.split("/")[-1], re.I)]
        names = [n for n in names if "fileheader" not in n.lower()]
    if not names:
        raise FileNotFoundError(f"{member_re} not in {path}")
    return z.open(sorted(names, key=len)[0])


def load_table(conn, fh, table: str, sep=",", encoding="utf-8", usecols=None):
    reader = pd.read_csv(
        io.TextIOWrapper(fh, encoding=encoding, errors="replace", newline=""),
        sep=sep, dtype=str, keep_default_na=False, chunksize=CHUNK,
        usecols=(lambda c: c in set(usecols)) if usecols else None,
        quotechar='"', low_memory=False,
    )
    total, first = 0, True
    with conn.cursor() as cur:
        for chunk in reader:
            chunk.columns = [snake(c) for c in chunk.columns]
            if first:
                if usecols:
                    missing = {snake(c) for c in usecols} - set(chunk.columns)
                    if missing:
                        print(f"  !! columns not in file (renamed upstream?): {sorted(missing)}")
                cols_sql = ", ".join(f'"{c}" text' for c in chunk.columns)
                cur.execute(f"DROP TABLE IF EXISTS raw.{table}")
                cur.execute(f"CREATE UNLOGGED TABLE raw.{table} ({cols_sql})")
                first = False
            buf = io.StringIO()
            chunk.to_csv(buf, index=False, header=False)
            buf.seek(0)
            cols = ", ".join(f'"{c}"' for c in chunk.columns)
            with cur.copy(f"COPY raw.{table} ({cols}) FROM STDIN WITH (FORMAT csv)") as cp:
                while data := buf.read(1 << 20):
                    cp.write(data)
            total += len(chunk)
            print(f"  raw.{table}: {total:,} rows", flush=True)
    conn.commit()
    return total


def load_acs(conn, path: Path):
    df = pd.read_csv(path, dtype=str)
    df = df.rename(columns=ACS_VARS)
    df["county_fips"] = df["state"] + df["county"]
    m = [c for c in df.columns if c.startswith(("m65_", "f65_"))]
    df["pop_65p"] = df[m].apply(pd.to_numeric, errors="coerce").sum(axis=1).astype("int64").astype(str)
    df = df[["county_fips", "NAME", "pop_total", "pop_65p", "median_hh_income"]]
    buf = io.BytesIO(df.to_csv(index=False).encode())
    return load_table(conn, buf, "acs_county")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    manifest = json.loads((DATA / "raw" / str(args.year) / "manifest.json").read_text())

    with psycopg.connect(DSN) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw")
        for name, meta in manifest.items():
            if name.startswith("_") or (args.only and name not in args.only):
                continue
            path = Path(meta["file"])
            print(f"[{name}] {path}")
            if name == "acs_county":
                load_acs(conn, path)
                continue
            src = SOURCES[name]
            if name == "county_shapes":
                print("  (shapefile: run `python -m pipeline.load_geo` if PostGIS is enabled)")
                continue
            with open_member(path, src.member) as fh:
                load_table(conn, fh, src.raw_table, src.sep, src.encoding, src.usecols)
            for member_re, table in src.extra_tables.items():
                with open_member(path, member_re) as fh:
                    load_table(conn, fh, table, src.sep, src.encoding)


if __name__ == "__main__":
    main()
