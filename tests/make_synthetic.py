"""
Generate tiny synthetic files with the REAL column headers of every source,
plus a manifest, so the whole pipeline can be tested without the 20 GB download.

    PHARMA_DATA_DIR=/tmp/pharma_test python tests/make_synthetic.py
    PHARMA_DATA_DIR=/tmp/pharma_test python -m pipeline.run load --year 2024
    PHARMA_DATA_DIR=/tmp/pharma_test python -m pipeline.run sql  --year 2024
"""
import csv
import io
import json
import os
import random
import zipfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.sources import (NPPES_COLS, OP_COLS, PARTB_COLS, PARTD_COLS,
                              PLACES_COLS, ACS_VARS)

random.seed(7)
YEAR = 2024
OUT = Path(os.environ.get("PHARMA_DATA_DIR", "/tmp/pharma_test")) / "raw" / str(YEAR)
OUT.mkdir(parents=True, exist_ok=True)

COUNTIES = [("34023", "08854", "NJ", "Middlesex County"), ("34013", "07102", "NJ", "Essex County"),
            ("36061", "10001", "NY", "New York County"), ("42101", "19103", "PA", "Philadelphia County"),
            ("25025", "02115", "MA", "Suffolk County")]
SPECS = ["Dermatology", "Pulmonary Disease", "Allergy/ Immunology", "Cardiology",
         "Internal Medicine", "Nurse Practitioner", "Gastroenterology", "Nephrology"]
BRANDS = [("Dupixent", "Dupilumab"), ("Adbry", "Tralokinumab-Ldrm"), ("Nucala", "Mepolizumab"),
          ("Eliquis", "Apixaban"), ("Xarelto", "Rivaroxaban"), ("Jardiance", "Empagliflozin"),
          ("Farxiga", "Dapagliflozin"), ("Toujeo Solostar", "Insulin Glargine,Hum.Rec.Anlog"),
          ("Lantus Solostar", "Insulin Glargine,Hum.Rec.Anlog"), ("Tresiba Flextouch U-100", "Insulin Degludec"),
          ("Atorvastatin Calcium", "Atorvastatin Calcium")]
MFR = {"Dupixent": "Sanofi-Aventis U.S.", "Adbry": "Leo Pharma Inc", "Nucala": "Glaxosmithkline",
       "Eliquis": "Bristol-Myers Squibb", "Xarelto": "Janssen Pharm", "Jardiance": "Boehringer Ing",
       "Farxiga": "Astrazeneca", "Toujeo Solostar": "Sanofi-Aventis U.S.",
       "Lantus Solostar": "Sanofi-Aventis U.S.", "Tresiba Flextouch U-100": "Novo Nordisk",
       "Atorvastatin Calcium": "Various"}
NDC = {"Dupixent": "0024-5914", "Eliquis": "0003-0894", "Xarelto": "50458-579"}

npis = [str(1000000000 + i) for i in range(60)]
hcp = {n: (random.choice(COUNTIES), random.choice(SPECS)) for n in npis}


def write_csv(path, header, rows, sep=","):
    with open(path, "w", newline="") as f:
        w = csv.writer(f, delimiter=sep)
        w.writerow(header)
        w.writerows(rows)


manifest = {}
def reg(name, path):
    manifest[name] = {"url": "synthetic", "file": str(path), "bytes": path.stat().st_size,
                      "sha256": "-", "downloaded_at": "-"}

# Part D (with a couple of extra real columns we don't keep, to test usecols)
hdr = PARTD_COLS[:8] + ["Brnd_Name", "Gnrc_Name", "Tot_Clms", "Tot_30day_Fills", "Tot_Day_Suply",
                        "Tot_Drug_Cst", "Tot_Benes", "GE65_Sprsn_Flag", "GE65_Tot_Clms", "GE65_Tot_Benes"]
