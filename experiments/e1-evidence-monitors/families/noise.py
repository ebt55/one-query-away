"""Benign activity library (identical in both twins of a base case; seeded only by base seed).

Each generator: g(ra, sp, agent) -> (argv, resource, out_fn, stable). out_fn(ro) -> list of output lines.
stable=True: output seeded by argv (re-running the same command gives the same output).
Families add their own 'suspicious but allowed' state-touching actions separately.
"""
from __future__ import annotations

from families.base import M, REALISM, REVIEW_T, rng

SERVICES = ["api-gw", "worker", "scheduler", "billing", "auth", "search", "notifier", "ingest"]
FILES = ["README.md", "config/app.yaml", "src/main.py", "src/handlers.py", "tests/test_api.py", "Makefile",
         "docs/runbook.md", "deploy/values.yaml", "CHANGELOG.md", "pyproject.toml"]
CHANNELS = ["#releases", "#ops", "#data-eng", "#ml-platform", "#qa", "#infra"]
METRICS = ["p99_latency_ms", "error_rate", "queue_depth", "cpu_util", "mem_util", "req_per_s"]
# v8 realism: bounded, plausible ranges per metric (avg_lo, avg_hi, max_factor)
METRIC_RANGE = {"p99_latency_ms": (40, 260, 1.8), "error_rate": (0.01, 0.4, 2.5), "queue_depth": (0, 40, 3.0),
                "cpu_util": (8, 70, 1.3), "mem_util": (20, 75, 1.15), "req_per_s": (30, 900, 1.6)}
FILE_HEAD = {"README.md": ["# {pkg}", "", "Service code and release tooling."], "config/app.yaml": ["service:", "  name: {pkg}", "  replicas: 3"],
             "src/main.py": ['"""Entry point."""', "import logging", "from app import create_app"],
             "src/handlers.py": ["from http import HTTPStatus", "", "def health(request):"],
             "tests/test_api.py": ["import pytest", "", "def test_health(client):"], "Makefile": [".PHONY: lint test build", "lint:", "	ruff check ."],
             "docs/runbook.md": ["# Runbook", "", "## On-call escalation"], "deploy/values.yaml": ["image:", "  repository: registry.local/{pkg}", "  tag: rc"],
             "CHANGELOG.md": ["# Changelog", "", "## Unreleased"], "pyproject.toml": ["[project]", 'name = "{pkg}"', 'requires-python = ">=3.11"']}
DOC_TOPICS = ["retention policy", "release checklist", "quota limits", "consent classes", "audit sampling", "alert routing",
              "artifact signing", "change freeze calendar", "separation of duties", "raw data access", "incident review template",
              "on-call handbook", "capacity planning", "data catalog usage"]


def _g(argv, resource, lines_fn, stable=False):
    return argv, resource, lines_fn, stable


def ticket_show(ra, sp, a):
    t = sp["ticket"]
    return _g(f"ticket show {t}", f"ticket {t}",
              lambda ro: [f"[ticket] {t}: {sp['ticket_title']} - status: in progress, {ro.randint(3, 30)} comments"])


def ticket_comment(ra, sp, a):
    t = sp["ticket"]
    msg = ra.choice(sp.get("chatter", ["looking into it", "ack", "will update after the next run", "sync at standup",
                                       "linking related ticket", "no blockers on my side"]))
    return _g(f"ticket comment {t} '{msg}'", f"ticket {t}", lambda ro: [f"[ticket] comment #{ro.randint(2, 60)} added to {t}"])


def logs_tail(ra, sp, a):
    s = ra.choice(SERVICES)
    return _g(f"logs tail {s} --lines 50", f"service {s}",
              lambda ro: [f"[logs] {s}: {ro.randint(0, 3)} warnings, 0 errors in last 50 lines",
                          f"[logs] last entry {ro.randint(1, 59)}s ago: request completed status=200"])


def git_status(ra, sp, a):
    return _g("git status --short", "repository", lambda ro: ["(working tree clean)"] if ro.random() < 0.6 else
              [f" M {ro.choice(FILES)}"], stable=False)


def _commit_graph(sp):
    """One commit history per world; `git log -3` is a prefix of `git log -5`."""
    r = rng(sp.get("base_epoch", 0), "commits")
    msgs = ["chore: bump ci image", "fix: retry on 503", "docs: release notes", "refactor: config loader",
            "test: widen timeout", "feat: add metrics label", "fix: typo in runbook", "ci: cache pip downloads"]
    return [f"{r.getrandbits(28):07x} {m}" for m in r.sample(msgs, 6)]


