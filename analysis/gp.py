"""
M4. Gaussian-process model of county promotion: how much is location, how much is covariates?

    python -m analysis.gp --year 2024            # needs torch + gpytorch (requirements-analysis.txt)

For each drug class, county-level:

    y(county) = c + f_space(location) + f_cov(covariates) + noise
    f_space = f_local + f_regional, each GP(0, s^2 * Matern-3/2(distance / l)) on projected km (EPSG:5070),
              initialised at ~50 km and ~500 km so one can pick up metro/territory structure, the other regions
    f_cov   ~ GP(0, s_c^2 * RBF with one lengthscale per covariate (ARD))

y is the same outcome as M3: log(1 + class promotion $ per 1,000 residents 65+). Covariates are
standardised: log fills per 1k 65+, disease prevalence, SVI, log median income, log population.

Hyperparameters are fit by maximising the exact marginal likelihood. Reported:
  * spatial ranges: distance at which each spatial part's correlation falls to 0.05 (about 2.74 * l for Matern-3/2)
  * relevance of each covariate (1 / lengthscale; larger = outcome changes faster along it)
  * share of y's variance carried by each component's posterior mean
  * effect curves: f_cov along one covariate, others held at their mean, with 90% bands
  * 5-fold out-of-sample R^2 for linear OLS, GP(covariates), GP(space), GP(space + covariates),
    and Moran's I of the out-of-fold errors (spatial structure left over)

Writes analysis/results/gp.md and web/data/gp.json.
"""
from __future__ import annotations

import argparse
import json
import math
import warnings
from datetime import datetime, timezone

import numpy as np
import psycopg

from analysis.models import DSN, M3_CLASSES, ROOT, num, sql_df

warnings.filterwarnings("ignore")

OUT_MD = ROOT / "analysis" / "results" / "gp.md"
OUT_JSON = ROOT / "web" / "data" / "gp.json"
COVS = ["log_fills_pc", "prev", "svi", "log_income", "log_pop"]
COV_LABEL = {"log_fills_pc": "Sales (log fills per 1k 65+)", "prev": "Disease prevalence (%)",
             "svi": "Social vulnerability (SVI)", "log_income": "Median income (log $)",
             "log_pop": "Population (log)"}
KM = 100.0          # coordinates in units of 100 km
SEED = 7


# ------------------------------------------------------------------ data
def load(conn, year, cls, measure):
    df = sql_df(conn, """
        WITH c AS (
          SELECT t.county_fips,
                 sum(t.pay_usd) AS pay, sum(t.fills_30d) AS fills
          FROM mart.targeting_index t
          WHERE t.year = %(y)s AND t.class_id = %(c)s
          GROUP BY 1)
        SELECT c.*, k.pop_65p AS pop65, k.pop_total, k.svi_overall AS svi,
               k.median_hh_income AS income, dp.prevalence_pct AS prev,
               ST_X(p) / 1000 AS x_km, ST_Y(p) / 1000 AS y_km
        FROM c
        JOIN core.county k USING (county_fips)
        JOIN geo.county_shape s USING (county_fips)
        LEFT JOIN core.disease_prev dp ON dp.county_fips = c.county_fips AND dp.measure_id = %(m)s
        CROSS JOIN LATERAL (SELECT ST_Transform(ST_PointOnSurface(s.geometry), 5070) AS p) pt
    """, y=year, c=cls, m=measure)
    num(df, ["pay", "fills", "pop65", "pop_total", "svi", "income", "prev", "x_km", "y_km"])
    df = df[(df.pop65 >= 1000) & (df.fills > 0)].dropna(subset=["svi", "income", "prev"])
    # contiguous US only: Alaska/Hawaii distances in a CONUS projection are not meaningful
    df = df[~df.county_fips.str[:2].isin(["02", "15", "72"])].reset_index(drop=True)
    df["y"] = np.log1p(1000 * df.pay.fillna(0) / df.pop65)
    df["log_fills_pc"] = np.log(1000 * df.fills / df.pop65)
    df["log_income"] = np.log(df.income)
    df["log_pop"] = np.log(df.pop_total)
    return df


# ------------------------------------------------------------------ GP
def _torch():
    import gpytorch
    import torch
    torch.set_default_dtype(torch.float64)
    return torch, gpytorch