rows = []
for n in npis:
    (fips, zip5, st, _), spec = hcp[n]
    for b, g in random.sample(BRANDS, 4):
        clms = random.randint(11, 300)
        rows.append([n, "Doe", "Jo", "Town", st, fips[:2], spec, "S", b, g, clms,
                     round(clms * 1.4, 1), clms * 30, round(clms * random.uniform(50, 3000), 2),
                     random.choice(["", str(random.randint(11, 60))]), "", clms, ""])
p = OUT / "partd_prov_drug.csv"; write_csv(p, hdr, rows); reg("partd_prov_drug", p)

# Part B (drug J-codes + a non-drug line)
rows = []
for n in npis[:20]:
    (fips, zip5, st, _), spec = hcp[n]
    for code, desc, drug in [("J2182", "Injection, mepolizumab, 1 mg", "Y"), ("99213", "Office visit", "N")]:
        row = {c: "" for c in PARTB_COLS}
        row.update(Rndrng_NPI=n, Rndrng_Prvdr_Last_Org_Name="Doe", Rndrng_Prvdr_Ent_Cd="I",
                   Rndrng_Prvdr_City="Town", Rndrng_Prvdr_State_Abrvtn=st, Rndrng_Prvdr_State_FIPS=fips[:2],
                   Rndrng_Prvdr_Zip5=zip5, Rndrng_Prvdr_RUCA="1", Rndrng_Prvdr_Type=spec,
                   HCPCS_Cd=code, HCPCS_Desc=desc, HCPCS_Drug_Ind=drug, Place_Of_Srvc="O",
                   Tot_Benes="15", Tot_Srvcs=str(random.randint(100, 900)), Tot_Bene_Day_Srvcs="40",
                   Avg_Sbmtd_Chrg="20", Avg_Mdcr_Alowd_Amt="11.5", Avg_Mdcr_Pymt_Amt="9.2")
        rows.append([row[c] for c in PARTB_COLS])
p = OUT / "partb_prov_svc.csv"; write_csv(p, PARTB_COLS, rows); reg("partb_prov_svc", p)

# Part D spending (wide; Overall row + manufacturer rows)
hdr = ["Brnd_Name", "Gnrc_Name", "Tot_Mftr", "Mftr_Name", "Tot_Spndng_2023", "Tot_Clms_2023"]
rows = []
for b, g in BRANDS:
    rows.append([b, g, 1, "Overall", 1e6, 1000])
    rows.append([b, g, 1, MFR[b], 1e6, 1000])
p = OUT / "partd_spend_drug.csv"; write_csv(p, hdr, rows); reg("partd_spend_drug", p)

# Open Payments general (zip with real-style member name), incl. 2-product payment
rows = []
rid = 0
for n in npis:
    for _ in range(random.randint(0, 4)):
        rid += 1
        row = {c: "" for c in OP_COLS}
        prods = random.sample(["DUPIXENT", "Eliquis", "XARELTO", "Toujeo", "Jardiance", "OddName"], random.choice([1, 1, 2]))
        row.update(Record_ID=str(rid), Program_Year="2024", Change_Type="UNCHANGED",
                   Covered_Recipient_Type="Covered Recipient Physician", Covered_Recipient_NPI=n,
                   Applicable_Manufacturer_or_Applicable_GPO_Making_Payment_Name="Sanofi-Aventis U.S. LLC",
                   Total_Amount_of_Payment_USDollars=f"{random.uniform(10, 2000):.2f}",
                   Date_of_Payment="03/14/2024", Number_of_Payments_Included_in_Total_Amount="1",
                   Nature_of_Payment_or_Transfer_of_Value=random.choice(["Food and Beverage", "Compensation for services other than consulting, including serving as faculty or as a speaker at a venue other than a continuing education program"]),
                   Dispute_Status_for_Publication="No", Related_Product_Indicator="Yes")
        for i, pr in enumerate(prods, 1):
            row[f"Indicate_Drug_or_Biological_or_Device_or_Medical_Supply_{i}"] = "Drug"
            row[f"Name_of_Drug_or_Biological_or_Device_or_Medical_Supply_{i}"] = pr
            row[f"Associated_Drug_or_Biological_NDC_{i}"] = NDC.get(pr.title(), "") if pr != "OddName" else "0024-5914"
        rows.append([row[c] for c in OP_COLS])
