"""Bootstrap, Holm, McNemar, AUROC on SYNTHETIC data with known answers."""
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

import stats as S


def test_holm_known():
    adj = S.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert np.isclose(adj["a"], 0.03) and np.isclose(adj["c"], 0.06) and np.isclose(adj["b"], 0.06)
    adj = S.holm({"a": 0.5, "b": 0.6})
    assert adj["a"] == 1.0 and adj["b"] == 1.0


def test_mcnemar_exact_known():
    # 9 vs 1 discordant: two-sided exact binomial p = 2 * P(X <= 1 | n=10, 0.5) = 22/1024
    assert np.isclose(S.mcnemar_exact(9, 1), 22 / 1024)
    assert S.mcnemar_exact(0, 0) == 1.0


def test_wilson():
    lo, hi = S.wilson(5, 10)
    assert np.isclose(lo, 0.2366, atol=1e-3) and np.isclose(hi, 0.7634, atol=1e-3)


def test_stratified_counts_preserve_family_sizes():
    fam = np.array(["F1"] * 4 + ["F2"] * 7 + ["F3"] * 2)
    C = S.stratified_counts(fam, 500, 1)
    for f in ("F1", "F2", "F3"):
        assert (C[:, fam == f].sum(1) == (fam == f).sum()).all()
    assert (S.stratified_counts(fam, 500, 1) == C).all()  # reproducible


def test_paired_identical_gives_zero():
    bc = [f"b{i}" for i in range(30)]
    fam = ["F1"] * 10 + ["F2"] * 10 + ["F3"] * 10
    boot = S.ClusterBootstrap(bc, fam, 2000, 0)
    x = pd.Series(np.random.default_rng(0).integers(0, 2, 30).astype(float), index=bc)
    one = pd.Series(1.0, index=bc)
    r = boot.paired_diff(x, one, x, one)
    assert r["point"] == 0 and r["lo"] == 0 and r["hi"] == 0 and r["p_two_sided"] == 1.0


def test_paired_requires_complete_pairs():
    bc = [f"b{i}" for i in range(6)]
    boot = S.ClusterBootstrap(bc, ["F1"] * 6, 100, 0)
    full = pd.Series(1.0, index=bc)
    import pytest
    with pytest.raises(ValueError):
        boot.paired_diff(full.iloc[:5], full.iloc[:5], full, full)


def _sim_paired(rng, n_per_fam=15, pa=0.7, pb=0.5, rho=0.5):
    """Correlated paired Bernoulli outcomes per base case with family-specific offsets (true diff = pa - pb)."""
    rows = []
    for f, off in (("F1", -0.1), ("F2", 0.0), ("F3", 0.1)):
        for i in range(n_per_fam):
            u = rng.random()
            a = float(u < pa + off)
            b = float((u if rng.random() < rho else rng.random()) < pb + off)
            rows.append((f"{f}-{i}", f, a, b))
    return pd.DataFrame(rows, columns=["bc", "fam", "a", "b"])


def test_bootstrap_ci_coverage():
    """Percentile CI for a paired difference of rates should cover the true value ~95% of the time."""
    rng = np.random.default_rng(123)
    true = 0.2  # family offsets cancel (equal family sizes)
    hits, n_sim = 0, 300
    for k in range(n_sim):
        d = _sim_paired(rng)
        boot = S.ClusterBootstrap(d.bc, d.fam, 1000, k)
        one = pd.Series(1.0, index=d.bc.values)
        r = boot.paired_diff(pd.Series(d.a.values, index=d.bc), one, pd.Series(d.b.values, index=d.bc), one)
        hits += r["lo"] <= true <= r["hi"]
    cov = hits / n_sim
    print(f" bootstrap CI coverage {hits}/{n_sim} = {cov:.3f}")
    assert 0.88 <= cov <= 0.99, cov


