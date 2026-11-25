"""
Statistical models on the 2024 cube. Run after `python -m pipeline.run sql`.

    pip install -r requirements-analysis.txt
    python -m analysis.models --year 2024

Writes analysis/results/models.md (tables + plain-English reading) and
web/data/models.json (for the website's Models tab).

All three models are cross-sectional associations. Companies target high
prescribers and high-volume areas, so none of the coefficients are causal
effects of promotion. See "Limits" in the generated report.

M1  Prescriber level, within class: is getting a product's promotion
    associated with a higher share of that product in the prescriber's
    class fills, vs same-specialty peers in the same county?
M2  County level, within class: share of voice (product's share of class
    promotion) vs share of market (product's share of class fills).
M3  County level, per class: after accounting for sales, does promotion
    track disease burden, vulnerability or income? Spatial lag model.
"""
from __future__ import annotations

import argparse
import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PHARMA_DSN", "postgresql://pharma:pharma@localhost:5432/pharma")
OUT_MD = ROOT / "analysis" / "results" / "models.md"
OUT_JSON = ROOT / "web" / "data" / "models.json"

M1_PRODUCTS = ["ELIQUIS", "XARELTO", "JARDIANCE", "FARXIGA", "DUPIXENT"]
M3_CLASSES = {  # class -> (PLACES measure used as burden, label)
    "SGLT2": ("DIABETES", "diabetes prevalence"),
    "DOAC": ("CHD", "coronary heart disease prevalence (AF not in PLACES)"),
    "T2_BIOLOGIC": ("CASTHMA", "asthma prevalence"),
}


