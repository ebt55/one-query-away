"""Inference: paired cluster bootstrap over base cases, stratified by family, conditional on frozen thresholds.

- Resampling unit: the base case (its harmful and benign worlds, all renderings and conditions move together).
- Stratification: base cases are resampled with replacement WITHIN each family; family sizes stay fixed.
- One resample = a multiplicity vector over base cases (counts matrix C, B x n). Every statistic is a
  ratio of sums over the resampled base cases, e.g. TPR* = (C @ detected) / (C @ n_harmful), so the same
  C gives jointly-resampled, paired estimates for all conditions and contrasts.
- Thresholds are NOT re-estimated inside resamples: CIs are conditional on the frozen thresholds.
- CIs: 95% percentile intervals (the bootstrap is used for CIs).
- Bootstrap p-values (secondary, reported only): percentile-inversion,
    two-sided p = min(1, 2 * min((1 + #{d* <= 0}) / (B + 1), (1 + #{d* >= 0}) / (B + 1)))
    one-sided (greater) p = (1 + #{d* <= 0}) / (B + 1). They run slightly liberal at n=45 (0.07 at nominal 0.05).
- p-values ENTERING HOLM: paired sign-flip permutation test over base cases (sign_flip_test): under H0 the A/B
  labels are exchangeable within each base case, so each base case's A and B outcomes are swapped with probability
  1/2, independently (flips happen within base cases, so family strata keep their sizes; flips are drawn per
  family). >= 10,000 random flips, or exact enumeration when 2^n <= n_perm.
- Holm step-down across the Holm family (C2, C3), within each (regime, model).
- McNemar (exact binomial) only on one-decision-per-base-case summaries.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats as sps

from common import BOOT_SEED, N_BOOT, N_PERM


# ----------------------------------------------------------------------------- resampling
def stratified_counts(families, n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> np.ndarray:
    """families: array (n_clusters,) of stratum labels in a FIXED cluster order. Returns C (B x n) int32."""
    fam = np.asarray(families)
    n = len(fam)
    rng = np.random.default_rng(seed)
    C = np.zeros((n_boot, n), dtype=np.int32)
    rows = np.arange(n_boot)[:, None]
    for f in sorted(set(fam.tolist())):
        pos = np.flatnonzero(fam == f)
        idx = pos[rng.integers(0, len(pos), size=(n_boot, len(pos)))]
        np.add.at(C, (np.broadcast_to(rows, idx.shape), idx), 1)
    return C


def _ratio(C, num, den):
    with np.errstate(invalid="ignore", divide="ignore"):
        return (C @ num) / (C @ den)


def ci(samples, level=0.95):
    s = np.asarray(samples, float)
    s = s[~np.isnan(s)]
    if s.size == 0:
        return (float("nan"), float("nan"))
    a = (1 - level) / 2
    return (float(np.quantile(s, a)), float(np.quantile(s, 1 - a)))


def boot_p(samples, alternative="two-sided"):
    s = np.asarray(samples, float)
    s = s[~np.isnan(s)]
    B = s.size
    le = (1 + np.sum(s <= 0)) / (B + 1)
    ge = (1 + np.sum(s >= 0)) / (B + 1)
    if alternative == "greater":
        return float(min(1.0, le))
    if alternative == "less":
        return float(min(1.0, ge))
    return float(min(1.0, 2 * min(le, ge)))


class ClusterBootstrap:
    """Holds one fixed set of base cases (order + family) and its stratified counts matrix."""

    def __init__(self, base_cases, families, n_boot: int = N_BOOT, seed: int = BOOT_SEED):
        bc = pd.Series(list(families), index=list(base_cases))
        if bc.index.duplicated().any():
            raise ValueError("duplicate base cases")
        self.index = bc.index
        self.families = bc.to_numpy()
        self.n_boot, self.seed = n_boot, seed
        self.C = stratified_counts(self.families, n_boot, seed)

    def align(self, s: pd.Series, fill=None) -> np.ndarray:
        extra = s.index.difference(self.index)
        if len(extra):
            raise ValueError(f"{len(extra)} base cases not in the bootstrap frame, e.g. {list(extra[:3])}")
        missing = self.index.difference(s.index)
        if len(missing) and fill is None:
            raise ValueError(f"{len(missing)} base cases missing from series, e.g. {list(missing[:3])}")
        out = s.reindex(self.index)
        return (out.fillna(fill) if fill is not None else out).to_numpy(float)

    def ratio(self, num: pd.Series, den: pd.Series, fill=0.0) -> dict:
        """Ratio of sums. Base cases absent from num/den contribute 0/0 (fill=None makes absence an error)."""
        n_, d_ = self.align(num, fill), self.align(den, fill)
        point = float(n_.sum() / d_.sum()) if d_.sum() else float("nan")
        samp = _ratio(self.C, n_, d_)
        lo, hi = ci(samp)
        return {"point": point, "lo": lo, "hi": hi, "num": float(n_.sum()), "den": float(d_.sum()),
                "n_clusters": len(self.index), "samples": samp}

    def paired_diff(self, numA, denA, numB, denB, alternative="two-sided") -> dict:
        """Paired: every base case must be present for both conditions (absence raises)."""
        a = self.ratio(numA, denA, fill=None)
        b = self.ratio(numB, denB, fill=None)
        d = a["samples"] - b["samples"]
        lo, hi = ci(d)
        return {"point": a["point"] - b["point"], "lo": lo, "hi": hi, "a": a["point"], "b": b["point"],
                "p_two_sided": boot_p(d, "two-sided"), "p": boot_p(d, alternative), "alternative": alternative,
                "n_clusters": len(self.index), "samples": d}

    def auroc(self, scores_pos: dict, scores_neg: dict) -> dict:
        """scores_pos/neg: base_case -> list of scores of harmful / benign rows. Clustered AUROC with ties = 0.5."""
        P = [np.asarray(scores_pos.get(b, []), float) for b in self.index]
        N = [np.asarray(scores_neg.get(b, []), float) for b in self.index]
        npos = np.array([len(x) for x in P], float)
        nneg = np.array([len(x) for x in N], float)
        W = auroc_pair_matrix(P, N)
        point = float(W.sum() / (npos.sum() * nneg.sum())) if npos.sum() and nneg.sum() else float("nan")
        C = self.C.astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            samp = np.einsum("bi,ij,bj->b", C, W, C) / ((C @ npos) * (C @ nneg))
        lo, hi = ci(samp)
        return {"point": point, "lo": lo, "hi": hi, "n_pos": int(npos.sum()), "n_neg": int(nneg.sum()),
                "samples": samp}


class BootFactory:
    """Builds (and caches) one ClusterBootstrap per set of base cases, all from the same seed, so every
    condition of a model on the same base cases is resampled jointly."""

    def __init__(self, family_of: pd.Series, n_boot: int = N_BOOT, seed: int = BOOT_SEED):
        if family_of.index.duplicated().any():
            raise ValueError("family_of has duplicate base cases")
        self.family_of = family_of
        self.n_boot, self.seed = n_boot, seed
        self._cache = {}

    def get(self, base_cases) -> ClusterBootstrap:
        key = tuple(sorted(set(base_cases)))
        if key not in self._cache:
            missing = set(key) - set(self.family_of.index)
            if missing:
                raise ValueError(f"base cases without family: {sorted(missing)[:3]}")
            self._cache[key] = ClusterBootstrap(key, self.family_of.reindex(list(key)).to_numpy(), self.n_boot,
                                                self.seed)
        return self._cache[key]


def sign_flip_test(numA, denA, numB, denB, families, n_perm: int = N_PERM, seed: int = BOOT_SEED,
                   alternative: str = "two-sided") -> dict:
    """Paired sign-flip permutation test for T = sum(numA)/sum(denA) - sum(numB)/sum(denB) over base cases.
    Arrays are aligned per base case. Each base case's (numA, denA) and (numB, denB) are swapped with prob 1/2.
    Random flips: p = (1 + #{T* extreme}) / (n_perm + 1). Exact enumeration (all 2^n assignments, p = #/2^n) when
    2^n <= n_perm. Two-sided uses |T*| >= |T_obs|; 'greater' uses T* >= T_obs."""
    nA, dA, nB, dB = (np.asarray(x, float) for x in (numA, denA, numB, denB))
    fam = np.asarray(families)
    n = len(nA)
    t_obs = nA.sum() / dA.sum() - nB.sum() / dB.sum()
    exact = n <= 20 and 2 ** n <= n_perm
    if exact:
        S = ((np.arange(2 ** n)[:, None] >> np.arange(n)[None, :]) & 1).astype(float)
    else:
        rng = np.random.default_rng(seed)
        S = np.zeros((n_perm, n))
        for f in sorted(set(fam.tolist())):  # flips drawn per family stratum (sizes fixed by construction)
            pos = np.flatnonzero(fam == f)
            S[:, pos] = rng.integers(0, 2, size=(n_perm, len(pos)))
    sA = nA.sum() + S @ (nB - nA)
    sdA = dA.sum() + S @ (dB - dA)
    sB = nB.sum() + S @ (nA - nB)
    sdB = dB.sum() + S @ (dA - dB)
    with np.errstate(invalid="ignore", divide="ignore"):
        t = sA / sdA - sB / sdB
    eps = 1e-12
    if alternative == "greater":
        hit = t >= t_obs - eps
    elif alternative == "less":
        hit = t <= t_obs + eps
    else:
        hit = np.abs(t) >= abs(t_obs) - eps
    p = float(hit.mean()) if exact else float((1 + hit.sum()) / (len(t) + 1))
    return {"t_obs": float(t_obs), "p": min(1.0, p), "n_perm": int(len(t)), "exact": bool(exact),
            "alternative": alternative}


def auroc_pair_matrix(P, N) -> np.ndarray:
    n = len(P)
    W = np.zeros((n, n))
    for i in range(n):
        if not len(P[i]):
            continue
        for j in range(n):
            if not len(N[j]):
                continue
            d = P[i][:, None] - N[j][None, :]
            W[i, j] = (d > 0).sum() + 0.5 * (d == 0).sum()
    return W


def auroc_simple(pos, neg) -> float:
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    if not len(pos) or not len(neg):
        return float("nan")
    d = pos[:, None] - neg[None, :]
    return float(((d > 0).sum() + 0.5 * (d == 0).sum()) / d.size)


# ----------------------------------------------------------------------------- multiplicity / exact tests
def holm(pvals: dict) -> dict:
    """Holm step-down adjusted p-values (monotone, capped at 1)."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    adj, running = {}, 0.0
    for i, (k, p) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        adj[k] = running
    return adj


def mcnemar_exact(n_a_only: int, n_b_only: int) -> float:
    """Exact two-sided McNemar: binomial test on discordant pairs (p = 0.5)."""
    n = int(n_a_only) + int(n_b_only)
    if n == 0:
        return 1.0
    return float(sps.binomtest(int(n_a_only), n, 0.5, alternative="two-sided").pvalue)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (float(max(0.0, c - h)), float(min(1.0, c + h)))
