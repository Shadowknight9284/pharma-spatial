"""
Live check: resolve every source for a year and probe each URL with a
1-byte ranged GET. No downloads. Run before a full `download`.

    python tests/check_sources_live.py --year 2024
Exit code 1 if any source fails to resolve or doesn't answer 200/206.
"""
import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.sources import SOURCES, UA  # noqa: E402


def probe(url: str) -> str:
    r = requests.get(url, headers={**UA, "Range": "bytes=0-0"}, timeout=60, stream=True)
    size = r.headers.get("Content-Range", "").split("/")[-1] or r.headers.get("Content-Length") or "?"
    r.close()
    if r.status_code not in (200, 206):
        raise RuntimeError(f"HTTP {r.status_code}")
    return f"HTTP {r.status_code}, {size} bytes"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    year = ap.parse_args().year
    bad = 0
    for name, s in SOURCES.items():
        try:
            url = s.url(year)
            print(f"ok   {name:16s} {probe(url)}\n     {url}")
        except Exception as e:
            bad += 1
            print(f"FAIL {name:16s} {type(e).__name__}: {e}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
