"""
Source registry: where each dataset lives, how to find the right file,
and which columns land in the raw schema.

Discovery uses each publisher's catalog API instead of hard-coded file
names, because CMS/FDA rename files every release.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import requests

UA = {"User-Agent": "pharma-spatial-pipeline/0.1 (research; contact via GitHub)"}


# --------------------------------------------------------------------------
# Discovery helpers
# --------------------------------------------------------------------------
_cms_catalog_cache: dict | None = None


def cms_catalog() -> dict:
    """data.cms.gov publishes a DCAT catalog listing every dataset + yearly CSV."""
    global _cms_catalog_cache
    if _cms_catalog_cache is None:
        r = requests.get("https://data.cms.gov/data.json", headers=UA, timeout=120)
        r.raise_for_status()
        _cms_catalog_cache = r.json()
    return _cms_catalog_cache


def _cms_versions(dataset_title: str) -> list[tuple[str, str]]:
    """All (date, csv_url) releases of a data.cms.gov dataset, newest first.

    The DCAT catalog lists each yearly release as its own dataset entry titled
    '<name> : <YYYY-MM-DD>' (e.g. 'Medicare Part D Prescribers - by Provider
    and Drug : 2024-12-01'), with a CSV distribution plus API endpoints.
    Older catalogs used one entry named exactly '<name>'; both are handled.
    """
    want = dataset_title.strip().lower()
    out = []
    for ds in cms_catalog()["dataset"]:
        title = ds.get("title", "").strip()
        base, _, date = title.partition(" : ")
        if base.strip().lower() != want:
            continue
        for d in ds.get("distribution", []):
            if d.get("mediaType") == "text/csv" and d.get("downloadURL"):
                dt = date or (re.findall(r"20\d\d-\d\d-\d\d", d.get("title", "")) or [""])[0]
                out.append((dt, d["downloadURL"]))
    if not out:
        raise LookupError(f"Dataset not found in data.cms.gov catalog: {dataset_title}")
    return sorted(out, reverse=True)


def cms_csv_url(dataset_title: str, year: int) -> str:
    """CSV for the release whose date falls in `year` (data year, e.g. 2024-12-01)."""
    versions = _cms_versions(dataset_title)
    for dt, url in versions:
        if dt.startswith(str(year)):
            return url
    raise LookupError(f"{dataset_title}: no CSV for {year}. "
                      f"Available: {sorted({dt[:4] for dt, _ in versions})}")


def cms_latest_csv_url(dataset_title: str) -> str:
    """Newest release (e.g. Part D Spending by Drug, one wide file covering 5 years)."""
    return _cms_versions(dataset_title)[0][1]


def open_payments_general_url(year: int) -> str:
    """openpaymentsdata.cms.gov is a DKAN site; its metastore lists every file."""
    url = "https://openpaymentsdata.cms.gov/api/1/metastore/schemas/dataset/items"
    r = requests.get(url, params={"show-reference-ids": "false"}, headers=UA, timeout=120)
    r.raise_for_status()
    for ds in r.json():
        title = ds.get("title", "")
        if title.strip().lower() == f"{year} general payment data":
            for d in ds.get("distribution", []):
                u = d.get("downloadURL") or (d.get("data") or {}).get("downloadURL")
                if u and u.lower().endswith((".csv", ".zip")):
                    return u
    raise LookupError(f"Open Payments general payments for {year} not found; "
                      "check https://openpaymentsdata.cms.gov/datasets")


def nppes_full_url() -> str:
    """Scrape the NPPES page for the current monthly full replacement file."""
    page = "https://download.cms.gov/nppes/NPI_Files.html"
    html = requests.get(page, headers=UA, timeout=60).text
    # Full monthly file: NPPES_Data_Dissemination_<Month>_<YYYY>[_V2].zip (href quoted ' or ")
    links = re.findall(r'''href=["']([^"']*NPPES_Data_Dissemination_[A-Za-z]+_\d{4}[^"']*\.zip)["']''', html)
    links = [l for l in links if "weekly" not in l.lower() and "deactivat" not in l.lower()]
    if not links:
        raise LookupError("No NPPES full file link found on " + page)
    link = links[0]
    return link if link.startswith("http") else "https://download.cms.gov/nppes/" + link.lstrip("./")


def cdc_places_county_url() -> str:
    """Find the newest 'PLACES: Local Data for Better Health, County Data' release."""
    r = requests.get(
        "https://api.us.socrata.com/api/catalog/v1",
        params={"domains": "data.cdc.gov", "q": "PLACES County Data", "limit": 50},
        headers=UA, timeout=60,
    )
    r.raise_for_status()
    best = None
    for res in r.json()["results"]:
        name = res["resource"]["name"]
        # 2020–2024: 'County Data 2024 release'; 2025+: 'County Data, 2025 release'.
        # The 'GIS Friendly Format' twins are wide, not long; skip them.
        m = re.match(r"PLACES: Local Data for Better Health, County Data,? (\d{4}) release", name)
        if m and (best is None or int(m.group(1)) > best[0]):
            best = (int(m.group(1)), res["resource"]["id"])
    if not best:
        raise LookupError("PLACES county dataset not found on data.cdc.gov")
    return f"https://data.cdc.gov/api/views/{best[1]}/rows.csv?accessType=DOWNLOAD"


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
@dataclass
class Source:
    name: str                      # short key, also file stem
    raw_table: str                 # raw.<table>
    url: Callable[[int], str]      # year -> URL
    usecols: list[str] | None = None   # None = keep every column
    sep: str = ","
    encoding: str = "utf-8"
    member: str | None = None      # file (regex) inside a zip
    extra_tables: dict = field(default_factory=dict)  # second member -> table
    big: bool = False
    note: str = ""


PARTD_COLS = [
    "Prscrbr_NPI", "Prscrbr_Last_Org_Name", "Prscrbr_First_Name", "Prscrbr_City",
    "Prscrbr_State_Abrvtn", "Prscrbr_State_FIPS", "Prscrbr_Type", "Prscrbr_Type_Src",
    "Brnd_Name", "Gnrc_Name", "Tot_Clms", "Tot_30day_Fills", "Tot_Day_Suply",
    "Tot_Drug_Cst", "Tot_Benes", "GE65_Tot_Clms", "GE65_Tot_Benes",
]

PARTB_COLS = [
    "Rndrng_NPI", "Rndrng_Prvdr_Last_Org_Name", "Rndrng_Prvdr_Ent_Cd",
    "Rndrng_Prvdr_City", "Rndrng_Prvdr_State_Abrvtn", "Rndrng_Prvdr_State_FIPS",
    "Rndrng_Prvdr_Zip5", "Rndrng_Prvdr_RUCA", "Rndrng_Prvdr_Type",
    "HCPCS_Cd", "HCPCS_Desc", "HCPCS_Drug_Ind", "Place_Of_Srvc",
    "Tot_Benes", "Tot_Srvcs", "Tot_Bene_Day_Srvcs",
    "Avg_Sbmtd_Chrg", "Avg_Mdcr_Alowd_Amt", "Avg_Mdcr_Pymt_Amt",
]

OP_COLS = [
    "Record_ID", "Program_Year", "Change_Type", "Covered_Recipient_Type",
    "Covered_Recipient_NPI", "Covered_Recipient_Profile_ID",
    "Covered_Recipient_Primary_Type_1", "Covered_Recipient_Specialty_1",
    "Recipient_City", "Recipient_State", "Recipient_Zip_Code",
    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_ID",
    "Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name",
    "Total_Amount_of_Payment_USDollars", "Date_of_Payment",
    "Number_of_Payments_Included_in_Total_Amount",
    "Form_of_Payment_or_Transfer_of_Value", "Nature_of_Payment_or_Transfer_of_Value",
    "Dispute_Status_for_Publication", "Related_Product_Indicator",
] + [
    f"{c}_{i}"
    for i in range(1, 6)
    for c in (
        "Covered_or_Noncovered_Indicator",
        "Indicate_Drug_or_Biological_or_Device_or_Medical_Supply",
        "Product_Category_or_Therapeutic_Area",
        "Name_of_Drug_or_Biological_or_Device_or_Medical_Supply",
        "Associated_Drug_or_Biological_NDC",
    )
]

NPPES_COLS = [
    "NPI", "Entity Type Code",
    "Provider Organization Name (Legal Business Name)",
    "Provider Last Name (Legal Name)", "Provider First Name",
    "Provider Business Practice Location Address City Name",
    "Provider Business Practice Location Address State Name",
    "Provider Business Practice Location Address Postal Code",
    "NPI Deactivation Date",
] + [f"Healthcare Provider Taxonomy Code_{i}" for i in range(1, 4)] + [
    f"Healthcare Provider Primary Taxonomy Switch_{i}" for i in range(1, 4)
]

PLACES_COLS = ["Year", "StateAbbr", "LocationName", "LocationID", "DataSource",
               "Category", "Measure", "MeasureId", "Data_Value_Type", "DataValueTypeID",
               "Data_Value", "Low_Confidence_Limit", "High_Confidence_Limit",
               "TotalPopulation"]

SOURCES: dict[str, Source] = {s.name: s for s in [
    Source(
        "partd_prov_drug", "partd_prov_drug",
        lambda y: cms_csv_url("Medicare Part D Prescribers - by Provider and Drug", y),
        usecols=PARTD_COLS, big=True,
        note="~25M rows/yr. No ZIP in this file: location comes from NPPES.",
    ),
    Source(
        "partb_prov_svc", "partb_prov_svc",
        lambda y: cms_csv_url("Medicare Physician & Other Practitioners - by Provider and Service", y),
        usecols=PARTB_COLS, big=True,
        note="Physician-administered drugs = HCPCS J-codes (HCPCS_Drug_Ind = 'Y').",
    ),
    Source(
        "partd_spend_drug", "partd_spend_drug",
        lambda y: cms_latest_csv_url("Medicare Part D Spending by Drug"),
        usecols=None,
        note="Wide by year; Mftr_Name gives a free brand->manufacturer map.",
    ),
    Source(
        "op_general", "op_general",
        open_payments_general_url,
        usecols=OP_COLS, big=True, member=r"OP_DTL_GNRL_PGYR\d{4}.*\.csv",
        note="~15M rows/yr, several GB. Up to 5 products per payment.",
    ),
    Source(
        "nppes", "nppes",
        lambda y: nppes_full_url(),
        usecols=NPPES_COLS, big=True, member=r"npidata_pfile_\d+-\d+\.csv",
        note="~9 GB unzipped, 330 columns; we keep 15.",
    ),
    Source(
        "fda_ndc", "fda_ndc_product",
        lambda y: "https://www.accessdata.fda.gov/cder/ndctext.zip",
        sep="\t", encoding="latin-1", member=r"product\.txt",
        extra_tables={r"package\.txt": "fda_ndc_package"},
    ),
    Source(
        "places_county", "places_county",
        lambda y: cdc_places_county_url(),
        usecols=PLACES_COLS,
        note="Long format: one row per county x measure.",
    ),
    Source(
        "zcta_county", "zcta_county",
        lambda y: "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt",
        sep="|",
        usecols=["GEOID_ZCTA5_20", "GEOID_COUNTY_20", "NAMELSAD_COUNTY_20", "AREALAND_PART"],
        note="ZCTA->county; we assign each ZIP to the county holding most of its land.",
    ),
    Source(
        "zcta_cousub", "zcta_cousub",
        lambda y: "https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_cousub20_natl.txt",
        sep="|", encoding="utf-8-sig",
        usecols=["GEOID_ZCTA5_20", "GEOID_COUSUB_20", "AREALAND_PART"],
        note="ZCTA->town; only used for Connecticut (see ct_cousub_xwalk).",
    ),
    Source(
        "ct_cousub_xwalk", "ct_cousub_xwalk",
        lambda y: "https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.txt",
        sep="|", encoding="utf-8-sig",
        note="CT replaced its 8 counties with 9 planning regions in 2022. 2020 ZIP->county "
             "files use the old counties; ACS/SVI/TIGER use the new ones. Town -> region.",
    ),
    Source(
        "svi_county", "svi_county",
        lambda y: "https://svi.cdc.gov/Documents/Data/2022/csv/states_counties/SVI_2022_US_county.csv",
        usecols=["FIPS", "STATE", "COUNTY", "E_TOTPOP", "RPL_THEMES",
                 "RPL_THEME1", "RPL_THEME2", "RPL_THEME3", "RPL_THEME4"],
        note="If 404: download manually from https://www.atsdr.cdc.gov/place-health/php/svi/svi-data-documentation-download.html",
    ),
    Source(
        "county_shapes", "county_shapes",
        lambda y: "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_500k.zip",
        note="Shapefile; loaded into PostGIS by load_geo.py (optional).",
    ),
]}

# ACS is pulled through the Census API (JSON), handled in download.py.
ACS_VARS = {
    "B01003_001E": "pop_total",
    "B19013_001E": "median_hh_income",
    # 65+ = male 020..025 + female 044..049
    **{f"B01001_{i:03d}E": f"m65_{i}" for i in range(20, 26)},
    **{f"B01001_{i:03d}E": f"f65_{i}" for i in range(44, 50)},
}
