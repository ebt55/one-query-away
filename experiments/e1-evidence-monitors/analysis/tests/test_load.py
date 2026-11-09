"""Loader: real spike/pilot runs load; every kind of schema drift raises. Uses SYNTHETIC records for drift cases."""
import copy
import json

import pytest

import load
import synthetic
from common import RUNS_DIR

SPIKE = RUNS_DIR / "20261107T234300Z_e1spike_bf31b8"
HB_PILOT = RUNS_DIR / "20261108T010026Z_e1pilot-hb_092172"
TG_PILOT = RUNS_DIR / "20261108T004410Z_e1pilot_ce1346"
FAKE = RUNS_DIR / "20261108T010009Z_e1fake_0071b7"
RID = "20261108T000000Z_x_aaaaaa"


def _write_run(tmp_path, recs, name="e1main", synthetic_flag=True, run_id=RID, regime="targeted",
               p2g_rule="p2g_v1_cross_actor"):
    d = tmp_path / run_id
    d.mkdir()
    meta = {"run_id": run_id, "name": name, "regime": regime}
    if regime == "handbook" and p2g_rule:
        meta["p2g_rule"] = p2g_rule
    if synthetic_flag:
        meta["synthetic"] = True
    (d / "run_meta.json").write_text(json.dumps(meta))
    with open(d / "monitor_results.jsonl", "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return d


def _recs(run_id=RID, n=40, regime="targeted"):
    runs, truth = synthetic.generate_records(seed=3, n_cal_per_fam=1, n_test_per_fam=1, n_f4=0, repeat_frac=0,
                                             regimes=(regime,), models=("haiku",))
    key = next(r for r, rg in truth["run_regimes"].items() if rg == regime and r.endswith("_test"))
    recs = copy.deepcopy(runs[key][:n])
    for r in recs:
        r["run_id"] = run_id
    return recs


@pytest.mark.skipif(not SPIKE.exists(), reason="spike run not present")
def test_spike_run_loads():
    df, infos = load.load_runs([SPIKE])
    assert len(df) == 40
    assert set(df.split) == {"spike"} and set(df.family) == {"F1"} and set(df.regime) == {"targeted"}
    assert df.groupby(["model_key", "condition"]).size().eq(4).all()
    p1 = df[df.condition == "P1"]
    assert (p1.verdict == "insufficient_evidence").all() and len(p1) == 8
    assert not df.is_failure.any()
    assert (df[df.condition == "P2"].q_used == 3).all()
    assert df.thinking_tokens.notna().all()


@pytest.mark.skipif(not (HB_PILOT.exists() and TG_PILOT.exists()), reason="pilot runs not present")
def test_real_pilots_load_with_regimes():
    df, infos = load.load_runs([HB_PILOT, TG_PILOT])
    assert set(df.regime) == {"handbook", "targeted"}
    hb = df[df.regime == "handbook"]
    assert "P2S" in set(hb.condition) and "P2" not in set(hb.condition)
    assert "P2G" not in set(hb[hb.run_id == HB_PILOT.name].condition)  # p2g_v0 rows superseded
    assert hb.loc[~hb.is_failure & (hb.condition != "VSEP"), "policy_id"].notna().all()
    assert df[df.regime == "targeted"].policy_id.isna().all()
    assert (hb.true_policy_id == hb.apply(lambda r: f"RP-{r.family[1]}" if r.harmful else "none", axis=1)).all()


@pytest.mark.skipif(not FAKE.exists(), reason="fake run not present")
def test_fake_backend_runs_skipped():
    df, info = load.load_run(FAKE)
    assert df is None and "fake" in info["skipped"]


def test_synthetic_roundtrip(tmp_path):
    recs = _recs()
    d = _write_run(tmp_path, recs)
    df, _ = load.load_runs([d])
    assert len(df) == len(recs) and set(df.run_kind) == {"synthetic"} and set(df.regime) == {"targeted"}


def test_handbook_roundtrip(tmp_path):
    d = _write_run(tmp_path, _recs(regime="handbook", n=60), regime="handbook")
    df, _ = load.load_runs([d])
    assert set(df.regime) == {"handbook"} and df.loc[~df.is_failure, "policy_id"].notna().all()


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.pop("family"), "missing required"),
    (lambda r: r.update(new_field=1), "unknown field"),
    (lambda r: r.update(cond="CE_V9"), "not in vocabulary"),
    (lambda r: r.update(split="holdout"), "split"),
    (lambda r: r.update(family="F9"), "family"),
    (lambda r: r.update(label="yes"), "label must be bool"),
    (lambda r: r["parsed"].update(suspicion=11) if r["parsed"]["valid"] else r["parsed"].update(outcome="x"),
     "suspicion|outcome ok"),
    (lambda r: r["calls"][0].update(surprise=True), "unknown call field"),
    (lambda r: r["calls"][0].update(backend="fake"), "backend=fake"),
    (lambda r: r.update(C_lim="maybe"), "C_lim"),
    (lambda r: r.update(schema="e1.monitor_result.v2"), "unknown schema"),
    (lambda r: r.update(run_id="other"), "run_id"),
    (lambda r: r.update(cond="IE_P2G", q_used=3, records=1, chars=10), "not valid in the targeted regime"),
    (lambda r: r.update(notes=json.dumps({"policy_id": "RP-1"})), "policy_id present in a targeted"),
    (lambda r: r.update(notes=json.dumps({"regime": "handbook"})), "notes.regime"),
    (lambda r: r.update(notes="{not json"), "not valid JSON"),
    (lambda r: r.update(notes=json.dumps({"mood": 1})), "unknown key"),
])
def test_schema_drift_raises(tmp_path, mutate, msg):
    recs = _recs()
    r0 = copy.deepcopy(recs[0])
    mutate(r0)
    recs[0] = r0
    d = _write_run(tmp_path, recs)
    with pytest.raises(load.SchemaDriftError, match=msg):
        load.load_runs([d])


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.pop("notes"), "need notes"),
    (lambda r: r.update(notes=json.dumps({"regime": "handbook", "policy_id": "RP-9"})), "not in"),
    (lambda r: r.update(notes=json.dumps({"regime": "targeted", "policy_id": "RP-1"})), "notes.regime"),
    (lambda r: r.update(cond="IE_P2", q_used=3, records=1, chars=10), "not valid in the handbook regime"),
])
def test_handbook_drift_raises(tmp_path, mutate, msg):
    recs = _recs(regime="handbook", n=60)
    i = next(k for k, r in enumerate(recs) if r["parsed"]["valid"] and r["cond"] != "CE_VSEP")
    r0 = copy.deepcopy(recs[i])
    mutate(r0)
    recs[i] = r0
    d = _write_run(tmp_path, recs, regime="handbook")
    with pytest.raises(load.SchemaDriftError, match=msg):
        load.load_runs([d])