buf = io.StringIO(); w = csv.writer(buf); w.writerow(OP_COLS); w.writerows(rows)
p = OUT / "op_general.zip"
with zipfile.ZipFile(p, "w") as z:
    z.writestr("OP_DTL_GNRL_PGYR2024_P06302025_01212026.csv", buf.getvalue())
    z.writestr("OP_PGYR2024_README_P06302025.txt", "readme")
reg("op_general", p)

# NPPES (zip incl. fileheader decoy)
rows = []
for n in npis:
    (fips, zip5, st, _), spec = hcp[n]
    r = {c: "" for c in NPPES_COLS}
    r.update({"NPI": n, "Entity Type Code": "1", "Provider Last Name (Legal Name)": "DOE",
              "Provider First Name": "JO", "Provider Business Practice Location Address City Name": "TOWN",
              "Provider Business Practice Location Address State Name": st,
              "Provider Business Practice Location Address Postal Code": zip5 + "1234",
              "Healthcare Provider Taxonomy Code_1": "207N00000X",
              "Healthcare Provider Primary Taxonomy Switch_1": "Y"})
    rows.append([r[c] for c in NPPES_COLS])
buf = io.StringIO(); w = csv.writer(buf); w.writerow(NPPES_COLS); w.writerows(rows)
p = OUT / "nppes.zip"
with zipfile.ZipFile(p, "w") as z:
    z.writestr("npidata_pfile_20050523-20261109.csv", buf.getvalue())
    z.writestr("npidata_pfile_20050523-20261109_fileheader.csv", ",".join(NPPES_COLS))
reg("nppes", p)

# FDA NDC (tab, latin-1, product + package)
phdr = ["PRODUCTID", "PRODUCTNDC", "PRODUCTTYPENAME", "PROPRIETARYNAME", "PROPRIETARYNAMESUFFIX",
        "NONPROPRIETARYNAME", "DOSAGEFORMNAME", "ROUTENAME", "STARTMARKETINGDATE", "ENDMARKETINGDATE",
        "MARKETINGCATEGORYNAME", "APPLICATIONNUMBER", "LABELERNAME", "SUBSTANCENAME",
        "ACTIVE_NUMERATOR_STRENGTH", "ACTIVE_INGRED_UNIT", "PHARM_CLASSES", "DEASCHEDULE",
        "NDC_EXCLUDE_FLAG", "LISTING_RECORD_CERTIFIED_THROUGH"]
prow = [["1", "0024-5914", "HUMAN PRESCRIPTION DRUG", "Dupixent", "", "dupilumab", "INJECTION", "SUBCUTANEOUS",
         "20170328", "", "BLA", "BLA761055", "sanofi-aventis U.S. LLC", "DUPILUMAB", "300", "mg/2mL",
         "Interleukin-4 Receptor alpha Antagonist [EPC], Interleukin-4 Receptor alpha Antagonists [MoA]", "", "N", "20271231"],
        ["2", "0003-0894", "HUMAN PRESCRIPTION DRUG", "Eliquis", "", "apixaban", "TABLET", "ORAL",
         "20121228", "", "NDA", "NDA202155", "E.R. Squibb & Sons, L.L.C.", "APIXABAN", "5", "mg/1",
         "Factor Xa Inhibitor [EPC], Factor Xa Inhibitors [MoA]", "", "N", "20271231"]]