def make_model(X, y, parts):
    """parts: subset of {'space', 'cov'}; X columns = [x, y, covariates...]."""
    torch, gpytorch = _torch()
    from gpytorch.kernels import MaternKernel, RBFKernel, ScaleKernel

    class GP(gpytorch.models.ExactGP):
        def __init__(self):
            lik = gpytorch.likelihoods.GaussianLikelihood()
            super().__init__(X, y, lik)
            self.mean_module = gpytorch.means.ConstantMean()
            ks = {}
            if "space" in parts:                          # two scales: local (~50 km) and regional (~500 km)
                for name, l0 in (("space_local", 0.5), ("space_regional", 5.0)):
                    k = MaternKernel(nu=1.5, active_dims=[0, 1])
                    k.lengthscale = l0
                    ks[name] = ScaleKernel(k)
            if "cov" in parts:
                d = X.shape[1] - 2
                k = RBFKernel(ard_num_dims=d, active_dims=list(range(2, 2 + d)))
                k.lengthscale = torch.full((1, d), 2.0)
                ks["cov"] = ScaleKernel(k)
            self.parts = torch.nn.ModuleDict(ks)

        def forward(self, x):
            k = None
            for m in self.parts.values():
                k = m(x) if k is None else k + m(x)
            return gpytorch.distributions.MultivariateNormal(self.mean_module(x), k)

    return GP()


def fit(X, y, parts, iters=150, lr=0.08, init=None):
    torch, gpytorch = _torch()
    torch.manual_seed(SEED)
    m = make_model(X, y, parts)
    if init is not None:                      # warm start from a fitted model's hyperparameters
        own = m.state_dict()
        m.load_state_dict({k: v for k, v in init.items() if k in own and own[k].shape == v.shape}, strict=False)
    m.train()
    opt = torch.optim.Adam(m.parameters(), lr=lr)
    mll = gpytorch.mlls.ExactMarginalLogLikelihood(m.likelihood, m)
    with gpytorch.settings.max_cholesky_size(100_000):
        for _ in range(iters):
            opt.zero_grad()
            loss = -mll(m(X), y)
            loss.backward()
            opt.step()
    m.eval()
    return m, float(loss)


def predict(m, Xs):
    torch, gpytorch = _torch()
    with torch.no_grad(), gpytorch.settings.max_cholesky_size(100_000), gpytorch.settings.fast_pred_var(False):
        return m.likelihood(m(Xs)).mean.numpy()


def components(m, X, y, grids):
    """Posterior mean of each additive part at the training points, and f_cov (mean, sd) on grids."""
    torch, _ = _torch()
    with torch.no_grad():
        K = sum(p(X).to_dense() for p in m.parts.values())
        K = K + m.likelihood.noise * torch.eye(len(X))
        L = torch.linalg.cholesky(K)
        alpha = torch.cholesky_solve((y - m.mean_module.constant).unsqueeze(-1), L)
        at_train = {name: (p(X).to_dense() @ alpha).squeeze(-1).numpy() for name, p in m.parts.items()}
        curves = {}
        kc = m.parts["cov"]
        for j, G in grids.items():
            Kgx = kc(G, X).to_dense()
            mu = (Kgx @ alpha).squeeze(-1)
            v = torch.cholesky_solve(Kgx.T, L)
            var = kc(G).to_dense().diagonal() - (Kgx * v.T).sum(-1)
            curves[j] = (mu.numpy(), var.clamp_min(0).sqrt().numpy())
    return at_train, curves


# ------------------------------------------------------------------ helpers
def moran(resid, xy, k=6):
    """Moran's I with row-standardised k-nearest-neighbour weights (permutation p, 199 draws)."""
    from scipy.spatial import cKDTree
    _, idx = cKDTree(xy).query(xy, k=k + 1)
    nb = idx[:, 1:]
    z = resid - resid.mean()
    I = lambda v: (v * v[nb].mean(1)).sum() / (v * v).sum()
    obs = I(z)
    rng = np.random.default_rng(SEED)
    sims = np.array([I(rng.permutation(z)) for _ in range(199)])
    return float(obs), float((1 + (sims >= obs).sum()) / 200)


def r2(y, p):
    return float(1 - ((y - p) ** 2).sum() / ((y - y.mean()) ** 2).sum())


