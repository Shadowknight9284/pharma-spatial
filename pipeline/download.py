"""
Download every source into data/raw/<year>/.

    python -m pipeline.download --year 2024             # everything
    python -m pipeline.download --year 2024 --only nppes op_general

Writes data/raw/<year>/manifest.json with URL, size, sha256 and timestamp
so every load is reproducible.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from pathlib import Path

import requests

from .sources import ACS_VARS, SOURCES, UA

DATA = Path(os.environ.get("PHARMA_DATA_DIR", "data"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path, retries: int = 4) -> Path:
    """Stream to disk with HTTP range resume (these files are multi-GB)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, retries + 1):
        try:
            have = part.stat().st_size if part.exists() else 0
            headers = dict(UA)
            if have:
                headers["Range"] = f"bytes={have}-"
            with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as r:
                if r.status_code == 416:      # already complete
                    break
                r.raise_for_status()
                mode = "ab" if have and r.status_code == 206 else "wb"
                total = int(r.headers.get("Content-Length", 0)) + (have if mode == "ab" else 0)
                done = have if mode == "ab" else 0
                t0 = time.time()
                with part.open(mode) as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total and time.time() - t0 > 5:
                            print(f"  {dest.name}: {done/1e9:.2f}/{total/1e9:.2f} GB", flush=True)
                            t0 = time.time()
            break
        except (requests.ConnectionError, requests.Timeout) as e:
            print(f"  retry {attempt}/{retries} after {e!r}")
            time.sleep(5 * attempt)
    else:
        raise RuntimeError(f"failed: {url}")
    part.rename(dest)
    return dest


def ext_for(url: str) -> str:
    u = url.lower().split("?")[0]
    for e in (".zip", ".csv", ".txt"):
        if u.endswith(e):
            return e
    return ".csv"


def download_acs(year: int, dest: Path) -> Path:
    """County ACS 5-year estimates via the Census API (key optional)."""
    acs_year = min(year, 2023)  # ACS 5-yr lags ~2 years; bump when newer is out
    params = {"get": "NAME," + ",".join(ACS_VARS), "for": "county:*"}
    if os.environ.get("CENSUS_API_KEY"):
        params["key"] = os.environ["CENSUS_API_KEY"]
    r = requests.get(f"https://api.census.gov/data/{acs_year}/acs/acs5",
                     params=params, headers=UA, timeout=120)
    r.raise_for_status()
    rows = r.json()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="") as f:
        csv.writer(f).writerows(rows)
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--only", nargs="*", help="subset of source names")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    outdir = DATA / "raw" / str(args.year)
    manifest_path = outdir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    names = args.only or list(SOURCES) + ["acs_county"]
    for name in names:
        print(f"[{name}]")
        try:
            if name == "acs_county":
                dest = outdir / "acs_county.csv"
                url = "api.census.gov acs5"
                if args.force or not dest.exists():
                    download_acs(args.year, dest)
            else:
                src = SOURCES[name]
                url = src.url(args.year)
                dest = outdir / f"{name}{ext_for(url)}"
                if args.force or not dest.exists():
                    print(f"  GET {url}")
                    fetch(url, dest)
                else:
                    print(f"  exists, skipping ({dest})")
            manifest[name] = {
                "url": url, "file": str(dest), "bytes": dest.stat().st_size,
                "sha256": _sha256(dest), "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            manifest_path.write_text(json.dumps(manifest, indent=2))
        except Exception as e:  # keep going; report at the end
            print(f"  !! {name} failed: {e}")
            manifest.setdefault("_errors", {})[name] = str(e)
            manifest_path.write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