def test_valid_handbook_verdict_needs_policy_id(tmp_path):
    recs = _recs(regime="handbook", n=60)
    r = next(r for r in recs if r["parsed"]["valid"] and r["cond"] != "CE_VSEP")
    r["notes"] = json.dumps({"regime": "handbook", "policy_id": None})
    d = _write_run(tmp_path, recs, regime="handbook")
    with pytest.raises(load.SchemaDriftError, match="without policy_id"):
        load.load_runs([d])


def test_bad_run_regime_raises(tmp_path):
    d = _write_run(tmp_path, _recs(), regime="lenient")
    with pytest.raises(load.SchemaDriftError, match="regime"):
        load.load_runs([d])


def test_same_world_both_regimes_ok_but_label_must_agree(tmp_path):
    runs, truth = synthetic.generate_records(seed=5, n_cal_per_fam=1, n_test_per_fam=1, n_f4=0, repeat_frac=0,
                                             models=("haiku",))
    paths = synthetic.write_runs(tmp_path / "ok", seed=5, n_cal_per_fam=1, n_test_per_fam=1, n_f4=0, repeat_frac=0,
                                 models=("haiku",))
    df, _ = load.load_runs(paths)
    assert set(df.regime) == {"handbook", "targeted"}
    shared = set(df[df.regime == "handbook"].world_id) & set(df[df.regime == "targeted"].world_id)
    assert shared  # same worlds in both regimes: duplicates key includes regime
    # flip one targeted label -> cross-regime inconsistency must raise
    tgt = next(p for p in paths if "-hb" not in p.name and p.name.endswith("_test"))
    lines = (tgt / "monitor_results.jsonl").read_text().splitlines()
    r = json.loads(lines[0])
    r["label"] = not r["label"]
    r["C_full"] = "violation" if r["label"] else "compliant"
    r["C_lim"] = None if r["C_lim"] is None else "unknown"
    lines[0] = json.dumps(r)
    (tgt / "monitor_results.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(load.ValidationError, match="inconsistent harmful"):
        load.load_runs(paths)


def test_model_mismatch_raises(tmp_path):
    recs = _recs()
    for c in recs[0]["calls"]:
        if c["outcome"] == "ok":
            c["served_model"], c["model_mismatch"] = "claude-other", True
    d = _write_run(tmp_path, recs)
    with pytest.raises(load.SchemaDriftError, match="served_model"):
        load.load_runs([d])


def test_duplicate_decision_raises(tmp_path):
    recs = _recs()
    recs.append(copy.deepcopy(recs[0]))
    d = _write_run(tmp_path, recs)
    with pytest.raises(load.ValidationError, match="duplicate"):
        load.load_runs([d])


def test_unsound_clim_raises(tmp_path):
    recs = _recs()
    r = next(x for x in recs if x["C_lim"] in ("violation", "compliant"))
    r["C_lim"] = "compliant" if r["C_lim"] == "violation" else "violation"
    d = _write_run(tmp_path, recs)
    with pytest.raises(load.ValidationError, match="unsound"):
        load.load_runs([d])


def test_schemaless_record_outside_spike_raises(tmp_path):
    recs = _recs()
    del recs[0]["schema"]
    d = _write_run(tmp_path, recs)
    with pytest.raises(load.SchemaDriftError, match="no 'schema'"):
        load.load_runs([d])


def test_mixed_kinds_refused(tmp_path):
    a = _write_run(tmp_path, _recs(), synthetic_flag=True)
    rid = "20261108T000001Z_y_bbbbbb"
    b = _write_run(tmp_path, _recs(run_id=rid), synthetic_flag=False, run_id=rid)
    with pytest.raises(load.ValidationError, match="mix run kinds"):
        load.load_runs([a, b])


def test_outcome_mapping():
    assert load.map_outcome({"outcome": "refusal", "valid": False})[0] == "refusal"
    assert load.map_outcome({"outcome": "timeout", "valid": False})[0] == "timeout"
    assert load.map_outcome({"outcome": "max_tokens", "valid": False})[0] == "invalid"
    assert load.map_outcome({"outcome": "api_error", "valid": False})[0] == "invalid"
    assert load.map_outcome({"outcome": "ok", "valid": False, "invalid_reason": "parse_error"})[0] == "invalid"
    v, _, s = load.map_outcome({"outcome": "ok", "valid": True, "verdict": "compliant", "suspicion": 2})
    assert v == "compliant" and s == 2.0


# ----------------------------------------------------------------------------- superseded rows / P2G rule
def test_p2g_rule_unknown_raises(tmp_path):
    d = _write_run(tmp_path, _recs(regime="handbook", n=80), regime="handbook", p2g_rule=None)
    with pytest.raises(load.SchemaDriftError, match="P2G rule is unknown"):
        load.load_runs([d], registry_path=None)


def test_p2g_v0_rows_excluded_everywhere(tmp_path):
    recs = _recs(regime="handbook", n=80)
    assert any(r["cond"] == "IE_P2G" for r in recs)
    d = _write_run(tmp_path, recs, regime="handbook", p2g_rule="p2g_v0")
    df, infos, sup = load.load_runs([d], registry_path=None, return_superseded=True)
    assert "P2G" not in set(df.condition) and set(sup.condition) == {"P2G"}
    assert infos[0]["superseded"] == {"IE_P2G": int((sup.condition == "P2G").sum())}
    assert len(df) + len(sup) == len(recs) + int((df.source_line == -1).sum())


def test_annotation_superseded_conds_and_registry(tmp_path):
    recs = _recs(regime="handbook", n=80)
    d = _write_run(tmp_path, recs, regime="handbook", p2g_rule="p2g_v1_cross_actor")
    meta = json.loads((d / "run_meta.json").read_text())
    meta["annotations"] = {"superseded_conds": {"IE_P1": "test: retired prompt"}}
    (d / "run_meta.json").write_text(json.dumps(meta))
    df, _, sup = load.load_runs([d], registry_path=None, return_superseded=True)
    assert "P1" not in set(df.condition) and set(sup.condition) == {"P1"} and "P2G" in set(df.condition)
    # registry entry with a model filter
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"p2g_rules": {"current": "p2g_v1_cross_actor", "superseded": ["p2g_v0"]},
                               "runs": [{"run_id": RID, "conds": ["CE_V0"], "models": ["haiku"], "reason": "t"}]}))
    df2, _, sup2 = load.load_runs([d], registry_path=reg, return_superseded=True)
    assert set(sup2.condition) == {"P1", "V0"}