def cv(Xn, yn, xy, init, folds=5):
    torch, _ = _torch()
    rng = np.random.default_rng(SEED)
    fold = rng.permutation(len(yn)) % folds
    preds = {k: np.zeros(len(yn)) for k in ["linear", "gp_cov", "gp_space", "gp_both"]}
    variants = {"gp_cov": {"cov"}, "gp_space": {"space"}, "gp_both": {"space", "cov"}}
    for f in range(folds):
        tr, te = fold != f, fold == f
        A = np.column_stack([np.ones(tr.sum()), Xn[tr, 2:]])
        beta = np.linalg.lstsq(A, yn[tr], rcond=None)[0]
        preds["linear"][te] = np.column_stack([np.ones(te.sum()), Xn[te, 2:]]) @ beta
        Xt, yt, Xe = (torch.tensor(Xn[tr]), torch.tensor(yn[tr]), torch.tensor(Xn[te]))
        for name, parts in variants.items():
            m, _ = fit(Xt, yt, parts, iters=40, lr=0.05, init=init)
            preds[name][te] = predict(m, Xe)
    out = {}
    for k, p in preds.items():
        I, pI = moran(yn - p, xy)
        out[k] = {"r2": r2(yn, p), "moran_I": I, "moran_p": pI}
    return out


# ------------------------------------------------------------------ per class
def run_class(conn, year, cls, measure, mlabel):
    torch, _ = _torch()
    df = load(conn, year, cls, measure)
    mu, sd = df[COVS].mean(), df[COVS].std()
    Z = ((df[COVS] - mu) / sd).values
    xy = df[["x_km", "y_km"]].values
    Xn = np.column_stack([xy / KM, Z])
    ym, ys = df.y.mean(), df.y.std()
    yn = ((df.y - ym) / ys).values
    X, y = torch.tensor(Xn), torch.tensor(yn)

    m, nll = fit(X, y, {"space", "cov"}, iters=200)
    sp = sorted(["space_local", "space_regional"], key=lambda n: float(m.parts[n].base_kernel.lengthscale))
    ls = {n: float(m.parts[k].base_kernel.lengthscale) * KM for n, k in zip(["local", "regional"], sp)}
    scale = {n: float(m.parts[k].outputscale) for n, k in zip(["local", "regional"], sp)}
    ls_cov = m.parts["cov"].base_kernel.lengthscale.detach().numpy().ravel()
    noise = float(m.likelihood.noise)

    grids, gx = {}, {}
    for j, c in enumerate(COVS):
        lo, hi = np.quantile(Z[:, j], [0.02, 0.98])
        g = np.linspace(lo, hi, 40)
        G = np.zeros((40, Xn.shape[1]))
        G[:, 2 + j] = g
        grids[c], gx[c] = torch.tensor(G), g * sd[c] + mu[c]
    at_train, curves = components(m, X, y, grids)

    loc, reg = at_train[sp[0]], at_train[sp[1]]
    space = loc + reg
    v = np.var(yn)
    shares = {"local": float(np.var(loc) / v), "regional": float(np.var(reg) / v),
              "space": float(np.var(space) / v), "covariates": float(np.var(at_train["cov"]) / v),
              "residual": float(np.var(yn - space - at_train["cov"]) / v)}
    rng_km = {n: 2.74 * l for n, l in ls.items()}
    print(f"  GP {cls}: n={len(df)} range local={rng_km['local']:.0f} km regional={rng_km['regional']:.0f} km "
          f"shares=" + ", ".join(f"{k}={x:.2f}" for k, x in shares.items()))
    cvres = cv(Xn, yn, xy, m.state_dict())
    print(f"     CV R2: " + ", ".join(f"{k}={r['r2']:.2f}" for k, r in cvres.items()))

    disp = lambda c, x: (np.exp(x) if c in ("log_income", "log_pop", "log_fills_pc") else x)
    return {
        "class": cls, "burden": mlabel, "n": int(len(df)),
        "y_mean": float(ym), "y_sd": float(ys),
        "range_km": rng_km, "lengthscale_km": ls,
        "outputscale": {**scale, "covariates": float(m.parts["cov"].outputscale), "noise": noise},
        "relevance": {c: float(1 / l) for c, l in zip(COVS, ls_cov)},
        "shares": shares,
        "cv": cvres,
        "curves": {c: {"x": [float(disp(c, x)) for x in gx[c]],
                       "mean": [float(a * ys) for a in curves[c][0]],
                       "sd": [float(s * ys) for s in curves[c][1]]} for c in COVS},
        # spatial effect on the outcome scale (log points), per county, for the map
        "space_effect": {f: round(float(e * ys), 3) for f, e in zip(df.county_fips, space)},
        "local_effect": {f: round(float(e * ys), 3) for f, e in zip(df.county_fips, loc)},
        "cov_effect": {f: round(float(e * ys), 3) for f, e in zip(df.county_fips, at_train["cov"])},
    }