def test_bootstrap_p_detects_large_effect_and_not_null():
    rng = np.random.default_rng(5)
    d = _sim_paired(rng, pa=0.9, pb=0.2)
    boot = S.ClusterBootstrap(d.bc, d.fam, 4000, 1)
    one = pd.Series(1.0, index=d.bc.values)
    r = boot.paired_diff(pd.Series(d.a.values, index=d.bc), one, pd.Series(d.b.values, index=d.bc), one, "greater")
    assert r["p"] < 0.01 and r["lo"] > 0
    rej, n_sim = 0, 200
    for k in range(n_sim):  # null: same marginal rates -> two-sided type-I error near 5%
        d = _sim_paired(rng, pa=0.5, pb=0.5)
        boot = S.ClusterBootstrap(d.bc, d.fam, 1000, k)
        r = boot.paired_diff(pd.Series(d.a.values, index=d.bc), one.reindex(d.bc.values).fillna(1.0),
                             pd.Series(d.b.values, index=d.bc), one.reindex(d.bc.values).fillna(1.0))
        rej += r["p_two_sided"] < 0.05
    print(f" null type-I error (two-sided, 0.05) {rej}/{n_sim} = {rej / n_sim:.3f}")
    assert rej / n_sim <= 0.11, rej / n_sim


def test_ratio_of_sums_matches_pooled():
    bc = ["a", "b", "c"]
    boot = S.ClusterBootstrap(bc, ["F1"] * 3, 200, 0)
    r = boot.ratio(pd.Series([1.0, 2.0, 0.0], index=bc), pd.Series([1.0, 3.0, 2.0], index=bc))
    assert np.isclose(r["point"], 3 / 6)


def test_clustered_auroc_point_matches_sklearn():
    rng = np.random.default_rng(2)
    bc = [f"b{i}" for i in range(40)]
    pos = {b: [float(rng.integers(3, 11))] for b in bc}
    neg = {b: [float(rng.integers(0, 8))] for b in bc}
    boot = S.ClusterBootstrap(bc, ["F1"] * 20 + ["F2"] * 20, 1000, 0)
    a = boot.auroc(pos, neg)
    y = [1] * 40 + [0] * 40
    s = [pos[b][0] for b in bc] + [neg[b][0] for b in bc]
    assert np.isclose(a["point"], roc_auc_score(y, s))
    assert a["lo"] <= a["point"] <= a["hi"]


def test_sign_flip_exact_small_n():
    # n=3, all three pairs favour A by 1 -> T_obs = 1; exact two-sided p = #{|T*| >= 1}/8 = 2/8
    one = np.ones(3)
    r = S.sign_flip_test(one, one, np.zeros(3), one, ["F1"] * 3, n_perm=10_000, alternative="two-sided")
    assert r["exact"] and np.isclose(r["p"], 0.25) and r["t_obs"] == 1.0
    r = S.sign_flip_test(one, one, np.zeros(3), one, ["F1"] * 3, n_perm=10_000, alternative="greater")
    assert np.isclose(r["p"], 0.125)
    r = S.sign_flip_test(one, one, one, one, ["F1"] * 3)  # identical -> p = 1
    assert r["p"] == 1.0


def test_sign_flip_type1_and_power():
    rng = np.random.default_rng(77)
    rej, n_sim = 0, 300
    for k in range(n_sim):
        d = _sim_paired(rng, pa=0.5, pb=0.5)
        one = np.ones(len(d))
        r = S.sign_flip_test(d.a.values, one, d.b.values, one, d.fam.values, n_perm=10_000, seed=k)
        rej += r["p"] < 0.05
    print(f" sign-flip null type-I error (two-sided, 0.05) {rej}/{n_sim} = {rej / n_sim:.3f}")
    assert rej / n_sim <= 0.075
    d = _sim_paired(rng, pa=0.9, pb=0.2)
    one = np.ones(len(d))
    assert S.sign_flip_test(d.a.values, one, d.b.values, one, d.fam.values, seed=1)["p"] < 0.01