def test_conflicting_p2g_records_raise(tmp_path):
    d = _write_run(tmp_path, _recs(regime="handbook", n=80), regime="handbook", p2g_rule="p2g_v1_cross_actor")
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"p2g_rules": {"current": "p2g_v1_cross_actor", "superseded": ["p2g_v0"]},
                               "runs": [{"run_id": RID, "conds": ["IE_P2G"], "p2g_rule": "p2g_v0", "reason": "t"}]}))
    with pytest.raises(load.SchemaDriftError, match="conflicting P2G"):
        load.load_runs([d], registry_path=reg)


@pytest.mark.skipif(not HB_PILOT.exists(), reason="pilot runs not present")
def test_real_pilot_p2g_v0_excluded_and_deepseek_arms():
    runs = load.discover_runs(RUNS_DIR, kinds=("pilot",))
    df, infos, sup = load.load_runs(runs, return_superseded=True)
    hb = df[df.regime == "handbook"]
    assert (hb[hb.condition == "P2G"].p2g_rule == "p2g_v1_cross_actor").all()
    assert set(sup.condition) == {"P2G"}
    assert {"deepseek", "deepseek-thinking"} <= set(df.model_key)  # thinking modes never pool


def test_deepseek_thinking_must_be_recorded(tmp_path):
    recs = _recs()
    for r in recs:
        r["model"] = "deepseek"
    d = _write_run(tmp_path, recs)
    with pytest.raises(load.SchemaDriftError, match="deepseek_thinking"):
        load.load_runs([d])


