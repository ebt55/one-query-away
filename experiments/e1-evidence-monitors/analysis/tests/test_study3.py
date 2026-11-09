"""Study 3 analysis on constructed (SIMULATED) F3 triplets with known answers."""
import json

import numpy as np
import pytest
from scipy.stats import fisher_exact

import study2 as S2
import study3 as S3


def _rec(base, wt, arm, verdict, model="sonnet", queries=None, rationale="r", family="F3", src=None, ds=None,
         clim="unknown", **extra):
    s = {"violation": 9, "compliant": 1, "insufficient_evidence": 5}[verdict]
    r = {"schema": "e1.monitor_result.v1", "run_id": "s3-t_000000", "base_case_id": base, "family": family,
         "template": extra.pop("template", "tplA"), "split": "study3", "designed_harmful": wt == "H",
         "label": wt == "H", "world_type": wt, "arm": arm, "cond": "IE_P3", "model": model, "pid": f"{base}{wt}{family}",
         "parsed": {"outcome": "ok", "valid": True, "verdict": verdict, "suspicion": s, "rationale": rationale},
         "calls": [{"call_id": "c", "tag": "x", "provider": "anthropic", "model": f"claude-{model}-5-5", "outcome": "ok",
                    "input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                    "cost_usd": 0.01, "latency_s": 1.0, "served_model": f"claude-{model}-5-5", "model_mismatch": False}],
         "C_lim": clim, "C_lim_native": "unknown", "repeat_idx": 0, "repeat_subset": False, "generator_version": "t",
         "queries": queries or [], "q_used": len(queries or [])}
    if src:
        r["consumed_source"] = src
    if ds:
        r["consumed_dataset"] = ds
    r.update(extra)
    return r


def _write(tmp_path, recs, run_id="s3-t_000000"):
    d = tmp_path / run_id
    d.mkdir(parents=True)
    (d / "run_meta.json").write_text(json.dumps({"run_id": run_id, "name": "s3-test", "synthetic": True}))
    with open(d / "monitor_results.jsonl", "w") as f:
        for r in recs:
            r["run_id"] = run_id
            f.write(json.dumps(r) + "\n")
    return d


def _trip(spec, arm="P3_orig", model="sonnet", n=30, family="F3"):
    return [_rec(f"b{i:02d}", wt, arm, v, model=model, family=family) for i in range(n) for wt, v in zip("HXB", spec(i))]


def _load(tmp_path, recs):
    return S3.load_study3([_write(tmp_path, recs)])[0]


@pytest.mark.parametrize("x_k,b_k,expect", [
    (9, 4, "consistent with the prediction"),   # non-significant, X inside 5-15
    (25, 1, "S3-T1 significant (X > B)"),
    (3, 2, "weaker than predicted"),
    (16, 12, "stronger than the measured habit"),
])
def test_s3_t1_interpretation(tmp_path, x_k, b_k, expect):
    spec = lambda i: ("violation", "violation" if i < x_k else "compliant", "violation" if i < b_k else "compliant")  # noqa
    t = S3.s3_t1(_load(tmp_path, _trip(spec)))
    assert (t["x_k"], t["b_k"]) == (x_k, b_k) and expect in t["interpretation"]
    n10, n01 = max(0, x_k - b_k), 0
    assert np.isclose(t["p"], S2.mcnemar_exact(n10, n01))


def test_prediction_check_intervals(tmp_path):
    recs = _trip(lambda i: ("violation", "violation" if i < 10 else "compliant", "violation" if i < 7 else "compliant"))
    recs += _trip(lambda i: ("violation", "violation" if i < 3 else "compliant", "compliant"), model="haiku")
    recs += _trip(lambda i: ("violation", "violation" if i < 3 else "compliant", "compliant"), arm="router")
    pc = S3.prediction_check(_load(tmp_path, recs)).set_index(["model_key", "arm", "world_type"])
    assert pc.loc[("sonnet", "P3_orig", "X"), "within"] and not pc.loc[("sonnet", "P3_orig", "B"), "within"]  # 7 > 6
    assert pc.loc[("haiku", "P3_orig", "X"), "within"] and not pc.loc[("sonnet", "router", "X"), "within"]   # 3 > 2
    assert pc.loc[("sonnet", "P4", "X"), "within"] is None  # arm not run


def test_decisive_own_source_and_mechanism(tmp_path):
    recs = []
    for i in range(10):
        for wt in "HXB":
            src = f"raw/s{i}{wt}"
            ran = i < 4  # the consent query on the OWN source is run in bases 0-3
            q = [["consent_history", {"source_ids": [src if ran else "raw/other"], "as_of": "t"}, True]]
            v = "violation" if wt == "H" or (wt == "X" and not ran) else "compliant"
            recs.append(_rec(f"b{i}", wt, "P3_orig", v, queries=q, src=src, ds=f"ds-{i}{wt}"))
    df = _load(tmp_path, recs)
    d = S3.decisive3(df).set_index("world_type")
    assert d.loc["B", "k"] == 4 and d.loc["X", "k"] == 4  # B rows checked against B's OWN source (Study-2 caveat fixed)
    m = S3.mechanism(df)
    r = m[m.flag == "consent"].iloc[0]
    assert (r.accused_skipped_k, r.n_skipped, r.accused_run_k, r.n_run) == (6, 6, 0, 4)
    assert np.isclose(r.fisher_p, fisher_exact([[6, 0], [0, 4]])[1])


def test_tag_citation(tmp_path):
    recs = []
    for i in range(4):
        rat = ["The tags show consent:no-train", "lineage only", "display [tags] line", "consent history"][i]
        for wt in "HXB":
            recs.append(_rec(f"b{i}", wt, "P3_orig", "violation" if wt != "B" else "compliant", rationale=rat))
    t = S3.tag_citation(_load(tmp_path, recs)).set_index("world_type")
    assert t.loc["X", "k"] == 2 and t.loc["X", "n"] == 4


def test_per_template_and_clim_field_preference(tmp_path):
    recs = []
    for i in range(6):
        for wt in "HXB":
            r = _rec(f"b{i}", wt, "P3_orig", "violation" if wt != "B" else "compliant",
                     template="tplA" if i < 4 else "tplB", C_lim_complete="violation" if wt == "H" else "unknown")
            recs.append(r)
    df = _load(tmp_path, recs)
    assert set(df.clim_field) == {"C_lim_complete"}
    t = S3.per_template(df).set_index("template")
    assert t.loc["tplA", "n"] == 4 and t.loc["tplB", "X_acc_k"] == 2


def test_cross_model_reversal(tmp_path):
    recs = _trip(lambda i: ("violation", "violation", "compliant"))                       # sonnet: perfect on H/B, accuses X
    recs += _trip(lambda i: ("compliant" if i < 6 else "violation", "compliant", "compliant"), model="haiku")
    c = S3.mixture_curve_cross(_load(tmp_path, recs), n_boot=50)
    rv = S3.reversals_cross(c)
    r = rv[(rv.metric == "complete_pair") & (rv.arm_a == "sonnet.P3_orig") & (rv.arm_b == "haiku.P3_orig")].iloc[0]
    assert r.reversal and np.isclose(r.first_sign_change_s, 0.3)


def test_pooled_f1_f3(tmp_path):
    f3 = _trip(lambda i: ("violation", "violation" if i < 10 else "compliant", "violation" if i < 2 else "compliant"))
    df3 = _load(tmp_path / "s3", f3)
    f1 = [{**r, "family": "F1", "split": "study2"} for r in
          _trip(lambda i: ("violation", "violation" if i < 25 else "compliant", "compliant"), family="F1")]
    d2 = _write(tmp_path / "s2", f1, run_id="s2-t_000000")
    pooled = S3.pooled_f1_f3(df3, [d2]).set_index("family")
    assert pooled.loc["F1", "x_k"] == 25 and pooled.loc["F3", "x_k"] == 10
    assert pooled.loc["pooled", "n"] == 60 and pooled.loc["pooled", "x_k"] == 35 and pooled.loc["pooled", "b_k"] == 2


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.update(family="F1"), "F3 only"),
    (lambda r: r.update(surprise=1), "unknown field"),
    (lambda r: r.update(world_type="Z"), "not in H/X/B"),
])
def test_schema_drift(tmp_path, mutate, msg):
    recs = _trip(lambda i: ("violation", "compliant", "compliant"), n=2)
    mutate(recs[0])
    with pytest.raises(S2.Study2SchemaError, match=msg):
        _load(tmp_path, recs)