# ------------------------------------------------------------------ report
def report(year, res):
    L = [f"# M4. Gaussian-process model — {year}", "",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by `python -m analysis.gp`._", "",
         "Outcome: log(1 + class promotion $ per 1,000 residents aged 65+), contiguous-US counties with >= 1,000 "
         "residents aged 65+. Model: constant + f_space(location) + f_cov(sales, disease prevalence, SVI, income, "
         "population) + noise. Location = two Matérn-3/2 kernels on projected km (a local and a regional scale); "
         "covariates = an ARD RBF kernel on the standardised values. "
         "covariates. Hyperparameters by exact marginal likelihood. Associations, not causal effects.", ""]
    L += ["## Summary", "",
          "| Class | Counties | Range: local / regional (km) | Var. share: local | regional | covariates | residual |",
          "|---|---:|---:|---:|---:|---:|---:|"]
    for r in res:
        s, g = r["shares"], r["range_km"]
        L.append(f"| {r['class']} | {r['n']:,} | {g['local']:,.0f} / {g['regional']:,.0f} | {s['local']:.0%} "
                 f"| {s['regional']:.0%} | {s['covariates']:.0%} | {s['residual']:.0%} |")
    L += ["", "_Range_: distance at which that spatial part's correlation falls to 0.05. "
          "_Variance shares_: variance of each component's posterior mean as a share of the outcome's variance "
          "(components can covary, so shares need not sum to 100%).", ""]
    L += ["## Out-of-sample fit (5-fold CV)", "",
          "| Class | Linear OLS | GP covariates | GP location | GP location + covariates | Moran's I of errors: OLS → GP both |",
          "|---|---:|---:|---:|---:|---:|"]
    for r in res:
        c = r["cv"]
        L.append(f"| {r['class']} | {c['linear']['r2']:.2f} | {c['gp_cov']['r2']:.2f} | {c['gp_space']['r2']:.2f} "
                 f"| {c['gp_both']['r2']:.2f} | {c['linear']['moran_I']:.2f} (p={c['linear']['moran_p']:.3f}) → "
                 f"{c['gp_both']['moran_I']:.2f} (p={c['gp_both']['moran_p']:.3f}) |")
    L += ["", "## Covariate relevance (1 / ARD lengthscale, standardised units)", "",
          "| Class | " + " | ".join(COV_LABEL[c] for c in COVS) + " |", "|---|" + "---:|" * len(COVS)]
    for r in res:
        L.append(f"| {r['class']} | " + " | ".join(f"{r['relevance'][c]:.2f}" for c in COVS) + " |")
    L += ["", "Larger = the outcome changes faster along that covariate. Near 0 = the GP found it nearly flat. "
          "Effect curves (with 90% bands) are on the website's Models tab and in `web/data/gp.json`.", "",
          "## Reading", "",
          "* If GP location + covariates beats GP covariates out of sample and the Moran's I of errors drops toward 0, "
          "promotion has geographic structure that the county covariates do not explain (e.g. sales-territory or "
          "metro-area effects).",
          "* The spatial range says how far that structure reaches: a few hundred km looks like metro areas / "
          "territories; thousands of km looks like regional differences.",
          "* Kentucky is missing: the current CDC PLACES release has no diabetes, CHD or asthma estimates for it.", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--classes", nargs="*", default=list(M3_CLASSES))
    a = ap.parse_args(argv)
    torch, _ = _torch()
    torch.set_num_threads(8)
    res = []
    with psycopg.connect(DSN) as conn:
        for cls in a.classes:
            measure, mlabel = M3_CLASSES[cls]
            res.append(run_class(conn, a.year, cls, measure, mlabel))
    OUT_MD.write_text(report(a.year, res))
    OUT_JSON.write_text(json.dumps({"year": a.year, "classes": res}, separators=(",", ":")))
    print(f"wrote {OUT_MD.relative_to(ROOT)} and {OUT_JSON.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