# ----------------------------------------------------------------------------- C_lim_native (builder v8)
def test_clim_native_loaded_and_targeted_fallback(tmp_path):
    d = _write_run(tmp_path, _recs(regime="handbook", n=80), regime="handbook")
    df, _ = load.load_runs([d])
    ie = df[(df.stratum == "IE")]
    assert ie.c_lim_native.notna().all() and set(ie.c_lim_native_source) == {"C_lim_native"}
    recs = _recs()
    for r in recs:
        r.pop("C_lim_native", None)
    d2 = _write_run(tmp_path / "t", recs) if (tmp_path / "t").mkdir() is None else None
    df2, _ = load.load_runs([d2])
    t = df2[df2.c_lim.notna()]
    assert (t.c_lim_native == t.c_lim).all()  # targeted C-lim is the native-only checker


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.update(C_lim_native="maybe"), "C_lim_native"),
])
def test_clim_native_enum(tmp_path, mutate, msg):
    recs = _recs(regime="handbook", n=80)
    r = next(x for x in recs if x["cond"] == "IE_P3")
    mutate(r)
    d = _write_run(tmp_path, recs, regime="handbook")
    with pytest.raises(load.SchemaDriftError, match=msg):
        load.load_runs([d])


def test_clim_native_unsound_or_inconsistent_raises(tmp_path):
    recs = _recs(regime="handbook", n=80)
    r = next(x for x in recs if x["cond"] == "IE_P3" and not x["label"])
    r["C_lim_native"] = "violation"  # benign world: a native violation is impossible
    d = _write_run(tmp_path, recs, regime="handbook")
    with pytest.raises(load.ValidationError, match="C_lim_native"):
        load.load_runs([d])


def test_main_handbook_needs_clim_native(tmp_path):
    recs = _recs(regime="handbook", n=80)
    for r in recs:
        r.pop("C_lim_native", None)
    d = _write_run(tmp_path, recs, regime="handbook", synthetic_flag=False)
    with pytest.raises(load.ValidationError, match="lack C_lim_native"):
        load.load_runs([d])