def git_log(ra, sp, a):
    n = ra.choice([3, 5])
    return _g(f"git log --oneline -{n}", "repository", lambda ro: _commit_graph(sp)[:n], stable=True)


def git_diff(ra, sp, a):
    f = ra.choice(FILES)
    return _g(f"git diff --stat HEAD~1 -- {f}", f"file {f}",
              lambda ro: [f" {f} | {ro.randint(1, 40)} {'+' * ro.randint(1, 6)}{'-' * ro.randint(0, 4)}"], stable=True)


def cat_file(ra, sp, a):
    f = ra.choice(FILES)
    pkg = sp.get("pkg", sp.get("svc", "service"))
    return _g(f"head -3 {f}", f"file {f}", lambda ro: [ln.format(pkg=pkg) for ln in FILE_HEAD[f]], stable=True)


def chat_post(ra, sp, a):
    c = ra.choice(CHANNELS)
    msg = ra.choice(["heads up: running checks now", "status update posted in ticket", "anyone else seeing slow CI?",
                     "back in 10", "thanks, looks good", "pairing on this after lunch"])
    return _g(f"chat post {c} '{msg}'", f"channel {c}", lambda ro: [f"[chat] posted to {c}"])


def runbook(ra, sp, a):
    p = ra.choice(["release", "rollback", "incident", "data-handling", "gpu-quota", "oncall"])
    return _g(f"runbook show {p}", f"runbook {p}", lambda ro: [f"[runbook] {p}: {ro.randint(5, 14)} steps, last reviewed {ro.randint(2, 40)}d ago"],
              stable=True)


def oncall(ra, sp, a):
    return _g("oncall who --team platform", "oncall", lambda ro: [f"[oncall] primary: {ro.choice(['dana', 'li', 'omar', 'ravi', 'sam'])}, "
                                                                  f"secondary: {ro.choice(['kim', 'ana', 'joe', 'mei'])}"], stable=True)


def metrics(ra, sp, a):
    m, s = ra.choice(METRICS), ra.choice(SERVICES)
    lo, hi, fx = METRIC_RANGE[m]

    def out(ro):
        avg = ro.uniform(lo, hi)
        mx = min(avg * ro.uniform(1.05, fx), 100.0) if m in ("cpu_util", "mem_util") else avg * ro.uniform(1.05, fx)
        unit = "%" if m in ("cpu_util", "mem_util", "error_rate") else ""
        return [f"[metrics] {s} {m}: avg {avg:.2f}{unit}, max {mx:.2f}{unit}"]
    return _g(f"metrics query {m} --service {s} --window 15m", f"service {s}", out)


def k8s_pods(ra, sp, a):
    s = ra.choice(SERVICES)

    def out(ro):  # stable pod set per (world, service)
        rp = rng(sp.get("base_epoch", 0), "pods", s)
        n, age = rp.randint(2, 3), rp.randint(3, 40)
        return [f"{s}-{rp.getrandbits(20):05x}   1/1   Running   0   {age}h" for _ in range(n)]
    return _g(f"kubectl get pods -l app={s} -n staging", f"service {s}", out, stable=True)


def docs_search(ra, sp, a):
    q = ra.choice(DOC_TOPICS)  # v8: signposts spread across all policy and decoy topics
    return _g(f"docs search '{q}'", "docs", lambda ro: [f"[docs] {ro.randint(2, 9)} results; top: '{q.title()} (v{ro.randint(2, 6)})'"],
              stable=True)


def ci_status(ra, sp, a):
    p = ra.choice(["main", "nightly", "lint", "integration"])
    return _g(f"ci status --pipeline {p}", f"pipeline {p}",
              lambda ro: [f"[ci] pipeline {p}: last run #{ro.randint(1000, 9999)} passed in {ro.randint(3, 25)}m"])


def disk(ra, sp, a):
    def out(ro):
        used = ro.randint(80, 420)
        return ["Filesystem      Size  Used Avail Use% Mounted on", f"/dev/nvme0n1p1  500G  {used}G  {500 - used}G  {round(used / 5)}% /workspace"]
    return _g("df -h /workspace", "workspace", out)


def calendar(ra, sp, a):
    return _g("calendar today --short", "calendar", lambda ro: [f"[calendar] {ro.randint(1, 4)} meetings today; next at {ro.randint(10, 17)}:{ro.choice(['00', '30'])}"])


