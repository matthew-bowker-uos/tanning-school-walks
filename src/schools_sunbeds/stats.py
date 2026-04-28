"""Statistics primitives for the H1 / H2 / H3 tests and SII / RII.

Three pieces:

- :func:`compute_ridit` maps IMD2025 quintiles (1–5, 1 = most deprived)
  to a population-weighted fractional rank in [0, 1] — the "ridit" used
  in the SII / RII calculation. Quintile 1 lands near 0.1 and quintile 5
  near 0.9, with the exact value depending on the population weights.

- :func:`fit_nb_glm` fits a negative-binomial GLM (statsmodels). It
  optionally clusters standard errors by a column (typically LAD).

- :func:`slope_index_inequality` and :func:`relative_index_inequality`
  return the population-weighted SII (level difference between most-
  and least-deprived ridits = 0 vs 1) and RII (the ratio between the two
  fitted values from an NB GLM with the ridit as the only predictor).

- :func:`bootstrap_statistic` runs a parametric/bootstrap loop and
  returns a point estimate plus 95 % CI for any callable that maps a
  resampled DataFrame to a scalar. Used for the headline
  ``RII_route / RII_buffer`` ratio.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd
import statsmodels.api as sm

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ridit


def compute_ridit(
    quintile: pd.Series,
    weights: pd.Series | None = None,
) -> pd.Series:
    """Population-weighted fractional rank for an ordinal grouping.

    For each row, the ridit is the cumulative population fraction up to
    the previous quintile, plus half the population fraction inside this
    quintile. Identical rows in the same quintile share the same ridit.

    ``quintile`` is treated as an ordinal scale (1 = most deprived, 5 =
    least). ``weights`` defaults to 1 per row when not supplied.
    """

    if weights is None:
        weights = pd.Series(1.0, index=quintile.index)
    df = pd.DataFrame({"q": quintile.astype("int64"), "w": weights.astype("float64")})
    grouped = df.groupby("q", observed=True)["w"].sum().sort_index()
    cum = grouped.cumsum()
    total = float(cum.iloc[-1]) if len(cum) else 1.0
    midpoints = (cum.shift(1, fill_value=0.0) + grouped / 2.0) / total
    return df["q"].map(midpoints)


# ---------------------------------------------------------------------------
# NB regression


@dataclass(frozen=True)
class NBResult:
    """Tidy summary of a negative-binomial GLM fit."""

    params: pd.Series
    bse: pd.Series
    pvalues: pd.Series
    conf_int_low: pd.Series
    conf_int_high: pd.Series
    irr: pd.Series  # exp(params)
    irr_low: pd.Series
    irr_high: pd.Series
    n: int
    aic: float
    formula: str
    fitted: object  # statsmodels result for downstream prediction

    def summary_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "coef": self.params,
                "se": self.bse,
                "p": self.pvalues,
                "ci_low": self.conf_int_low,
                "ci_high": self.conf_int_high,
                "IRR": self.irr,
                "IRR_low": self.irr_low,
                "IRR_high": self.irr_high,
            }
        )


def fit_nb_glm(
    df: pd.DataFrame,
    formula: str,
    *,
    offset_col: str | None = None,
    cluster_col: str | None = None,
    alpha: float | None = None,
) -> NBResult:
    """Fit ``y ~ ...`` as a negative-binomial GLM.

    ``alpha`` is the NB dispersion parameter; if None we estimate it
    via a Poisson-GLM-then-method-of-moments shortcut.

    ``cluster_col`` triggers cluster-robust SE if given.
    """

    formula_full = formula
    df = df.copy()
    if offset_col is not None:
        df["_offset"] = np.log(np.maximum(df[offset_col].astype("float64"), 1.0))

    if alpha is None:
        # Poisson first, then method-of-moments alpha estimate
        pois = sm.GLM.from_formula(
            formula_full,
            data=df,
            family=sm.families.Poisson(),
            offset=df["_offset"] if offset_col is not None else None,
        ).fit()
        mu = pois.fittedvalues
        y = pois.model.endog
        # Cameron & Trivedi method-of-moments NB2 alpha
        # var(y) = mu + alpha * mu^2 -> alpha = mean( (y-mu)^2 - mu ) / mean(mu^2)
        denom = float(np.mean(mu ** 2))
        alpha = max(float(np.mean((y - mu) ** 2 - mu)) / denom, 1e-6)

    fit_kwargs = {}
    if offset_col is not None:
        fit_kwargs["offset"] = df["_offset"]
    if cluster_col is not None:
        fit_kwargs["cov_type"] = "cluster"
        fit_kwargs["cov_kwds"] = {"groups": df[cluster_col]}

    res = sm.GLM.from_formula(
        formula_full,
        data=df,
        family=sm.families.NegativeBinomial(alpha=alpha),
    ).fit(**fit_kwargs)

    ci = res.conf_int()
    return NBResult(
        params=res.params,
        bse=res.bse,
        pvalues=res.pvalues,
        conf_int_low=ci[0],
        conf_int_high=ci[1],
        irr=np.exp(res.params),
        irr_low=np.exp(ci[0]),
        irr_high=np.exp(ci[1]),
        n=int(res.nobs),
        aic=float(res.aic),
        formula=formula_full,
        fitted=res,
    )


# ---------------------------------------------------------------------------
# SII / RII


def slope_index_inequality(
    outcome: pd.Series,
    ridit: pd.Series,
    weights: pd.Series | None = None,
) -> float:
    """Weighted-OLS slope on the outcome scale (SII).

    SII = β₁ from ``y ~ β₀ + β₁ * ridit``, weighted by ``weights``. With
    ridit ranging 0–1, SII is interpretable as the absolute difference
    in outcome between hypothetical most-deprived (ridit=0) and least-
    deprived (ridit=1) extremes.
    """

    df = pd.DataFrame({"y": outcome.astype("float64"), "r": ridit.astype("float64")})
    if weights is not None:
        df["w"] = weights.astype("float64")
        wls = sm.WLS(df["y"], sm.add_constant(df["r"]), weights=df["w"]).fit()
    else:
        wls = sm.OLS(df["y"], sm.add_constant(df["r"])).fit()
    return float(wls.params["r"])


def relative_index_inequality(
    df: pd.DataFrame,
    outcome_col: str,
    ridit_col: str,
    *,
    offset_col: str | None = None,
    weights_col: str | None = None,
) -> float:
    """RII via NB GLM: predicted outcome at ridit=0 / at ridit=1.

    A value > 1 indicates inequality favouring the less-deprived (i.e.
    higher exposure in more-deprived areas, since ridit=0 is most
    deprived). A value < 1 means more exposure in less-deprived areas.
    """

    formula = f"{outcome_col} ~ {ridit_col}"
    res = fit_nb_glm(df, formula, offset_col=offset_col)
    # Predicted value at ridit = 0 vs ridit = 1; the ratio is exp(β₁)
    beta_r = float(res.params[ridit_col])
    return float(np.exp(beta_r))


# ---------------------------------------------------------------------------
# Bootstrap


@dataclass(frozen=True)
class BootstrapResult:
    """Point estimate plus equal-tailed bootstrap CI for a scalar statistic."""

    point: float
    ci_low: float
    ci_high: float
    n_boot: int


def bootstrap_statistic(
    df: pd.DataFrame,
    fn: Callable[[pd.DataFrame], float],
    *,
    n_boot: int = 1_000,
    alpha: float = 0.05,
    seed: int = 20260428,
    cluster_col: str | None = None,
) -> BootstrapResult:
    """Bootstrap a scalar statistic from row resamples of ``df``.

    If ``cluster_col`` is given, sample whole clusters with replacement
    (block bootstrap). Default is i.i.d. row sampling with replacement.
    """

    rng = np.random.default_rng(seed)
    point = float(fn(df))
    boots = np.empty(n_boot, dtype="float64")

    if cluster_col is not None:
        clusters = df[cluster_col].unique()
        for i in range(n_boot):
            sampled = rng.choice(clusters, size=len(clusters), replace=True)
            sub = pd.concat([df.loc[df[cluster_col] == c] for c in sampled], ignore_index=True)
            try:
                boots[i] = float(fn(sub))
            except Exception as exc:  # noqa: BLE001
                log.debug("bootstrap iter %d failed: %s", i, exc)
                boots[i] = np.nan
    else:
        idx = np.arange(len(df))
        for i in range(n_boot):
            sample_idx = rng.choice(idx, size=len(idx), replace=True)
            sub = df.iloc[sample_idx].reset_index(drop=True)
            try:
                boots[i] = float(fn(sub))
            except Exception as exc:  # noqa: BLE001
                log.debug("bootstrap iter %d failed: %s", i, exc)
                boots[i] = np.nan

    boots = boots[np.isfinite(boots)]
    if len(boots) == 0:
        return BootstrapResult(point=point, ci_low=float("nan"), ci_high=float("nan"), n_boot=0)
    lo = float(np.quantile(boots, alpha / 2))
    hi = float(np.quantile(boots, 1 - alpha / 2))
    return BootstrapResult(point=point, ci_low=lo, ci_high=hi, n_boot=int(len(boots)))


__all__ = [
    "BootstrapResult",
    "NBResult",
    "bootstrap_statistic",
    "compute_ridit",
    "fit_nb_glm",
    "relative_index_inequality",
    "slope_index_inequality",
]