# ----------------------------------------------------------------------------- builder v8.1: p3i_mode, flags, exclusions
def test_p3i_modes_are_separate_conditions(tmp_path):
    paths = synthetic.write_runs(tmp_path / "r", seed=8, n_cal_per_fam=2, n_test_per_fam=2, n_f4=0, repeat_frac=0,
                                 regimes=("handbook",))
    df, _ = load.load_runs(paths)
    p3i = df[df.condition.str.startswith("P3I")]
    assert set(p3i.condition) == {"P3I_shared", "P3I_standalone"}  # never pooled into one "P3I"
    assert set(p3i[p3i.model_key == "haiku"].condition) == {"P3I_shared"}
    assert set(p3i[p3i.model_key == "sonnet"].condition) == {"P3I_standalone"}
    import calibrate as cal
    doc = cal.calibrate(df[df.split == "calibration"], mode="synthetic", source_desc="c")
    assert "haiku|P3I_shared|handbook" in doc["thresholds"] and "sonnet|P3I_standalone|handbook" in doc["thresholds"]


@pytest.mark.parametrize("mutate,msg", [
    (lambda r: r.update(notes=json.dumps({**json.loads(r["notes"]), "p3i_mode": "both"})), "p3i_mode"),
])
def test_bad_p3i_mode_raises(tmp_path, mutate, msg):
    recs = _recs(regime="handbook", n=200)
    r = next(x for x in recs if x["cond"] == "IE_P3I")
    mutate(r)
    d = _write_run(tmp_path, recs, regime="handbook")
    with pytest.raises(load.SchemaDriftError, match=msg):
        load.load_runs([d])


def test_p3i_mode_on_other_cond_raises(tmp_path):
    recs = _recs(regime="handbook", n=200)
    r = next(x for x in recs if x["cond"] == "IE_P3")
    r["notes"] = json.dumps({**json.loads(r["notes"]), "p3i_mode": "shared_selector"})
    d = _write_run(tmp_path, recs, regime="handbook")
    with pytest.raises(load.SchemaDriftError, match="only IE_P3I"):
        load.load_runs([d])


def test_logged_p2g_flag_used_and_checked_against_hashes(tmp_path):
    paths = synthetic.write_runs(tmp_path / "r", seed=9, n_cal_per_fam=2, n_test_per_fam=3, n_f4=0, repeat_frac=0,
                                 regimes=("handbook",), models=("haiku",))
    df, _ = load.load_runs(paths)
    hb = df[df.regime == "handbook"]
    assert set(hb.p2g_differs_source.dropna()) == {"logged"} and hb.p2g_evidence_differs.notna().all()
    # flip the logged flag on one IE row of one base case -> conflicting logged values must raise
    tst = next(p for p in paths if p.name.endswith("_test"))
    lines = (tst / "monitor_results.jsonl").read_text().splitlines()
    i = next(k for k, l in enumerate(lines) if '"p2g_evidence_differs"' in l)
    r = json.loads(lines[i])
    r["p2g_evidence_differs"] = not r["p2g_evidence_differs"]
    lines[i] = json.dumps(r)
    (tst / "monitor_results.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(load.ValidationError, match="p2g_evidence_differs"):
        load.load_runs(paths)


def test_excluded_run_never_loaded(tmp_path):
    d = _write_run(tmp_path, _recs(), run_id="20261108T043220Z_e1fake_78bd23_REAL_DEEPSEEK_SPEND_not_results")
    ok = _write_run(tmp_path, _recs(run_id="20261108T000009Z_ok_cccccc"), run_id="20261108T000009Z_ok_cccccc")
    df, infos = load.load_runs([d, ok])  # the registry excludes the first BEFORE reading it (run_id mismatch ignored)
    assert set(df.run_id) == {"20261108T000009Z_ok_cccccc"}
    assert infos[0]["kind"] == "excluded" and "REAL DeepSeek" in infos[0]["skipped"]


REAL_EXCLUDED = RUNS_DIR / "_validation" / "20261108T043220Z_e1fake_78bd23_REAL_DEEPSEEK_SPEND_not_results"


@pytest.mark.skipif(not REAL_EXCLUDED.exists(), reason="validation run not present")
def test_real_excluded_run_is_skipped():
    with pytest.raises(load.ValidationError, match="no monitor decisions"):
        load.load_runs([REAL_EXCLUDED])  # excluded -> nothing loaded



def test_run_kind_f4_names():
    assert load._run_kind({"name": "e1f4-hb"}) == "main"
    assert load._run_kind({"name": "e1pilot-hb", "annotations": {"run_kind": "f4_confirmation"}}) == "main"
    assert load._run_kind({"name": "e1pilot-hb"}) == "pilot"
