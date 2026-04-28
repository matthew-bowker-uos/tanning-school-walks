"""Tests for ``schools_sunbeds.stats`` against analytic reference values."""

from __future__ import annotations

import numpy as np
import pandas as pd

from schools_sunbeds import stats as st


def test_compute_ridit_unweighted_uniform_quintiles() -> None:
    q = pd.Series([1] * 10 + [2] * 10 + [3] * 10 + [4] * 10 + [5] * 10)
    r = st.compute_ridit(q)
    # Each quintile midpoint: 0.1, 0.3, 0.5, 0.7, 0.9
    by_q = r.groupby(q).first().sort_index()
    np.testing.assert_allclose(by_q.tolist(), [0.1, 0.3, 0.5, 0.7, 0.9])


def test_compute_ridit_weighted() -> None:
    # Half the population in Q1, the rest split among Q2-5
    q = pd.Series([1, 2, 3, 4, 5])
    w = pd.Series([50, 12, 13, 12, 13])
    r = st.compute_ridit(q, w)
    # Q1: cum 0 + 50/2 = 25; total 100 -> 0.25
    # Q2: cum 50 + 12/2 = 56; / 100 = 0.56
    # Q3: cum 62 + 13/2 = 68.5; / 100 = 0.685
    expected = [25 / 100, 56 / 100, 68.5 / 100, 81 / 100, 93.5 / 100]
    np.testing.assert_allclose(r.tolist(), expected, atol=1e-6)


def test_slope_index_inequality_recovers_known_slope() -> None:
    rng = np.random.default_rng(0)
    n = 200
    r = rng.uniform(0, 1, n)
    y = 5 + 2.0 * r + rng.normal(0, 0.5, n)  # true slope = 2.0
    sii = st.slope_index_inequality(pd.Series(y), pd.Series(r))
    assert abs(sii - 2.0) < 0.2


def test_fit_nb_glm_returns_irr_and_ci() -> None:
    rng = np.random.default_rng(1)
    n = 300
    x = rng.uniform(0, 1, n)
    mu = np.exp(0.5 + 1.0 * x)
    y = rng.poisson(mu)
    df = pd.DataFrame({"y": y, "x": x})
    res = st.fit_nb_glm(df, "y ~ x")
    assert "x" in res.params.index
    # IRR for x should be roughly exp(1.0) = 2.72 in this data
    assert 2.0 < float(res.irr["x"]) < 4.0
    assert res.n == n


def test_relative_index_inequality_matches_irr_at_ridit_extremes() -> None:
    rng = np.random.default_rng(2)
    n = 500
    r = rng.uniform(0, 1, n)
    mu = np.exp(0.4 + 1.5 * r)
    y = rng.poisson(mu)
    df = pd.DataFrame({"y": y, "ridit": r})
    rii = st.relative_index_inequality(df, "y", "ridit")
    # True RII = exp(1.5) = 4.48; allow noise
    assert 3.5 < rii < 5.5


def test_bootstrap_statistic_returns_ci() -> None:
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"x": rng.normal(10, 2, 200)})
    res = st.bootstrap_statistic(df, lambda d: float(d["x"].mean()), n_boot=200, seed=42)
    assert res.n_boot == 200
    assert res.ci_low < res.point < res.ci_high
    assert (res.ci_high - res.ci_low) < 1.0  # SE ~0.14, CI width ~0.55
