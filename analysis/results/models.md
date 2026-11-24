# Models — 2024

_Generated 2026-11-24 21:12 UTC by `python -m analysis.models`._

All results are **cross-sectional associations**. Companies pay the prescribers and places that already prescribe the most, so none of these coefficients is a causal effect of promotion.

## M1. Prescriber-level: promotion and product share within class

Outcome: the product's share (percentage points) of the prescriber's 30-day fills in its class (e.g. Eliquis / all DOAC fills). Comparison: same specialty, same county (specialty and county fixed effects), controlling for the prescriber's class volume and payments naming rival products. Individual prescribers with >= 12 class fills; 95% CIs clustered by county.

| Product | Prescribers | % paid | Mean share | Paid vs unpaid (raw) | Paid, adjusted (pp) | Per log $ (pp) | Rival log $ (pp) |
|---|---:|---:|---:|---:|---:|---:|---:|
| ELIQUIS | 266,550 | 13.5% | 81.5 | 80.5 vs 81.6 | +2.3 [+1.7, +2.8] | +0.74 [+0.58, +0.90] | -0.75 [-0.87, -0.62] |
| XARELTO | 266,550 | 7.7% | 16.7 | 22.7 vs 16.2 | +3.4 [+2.9, +3.9] | +1.04 [+0.91, +1.18] | -0.28 [-0.39, -0.18] |
| JARDIANCE | 212,114 | 24.1% | 73.6 | 64.6 vs 76.5 | +0.4 [-0.2, +0.9] | +0.37 [+0.23, +0.52] | -3.88 [-4.05, -3.71] |
| FARXIGA | 212,114 | 20.4% | 25.8 | 42.6 vs 21.5 | +15.2 [+14.5, +15.9] | +4.06 [+3.90, +4.23] | -0.17 [-0.32, -0.02] |
| DUPIXENT | 17,033 | 63.3% | 75.8 | 77.7 vs 72.4 | +8.1 [+6.7, +9.5] | +2.03 [+1.78, +2.28] | -1.75 [-2.04, -1.47] |

**Reading:** _Paid, adjusted_ is how many percentage points higher the product's share is among prescribers who received any payment naming it than among unpaid peers of the same specialty in the same county. _Rival log $_ should be negative if a competitor's promotion goes with a lower share for this product.

## M2. County-level: share of voice vs share of market

Outcome: product share of class fills in the county (pp). Regressor: product share of class promotion dollars in the county (pp). Product and county fixed effects, weighted by class fills; counties with >= $1,000 class promotion and >= 500 class fills.

| Class | Product x county cells | Counties | pp share per pp of voice |
|---|---:|---:|---:|
| DOAC | 1,579 | 543 | +0.060 [+0.040, +0.081] |
| SGLT2 | 2,776 | 980 | +0.161 [+0.117, +0.204] |
| T2_BIOLOGIC | 2,524 | 366 | +0.094 [+0.056, +0.132] |

**Reading:** a slope of 0.10 means a county where a product gets 10 more points of its class's promotion has, on average, 1 more point of the class's fills, relative to that product elsewhere and to other products in that county.

## M3. County-level: what does promotion track beyond sales?

Outcome: log(1 + class promotion $ per 1,000 residents aged 65+). Regressors: log class fills per 1,000 aged 65+, disease prevalence (z), SVI (z), log median income, log population. OLS, then a spatial lag model (GM_Lag; 6-nearest-neighbour weights, row-standardised). Counties with >= 1,000 residents aged 65+.

### SGLT2 (burden = diabetes prevalence)

n = 2,604 counties · OLS R² = 0.52 · Moran's I of OLS residuals = 0.22 (p = 0.001) · spatial ρ = 0.26 (se 0.03)

| Term | OLS (se) | Spatial lag (se) |
|---|---:|---:|
| const | -19.464 (2.332) | -14.022 (2.314) |
| log_fills_pc | +1.298 (0.043) | +1.248 (0.041) |
| prev_z | +0.347 (0.047) | +0.163 (0.050) |
| svi_z | +0.007 (0.042) | -0.005 (0.039) |
| log_income | +0.821 (0.219) | +0.370 (0.215) |
| log_pop | +0.610 (0.031) | +0.509 (0.032) |

### DOAC (burden = coronary heart disease prevalence (AF not in PLACES))

n = 2,612 counties · OLS R² = 0.48 · Moran's I of OLS residuals = 0.16 (p = 0.001) · spatial ρ = 0.00 (se 0.04)

| Term | OLS (se) | Spatial lag (se) |
|---|---:|---:|
| const | -6.316 (2.290) | -6.268 (2.335) |
| log_fills_pc | +0.737 (0.038) | +0.738 (0.039) |
| prev_z | -0.028 (0.043) | -0.029 (0.045) |
| svi_z | +0.062 (0.038) | +0.062 (0.038) |
| log_income | -0.315 (0.211) | -0.319 (0.214) |
| log_pop | +0.710 (0.030) | +0.708 (0.036) |

### T2_BIOLOGIC (burden = asthma prevalence)

n = 1,207 counties · OLS R² = 0.55 · Moran's I of OLS residuals = 0.11 (p = 0.001) · spatial ρ = 0.05 (se 0.06)

| Term | OLS (se) | Spatial lag (se) |
|---|---:|---:|
| const | -8.735 (2.936) | -7.940 (3.026) |
| log_fills_pc | +1.021 (0.043) | +1.026 (0.043) |
| prev_z | -0.101 (0.046) | -0.098 (0.046) |
| svi_z | +0.045 (0.059) | +0.028 (0.062) |
| log_income | +0.184 (0.287) | +0.095 (0.299) |
| log_pop | +0.802 (0.045) | +0.795 (0.045) |

## Limits

* One year, cross-section: payments and prescribing are measured in the same year, and promotion is targeted at existing high prescribers (reverse causality). Adding 2023 would allow lagged payments and within-prescriber changes (a much stronger design).
* Medicare Part D only; CMS suppresses cells under 11 claims; indication is inferred.
* Open Payments excludes rep detailing without a transfer of value, samples and DTC ads.
* M3's burden proxies are imperfect (no AF in PLACES; DOAC uses coronary heart disease).
