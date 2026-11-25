# M4. Gaussian-process model — 2024

_Generated 2026-11-25 02:12 UTC by `python -m analysis.gp`._

Outcome: log(1 + class promotion $ per 1,000 residents aged 65+), contiguous-US counties with >= 1,000 residents aged 65+. Model: constant + f_space(location) + f_cov(sales, disease prevalence, SVI, income, population) + noise. Location = two Matérn-3/2 kernels on projected km (a local and a regional scale); covariates = an ARD RBF kernel on the standardised values. covariates. Hyperparameters by exact marginal likelihood. Associations, not causal effects.

## Summary

| Class | Counties | Range: local / regional (km) | Var. share: local | regional | covariates | residual |
|---|---:|---:|---:|---:|---:|---:|
| SGLT2 | 2,590 | 102 / 1,833 | 2% | 9% | 44% | 24% |
| DOAC | 2,597 | 131 / 2,299 | 1% | 6% | 50% | 35% |
| T2_BIOLOGIC | 1,197 | 639 / 1,609 | 1% | 1% | 58% | 34% |

_Range_: distance at which that spatial part's correlation falls to 0.05. _Variance shares_: variance of each component's posterior mean as a share of the outcome's variance (components can covary, so shares need not sum to 100%).

## Out-of-sample fit (5-fold CV)

| Class | Linear OLS | GP covariates | GP location | GP location + covariates | Moran's I of errors: OLS → GP both |
|---|---:|---:|---:|---:|---:|
| SGLT2 | 0.51 | 0.54 | 0.29 | 0.62 | 0.22 (p=0.005) → -0.05 (p=1.000) |
| DOAC | 0.48 | 0.51 | 0.17 | 0.58 | 0.17 (p=0.005) → -0.03 (p=0.980) |
| T2_BIOLOGIC | 0.55 | 0.57 | 0.09 | 0.61 | 0.11 (p=0.005) → -0.03 (p=0.975) |

## Covariate relevance (1 / ARD lengthscale, standardised units)

| Class | Sales (log fills per 1k 65+) | Disease prevalence (%) | Social vulnerability (SVI) | Median income (log $) | Population (log) |
|---|---:|---:|---:|---:|---:|
| SGLT2 | 0.43 | 0.15 | 0.12 | 0.12 | 0.45 |
| DOAC | 0.33 | 0.13 | 0.14 | 0.14 | 0.46 |
| T2_BIOLOGIC | 0.27 | 0.11 | 0.12 | 0.13 | 0.46 |

Larger = the outcome changes faster along that covariate. Near 0 = the GP found it nearly flat. Effect curves (with 90% bands) are on the website's Models tab and in `web/data/gp.json`.

## Reading

* If GP location + covariates beats GP covariates out of sample and the Moran's I of errors drops toward 0, promotion has geographic structure that the county covariates do not explain (e.g. sales-territory or metro-area effects).
* The spatial range says how far that structure reaches: a few hundred km looks like metro areas / territories; thousands of km looks like regional differences.
* Kentucky is missing: the current CDC PLACES release has no diabetes, CHD or asthma estimates for it.