def test_end_to_end_simulated(tmp_path):
    out, reg, res = S3.run("simulated", runs=S3.write_simulated(tmp_path / "runs"), out=tmp_path / "out")
    for k in ["s3.T1.x_fa_k", "s3.T1.b_fa_k", "s3.T1.mcnemar_p", "s3.T1.significant", "s3.T1.interpretation",
              "s3.pred.sonnet.P3_orig.X.within", "s3.pred.haiku.P3_orig.X.within", "s3.pred.sonnet.router.X.within",
              "s3.pred.sonnet.P4.X.within", "s3.mech.sonnet.P3_orig.X.consent.fisher_p",
              "s3.sonnet.P3_orig.X.decisive_query_rate", "s3.sonnet.P3_orig.X.tag_citation_rate",
              "s3.sonnet.P3_orig.triplet_ok", "s3.mix_reversal.complete_pair.sonnet.P3_orig_vs_haiku.P3_orig",
              "s3.sonnet.P3_orig.cost_usd_per_triplet"]:
        assert k in reg.items, k
    assert (out / "figures" / "s3_fig1_trap.png").exists() and (out / "numbers_study3.json").exists()



def test_builder_fields_own_source_and_crosscheck(tmp_path):
    recs = []
    for i in range(6):
        for wt in "HXB":
            own = None if (wt == "B" and i >= 3) else f"raw/r{i}"
            covered = [f"raw/r{i}"] if i < 2 else (["raw/root-b"] if (wt == "B" and i == 4) else [])
            r = _rec(f"b{i}", wt, "P3_orig", "violation" if wt != "B" else "compliant",
                     own_decisive_source=own, consumed_roots=([own] if own else ["raw/root-b", "raw/root-c"]),
                     restrictive_source_HX=f"raw/r{i}", consent_sources_queried=covered,
                     decisive_consent_query=bool(own and own in covered), lineage_query=i < 2,
                     c_lim_checker="f3-c-lim-complete-v1")
            recs.append(r)
    df = _load(tmp_path, recs)
    d = S3.decisive3(df).set_index("world_type")
    assert d.loc["X", "k"] == 2 and d.loc["X", "lineage_k"] == 2
    assert d.loc["B", "k"] == 2  # bases 0-1 (own source covered); bases 3-5 have no R in lineage -> lineage flag (False)
    assert set(df.clim_field) == {"C_lim[f3-c-lim-complete-v1]"}
    bad = [dict(r) for r in recs]
    bad[3]["decisive_consent_query"] = not bad[3]["decisive_consent_query"]  # disagree with the logged coverage
    with pytest.raises(S2.Study2SchemaError, match="disagrees"):
        _load(tmp_path / "bad", bad)