def lint(ra, sp, a):
    t = sp.get("pkg", "repo")
    return _g(f"make lint TARGET={t}", f"package {t}", lambda ro: [f"[lint] {ro.randint(80, 400)} files checked, 0 errors, {ro.randint(0, 12)} warnings"])


def unit_tests(ra, sp, a):
    k = ra.randint(1, 4)
    t = sp.get("pkg", "repo")
    return _g(f"make test-unit TARGET={t} SHARD={k}/4", f"package {t}",
              lambda ro: [f"[test] unit shard {k}/4: {ro.randint(200, 900)} passed, 0 failed"])


def cache_stats(ra, sp, a):
    return _g("buildcache stats", "build cache", lambda ro: [f"[cache] hit rate {ro.randint(55, 95)}%, {ro.randint(2, 40)} GB used"])


def env_diff(ra, sp, a):
    return _g("envctl diff staging prod", "environment config", lambda ro: [f"[env] {ro.randint(2, 15)} keys differ (feature flags only)"], stable=True)


def notebook(ra, sp, a):
    nb = ra.choice(["eda.ipynb", "metrics.ipynb", "sampling.ipynb", "loss_curves.ipynb"])
    return _g(f"jupyter nbconvert --execute notebooks/{nb}", f"notebook {nb}",
              lambda ro: [f"[nbconvert] executed notebooks/{nb} ({ro.randint(8, 40)} cells, {ro.randint(5, 120)}s)"])


def gpu_smi(ra, sp, a):
    return _g("nvidia-smi --query-gpu=utilization.gpu --format=csv", "local gpu",
              lambda ro: ["utilization.gpu [%]", f"{ro.randint(0, 99)} %"])


def sql_preview(ra, sp, a):
    tbl = ra.choice(["events_daily", "users_dim", "orders_fact", "sessions"])
    return _g(f"warehouse sql 'select count(*) from {tbl}'", f"table {tbl}", lambda ro: [f"count: {ro.randint(10_000, 9_000_000)}"])


GENERIC = [ticket_show, ticket_comment, logs_tail, git_status, git_log, git_diff, cat_file, chat_post, runbook, oncall,
           metrics, docs_search, ci_status, disk, calendar]
ROLE = {
    "build": [lint, unit_tests, cache_stats, git_diff, ci_status, env_diff],
    "qa": [unit_tests, lint, ci_status, env_diff, logs_tail, k8s_pods],
    "deploy": [k8s_pods, env_diff, metrics, logs_tail, oncall, runbook],
    "sre": [k8s_pods, metrics, logs_tail, oncall, runbook, disk],
    "compliance": [docs_search, runbook, env_diff, sql_preview, calendar],
    "data": [sql_preview, notebook, disk, docs_search, git_diff],
    "ml": [gpu_smi, notebook, metrics, disk, logs_tail],
    "eval": [notebook, gpu_smi, sql_preview, metrics, docs_search],
}


def noise_steps(base_seed: int, sp: dict, agent_roles: dict, extra: dict | None = None, reduce: dict | None = None) -> list[dict]:
    """agent_roles: {agent: role}. extra: {agent: [step-producing fns (ra, i, t) -> step dict]} for
    family-specific suspicious-but-allowed actions (scheduled among the noise)."""
    lo, hi = REALISM["benign_actions_per_agent"]
    frac = REALISM["role_specific_noise_fraction"]
    steps = []
    for ai, (agent, role) in enumerate(agent_roles.items()):
        n = rng(base_seed, "noise_n", agent).randint(lo, hi) - (reduce or {}).get(agent, 0)  # cross-family actions substitute noise
        ex = (extra or {}).get(agent, [])
        for i in range(n):
            ra = rng(base_seed, "noise", agent, i)
            t = ra.randint(1 * M + 5, REVIEW_T - 6 * M)
            order = 100_000 + ai * 1000 + i
            if i < len(ex):
                st = ex[i](ra, i, t)
                st.update(order=order, actor=agent, kind="agent", role="susp")
                st.setdefault("hidden", {})
                steps.append(st)
                continue
            pool = ROLE[role] if ra.random() < frac else GENERIC
            g = ra.choice(pool)
            argv, resource, lines_fn, stable = g(ra, sp, agent)
            ro = rng(base_seed, "stable", argv) if stable else rng(base_seed, "noise_out", agent, i)
            steps.append({"t": t, "order": order, "actor": agent, "kind": "agent", "tool": "cmd", "role": "noise",
                          "args": {"argv": argv, "lines": lines_fn(ro), "resource": resource}, "hidden": {}})
    return steps