def sql_df(conn, q, **p) -> pd.DataFrame:
    with conn.cursor() as cur:
        cur.execute(q, p)
        cols = [d.name for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


def num(df, cols):
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


# ---------------------------------------------------------------- M1
def m1(conn, year):
    import pyfixest as pf

    rows = []
    for pid in M1_PRODUCTS:
        df = sql_df(conn, """
            SELECT npi, specialty, county_fips, class_fills, share, pay_usd, rival_pay_usd, n_meals
            FROM mart.npi_class_product
            WHERE year = %(y)s AND product_id = %(p)s AND entity_type = '1'
              AND class_fills >= 12 AND specialty IS NOT NULL AND county_fips IS NOT NULL
        """, y=year, p=pid)
        num(df, ["class_fills", "share", "pay_usd", "rival_pay_usd", "n_meals"])
        df = df[df.groupby("specialty")["npi"].transform("size") >= 50]
        df["paid"] = (df.pay_usd > 0).astype(float)
        df["meal_only"] = ((df.pay_usd > 0) & (df.pay_usd < 100)).astype(float)
        df["log_pay"] = np.log1p(df.pay_usd)
        df["log_rival_pay"] = np.log1p(df.rival_pay_usd)
        df["log_class_fills"] = np.log(df.class_fills)
        df["share_pp"] = 100 * df.share

        fit_a = pf.feols("share_pp ~ paid + log_rival_pay + log_class_fills | specialty + county_fips",
                         data=df, vcov={"CRV1": "county_fips"})
        fit_b = pf.feols("share_pp ~ log_pay + log_rival_pay + log_class_fills | specialty + county_fips",
                         data=df, vcov={"CRV1": "county_fips"})
        ta, tb = fit_a.tidy(), fit_b.tidy()
        rows.append({
            "product": pid, "n_npi": int(len(df)), "pct_paid": float(df.paid.mean()),
            "mean_share_pp": float(df.share_pp.mean()),
            "mean_share_paid": float(df.loc[df.paid == 1, "share_pp"].mean()),
            "mean_share_unpaid": float(df.loc[df.paid == 0, "share_pp"].mean()),
            "paid_pp": float(ta.loc["paid", "Estimate"]),
            "paid_lo": float(ta.loc["paid", "2.5%"]), "paid_hi": float(ta.loc["paid", "97.5%"]),
            "rival_pp_per_log": float(ta.loc["log_rival_pay", "Estimate"]),
            "rival_lo": float(ta.loc["log_rival_pay", "2.5%"]), "rival_hi": float(ta.loc["log_rival_pay", "97.5%"]),
            "logpay_pp": float(tb.loc["log_pay", "Estimate"]),
            "logpay_lo": float(tb.loc["log_pay", "2.5%"]), "logpay_hi": float(tb.loc["log_pay", "97.5%"]),
            "r2_within": float(getattr(fit_a, "_r2_within", np.nan)),
        })
        print(f"  M1 {pid}: n={len(df):,} paid={rows[-1]['paid_pp']:+.2f}pp")
    return rows


# ---------------------------------------------------------------- M2
def m2(conn, year):
    import pyfixest as pf

    df = sql_df(conn, """
        WITH t AS (
          SELECT t.*, sum(pay_usd)   OVER (PARTITION BY year, county_fips, class_id) AS class_pay,
                      sum(fills_30d) OVER (PARTITION BY year, county_fips, class_id) AS class_fills
          FROM mart.targeting_index t WHERE year = %(y)s
        )
        SELECT county_fips, product_id, class_id, class_share, pay_usd, class_pay, class_fills
        FROM t
        WHERE class_pay >= 1000 AND class_fills >= 500
          AND class_id IN ('DOAC', 'SGLT2', 'T2_BIOLOGIC')
    """, y=year)
    num(df, ["class_share", "pay_usd", "class_pay", "class_fills"])
    df["sov"] = df.pay_usd.fillna(0) / df.class_pay
    df["som_pp"], df["sov_pp"] = 100 * df.class_share.fillna(0), 100 * df.sov
    out = []
    for cls, g in df.groupby("class_id"):
        g = g[g.groupby("product_id")["county_fips"].transform("size") >= 30]
        fit = pf.feols("som_pp ~ sov_pp | product_id + county_fips", data=g,
                       vcov={"CRV1": "county_fips"}, weights="class_fills")
        t = fit.tidy()
        out.append({"class": cls, "n": int(len(g)), "n_counties": int(g.county_fips.nunique()),
                    "beta": float(t.loc["sov_pp", "Estimate"]),
                    "lo": float(t.loc["sov_pp", "2.5%"]), "hi": float(t.loc["sov_pp", "97.5%"])})
        print(f"  M2 {cls}: beta={out[-1]['beta']:.3f}")
    return out


# ---------------------------------------------------------------- M3
def m3(conn, year):
    import geopandas as gpd
    import libpysal
    import spreg
    from esda.moran import Moran

    geo = gpd.read_postgis("SELECT county_fips, geometry FROM geo.county_shape", conn, geom_col="geometry")
    out = []
    for cls, (measure, mlabel) in M3_CLASSES.items():
        df = sql_df(conn, """
            SELECT t.county_fips,
                   sum(t.pay_usd) AS pay, sum(t.fills_30d) AS fills,
                   max(c.pop_65p) AS pop65, max(c.pop_total) AS pop_total,
                   max(c.svi_overall) AS svi, max(c.median_hh_income) AS income,
                   max(dp.prevalence_pct) AS prev
            FROM mart.targeting_index t
            JOIN core.county c USING (county_fips)
            LEFT JOIN core.disease_prev dp ON dp.county_fips = t.county_fips AND dp.measure_id = %(m)s
            WHERE t.year = %(y)s AND t.class_id = %(c)s
            GROUP BY 1
        """, y=year, c=cls, m=measure)
        num(df, ["pay", "fills", "pop65", "pop_total", "svi", "income", "prev"])
        df = df[(df.pop65 >= 1000) & (df.fills > 0)].dropna(subset=["svi", "income", "prev"])
        df["y"] = np.log1p(1000 * df.pay.fillna(0) / df.pop65)          # promo $ per 1k aged 65+
        df["log_fills_pc"] = np.log(1000 * df.fills / df.pop65)          # sales per 1k aged 65+
        df["prev_z"] = (df.prev - df.prev.mean()) / df.prev.std()
        df["svi_z"] = (df.svi - df.svi.mean()) / df.svi.std()
        df["log_income"] = np.log(df.income)
        df["log_pop"] = np.log(df.pop_total)
        g = geo.merge(df, on="county_fips").reset_index(drop=True)
        cent = g.geometry.representative_point()
        w = libpysal.weights.KNN.from_dataframe(gpd.GeoDataFrame(geometry=cent), k=6)
        w.transform = "r"
        xs = ["log_fills_pc", "prev_z", "svi_z", "log_income", "log_pop"]
        y, X = g[["y"]].values, g[xs].values
        ols = spreg.OLS(y, X, w=w, name_y="log promo per 1k 65+", name_x=xs, moran=True)
        mi = Moran(ols.u.flatten(), w)
        lag = spreg.GM_Lag(y, X, w=w, name_y="log promo per 1k 65+", name_x=xs)
        names = ["const"] + xs
        coef = {}
        for i, n in enumerate(names):
            coef[n] = {"ols": float(ols.betas[i][0]), "ols_se": float(ols.std_err[i]),
                       "lag": float(lag.betas[i][0]), "lag_se": float(lag.std_err[i])}
        rho = float(lag.betas[-1][0]); rho_se = float(lag.std_err[-1])
        out.append({"class": cls, "burden": mlabel, "n": int(len(g)), "coef": coef,
                    "rho": rho, "rho_se": rho_se, "moran_I": float(mi.I), "moran_p": float(mi.p_sim),
                    "r2_ols": float(ols.r2), "pr2_lag": float(lag.pr2)})
        print(f"  M3 {cls}: n={len(g)} rho={rho:.2f} moran={mi.I:.2f}")
    return out


# ---------------------------------------------------------------- M5
def m5(conn, year):
    """Two-year, within-prescriber change (year-1 -> year). Empty list if year-1 isn't loaded."""
    import pyfixest as pf

    has = sql_df(conn, "SELECT count(*) AS n FROM mart.npi_class_product WHERE year = %(y)s", y=year - 1)
    if int(has.n[0]) == 0:
        return []
    rows = []
    for pid in M1_PRODUCTS:
        df = sql_df(conn, """
            SELECT a.npi, a.specialty, a.county_fips,
                   a.class_fills AS cf1, b.class_fills AS cf0, a.share AS s1, b.share AS s0,
                   a.pay_usd AS p1, b.pay_usd AS p0, a.rival_pay_usd AS r1, b.rival_pay_usd AS r0
            FROM mart.npi_class_product a
            JOIN mart.npi_class_product b ON b.npi = a.npi AND b.product_id = a.product_id AND b.year = %(y0)s
            WHERE a.year = %(y)s AND a.product_id = %(p)s AND a.entity_type = '1'
              AND a.class_fills >= 12 AND b.class_fills >= 12
              AND a.specialty IS NOT NULL AND a.county_fips IS NOT NULL
        """, y=year, y0=year - 1, p=pid)
        num(df, ["cf1", "cf0", "s1", "s0", "p1", "p0", "r1", "r0"])
        df = df[df.groupby("specialty")["npi"].transform("size") >= 50]
        paid0, paid1 = df.p0 > 0, df.p1 > 0
        df["started"] = (~paid0 & paid1).astype(float)
        df["stopped"] = (paid0 & ~paid1).astype(float)
        df["always"] = (paid0 & paid1).astype(float)
        df["d_share_pp"] = 100 * (df.s1 - df.s0)
        df["d_log_rival"] = np.log1p(df.r1) - np.log1p(df.r0)
        df["d_log_class"] = np.log(df.cf1) - np.log(df.cf0)
        fit = pf.feols("d_share_pp ~ started + stopped + always + d_log_rival + d_log_class | specialty + county_fips",
                       data=df, vcov={"CRV1": "county_fips"})
        t = fit.tidy()
        g = lambda k: {"est": float(t.loc[k, "Estimate"]), "lo": float(t.loc[k, "2.5%"]), "hi": float(t.loc[k, "97.5%"])}
        rows.append({"product": pid, "n_npi": int(len(df)),
                     "n_started": int(df.started.sum()), "n_stopped": int(df.stopped.sum()),
                     "n_always": int(df.always.sum()),
                     "mean_d_share": float(df.d_share_pp.mean()),
                     "started": g("started"), "stopped": g("stopped"), "always": g("always"),
                     "rival": g("d_log_rival")})
        print(f"  M5 {pid}: n={len(df):,} started={rows[-1]['started']['est']:+.2f}pp "
              f"stopped={rows[-1]['stopped']['est']:+.2f}pp")
    return rows


# ---------------------------------------------------------------- report
def ci(b, lo, hi, d=2):
    return f"{b:+.{d}f} [{lo:+.{d}f}, {hi:+.{d}f}]"


def report(year, r1, r2, r3, r5=()) -> str:
    L = [f"# Models — {year}", "",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m analysis.models`._", "",
         "All results are **cross-sectional associations**. Companies pay the prescribers and "
         "places that already prescribe the most, so none of these coefficients is a causal effect "
         "of promotion.", "",
         "## M1. Prescriber-level: promotion and product share within class", "",
         "Outcome: the product's share (percentage points) of the prescriber's 30-day fills in its "
         "class (e.g. Eliquis / all DOAC fills). Comparison: same specialty, same county "
         "(specialty and county fixed effects), controlling for the prescriber's class volume and "
         "payments naming rival products. Individual prescribers with >= 12 class fills; 95% CIs "
         "clustered by county.", "",
         "| Product | Prescribers | % paid | Mean share | Paid vs unpaid (raw) | Paid, adjusted (pp) | Per log $ (pp) | Rival log $ (pp) |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in r1:
        L.append(f"| {r['product']} | {r['n_npi']:,} | {100*r['pct_paid']:.1f}% | {r['mean_share_pp']:.1f} | "
                 f"{r['mean_share_paid']:.1f} vs {r['mean_share_unpaid']:.1f} | "
                 f"{ci(r['paid_pp'], r['paid_lo'], r['paid_hi'], 1)} | "
                 f"{ci(r['logpay_pp'], r['logpay_lo'], r['logpay_hi'])} | "
                 f"{ci(r['rival_pp_per_log'], r['rival_lo'], r['rival_hi'])} |")
    L += ["", "**Reading:** _Paid, adjusted_ is how many percentage points higher the product's share "
          "is among prescribers who received any payment naming it than among unpaid peers of the "
          "same specialty in the same county. _Rival log $_ should be negative if a competitor's "
          "promotion goes with a lower share for this product.", "",
          "## M2. County-level: share of voice vs share of market", "",
          "Outcome: product share of class fills in the county (pp). Regressor: product share of "
          "class promotion dollars in the county (pp). Product and county fixed effects, weighted "
          "by class fills; counties with >= $1,000 class promotion and >= 500 class fills.", "",
          "| Class | Product x county cells | Counties | pp share per pp of voice |", "|---|---:|---:|---:|"]
    for r in r2:
        L.append(f"| {r['class']} | {r['n']:,} | {r['n_counties']:,} | {ci(r['beta'], r['lo'], r['hi'], 3)} |")
    L += ["", "**Reading:** a slope of 0.10 means a county where a product gets 10 more points of its "
          "class's promotion has, on average, 1 more point of the class's fills, relative to that "
          "product elsewhere and to other products in that county.", "",
          "## M3. County-level: what does promotion track beyond sales?", "",
          "Outcome: log(1 + class promotion $ per 1,000 residents aged 65+). Regressors: log class "
          "fills per 1,000 aged 65+, disease prevalence (z), SVI (z), log median income, log "
          "population. OLS, then a spatial lag model (GM_Lag; 6-nearest-neighbour weights, "
          "row-standardised). Counties with >= 1,000 residents aged 65+.", ""]
    for r in r3:
        L += [f"### {r['class']} (burden = {r['burden']})", "",
              f"n = {r['n']:,} counties · OLS R² = {r['r2_ols']:.2f} · Moran's I of OLS residuals = "
              f"{r['moran_I']:.2f} (p = {r['moran_p']:.3f}) · spatial ρ = {r['rho']:.2f} (se {r['rho_se']:.2f})", "",
              "| Term | OLS (se) | Spatial lag (se) |", "|---|---:|---:|"]
        for n, c in r["coef"].items():
            L.append(f"| {n} | {c['ols']:+.3f} ({c['ols_se']:.3f}) | {c['lag']:+.3f} ({c['lag_se']:.3f}) |")
        L.append("")
    if r5:
        L += [f"## M5. Two years: change in share when a prescriber starts or stops being paid ({year - 1} → {year})", "",
              "Outcome: change in the product's share of the prescriber's class fills (pp) between the two years. "
              "Groups: started being paid for the product, stopped, paid both years; reference = paid neither year. "
              "Controls: change in log rival payments and log class volume; specialty and county fixed effects "
              "(i.e. specialty- and county-specific trends). Same prescriber in both years, >= 12 class fills each "
              "year. Anything fixed about the prescriber (habits, patient mix, existing loyalty) cancels out.", "",
              "| Product | Prescribers | Started / stopped / both | Started paid (pp) | Stopped (pp) | Paid both years (pp) |",
              "|---|---:|---:|---:|---:|---:|"]
        for r in r5:
            L.append(f"| {r['product']} | {r['n_npi']:,} | {r['n_started']:,} / {r['n_stopped']:,} / {r['n_always']:,} | "
                     f"{ci(r['started']['est'], r['started']['lo'], r['started']['hi'])} | "
                     f"{ci(r['stopped']['est'], r['stopped']['lo'], r['stopped']['hi'])} | "
                     f"{ci(r['always']['est'], r['always']['lo'], r['always']['hi'])} |")
        L += ["", "**Reading:** _Started paid_ is how much more the product's share rose for prescribers who began "
              "receiving payments naming it than for never-paid peers of the same specialty in the same county. "
              "Still not proof of causation: companies may start paying prescribers whose use is already rising.", ""]
    L += ["## Limits", "",
          "* M1-M3 are one-year cross-sections: promotion is targeted at existing high prescribers "
          "(reverse causality). M5 compares the same prescriber across two years, which removes fixed "
          "differences between prescribers but not companies targeting prescribers whose use is already rising.",
          "* Medicare Part D only; CMS suppresses cells under 11 claims; indication is inferred.",
          "* Open Payments excludes rep detailing without a transfer of value, samples and DTC ads.",
          "* M3's burden proxies are imperfect (no AF in PLACES; DOAC uses coronary heart disease).", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    a = ap.parse_args(argv)
    with psycopg.connect(DSN) as conn:
        print("M1 prescriber-level ..."); r1 = m1(conn, a.year)
        print("M2 share of voice ...");   r2 = m2(conn, a.year)
        print("M3 spatial ...");          r3 = m3(conn, a.year)
        print("M5 two-year change ...");  r5 = m5(conn, a.year)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(report(a.year, r1, r2, r3, r5))
    OUT_JSON.write_text(json.dumps({"year": a.year, "m1": r1, "m2": r2, "m3": r3, "m5": r5}, separators=(",", ":")))
    print(f"wrote {OUT_MD.relative_to(ROOT)} and {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
