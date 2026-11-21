"""
Optional: load Census county polygons into PostGIS (geo.county_shape).

Needs:  CREATE EXTENSION postgis;   and   pip install geopandas geoalchemy2 sqlalchemy
Alternative without Python:  ogr2ogr -f PostgreSQL PG:"$PHARMA_DSN" \
    /vsizip/data/raw/2024/county_shapes.zip -nln geo.county_shape -nlt MULTIPOLYGON -t_srs EPSG:4269
"""
import os
from pathlib import Path

DATA = Path(os.environ.get("PHARMA_DATA_DIR", "data"))
DSN = os.environ.get("PHARMA_DSN", "postgresql://pharma:pharma@localhost:5432/pharma")


def main(year: int = 2024):
    import geopandas as gpd
    from sqlalchemy import create_engine, text

    gdf = gpd.read_file(f"zip://{DATA / 'raw' / str(year) / 'county_shapes.zip'}")
    gdf = gdf.rename(columns={"GEOID": "county_fips", "NAME": "county_name",
                              "STUSPS": "state"})[["county_fips", "county_name", "state", "geometry"]]
    gdf = gdf.to_crs(4269)
    eng = create_engine(DSN.replace("postgresql://", "postgresql+psycopg://"))
    with eng.begin() as c:
        c.execute(text("CREATE SCHEMA IF NOT EXISTS geo"))
    gdf.to_postgis("county_shape", eng, schema="geo", if_exists="replace", index=False)
    with eng.begin() as c:
        c.execute(text("ALTER TABLE geo.county_shape ADD PRIMARY KEY (county_fips)"))
        c.execute(text("CREATE INDEX ON geo.county_shape USING gist (geometry)"))
    print(f"geo.county_shape: {len(gdf)} counties")


if __name__ == "__main__":
    main()