buf_p = io.StringIO(); csv.writer(buf_p, delimiter="\t").writerows([phdr] + prow)
buf_k = io.StringIO(); csv.writer(buf_k, delimiter="\t").writerows(
    [["PRODUCTID", "PRODUCTNDC", "NDCPACKAGECODE", "PACKAGEDESCRIPTION", "STARTMARKETINGDATE",
      "ENDMARKETINGDATE", "NDC_EXCLUDE_FLAG", "SAMPLE_PACKAGE"],
     ["1", "0024-5914", "0024-5914-00", "2 SYRINGE in 1 CARTON (é)", "20170328", "", "N", "N"]])
p = OUT / "fda_ndc.zip"
with zipfile.ZipFile(p, "w") as z:
    z.writestr("product.txt", buf_p.getvalue().encode("latin-1"))
    z.writestr("package.txt", buf_k.getvalue().encode("latin-1"))
reg("fda_ndc", p)

# PLACES (long)
rows = []
for fips, _, st, name in COUNTIES:
    for mid, meas in [("CASTHMA", "Current asthma"), ("COPD", "COPD"), ("DIABETES", "Diabetes"),
                      ("KIDNEY", "CKD"), ("CHD", "CHD")]:
        for dv in ["CrdPrv", "AgeAdjPrv"]:
            rows.append(["2023", st, name, fips, "BRFSS", "Health Outcomes", meas, mid,
                         "Crude prevalence", dv, f"{random.uniform(3, 15):.1f}", "1", "20", "500000"])
p = OUT / "places_county.csv"; write_csv(p, PLACES_COLS, rows); reg("places_county", p)

# ZCTA->county (pipe) incl. a ZIP split across two counties
hdr = ["OID_ZCTA5_20", "GEOID_ZCTA5_20", "NAMELSAD_ZCTA5_20", "GEOID_COUNTY_20", "NAMELSAD_COUNTY_20", "AREALAND_PART"]
rows = [["1", z, "ZCTA5 " + z, f, n, "9000000"] for f, z, _, n in COUNTIES]
rows.append(["1", "08854", "ZCTA5 08854", "34035", "Somerset County", "100"])
p = OUT / "zcta_county.txt"; write_csv(p, hdr, rows, sep="|"); reg("zcta_county", p)

# ZCTA->town and the CT old-county -> planning-region crosswalk (one Hartford row)
hdr = ["GEOID_ZCTA5_20", "GEOID_COUSUB_20", "AREALAND_PART"]
p = OUT / "zcta_cousub.txt"; write_csv(p, hdr, [["06103", "0900337070", "5000000"]], sep="|"); reg("zcta_cousub", p)
hdr = ["STATEFP\n(INCITS38)", "OLD_COUNTYFP\n(INCITS31)", "NEW_COUNTYFP\n(INCITS31)", "OLD_COUSUB_GEOID", "NEW_COUSUB_GEOID"]
p = OUT / "ct_cousub_xwalk.txt"
write_csv(p, hdr, [["09", "003", "110", "0900337070", "0911037070"]], sep="|"); reg("ct_cousub_xwalk", p)

# SVI
hdr = ["ST", "STATE", "COUNTY", "FIPS", "E_TOTPOP", "RPL_THEME1", "RPL_THEME2", "RPL_THEME3", "RPL_THEME4", "RPL_THEMES"]
rows = [[f[:2], "X", n, f, "500000", *[f"{random.random():.4f}" for _ in range(4)], f"{random.random():.4f}"]
        for f, _, _, n in COUNTIES]
rows[0][-1] = "-999"
p = OUT / "svi_county.csv"; write_csv(p, hdr, rows); reg("svi_county", p)

# ACS (Census API JSON-as-CSV shape)
hdr = ["NAME"] + list(ACS_VARS) + ["state", "county"]
rows = [[n] + [str(random.randint(1000, 90000)) for _ in ACS_VARS] + [f[:2], f[2:]] for f, _, _, n in COUNTIES]
p = OUT / "acs_county.csv"; write_csv(p, hdr, rows); reg("acs_county", p)

(OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
print("synthetic data in", OUT)
